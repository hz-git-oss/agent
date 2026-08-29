# Bash Agent

This context describes one continuous terminal conversation in which an operator can submit multiple requests and approve model-requested shell commands.

## Language

**Agent Run**:
One continuous conversation from startup until the operator exits or cancels. It contains zero or more Operator Turns and keeps completed history in memory.
_Avoid_: Session, chat

**Operator Turn**:
One nonblank operator request and the resulting Model Turns and Tool Rounds, ending when the model returns a final response.
_Avoid_: Task, user turn

**Model Turn**:
One model response within an Agent Run.
_Avoid_: Completion, answer

**Tool Round**:
The ordered set of tool requests produced by one Model Turn and the corresponding results returned to the model.
_Avoid_: Tool cycle, function-call batch

**Bash Tool**:
The sole capability through which the model can request a shell command during an Agent Run.
_Avoid_: Shell tool, terminal tool

**Command Approval**:
The operator's explicit consent to execute one command requested through the Bash Tool.
_Avoid_: Confirmation, permission

## Interaction

An Agent Run repeatedly prompts with `You: ` for a nonblank operator request and labels the
final response to each completed Operator Turn with `Assistant: `. Case-insensitive `exit`
and `quit`, EOF, and keyboard interruption end the Agent Run without adding termination
input to its history.

Completed Operator Turns remain in process-local memory for later turns and are discarded
when the Agent Run exits. Content-safe `INFO` events on stderr trace Agent Run, Operator
Turn, and model-request lifecycles without recording conversation or command content.
