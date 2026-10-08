"""
realityscan-mcp server.

Tool groups:
  connection    check_connection, preflight
  execution     rs_run (sync), rs_start_job (background), rs_job_* (status/wait/log/list/cancel)
  gui           launch_gui, instance_status   (delegate mode: drive a visible RealityScan window)
  project       export_report, export_reconstruction_region, dump_global_settings, run_rscmd_file
  files         fs_list, fs_find, fs_info, fs_read, fs_write, xml_read, xml_patch, xml_write
  compose       compose_model_export   (assemble a load→select→region→simplify→unwrap→texture→export sequence)

All RealityScan commands are validated against the 208-command whitelist before
spawn (unknown commands raise a modal dialog even in headless mode). File tools
are confined to RS_MCP_ROOTS.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from . import config, files, runner
from .commands import KNOWN_COMMANDS

mcp = _Server(
    "realityscan",
    instructions=(
        "Drives RealityScan 2.x (Windows) through its headless CLI. Use rs_start_job for anything "
        "that takes more than a minute (align, mesh, unwrap, texture, export) and poll with "
        "rs_job_status / rs_job_wait. Use rs_run only for quick commands. Commands are argv token "
        "lists: ['-load', 'C:/p.rsproj', '-selectMaximalComponent', '-exportSelectedModel', "
        "'C:/out.abc', 'C:/params.xml']. Paths must be absolute. -headless, -silent, -stdConsole "
        "and -quit are added automatically. File tools only work inside RS_MCP_ROOTS."
    ),
)


# ── connection ───────────────────────────────────────────────────────────────

@mcp.tool()
def check_connection() -> dict:
    """Report the detected RealityScan executable, version, allowed roots and job dir. Does not launch anything."""
    return config.snapshot()


@mcp.tool()
def preflight() -> dict:
    """Check everything a job needs: executable found, roots exist, jobs dir writable, whether RealityScan is already running."""
    snap = config.snapshot()
    problems = []
    if not snap["found"]:
        problems.append("RealityScan.exe not found (set REALITYSCAN_EXE)")
    if not snap["roots"]:
        problems.append("no roots (RS_MCP_ROOTS empty, no local data drive) — file tools disabled")
    for r in snap["roots"]:
        if not os.path.isdir(r):
            problems.append(f"root does not exist: {r}")
    try:
        probe = config.jobs_dir() / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as e:
        problems.append(f"jobs dir not writable: {e}")
    running = None
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq RealityScan.exe", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=15).stdout
            running = [ln for ln in out.splitlines() if "RealityScan.exe" in ln]
        except Exception as e:
            running = f"tasklist failed: {e}"
    return {"ok": not problems, "problems": problems, "realityscan_processes": running, **snap}


# ── execution ────────────────────────────────────────────────────────────────

@mcp.tool()
def rs_run(commands: list[str], timeout: float | None = None, headless: bool = True,
           validate: bool = True, instance: str | None = None, wait: bool = True,
           cwd: str | None = None) -> dict:
    """Run a RealityScan command sequence synchronously and return stdout/stderr/exit code.
    For quick operations only (status queries, reports, settings dumps). Long jobs: use rs_start_job.
    commands: argv tokens, one per element. instance: send to a running named GUI instance instead."""
    return runner.run(commands, timeout=timeout, headless=headless, validate=validate,
                      instance=instance, wait=wait, cwd=cwd)


@mcp.tool()
def rs_start_job(commands: list[str], label: str = "", headless: bool = True, validate: bool = True,
                 instance: str | None = None, wait: bool = True, write_progress: bool = False,
                 cwd: str | None = None) -> dict:
    """Start a RealityScan command sequence in the background and return a job id immediately.
    Use for align / mesh / simplify / unwrap / texture / export. Poll with rs_job_status or block with rs_job_wait.
    write_progress: also pass -writeProgress <jobdir>/progress.txt (off by default; enable once verified on this build)."""
    return runner.start_job(commands, label=label, headless=headless, validate=validate,
                            instance=instance, wait=wait, write_progress=write_progress, cwd=cwd)


@mcp.tool()
def rs_job_status(job_id: str, tail_lines: int = 40) -> dict:
    """State (running/done/failed/cancelled), exit code and log tails of a background job."""
    job = runner.get_job(job_id)
    if not job:
        return {"ok": False, "error": f"unknown job {job_id}"}
    s = job.summary(tail_lines=tail_lines)
    s["ok"] = s["state"] == "done"
    return s


@mcp.tool()
def rs_job_wait(job_id: str, timeout: float = 600, poll_s: float = 5) -> dict:
    """Block until a background job finishes or `timeout` seconds pass (returns still_running=true if it did not finish)."""
    return runner.wait_job(job_id, timeout=timeout, poll_s=poll_s)


@mcp.tool()
def rs_job_log(job_id: str, stream: str = "stdout", tail_lines: int = 200) -> dict:
    """Full or tailed stdout/stderr/progress log of a job. stream: stdout | stderr | progress."""
    job = runner.get_job(job_id)
    if not job:
        return {"ok": False, "error": f"unknown job {job_id}"}
    path = {"stdout": job.stdout_path, "stderr": job.stderr_path, "progress": job.progress_path}.get(stream)
    if path is None:
        return {"ok": False, "error": "stream must be stdout | stderr | progress"}
    if not path.exists():
        return {"ok": True, "text": "", "path": str(path)}
    text = path.read_text(encoding="utf-8", errors="replace")
    if tail_lines > 0:
        text = "\n".join(text.splitlines()[-tail_lines:])
    return {"ok": True, "path": str(path), "state": job.state(), "text": text}


@mcp.tool()
def rs_jobs() -> list[dict]:
    """List all jobs started in this server session, newest first."""
    return runner.list_jobs()


@mcp.tool()
def rs_job_cancel(job_id: str) -> dict:
    """Kill a running background job. RealityScan does not checkpoint; a killed align/mesh is lost."""
    job = runner.get_job(job_id)
    if not job:
        return {"ok": False, "error": f"unknown job {job_id}"}
    return {"ok": job.cancel(), "state": job.state()}


# ── gui / delegate ───────────────────────────────────────────────────────────

@mcp.tool()
def launch_gui(instance_name: str, project_path: str | None = None) -> dict:
    """Open a visible RealityScan window as a named instance (optionally loading a project).
    Then pass instance=<name> to rs_run / rs_start_job to execute commands inside that window while the operator watches."""
    return runner.launch_gui(instance_name, project_path)


@mcp.tool()
def instance_status(instance_name: str, timeout: float = 60) -> dict:
    """Ask a running named instance for its progress status (-getStatus)."""
    return runner.instance_status(instance_name, timeout=timeout)


# ── project-level conveniences ───────────────────────────────────────────────

@mcp.tool()
def export_report(project_path: str, output_file: str, template_file: str, timeout: float = 900) -> dict:
    """Load a project and write a report (-exportReport). template_file is an .html/.txt report template.
    Use for GCP residuals / alignment RMS. Returns the run result; read the output with fs_read."""
    return runner.run(["-load", project_path, "-exportReport", output_file, template_file], timeout=timeout)


@mcp.tool()
def export_reconstruction_region(project_path: str, output_rsbox: str, timeout: float = 600) -> dict:
    """Load a project and export its current reconstruction region to an .rsbox file.
    The result is the reference to xml_patch for new region boxes."""
    return runner.run(["-load", project_path, "-exportReconstructionRegion", output_rsbox], timeout=timeout)


@mcp.tool()
def dump_global_settings(output_rcconfig: str, timeout: float = 300) -> dict:
    """Write RealityScan's global settings to an .rcconfig file (-exportGlobalSettings).
    The file is binary and cannot be read with xml_read. To discover -set key names, xml_read a
    Configuration file saved from a GUI settings panel instead (see README)."""
    return runner.run(["-exportGlobalSettings", output_rcconfig], timeout=timeout)


@mcp.tool()
def run_rscmd_file(rscmd_path: str, label: str = "", background: bool = True, timeout: float | None = None) -> dict:
    """Execute a command file via -execRSCMD (one command per line, same syntax as the CLI).
    Handy for long, reviewable sequences written with fs_write. Background by default."""
    cmds = ["-execRSCMD", rscmd_path]
    if background:
        return runner.start_job(cmds, label=label or f"rscmd:{os.path.basename(rscmd_path)}")
    return runner.run(cmds, timeout=timeout)


@mcp.tool()
def list_known_commands(filter: str = "") -> dict:
    """List the whitelisted CLI commands (optionally substring-filtered, case-insensitive)."""
    f = filter.lower()
    cmds = sorted(c for c in KNOWN_COMMANDS if f in c.lower())
    return {"count": len(cmds), "commands": cmds}


# ── compose ──────────────────────────────────────────────────────────────────

@mcp.tool()
def compose_model_export(
    project_path: str,
    export_file: str,
    export_params_xml: str | None = None,
    model_name: str | None = None,
    region_rsbox: str | None = None,
    simplify_params_xml: str | None = None,
    simplify_target_triangles: int | None = None,
    unwrap_params_xml: str | None = None,
    texture_params_xml: str | None = None,
    do_unwrap: bool = False,
    do_texture: bool = False,
    save_project_as: str | None = None,
    extra_before_export: list[str] | None = None,
    start: bool = False,
    label: str = "",
) -> dict:
    """Assemble (and optionally start) a load → select model → [set region] → [simplify] → [unwrap] → [texture] → export sequence.
    Returns the token list so it can be reviewed before running. Set start=true to launch it as a background job.
    - model_name: -selectModel <name>; omitted = -selectMaximalComponent (largest component's model context).
    - simplify: pass either simplify_params_xml or simplify_target_triangles (int), not both.
    - unwrap/texture only run if do_unwrap / do_texture are true; params XMLs are optional (current settings otherwise).
    - export_params_xml: the params.xml holding anchor/format/texture settings (see README: derive from a GUI-saved reference).
    - save_project_as: -save <path> after export (omit to leave the project untouched)."""
    seq: list[str] = ["-load", project_path]
    if model_name:
        seq += ["-selectModel", model_name]
    else:
        seq += ["-selectMaximalComponent"]
    if region_rsbox:
        seq += ["-setReconstructionRegion", region_rsbox]
    if simplify_params_xml and simplify_target_triangles:
        return {"ok": False, "error": "pass simplify_params_xml or simplify_target_triangles, not both"}
    if simplify_params_xml:
        seq += ["-simplify", simplify_params_xml]
    elif simplify_target_triangles:
        seq += ["-simplify", str(int(simplify_target_triangles))]
    if do_unwrap:
        seq += ["-unwrap"] + ([unwrap_params_xml] if unwrap_params_xml else [])
    if do_texture:
        seq += ["-calculateTexture"] + ([texture_params_xml] if texture_params_xml else [])
    if extra_before_export:
        seq += list(extra_before_export)
    seq += ["-exportSelectedModel", export_file] + ([export_params_xml] if export_params_xml else [])
    if save_project_as:
        seq += ["-save", save_project_as]
    out: dict[str, Any] = {"ok": True, "commands": seq}
    if start:
        out["job"] = runner.start_job(seq, label=label or f"export:{os.path.basename(export_file)}")
        out["ok"] = out["job"].get("ok", False)
    return out


# ── files ────────────────────────────────────────────────────────────────────

def _guard(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except files.ScopeError as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
def fs_list(path: str, pattern: str = "*", recursive: bool = False, max_entries: int = 500) -> dict:
    """List a folder inside RS_MCP_ROOTS (glob pattern, optional recursion)."""
    return _guard(files.list_dir, path, pattern, recursive, max_entries)


@mcp.tool()
def fs_find(path: str, patterns: list[str], recursive: bool = True, max_entries: int = 500) -> dict:
    """Find files matching any of several glob patterns, e.g. ['*.rsproj','*.rsInfo','*.rsbox']."""
    return _guard(files.find_files, path, patterns, recursive, max_entries)


@mcp.tool()
def fs_info(path: str) -> dict:
    """Existence, type, size and mtime of a path (check an export landed and how big it is)."""
    return _guard(files.file_info, path)


@mcp.tool()
def fs_read(path: str, max_bytes: int = 200000, tail: bool = False) -> dict:
    """Read a text file (rsInfo, reports, logs, params.xml). tail=true returns the last max_bytes instead of the first."""
    return _guard(files.read_text, path, max_bytes, tail)


@mcp.tool()
def fs_write(path: str, content: str, overwrite: bool = False) -> dict:
    """Write a text file inside RS_MCP_ROOTS (rscmd files, GCP CSVs, notes). Refuses to overwrite unless overwrite=true."""
    return _guard(files.write_text, path, content, overwrite)


@mcp.tool()
def xml_read(path: str) -> dict:
    """Parse an XML settings file (params.xml, .rsbox, .rcconfig, .rsInfo) into a tag/attrib/text tree."""
    return _guard(files.xml_read, path)


@mcp.tool()
def xml_patch(path_in: str, path_out: str, attributes: dict | None = None,
              elements: dict | None = None, overwrite: bool = True) -> dict:
    """Copy an XML file, setting root attributes and/or element text by slash path.
    Example rsbox: elements={'widthHeightDepth':'260 260 200','Header/CentreEuclid/centre':'500100 5000200 40'}.
    Example export params: attributes={'settingsAnchor':'12.5 -40.25 3'}."""
    return _guard(files.xml_patch, path_in, path_out, attributes, elements, overwrite)


@mcp.tool()
def xml_write(path: str, root_tag: str, attributes: dict, overwrite: bool = False) -> dict:
    """Write a single-element XML file (root tag + attributes) — for params files whose keys are already known."""
    return _guard(files.xml_write, path, root_tag, attributes, overwrite)


# ── dev ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def dev_reload() -> dict:
    """Hot-reload the server's own modules (config, commands, files, runner) after editing the source on disk.
    Running background jobs are kept. Tool signatures in server.py itself still need an app restart."""
    import importlib
    from . import commands as _commands
    jobs_before = dict(runner._JOBS)
    out = {}
    for mod in (config, _commands, files, runner):
        try:
            importlib.reload(mod)
            out[mod.__name__] = "reloaded"
        except Exception as e:
            out[mod.__name__] = f"FAILED: {e}"
    runner._JOBS.update(jobs_before)
    src = os.path.abspath(runner.__file__)
    out["runner_file"] = src
    out["runner_size"] = os.path.getsize(src)
    out["delegate_quit_bug_fixed"] = "NO -quit here" in open(src, encoding="utf-8").read()
    return out


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
