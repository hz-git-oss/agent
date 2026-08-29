import logging
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
        if not self.answers:
            raise EOFError
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        assert isinstance(answer, str)
        return answer

    def write(self, text: str) -> None:
        self.output.append(text)


@dataclass
class FakeResponsesClient:
    responses: list[ModelResponse | BaseException]
    requests: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> ModelResponse:
        request = dict(kwargs)
        request["input"] = list(request["input"])
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


@dataclass
class FakeRunner:
    results: list[CommandResult | BaseException] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)

    def run(self, command: str) -> CommandResult:
        self.commands.append(command)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def test_no_tool_response_prints_labeled_final_text_and_prompts_again() -> None:
    terminal = FakeTerminal(["explain this repository"])
    client = FakeResponsesClient([ModelResponse(output=[], output_text="Final answer")])
    run = AgentRun("deployment", client, FakeRunner(), terminal)

    run.run()

    assert terminal.prompts == ["You: ", "You: "]
    assert terminal.output == ["Assistant: Final answer"]
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


def test_completed_operator_turns_continue_with_labels_and_history() -> None:
    first_output = OutputItem("message", "first answer")
    second_output = OutputItem("message", "second answer")
    terminal = FakeTerminal(["first request", "   ", "follow up", "exit"])
    client = FakeResponsesClient(
        [
            ModelResponse([first_output], "first answer"),
            ModelResponse([second_output], "second answer"),
        ]
    )

    AgentRun("deployment", client, FakeRunner(), terminal).run()

    assert terminal.prompts == ["You: ", "You: ", "You: ", "You: "]
    assert terminal.output == ["Assistant: first answer", "Assistant: second answer"]
    assert client.requests[0]["input"] == [{"role": "user", "content": "first request"}]
    assert client.requests[1]["input"] == [
        {"role": "user", "content": "first request"},
        first_output,
        {"role": "user", "content": "follow up"},
    ]


def test_later_operator_turn_receives_completed_bash_tool_history() -> None:
    call = FunctionCall('{"command":"printf ok"}')
    first_final = OutputItem("message", "first answer")
    second_final = OutputItem("message", "follow-up answer")
    result = completed("ok")
    client = FakeResponsesClient(
        [
            ModelResponse([call], "hidden"),
            ModelResponse([first_final], "first answer"),
            ModelResponse([second_final], "follow-up answer"),
        ]
    )

    AgentRun(
        "deployment",
        client,
        FakeRunner([result]),
        FakeTerminal(["first request", "yes", "follow up", "exit"]),
    ).run()

    assert client.requests[2]["input"] == [
        {"role": "user", "content": "first request"},
        call,
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": result.to_json(),
        },
        first_final,
        {"role": "user", "content": "follow up"},
    ]


def test_successful_turn_logs_content_safe_lifecycles(
    caplog: pytest.LogCaptureFixture,
) -> None:
    credential = "credential-secret"
    operator_text = f"operator-secret {credential}"
    model_text = "model-secret"
    deployment = "deployment-secret"
    command = "printf command-secret"
    command_output = "command-output-secret"
    call_id = "random-identifier-secret"
    client = FakeResponsesClient(
        [
            ModelResponse([FunctionCall(f'{{"command":"{command}"}}', call_id)], "hidden-secret"),
            ModelResponse([OutputItem("message", model_text)], model_text),
        ]
    )

    with caplog.at_level(logging.INFO, logger="azure_bash_agent.agent"):
        AgentRun(
            deployment,
            client,
            FakeRunner([completed(command_output)]),
            FakeTerminal([operator_text, "yes", "exit"]),
        ).run()

    records = [record for record in caplog.records if record.name == "azure_bash_agent.agent"]
    assert [record.levelname for record in records] == ["INFO"] * 11
    assert [record.getMessage() for record in records] == [
        "event=agent_run_started",
        "event=operator_turn_started operator_turn=1",
        "event=model_request_started operator_turn=1 model_turn=1",
        ("event=model_request_completed operator_turn=1 model_turn=1 output_items=1 tool_calls=1"),
        "event=bash_tool_requested operator_turn=1 model_turn=1 commands=1",
        ("event=bash_command_execution_started operator_turn=1 model_turn=1 command=1"),
        (
            "event=bash_command_execution_completed "
            "operator_turn=1 model_turn=1 command=1 status=completed"
        ),
        "event=model_request_started operator_turn=1 model_turn=2",
        ("event=model_request_completed operator_turn=1 model_turn=2 output_items=1 tool_calls=0"),
        "event=operator_turn_completed operator_turn=1 model_turns=2",
        "event=agent_run_exited reason=operator_exit completed_turns=1",
    ]
    log_text = caplog.text
    for sensitive_text in (
        credential,
        operator_text,
        model_text,
        deployment,
        command,
        command_output,
        call_id,
        "hidden-secret",
    ):
        assert sensitive_text not in log_text


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

    assert terminal.output == ["Assistant: Model returned no output."]


def test_blank_operator_request_reprompts() -> None:
    terminal = FakeTerminal(["   ", "task"])
    client = FakeResponsesClient([ModelResponse([], "answer")])

    AgentRun("deployment", client, FakeRunner(), terminal).run()

    assert terminal.prompts == ["You: ", "You: ", "You: "]
    assert client.requests[0]["input"] == [{"role": "user", "content": "task"}]


@pytest.mark.parametrize("answer", [" EXIT ", " qUiT ", EOFError(), KeyboardInterrupt()])
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
    assert terminal.output == ["Assistant: finished"]
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

    assert terminal.output == ["Assistant: final"]


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


@pytest.mark.parametrize("answer", ["exit", " QUIT ", EOFError(), KeyboardInterrupt()])
def test_approval_cancellation_stops_without_followup(answer: object) -> None:
    client = FakeResponsesClient([ModelResponse([FunctionCall('{"command":"x"}')], "")])
    runner = FakeRunner()

    AgentRun("deployment", client, runner, FakeTerminal(["task", answer])).run()

    assert runner.commands == []
    assert len(client.requests) == 1


def test_model_request_keyboard_interrupt_ends_agent_run_with_complete_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeResponsesClient([KeyboardInterrupt()])

    with caplog.at_level(logging.INFO, logger="azure_bash_agent.agent"):
        AgentRun(
            "deployment",
            client,
            FakeRunner(),
            FakeTerminal(["request"]),
        ).run()

    records = [record for record in caplog.records if record.name == "azure_bash_agent.agent"]
    assert [record.getMessage() for record in records] == [
        "event=agent_run_started",
        "event=operator_turn_started operator_turn=1",
        "event=model_request_started operator_turn=1 model_turn=1",
        "event=model_request_cancelled operator_turn=1 model_turn=1",
        "event=operator_turn_cancelled operator_turn=1 model_turns=0",
        "event=agent_run_exited reason=operator_exit completed_turns=0",
    ]


def test_command_keyboard_interrupt_ends_agent_run_with_complete_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeResponsesClient([ModelResponse([FunctionCall('{"command":"x"}')], "hidden")])
    terminal = FakeTerminal(["request", "yes"])

    with caplog.at_level(logging.INFO, logger="azure_bash_agent.agent"):
        AgentRun(
            "deployment",
            client,
            FakeRunner([KeyboardInterrupt()]),
            terminal,
        ).run()

    assert len(client.requests) == 1
    assert terminal.output == []
    records = [record for record in caplog.records if record.name == "azure_bash_agent.agent"]
    assert [record.getMessage() for record in records] == [
        "event=agent_run_started",
        "event=operator_turn_started operator_turn=1",
        "event=model_request_started operator_turn=1 model_turn=1",
        "event=model_request_completed operator_turn=1 model_turn=1 output_items=1 tool_calls=1",
        "event=bash_tool_requested operator_turn=1 model_turn=1 commands=1",
        ("event=bash_command_execution_started operator_turn=1 model_turn=1 command=1"),
        "event=operator_turn_cancelled operator_turn=1 model_turns=1",
        "event=agent_run_exited reason=operator_exit completed_turns=0",
    ]


def test_command_execution_failure_logs_content_safe_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    command = "printf command-secret"
    failure_text = "runner-error-secret"
    client = FakeResponsesClient([ModelResponse([FunctionCall(f'{{"command":"{command}"}}')], "")])

    with (
        caplog.at_level(logging.INFO, logger="azure_bash_agent.agent"),
        pytest.raises(RuntimeError, match=failure_text),
    ):
        AgentRun(
            "deployment",
            client,
            FakeRunner([RuntimeError(failure_text)]),
            FakeTerminal(["request", "yes"]),
        ).run()

    assert "event=bash_command_execution_failed operator_turn=1 model_turn=1 command=1" in [
        record.getMessage() for record in caplog.records
    ]
    assert command not in caplog.text
    assert failure_text not in caplog.text


def test_command_execution_status_is_redacted_in_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    unsafe_status = "unexpected-sensitive-status"
    client = FakeResponsesClient(
        [
            ModelResponse([FunctionCall('{"command":"x"}')], ""),
            ModelResponse([], "done"),
        ]
    )
    result = CommandResult(unsafe_status, None, "", False, False)

    with caplog.at_level(logging.INFO, logger="azure_bash_agent.agent"):
        AgentRun(
            "deployment",
            client,
            FakeRunner([result]),
            FakeTerminal(["request", "yes"]),
        ).run()

    assert (
        "event=bash_command_execution_completed "
        "operator_turn=1 model_turn=1 command=1 status=unknown"
        in [record.getMessage() for record in caplog.records]
    )
    assert unsafe_status not in caplog.text


def test_approval_cancellation_logs_operator_turn_before_exit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeResponsesClient([ModelResponse([FunctionCall('{"command":"x"}')], "")])

    with caplog.at_level(logging.INFO, logger="azure_bash_agent.agent"):
        AgentRun(
            "deployment",
            client,
            FakeRunner(),
            FakeTerminal(["request", "exit"]),
        ).run()

    assert [record.getMessage() for record in caplog.records] == [
        "event=agent_run_started",
        "event=operator_turn_started operator_turn=1",
        "event=model_request_started operator_turn=1 model_turn=1",
        ("event=model_request_completed operator_turn=1 model_turn=1 output_items=1 tool_calls=1"),
        "event=bash_tool_requested operator_turn=1 model_turn=1 commands=1",
        "event=operator_turn_cancelled operator_turn=1 model_turns=1",
        "event=agent_run_exited reason=operator_exit completed_turns=0",
    ]


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
    assert terminal.prompts == ["You: "]


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
