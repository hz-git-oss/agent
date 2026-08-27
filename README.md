# Azure Bash Agent

A synchronous terminal agent that uses an Azure OpenAI Responses deployment and can
request individually approved Bash commands. Each process handles one task.

## Requirements and installation

Python 3.12 or newer is required. Install the package from the repository root:

```powershell
py -m pip install .
```

For development, install the test extra in an isolated environment:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install ".[test]"
```

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
azure-bash-agent
```

Or select another configuration file:

```powershell
azure-bash-agent --config path\to\agent.toml
```

The configuration file must be inside a Git repository. The process discovers the
nearest ancestor whose `.git` entry is a directory or worktree metadata file.

Enter one nonblank task. The process ends after the model returns a final response, or
when `exit`, EOF, or `Ctrl+C` cancels interaction. When the model requests Bash, the exact
command is shown in an escaped representation. Only `y` or `yes`, ignoring surrounding
whitespace and case, approves it. Every other response denies that command and sends a
structured denial back to the model.

Each approved command starts a fresh process as:

```text
<executable> -lc <original-command>
```

Commands inherit the agent environment and start at the discovered Git root. Shell state
does not persist between commands, but filesystem changes do. A nonzero command exit is
returned to the model as a normal completed result.

## State and limits

Response items and tool results are retained only in memory for the current Agent Run.
Requests use `store=False` and do not use `previous_response_id`. This application does
not write a transcript, but Azure service-side processing and retention remain governed
by the policies of the selected Azure deployment.

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

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff format --check .
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m mypy
```
