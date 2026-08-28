import shutil
import signal
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import azure_bash_agent.bash_runner as bash_runner_module
from azure_bash_agent.bash_runner import BashCommandRunner, CommandResult


def test_run_captures_combined_output_from_repository_root(tmp_path: Path) -> None:
    git_bash = Path(r"C:\Program Files\Git\usr\bin\bash.exe")
    executable = str(git_bash) if git_bash.is_file() else shutil.which("bash")
    if executable is None:
        pytest.skip("Bash is not installed")
    runner = BashCommandRunner(
        executable=executable,
        repository_root=tmp_path,
        timeout_seconds=5,
        max_output_chars=2_000,
    )

    result = runner.run("printf 'stdout'; printf 'stderr' >&2; printf '\n'; pwd")

    assert result.status == "completed"
    assert result.exit_code == 0
    assert result.output.startswith("stdoutstderr\n")
    assert tmp_path.name in result.output
    assert result.timed_out is False
    assert result.truncated is False
    assert result.reason is None


def make_runner(tmp_path: Path, *, cap: int = 2_000) -> BashCommandRunner:
    return BashCommandRunner(
        executable="bash",
        repository_root=tmp_path,
        timeout_seconds=5,
        max_output_chars=cap,
    )


class FakeProcess:
    def __init__(
        self,
        outputs: list[tuple[bytes, None] | BaseException],
        *,
        returncode: int = 0,
        pid: int = 123,
        wait_error: BaseException | None = None,
    ) -> None:
        self.outputs = outputs
        self.returncode = returncode
        self.pid = pid
        self.wait_error = wait_error
        self.killed = False
        self.communicate_timeouts: list[float | None] = []

    def communicate(self, timeout: float | None = None) -> tuple[bytes, None]:
        self.communicate_timeouts.append(timeout)
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        return output

    def wait(self, timeout: float | None = None) -> int:
        if self.wait_error is not None:
            raise self.wait_error
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def patch_process(
    monkeypatch: pytest.MonkeyPatch,
    process: FakeProcess,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def create_process(*args: object, **kwargs: Any) -> FakeProcess:
        calls.append({"args": args, **kwargs})
        return process

    monkeypatch.setattr(subprocess, "Popen", create_process)
    return calls


def test_nonzero_exit_is_a_completed_result(tmp_path: Path) -> None:
    git_bash = Path(r"C:\Program Files\Git\usr\bin\bash.exe")
    executable = str(git_bash) if git_bash.is_file() else shutil.which("bash")
    if executable is None:
        pytest.skip("Bash is not installed")
    runner = BashCommandRunner(
        executable=executable,
        repository_root=tmp_path,
        timeout_seconds=5,
        max_output_chars=2_000,
    )

    result = runner.run("printf failure; exit 7")

    assert result.status == "completed"
    assert result.exit_code == 7
    assert result.output == "failure"


def test_invalid_utf8_is_replaced(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    process = FakeProcess([(b"before\xffafter", None)])
    patch_process(monkeypatch, process)

    result = make_runner(tmp_path).run("command")

    assert result.output == "before\ufffdafter"


def test_output_truncation_keeps_head_tail_marker_and_hard_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = FakeProcess([(b"A" * 60 + b"B" * 60, None)])
    patch_process(monkeypatch, process)

    result = make_runner(tmp_path, cap=80).run("command")

    assert result.truncated is True
    assert len(result.output) == 80
    assert result.output.startswith("A")
    assert result.output.endswith("B")
    assert "71 characters omitted" in result.output


def test_small_output_cap_keeps_both_ends_and_compact_omission_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = FakeProcess([(b"A" * 60 + b"B" * 60, None)])
    patch_process(monkeypatch, process)

    result = make_runner(tmp_path, cap=10).run("command")

    assert result.truncated is True
    assert len(result.output) == 10
    assert result.output.startswith("A")
    assert result.output.endswith("B")
    assert "..." in result.output


def test_nul_is_rejected_without_process_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail_create(*args: object, **kwargs: object) -> None:
        pytest.fail("Popen must not be called")

    monkeypatch.setattr(subprocess, "Popen", fail_create)

    result = make_runner(tmp_path).run("printf '\0'")

    assert result.status == "invalid_request"
    assert result.exit_code is None
    assert result.reason is not None


def test_timeout_retains_partial_output_without_duplicate_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    timeout = subprocess.TimeoutExpired("bash", 5, output=b"partial-")
    process = FakeProcess([timeout, (b"partial-tail", None)])
    calls = patch_process(monkeypatch, process)
    monkeypatch.setattr(bash_runner_module, "_WINDOWS", False)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        bash_runner_module,
        "_kill_process_group",
        lambda pid, sig: signals.append((pid, sig)),
    )

    result = make_runner(tmp_path).run("slow command")

    assert result.status == "timed_out"
    assert result.timed_out is True
    assert result.output == "partial-tail"
    assert result.exit_code is None
    assert signals == [
        (123, signal.SIGTERM),
        (123, 0),
        (123, getattr(signal, "SIGKILL", 9)),
    ]
    assert len(calls) == 1


def test_timeout_output_collection_remains_bounded_when_pipes_stay_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    initial_timeout = subprocess.TimeoutExpired("bash", 5, output=b"partial-")
    collection_timeout = subprocess.TimeoutExpired("bash", 1, output=b"partial-more")
    final_timeout = subprocess.TimeoutExpired("bash", 1, output=b"partial-more-tail")
    process = FakeProcess([initial_timeout, collection_timeout, final_timeout])
    patch_process(monkeypatch, process)
    monkeypatch.setattr(bash_runner_module, "_WINDOWS", False)
    monkeypatch.setattr(bash_runner_module, "_kill_process_group", lambda pid, sig: None)

    result = make_runner(tmp_path).run("slow command")

    assert result.status == "timed_out"
    assert result.output == "partial-more-tail"
    assert result.reason is not None
    assert "output collection" in result.reason
    assert process.killed is True
    assert process.communicate_timeouts == [5, 1.0, 1.0]


def test_posix_timeout_escalates_process_group_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    timeout = subprocess.TimeoutExpired("bash", 5)
    wait_timeout = subprocess.TimeoutExpired("bash", 1)
    process = FakeProcess([timeout, (b"", None)], wait_error=wait_timeout)
    patch_process(monkeypatch, process)
    monkeypatch.setattr(bash_runner_module, "_WINDOWS", False)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        bash_runner_module,
        "_kill_process_group",
        lambda pid, sig: signals.append((pid, sig)),
    )

    make_runner(tmp_path).run("slow command")

    assert signals == [(123, signal.SIGTERM), (123, getattr(signal, "SIGKILL", 9))]


def test_posix_timeout_kills_group_that_survives_parent_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    timeout = subprocess.TimeoutExpired("bash", 5)
    process = FakeProcess([timeout, (b"", None)])
    patch_process(monkeypatch, process)
    monkeypatch.setattr(bash_runner_module, "_WINDOWS", False)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        bash_runner_module,
        "_kill_process_group",
        lambda pid, sig: signals.append((pid, sig)),
    )

    make_runner(tmp_path).run("slow command")

    assert signals == [
        (123, signal.SIGTERM),
        (123, 0),
        (123, getattr(signal, "SIGKILL", 9)),
    ]


def test_windows_timeout_uses_taskkill_without_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    timeout = subprocess.TimeoutExpired("bash", 5)
    process = FakeProcess([timeout, (b"", None)])
    patch_process(monkeypatch, process)
    monkeypatch.setattr(bash_runner_module, "_WINDOWS", True)
    taskkill_calls: list[SimpleNamespace] = []

    def run_taskkill(command: list[str], **kwargs: Any) -> SimpleNamespace:
        taskkill_calls.append(SimpleNamespace(command=command, kwargs=kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run_taskkill)

    make_runner(tmp_path).run("slow command")

    assert taskkill_calls[0].command == ["taskkill", "/PID", "123", "/T", "/F"]
    assert taskkill_calls[0].kwargs["shell"] is False


def test_windows_taskkill_failure_falls_back_to_process_kill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    timeout = subprocess.TimeoutExpired("bash", 5)
    process = FakeProcess([timeout, (b"", None)])
    patch_process(monkeypatch, process)
    monkeypatch.setattr(bash_runner_module, "_WINDOWS", True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(returncode=1),
    )

    result = make_runner(tmp_path).run("slow command")

    assert result.status == "timed_out"
    assert result.reason is not None
    assert "taskkill failed" in result.reason
    assert process.killed is True


def test_spawn_failure_is_structured_and_attempted_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    attempts = 0

    def fail_create(*args: object, **kwargs: object) -> None:
        nonlocal attempts
        attempts += 1
        raise OSError("missing executable")

    monkeypatch.setattr(subprocess, "Popen", fail_create)

    result = make_runner(tmp_path).run("command")

    assert result.status == "execution_error"
    assert result.reason == "could not start Bash: missing executable"
    assert attempts == 1


def test_runner_invokes_original_command_once_with_expected_process_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = FakeProcess([(b"", None)], returncode=2)
    calls = patch_process(monkeypatch, process)
    monkeypatch.setattr(bash_runner_module, "_WINDOWS", False)

    make_runner(tmp_path).run("printf original")

    assert len(calls) == 1
    assert calls[0]["args"][0] == ["bash", "-lc", "printf original"]
    assert calls[0]["cwd"] == tmp_path
    assert calls[0]["stderr"] is subprocess.STDOUT
    assert calls[0]["start_new_session"] is True


def test_command_result_serializes_all_model_visible_fields() -> None:
    result = CommandResult("denied", None, "", False, False, "not approved")

    assert result.to_json() == (
        '{"status": "denied", "exit_code": null, "output": "", '
        '"timed_out": false, "truncated": false, "reason": "not approved"}'
    )
