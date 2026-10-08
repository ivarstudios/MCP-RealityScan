"""Run with: python -m pytest tests -q   (or: python tests/test_server.py)"""
import os, sys, time, tempfile, pathlib, json
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

TMP = pathlib.Path(tempfile.mkdtemp(prefix="rsmcp-"))
STUB = str(ROOT / "tests" / "stub_realityscan.py")
os.environ["REALITYSCAN_EXE"] = STUB  # a .py stand-in: the runner starts it with the current Python
os.environ["RS_MCP_ROOTS"] = str(TMP)
os.environ["RS_MCP_JOBS"] = str(TMP / "jobs")

from realityscan_mcp import server, runner, files, config  # noqa: E402
from realityscan_mcp.commands import KNOWN_COMMANDS, unknown_commands  # noqa: E402


def test_whitelist():
    assert len(KNOWN_COMMANDS) == 208
    assert unknown_commands(["-load", "x", "-moveReconstructionRegion", "-1", "0", "0"]) == []
    assert unknown_commands(["-alignn"]) == ["-alignn"]


def test_argv_headless():
    argv = runner.build_argv(["-load", "p.rsproj", "-align"])
    assert argv[:2] == [sys.executable, STUB]  # Windows can't start a .py, so it runs through Python
    assert argv[2] == "-headless" and "-silent" in argv and "-stdConsole" in argv
    assert argv[-4:] == ["-load", "p.rsproj", "-align", "-quit"]


def test_argv_delegate():
    argv = runner.build_argv(["-align"], instance="LIVE1")
    i = argv.index("-delegateTo")
    assert argv[i + 1] == "LIVE1" and argv[i + 2] == "-align"
    # waits for the window to finish, but never appends -quit: that would close the operator's window
    assert argv[-2:] == ["-waitCompleted", "LIVE1"] and "-quit" not in argv
    # closing the window is an explicit delegated -quit, with nothing left to wait for
    assert runner.build_argv(["-quit"], instance="LIVE1")[-3:] == ["-delegateTo", "LIVE1", "-quit"]


def test_file_version():
    if os.name != "nt":
        return
    # python.exe has the same trap as RealityScan.exe: its fixed version reads 3.12.10150.1013,
    # its text version 3.12.10. The text version must win.
    exe = getattr(sys, "_base_executable", sys.executable)
    assert config.file_version(exe) == "%d.%d.%d" % sys.version_info[:3]
    assert config.file_version(STUB) is None


def test_default_roots():
    # unset, the roots are the local fixed drives but the system drive - nothing project-specific
    old = os.environ.pop("RS_MCP_ROOTS")
    try:
        assert config.roots_source().startswith("default")
        if os.name == "nt":
            system = (os.environ.get("SystemDrive") or "C:").upper()
            assert all(not str(r).upper().startswith(system) for r in config.roots())
    finally:
        os.environ["RS_MCP_ROOTS"] = old
    assert config.roots() == [TMP.resolve()] and config.roots_source() == "RS_MCP_ROOTS"


def test_validate_rejects():
    r = runner.run(["-load", "p", "-alignn"])
    assert r["ok"] is False and "Unknown command" in r["error"]
    r = runner.run(["-load", "p", "-alignn"], validate=False)
    assert r["ok"] is True  # stub does not care


def test_sync_run_and_stub_outputs():
    rc = TMP / "g.rcconfig"
    r = server.dump_global_settings(str(rc))
    assert r["ok"] and rc.exists()
    x = server.xml_read(str(rc))
    assert x["root"]["attrib"]["TexturingColorCorrection"] == "1"

    box = TMP / "cur.rsbox"
    r = server.export_reconstruction_region(str(TMP / "p.rsproj"), str(box))
    assert r["ok"] and box.exists()
    r = server.xml_patch(str(box), str(TMP / "core.rsbox"),
                         elements={"widthHeightDepth": "260 260 200", "Header/CentreEuclid/centre": "500100 5000200 40"})
    assert r["ok"], r
    t = server.xml_read(str(TMP / "core.rsbox"))
    kids = {c["tag"]: c for c in t["root"]["children"]}
    assert kids["widthHeightDepth"]["text"] == "260 260 200"
    r = server.xml_patch(str(box), str(TMP / "bad.rsbox"), elements={"nope/x": "1"})
    assert r["ok"] is False

    rep = TMP / "r.html"
    r = server.export_report(str(TMP / "p.rsproj"), str(rep), str(TMP / "tpl.html"))
    assert r["ok"] and rep.read_text().startswith("REPORT")


def test_background_job():
    j = server.rs_start_job(["-load", "p.rsproj", "-sleep", "2", "-calculateNormalModel"], label="t", validate=False)
    assert j["ok"], j
    s = server.rs_job_status(j["id"])
    assert s["state"] == "running"
    w = server.rs_job_wait(j["id"], timeout=15, poll_s=0.5)
    assert w["state"] == "done" and w["returncode"] == 0, w
    log = server.rs_job_log(j["id"], "stdout")
    assert "STUB argv" in log["text"] and "-calculateNormalModel" in log["text"]
    assert any(x["id"] == j["id"] for x in server.rs_jobs())


def test_background_fail_and_cancel():
    j = server.rs_start_job(["-fail"], validate=False)
    w = server.rs_job_wait(j["id"], timeout=10, poll_s=0.3)
    assert w["state"] == "failed" and w["returncode"] == 3
    j = server.rs_start_job(["-sleep", "30"], validate=False)
    time.sleep(0.5)
    c = server.rs_job_cancel(j["id"])
    assert c["ok"]
    time.sleep(0.5)
    assert server.rs_job_status(j["id"])["state"] == "cancelled"


def test_fs_scope():
    r = server.fs_write(str(TMP / "a" / "n.txt"), "hello")
    assert r["ok"]
    assert server.fs_write(str(TMP / "a" / "n.txt"), "x")["ok"] is False  # no overwrite
    assert server.fs_read(str(TMP / "a" / "n.txt"))["text"] == "hello"
    assert server.fs_list(str(TMP), "*", True)["ok"]
    assert server.fs_find(str(TMP), ["*.rsbox"])["matches"]
    outside = str(TMP.parent / "rsmcp-outside.txt")  # absolute on every OS, but not under the root
    out = server.fs_read(outside)
    assert out["ok"] is False and "outside" in out["error"]
    assert server.fs_write(outside, "x")["ok"] is False
    assert server.fs_read("etc/hostname")["ok"] is False  # relative paths are refused too


def test_compose():
    r = server.compose_model_export(
        project_path="C:/p.rsproj", export_file="C:/out/site_core.abc",
        export_params_xml="C:/params/export_abc.xml", region_rsbox="C:/params/core.rsbox",
        simplify_target_triangles=25_000_000, do_unwrap=True, unwrap_params_xml="C:/params/unwrap.xml",
        do_texture=True, texture_params_xml="C:/params/texture.xml")
    assert r["ok"]
    assert r["commands"] == [
        "-load", "C:/p.rsproj", "-selectMaximalComponent", "-setReconstructionRegion", "C:/params/core.rsbox",
        "-simplify", "25000000", "-unwrap", "C:/params/unwrap.xml", "-calculateTexture", "C:/params/texture.xml",
        "-exportSelectedModel", "C:/out/site_core.abc", "C:/params/export_abc.xml"]
    assert unknown_commands(r["commands"]) == []
    assert server.compose_model_export("p", "o", simplify_params_xml="a", simplify_target_triangles=1)["ok"] is False


def test_preflight_and_tools_registered():
    p = server.preflight()
    assert p["found"] is True
    import asyncio
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    for n in ["check_connection", "rs_run", "rs_start_job", "rs_job_status", "rs_job_wait", "launch_gui",
              "compose_model_export", "fs_read", "xml_patch", "run_rscmd_file", "list_known_commands"]:
        assert n in names, n
    print("tools:", len(names))


if __name__ == "__main__":
    import traceback
    failed = []
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("ok  ", name)
            except Exception:
                failed.append(name); print("FAIL", name); traceback.print_exc()
    print("ALL OK" if not failed else f"{len(failed)} FAILED: {', '.join(failed)}")
    sys.exit(1 if failed else 0)
