"""TypeSafe / Jev wrapper, HTTP fallback, and keyword-heuristic mock."""

from __future__ import annotations

import hashlib, json, os, re, time
from dataclasses import dataclass, field

from jev_job_hunter.questions import (
    DETAIL_NOULS, JOB_POSTING_THRESHOLD, JOB_TEXT_LIMIT, MAX_CONTROLS_TO_SCORE,
    MAX_JOB_FILTER, MAX_LINKS_TO_SCORE, MIN_JOB_LINKS, PAGE_KIND_CRITERIA,
    PAGE_KIND_INSTRUCTIONS, apply_filter_instructions, browse_more_instructions,
    is_ai_related_instructions, is_job_posting_instructions,
    is_software_related_instructions, leads_to_jobs_instructions,
)

API_URL = "https://api.typesafe.ai/v1/systemone"
PAGE_KINDS = list(PAGE_KIND_CRITERIA)
_JOB = re.compile(
    r"\b(engineer|scientist|designer|manager|director|advocate|recruiter|"
    r"executive|intern|researcher|specialist|analyst|coordinator|developer|sre|pm)\b", re.I)
_NAV = re.compile(
    r"^(careers?|jobs?|about|company|blog|news|contact|home|research|"
    r"products?|safety|login|sign in|teams?|life|open roles)$", re.I)


@dataclass
class Ans:
    type: str
    noul: float = 0.0
    choice: str = ""
    probabilities: dict = field(default_factory=dict)
    confidence: float = 0.0


@dataclass
class JevResult:
    answers: dict[str, Ans]
    latency_ms: int


def _j(key: str, base: float, spread: float = 0.03) -> float:
    h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16)
    return max(0.0, min(1.0, base + ((h % 1000) / 1000 - 0.5) * 2 * spread))


def _lat(payload: str) -> int:
    return 90 + int(hashlib.md5(payload.encode()).hexdigest()[:8], 16) % 51


def _tbl(blob: str, rows: list, default: float, tag: str) -> float:
    low = blob.lower()
    for keys, base in rows:
        if any(k in low for k in keys):
            return _j(blob + tag, base, 0.04)
    return _j(blob + tag, default, 0.04)


def mock_noul_careers(text: str, href: str) -> float:
    return _tbl(f"{text} {href}", [
        (("career", "jobs", "hiring", "join us", "join-us", "open role", "openings", "positions", "work with us"), 0.93),
        (("life at", "teams", "people"), 0.48), (("about", "company"), 0.20),
        (("research", "news", "blog"), 0.16),
    ], 0.08, "|c")


def mock_noul_job_posting(text: str, href: str) -> float:
    if _NAV.match(text.strip()):
        return _j(text + "|jp", 0.08)
    if re.match(r"^apply(\s+now)?$", text.strip(), re.I):
        return _j(text + "|jp", 0.08)
    if _JOB.search(text) and 8 <= len(text) <= 80:
        return _j(text + href + "|jp", 0.91, 0.04)
    if any(k in href.lower() for k in ("/job/", "/jobs/", "lever.co", "ashby", "greenhouse")):
        return _j(href + "|jp", 0.78, 0.05)
    return _j(text + href + "|jp", 0.10, 0.04)


def mock_noul_software(text: str) -> float:
    return _tbl(text, [
        (("engineer", "developer", "software", "backend", "frontend", "full-stack", "fullstack", "infra", "platform", "sre"), 0.90),
        (("advocate", "solutions", "forward deployed"), 0.74),
        (("designer", "recruiter", "account executive", "sales", "program manager"), 0.18),
    ], 0.28, "|sw")


def mock_noul_ai(text: str) -> float:
    v = _tbl(text, [
        (("ai", "ml", "machine learning", "llm", "agent", "gpt", "research engineer", "research scientist", "model"), 0.92),
        (("designer", "recruiter", "account executive", "sales"), 0.10),
        (("backend", "full-stack", "fullstack", "software engineer"), 0.42),
    ], 0.28, "|ai")
    if "agent" in text.lower():
        v = max(v, _j(text + "|ag", 0.96, 0.015))
    return v


def mock_page_kind(url: str, title: str, links: list[dict]) -> str:
    u, n = url.lower(), sum(1 for L in links if mock_noul_job_posting(L.get("text") or "", L.get("href") or "") >= 0.6)
    if n >= MIN_JOB_LINKS:
        return "job_list"
    if any(x in u for x in ("/job/", "/jobs/")) and n < 2:
        return "job_detail"
    if "career" in u or "/jobs" in u or any(x in title.lower() for x in ("careers", "jobs")):
        return "careers_hub"
    if u.count("/") <= 3 and not any(x in u for x in ("career", "blog", "research", "about")):
        return "homepage"
    return "other"


def _choice(best: str, options: list[str], key: str) -> Ans:
    peak = _j(key + "|pk", 0.84, 0.04)
    rest = (1.0 - peak) / max(1, len(options) - 1)
    probs = {o: (peak if o == best else rest) for o in options}
    s = sum(probs.values()) or 1.0
    return Ans(type="choice", choice=best, probabilities={k: v / s for k, v in probs.items()}, confidence=peak)


def mock_detail(state: dict) -> dict[str, float]:
    job = state.get("job") or {}
    b = f"{job.get('title', '')} {job.get('text', '')}".lower()
    ai = 0.90 if any(x in b for x in ("ai", "ml", "llm", "agent", "model", "research")) else 0.28
    sw = 0.88 if any(x in b for x in ("engineer", "software", "backend", "python", "system")) else 0.30
    ag = 0.91 if any(x in b for x in ("agent", "llm", "tool-use", "orchestr")) else 0.22
    be = 0.70 if any(x in b for x in ("backend", "full-stack", "fullstack", "api", "infra", "python")) else 0.35
    raw = dict(ai_relevance=ai, software_relevance=sw, agent_llm_relevance=ag,
               backend_fullstack_relevance=be, overall_fit=max(0.55 * ai + 0.45 * sw, 0.5 * ag + 0.5 * sw))
    return {k: _j(b + k, v) for k, v in raw.items()}


def build_nav_questions(links: list) -> dict:
    qs = {"page_kind": {"type": "choice", "instructions": PAGE_KIND_INSTRUCTIONS, "criteria": PAGE_KIND_CRITERIA}}
    for i, L in enumerate(links):
        idx = int(L.get("i", i))
        text, href = L.get("text") or "", L.get("href") or ""
        qs[f"careers_{idx}"] = {"type": "noul", "instructions": leads_to_jobs_instructions(idx, text, href)}
        qs[f"job_{idx}"] = {"type": "noul", "instructions": is_job_posting_instructions(idx, text, href)}
    return qs


def build_job_questions(indices: list[int], links: list) -> dict:
    qs = {}
    for j, i in enumerate(indices):
        title = (links[i].get("text") or links[i].get("title") or "") if 0 <= i < len(links) else ""
        qs[f"sw_{i}"] = {"type": "noul", "instructions": is_software_related_instructions(j, title)}
        qs[f"ai_{i}"] = {"type": "noul", "instructions": is_ai_related_instructions(j, title)}
    return qs


def build_detail_questions() -> dict:
    return {k: {"type": "noul", "instructions": v} for k, v in DETAIL_NOULS.items()}


def build_control_questions(controls: list[dict]) -> dict:
    qs = {}
    for i, c in enumerate(controls[:MAX_CONTROLS_TO_SCORE]):
        text = c.get("text") or ""
        qs[f"apply_{i}"] = {"type": "noul", "instructions": apply_filter_instructions(i, text)}
        qs[f"more_{i}"] = {"type": "noul", "instructions": browse_more_instructions(i, text)}
    return qs


def control_state(profile: dict, query: str, url: str, controls: list[dict]) -> dict:
    return {
        "candidate_profile": profile,
        "hunt_query": query or "",
        "url": url,
        "controls": [{"i": i, "text": c.get("text") or ""} for i, c in enumerate(controls[:MAX_CONTROLS_TO_SCORE])],
    }


def mock_noul_apply_filter(text: str, query: str) -> float:
    t, q = (text or "").lower(), (query or "").lower()
    if not t:
        return 0.05
    q_parts = [p for p in re.split(r"[/,&|]+", q) if len(p.strip()) >= 4]
    if q_parts and any(p.strip() in t for p in q_parts):
        return _j(t + "|afq", 0.93, 0.03)
    return _tbl(t, [
        (("engineer", "engineering", "research", "software", "technical", "ai", "ml", "platform"), 0.78),
        (("sales", "account", "g&a", "communications", "privacy", "cookie", "language", "english"), 0.08),
    ], 0.22, "|af")


def mock_noul_browse_more(text: str) -> float:
    return _tbl(text or "", [
        (("all teams", "all departments", "department", "team", "filter", "next", "load more", "show more", "see more"), 0.88),
        (("log in", "login", "cookie", "privacy", "language", "english"), 0.06),
    ], 0.18, "|more")


def _mock_answer(questions: dict, state) -> dict[str, Ans]:
    st = state if isinstance(state, dict) else {}
    links, url, title = st.get("links") or [], st.get("url") or "", st.get("title") or ""
    answers: dict[str, Ans] = {}
    if "page_kind" in questions:
        answers["page_kind"] = _choice(mock_page_kind(url, title, links), PAGE_KINDS, url + title)
    if "overall_fit" in questions:
        answers.update({k: Ans(type="noul", noul=v) for k, v in mock_detail(st).items()})
    for i, link in enumerate(links):
        t, h = link.get("text") or "", link.get("href") or ""
        vals = {f"careers_{i}": mock_noul_careers(t, h), f"job_{i}": mock_noul_job_posting(t, h),
                f"sw_{i}": mock_noul_software(t), f"ai_{i}": mock_noul_ai(t)}
        answers.update({qid: Ans(type="noul", noul=val) for qid, val in vals.items() if qid in questions})
    for job in st.get("jobs") or []:
        i, t = int(job.get("i", 0)), job.get("title") or job.get("text") or ""
        vals = {f"sw_{i}": mock_noul_software(t), f"ai_{i}": mock_noul_ai(t)}
        answers.update({qid: Ans(type="noul", noul=val) for qid, val in vals.items() if qid in questions})
    query = str(st.get("hunt_query") or "")
    for c in st.get("controls") or []:
        i, t = int(c.get("i", 0)), c.get("text") or ""
        vals = {f"apply_{i}": mock_noul_apply_filter(t, query), f"more_{i}": mock_noul_browse_more(t)}
        answers.update({qid: Ans(type="noul", noul=val) for qid, val in vals.items() if qid in questions})
    for qid, spec in questions.items():
        if qid not in answers:
            crit = list(spec.get("criteria") or ["other"])
            answers[qid] = Ans(type="noul", noul=0.5) if spec.get("type") == "noul" else _choice(crit[0], crit, qid)
    return answers


def _sdk_questions(questions: dict):
    from typesafe_sdk import Choice, Noul
    out = {}
    for k, q in questions.items():
        out[k] = Noul(instructions=q["instructions"]) if q["type"] == "noul" else Choice(
            instructions=q["instructions"], criteria=q["criteria"])
    return out


def _from_obj(response) -> dict[str, Ans]:
    items = (response.get("answers") or response).items() if isinstance(response, dict) else (getattr(response, "answers", {}) or {}).items()
    out = {}
    for k, a in items:
        if isinstance(a, dict):
            out[k] = (Ans(type="noul", noul=float(a.get("noul") or 0)) if (a.get("type") == "noul" or "noul" in a)
                      else Ans(type="choice", choice=str(a.get("choice") or ""),
                               probabilities=dict(a.get("probabilities") or {}),
                               confidence=float(a.get("confidence") or 0)))
        elif hasattr(a, "choice"):
            out[k] = Ans(type="choice", choice=str(a.choice), probabilities=dict(getattr(a, "probabilities", {}) or {}),
                         confidence=float(getattr(a, "confidence", 0) or 0))
        else:
            out[k] = Ans(type="noul", noul=float(getattr(a, "noul", 0) or 0))
    return out


def _http_call(state, questions: dict) -> dict:
    body = {"model": "jev-latest", "state": state, "questions": questions}
    headers = {"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}", "Content-Type": "application/json"}
    try:
        import httpx
        r = httpx.post(API_URL, headers=headers, json=body, timeout=60.0)
        r.raise_for_status()
        return r.json()
    except ImportError:
        import urllib.request
        req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())


def ask(state, questions: dict, mock: bool) -> JevResult:
    blob = json.dumps({"state": state, "q": sorted(questions)}, default=str, sort_keys=True)
    if mock:
        return JevResult(answers=_mock_answer(questions, state), latency_ms=_lat(blob))
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        raise SystemExit("ERROR: TYPESAFE_API_KEY is not set. Add it to .env or rerun with --mock.")
    t0 = time.perf_counter()
    try:
        from typesafe_sdk import TypeSafeClient
        answers = _from_obj(TypeSafeClient().system_one(state=state, questions=_sdk_questions(questions)))
    except ImportError:
        answers = _from_obj(_http_call(state, questions))
    return JevResult(answers=answers, latency_ms=int((time.perf_counter() - t0) * 1000))


def step_state(company: dict, url: str, title: str | None, links: list[dict], recent: list[str]) -> dict:
    scored = links[:MAX_LINKS_TO_SCORE]
    return {"company": {"id": company.get("id"), "name": company.get("name")}, "url": url, "title": title,
            "recent_actions": recent[-6:],
            "links": [{"i": i, "text": L["text"], "href": L.get("href") or ""} for i, L in enumerate(scored)]}


def job_filter_state(company: dict, links: list[dict], indices: list[int]) -> dict:
    jobs = []
    for i in indices:
        L = links[i]
        jobs.append({"i": i, "title": L["text"], "href": L.get("href") or ""})
    return {"company": {"id": company.get("id"), "name": company.get("name")}, "jobs": jobs}


def score_page(company: dict, url: str, title: str | None, links: list[dict], recent: list[str], mock: bool):
    st = step_state(company, url, title, links, recent)
    nav = ask(st, build_nav_questions(st["links"]), mock=mock)
    kind = nav.answers["page_kind"].choice if nav.answers.get("page_kind") else ""
    hits = []
    for i, L in enumerate(st["links"]):
        a = nav.answers.get(f"job_{i}")
        noul = float(a.noul) if a is not None else 0.0
        if noul >= JOB_POSTING_THRESHOLD:
            hits.append((noul, i))
    hits.sort(key=lambda x: x[0], reverse=True)
    if kind == "job_list" or len(hits) >= MIN_JOB_LINKS:
        indices = [i for _, i in hits[:MAX_JOB_FILTER]]
        job = ask(job_filter_state(company, st["links"], indices), build_job_questions(indices, st["links"]), mock=mock)
        return nav.answers, job.answers, nav.latency_ms, job.latency_ms
    return nav.answers, None, nav.latency_ms, None


def detail_state(profile: dict, title: str, url: str, text: str) -> dict:
    return {"candidate_profile": profile, "job": {"title": title, "url": url, "text": text[:JOB_TEXT_LIMIT]}}
