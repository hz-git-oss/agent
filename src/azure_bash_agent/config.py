"""Load and validate agent configuration."""

from __future__ import annotations

import math
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


class ConfigError(ValueError):
    """Raised when agent configuration is invalid."""


@dataclass(frozen=True)
class LlmSettings:
    """Settings for Azure OpenAI requests."""

    base_url: str
    model: str
    request_timeout_seconds: float


@dataclass(frozen=True)
class BashSettings:
    """Settings for Bash command execution."""

    executable: str
    command_timeout_seconds: float
    max_output_chars: int


@dataclass(frozen=True)
class Settings:
    """Validated settings for one agent process."""

    llm: LlmSettings
    bash: BashSettings
    repository_root: Path


def load_config(
    path: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Load settings from *path* and discover its containing Git repository."""
    environment = os.environ if environ is None else environ
    config_path = path.resolve()
    try:
        with config_path.open("rb") as config_file:
            data = tomllib.load(config_file)
    except FileNotFoundError as error:
        raise ConfigError(f"configuration file not found: {path}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML in configuration: {error}") from error

    llm = _require_section(data, "llm")
    bash = _require_section(data, "bash")
    file_base_url = _require_key(llm, "llm", "base_url")
    file_model = _require_key(llm, "llm", "model")
    request_timeout = _require_key(llm, "llm", "request_timeout_seconds")
    executable = _require_key(bash, "bash", "executable")
    file_command_timeout = _require_key(bash, "bash", "command_timeout_seconds")
    max_output_chars = _require_key(bash, "bash", "max_output_chars")

    base_url = environment.get("AZURE_OPENAI_BASE_URL", file_base_url)
    model = environment.get("AZURE_OPENAI_MODEL", file_model)
    command_timeout = environment.get(
        "AGENT_COMMAND_TIMEOUT_SECONDS",
        file_command_timeout,
    )

    if not isinstance(base_url, str) or not _has_supported_endpoint(base_url):
        raise ConfigError("llm.base_url must be a supported HTTPS Azure v1 endpoint")
    if not isinstance(model, str) or not model.strip():
        raise ConfigError("llm.model must be a nonblank string")
    if not isinstance(executable, str) or not executable.strip():
        raise ConfigError("bash.executable must be a nonblank string")

    normalized_base_url = base_url if base_url.endswith("/") else f"{base_url}/"

    return Settings(
        llm=LlmSettings(
            base_url=normalized_base_url,
            model=model,
            request_timeout_seconds=_positive_number(
                request_timeout,
                "llm.request_timeout_seconds",
            ),
        ),
        bash=BashSettings(
            executable=executable,
            command_timeout_seconds=_positive_number(
                command_timeout,
                "bash.command_timeout_seconds",
                allow_text="AGENT_COMMAND_TIMEOUT_SECONDS" in environment,
            ),
            max_output_chars=_positive_integer(max_output_chars, "bash.max_output_chars"),
        ),
        repository_root=_find_repository_root(config_path),
    )


def _require_section(data: object, name: str) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise ConfigError("configuration must contain TOML tables")
    section = data.get(name)
    if not isinstance(section, Mapping):
        raise ConfigError(f"missing or invalid [{name}] section")
    return section


def _require_key(section: Mapping[str, Any], section_name: str, key: str) -> Any:
    if key not in section:
        raise ConfigError(f"missing configuration key: {section_name}.{key}")
    return section[key]


def _positive_number(value: object, name: str, *, allow_text: bool = False) -> float:
    if allow_text and isinstance(value, str):
        try:
            value = float(value)
        except ValueError as error:
            raise ConfigError(f"{name} must be a finite number greater than zero") from error
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{name} must be a finite number greater than zero")
    converted = float(value)
    if not math.isfinite(converted) or converted <= 0:
        raise ConfigError(f"{name} must be a finite number greater than zero")
    return converted


def _positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{name} must be a positive TOML integer")
    return value


def _has_supported_endpoint(base_url: str) -> bool:
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        return False
    path = parsed.path.removesuffix("/")
    openai_suffix = ".openai.azure.com"
    if parsed.hostname.endswith(openai_suffix):
        resource = parsed.hostname[: -len(openai_suffix)]
        return bool(resource) and "." not in resource and path == "/openai/v1"

    foundry_suffix = ".services.ai.azure.com"
    if not parsed.hostname.endswith(foundry_suffix):
        return False
    resource = parsed.hostname[: -len(foundry_suffix)]
    if not resource or "." in resource:
        return False
    segments = path.split("/")
    return path == "/openai/v1" or (
        len(segments) == 6
        and segments[1:3] == ["api", "projects"]
        and bool(segments[3])
        and segments[4:] == ["openai", "v1"]
    )


def _find_repository_root(config_path: Path) -> Path:
    for candidate in (config_path.parent, *config_path.parent.parents):
        git_entry = candidate / ".git"
        if git_entry.is_dir() or git_entry.is_file():
            return candidate.resolve()
    raise ConfigError(f"configuration file is not inside a Git repository: {config_path}")
