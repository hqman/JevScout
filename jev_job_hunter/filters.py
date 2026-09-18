"""Generic listing-filter / pagination picks from Jev noul answers. No company names."""

from __future__ import annotations

from jev_job_hunter.questions import FILTER_CLICK_THRESHOLD, MAX_FILTER_CLICKS, MORE_CLICK_THRESHOLD


def _n(answers: dict, key: str) -> float:
    a = answers.get(key)
    if a is None:
        return 0.0
    return float(getattr(a, "noul", 0) or 0)


def pick_filter_clicks(controls: list[dict], answers: dict) -> list[str]:
    ranked = []
    for i, c in enumerate(controls):
        text = str(c.get("text") or "").strip()
        if not text:
            continue
        noul = _n(answers, f"apply_{i}")
        if noul >= FILTER_CLICK_THRESHOLD:
            ranked.append((noul, i, text))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    out, seen = [], set()
    for _, _, text in ranked:
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= MAX_FILTER_CLICKS:
            break
    return out


def pick_more_click(controls: list[dict], answers: dict, already: set[str] | None = None) -> str | None:
    already = already or set()
    best_n, best = 0.0, None
    for i, c in enumerate(controls):
        text = str(c.get("text") or "").strip()
        if not text or text in already:
            continue
        noul = _n(answers, f"more_{i}")
        if noul >= MORE_CLICK_THRESHOLD and noul > best_n:
            best_n, best = noul, text
    return best
