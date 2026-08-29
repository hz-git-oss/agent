# Azure Bash Agent

A synchronous terminal agent that uses an Azure OpenAI Responses deployment and can
request individually approved Bash commands. Each process hosts one continuous Agent Run
with multiple Operator Turns.

## Requirements and installation

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) are required. From the
repository root, create the managed environment and install the package with its
development dependencies:

```powershell
uv sync
```

uv creates and manages `.venv`; activation is not required. To install only runtime
dependencies, use `uv sync --no-dev`.

## Configuration

Copy `agent.example.toml` to the ignored local file `agent.toml`, then edit its values:

```powershell
Copy-Item agent.example.toml agent.toml
```

The settings are:

- `llm.base_url`: Complete Azure HTTPS v1 endpoint.
- `llm.model`: Azure deployment name, not the underlying model family name unless the
	deployment was deliberately given that same name.
- `llm.request_timeout_seconds`: Positive, finite Responses API request timeout.
- `bash.executable`: Nonblank Bash executable name or path.
- `bash.command_timeout_seconds`: Positive, finite timeout for each approved command.
- `bash.max_output_chars`: Positive integer cap for model-visible combined command output.

Three environment variables override TOML before validation:

- `AZURE_OPENAI_BASE_URL` overrides `llm.base_url`.
- `AZURE_OPENAI_MODEL` overrides `llm.model`.
- `AGENT_COMMAND_TIMEOUT_SECONDS` overrides `bash.command_timeout_seconds`.

Exactly these endpoint forms are supported, with or without the final slash:

```text
https://<resource>.openai.azure.com/openai/v1/
https://<resource>.services.ai.azure.com/openai/v1/
https://<resource>.services.ai.azure.com/api/projects/<project>/openai/v1/
```

Credentials, ports, queries, fragments, empty resource/project names, and extra path
segments are rejected.

## Azure access

Authentication uses an unchanged `DefaultAzureCredential` and requests the
`https://ai.azure.com/.default` scope at startup. Supported credential sources include
environment credentials, workload identity, managed identity, shared developer-tool
caches, Azure CLI, Azure PowerShell, and Azure Developer CLI, according to the installed
Azure Identity version and environment. Running `az login` is one possible local setup;
it is not the only supported source.

The authenticated principal needs inference data-plane access to the selected deployment.
Assign the least-privileged role appropriate to the endpoint, commonly **Cognitive
Services OpenAI User** for an Azure OpenAI resource or **Azure AI User** at the relevant
Foundry project/resource scope. Resource-management roles such as Contributor do not by
themselves guarantee inference data-plane access.

## Running

Use the default `agent.toml` in the current directory:

```powershell
uv run azure-bash-agent
```

Or select another configuration file:

```powershell
uv run azure-bash-agent --config path\to\agent.toml
```

The configuration file must be inside a Git repository. The process discovers the
nearest ancestor whose `.git` entry is a directory or worktree metadata file.

The process repeatedly prompts with `You: ` for an Operator Turn and labels each final
model response `Assistant: `. Blank input is ignored. Enter `exit` or `quit`, ignoring
surrounding whitespace and case, or use EOF or `Ctrl+C` to end the Agent Run cleanly;
termination input is handled locally and is not sent to the model.

Completed Operator Turns, including Model Turns and Tool Rounds, remain in memory and are
sent with later requests so follow-up questions retain context. History is discarded when
the process exits.

When the model requests Bash, the exact command is shown in an escaped representation.
Only `y` or `yes`, ignoring surrounding whitespace and case, approves it. Every other
response denies that command and sends a structured denial back to the model. Each command
requires separate Command Approval, and model text accompanying a Bash Tool request stays
hidden.

Each approved command starts a fresh process as:

```text
<executable> -lc <original-command>
```

Commands inherit the agent environment and start at the discovered Git root. Shell state
does not persist between commands, but filesystem changes do. A nonzero command exit is
returned to the model as a normal completed result.

## State, tracing, and limits

Response items and tool results from completed Operator Turns are retained only in memory
for the current Agent Run. Requests use `store=False` and do not use
`previous_response_id`. This application does not write a transcript, but Azure
service-side processing and retention remain governed by the policies of the selected
Azure deployment.

Deterministic `INFO` events on stderr trace Agent Run, Operator Turn, and model-request
lifecycles using turn numbers, statuses, and counts. These operational traces omit prompts,
model text, commands, command output, credentials, configuration values, random identifiers,
and durations. Prompts and assistant responses remain on stdout.

Combined stdout and stderr are decoded as UTF-8 with replacement. Output over
`bash.max_output_chars` is reduced to approximately equal head and tail portions with an
explicit omitted-character marker. On timeout, the runner performs best-effort process
tree cleanup: POSIX process-group termination escalates from `SIGTERM` to `SIGKILL`, and
Windows uses `taskkill /T /F` when available. Cleanup is not hardened containment.

> [!WARNING]
> Bash execution is not a security sandbox. Starting in the repository root is only a
> working-directory convention, not filesystem containment. Approved commands can read,
> modify, or delete files outside the repository and can access inherited credentials and
> network resources available to the process.

## Development checks

The test suite includes a deterministic end-to-end scenario at the in-process CLI boundary.
It loads a temporary TOML configuration, uses production terminal adaptation and Agent Run
orchestration, and executes an approved harmless command through the real Bash runner. Only
the external Responses client is replaced with a scripted mock LLM, so the scenario requires
neither Azure authentication nor network access. It skips with an explicit reason when Bash
is unavailable.

```powershell
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run mypy
```
