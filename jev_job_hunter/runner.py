"""Autonomous hunt: start → extract → Jev → navigate → report over one CDP session."""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from jev_job_hunter import board
from jev_job_hunter.chrome import BrowserSession, ChromeError, load_extract_js
from jev_job_hunter.filters import pick_filter_clicks, pick_more_click
from jev_job_hunter.jev import (
    ask, build_control_questions, build_detail_questions, control_state,
    detail_state, score_page,
)
from jev_job_hunter.links import board_search_query, job_like_count, select_links, still_on_board
from jev_job_hunter.policy import complete_detail, decide_detail, decide_step, queued_job, tick
from jev_job_hunter.questions import (
    MAX_FILTER_ROUNDS, MAX_LINKS_TO_SCORE, MAX_SCROLLS, MIN_JOB_LINKS,
)
from jev_job_hunter.store import (
    append_log, company_for_url, expected_url, finish_company, finish_report, new_run,
    next_action, note_action, save_run, truncate_log,
)

CDP_HINT = (
    "Chrome CDP not reachable: {reason}. "
    "Fallback: evaluate_script + uv run jjh step --page -"
)


@dataclass
class PageResult:
    action: str
    stage: str
    url: str
    n_obs: int
    jev_ms: int | None


def coerce_page(data) -> dict:
    for _ in range(3):
        if not isinstance(data, str):
            break
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise SystemExit(f"ERROR: invalid wrapped page JSON: {e}") from e
    if not isinstance(data, dict):
        raise SystemExit("ERROR: page JSON must be an object")
    links = data.get("links") or []
    if not isinstance(links, list):
        links = []
    data["links"] = [
        {"text": str(L.get("text") or ""), "href": str(L.get("href") or "")}
        for L in links if isinstance(L, dict)
    ]
    data["url"] = str(data.get("url") or "")
    data["title"] = str(data.get("title") or "")
    data["text"] = str(data.get("text") or "")
    controls = data.get("controls") or []
    if not isinstance(controls, list):
        controls = []
    data["controls"] = [{"text": str(c.get("text") or "")} for c in controls if isinstance(c, dict)]
    return data


def apply_page(root: Path, run: dict, page: dict, mock: bool,
               allow_more: bool = False) -> PageResult:
    """Score + decide + print one board. Shared by `jjh step` and `jjh run`."""
    url, title, text = page["url"], page["title"], page["text"]
    n_jobs = job_like_count(page["links"])
    links = select_links(page["links"], MAX_LINKS_TO_SCORE, hunt_query(run))
    n_obs = len(page["links"])
    company = company_for_url(run, url)
    skipped = tick(run, company)
    if skipped:
        save_run(root, run)
        board.print_step_header(company, url, skipped.stage)
        sys.stdout.flush()
        return PageResult(skipped.action, skipped.stage, url, n_obs, None)
    st = run["by_company"][company["id"]]
    cand = queued_job(st, url)
    if cand is not None:
        result = ask(detail_state(run["candidate"], cand["title"], url, text),
                     build_detail_questions(), mock=mock)
        scores = {k: a.noul for k, a in result.answers.items() if a.type == "noul"}
        scores["latency_ms"] = result.latency_ms
        save = decide_detail(run, company, cand["title"], url, scores)
        d = complete_detail(run, company, url)
        save_run(root, run)
        board.print_detail(cand["title"], company.get("name", ""), url, scores, save,
                           queue_left=len(st.get("queue") or []))
        sys.stdout.flush()
        return PageResult(d.action, "detail", url, n_obs, result.latency_ms)
    nav_a, job_a, lat_a, lat_b = score_page(
        company, url, title, links, run.get("recent_actions") or [], mock)
    jev_ms = lat_a if lat_b is None else lat_a + lat_b
    d = decide_step(run, company, url, links, nav_a, job_a, allow_more=allow_more)
    save_run(root, run)
    board.print_step_header(company, url, d.stage)
    if d.job_rows is not None:
        board.print_jobs_board(d.job_rows)
    else:
        board.print_nav_board(d.ranked_nav or [])
    board.print_meta(lat_a, n_obs, lat_b)
    if n_jobs > len(links):
        print(f"  listing  {n_jobs} postings → scored {len(links)} by query")
    sys.stdout.flush()
    return PageResult(d.action, d.stage, url, n_obs, jev_ms)


def hunt_query(run: dict) -> str:
    q = (run.get("query") or "").strip()
    if q:
        return q
    cand = run.get("candidate") or {}
    roles = ", ".join(cand.get("target_roles") or [])
    return " ".join(x for x in (cand.get("headline") or "", roles) if x)


def _extract(session: BrowserSession, js: str, run: dict, url: str) -> dict:
    page = coerce_page(session.extract(js, need_text=_need_text(run, url)))
    tries = 0
    while not page["links"] and tries < 8:
        time.sleep(0.4)
        session.wait_ready(cap=1.0)
        page = coerce_page(session.extract(js, need_text=_need_text(run, url)))
        tries += 1
    return page


def scroll_listing(session: BrowserSession, js: str, run: dict, url: str,
                   page: dict) -> tuple[dict, int]:
    """Scroll to the bottom so footer / lazy links become visible, then stop
    when height and link count stabilize (also covers infinite job lists)."""
    scrolls = 0
    for _ in range(MAX_SCROLLS):
        info = page.get("scroll") or {}
        h = int(info.get("h") or 0)
        inner = int(info.get("inner") or 0)
        n_before = len(page["links"])
        if h <= inner + 80:
            break
        session.scroll_to_bottom()
        time.sleep(0.2)
        session.wait_ready(cap=1.2)
        page = _extract(session, js, run, url)
        scrolls += 1
        info2 = page.get("scroll") or {}
        h2 = int(info2.get("h") or 0)
        if len(page["links"]) <= n_before and h2 <= h + 40:
            break
    return page, scrolls


def score_controls(run: dict, page: dict, mock: bool):
    controls = page.get("controls") or []
    if not controls:
        return None, [], 0
    result = ask(
        control_state(run.get("candidate") or {}, hunt_query(run), page["url"], controls),
        build_control_questions(controls), mock=mock)
    rows = []
    for i, c in enumerate(controls):
        a, m = result.answers.get(f"apply_{i}"), result.answers.get(f"more_{i}")
        rows.append({
            "text": c.get("text") or "",
            "apply": float(a.noul) if a else 0.0,
            "more": float(m.noul) if m else 0.0,
        })
    return result.answers, rows, result.latency_ms


def apply_filters(session: BrowserSession, js: str, run: dict, url: str,
                  page: dict, mock: bool) -> tuple[dict, int]:
    if job_like_count(page["links"]) <= MAX_LINKS_TO_SCORE:
        return page, 0
    company = company_for_url(run, url)
    st = run["by_company"][company["id"]]
    if st.get("filtered"):
        return page, 0
    jev_ms, clicked = 0, set()
    for rnd in range(MAX_FILTER_ROUNDS):
        answers, rows, lat = score_controls(run, page, mock)
        if not answers:
            break
        jev_ms += lat
        apply = pick_filter_clicks(page.get("controls") or [], answers)
        more = pick_more_click(page.get("controls") or [], answers, clicked)
        if rnd == 0 and more:
            picks = [more]
        else:
            picks = [t for t in apply if t not in clicked] or ([more] if more else [])
        for r in rows:
            r["pick"] = r["text"] in picks
        show = sorted(rows, key=lambda r: max(r["apply"], r["more"]), reverse=True)[:12]
        if picks:
            board.print_filter_board(show, picks)
            sys.stdout.flush()
        if not picks:
            break
        typed = False
        typed_q = board_search_query(hunt_query(run))
        for text in picks:
            if session.click_text(text, stay_url=url):
                clicked.add(text)
                time.sleep(0.15)
                if typed_q and session.type_into_focused(typed_q):
                    typed = True
                    print(f"  search  {typed_q!r}", flush=True)
        session.wait_ready(cap=2.0)
        page = _extract(session, js, run, url)
        if not still_on_board(url, page.get("url") or ""):
            print(f"  filter  left the board — back to listing", flush=True)
            session.navigate(url)
            page = _extract(session, js, run, url)
            typed = False
        page, _ = scroll_listing(session, js, run, url, page)
        empty = typed and (
            job_like_count(page["links"]) < MIN_JOB_LINKS
            or "no results" in (page.get("text") or "").lower()
        )
        if empty:
            print("  search  no results — cleared", flush=True)
            session.type_into_focused("")
            session.wait_ready(cap=1.5)
            page = _extract(session, js, run, url)
            page, _ = scroll_listing(session, js, run, url, page)
            typed = False
        if typed and job_like_count(page["links"]) <= MAX_LINKS_TO_SCORE:
            break
        if rnd > 0:
            break
    st["filtered"] = True
    return page, jev_ms


def load_listing(session: BrowserSession, js: str, run: dict, url: str,
                 mock: bool) -> tuple[dict, int, int]:
    t_ex = time.perf_counter()
    page = _extract(session, js, run, url)
    page, _ = scroll_listing(session, js, run, url, page)
    extract_ms = int((time.perf_counter() - t_ex) * 1000)
    page, filter_ms = apply_filters(session, js, run, url, page, mock)
    return page, extract_ms, filter_ms


def try_more_listing(session: BrowserSession, js: str, run: dict, url: str,
                     page: dict, mock: bool) -> dict | None:
    answers, _, _ = score_controls(run, page, mock)
    nxt = pick_more_click(page.get("controls") or [], answers or {}, set())
    n_before = len(page["links"])
    if nxt and session.click_text(nxt, stay_url=url):
        session.wait_ready(cap=2.0)
        page = _extract(session, js, run, url)
        if not still_on_board(url, page.get("url") or ""):
            return None
        page, _ = scroll_listing(session, js, run, url, page)
        return page
    page2, n_scroll = scroll_listing(session, js, run, url, page)
    if n_scroll and len(page2["links"]) > n_before:
        return page2
    return None


def _need_text(run: dict, url: str) -> bool:
    active = next((c for c in run.get("companies") or [] if c.get("status") == "active"), None)
    if not active:
        return True
    st = run["by_company"].get(active["id"]) or {}
    return queued_job(st, url) is not None


def _print_timing(nav_ms: int, extract_ms: int, jev_ms: int | None) -> None:
    jev = 0 if jev_ms is None else jev_ms
    print(f"  timing  nav {nav_ms} ms · extract {extract_ms} ms · jev {jev} ms")
    sys.stdout.flush()


def _cdp_fail(reason: str) -> None:
    print(CDP_HINT.format(reason=reason))
    raise SystemExit(1)


def run_hunt(root: Path, companies: str, mock: bool, max_pages: int | None, tab: str,
             query: str = "") -> None:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    session: BrowserSession | None = None
    run: dict | None = None
    pages_done = 0
    t0 = time.perf_counter()
    exit_code = 0
    js = load_extract_js(root)
    try:
        print("  Chrome  one CDP handshake — click Allow once if prompted", flush=True)
        try:
            session = BrowserSession.open()
        except ChromeError as e:
            _cdp_fail(str(e))
        ids = [x.strip() for x in (companies or "").split(",") if x.strip()] or None
        run = new_run(root, ids, mock, query)
        truncate_log(root)
        first = run["companies"][0]["url"]
        note_action(run, f"NAVIGATE {first}")
        save_run(root, run)
        board.print_start(run["candidate"], run["companies"], run.get("query") or "")
        sys.stdout.flush()
        try:
            if tab == "reuse":
                session.reuse_tab(first)
            else:
                session.new_tab()
        except ChromeError as e:
            _cdp_fail(str(e))
        print(f"  Chrome  tab #{session.tab_index()}  ({tab})", flush=True)

        while True:
            if max_pages is not None and pages_done >= max_pages:
                break
            url = expected_url(run)
            if not url:
                break
            page_t0 = time.perf_counter()
            nav_ms = session.navigate(url)
            page, extract_ms, filter_ms = load_listing(session, js, run, url, mock)
            result = apply_page(root, run, page, mock, allow_more=True)
            more_rounds = 0
            while result.action == "MORE" and more_rounds < 4:
                more_rounds += 1
                nxt = try_more_listing(session, js, run, url, page, mock)
                if nxt is None:
                    result = PageResult(
                        next_action(finish_company(run, "done")), "jobs",
                        result.url, result.n_obs, result.jev_ms)
                    save_run(root, run)
                    break
                page = nxt
                result = apply_page(root, run, page, mock, allow_more=True)
            pages_done += 1
            _print_timing(nav_ms, extract_ms, result.jev_ms)
            if filter_ms:
                print(f"  filter  jev {filter_ms} ms", flush=True)
            append_log(root, {
                "cmd": "run", "url": result.url, "stage": result.stage,
                "links": result.n_obs, "jev_ms": result.jev_ms,
                "extract_ms": extract_ms, "nav_ms": nav_ms,
                "total_ms": int((time.perf_counter() - page_t0) * 1000),
                "action": result.action,
            })
            if not result.action.startswith("NAVIGATE "):
                break
    except KeyboardInterrupt:
        print("\n  interrupted — writing results so far", flush=True)
        exit_code = 130
    except ChromeError as e:
        print(CDP_HINT.format(reason=str(e)))
        exit_code = 1
    finally:
        elapsed = time.perf_counter() - t0
        should_report = run is not None and (pages_done > 0 or exit_code == 130)
        if should_report:
            summary, ranked = finish_report(root, run)
            board.print_report(summary, ranked)
            print(f"  total {pages_done} pages in {elapsed:.1f} s")
            print("ACTION END")
            sys.stdout.flush()
            append_log(root, {
                "cmd": "run", "stage": "report", "action": "END",
                "links": summary.get("jobs_found"),
                "total_ms": int(elapsed * 1000),
            })
        if session is not None:
            session.close()
    if exit_code:
        raise SystemExit(exit_code)
