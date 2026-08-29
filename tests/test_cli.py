import logging
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from azure.core.exceptions import ClientAuthenticationError
from openai import OpenAIError

import azure_bash_agent.cli as cli
from azure_bash_agent.agent import AgentProtocolError, AgentRun
from azure_bash_agent.bash_runner import BashCommandRunner
from azure_bash_agent.config import BashSettings, ConfigError, LlmSettings, Settings


def test_main_uses_default_config_and_public_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    settings = SimpleNamespace(llm=SimpleNamespace(model="deployment"))
    client = object()
    runner = object()
    terminal = object()
    agent_run = SimpleNamespace(run=lambda: events.append("run"))
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda path: events.append(("config", path)) or settings,
    )
    monkeypatch.setattr(
        cli,
        "create_responses_client",
        lambda received: events.append(("client", received)) or client,
    )
    monkeypatch.setattr(
        cli,
        "create_command_runner",
        lambda received: events.append(("runner", received)) or runner,
    )
    monkeypatch.setattr(
        cli,
        "create_terminal",
        lambda stdin, stdout: events.append(("terminal", stdin, stdout)) or terminal,
    )
    monkeypatch.setattr(
        cli,
        "create_agent_run",
        lambda received, responses, command_runner, io: (
            events.append(("agent", received, responses, command_runner, io)) or agent_run
        ),
    )
    stdin = StringIO()
    stdout = StringIO()

    result = cli.main([], stdin=stdin, stdout=stdout, stderr=StringIO())

    assert result == 0
    assert events == [
        ("config", Path("agent.toml")),
        ("client", settings.llm),
        ("runner", settings),
        ("terminal", stdin, stdout),
        ("agent", settings, client, runner, terminal),
        "run",
    ]


def patch_composition(
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_error: BaseException | None = None,
) -> tuple[object, list[object]]:
    settings = SimpleNamespace(llm=SimpleNamespace(model="deployment"))
    events: list[object] = []

    def run() -> None:
        events.append("run")
        if run_error is not None:
            raise run_error

    monkeypatch.setattr(cli, "load_config", lambda path: settings)
    monkeypatch.setattr(cli, "create_responses_client", lambda llm: "client")
    monkeypatch.setattr(cli, "create_command_runner", lambda value: "runner")
    monkeypatch.setattr(cli, "create_terminal", lambda stdin, stdout: "terminal")
    monkeypatch.setattr(
        cli,
        "create_agent_run",
        lambda *args: SimpleNamespace(run=run),
    )
    return settings, events


def test_main_selects_explicit_config_and_wires_complete_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    settings = SimpleNamespace(llm=SimpleNamespace(model="deployment"))
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda path: events.append(("load", path)) or settings,
    )
    monkeypatch.setattr(
        cli,
        "create_responses_client",
        lambda value: events.append(("client", value)) or "client",
    )
    monkeypatch.setattr(
        cli,
        "create_command_runner",
        lambda value: events.append(("runner", value)) or "runner",
    )
    monkeypatch.setattr(
        cli,
        "create_terminal",
        lambda stdin, stdout: events.append("terminal") or "terminal",
    )
    monkeypatch.setattr(
        cli,
        "create_agent_run",
        lambda *args: (
            events.append(("agent", args)) or SimpleNamespace(run=lambda: events.append("run"))
        ),
    )

    result = cli.main(
        ["--config", "custom.toml"],
        stdin=StringIO(),
        stdout=StringIO(),
        stderr=StringIO(),
    )

    assert result == 0
    assert events == [
        ("load", Path("custom.toml")),
        ("client", settings.llm),
        ("runner", settings),
        "terminal",
        ("agent", (settings, "client", "runner", "terminal")),
        "run",
    ]


def test_configuration_failure_is_concise_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "load_config",
        lambda path: (_ for _ in ()).throw(ConfigError("bad config\nverbose detail")),
    )
    stdout = StringIO()
    stderr = StringIO()

    result = cli.main([], stdin=StringIO(), stdout=stdout, stderr=stderr)

    assert result == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "Configuration error: bad config\n"


def test_authentication_failure_mentions_az_login_as_one_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, _ = patch_composition(monkeypatch)
    monkeypatch.setattr(cli, "load_config", lambda path: settings)
    monkeypatch.setattr(
        cli,
        "create_responses_client",
        lambda llm: (_ for _ in ()).throw(
            ClientAuthenticationError("credential failed\nlong chain")
        ),
    )
    stderr = StringIO()

    result = cli.main([], stdin=StringIO(), stdout=StringIO(), stderr=stderr)

    assert result == 3
    assert "Azure authentication failed: credential failed." in stderr.getvalue()
    assert "az login is one possible remedy" in stderr.getvalue()
    assert "long chain" not in stderr.getvalue()


def test_openai_failure_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_composition(monkeypatch, run_error=OpenAIError("request failed"))
    stderr = StringIO()

    result = cli.main([], stdin=StringIO(), stdout=StringIO(), stderr=stderr)

    assert result == 4
    assert stderr.getvalue() == "OpenAI request failed: request failed\n"


def test_protocol_failure_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_composition(monkeypatch, run_error=AgentProtocolError("unknown tool"))
    stderr = StringIO()

    result = cli.main([], stdin=StringIO(), stdout=StringIO(), stderr=stderr)

    assert result == 5
    assert stderr.getvalue() == "Model protocol error: unknown tool\n"


def test_startup_os_failure_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_composition(monkeypatch)
    monkeypatch.setattr(
        cli,
        "create_command_runner",
        lambda settings: (_ for _ in ()).throw(OSError("startup failed")),
    )
    stderr = StringIO()

    result = cli.main([], stdin=StringIO(), stdout=StringIO(), stderr=stderr)

    assert result == 6
    assert stderr.getvalue() == "Startup OS error: startup failed\n"


def test_ctrl_c_is_clean_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_composition(monkeypatch, run_error=KeyboardInterrupt())
    stderr = StringIO()

    result = cli.main([], stdin=StringIO(), stdout=StringIO(), stderr=stderr)

    assert result == 0
    assert stderr.getvalue() == ""


def test_normal_agent_output_uses_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(llm=SimpleNamespace(model="deployment"))
    monkeypatch.setattr(cli, "load_config", lambda path: settings)
    monkeypatch.setattr(cli, "create_responses_client", lambda llm: "client")
    monkeypatch.setattr(cli, "create_command_runner", lambda value: "runner")
    monkeypatch.setattr(
        cli,
        "create_agent_run",
        lambda settings, client, runner, terminal: SimpleNamespace(
            run=lambda: terminal.write("final output")
        ),
    )
    stdout = StringIO()
    stderr = StringIO()

    result = cli.main([], stdin=StringIO(), stdout=stdout, stderr=stderr)

    assert result == 0
    assert stdout.getvalue() == "final output\n"
    assert stderr.getvalue() == ""


def test_application_info_logs_use_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_composition(monkeypatch)
    monkeypatch.setattr(
        cli,
        "create_agent_run",
        lambda *args: SimpleNamespace(
            run=lambda: logging.getLogger("azure_bash_agent.agent").info("event=agent_run_started")
        ),
    )
    stdout = StringIO()
    stderr = StringIO()

    result = cli.main([], stdin=StringIO(), stdout=stdout, stderr=stderr)

    assert result == 0
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "INFO event=agent_run_started\n"


def test_real_command_runner_factory_returns_configured_adapter(tmp_path: Path) -> None:
    settings = Settings(
        llm=LlmSettings("https://example.openai.azure.com/openai/v1/", "model", 10),
        bash=BashSettings("bash", 5, 100),
        repository_root=tmp_path,
    )

    runner = cli.create_command_runner(settings)

    assert isinstance(runner, BashCommandRunner)
    assert runner.run("contains\0nul").status == "invalid_request"


def test_real_terminal_factory_handles_lines_output_and_eof() -> None:
    stdout = StringIO()
    terminal = cli.create_terminal(StringIO("answer\r\n"), stdout)

    assert terminal.prompt("Prompt: ") == "answer"
    terminal.write("result")
    assert stdout.getvalue() == "Prompt: result\n"

    with pytest.raises(EOFError):
        terminal.prompt("Again: ")


def test_real_agent_run_factory_composes_injected_dependencies(tmp_path: Path) -> None:
    settings = Settings(
        llm=LlmSettings("https://example.openai.azure.com/openai/v1/", "model", 10),
        bash=BashSettings("bash", 5, 100),
        repository_root=tmp_path,
    )
    client = SimpleNamespace(create=lambda **kwargs: None)
    runner = SimpleNamespace(run=lambda command: None)
    terminal = cli.create_terminal(StringIO("exit\n"), StringIO())

    agent_run = cli.create_agent_run(settings, client, runner, terminal)

    assert isinstance(agent_run, AgentRun)
    agent_run.run()
