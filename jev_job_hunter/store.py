"""Persist run state and write final results."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from dotenv import load_dotenv

from jev_job_hunter.questions import MAX_RECENT_ACTIONS


def find_root() -> Path:
    cur = Path.cwd().resolve()
    for p in [cur, *cur.parents]:
        if (p / "config" / "companies.yaml").exists():
            return p
    return Path(__file__).resolve().parents[1]


def load_env(root: Path | None = None) -> Path:
    root = root or find_root()
    load_dotenv(root / ".env")
    return root


def load_run(root: Path) -> dict:
    path = root / "state" / "run.json"
    if not path.exists():
        raise SystemExit("ERROR: no run in progress. Run `jjh start` first.")
    return json.loads(path.read_text())


def save_run(root: Path, run: dict) -> None:
    path = root / "state" / "run.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run, indent=2) + "\n")


def log_path(root: Path) -> Path:
    return root / "state" / "jjh.log"


def truncate_log(root: Path) -> None:
    path = log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")


def append_log(root: Path, rec: dict) -> None:
    path = log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": rec.get("ts") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cmd": rec.get("cmd"),
        "url": rec.get("url"),
        "stage": rec.get("stage"),
        "links": rec.get("links"),
        "jev_ms": rec.get("jev_ms"),
        "extract_ms": rec.get("extract_ms"),
        "nav_ms": rec.get("nav_ms"),
        "total_ms": rec.get("total_ms"),
        "action": rec.get("action"),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_log(root: Path, n: int = 20) -> list[dict]:
    path = log_path(root)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _homepage(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    p = urlsplit(raw)
    host = p.netloc or p.path.split("/")[0]
    scheme = p.scheme or "https"
    return f"{scheme}://{host}/"


def _company_from_url(url: str, used: set[str]) -> dict:
    home = _homepage(url)
    host = (urlsplit(home).hostname or "company").lower()
    name = host.removeprefix("www.").split(".")[0].replace("-", " ").title()
    base = re.sub(r"[^a-z0-9]+", "-", host.removeprefix("www."))[:40].strip("-") or "company"
    cid = base
    n = 2
    while cid in used:
        cid = f"{base}-{n}"
        n += 1
    used.add(cid)
    return {"id": cid, "name": name, "url": home}


def resolve_companies(catalog: list[dict], specs: list[str] | None) -> list[dict]:
    """Catalog ids, hostnames, or full homepages. Unknown specs become a homepage start URL."""
    by_id = {str(c.get("id") or ""): c for c in catalog}
    by_host = {}
    for c in catalog:
        host = (urlsplit(c.get("url") or "").hostname or "").lower().removeprefix("www.")
        if host:
            by_host[host] = c
    wanted = [x.strip() for x in (specs or []) if x.strip()]
    if not wanted:
        return [{**c} for c in catalog]
    used: set[str] = set()
    out = []
    for spec in wanted:
        if spec in by_id:
            c = {**by_id[spec]}
            used.add(c["id"])
            out.append(c)
            continue
        home = _homepage(spec)
        host = (urlsplit(home).hostname or "").lower().removeprefix("www.")
        if host in by_host:
            c = {**by_host[host]}
            used.add(c["id"])
            out.append(c)
            continue
        out.append(_company_from_url(spec, used))
    return out


def new_run(root: Path, company_ids: list[str] | None, mock: bool, query: str = "") -> dict:
    cfg = yaml.safe_load((root / "config" / "companies.yaml").read_text())
    companies = [{**c, "status": "pending"} for c in resolve_companies(cfg["companies"], company_ids)]
    if not companies:
        raise SystemExit("ERROR: no companies selected.")
    companies[0]["status"] = "active"
    run = {
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mock": mock, "query": (query or "").strip(),
        "candidate": cfg["candidate"], "companies": companies,
        "current_id": companies[0]["id"], "pages_visited": 0, "recent_actions": [],
        "expected_url": companies[0]["url"],
        "by_company": {c["id"]: {"steps": 0, "jobs_opened": 0,
                                 "visited_hrefs": [], "queue": [], "candidates": []} for c in companies},
        "results": [], "stats": {"job_postings_seen": 0, "ai_software_jobs": 0},
    }
    save_run(root, run)
    return run


def current_company(run: dict) -> dict:
    return next((c for c in run["companies"] if c["id"] == run["current_id"]), run["companies"][0])


def company_for_url(run: dict, url: str) -> dict:
    active = next((c for c in run["companies"] if c.get("status") == "active"), None)
    if active:
        run["current_id"] = active["id"]
        return active
    u = (url or "").lower().replace("www.", "")
    for c in run["companies"]:
        host = c["url"].replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/").split("/")[0]
        if host in u:
            run["current_id"] = c["id"]
            if c["status"] == "pending":
                c["status"] = "active"
            return c
    return current_company(run)


def note_action(run: dict, action: str) -> None:
    run["recent_actions"] = (run.get("recent_actions") or [])[-MAX_RECENT_ACTIONS + 1:] + [action]
    if action.startswith("NAVIGATE "):
        run["expected_url"] = action[len("NAVIGATE "):].strip()


def expected_url(run: dict) -> str | None:
    url = (run.get("expected_url") or "").strip()
    if url:
        return url
    for action in reversed(run.get("recent_actions") or []):
        if isinstance(action, str) and action.startswith("NAVIGATE "):
            got = action[len("NAVIGATE "):].strip()
            if got:
                return got
    active = next((c for c in run.get("companies") or [] if c.get("status") == "active"), None)
    if active and active.get("url"):
        return str(active["url"])
    return None


def finish_company(run: dict, status: str) -> str | None:
    current_company(run)["status"] = status
    nxt = next((c for c in run["companies"] if c["status"] == "pending"), None)
    if not nxt:
        return None
    nxt["status"] = "active"
    run["current_id"] = nxt["id"]
    return nxt["url"]


def next_action(url: str | None) -> str:
    return f"NAVIGATE {url}" if url else "REPORT"


def finish_report(root: Path, run: dict) -> tuple[dict, list[dict]]:
    from jev_job_hunter.questions import FIT_SAVE_THRESHOLD
    ranked = sorted(run.get("results") or [], key=lambda m: m.get("overall_fit") or 0, reverse=True)
    scanned = sum(1 for c in run["companies"] if c["status"] != "pending")
    strong = sum(1 for m in ranked if (m.get("overall_fit") or 0) >= FIT_SAVE_THRESHOLD)
    summary = {
        "scanned": scanned or len(run["companies"]), "pages": run.get("pages_visited") or 0,
        "jobs_found": run.get("stats", {}).get("job_postings_seen") or 0,
        "ai_software": run.get("stats", {}).get("ai_software_jobs") or 0, "strong": strong,
    }
    write_results(root, run, summary, ranked)
    return summary, ranked


def write_results(root: Path, run: dict, summary: dict, ranked: list[dict]) -> None:
    out = root / "results"
    out.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "summary": summary, "matches": ranked,
               "run": {"companies": run["companies"], "pages_visited": run["pages_visited"],
                       "stats": run["stats"], "results": run["results"]}}
    (out / "latest.json").write_text(json.dumps(payload, indent=2) + "\n")
    lines = ["# AI Job Hunter — results", "",
             *(f"- {k} **{summary[v]}**" for k, v in
               (("Scanned", "scanned"), ("Visited", "pages"), ("Jobs found", "jobs_found"),
                ("AI-Software jobs", "ai_software"), ("Strong matches", "strong"))), ""]
    for i, m in enumerate(ranked, 1):
        lines += [f"## {i}. {m['title']}", f"- {m['company']}",
                  f"- Match: {int(round(m['overall_fit'] * 100))}%", f"- {m['url']}", ""]
    (out / "latest.md").write_text("\n".join(lines))
