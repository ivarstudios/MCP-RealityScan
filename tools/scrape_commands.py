#!/usr/bin/env python3
"""Regenerate src/realityscan_mcp/commands.py from a plain list of command names
(one per line, without the leading dash). Produce the list by copying the
"All commands" documentation page's command column into commands.txt, then:

    python tools/scrape_commands.py commands.txt
"""
import re, sys, pathlib, datetime
src = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
names = sorted({re.sub(r"^-", "", l.strip()) for l in src.splitlines() if l.strip()})
out = pathlib.Path(__file__).resolve().parents[1] / "src" / "realityscan_mcp" / "commands.py"
body = out.read_text(encoding="utf-8")
new = "KNOWN_COMMANDS: frozenset[str] = frozenset({\n" + "".join(f'    "-{n}",\n' for n in names) + "})"
body = re.sub(r"KNOWN_COMMANDS: frozenset\[str\] = frozenset\(\{.*?\}\)", new, body, flags=re.S)
body = re.sub(r"scraped \d{4}-\d{2}-\d{2}", f"scraped {datetime.date.today()}", body)
body = re.sub(r"\n\d+ commands\.", f"\n{len(names)} commands.", body)
out.write_text(body, encoding="utf-8")
print(f"wrote {len(names)} commands to {out}")
