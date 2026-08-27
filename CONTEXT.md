# Bash Agent

This context describes one task-oriented conversation in which an operator can approve model-requested shell commands.

## Language

**Agent Run**:
A conversation that starts with one operator task and ends when the model requests no tool, or when the operator cancels it.
_Avoid_: Session, chat

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
