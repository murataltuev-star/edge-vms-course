# server/recording.py — Lesson 6, aimed at the real pipeline since Lesson 13.
# The ONLY code that knows a subprocess exists. If this module starts growing
# a job queue or persisting anything, the design has gone wrong.
import os
import subprocess
import sys

CHILD_SCRIPT = "looper.py"                              # was "camera_sim.py" until Lesson 13
CHILD_ARGV = [sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                           "edge", "looper.py")]
STOP_TIMEOUT = 15.0

_current_proc: subprocess.Popen | None = None


class NotManaged(Exception):
    """Raised when the running recording wasn't started by this server."""


def _reap_if_dead():
    """If our child exited on its own since we last checked, forget it."""
    global _current_proc
    if _current_proc is not None and _current_proc.poll() is not None:
        _current_proc = None


def _scan_ps(ps_output: str, own_pid: int | None) -> int | None:
    """Pure half of _find_external_camera_pid, for tests.
    Checks that CHILD_SCRIPT is specifically the argument immediately after
    a Python interpreter — not merely present somewhere in the command line.
    That rules out anything whose argv happens to *mention* the filename
    (an editor, `tail -f`, a shell history match) without running it."""
    for line in ps_output.splitlines()[1:]:
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid_str, args = parts
        tokens = args.split()
        # Position, not presence — and the token BEFORE the script must be a
        # Python interpreter. Lesson 6's version checked tokens[1] alone, which
        # still matches `vim looper.py`: two tokens, script second. Requiring
        # python* first is what actually rules out editors and pagers.
        if len(tokens) < 2 or os.path.basename(tokens[1]) != CHILD_SCRIPT \
                or not os.path.basename(tokens[0]).startswith("python"):
            continue
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        if own_pid is not None and pid == own_pid:
            continue                       # that's ours, already reported by status()
        return pid
    return None


def _find_external_camera_pid() -> int | None:
    """Scan the process table for a looper.py this server didn't start."""
    result = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True, check=True)
    return _scan_ps(result.stdout, _current_proc.pid if _current_proc is not None else None)


def status():
    _reap_if_dead()
    if _current_proc is not None:
        return {"running": True, "managed": True, "pid": _current_proc.pid}
    external_pid = _find_external_camera_pid()
    if external_pid is not None:
        return {"running": True, "managed": False, "pid": external_pid}
    return {"running": False, "managed": False, "pid": None}


def start():
    global _current_proc
    current = status()
    if current["running"]:
        return current                              # idempotent — Lesson 4's rule, for real
    _current_proc = subprocess.Popen(
        CHILD_ARGV,
        start_new_session=True,                     # Lesson 6, Step 5: a Ctrl+C on the server
    )                                               # must not reach the recording
    return status()


def stop():
    global _current_proc
    current = status()
    if not current["running"]:
        return current                              # idempotent no-op
    if not current["managed"]:
        raise NotManaged("Recording is running but was not started by this server; "
                         "stop it where it was started.")
    _current_proc.terminate()                       # SIGTERM
    try:
        _current_proc.wait(timeout=STOP_TIMEOUT)
    except subprocess.TimeoutExpired:
        _current_proc.kill()                        # SIGKILL — last resort
        _current_proc.wait()
    _current_proc = None
    return status()
