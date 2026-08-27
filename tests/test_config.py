from pathlib import Path

import pytest

from azure_bash_agent.config import ConfigError, load_config


def test_load_config_reads_settings_and_discovers_repository_root(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    config_path = repository / "config" / "agent.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        """
[llm]
base_url = "https://example.openai.azure.com/openai/v1"
model = "deployment-name"
request_timeout_seconds = 60

[bash]
executable = "bash"
command_timeout_seconds = 30
max_output_chars = 20000
""".strip(),
        encoding="utf-8",
    )

    settings = load_config(config_path, environ={})

    assert settings.llm.base_url == "https://example.openai.azure.com/openai/v1/"
    assert settings.llm.model == "deployment-name"
    assert settings.llm.request_timeout_seconds == 60
    assert settings.bash.executable == "bash"
    assert settings.bash.command_timeout_seconds == 30
    assert settings.bash.max_output_chars == 20_000
    assert settings.repository_root == repository


def test_environment_overrides_supported_configuration_values(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    config_path = repository / "agent.toml"
    config_path.write_text(
        """
[llm]
base_url = "https://file.openai.azure.com/openai/v1/"
model = "file-model"
request_timeout_seconds = 60

[bash]
executable = "bash"
command_timeout_seconds = 30
max_output_chars = 20000
""".strip(),
        encoding="utf-8",
    )

    settings = load_config(
        config_path,
        environ={
            "AZURE_OPENAI_BASE_URL": "https://environment.services.ai.azure.com/openai/v1",
            "AZURE_OPENAI_MODEL": "environment-model",
            "AGENT_COMMAND_TIMEOUT_SECONDS": "45",
        },
    )

    assert settings.llm.base_url == "https://environment.services.ai.azure.com/openai/v1/"
    assert settings.llm.model == "environment-model"
    assert settings.bash.command_timeout_seconds == 45


def test_load_config_rejects_non_https_base_url(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    config_path = repository / "agent.toml"
    config_path.write_text(
        """
[llm]
base_url = "http://example.openai.azure.com/openai/v1/"
model = "deployment-name"
request_timeout_seconds = 60

[bash]
executable = "bash"
command_timeout_seconds = 30
max_output_chars = 20000
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"llm\.base_url"):
        load_config(config_path, environ={})


def test_load_config_accepts_foundry_resource_endpoint(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    config_path = repository / "agent.toml"
    config_path.write_text(
        """
[llm]
base_url = "https://example.services.ai.azure.com/openai/v1/"
model = "deployment-name"
request_timeout_seconds = 60

[bash]
executable = "bash"
command_timeout_seconds = 30
max_output_chars = 20000
""".strip(),
        encoding="utf-8",
    )

    settings = load_config(config_path, environ={})

    assert settings.llm.base_url == "https://example.services.ai.azure.com/openai/v1/"


def test_load_config_accepts_foundry_project_endpoint(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    config_path = repository / "agent.toml"
    config_path.write_text(
        """
[llm]
base_url = "https://example.services.ai.azure.com/api/projects/project-name/openai/v1/"
model = "deployment-name"
request_timeout_seconds = 60

[bash]
executable = "bash"
command_timeout_seconds = 30
max_output_chars = 20000
""".strip(),
        encoding="utf-8",
    )

    settings = load_config(config_path, environ={})

    assert settings.llm.base_url == (
        "https://example.services.ai.azure.com/api/projects/project-name/openai/v1/"
    )


def test_load_config_rejects_non_azure_host(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    config_path = repository / "agent.toml"
    config_path.write_text(
        """
[llm]
base_url = "https://example.invalid/openai/v1/"
model = "deployment-name"
request_timeout_seconds = 60

[bash]
executable = "bash"
command_timeout_seconds = 30
max_output_chars = 20000
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"llm\.base_url"):
        load_config(config_path, environ={})


def test_load_config_rejects_wrong_v1_path(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    config_path = repository / "agent.toml"
    config_path.write_text(
        """
[llm]
base_url = "https://example.openai.azure.com/openai/v2/"
model = "deployment-name"
request_timeout_seconds = 60

[bash]
executable = "bash"
command_timeout_seconds = 30
max_output_chars = 20000
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"llm\.base_url"):
        load_config(config_path, environ={})


def test_load_config_rejects_base_url_query(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    config_path = repository / "agent.toml"
    config_path.write_text(
        """
[llm]
base_url = "https://example.openai.azure.com/openai/v1/?ignored=true"
model = "deployment-name"
request_timeout_seconds = 60

[bash]
executable = "bash"
command_timeout_seconds = 30
max_output_chars = 20000
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"llm\.base_url"):
        load_config(config_path, environ={})


VALID_CONFIG = """
[llm]
base_url = "https://example.openai.azure.com/openai/v1/"
model = "deployment-name"
request_timeout_seconds = 60

[bash]
executable = "bash"
command_timeout_seconds = 30
max_output_chars = 20000
""".strip()


def write_config(tmp_path: Path, contents: str = VALID_CONFIG, *, git_file: bool = False) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    git_entry = repository / ".git"
    if git_file:
        git_entry.write_text("gitdir: ../worktrees/repository\n", encoding="utf-8")
    else:
        git_entry.mkdir()
    config_path = repository / "config" / "agent.toml"
    config_path.parent.mkdir()
    config_path.write_text(contents, encoding="utf-8")
    return config_path


@pytest.mark.parametrize(
    "base_url",
    [
        "https://example.openai.azure.com/openai/v1/#fragment",
        "https://user@example.openai.azure.com/openai/v1/",
        "https://example.openai.azure.com:443/openai/v1/",
        "https://example.openai.azure.com/openai/v1//",
        "https://example.openai.azure.com/openai/v1/extra",
        "https://openai.azure.com/openai/v1/",
        "https://services.ai.azure.com/openai/v1/",
        "https://example.services.ai.azure.com/api/projects//openai/v1/",
    ],
)
def test_load_config_rejects_unsupported_endpoint_shapes(tmp_path: Path, base_url: str) -> None:
    config_path = write_config(
        tmp_path,
        VALID_CONFIG.replace(
            "https://example.openai.azure.com/openai/v1/",
            base_url,
        ),
    )

    with pytest.raises(ConfigError, match=r"llm\.base_url"):
        load_config(config_path, environ={})


def test_load_config_wraps_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.toml", environ={})


def test_load_config_wraps_malformed_toml(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, "[llm\ninvalid")

    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(config_path, environ={})


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (VALID_CONFIG.replace("[llm]", "[other]"), r"\[llm\]"),
        (VALID_CONFIG.replace("[bash]", "[other]"), r"\[bash\]"),
        (VALID_CONFIG.replace("[llm]", "llm = 1\n[other]"), r"\[llm\]"),
        (VALID_CONFIG.replace("[bash]", "bash = 1\n[other]"), r"\[bash\]"),
        (
            VALID_CONFIG.replace('base_url = "https://example.openai.azure.com/openai/v1/"\n', ""),
            "llm.base_url",
        ),
        (VALID_CONFIG.replace('model = "deployment-name"\n', ""), "llm.model"),
        (VALID_CONFIG.replace("request_timeout_seconds = 60\n", ""), "llm.request_timeout_seconds"),
        (VALID_CONFIG.replace('executable = "bash"\n', ""), "bash.executable"),
        (
            VALID_CONFIG.replace("command_timeout_seconds = 30\n", ""),
            "bash.command_timeout_seconds",
        ),
        (VALID_CONFIG.replace("max_output_chars = 20000", ""), "bash.max_output_chars"),
    ],
)
def test_load_config_rejects_missing_or_invalid_structure(
    tmp_path: Path, contents: str, message: str
) -> None:
    config_path = write_config(tmp_path, contents)

    with pytest.raises(ConfigError, match=message):
        load_config(config_path, environ={})


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            'base_url = "https://example.openai.azure.com/openai/v1/"',
            "base_url = 1",
            "llm.base_url",
        ),
        ('model = "deployment-name"', "model = 1", "llm.model"),
        ('model = "deployment-name"', 'model = "   "', "llm.model"),
        ('executable = "bash"', "executable = 1", "bash.executable"),
        ('executable = "bash"', 'executable = "   "', "bash.executable"),
    ],
)
def test_load_config_rejects_invalid_string_settings(
    tmp_path: Path, old: str, new: str, message: str
) -> None:
    config_path = write_config(tmp_path, VALID_CONFIG.replace(old, new))

    with pytest.raises(ConfigError, match=message):
        load_config(config_path, environ={})


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "true", '"60"'])
@pytest.mark.parametrize(
    ("key", "message"),
    [
        ("request_timeout_seconds", "llm.request_timeout_seconds"),
        ("command_timeout_seconds", "bash.command_timeout_seconds"),
    ],
)
def test_load_config_rejects_invalid_timeouts(
    tmp_path: Path, key: str, message: str, value: str
) -> None:
    original = "60" if key == "request_timeout_seconds" else "30"
    config_path = write_config(
        tmp_path, VALID_CONFIG.replace(f"{key} = {original}", f"{key} = {value}")
    )

    with pytest.raises(ConfigError, match=message):
        load_config(config_path, environ={})


def test_load_config_validates_command_timeout_environment_override(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)

    with pytest.raises(ConfigError, match=r"bash\.command_timeout_seconds"):
        load_config(config_path, environ={"AGENT_COMMAND_TIMEOUT_SECONDS": "invalid"})


def test_load_config_validates_all_environment_overrides_after_precedence(tmp_path: Path) -> None:
    config_path = write_config(tmp_path)

    with pytest.raises(ConfigError, match=r"llm\.model"):
        load_config(config_path, environ={"AZURE_OPENAI_MODEL": " "})


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "true", '"10"'])
def test_load_config_rejects_invalid_output_cap(tmp_path: Path, value: str) -> None:
    config_path = write_config(
        tmp_path,
        VALID_CONFIG.replace("max_output_chars = 20000", f"max_output_chars = {value}"),
    )

    with pytest.raises(ConfigError, match=r"bash\.max_output_chars"):
        load_config(config_path, environ={})


def test_load_config_accepts_worktree_metadata_file_and_returns_absolute_root(
    tmp_path: Path,
) -> None:
    config_path = write_config(tmp_path, git_file=True)

    settings = load_config(config_path, environ={})

    assert settings.repository_root == config_path.parent.parent.resolve()
    assert settings.repository_root.is_absolute()


def test_load_config_wraps_missing_git_root(tmp_path: Path) -> None:
    config_path = tmp_path / "agent.toml"
    config_path.write_text(VALID_CONFIG, encoding="utf-8")

    with pytest.raises(ConfigError, match="Git repository"):
        load_config(config_path, environ={})
