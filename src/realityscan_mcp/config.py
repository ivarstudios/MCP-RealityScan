"""
Configuration and executable discovery.

Everything is driven by environment variables so the desktop MCP config is the
single place to change behaviour:

  REALITYSCAN_EXE    absolute path to RealityScan.exe (wins over discovery)
  REALITYSCAN_HOME   install folder containing RealityScan.exe
  RS_MCP_ROOTS       ';'-separated list of folders the file tools may touch
                     (default: every local fixed drive but the system drive)
  RS_MCP_JOBS        folder for job logs (default: %LOCALAPPDATA%\\realityscan-mcp\\jobs
                     or <tempdir>/realityscan-mcp/jobs)
  RS_MCP_DEFAULT_TIMEOUT  seconds a synchronous run() waits before giving up
                     (default 3600; background jobs have no timeout)

Nothing here raises on import when RealityScan is not installed, so the server
still starts and `check_connection` can report the problem.
"""

from __future__ import annotations

import ctypes
import glob
import os
import re
import sys
import tempfile
from ctypes import Structure, byref, c_uint, c_ushort, c_void_p, create_string_buffer
from pathlib import Path

_INSTALL_GLOBS = [
    r"C:\Program Files\Epic Games\RealityScan*\RealityScan.exe",
    r"C:\Program Files\Epic Games\RealityScan\RealityScan.exe",
    r"C:\Program Files\Capturing Reality\RealityScan\RealityScan.exe",
    r"C:\Program Files\Capturing Reality\RealityCapture\RealityCapture.exe",
    r"C:\Program Files\Epic Games\RealityCapture*\RealityCapture.exe",
]


def find_executable() -> str | None:
    env_exe = os.environ.get("REALITYSCAN_EXE")
    if env_exe and os.path.isfile(env_exe):
        return env_exe
    home = os.environ.get("REALITYSCAN_HOME")
    if home:
        for name in ("RealityScan.exe", "RealityCapture.exe"):
            p = os.path.join(home, name)
            if os.path.isfile(p):
                return p
    matches: list[str] = []
    for pat in _INSTALL_GLOBS:
        matches.extend(glob.glob(pat))
    rs = sorted((m for m in matches if "realityscan" in m.lower()), reverse=True)
    rc = sorted((m for m in matches if "realityscan" not in m.lower()), reverse=True)
    for p in rs + rc:
        if os.path.isfile(p):
            return p
    return None


def roots() -> list[Path]:
    """Where the file tools may read and write: RS_MCP_ROOTS when it is set, else
    every local fixed drive but the system drive. Nothing about a project or a
    dataset belongs here - the same install works on any PC, for any project. The
    default keeps the tools off C:\\ (Windows, programs) and off network shares
    (read-only sources live there); set RS_MCP_ROOTS to narrow or widen it."""
    raw = os.environ.get("RS_MCP_ROOTS", "")
    out: list[Path] = []
    for part in raw.split(";"):
        part = part.strip().strip('"')
        if part:
            out.append(Path(part).resolve())
    return out if raw.strip() else default_roots()


def default_roots() -> list[Path]:
    if os.name != "nt":
        return []
    system = (os.environ.get("SystemDrive") or "C:").rstrip("\\").upper()
    k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    mask = k32.GetLogicalDrives()
    out = []
    for i in range(26):
        if not mask & (1 << i):
            continue
        drive = f"{chr(65 + i)}:"
        if drive != system and k32.GetDriveTypeW(drive + "\\") == 3:  # DRIVE_FIXED
            out.append(Path(drive + "\\"))
    return out


def roots_source() -> str:
    if os.environ.get("RS_MCP_ROOTS", "").strip():
        return "RS_MCP_ROOTS"
    return "default: local fixed drives but the system drive"


def jobs_dir() -> Path:
    raw = os.environ.get("RS_MCP_JOBS")
    if raw:
        p = Path(raw)
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        p = Path(os.environ["LOCALAPPDATA"]) / "realityscan-mcp" / "jobs"
    else:
        p = Path(tempfile.gettempdir()) / "realityscan-mcp" / "jobs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_timeout() -> float:
    try:
        return float(os.environ.get("RS_MCP_DEFAULT_TIMEOUT", "3600"))
    except ValueError:
        return 3600.0


# ── Windows file version via ctypes (no pywin32) ─────────────────────────────
class _FixedFileInfo(Structure):
    _fields_ = [
        ("dwSignature", c_uint), ("dwStrucVersion", c_uint),
        ("dwFileVersionMS", c_uint), ("dwFileVersionLS", c_uint),
        ("dwProductVersionMS", c_uint), ("dwProductVersionLS", c_uint),
        ("dwFileFlagsMask", c_uint), ("dwFileFlags", c_uint),
        ("dwFileOS", c_uint), ("dwFileType", c_uint),
        ("dwFileSubtype", c_uint), ("dwFileDateMS", c_uint),
        ("dwFileDateLS", c_uint),
    ]


def file_version(path: str | None) -> str | None:
    if not path or os.name != "nt":
        return None
    try:
        ver = ctypes.windll.version  # type: ignore[attr-defined]
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = create_string_buffer(size)
        ver.GetFileVersionInfoW(path, 0, size, buf)
        # The fixed fields hold 16 bits per part, which cuts RealityScan's build number short
        # (2.2.0.119430 reads as 2.2.0.53894). The text version has it in full, so prefer that.
        text = _text_version(ver, buf)
        if text:
            return text
        ptr = c_void_p()
        length = c_uint()
        if not ver.VerQueryValueW(buf, "\\", byref(ptr), byref(length)):
            return None
        ffi = ctypes.cast(ptr, ctypes.POINTER(_FixedFileInfo)).contents
        ms, ls = ffi.dwProductVersionMS, ffi.dwProductVersionLS
        return f"{ms >> 16 & 0xFFFF}.{ms & 0xFFFF}.{ls >> 16 & 0xFFFF}.{ls & 0xFFFF}"
    except Exception:
        return None


def _text_version(ver, buf) -> str | None:
    """Numeric part of the ProductVersion (or FileVersion) string, e.g. '2.2.0.119430' from '2.2.0.119430.RS'."""
    ptr = c_void_p()
    length = c_uint()
    if not ver.VerQueryValueW(buf, "\\VarFileInfo\\Translation", byref(ptr), byref(length)) or length.value < 4:
        return None
    lang, codepage = ctypes.cast(ptr, ctypes.POINTER(c_ushort * 2)).contents
    for key in ("ProductVersion", "FileVersion"):
        query = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\{key}"
        if ver.VerQueryValueW(buf, query, byref(ptr), byref(length)) and length.value:
            match = re.match(r"\d+(?:\.\d+)*", ctypes.wstring_at(ptr, length.value).strip("\0 "))
            if match:
                return match.group(0)
    return None


def snapshot() -> dict:
    exe = find_executable()
    return {
        "executable": exe,
        "found": bool(exe),
        "version": file_version(exe),
        "roots": [str(r) for r in roots()],
        "roots_from": roots_source(),
        "jobs_dir": str(jobs_dir()),
        "default_timeout_s": default_timeout(),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "env": {
            "REALITYSCAN_EXE": os.environ.get("REALITYSCAN_EXE"),
            "REALITYSCAN_HOME": os.environ.get("REALITYSCAN_HOME"),
            "RS_MCP_ROOTS": os.environ.get("RS_MCP_ROOTS"),
            "RS_MCP_JOBS": os.environ.get("RS_MCP_JOBS"),
        },
    }
