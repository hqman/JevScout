# Jev AI Job Hunter

Demo MVP: a coding-agent **skill** that hunts AI/software jobs on real company sites.

**Chrome sees and acts. Jev decides.** The host CLI runs one command. `jjh` drives Chrome over CDP and calls [Jev](https://docs.typesafe.ai) (TypeSafe System One) to score every link and job. The host LLM never picks what to click.

<video src="demo/jev_job.mp4" controls playsinline preload="metadata" width="100%">
  <a href="demo/jev_job.mp4">Watch the demo (MP4)</a>
</video>

## Setup

```bash
cp .env.example .env   # then set TYPESAFE_API_KEY (optional for --mock)
uv sync
```

Visible Chrome (not headless). Chrome DevTools MCP is optional; `jjh run` talks to Chrome over CDP directly.

## One-command hunt

```bash
uv run jjh run --url https://openai.com --query "Applied AI Engineer, Codex"
uv run jjh run --url https://typesafe.ai --query "backend / AI engineer"
uv run jjh log
```

Any company homepage works. `--companies openai` is only a shortcut. Add `--mock` with no API key, `--max-pages N`, `--tab reuse`. Tests: `uv run pytest -q`.

## Slow-motion (fixtures / narrated demos)

```bash
uv run jjh start --mock
uv run jjh step --page tests/fixtures/openai_home.page.json --mock
uv run jjh step --page tests/fixtures/openai_careers.page.json --mock
uv run jjh step --page tests/fixtures/openai_job.page.json --mock
uv run jjh report
```

If CDP is down: `evaluate_script` (no `filePath`) then `uv run jjh step --page - <<'EOF' ... EOF`.

See [DEMO.md](DEMO.md).
