# Demo recording script

Split-screen: **left** Grok CLI (large), **right** visible Chrome. Terminal font large enough to read the probability board. Do **not** pass `--headless`.

## Prompt

```
Find me AI and software-engineer jobs using the Jev job hunter skill.
Start with TypeSafe. Chrome sees and acts; Jev decides.
```

If there is no `TYPESAFE_API_KEY`, say so when the agent asks; `--mock` only if they agree.

## Narration (one command)

1. **Setup (5s)** — “Chrome opens real career sites. Jev scores every link. The coding agent does not pick what to click.”
2. Run `uv run jjh run --companies typesafe`. Boards stream live: homepage nav → Ashby jobs → a few detail pages → report.
3. Pause on a PASS row and a detail `→ Save`. End on `ACTION END` and `results/latest.md`.

Slow-motion (`jjh start` / `jjh step` / MCP navigate) is only for a narrated step-by-step take.

## If a company is skipped

Login walls and captchas happen. Narrate: “Jev could not proceed — no invented careers URL.” One successful company + report is enough.
