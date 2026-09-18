"""Full mock flow: start → home → careers → details → report."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from jev_job_hunter.cli import main

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "tests/fixtures/openai_home.page.json"
CAREERS = ROOT / "tests/fixtures/openai_careers.page.json"
JOB = ROOT / "tests/fixtures/openai_job.page.json"
ROLE_HREFS = {
    "https://openai.com/careers/research-engineer-agents/",
    "https://openai.com/careers/research-engineer-reasoning/",
    "https://openai.com/careers/backend-engineer/",
    "https://openai.com/careers/ml-infra-engineer/",
    "https://openai.com/careers/platform-engineer/",
}


def _run(args: list[str]) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        main(args)
    return buf.getvalue()


def _last(out: str) -> str:
    return out.strip().splitlines()[-1]


def test_extract_js_matches_skill_and_jjh_js(monkeypatch):
    monkeypatch.chdir(ROOT)
    js = (ROOT / "jev_job_hunter/extract.js").read_text(encoding="utf-8").strip()
    skill = (ROOT / "agents/skills/jev-job-hunter/SKILL.md").read_text(encoding="utf-8")
    assert js in skill
    assert _run(["js"]).strip() == js


def test_mock_flow(monkeypatch, tmp_path):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("JJH_MOCK", "1")
    navigated: list[str] = []

    start = _run(["start", "--mock", "--companies", "openai"])
    assert "AI JOB HUNTER" in start
    assert "AI Engineer / Full-stack Developer" in start
    assert _last(start) == "ACTION NAVIGATE https://openai.com/"

    wrapped = tmp_path / "home.wrapped.json"
    wrapped.write_text(json.dumps(HOME.read_text(encoding="utf-8")), encoding="utf-8")
    step_home = _run(["step", "--mock", "--page", str(wrapped)])
    assert "%" in step_home
    assert "Jev" in step_home and "ms" in step_home
    assert "(2 requests)" not in step_home
    assert "Observed" in step_home
    assert _last(step_home) == "ACTION NAVIGATE https://openai.com/careers/"
    navigated.append("https://openai.com/careers/")

    step_jobs = _run(["step", "--mock", "--page", str(CAREERS)])
    assert "PASS" in step_jobs
    assert "AI" in step_jobs and "Software" in step_jobs
    assert "Research Engineer" in step_jobs
    assert "(2 requests)" in step_jobs
    assert " ms + " in step_jobs
    first = _last(step_jobs)
    assert first.startswith("ACTION NAVIGATE ")
    job1 = first[len("ACTION NAVIGATE "):]
    assert job1 in ROLE_HREFS
    assert job1 not in navigated
    navigated.append(job1)

    template = json.loads(JOB.read_text(encoding="utf-8"))
    page = tmp_path / "job.page.json"
    href = job1
    while True:
        payload = dict(template)
        payload["url"] = href
        page.write_text(json.dumps(payload), encoding="utf-8")
        out = _run(["step", "--mock", "--page", str(page)])
        assert "STAGE detail" in out
        assert "Queue" in out
        assert "Overall fit" in out
        line = _last(out)
        if line == "ACTION REPORT":
            break
        assert line.startswith("ACTION NAVIGATE ")
        href = line[len("ACTION NAVIGATE "):]
        assert href in ROLE_HREFS
        assert href not in navigated
        navigated.append(href)

    assert len(navigated) == 1 + 3  # careers hub + max 3 jobs
    assert len(navigated) == len(set(navigated))

    report = _run(["report"])
    assert "Strong matches" in report
    assert "Match:" in report
    assert _last(report) == "ACTION END"

    payload = json.loads((ROOT / "results/latest.json").read_text(encoding="utf-8"))
    matches = payload.get("matches") or payload.get("run", {}).get("results") or []
    strong = [m for m in matches if (m.get("overall_fit") or 0) >= 0.7 or m.get("saved")]
    assert len(strong) >= 1
    assert (ROOT / "results/latest.md").is_file()
    hrefs = [m.get("url") for m in matches]
    assert len(hrefs) == len(set(hrefs))


def test_pick_page():
    from jev_job_hunter.chrome import ChromeError, pick_page

    pages = [
        {"type": "page", "id": "g", "url": "https://mail.google.com/mail", "title": "Gmail"},
        {"type": "page", "id": "h", "url": "https://typesafe.ai/", "title": "TypeSafe"},
        {"type": "page", "id": "j", "url": "https://jobs.ashbyhq.com/typesafe-ai/abc?utm=1", "title": "Job"},
        {"type": "iframe", "id": "i", "url": "https://openai.com/", "title": "iframe"},
    ]
    assert pick_page(pages, "https://typesafe.ai/")["id"] == "h"
    assert pick_page(pages, "https://typesafe.ai")["id"] == "h"
    assert pick_page(pages, "https://jobs.ashbyhq.com/typesafe-ai")["id"] == "j"
    try:
        pick_page(pages, "https://openai.com/")
        raise AssertionError("expected no-match error")
    except ChromeError as e:
        msg = str(e)
        assert "no tab matches https://openai.com/" in msg
        assert "https://mail.google.com/mail" in msg
        assert "https://typesafe.ai/" in msg
        assert "Candidates:" in msg


def test_step_page_stdin(monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setenv("JJH_MOCK", "1")
    start = _run(["start", "--mock", "--companies", "openai"])
    assert _last(start) == "ACTION NAVIGATE https://openai.com/"
    monkeypatch.setattr(sys, "stdin", io.StringIO(HOME.read_text(encoding="utf-8")))
    out = _run(["step", "--mock", "--page", "-"])
    assert "%" in out
    assert _last(out) == "ACTION NAVIGATE https://openai.com/careers/"


def test_page_is_ready():
    from jev_job_hunter.chrome import page_is_ready

    assert page_is_ready([]) is False
    assert page_is_ready([("complete", 5)]) is False
    assert page_is_ready([("loading", 0), ("loading", 0)]) is False
    assert page_is_ready([("interactive", 0), ("interactive", 0)]) is False
    assert page_is_ready([("complete", 3), ("complete", 8)]) is False
    assert page_is_ready([("complete", 5), ("complete", 5)]) is True
    assert page_is_ready([("loading", 0), ("interactive", 4), ("interactive", 4)]) is True
    assert page_is_ready([("interactive", 4), ("interactive", 10)]) is False
    assert page_is_ready([("interactive", 4), ("interactive", 10), ("complete", 10)]) is True
    assert page_is_ready([("complete", 12), ("complete", 12), ("complete", 12)]) is True


def test_pick_filter_clicks():
    from types import SimpleNamespace
    from jev_job_hunter.filters import pick_filter_clicks, pick_more_click

    controls = [{"text": "Sales"}, {"text": "Research"}, {"text": "All teams"}]
    answers = {
        "apply_0": SimpleNamespace(noul=0.1),
        "apply_1": SimpleNamespace(noul=0.91),
        "apply_2": SimpleNamespace(noul=0.2),
        "more_0": SimpleNamespace(noul=0.1),
        "more_1": SimpleNamespace(noul=0.2),
        "more_2": SimpleNamespace(noul=0.88),
    }
    assert pick_filter_clicks(controls, answers) == ["Research"]
    assert pick_more_click(controls, answers) == "All teams"
    assert pick_more_click(controls, answers, {"All teams"}) is None


def test_query_ranks_late_job_titles():
    from jev_job_hunter.links import select_links

    links = []
    for i in range(160):
        links.append({
            "text": f"Account Director {i} Sales",
            "href": f"https://openai.com/careers/account-director-{i}-sales/",
        })
        links.append({
            "text": "Apply now",
            "href": f"https://jobs.ashbyhq.com/openai/{i}/application",
        })
    links.append({
        "text": "Applied AI Engineer, Codex Technical Success San Francisco",
        "href": "https://openai.com/careers/applied-ai-engineer-codex-san-francisco/",
    })
    links.append({
        "text": "AI Systems Engineer, Codex Agents Codex - Engineering San Francisco",
        "href": "https://openai.com/careers/ai-systems-engineer-codex-agents-san-francisco/",
    })
    got = select_links(links, 20, "Applied AI Engineer, Codex")
    texts = [L["text"] for L in got]
    assert any("Applied AI Engineer, Codex" in t for t in texts)
    assert any("Codex Agents" in t for t in texts)
    assert not any(t.startswith("Apply now") for t in texts)
    assert texts[0].startswith("Applied AI Engineer, Codex")


def test_board_search_query_strips_sentences():
    from jev_job_hunter.links import board_search_query

    assert board_search_query("Applied AI Engineer, Codex") == "Applied AI Engineer, Codex"
    assert "seeking" not in board_search_query("AI programmer seeking").lower()
    assert board_search_query("find me backend jobs that fit") == "backend"


def test_homepage_scroll_reveals_footer_careers():
    from jev_job_hunter.runner import scroll_listing

    first = {
        "url": "https://www.example.com/", "title": "Home", "text": "",
        "links": [
            {"text": "Chat", "href": "https://chat.example.com/"},
            {"text": "API", "href": "https://api.example.com/"},
        ],
        "controls": [], "scroll": {"y": 0, "h": 1469, "inner": 863},
    }
    after = {
        **first,
        "links": first["links"] + [
            {"text": "Careers", "href": "https://talent.example.com/"},
        ],
        "scroll": {"y": 606, "h": 1469, "inner": 863},
    }

    class Fake:
        def __init__(self):
            self.bottoms = 0

        def scroll_to_bottom(self):
            self.bottoms += 1

        def wait_ready(self, cap=1.0):
            pass

        def extract(self, js, need_text=True):
            return after if self.bottoms else first

    page, n = scroll_listing(Fake(), "js", {}, first["url"], first)
    assert n >= 1
    assert any(L["text"] == "Careers" for L in page["links"])


def test_still_on_board():
    from jev_job_hunter.links import still_on_board

    listing = "https://openai.com/careers/search/"
    assert still_on_board(listing, "https://openai.com/careers/search/?q=Codex")
    assert still_on_board(listing, "https://jobs.ashbyhq.com/openai/abc")
    assert still_on_board(listing, "https://openai.com/careers/search/applied-ai-engineer-codex/")
    assert not still_on_board(listing, "https://openai.com/research/index/")
    assert not still_on_board(listing, "https://openai.com/")


def test_resolve_any_homepage():
    from jev_job_hunter.store import resolve_companies

    catalog = [{"id": "openai", "name": "OpenAI", "url": "https://openai.com/"}]
    got = resolve_companies(catalog, ["https://stripe.com"])
    assert got[0]["url"] == "https://stripe.com/"
    assert got[0]["id"] == "stripe-com"
    openai = resolve_companies(catalog, ["openai.com"])
    assert openai[0]["id"] == "openai"
    named = resolve_companies(catalog, ["openai"])
    assert named[0]["url"] == "https://openai.com/"
