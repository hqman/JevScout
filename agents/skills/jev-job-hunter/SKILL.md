---
name: jev-job-hunter
description: >-
  Finds AI and software-engineer jobs by driving Chrome DevTools MCP and letting
  Jev (TypeSafe System One) score every careers link and job title. Use when the
  user wants to find jobs, run the job hunter, browse careers pages, match AI
  engineer roles, or mentions Jev / TypeSafe job search. Chrome sees and acts;
  Jev decides — the host LLM never picks links.
---

# Jev AI Job Hunter

Host CLI skill. **Chrome sees and acts. Jev decides.** You never choose links, never judge fit, never invent URLs.

Run every command from the repo root with `uv run jjh ...`.

## Setup

1. If `.env` has no `TYPESAFE_API_KEY`, tell the user and use `--mock` **only if they agree**. Copy `.env.example` otherwise.
2. Use a **visible** Chrome window. `jjh run` talks to Chrome over CDP itself — do **not** call `list_pages` / `evaluate_script` / `navigate_page` in the primary path (a second debugger connection makes Chrome ask Allow again).
3. Follow ATS redirects (Greenhouse / Lever / Ashby / Workday). Cross-domain is expected. Never log in, apply, or fill forms.

## Loop

**Primary:** `uv run jjh run --url https://<company-homepage> --query "<role keywords>"`.

Same flow on every site: **homepage → Jev picks Careers/Jobs → listing (filter / scroll / next page) → job details → report**. Never invent `/careers`. `--query` is short titles/skills, never a full sentence. Stream the **entire** stdout. Catalog ids (`openai`, `typesafe`) are shortcuts, not a closed list.

`jjh` **scrolls each page to the bottom before scoring links** (footer Careers/Jobs often fail `checkVisibility` above the fold), then scores listing **filters** with Jev (from the query — no hardcoded team names) and pages by clicking next / **scrolling** until the list stops growing.

**Slow-motion:** `uv run jjh start --query "..."` then `uv run jjh step` → MCP `navigate_page` on the same tab. Never `filePath` / `take_snapshot`.

**Fallback** (stdout contains `Chrome CDP not reachable`): `evaluate_script` with the JS below (`uv run jjh js`) then `uv run jjh step --page -`.

```js
() => {
  const cur = location.href.replace(/#.*$/, "");
  const tidy = (s) => String(s || "").replace(/\s+/g, " ").trim();
  const links = [], seen = new Map();
  for (const a of document.querySelectorAll("a[href]")) {
    const raw = (a.getAttribute("href") || "").trim();
    if (!raw || raw[0] === "#" || /^(javascript:|mailto:|tel:)/i.test(raw)) continue;
    const href = String(a.href).replace(/#.*$/, "");
    if (!href || href === cur) continue;
    if (a.checkVisibility && !a.checkVisibility({checkOpacity: true})) continue;
    let text = tidy(a.innerText || "");
    text = text.replace(/\(opens in a new (window|tab)\)/gi, "").trim();
    if (!text) text = a.getAttribute("aria-label") || a.title || "";
    if (!text) {
      const last = (href.replace(/^[a-z]+:\/\/[^/]+/i, "").split(/[?#]/)[0] || "").split("/").filter(Boolean).pop() || "";
      try { text = decodeURIComponent(last).replace(/[-_]/g, " "); } catch { text = last.replace(/[-_]/g, " "); }
    }
    text = tidy(text).slice(0, 100);
    if (!text && !href) continue;
    if (seen.has(href)) {
      if (text && !seen.get(href)) { seen.set(href, text); const p = links.find(l => l.href === href); if (p) p.text = text; }
      continue;
    }
    seen.set(href, text);
    links.push({ text, href });
    if (links.length >= 2000) break;
  }
  const controls = [], cseen = new Set();
  for (const el of document.querySelectorAll('button, label, [role="option"], [role="menuitemcheckbox"], [role="menuitem"], [role="checkbox"], [role="combobox"]')) {
    let text = tidy(el.innerText || el.getAttribute("aria-label") || "");
    text = text.replace(/\(opens in a new (window|tab)\)/gi, "").trim().slice(0, 60);
    if (!text || text.length < 2 || cseen.has(text)) continue;
    cseen.add(text);
    controls.push({ text });
    if (controls.length >= 50) break;
  }
  const body = document.body;
  return {
    url: location.href, title: document.title, links, controls,
    text: ((body && body.innerText) || "").replace(/\n{3,}/g, "\n\n").slice(0, 6000),
    scroll: { y: window.scrollY, h: body ? body.scrollHeight : 0, inner: window.innerHeight }
  };
}
```

## Hard rules

- You do **not** pick Careers, filters, or job titles. `jjh` / Jev already scored them.
- You do **not** invent careers URLs or filter names.
- Never click Apply, never log in, never fill forms.
- Cookie banner: the **one** allowed host decision, only if `jjh` cannot proceed.
