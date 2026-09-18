"""Listing-link hygiene. Drop apply buttons; keep posting URLs when the page is a board."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from jev_job_hunter.questions import MIN_JOB_LINKS

_APPLY = re.compile(r"^(apply now|apply for this job)\b", re.I)
_SKIP_SLUGS = {
    "search", "teams", "life", "emerging-talent", "university", "students",
    "locations", "benefits", "faq", "index",
}
_ATS_HOSTS = (
    "ashbyhq.com", "greenhouse.io", "lever.co", "myworkdayjobs.com",
    "smartrecruiters.com", "jobvite.com", "icims.com", "workable.com",
    "gem.com", "recruitee.com", "bamboohr.com", "rippling.com",
)
_JOB_PATH = re.compile(r"(career|job|opening|position|hiring|join-?us|work-with)", re.I)


def is_apply(text: str, href: str) -> bool:
    if _APPLY.search((text or "").strip()):
        return True
    path = (urlsplit(href or "").path or "").lower()
    return path.endswith("/application") or "/application/" in path


def is_job_like(text: str, href: str) -> bool:
    if is_apply(text, href):
        return False
    p = urlsplit(href or "")
    host = (p.hostname or "").lower()
    parts = [x for x in (p.path or "").rstrip("/").split("/") if x]
    if any(h in host for h in _ATS_HOSTS):
        return len(parts) >= 2
    slug = parts[-1].lower() if parts else ""
    if slug in _SKIP_SLUGS:
        return False
    if not _JOB_PATH.search(p.path or "") and not _JOB_PATH.search(host):
        return False
    if len(parts) >= 2 and "-" in slug and len(slug) >= 8:
        return True
    return len(parts) >= 3


def job_like_count(links: list[dict]) -> int:
    return sum(1 for L in links if is_job_like(str(L.get("text") or ""), str(L.get("href") or "")))


def still_on_board(listing: str, current: str) -> bool:
    """False when a filter click left the jobs board (e.g. header Research)."""
    if not (current or "").strip():
        return True
    lp, cp = urlsplit(listing or ""), urlsplit(current)
    ln = (lp.path or "/").rstrip("/") or "/"
    cn = (cp.path or "/").rstrip("/") or "/"
    if (lp.hostname or "").lower() == (cp.hostname or "").lower() and ln == cn:
        return True
    host = (cp.hostname or "").lower()
    if any(h in host for h in _ATS_HOSTS):
        return True
    if _JOB_PATH.search(cp.path or "") or _JOB_PATH.search(host):
        return True
    if ln != "/" and cn.startswith(ln + "/"):
        return True
    return False


_STOP = {"the", "and", "for", "jobs", "that", "fit", "find", "with", "from", "this", "your"}
_TALK = {
    "seeking", "looking", "find", "want", "need", "please", "help", "show",
    "search", "jobs", "job", "roles", "role", "openings", "fit", "me", "my",
    "someone", "position", "positions", "hiring", "work",
}


def query_tokens(query: str) -> list[str]:
    parts = re.findall(r"[a-z0-9]+", (query or "").lower())
    return [p for p in parts if p not in _STOP and (len(p) >= 3 or p in {"ai", "ml"})]


def board_search_query(query: str) -> str:
    """Compact job-board search string. Never dump a conversational sentence."""
    raw = (query or "").strip()
    if not raw:
        return ""
    words = re.findall(r"[a-z0-9]+", raw.lower())
    if any(w in _TALK for w in words):
        toks = [t for t in query_tokens(raw) if t not in _TALK]
        return " ".join(toks[:5])
    if len(raw) <= 80:
        return raw
    return " ".join(query_tokens(raw)[:5])


def query_score(text: str, tokens: list[str]) -> int:
    if not tokens:
        return 0
    hay = (text or "").lower()
    hits = sum(1 for t in tokens if t in hay)
    if not hits:
        return 0
    return hits + (3 if hits == len(tokens) else 0)


def select_links(links: list[dict], limit: int, query: str = "") -> list[dict]:
    cleaned = [L for L in links if not is_apply(str(L.get("text") or ""), str(L.get("href") or ""))]
    jobs = [L for L in cleaned if is_job_like(str(L.get("text") or ""), str(L.get("href") or ""))]
    pool = jobs if len(jobs) >= MIN_JOB_LINKS else cleaned
    tokens = query_tokens(query)
    if tokens and len(pool) > limit:
        pool = sorted(pool, key=lambda L: query_score(str(L.get("text") or ""), tokens), reverse=True)
    return pool[:limit]
