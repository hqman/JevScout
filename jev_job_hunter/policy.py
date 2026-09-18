"""Stage machine: Jev answers + thresholds → one ACTION line."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from jev_job_hunter.questions import (
    FIT_SAVE_THRESHOLD, JOB_POSTING_THRESHOLD, MAX_BOARD_NAV_LINKS,
    MAX_JOBS_OPEN_PER_COMPANY, MAX_NAVIGATION_STEPS_PER_COMPANY, MIN_JOB_LINKS,
    NAV_CLICK_THRESHOLD, RELEVANCE_THRESHOLD,
)
from jev_job_hunter.store import finish_company, next_action, note_action


@dataclass
class Decision:
    stage: str
    action: str
    ranked_nav: list | None = None
    job_rows: list | None = None


def normalize_href(url: str) -> str:
    s = (url or "").strip()
    if not s:
        return ""
    p = urlsplit(s)
    host = (p.hostname or "").lower()
    path = (p.path or "").rstrip("/")
    scheme = (p.scheme or "https").lower()
    if host:
        return f"{scheme}://{host}{path}"
    return path.lower()


def _n(answers: dict, key: str) -> float:
    a = answers.get(key)
    return float(a.noul) if a is not None else 0.0


def _rows(links: list[dict], answers: dict) -> list[dict]:
    return [{**link, "i": i, "careers": _n(answers, f"careers_{i}"), "job": _n(answers, f"job_{i}"),
             "software": _n(answers, f"sw_{i}"), "ai": _n(answers, f"ai_{i}")} for i, link in enumerate(links)]


def _act(run, stage: str, action: str, **kw) -> Decision:
    note_action(run, action)
    return Decision(stage=stage, action=action, **kw)


def _visited(st: dict) -> set[str]:
    return {normalize_href(h) for h in (st.get("visited_hrefs") or []) if h}


def _mark(st: dict, href: str) -> None:
    key = normalize_href(href)
    if not key:
        return
    vis = _visited(st)
    vis.add(key)
    st["visited_hrefs"] = list(vis)


def queued_job(st: dict, url: str) -> dict | None:
    key = normalize_href(url)
    if not key:
        return None
    for c in st.get("queue") or []:
        if normalize_href(c.get("href") or "") == key:
            return c
    return None


def tick(run: dict, company: dict) -> Decision | None:
    st = run["by_company"][company["id"]]
    st["steps"] = int(st.get("steps") or 0) + 1
    run["pages_visited"] = int(run.get("pages_visited") or 0) + 1
    if st["steps"] > MAX_NAVIGATION_STEPS_PER_COMPANY:
        return decide_skip(run)
    return None


def decide_skip(run: dict) -> Decision:
    return _act(run, "skip", next_action(finish_company(run, "skipped")))


def decide_step(run: dict, company: dict, url: str, links: list[dict],
                nav_answers: dict, job_answers: dict | None = None,
                allow_more: bool = False) -> Decision:
    answers = {**(nav_answers or {}), **(job_answers or {})}
    st = run["by_company"][company["id"]]
    rows = _rows(links, answers)
    hits = [r for r in rows if r["job"] >= JOB_POSTING_THRESHOLD]
    run["stats"]["job_postings_seen"] += len(hits)
    kind = answers["page_kind"].choice if answers.get("page_kind") else ""
    if kind == "job_list" or len(hits) >= MIN_JOB_LINKS:
        return _jobs(run, st, rows, hits, allow_more)
    return _nav(run, st, rows)


def complete_detail(run: dict, company: dict, url: str) -> Decision:
    st = run["by_company"][company["id"]]
    key = normalize_href(url)
    kept = [c for c in (st.get("queue") or []) if normalize_href(c.get("href") or "") != key]
    st["queue"] = kept
    _mark(st, url)
    st["jobs_opened"] = int(st.get("jobs_opened") or 0) + 1
    if kept:
        return _act(run, "detail", f"NAVIGATE {kept[0]['href']}")
    return _act(run, "detail", next_action(finish_company(run, "done")))


def _jobs(run, st, rows, hits, allow_more: bool = False) -> Decision:
    display = sorted(hits or rows, key=lambda r: max(r["ai"], r["software"]), reverse=True)
    board, passing, seen = [], [], set()
    for r in display:
        ok = r["software"] >= RELEVANCE_THRESHOLD or r["ai"] >= RELEVANCE_THRESHOLD
        board.append({**r, "pass": ok and r["job"] >= JOB_POSTING_THRESHOLD})
        href = r.get("href") or ""
        key = normalize_href(href)
        if r["job"] >= JOB_POSTING_THRESHOLD and ok and key and key not in seen:
            seen.add(key)
            passing.append({"title": r["text"], "href": href, "ai": r["ai"], "software": r["software"]})
    passing.sort(key=lambda c: max(c["ai"], c["software"]), reverse=True)
    visited = _visited(st)
    queue = []
    for c in passing:
        if normalize_href(c["href"]) in visited:
            continue
        queue.append(c)
        if len(queue) >= MAX_JOBS_OPEN_PER_COMPANY:
            break
    st["queue"] = queue
    have = {normalize_href(c.get("href") or "") for c in st.get("candidates") or []}
    for c in passing:
        key = normalize_href(c["href"])
        if key not in have:
            st.setdefault("candidates", []).append(c)
            have.add(key)
    run["stats"]["ai_software_jobs"] = sum(len(v.get("candidates") or []) for v in run["by_company"].values())
    if queue:
        _mark(st, queue[0]["href"])
        return _act(run, "jobs", f"NAVIGATE {queue[0]['href']}", job_rows=board[:15])
    if allow_more:
        return _act(run, "jobs", "MORE", job_rows=board[:15])
    return _act(run, "jobs", next_action(finish_company(run, "done")), job_rows=board[:15])


def _nav(run, st, rows) -> Decision:
    ranked = sorted(rows, key=lambda r: r["careers"], reverse=True)
    visited = _visited(st)
    for r in ranked:
        if r["careers"] < NAV_CLICK_THRESHOLD:
            break
        href = r.get("href") or ""
        key = normalize_href(href)
        if not key or key in visited:
            continue
        _mark(st, href)
        return _act(run, "navigate", f"NAVIGATE {href}", ranked_nav=ranked[:MAX_BOARD_NAV_LINKS])
    return _act(run, "navigate", next_action(finish_company(run, "skipped")),
                ranked_nav=ranked[:MAX_BOARD_NAV_LINKS])


def decide_detail(run: dict, company: dict, title: str, url: str, scores: dict) -> bool:
    overall = float(scores.get("overall_fit") or 0)
    save = overall >= FIT_SAVE_THRESHOLD
    run["results"].append({
        "title": title, "company": company.get("name"), "company_id": company.get("id"),
        "url": url, "overall_fit": overall, "saved": save,
        "ai_relevance": float(scores.get("ai_relevance") or 0),
        "software_relevance": float(scores.get("software_relevance") or 0),
        "agent_llm_relevance": float(scores.get("agent_llm_relevance") or 0),
        "backend_fullstack_relevance": float(scores.get("backend_fullstack_relevance") or 0),
    })
    return save
