"""
RealityScan process runner.

Model: RealityScan is driven as a headless batch — one process, a sequence of
commands, `-quit` at the end:

    RealityScan.exe -headless -silent <crashdir> -stdConsole \
        -load p.rsproj -selectMaximalComponent -calculateNormalModel -save p.rsproj -quit

Two execution modes:

  * run()          synchronous; blocks up to `timeout` seconds. Fine for short
                   things (export a report, dump settings, query status).
  * start_job()    background; returns a job id immediately. stdout/stderr go to
                   files in the job dir and status is polled. This is the mode
                   for alignment / meshing / texturing / big exports, which take
                   minutes to hours and must not hold an MCP tool call open.

Delegate mode (either function, `instance=`): commands are sent to an already
running, *visible*, named GUI instance via `-delegateTo`. A short-lived headless
"courier" process carries them and (optionally) blocks on `-waitCompleted`. The
operator watches the work happen in the real GUI and can intervene.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .commands import unknown_commands


class RealityScanError(Exception):
    pass


# ── argv assembly ────────────────────────────────────────────────────────────

def _crash_dir() -> str:
    d = config.jobs_dir() / "_crash"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _exe_argv(exe: str) -> list[str]:
    # A .py stand-in (the test stub) runs through this Python: Windows cannot start a script as a program.
    if exe.lower().endswith(".py"):
        return [sys.executable, exe]
    return [exe]


def build_argv(
    commands: list[str],
    *,
    headless: bool = True,
    console: bool = True,
    silent: bool = True,
    auto_quit: bool = True,
    validate: bool = True,
    instance: str | None = None,
    wait: bool = True,
    progress_file: str | None = None,
) -> list[str]:
    exe = config.find_executable()
    if not exe:
        raise RealityScanError(
            "RealityScan.exe not found. Set REALITYSCAN_EXE to the absolute path of the executable."
        )
    if validate:
        bad = unknown_commands(commands)
        if bad:
            raise RealityScanError(
                f"Unknown command tokens {bad}. Fix the typo, or pass validate=false if the "
                f"command is newer than the whitelist. (Unknown commands raise a modal dialog "
                f"even in headless mode and hang the process.)"
            )
    cmds = [str(c) for c in commands]

    argv = _exe_argv(exe)
    if instance:
        # Courier: headless carrier that forwards to a visible named instance.
        argv.append("-headless")
        if silent:
            argv += ["-silent", _crash_dir()]
        if console:
            argv.append("-stdConsole")
        argv += ["-delegateTo", str(instance)]
        argv += cmds
        if wait and (not cmds or cmds[-1] != "-quit"):
            argv += ["-waitCompleted", str(instance)]
        # NO -quit here: everything after -delegateTo is forwarded to the instance,
        # so a trailing -quit would close the operator's RealityScan window.
        # The courier exits by itself once its command line is consumed.
        # To close the instance deliberately, delegate ["-quit"] explicitly.
        return argv

    if headless:
        argv.append("-headless")
    if silent:
        argv += ["-silent", _crash_dir()]
    if console:
        argv.append("-stdConsole")
    if progress_file:
        argv += ["-writeProgress", str(progress_file)]
    argv += cmds
    if auto_quit and "-quit" not in cmds:
        argv.append("-quit")
    return argv


def printable(argv: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return " ".join(shlex.quote(a) for a in argv)


# ── synchronous run ──────────────────────────────────────────────────────────

def run(commands: list[str], *, timeout: float | None = None, cwd: str | None = None, **kw) -> dict:
    try:
        argv = build_argv(commands, **kw)
    except RealityScanError as e:
        return {"ok": False, "error": str(e), "returncode": None, "stdout": "", "stderr": "",
                "timed_out": False, "argv": None}
    cmdline = printable(argv)
    t0 = time.time()
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout if timeout is not None else config.default_timeout(), cwd=cwd,
        )
    except subprocess.TimeoutExpired as e:
        return {"ok": False, "error": f"timeout after {timeout}s — process killed", "returncode": None,
                "stdout": e.stdout if isinstance(e.stdout, str) else "",
                "stderr": e.stderr if isinstance(e.stderr, str) else "",
                "timed_out": True, "argv": cmdline, "elapsed_s": round(time.time() - t0, 1)}
    except FileNotFoundError:
        return {"ok": False, "error": "executable path is not valid", "returncode": None,
                "stdout": "", "stderr": "", "timed_out": False, "argv": cmdline}
    return {
        "ok": proc.returncode == 0,
        "error": None if proc.returncode == 0 else f"exit code {proc.returncode}",
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "timed_out": False,
        "argv": cmdline,
        "elapsed_s": round(time.time() - t0, 1),
    }


# ── background jobs ──────────────────────────────────────────────────────────

_JOBS: dict[str, "Job"] = {}
_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Job:
    def __init__(self, job_id: str, argv: list[str], label: str, cwd: str | None):
        self.id = job_id
        self.argv = argv
        self.label = label
        self.cwd = cwd
        self.dir = config.jobs_dir() / job_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.stdout_path = self.dir / "stdout.log"
        self.stderr_path = self.dir / "stderr.log"
        self.progress_path = self.dir / "progress.txt"
        self.started = _now()
        self.finished: str | None = None
        self.proc: subprocess.Popen | None = None
        self.error: str | None = None
        self.cancelled = False

    def start(self) -> None:
        (self.dir / "argv.json").write_text(json.dumps(
            {"id": self.id, "label": self.label, "argv": self.argv, "cmdline": printable(self.argv),
             "cwd": self.cwd, "started": self.started}, indent=2), encoding="utf-8")
        out = open(self.stdout_path, "w", encoding="utf-8", errors="replace")
        err = open(self.stderr_path, "w", encoding="utf-8", errors="replace")
        flags = 0
        if os.name == "nt":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP  # lets us send CTRL_BREAK; also detaches from console
        try:
            self.proc = subprocess.Popen(self.argv, stdout=out, stderr=err, cwd=self.cwd,
                                         creationflags=flags, close_fds=True)
        except Exception as e:  # FileNotFoundError etc.
            self.error = f"spawn failed: {e}"
            self.finished = _now()
            out.close(); err.close()

    def poll(self) -> int | None:
        if self.proc is None:
            return None
        rc = self.proc.poll()
        if rc is not None and self.finished is None:
            self.finished = _now()
        return rc

    def state(self) -> str:
        if self.error:
            return "failed"
        if self.cancelled:
            return "cancelled"
        rc = self.poll()
        if rc is None:
            return "running"
        return "done" if rc == 0 else "failed"

    def cancel(self) -> bool:
        if self.proc is None or self.poll() is not None:
            return False
        self.cancelled = True
        try:
            self.proc.kill()
        except Exception:
            return False
        return True

    def _tail(self, path: Path, lines: int) -> str:
        if not path.exists():
            return ""
        try:
            data = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
        return "\n".join(data.splitlines()[-lines:]) if lines > 0 else data

    def summary(self, tail_lines: int = 40) -> dict:
        rc = self.poll()
        info = {
            "id": self.id,
            "label": self.label,
            "state": self.state(),
            "returncode": rc,
            "pid": self.proc.pid if self.proc else None,
            "started": self.started,
            "finished": self.finished,
            "job_dir": str(self.dir),
            "cmdline": printable(self.argv),
            "error": self.error,
        }
        if tail_lines:
            info["stdout_tail"] = self._tail(self.stdout_path, tail_lines)
            info["stderr_tail"] = self._tail(self.stderr_path, tail_lines)
            info["progress_tail"] = self._tail(self.progress_path, 5)
        return info


def start_job(commands: list[str], *, label: str = "", cwd: str | None = None,
              write_progress: bool = False, **kw) -> dict:
    job_id = datetime.now().strftime("%y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    job_dir = config.jobs_dir() / job_id
    progress = str(job_dir / "progress.txt") if (write_progress and not kw.get("instance")) else None
    try:
        argv = build_argv(commands, progress_file=progress, **kw)
    except RealityScanError as e:
        return {"ok": False, "error": str(e)}
    job = Job(job_id, argv, label or (commands[0] if commands else "job"), cwd)
    with _LOCK:
        _JOBS[job_id] = job
    job.start()
    s = job.summary(tail_lines=0)
    s["ok"] = job.error is None
    return s


def get_job(job_id: str) -> Job | None:
    with _LOCK:
        return _JOBS.get(job_id)


def list_jobs() -> list[dict]:
    with _LOCK:
        jobs = list(_JOBS.values())
    return [j.summary(tail_lines=0) for j in sorted(jobs, key=lambda j: j.started, reverse=True)]


def wait_job(job_id: str, timeout: float, poll_s: float = 2.0) -> dict:
    job = get_job(job_id)
    if not job:
        return {"ok": False, "error": f"unknown job {job_id}"}
    t0 = time.time()
    while job.state() == "running" and (time.time() - t0) < timeout:
        time.sleep(poll_s)
    s = job.summary()
    s["ok"] = s["state"] == "done"
    s["still_running"] = s["state"] == "running"
    return s


# ── GUI instance (delegate target) ───────────────────────────────────────────

def launch_gui(instance_name: str, project_path: str | None = None) -> dict:
    exe = config.find_executable()
    if not exe:
        return {"ok": False, "error": "RealityScan.exe not found"}
    argv = _exe_argv(exe) + ["-setInstanceName", str(instance_name)]
    if project_path:
        argv += ["-load", project_path]
    flags = 0
    kw: dict = {}
    if os.name == "nt":
        # The MCP server is a hidden child of the desktop app; without an explicit
        # show-state the GUI inherits "hidden" and runs invisibly. Force a normal window.
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 1  # SW_SHOWNORMAL
        kw["startupinfo"] = si
    try:
        proc = subprocess.Popen(argv, creationflags=flags, close_fds=True, **kw)
    except Exception as e:
        return {"ok": False, "error": f"spawn failed: {e}"}
    return {"ok": True, "instance": str(instance_name), "pid": proc.pid, "cmdline": printable(argv),
            "note": "A visible RealityScan window is starting. Send commands to it with instance=<name>."}


def instance_status(instance_name: str, timeout: float = 60) -> dict:
    """Query a running named instance with -getStatus via a headless courier."""
    return run(["-getStatus", str(instance_name)], timeout=timeout, headless=True, auto_quit=True)
