# Azure Bash Agent implementation handoff

## Next-session focus

Continue and complete the confirmed implementation, preserving every resolved design decision below. Work is on branch `feature/azure-bash-agent` in `C:\Ai\agent`.

## Current state

- Baseline files now exist at `pyproject.toml`, `agent.example.toml`, `CONTEXT.md`, `src/azure_bash_agent/__init__.py`, `src/azure_bash_agent/config.py`, and `tests/test_config.py`.
- `.gitignore` now ignores local `agent.toml`.
- Configuration loading has working tests for TOML loading, URL normalization, Git-root discovery, and environment precedence.
- The latest edit imported `ConfigError` in `tests/test_config.py`, but `ConfigError` has not been implemented and no validation test has yet been added. Current command `py -m pytest tests/test_config.py` fails during collection with `ImportError: cannot import name 'ConfigError'`—this is the active red TDD state.
- Task status: #1 complete; #2 in progress; #3–#6 pending. Use `TaskList` for the task descriptions.
- No commit has been made. The repository contained unrelated/pre-existing untracked agent configuration and documentation files before implementation. Do not discard them. Inspect `git status` before staging and commit only intended project files while respecting the `/implement` requirement to commit completed work.
- `py` is available (Python 3.14.5); bare `python` resolves to an unavailable Windows Store alias. `pytest` is installed globally. Ruff and mypy were not globally installed when checked. Git Bash is at `C:\Program Files\Git\usr\bin\bash.exe` and is also visible as `bash` in the current shell.

## Confirmed product decisions

### Run and interaction model

- Implement a synchronous terminal REPL for one task per process invocation.
- Canonical terms are recorded in `CONTEXT.md`: **Agent Run**, **Model Turn**, **Tool Round**, **Bash Tool**, and **Command Approval**.
- Prompt for exactly one nonblank task at startup. A blank task reprompts.
- Each Model Turn may request Bash calls. Execute the Tool Round, return all ordered results together, and continue.
- When a Model Turn requests no tool, print its final text and end the entire Agent Run successfully.
- If a no-tool response contains no printable text, print a short notice and terminate successfully.
- Do not stream model responses.
- Do not display intermediate model text accompanying tool calls; display only final no-tool text. Approval prompts provide intermediate visibility.
- If multiple Bash calls are requested in one Model Turn, process them sequentially and return all results in one follow-up request.
- A nonzero command exit code is a normal tool result and does not terminate the run.
- Do not impose a Tool Round limit. The operator can cancel with `exit`, EOF, or `Ctrl+C`.
- Recognize trimmed `exit` case-insensitively at every interactive input point, including initial task and command approvals; terminate immediately without another model request.
- EOF and `Ctrl+C` are clean cancellation paths.

### OpenAI SDK and Azure identity

- Use the standard OpenAI Python SDK and its Responses API—not legacy Chat Completions and not the dated Azure API client pattern.
- Construct `OpenAI(base_url=<complete-v1-url>, api_key=<callable-token-provider>, timeout=<configured timeout>)`.
- Build the callable with `get_bearer_token_provider(DefaultAzureCredential(), "https://ai.azure.com/.default")`; pass the provider itself, not `provider()`, so credentials refresh.
- Use `DefaultAzureCredential()` unchanged; do not disable credential sources.
- At startup, explicitly request a token for `https://ai.azure.com/.default` to fail fast, then retain the callable provider for refresh.
- `model=` is the configured Azure deployment name.
- Require a complete HTTPS Azure v1 base URL ending in `/openai/v1/`; support Azure OpenAI resource, Foundry resource, and Foundry project URL shapes. Normalize only a missing trailing slash. Do not add `api_version`.
- Set `store=False`. Preserve the current Agent Run's response/input items locally in memory rather than using service-side `previous_response_id` state.
- Agent state and transcripts are not persisted locally. Documentation must clarify that Azure service-side handling remains governed by the deployment's Azure policies.
- Keep OpenAI SDK default transient retries. Never retry a Bash call automatically.
- Startup/configuration/authentication/model API failures go concisely to stderr and return nonzero. Authentication guidance may mention `az login` as one possible local remedy without implying it is the only credential source.
- Current official-reference facts gathered during design: packages are `openai` and `azure-identity`; Microsoft documented `openai>=1.106.0` for callable Entra token providers. Primary sources to recheck if necessary:
  - https://learn.microsoft.com/en-us/azure/foundry/openai/supported-languages
  - https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses
  - https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle
  - https://learn.microsoft.com/en-us/python/api/overview/azure/identity-readme
  - https://github.com/openai/openai-python#microsoft-azure-openai

### Configuration and CLI

- Require Python 3.12+.
- Use PEP 621 `pyproject.toml`, `src/azure_bash_agent/`, and `tests/`; do not commit a tool-specific lockfile.
- Runtime dependencies: `openai` and `azure-identity`. Test extra: `pytest`, Ruff, and mypy.
- Console command: `azure-bash-agent`, with optional `--config PATH`; default is `agent.toml` in the current directory.
- Commit only placeholder `agent.example.toml`; local `agent.toml` remains ignored.
- Canonical configuration shape:

```toml
[llm]
base_url = "https://example.openai.azure.com/openai/v1/"
model = "deployment-name"
request_timeout_seconds = 60

[bash]
executable = "bash"
command_timeout_seconds = 30
max_output_chars = 20000
```

- Environment overrides: `AZURE_OPENAI_BASE_URL`, `AZURE_OPENAI_MODEL`, and `AGENT_COMMAND_TIMEOUT_SECONDS`. Environment wins over TOML. The timeout environment variable affects only Bash command timeout.
- Validate configuration with clear domain-facing errors: required sections/keys and types; nonblank deployment and executable; complete HTTPS v1 URL; positive finite request/command timeouts; positive output limit; existing config file.
- Discover the Git root containing the selected configuration file; fail startup if none exists. Execute commands from that root.

### Bash Tool and approval boundary

- Expose exactly one strict function tool named `bash`, accepting one required string property `command`, with no additional properties.
- Every command requires individual Command Approval. Show the exact command in an escaped/safe representation. Only explicit `y` or `yes`, case-insensitive after trimming, authorizes execution; every other answer denies it.
- A denied command is not executed. Return a structured denied result to the model and continue processing remaining calls in that Tool Round.
- Reject NUL-containing commands. Escape terminal control characters in the approval display while executing the original unchanged command after approval.
- Run each approved command in a fresh subprocess using configured executable plus `-lc`; shell state (`cd`, variables, aliases, functions) does not persist, but filesystem changes do.
- Inherit the parent environment. Do not separately reveal environment values to the model; a command can still explicitly print them.
- Use the discovered Git root as cwd. Clearly document that cwd is only a convention and Bash is not a filesystem/security sandbox; commands may access outside the repository.
- Capture combined stdout and stderr. Decode UTF-8 with replacement.
- Default command timeout is 30 seconds. On timeout, perform best-effort whole-process-tree termination with native platform facilities and no new dependency. On Windows, likely use a new process group and `taskkill /T /F`; on POSIX, use a new session/process group and group signals. Avoid killing unrelated processes. Document that this is best effort and not hardened containment.
- Default output cap is 20,000 characters. On overflow, preserve approximately half the beginning and half the end with an explicit omitted-character marker.
- Return model-visible structured JSON for every Bash request containing `status`, `exit_code`, `output`, `timed_out`, `truncated`, and, where applicable, `reason`. A denied request has status `denied`. Timeout and invalid command states should have explicit statuses/reasons and JSON-compatible null where no exit code exists.
- Preserve call/result ordering.
- If a `bash` call has malformed arguments, return an `invalid_request` tool result so the model can recover. An unknown tool name is a fatal protocol error because it violates the configured contract.

### Prompt, output, docs, and testing

- Keep a small default developer/system instruction in code, not configuration: the model has one Bash Tool, uses it only when necessary, and otherwise answers directly.
- Assistant final responses and approval interaction go to stdout. Startup/config/auth/API errors go to stderr.
- README must document installation, creating config, Azure RBAC/inference-role prerequisites, `DefaultAzureCredential`, `az login` as one optional local source, execution, approval semantics, in-memory conversation state, Azure data-policy caveat, timeout/output behavior, and the non-sandbox warning.
- No ADR is warranted; technology was mandated rather than selected through a repository-level trade-off.
- Tests must be isolated and must not need Azure or real shell execution for agent-loop tests. Mock public adapter boundaries rather than internals. Pre-agreed seams:
  - configuration loading and validation;
  - Bash execution result behavior (real tiny subprocess tests are acceptable where portable; mock process boundaries for timeout/tree cleanup as needed);
  - Agent Run via fake Responses client, fake command runner, and injected input/output;
  - CLI startup/auth/error mapping through injected or patched public factories.
- Required behavior coverage: configuration precedence/validation; exit behavior; no-tool termination; empty model output; approval/denial; timeout; process cleanup; truncation; UTF-8 replacement; malformed calls; unknown tool; nonzero exits; and ordered multiple-tool sequencing.
- Follow vertical red→green slices. Do not bulk-author all tests before implementation.
- Run typechecking regularly and targeted test files regularly. At completion, run the entire pytest suite, Ruff format/check, and strict mypy.
- After implementation invoke `/code-review`, fix verified findings, rerun gates, and commit to the current feature branch. Commit message must satisfy the harness requirement to end with `Co-Authored-By: Claude <noreply@anthropic.com>`.

## Suggested implementation boundaries

These are guidance, not additional decisions:

- Keep validated immutable settings in `config.py`.
- Put process execution and bounded output in a dedicated Bash adapter module.
- Put response-item/tool-call translation and the Agent Run state machine in an agent module whose dependencies are injected.
- Keep Azure credential/client construction and terminal/exit-code mapping at the CLI composition root.
- Use SDK response objects defensively; tests should use small fakes matching only public fields actually consumed.

## Suggested skills

Call these using the Skill tool:

1. `tdd` — resume the active red→green implementation at the confirmed public seams.
2. `claude-api` is **not applicable** because OpenAI/Azure OpenAI is explicitly selected; rely on current official Microsoft/OpenAI docs if SDK details need checking.
3. `code-review` — mandatory after implementation, per `/implement`.
4. `simplify` — optional after tests pass and before final review, to improve quality without changing behavior.

## Immediate next action

Implement `ConfigError` and add one concrete failing configuration-validation test (for example an invalid non-HTTPS/non-v1 URL), make it pass, then continue one validation case at a time. Do not mistakenly treat the current collection failure as a completed red test: first add the behavior assertion that motivated the import.
