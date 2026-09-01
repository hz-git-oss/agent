import json
import shutil
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

import azure_bash_agent.cli as cli
from azure_bash_agent.agent import ModelResponse

_COMMAND_OUTPUT = "cli-seam-bash-output"
_COMMAND = f"printf '{_COMMAND_OUTPUT}'"
_FIRST_REQUEST = "Run the harmless Bash check"
_FOLLOW_UP = "What happened in the first turn?"
_FIRST_RESPONSE = f"Bash returned {_COMMAND_OUTPUT}."
_RETAINED_RESPONSE = f"The retained Bash output was {_COMMAND_OUTPUT}."


def _bash_call() -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        arguments=json.dumps({"command": _COMMAND}),
        call_id="scripted-call",
        name="bash",
        type="function_call",
    )


def _model_message(text: str, item_id: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id=item_id,
        content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
        role="assistant",
        status="completed",
        type="message",
    )


class _ScriptedResponsesClient:
    def create(
        self,
        *,
        model: str,
        input: list[object],
        instructions: str,
        tools: list[dict[str, object]],
        store: bool,
    ) -> ModelResponse:
        request = _latest_operator_request(input)
        tool_result = _tool_result(input)

        if request == _FIRST_REQUEST and tool_result is None:
            return ModelResponse(
                output=[_bash_call()],
                output_text="",
            )
        if request == _FIRST_REQUEST:
            output = _completed_command_output(tool_result)
            text = (
                f"Bash returned {output}." if output is not None else "Bash result was unavailable."
            )
            return ModelResponse(output=[_model_message(text, "first-final")], output_text=text)
        if request == _FOLLOW_UP:
            retained = (
                _has_operator_request(input, _FIRST_REQUEST)
                and any(isinstance(item, ResponseFunctionToolCall) for item in input)
                and _completed_command_output(tool_result) == _COMMAND_OUTPUT
                and any(
                    isinstance(item, ResponseOutputMessage)
                    and _response_message_text(item) == _FIRST_RESPONSE
                    for item in input
                )
            )
            text = _RETAINED_RESPONSE if retained else "The first Operator Turn was not retained."
            return ModelResponse(output=[_model_message(text, "follow-up-final")], output_text=text)
        raise AssertionError(f"unexpected operator request: {request!r}")


def test_mock_llm_bash_scenario_at_cli_seam(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = _find_bash()
    if executable is None:
        pytest.skip("Bash is unavailable; the CLI Bash scenario requires a Bash executable")

    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    config_path = repository / "agent.toml"
    config_path.write_text(
        f"""
[llm]
base_url = "https://example.openai.azure.com/openai/v1/"
model = "scripted-deployment"
request_timeout_seconds = 5

[bash]
executable = {json.dumps(executable)}
command_timeout_seconds = 5
max_output_chars = 2000
""".strip(),
        encoding="utf-8",
    )
    for name in (
        "AZURE_OPENAI_BASE_URL",
        "AZURE_OPENAI_MODEL",
        "AGENT_COMMAND_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "create_responses_client", lambda settings: _ScriptedResponsesClient())
    stdout = StringIO()
    stderr = StringIO()

    exit_code = cli.main(
        ["--config", str(config_path)],
        stdin=StringIO(f"{_FIRST_REQUEST}\nyes\n{_FOLLOW_UP}\nexit\n"),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stdout.getvalue() == (
        f"You: Run {_COMMAND!a}? [y/N]: Assistant: {_FIRST_RESPONSE}\n"
        f"You: Assistant: {_RETAINED_RESPONSE}\n"
        "You: "
    )
    trace = stderr.getvalue()
    assert "INFO event=bash_tool_requested operator_turn=1 model_turn=1 commands=1\n" in trace
    assert (
        "INFO event=bash_command_execution_started operator_turn=1 model_turn=1 command=1\n"
    ) in trace
    assert (
        "INFO event=bash_command_execution_completed "
        "operator_turn=1 model_turn=1 command=1 status=completed\n"
    ) in trace
    assert _COMMAND not in trace
    assert _COMMAND_OUTPUT not in trace


def _find_bash() -> str | None:
    git_bash = Path(r"C:\Program Files\Git\usr\bin\bash.exe")
    return str(git_bash) if git_bash.is_file() else shutil.which("bash")


def _latest_operator_request(history: list[object]) -> str | None:
    for item in reversed(history):
        if isinstance(item, dict) and item.get("role") == "user":
            content = item.get("content")
            return content if isinstance(content, str) else None
    return None


def _tool_result(history: list[object]) -> dict[str, Any] | None:
    for item in reversed(history):
        if isinstance(item, dict) and item.get("type") == "function_call_output":
            return item
    return None


def _completed_command_output(tool_result: dict[str, Any] | None) -> str | None:
    if tool_result is None or not isinstance(tool_result.get("output"), str):
        return None
    result = json.loads(tool_result["output"])
    if result.get("status") != "completed" or result.get("exit_code") != 0:
        return None
    output = result.get("output")
    return output if isinstance(output, str) else None


def _response_message_text(message: ResponseOutputMessage) -> str | None:
    for content in message.content:
        if isinstance(content, ResponseOutputText):
            return content.text
    return None


def _has_operator_request(history: list[object], request: str) -> bool:
    return any(
        isinstance(item, dict) and item.get("role") == "user" and item.get("content") == request
        for item in history
    )
