"""Terminal probability board."""

from __future__ import annotations

BAR = "=" * 52


def print_start(profile: dict, companies: list[dict], query: str = "") -> None:
    print(BAR, "\n  AI JOB HUNTER\n", BAR, sep="")
    print(f"  {profile.get('headline', '')}")
    print(f"  Interests  {', '.join(profile.get('interests') or [])}")
    print(f"  Target     {', '.join(profile.get('target_roles') or [])}")
    if query:
        print(f"  Query      {query}")
    print("-" * 52)
    for c in companies:
        print(f"  {c['name']:<16} {c['url']}")
    print(BAR)


def print_chrome(tab_n: int, url: str, extract_ms: int) -> None:
    print(f"  Chrome  tab #{tab_n}  {url}  (extract {extract_ms} ms)")


def print_step_header(company: dict, url: str, stage: str) -> None:
    print(BAR)
    print(f"  {company.get('name', '')}  ·  {url}")
    print(f"  STAGE {stage}")
    print("-" * 52)


def print_nav_board(ranked: list[dict]) -> None:
    width = min(max((len(r["text"]) for r in ranked), default=10), 28)
    for r in ranked:
        print(f"  {r['text'][:28]:<{width}}  {int(round(float(r['careers'])*100)):3d}%")
    if not ranked:
        print("  (no scored links)")


def print_filter_board(rows: list[dict], picked: list[str]) -> None:
    print(BAR)
    print("  STAGE filter")
    print("-" * 52)
    for r in rows:
        mark = "CLICK" if r.get("pick") else ""
        print(f"  {r['text'][:36]:<36}  apply {int(round(r['apply']*100)):3d}%  more {int(round(r['more']*100)):3d}%  {mark}")
    if picked:
        print(f"  → apply {', '.join(picked)}")
    elif not rows:
        print("  (no listing controls)")


def print_jobs_board(rows: list[dict]) -> None:
    for r in rows:
        flag = "PASS" if r["pass"] else "SKIP"
        print(f"  {r['text'][:44]:<44}  AI {int(round(r['ai']*100)):3d}%  Software {int(round(r['software']*100)):3d}%  {flag}")
    if not rows:
        print("  (no job titles on this page)")


def print_meta(latency_a: int, n_obs: int, latency_b: int | None = None) -> None:
    print("-" * 52)
    if latency_b is None:
        print(f"  Jev     {latency_a} ms")
    else:
        print(f"  Jev     {latency_a} ms + {latency_b} ms  (2 requests)")
    print(f"  Observed {n_obs} clickable elements")


DETAIL_LABELS = (
    ("ai_relevance", "AI relevance"),
    ("software_relevance", "Software relevance"),
    ("agent_llm_relevance", "Agent / LLM relevance"),
    ("backend_fullstack_relevance", "Backend / Full-stack"),
    ("overall_fit", "Overall fit"),
)


def print_detail(title: str, company: str, url: str, scores: dict, save: bool,
                 queue_left: int = 0) -> None:
    print(BAR)
    print(f"  {company}  ·  {title}\n  {url}\n  STAGE detail\n{'-'*52}")
    for key, label in DETAIL_LABELS:
        print(f"  {label:<24} {int(round(float(scores.get(key, 0))*100)):3d}%")
    print(f"  → {'Save' if save else 'Skip'}")
    print(f"  Jev     {scores.get('latency_ms', 0)} ms")
    print(f"  Queue  {queue_left} more job(s) for {company}")


def print_report(summary: dict, ranked: list[dict]) -> None:
    print(BAR, "\n  AI JOB HUNTER  ·  REPORT\n", BAR, sep="")
    print(f"  Scanned {summary['scanned']} companies / Visited {summary['pages']} pages")
    print(f"  Jobs found {summary['jobs_found']} / AI-Software jobs {summary['ai_software']}")
    print(f"  Strong matches {summary['strong']}\n{'-'*52}")
    if not ranked:
        print("  (no evaluated jobs)")
    for i, m in enumerate(ranked, 1):
        print(f"  {i}. {m['title']}\n     {m['company']}\n     Match: {int(round(m['overall_fit']*100))}%\n     {m['url']}")
    print(BAR)
