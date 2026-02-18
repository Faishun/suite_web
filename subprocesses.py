from __future__ import annotations

import os
import selectors
import subprocess
import time
from pathlib import Path


class CommandResult:
    def __init__(self, exit_code: int, duration_s: float):
        self.exit_code = exit_code
        self.duration_s = duration_s


def run_command_streaming(
    *,
    cmd: list[str],
    cwd: Path | None,
    env: dict[str, str],
    log_path: Path,
    timeout_s: float | None = None,
    pid_hook=None,
    should_cancel=None,
) -> tuple[CommandResult, int | None]:
    """
    Run a subprocess and stream stdout/stderr into log_path.
    Returns (result, pid).
    """
    start = time.time()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("a", encoding="utf-8") as logf:
        logf.write(f"[cmd] cwd={cwd or Path.cwd()}\n")
        logf.write(f"[cmd] {' '.join(_shell_escape(x) for x in cmd)}\n")
        logf.flush()

        # Encourage real-time stdout flush for Python tools.
        env = dict(env)
        env.setdefault("PYTHONUNBUFFERED", "1")

        p = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False,  # we stream bytes to avoid buffering issues and handle \r updates
            start_new_session=True,  # make it easy to kill the whole process group
        )

        pid = p.pid
        if pid_hook is not None:
            try:
                pid_hook(pid)
            except Exception:
                # Don't fail the run because PID persistence failed.
                pass
        try:
            for chunk in _iter_chunks(p, timeout_s=timeout_s, should_cancel=should_cancel):
                # Normalize carriage returns (progress bars) into newlines so the UI can show updates.
                text = chunk.decode("utf-8", errors="replace").replace("\r", "\n")
                logf.write(text)
                if not text.endswith("\n"):
                    logf.write("\n")
                logf.flush()
        finally:
            exit_code = p.wait(timeout=10)

        dur = time.time() - start
        logf.write(f"[cmd] exit_code={exit_code} duration_s={dur:.2f}\n")
        logf.flush()

    return CommandResult(exit_code=exit_code, duration_s=dur), pid


def _iter_chunks(p: subprocess.Popen, timeout_s: float | None, should_cancel=None):
    start = time.time()
    assert p.stdout is not None
    fd = p.stdout.fileno()
    try:
        os.set_blocking(fd, False)
    except Exception:
        pass
    sel = selectors.DefaultSelector()
    sel.register(fd, selectors.EVENT_READ)
    while True:
        if should_cancel is not None:
            try:
                if should_cancel():
                    yield b"[cmd] cancellation requested; terminating process group\n"
                    try:
                        import signal

                        os.killpg(p.pid, signal.SIGTERM)
                    except Exception:
                        p.terminate()
                    break
            except Exception:
                pass

        if timeout_s is not None and (time.time() - start) > timeout_s:
            p.terminate()
            yield b"[cmd] timeout reached, terminating process\n"
            break

        events = sel.select(timeout=0.2)
        if events:
            try:
                data = os.read(fd, 8192)
            except Exception:
                data = b""
            if data:
                yield data
                continue

        if p.poll() is not None:
            # Drain any remaining output.
            try:
                rest = p.stdout.read()
            except Exception:
                rest = b""
            if rest:
                yield rest
            break

        time.sleep(0.05)


def _shell_escape(s: str) -> str:
    # Best-effort for logging only.
    if not s:
        return "''"
    if all(c.isalnum() or c in "._-/:=@+" for c in s):
        return s
    return "'" + s.replace("'", "'\"'\"'") + "'"

