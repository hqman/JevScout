"""Parse chrome-devtools-mcp accessibility snapshots into clickable elements."""

from __future__ import annotations

import re
from jev_job_hunter.questions import MAX_SNAPSHOT_ELEMENTS

_UID = re.compile(r'uid=(\S+)\s+(\S+)(?:[^"]*"([^"]*)")?', re.I)
_FALLBACK = re.compile(r'uid=(\S+).*?"([^"]+)"')
_HREF = re.compile(r'(?:href|url)="([^"]*)"', re.I)
_TITLE = re.compile(r'RootWebArea\s+"([^"]+)"', re.I)
_KEEP = {"link", "button", "menuitem", "tab"}
_NEW_WINDOW = re.compile(r"\s*\(opens in a new window\)\s*$", re.I)


def page_title(text: str) -> str | None:
    m = _TITLE.search(text)
    return m.group(1) if m else None


def parse_snapshot(text: str, cap: int = MAX_SNAPSHOT_ELEMENTS) -> list[dict]:
    found, seen_uid, seen_pair = [], set(), set()
    for raw in text.splitlines():
        line = raw.strip()
        if "uid=" not in line:
            continue
        m = _UID.search(line)
        if m:
            uid, role, name = m.group(1), m.group(2).lower().strip(","), m.group(3) or ""
        else:
            fb = _FALLBACK.search(line)
            if not fb:
                continue
            uid, name, role = fb.group(1), fb.group(2), "link"
        if role not in _KEEP:
            continue
        href_m = _HREF.search(line)
        href = href_m.group(1) if href_m else ""
        name = _NEW_WINDOW.sub("", (name or "").strip()).strip()
        if (not name and not href) or uid in seen_uid:
            continue
        pair = (name.lower(), href)
        if name and pair in seen_pair:
            continue
        seen_uid.add(uid)
        if name:
            seen_pair.add(pair)
        found.append({"uid": uid, "role": role, "text": name, "href": href})
    return found[:cap]
