"""Command-line composition root for one Agent Run."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Never, TextIO

from azure.core.exceptions import ClientAuthenticationError
from azure.identity import CredentialUnavailableError
from openai import OpenAIError

from azure_bash_agent.agent import (
    AgentProtocolError,
    AgentRun,
    ResponsesClient,
    TerminalIO,
)
from azure_bash_agent.azure_client import create_responses_client
from azure_bash_agent.bash_runner import BashCommandRunner, CommandRunner
from azure_bash_agent.config import ConfigError, Settings, load_config


class _ArgumentError(ValueError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise _ArgumentError(message)


class _StreamTerminal:
    def __init__(self, stdin: TextIO, stdout: TextIO) -> None:
        self._stdin = stdin
        self._stdout = stdout

    def prompt(self, prompt: str) -> str:
        self._stdout.write(prompt)
        self._stdout.flush()
        line = self._stdin.readline()
        if line == "":
            raise EOFError
        return line.rstrip("\r\n")

    def write(self, text: str) -> None:
        self._stdout.write(f"{text}\n")
        self._stdout.flush()


def create_command_runner(settings: Settings) -> CommandRunner:
    """Create the configured Bash execution adapter."""
    return BashCommandRunner(
        executable=settings.bash.executable,
        repository_root=settings.repository_root,
        timeout_seconds=settings.bash.command_timeout_seconds,
        max_output_chars=settings.bash.max_output_chars,
    )


def create_terminal(stdin: TextIO, stdout: TextIO) -> TerminalIO:
    """Create terminal I/O from injectable streams."""
    return _StreamTerminal(stdin, stdout)


def create_agent_run(
    settings: Settings,
    responses_client: ResponsesClient,
    command_runner: CommandRunner,
    terminal: TerminalIO,
) -> AgentRun:
    """Create one fully composed Agent Run."""
    return AgentRun(
        settings.llm.model,
        responses_client,
        command_runner,
        terminal,
    )


def main(
    argv: list[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one configured task and return a process exit code."""
    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr

    parser = _ArgumentParser(prog="azure-bash-agent")
    parser.add_argument("--config", type=Path, default=Path("agent.toml"))
    try:
        arguments = parser.parse_args(argv)
    except _ArgumentError as error:
        _write_error(error_stream, f"Argument error: {_summary(error)}")
        return 2

    try:
        settings = load_config(arguments.config)
        responses_client = create_responses_client(settings.llm)
        command_runner = create_command_runner(settings)
        terminal = create_terminal(input_stream, output_stream)
        agent_run = create_agent_run(
            settings,
            responses_client,
            command_runner,
            terminal,
        )
        agent_run.run()
    except ConfigError as error:
        _write_error(error_stream, f"Configuration error: {_summary(error)}")
        return 2
    except (ClientAuthenticationError, CredentialUnavailableError) as error:
        _write_error(
            error_stream,
            "Azure authentication failed: "
            f"{_summary(error)}. Check DefaultAzureCredential configuration; "
            "az login is one possible remedy.",
        )
        return 3
    except OpenAIError as error:
        _write_error(error_stream, f"OpenAI request failed: {_summary(error)}")
        return 4
    except AgentProtocolError as error:
        _write_error(error_stream, f"Model protocol error: {_summary(error)}")
        return 5
    except OSError as error:
        _write_error(error_stream, f"Startup OS error: {_summary(error)}")
        return 6
    except KeyboardInterrupt:
        return 0
    return 0


def _summary(error: BaseException) -> str:
    message = str(error).splitlines()[0].strip()
    return message or type(error).__name__


def _write_error(stream: TextIO, message: str) -> None:
    stream.write(f"{message}\n")
    stream.flush()


if __name__ == "__main__":
    raise SystemExit(main())
