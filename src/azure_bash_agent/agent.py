"""Synchronous Agent Run state machine for the approved Bash Tool."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from azure_bash_agent.bash_runner import CommandResult, CommandRunner

DEFAULT_INSTRUCTIONS = (
    "You have one Bash tool. Use it only when necessary to complete the operator's task; "
    "otherwise answer directly."
)

BASH_TOOL: dict[str, object] = {
    "type": "function",
    "name": "bash",
    "description": "Run one Bash command after explicit operator approval.",
    "parameters": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
        "additionalProperties": False,
    },
    "strict": True,
}

_NO_OUTPUT_NOTICE = "Model returned no output."


class AgentProtocolError(RuntimeError):
    """Raised when a model response violates the configured tool protocol."""


class TerminalIO(Protocol):
    """Interactive input and normal output for one Agent Run."""

    def prompt(self, prompt: str) -> str:
        """Read one operator response."""

    def write(self, text: str) -> None:
        """Write normal operator-visible output."""


@dataclass(frozen=True)
class ModelResponse:
    """The small public response shape consumed by AgentRun."""

    output: list[object]
    output_text: str


class ResponsesClient(Protocol):
    """The small Responses API boundary consumed by AgentRun."""

    def create(
        self,
        *,
        model: str,
        input: list[object],
        instructions: str,
        tools: list[dict[str, object]],
        store: bool,
    ) -> ModelResponse:
        """Create one synchronous Model Turn."""


@dataclass(frozen=True)
class _FunctionCall:
    name: str
    arguments: object
    call_id: str


class AgentRun:
    """Coordinate one task through zero or more Tool Rounds."""

    def __init__(
        self,
        model: str,
        responses_client: ResponsesClient,
        command_runner: CommandRunner,
        terminal: TerminalIO,
    ) -> None:
        self._model = model
        self._responses_client = responses_client
        self._command_runner = command_runner
        self._terminal = terminal

    def run(self) -> None:
        """Run until final model output or clean operator cancellation."""
        task = self._read_task()
        if task is None:
            return
        history: list[object] = [{"role": "user", "content": task}]

        while True:
            response = self._responses_client.create(
                model=self._model,
                input=list(history),
                instructions=DEFAULT_INSTRUCTIONS,
                tools=[BASH_TOOL],
                store=False,
            )
            history.extend(response.output)
            calls = self._function_calls(response.output)
            if not calls:
                self._terminal.write(
                    response.output_text if response.output_text.strip() else _NO_OUTPUT_NOTICE
                )
                return

            results: list[object] = []
            for call in calls:
                command = _parse_command(call.arguments)
                if command is None:
                    result = CommandResult(
                        status="invalid_request",
                        exit_code=None,
                        output="",
                        timed_out=False,
                        truncated=False,
                        reason="arguments must be exactly an object with one string command",
                    )
                else:
                    approval = self._read_approval(command)
                    if approval is None:
                        return
                    if approval:
                        result = self._command_runner.run(command)
                    else:
                        result = CommandResult(
                            status="denied",
                            exit_code=None,
                            output="",
                            timed_out=False,
                            truncated=False,
                            reason="command was not approved",
                        )
                results.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": result.to_json(),
                    }
                )
            history.extend(results)

    def _read_task(self) -> str | None:
        while True:
            answer = self._prompt("Task: ")
            if answer is None or answer.strip().casefold() == "exit":
                return None
            if answer.strip():
                return answer

    def _read_approval(self, command: str) -> bool | None:
        answer = self._prompt(f"Run {command!a}? [y/N]: ")
        if answer is None or answer.strip().casefold() == "exit":
            return None
        return answer.strip().casefold() in {"y", "yes"}

    def _prompt(self, prompt: str) -> str | None:
        try:
            return self._terminal.prompt(prompt)
        except (EOFError, KeyboardInterrupt):
            return None

    @staticmethod
    def _function_calls(output: list[object]) -> list[_FunctionCall]:
        calls: list[_FunctionCall] = []
        seen_ids: set[str] = set()
        for item in output:
            if getattr(item, "type", None) != "function_call":
                continue
            name = getattr(item, "name", None)
            call_id = getattr(item, "call_id", None)
            if name != "bash":
                raise AgentProtocolError(f"unknown tool requested: {name!r}")
            if not isinstance(call_id, str) or not call_id.strip() or call_id in seen_ids:
                raise AgentProtocolError("Bash tool call has an unusable call_id")
            seen_ids.add(call_id)
            calls.append(
                _FunctionCall(
                    name=name,
                    arguments=getattr(item, "arguments", None),
                    call_id=call_id,
                )
            )
        return calls


def _parse_command(arguments: object) -> str | None:
    if not isinstance(arguments, str):
        return None
    try:
        parsed: Any = json.loads(arguments)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict) or set(parsed) != {"command"}:
        return None
    command = parsed["command"]
    return command if isinstance(command, str) else None
