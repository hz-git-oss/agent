"""Run individually approved Bash commands behind a structured adapter."""

from __future__ import annotations

import json
import os
import signal
import subprocess
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol, cast

_WINDOWS = os.name == "nt"
_TERMINATION_GRACE_SECONDS = 1.0


class CommandRunner(Protocol):
    """A boundary for executing one Bash Tool command."""

    def run(self, command: str) -> CommandResult:
        """Execute *command* and return a model-visible result."""


@dataclass(frozen=True)
class CommandResult:
    """The stable model-visible result of one command request."""

    status: str
    exit_code: int | None
    output: str
    timed_out: bool
    truncated: bool
    reason: str | None = None

    def to_json(self) -> str:
        """Serialize every result field for a function-call output."""
        return json.dumps(asdict(self), ensure_ascii=True)


class BashCommandRunner:
    """Execute each command in a fresh Bash process."""

    def __init__(
        self,
        *,
        executable: str,
        repository_root: Path,
        timeout_seconds: float,
        max_output_chars: int,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._executable = executable
        self._repository_root = repository_root
        self._timeout_seconds = timeout_seconds
        self._max_output_chars = max_output_chars
        self._environ = environ

    def run(self, command: str) -> CommandResult:
        """Run one command without retrying process creation."""
        if "\0" in command:
            return CommandResult(
                status="invalid_request",
                exit_code=None,
                output="",
                timed_out=False,
                truncated=False,
                reason="command contains a NUL character",
            )

        process: subprocess.Popen[bytes]
        try:
            process = subprocess.Popen(
                [self._executable, "-lc", command],
                cwd=self._repository_root,
                env=None if self._environ is None else dict(self._environ),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=not _WINDOWS,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if _WINDOWS else 0),
            )
        except OSError as error:
            return _error_result(f"could not start Bash: {_first_line(error)}")

        try:
            output, _ = process.communicate(timeout=self._timeout_seconds)
        except subprocess.TimeoutExpired as error:
            prefix = _as_bytes(error.output)
            cleanup_reason = self._terminate_process_tree(process)
            output, collection_reason = _collect_timeout_output(process, prefix)
            decoded, truncated = _bounded_output(output, self._max_output_chars)
            reasons = [reason for reason in (cleanup_reason, collection_reason) if reason]
            return CommandResult(
                status="timed_out",
                exit_code=None,
                output=decoded,
                timed_out=True,
                truncated=truncated,
                reason="; ".join(reasons) if reasons else "command timed out",
            )
        except OSError as error:
            return _error_result(f"could not run Bash: {_first_line(error)}")

        decoded, truncated = _bounded_output(output, self._max_output_chars)
        return CommandResult(
            status="completed",
            exit_code=process.returncode,
            output=decoded,
            timed_out=False,
            truncated=truncated,
        )

    def _terminate_process_tree(self, process: subprocess.Popen[bytes]) -> str | None:
        try:
            if _WINDOWS:
                completed = subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                )
                if completed.returncode != 0:
                    with suppress(OSError):
                        process.kill()
                    return f"taskkill failed with exit code {completed.returncode}"
                return None

            _kill_process_group(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=_TERMINATION_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                _kill_process_group(process.pid, getattr(signal, "SIGKILL", 9))
            else:
                try:
                    _kill_process_group(process.pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    _kill_process_group(process.pid, getattr(signal, "SIGKILL", 9))
            return None
        except OSError as error:
            with suppress(OSError):
                process.kill()
            return f"timeout cleanup failed: {_first_line(error)}"


def _as_bytes(output: bytes | str | None) -> bytes:
    if output is None:
        return b""
    return output if isinstance(output, bytes) else output.encode("utf-8", errors="replace")


def _bounded_output(output: bytes, cap: int) -> tuple[str, bool]:
    decoded = output.decode("utf-8", errors="replace")
    if len(decoded) <= cap:
        return decoded, False

    omitted = len(decoded)
    while True:
        marker = f"\n... {omitted} characters omitted ...\n"
        retained = max(0, cap - len(marker))
        updated_omitted = len(decoded) - retained
        if updated_omitted == omitted:
            break
        omitted = updated_omitted
    if retained < 2:
        marker = "..."[:cap]
        retained = cap - len(marker)
    if retained <= 0:
        return marker, True
    head_length = (retained + 1) // 2
    tail_length = retained - head_length
    tail = decoded[-tail_length:] if tail_length else ""
    return f"{decoded[:head_length]}{marker}{tail}", True


def _collect_timeout_output(
    process: subprocess.Popen[bytes], prefix: bytes
) -> tuple[bytes, str | None]:
    try:
        remaining, _ = process.communicate(timeout=_TERMINATION_GRACE_SECONDS)
        return _merge_output(prefix, remaining), None
    except subprocess.TimeoutExpired as error:
        collected = _merge_output(prefix, _as_bytes(error.output))
        with suppress(OSError):
            process.kill()
    except OSError as error:
        return prefix, f"output collection failed: {_first_line(error)}"

    try:
        remaining, _ = process.communicate(timeout=_TERMINATION_GRACE_SECONDS)
        return (
            _merge_output(collected, remaining),
            "output collection required a forced process kill",
        )
    except subprocess.TimeoutExpired as error:
        return (
            _merge_output(collected, _as_bytes(error.output)),
            "output collection remained open after forced process kill",
        )
    except OSError as error:
        return collected, f"output collection failed: {_first_line(error)}"


def _merge_output(prefix: bytes, output: bytes) -> bytes:
    return output if output.startswith(prefix) else prefix + output


def _error_result(reason: str) -> CommandResult:
    return CommandResult(
        status="execution_error",
        exit_code=None,
        output="",
        timed_out=False,
        truncated=False,
        reason=reason,
    )


def _first_line(error: BaseException) -> str:
    return str(error).splitlines()[0] if str(error) else type(error).__name__


def _kill_process_group(pid: int, sig: int) -> None:
    killpg = cast(Callable[[int, int], None], vars(os)["killpg"])
    killpg(pid, sig)
