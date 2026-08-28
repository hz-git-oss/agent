from dataclasses import dataclass, field
from typing import Any

import pytest

from azure_bash_agent.agent import (
    BASH_TOOL,
    DEFAULT_INSTRUCTIONS,
    AgentProtocolError,
    AgentRun,
    ModelResponse,
)
from azure_bash_agent.bash_runner import CommandResult


@dataclass
class FakeTerminal:
    answers: list[object]
    prompts: list[str] = field(default_factory=list)
    output: list[str] = field(default_factory=list)

    def prompt(self, prompt: str) -> str:
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        assert isinstance(answer, str)
        return answer

    def write(self, text: str) -> None:
        self.output.append(text)


@dataclass
class FakeResponsesClient:
    responses: list[ModelResponse]
    requests: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> ModelResponse:
        request = dict(kwargs)
        request["input"] = list(request["input"])
        self.requests.append(request)
        return self.responses.pop(0)


@dataclass
class FakeRunner:
    results: list[CommandResult] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)

    def run(self, command: str) -> CommandResult:
        self.commands.append(command)
        return self.results.pop(0)


def test_no_tool_response_prints_final_text_and_ends_after_one_request() -> None:
    terminal = FakeTerminal(["explain this repository"])
    client = FakeResponsesClient([ModelResponse(output=[], output_text="Final answer")])
    run = AgentRun("deployment", client, FakeRunner(), terminal)

    run.run()

    assert terminal.output == ["Final answer"]
    assert len(client.requests) == 1
    assert client.requests[0]["input"] == [{"role": "user", "content": "explain this repository"}]


@dataclass(frozen=True)
class FunctionCall:
    arguments: object
    call_id: object = "call-1"
    name: object = "bash"
    type: str = "function_call"


@dataclass(frozen=True)
class OutputItem:
    type: str
    text: str = ""


def completed(output: str = "done", exit_code: int = 0) -> CommandResult:
    return CommandResult("completed", exit_code, output, False, False)


def test_request_uses_exact_tool_contract_and_local_state_options() -> None:
    terminal = FakeTerminal(["task"])
    client = FakeResponsesClient([ModelResponse([], "answer")])

    AgentRun("deployment", client, FakeRunner(), terminal).run()

    request = client.requests[0]
    assert request["model"] == "deployment"
    assert request["instructions"] == DEFAULT_INSTRUCTIONS
    assert request["tools"] == [BASH_TOOL]
    assert request["store"] is False
    assert "previous_response_id" not in request
    assert BASH_TOOL == {
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


def test_whitespace_only_model_output_prints_notice() -> None:
    terminal = FakeTerminal(["task"])

    AgentRun(
        "deployment",
        FakeResponsesClient([ModelResponse([], " \n")]),
        FakeRunner(),
        terminal,
    ).run()

    assert terminal.output == ["Model returned no output."]


def test_blank_initial_task_reprompts() -> None:
    terminal = FakeTerminal(["   ", "task"])
    client = FakeResponsesClient([ModelResponse([], "answer")])

    AgentRun("deployment", client, FakeRunner(), terminal).run()

    assert terminal.prompts == ["Task: ", "Task: "]
    assert client.requests[0]["input"] == [{"role": "user", "content": "task"}]


@pytest.mark.parametrize("answer", [" EXIT ", EOFError(), KeyboardInterrupt()])
def test_initial_cancellation_does_not_request_model(answer: object) -> None:
    client = FakeResponsesClient([])

    AgentRun("deployment", client, FakeRunner(), FakeTerminal([answer])).run()

    assert client.requests == []


def test_approved_bash_call_executes_and_returns_structured_output() -> None:
    call = FunctionCall('{"command":"printf ok"}')
    final = OutputItem("message", "finished")
    client = FakeResponsesClient(
        [ModelResponse([call], "hidden"), ModelResponse([final], "finished")]
    )
    runner = FakeRunner([completed("ok")])
    terminal = FakeTerminal(["task", "yes"])

    AgentRun("deployment", client, runner, terminal).run()

    assert runner.commands == ["printf ok"]
    assert terminal.output == ["finished"]
    tool_output = client.requests[1]["input"][-1]
    assert tool_output == {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": completed("ok").to_json(),
    }


def test_intermediate_model_text_is_suppressed() -> None:
    client = FakeResponsesClient(
        [
            ModelResponse(
                [OutputItem("message", "secret"), FunctionCall('{"command":"x"}')], "secret"
            ),
            ModelResponse([], "final"),
        ]
    )
    terminal = FakeTerminal(["task", "n"])

    AgentRun("deployment", client, FakeRunner(), terminal).run()

    assert terminal.output == ["final"]


def test_denial_skips_runner_and_returns_tool_result() -> None:
    call = FunctionCall('{"command":"danger"}')
    client = FakeResponsesClient([ModelResponse([call], ""), ModelResponse([], "done")])
    runner = FakeRunner()

    AgentRun("deployment", client, runner, FakeTerminal(["task", "anything"])).run()

    assert runner.commands == []
    assert '"status": "denied"' in client.requests[1]["input"][-1]["output"]


def test_multiple_calls_execute_in_order_and_share_one_followup() -> None:
    calls = [
        FunctionCall('{"command":"one"}', "call-1"),
        FunctionCall('{"command":"two"}', "call-2"),
    ]
    client = FakeResponsesClient([ModelResponse(calls, ""), ModelResponse([], "done")])
    runner = FakeRunner([completed("first"), completed("second")])

    AgentRun("deployment", client, runner, FakeTerminal(["task", "y", "yes"])).run()

    assert runner.commands == ["one", "two"]
    assert len(client.requests) == 2
    outputs = client.requests[1]["input"][-2:]
    assert [item["call_id"] for item in outputs] == ["call-1", "call-2"]


def test_response_items_and_results_remain_in_exact_local_order() -> None:
    message = OutputItem("reasoning", "opaque")
    call = FunctionCall('{"command":"one"}')
    final = OutputItem("unknown", "retained")
    client = FakeResponsesClient(
        [ModelResponse([message, call], ""), ModelResponse([final], "done")]
    )

    AgentRun("deployment", client, FakeRunner([completed()]), FakeTerminal(["task", "y"])).run()

    followup = client.requests[1]["input"]
    assert followup[1:3] == [message, call]
    assert followup[3]["type"] == "function_call_output"
    assert client.requests[0]["input"] == [{"role": "user", "content": "task"}]


def test_nonzero_bash_result_continues_agent_run() -> None:
    call = FunctionCall('{"command":"exit 4"}')
    client = FakeResponsesClient([ModelResponse([call], ""), ModelResponse([], "recovered")])

    AgentRun(
        "deployment",
        client,
        FakeRunner([completed("failed", 4)]),
        FakeTerminal(["task", "y"]),
    ).run()

    assert '"exit_code": 4' in client.requests[1]["input"][-1]["output"]


@pytest.mark.parametrize("answer", ["exit", EOFError(), KeyboardInterrupt()])
def test_approval_cancellation_stops_without_followup(answer: object) -> None:
    client = FakeResponsesClient([ModelResponse([FunctionCall('{"command":"x"}')], "")])
    runner = FakeRunner()

    AgentRun("deployment", client, runner, FakeTerminal(["task", answer])).run()

    assert runner.commands == []
    assert len(client.requests) == 1


def test_approval_display_is_safely_escaped_but_command_is_unchanged() -> None:
    command = "line1\n\x1b\0caf\xe9\x07"
    call = FunctionCall('{"command":"line1\\n\\u001b\\u0000caf\\u00e9\\u0007"}')
    runner = FakeRunner([completed()])
    terminal = FakeTerminal(["task", "y"])
    client = FakeResponsesClient([ModelResponse([call], ""), ModelResponse([], "done")])

    AgentRun("deployment", client, runner, terminal).run()

    assert terminal.prompts[1] == f"Run {command!a}? [y/N]: "
    assert runner.commands == [command]


@pytest.mark.parametrize(
    "arguments",
    [
        "not json",
        "[]",
        "{}",
        '{"other":"x"}',
        '{"command":"x","extra":1}',
        '{"command":1}',
        None,
    ],
)
def test_malformed_arguments_recover_with_invalid_request(arguments: object) -> None:
    call = FunctionCall(arguments)
    client = FakeResponsesClient([ModelResponse([call], ""), ModelResponse([], "done")])
    runner = FakeRunner()

    AgentRun("deployment", client, runner, FakeTerminal(["task"])).run()

    assert runner.commands == []
    assert '"status": "invalid_request"' in client.requests[1]["input"][-1]["output"]


@pytest.mark.parametrize(
    "bad_call",
    [
        FunctionCall("{}", name="other"),
        FunctionCall("{}", call_id=""),
        FunctionCall("{}", call_id=None),
    ],
)
def test_protocol_errors_happen_before_any_command_side_effect(bad_call: FunctionCall) -> None:
    valid = FunctionCall('{"command":"must not run"}', "valid")
    runner = FakeRunner([completed()])
    terminal = FakeTerminal(["task", "y"])

    with pytest.raises(AgentProtocolError):
        AgentRun(
            "deployment",
            FakeResponsesClient([ModelResponse([valid, bad_call], "")]),
            runner,
            terminal,
        ).run()

    assert runner.commands == []
    assert terminal.prompts == ["Task: "]


def test_two_tool_rounds_have_no_artificial_round_limit() -> None:
    first = FunctionCall('{"command":"one"}', "first")
    second = FunctionCall('{"command":"two"}', "second")
    client = FakeResponsesClient(
        [
            ModelResponse([first], ""),
            ModelResponse([second], ""),
            ModelResponse([], "done"),
        ]
    )
    runner = FakeRunner([completed(), completed()])

    AgentRun("deployment", client, runner, FakeTerminal(["task", "y", "y"])).run()

    assert runner.commands == ["one", "two"]
    assert len(client.requests) == 3
