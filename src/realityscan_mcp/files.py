"""
Scoped file helpers.

RealityScan's CLI is file-driven: params.xml for export/unwrap/texture/simplify
settings, .rsbox for reconstruction regions, .rcconfig for global settings,
.rsInfo written beside exports, .rscmd command files, reports. The MCP needs to
read and write those, but only inside folders the operator has listed in
RS_MCP_ROOTS. Everything here refuses paths outside those roots.
"""

from __future__ import annotations

import fnmatch
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from . import config


class ScopeError(Exception):
    pass


def resolve(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise ScopeError(f"path must be absolute: {path}")
    rp = p.resolve()
    rs = config.roots()
    if not rs:
        raise ScopeError("no allowed roots: RS_MCP_ROOTS is empty and no local fixed drive besides the system drive was found")
    for root in rs:
        try:
            rp.relative_to(root)
            return rp
        except ValueError:
            continue
    raise ScopeError(f"{path} is outside the allowed roots {[str(r) for r in rs]}")


def list_dir(path: str, pattern: str = "*", recursive: bool = False, max_entries: int = 500) -> dict:
    p = resolve(path)
    if not p.is_dir():
        return {"ok": False, "error": f"not a directory: {p}"}
    entries = []
    it = p.rglob(pattern) if recursive else p.glob(pattern)
    for child in sorted(it):
        try:
            st = child.stat()
        except OSError:
            continue
        entries.append({
            "name": str(child.relative_to(p)),
            "type": "dir" if child.is_dir() else "file",
            "size": st.st_size if child.is_file() else None,
            "mtime": int(st.st_mtime),
        })
        if len(entries) >= max_entries:
            return {"ok": True, "path": str(p), "entries": entries, "truncated": True}
    return {"ok": True, "path": str(p), "entries": entries, "truncated": False}


def file_info(path: str) -> dict:
    p = resolve(path)
    if not p.exists():
        return {"ok": False, "error": f"does not exist: {p}", "exists": False}
    st = p.stat()
    return {"ok": True, "path": str(p), "exists": True, "type": "dir" if p.is_dir() else "file",
            "size": st.st_size, "mtime": int(st.st_mtime)}


def read_text(path: str, max_bytes: int = 200_000, tail: bool = False) -> dict:
    p = resolve(path)
    if not p.is_file():
        return {"ok": False, "error": f"not a file: {p}"}
    size = p.stat().st_size
    with open(p, "rb") as fh:
        if tail and size > max_bytes:
            fh.seek(size - max_bytes)
        data = fh.read(max_bytes)
    text = data.decode("utf-8", errors="replace")
    return {"ok": True, "path": str(p), "size": size, "truncated": size > max_bytes,
            "from_tail": tail and size > max_bytes, "text": text}


def write_text(path: str, content: str, overwrite: bool = False, make_dirs: bool = True) -> dict:
    p = resolve(path)
    if p.exists() and not overwrite:
        return {"ok": False, "error": f"exists and overwrite=false: {p}"}
    if make_dirs:
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(p), "bytes": len(content.encode("utf-8"))}


# ── XML helpers ──────────────────────────────────────────────────────────────
# RealityScan settings files are flat-ish XML: a root element with attributes
# (params.xml exported from the GUI's export dialogs) or a small tree (.rsbox).
# Rather than hard-code attribute names we might get wrong, the robust path is:
#   1. produce a reference file from RealityScan itself (export once from the
#      GUI with "save settings", or -exportReconstructionRegion / -exportGlobalSettings)
#   2. patch it here.
# `xml_patch` sets root attributes and/or element text by a slash path, e.g.
#   elements={"widthHeightDepth": "260 260 200", "Header/CentreEuclid/centre": "500100 5000200 40"}


def xml_read(path: str) -> dict:
    p = resolve(path)
    if not p.is_file():
        return {"ok": False, "error": f"not a file: {p}"}
    try:
        tree = ET.parse(p)
    except ET.ParseError as e:
        return {"ok": False, "error": f"XML parse error: {e}"}
    root = tree.getroot()

    def walk(el, depth=0):
        node = {"tag": el.tag, "attrib": dict(el.attrib)}
        text = (el.text or "").strip()
        if text:
            node["text"] = text
        kids = [walk(c, depth + 1) for c in el] if depth < 6 else []
        if kids:
            node["children"] = kids
        return node

    return {"ok": True, "path": str(p), "root": walk(root)}


def xml_patch(path_in: str, path_out: str, attributes: dict | None = None,
              elements: dict | None = None, overwrite: bool = True) -> dict:
    src = resolve(path_in)
    dst = resolve(path_out)
    if not src.is_file():
        return {"ok": False, "error": f"not a file: {src}"}
    if dst.exists() and not overwrite:
        return {"ok": False, "error": f"exists and overwrite=false: {dst}"}
    try:
        tree = ET.parse(src)
    except ET.ParseError as e:
        return {"ok": False, "error": f"XML parse error: {e}"}
    root = tree.getroot()
    changed = []
    for k, v in (attributes or {}).items():
        root.set(k, str(v))
        changed.append(f"@{k}")
    missing = []
    for path, text in (elements or {}).items():
        el = root.find(path)
        if el is None:
            missing.append(path)
            continue
        el.text = str(text)
        changed.append(path)
    if missing:
        return {"ok": False, "error": f"elements not found: {missing}", "changed": changed}
    dst.parent.mkdir(parents=True, exist_ok=True)
    tree.write(dst, encoding="utf-8", xml_declaration=True)
    return {"ok": True, "path": str(dst), "changed": changed}


def xml_write(path: str, root_tag: str, attributes: dict | None = None, overwrite: bool = False) -> dict:
    """Write a one-element params.xml (root tag + attributes). Use for settings
    files whose attribute names you already know from a reference export."""
    p = resolve(path)
    if p.exists() and not overwrite:
        return {"ok": False, "error": f"exists and overwrite=false: {p}"}
    root = ET.Element(root_tag)
    for k, v in (attributes or {}).items():
        root.set(k, str(v))
    p.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(p, encoding="utf-8", xml_declaration=True)
    return {"ok": True, "path": str(p), "attributes": len(attributes or {})}


def find_files(path: str, patterns: list[str], recursive: bool = True, max_entries: int = 500) -> dict:
    """Glob several patterns under a folder (e.g. ['*.rsproj', '*.rsInfo'])."""
    p = resolve(path)
    if not p.is_dir():
        return {"ok": False, "error": f"not a directory: {p}"}
    out = []
    walker = os.walk(p) if recursive else [(str(p), [], [c.name for c in p.iterdir() if c.is_file()])]
    for dirpath, _dirs, files in walker:
        for f in files:
            if any(fnmatch.fnmatch(f, pat) for pat in patterns):
                fp = Path(dirpath) / f
                try:
                    st = fp.stat()
                except OSError:
                    continue
                out.append({"path": str(fp), "size": st.st_size, "mtime": int(st.st_mtime)})
                if len(out) >= max_entries:
                    return {"ok": True, "matches": out, "truncated": True}
    return {"ok": True, "matches": out, "truncated": False}
