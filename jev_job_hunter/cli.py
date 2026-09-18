"""jjh CLI: start / js / step / run / report / log."""

from __future__ import annotations

import argparse, json, os, sys, time
from pathlib import Path

from jev_job_hunter import board
from jev_job_hunter.chrome import ChromeError, extract_from_chrome, load_extract_js
from jev_job_hunter.runner import apply_page, coerce_page, run_hunt
from jev_job_hunter.snapshot import page_title, parse_snapshot
from jev_job_hunter.store import (
    append_log, expected_url, finish_report, load_env, load_run, new_run,
    note_action, read_log, save_run, truncate_log,
)
from jev_job_hunter.questions import MAX_LINKS_TO_SCORE


def _company_specs(ns) -> list[str]:
    parts = []
    for chunk in (getattr(ns, "companies", "") or "").split(","):
        if chunk.strip():
            parts.append(chunk.strip())
    for u in getattr(ns, "url", None) or []:
        if str(u).strip():
            parts.append(str(u).strip())
    return parts


def _mock(ns) -> bool:
    return bool(getattr(ns, "mock", False) or os.environ.get("JJH_MOCK") == "1")


def _action(action: str) -> None:
    print(f"ACTION {action}")


def _read(root: Path, p: str, kind: str) -> Path:
    path = Path(p)
    if not path.is_file():
        path = root / p
    if not path.is_file():
        raise SystemExit(f"ERROR: {kind} not found: {p}")
    return path


def _load_page_text(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit(f"ERROR: invalid page JSON: {e}") from e
    return coerce_page(data)


def _load_page(path: Path) -> dict:
    return _load_page_text(path.read_text(encoding="utf-8", errors="replace"))


def _cdp_fail(reason: str) -> None:
    print(
        f"Chrome CDP not reachable: {reason}. "
        "Fallback: evaluate_script + uv run jjh step --page -"
    )
    raise SystemExit(1)


def _page_from_chrome(root: Path, run: dict) -> tuple[dict, int, str, int]:
    want = expected_url(run)
    t0 = time.perf_counter()
    try:
        raw, picked, tab_n = extract_from_chrome(want, load_extract_js(root))
    except ChromeError as e:
        _cdp_fail(str(e))
    extract_ms = int((time.perf_counter() - t0) * 1000)
    tab_url = str(picked.get("url") or raw.get("url") or "")
    return coerce_page(raw), tab_n, tab_url, extract_ms


def cmd_start(root: Path, ns) -> None:
    t0 = time.perf_counter()
    ids = _company_specs(ns) or None
    run = new_run(root, ids, _mock(ns), getattr(ns, "query", "") or "")
    truncate_log(root)
    url = run["companies"][0]["url"]
    note_action(run, f"NAVIGATE {url}")
    save_run(root, run)
    board.print_start(run["candidate"], run["companies"], run.get("query") or "")
    action = f"NAVIGATE {url}"
    _action(action)
    append_log(root, {
        "cmd": "start", "url": url, "stage": "start", "action": action,
        "total_ms": int((time.perf_counter() - t0) * 1000),
    })


def cmd_js(root: Path, ns) -> None:
    sys.stdout.write(load_extract_js(root))


def cmd_step(root: Path, ns) -> None:
    t0 = time.perf_counter()
    rec = {"cmd": "step", "url": None, "stage": None, "links": None,
           "jev_ms": None, "extract_ms": None, "action": None}
    chrome_meta = None
    try:
        run = load_run(root)
        mock = _mock(ns) or bool(run.get("mock"))
        if ns.page:
            if ns.page == "-":
                page = _load_page_text(sys.stdin.read())
            else:
                page = _load_page(_read(root, ns.page, "page JSON"))
        elif ns.snapshot:
            if not ns.url:
                raise SystemExit("ERROR: --url is required with --snapshot")
            snap = _read(root, ns.snapshot, "snapshot").read_text(errors="replace")
            elements = parse_snapshot(snap)
            links = [e for e in elements if e.get("text")][:MAX_LINKS_TO_SCORE]
            page = {"url": ns.url, "title": page_title(snap), "text": snap, "links": links}
            page["_n_obs"] = len(elements)
        else:
            page, tab_n, tab_url, extract_ms = _page_from_chrome(root, run)
            rec["extract_ms"] = extract_ms
            chrome_meta = (tab_n, tab_url or page["url"], extract_ms)
        if chrome_meta:
            board.print_chrome(*chrome_meta)
        result = apply_page(root, run, page, mock)
        rec["url"] = result.url
        rec["links"] = result.n_obs if page.get("_n_obs") is None else page["_n_obs"]
        rec["jev_ms"] = result.jev_ms
        rec["stage"] = result.stage
        rec["action"] = result.action
        _action(result.action)
    finally:
        rec["total_ms"] = int((time.perf_counter() - t0) * 1000)
        append_log(root, rec)


def cmd_run(root: Path, ns) -> None:
    run_hunt(root, ",".join(_company_specs(ns)), _mock(ns), ns.max_pages, ns.tab, ns.query)


def cmd_report(root: Path, ns) -> None:
    t0 = time.perf_counter()
    run = load_run(root)
    summary, ranked = finish_report(root, run)
    board.print_report(summary, ranked)
    _action("END")
    append_log(root, {
        "cmd": "report", "stage": "report", "action": "END",
        "links": summary.get("jobs_found"),
        "total_ms": int((time.perf_counter() - t0) * 1000),
    })


def cmd_log(root: Path, ns) -> None:
    rows = read_log(root, 20)
    if not rows:
        print("(no jjh log)")
        return
    print(f"{'ts':<20} {'cmd':<6} {'stage':<10} {'links':>5} {'nav_ms':>6} {'ext_ms':>6} {'jev_ms':>6} {'total':>7}  action")
    for r in rows:
        ts = str(r.get("ts") or "")[:20]
        cmd = str(r.get("cmd") or "")[:6]
        stage = str(r.get("stage") or "-")[:10]
        def _n(key, width):
            v = r.get(key)
            return f"{'-' if v is None else v:>{width}}"
        action = r.get("action") or ""
        print(f"{ts:<20} {cmd:<6} {stage:<10} {_n('links',5)} {_n('nav_ms',6)} {_n('extract_ms',6)} {_n('jev_ms',6)} {_n('total_ms',7)}  {action}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jjh", description="Jev AI Job Hunter")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--companies", default="")
    s.add_argument("--url", action="append", default=[], metavar="HOMEPAGE")
    s.add_argument("--query", default="")
    s.add_argument("--mock", action="store_true")
    sub.add_parser("js")
    t = sub.add_parser("step")
    src = t.add_mutually_exclusive_group(required=False)
    src.add_argument("--page")
    src.add_argument("--snapshot")
    t.add_argument("--url", default=""); t.add_argument("--mock", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--companies", default="")
    r.add_argument("--url", action="append", default=[], metavar="HOMEPAGE")
    r.add_argument("--mock", action="store_true")
    r.add_argument("--max-pages", type=int, default=None)
    r.add_argument("--tab", choices=("new", "reuse"), default="new")
    r.add_argument("--query", default="")
    sub.add_parser("report").add_argument("--mock", action="store_true")
    sub.add_parser("log")
    return p


def main(argv=None) -> None:
    ns = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    root = load_env()
    {"start": cmd_start, "js": cmd_js, "step": cmd_step, "run": cmd_run,
     "report": cmd_report, "log": cmd_log}[ns.cmd](root, ns)


if __name__ == "__main__":
    main()
