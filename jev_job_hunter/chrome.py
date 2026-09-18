"""Connect to a running Chrome via CDP and extract / navigate pages."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

CDP_TIMEOUT = 10.0
READY_POLL_S = 0.15
READY_CAP_S = 6.0


class ChromeError(Exception):
    """CDP discovery, connect, or evaluate failed."""


def _home() -> Path:
    return Path.home()


def _active_port_candidates() -> list[Path]:
    home = _home()
    return [
        home / "Library/Application Support/Google/Chrome/DevToolsActivePort",
        home / "Library/Application Support/Google/Chrome Canary/DevToolsActivePort",
        home / "Library/Application Support/Google/Chrome for Testing/DevToolsActivePort",
        home / "Library/Application Support/Chromium/DevToolsActivePort",
        home / ".config/google-chrome/DevToolsActivePort",
        home / ".config/google-chrome-unstable/DevToolsActivePort",
        home / ".config/chromium/DevToolsActivePort",
    ]


def _read_active_port_file() -> tuple[int, str] | None:
    for path in _active_port_candidates():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if not lines:
            continue
        try:
            port = int(lines[0].strip())
        except ValueError:
            continue
        ws_path = lines[1].strip() if len(lines) > 1 else ""
        return port, ws_path
    return None


def find_devtools_port() -> int | None:
    env = (os.environ.get("JJH_CDP_PORT") or "").strip()
    if env:
        try:
            return int(env)
        except ValueError:
            return None
    found = _read_active_port_file()
    return found[0] if found else None


def _ws_from_path(port: int, ws_path: str) -> str:
    if ws_path.startswith("ws://") or ws_path.startswith("wss://"):
        return ws_path
    if not ws_path.startswith("/"):
        ws_path = "/" + ws_path
    return f"ws://127.0.0.1:{port}{ws_path}"


def browser_ws_url(port: int) -> str | None:
    found = _read_active_port_file()
    if not found:
        return None
    file_port, ws_path = found
    if file_port != port or not ws_path:
        return None
    return _ws_from_path(port, ws_path)


def resolve_browser_endpoint() -> tuple[int, str]:
    """Browser WS URL. Prefer DevToolsActivePort; /json only if env port and no file."""
    found = _read_active_port_file()
    if found:
        port, ws_path = found
        if not ws_path:
            raise ChromeError(f"DevToolsActivePort on {port} has no websocket path")
        return port, _ws_from_path(port, ws_path)
    env = (os.environ.get("JJH_CDP_PORT") or "").strip()
    if not env:
        raise ChromeError("DevToolsActivePort not found (set JJH_CDP_PORT)")
    try:
        port = int(env)
    except ValueError as e:
        raise ChromeError(f"invalid JJH_CDP_PORT={env!r}") from e
    ws = _browser_ws_from_json(port)
    if not ws:
        raise ChromeError(f"no CDP websocket on port {port}")
    return port, ws


def _browser_ws_from_json(port: int) -> str | None:
    for path in ("/json/version", "/json"):
        try:
            r = httpx.get(f"http://127.0.0.1:{port}{path}", timeout=3.0)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        try:
            data = r.json()
        except Exception:
            continue
        if isinstance(data, dict) and data.get("webSocketDebuggerUrl"):
            return str(data["webSocketDebuggerUrl"])
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("type") == "browser" and item.get("webSocketDebuggerUrl"):
                    return str(item["webSocketDebuggerUrl"])
            for item in data:
                if isinstance(item, dict) and item.get("webSocketDebuggerUrl"):
                    return str(item["webSocketDebuggerUrl"])
    return None


def _norm_url(url: str) -> tuple[str, str]:
    raw = (url or "").split("#", 1)[0].strip()
    if not raw:
        return "", ""
    p = urlsplit(raw)
    host = (p.netloc or "").lower()
    scheme = (p.scheme or "https").lower()
    origin = f"{scheme}://{host}" if host else ""
    path = (p.path or "").rstrip("/")
    return origin, path


def urls_equal(a: str, b: str) -> bool:
    return _norm_url(a) == _norm_url(b) and bool(_norm_url(a)[0])


def url_has_prefix(expected: str, page: str) -> bool:
    eo, ep = _norm_url(expected)
    po, pp = _norm_url(page)
    if not eo or eo != po:
        return False
    if not ep:
        return True
    return pp == ep or pp.startswith(ep + "/")


def _tab_pages(pages: list[dict]) -> list[dict]:
    tabs = [p for p in pages if (p.get("type") or "page") == "page"]
    return tabs if tabs else list(pages)


def pick_page(pages: list[dict], expected_url: str | None) -> dict:
    tabs = _tab_pages(pages)
    expected = (expected_url or "").strip()
    if expected:
        for p in tabs:
            if urls_equal(expected, str(p.get("url") or "")):
                return p
        for p in tabs:
            if url_has_prefix(expected, str(p.get("url") or "")):
                return p
    urls = [str(p.get("url") or "") for p in tabs]
    listed = ", ".join(urls[:40]) if urls else "(none)"
    want = expected or "(none)"
    raise ChromeError(f"no tab matches {want}. Candidates: {listed}")


def page_is_ready(samples: list[tuple[str, int]]) -> bool:
    """Ready when last readyState != loading, count > 0, and count unchanged for 2 polls."""
    if len(samples) < 2:
        return False
    _prev_state, prev_count = samples[-2]
    state, count = samples[-1]
    return state != "loading" and count > 0 and count == prev_count


def _connect(ws_url: str, timeout: float = CDP_TIMEOUT):
    try:
        from websockets.sync.client import connect
    except ImportError as e:
        raise ChromeError("websockets package is not installed") from e
    try:
        return connect(ws_url, open_timeout=timeout, close_timeout=2, max_size=None)
    except Exception as e:
        raise ChromeError(f"websocket connect failed ({ws_url}): {e}") from e


class _Cdp:
    def __init__(self, ws, timeout: float = CDP_TIMEOUT):
        self.ws = ws
        self.timeout = timeout
        self._id = 0

    def call(self, method: str, params: dict | None = None, session_id: str | None = None,
             timeout: float | None = None) -> dict:
        self._id += 1
        mid = self._id
        payload: dict = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        self.ws.send(json.dumps(payload))
        limit = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ChromeError(f"CDP timeout waiting for {method}")
            try:
                raw = self.ws.recv(timeout=remaining)
            except Exception as e:
                raise ChromeError(f"CDP recv failed for {method}: {e}") from e
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("id") != mid:
                continue
            if msg.get("error"):
                err = msg["error"]
                text = err.get("message") if isinstance(err, dict) else str(err)
                raise ChromeError(f"{method}: {text}")
            return msg.get("result") or {}


def _evaluate_value(result: dict) -> dict:
    if result.get("exceptionDetails"):
        det = result["exceptionDetails"]
        text = det.get("text") or det.get("exception", {}).get("description") or det
        raise ChromeError(f"Runtime.evaluate exception: {text}")
    inner = result.get("result") or {}
    if "value" not in inner:
        raise ChromeError(f"Runtime.evaluate returned no value ({inner.get('description') or inner.get('type')})")
    value = inner["value"]
    if not isinstance(value, dict):
        raise ChromeError("extract.js did not return an object")
    return value


def _page_id_from_ws(ws_url: str) -> str | None:
    marker = "/devtools/page/"
    if marker not in ws_url:
        return None
    return ws_url.split(marker, 1)[1].split("?", 1)[0].strip() or None


def _port_from_ws(ws_url: str) -> int | None:
    try:
        return urlsplit(ws_url).port
    except Exception:
        return None


def _is_browser_ws(ws_url: str) -> bool:
    return "/devtools/browser/" in (ws_url or "")


def _attach(cdp: _Cdp, target_id: str) -> str:
    result = cdp.call("Target.attachToTarget", {"targetId": target_id, "flatten": True})
    session_id = result.get("sessionId")
    if not session_id:
        raise ChromeError(f"attachToTarget returned no sessionId for {target_id}")
    return session_id


def _eval_on(cdp: _Cdp, js_source: str, session_id: str | None = None) -> dict:
    expression = "(" + js_source + ")()"
    result = cdp.call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
        session_id=session_id,
    )
    return _evaluate_value(result)


def _list_pages_http(port: int) -> list[dict] | None:
    url = f"http://127.0.0.1:{port}/json"
    try:
        r = httpx.get(url, timeout=3.0)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    return data if isinstance(data, list) else None


def _json_like(target: dict, port: int) -> dict:
    tid = str(target.get("targetId") or "")
    return {
        "id": tid,
        "type": target.get("type") or "page",
        "title": target.get("title") or "",
        "url": target.get("url") or "",
        "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/{tid}",
        "targetId": tid,
    }


def _list_pages_cdp(port: int) -> list[dict]:
    ws_url = browser_ws_url(port)
    if not ws_url:
        raise ChromeError(
            f"no browser websocket for port {port} (DevToolsActivePort missing path)"
        )
    with _connect(ws_url) as ws:
        cdp = _Cdp(ws)
        result = cdp.call("Target.getTargets")
    infos = result.get("targetInfos") or []
    return [_json_like(t, port) for t in infos if isinstance(t, dict)]


def list_pages(port: int) -> list[dict]:
    found = _read_active_port_file()
    if found and found[0] == port:
        return _list_pages_cdp(port)
    pages = _list_pages_http(port)
    if pages is not None:
        return pages
    return _list_pages_cdp(port)


def load_extract_js(root: Path | None = None) -> str:
    path = Path(__file__).with_name("extract.js")
    if path.is_file():
        return path.read_text(encoding="utf-8")
    if root is not None:
        alt = root / "jev_job_hunter" / "extract.js"
        if alt.is_file():
            return alt.read_text(encoding="utf-8")
    raise ChromeError("extract.js not found")


class BrowserSession:
    """One browser websocket for a whole hunt. Create/reuse a tab and keep it attached."""

    def __init__(self, ws, cdp: _Cdp, port: int):
        self.ws = ws
        self.cdp = cdp
        self.port = port
        self.target_id: str | None = None
        self.session_id: str | None = None

    @classmethod
    def open(cls) -> BrowserSession:
        port, ws_url = resolve_browser_endpoint()
        # One browser websocket. Retrying opens a second socket and Chrome
        # prompts Allow again.
        ws = _connect(ws_url, timeout=60.0)
        session = cls(ws, _Cdp(ws), port)
        try:
            session.cdp.call("Target.setDiscoverTargets", {"discover": True})
            session.cdp.call(
                "Target.setAutoAttach",
                {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True},
            )
        except ChromeError:
            pass
        return session

    def close(self) -> None:
        if self.session_id:
            try:
                self.cdp.call("Target.detachFromTarget", {"sessionId": self.session_id}, timeout=2.0)
            except Exception:
                pass
            self.session_id = None
        try:
            self.ws.close()
        except Exception:
            pass

    def list_pages(self) -> list[dict]:
        result = self.cdp.call("Target.getTargets")
        infos = result.get("targetInfos") or []
        return [_json_like(t, self.port) for t in infos if isinstance(t, dict)]

    def tab_index(self) -> int:
        if not self.target_id:
            return 1
        tabs = _tab_pages(self.list_pages())
        for i, p in enumerate(tabs, 1):
            if (p.get("id") or p.get("targetId")) == self.target_id:
                return i
        return 1

    def new_tab(self) -> str:
        result = self.cdp.call("Target.createTarget", {"url": "about:blank"})
        tid = str(result.get("targetId") or "")
        if not tid:
            raise ChromeError("Target.createTarget returned no targetId")
        try:
            self.cdp.call("Target.activateTarget", {"targetId": tid})
        except ChromeError:
            pass
        self.attach(tid)
        return tid

    def reuse_tab(self, expected: str | None) -> str:
        picked = pick_page(self.list_pages(), expected)
        tid = str(picked.get("id") or picked.get("targetId") or "")
        if not tid:
            raise ChromeError("matching tab has no target id")
        try:
            self.cdp.call("Target.activateTarget", {"targetId": tid})
        except ChromeError:
            pass
        self.attach(tid)
        return tid

    def attach(self, target_id: str) -> str:
        if self.session_id:
            try:
                self.cdp.call("Target.detachFromTarget", {"sessionId": self.session_id}, timeout=2.0)
            except ChromeError:
                pass
            self.session_id = None
        self.target_id = target_id
        self.session_id = _attach(self.cdp, target_id)
        self.cdp.call("Page.enable", session_id=self.session_id)
        return self.session_id

    def call_page(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        if not self.session_id or not self.target_id:
            raise ChromeError("no attached page session")
        try:
            return self.cdp.call(method, params, session_id=self.session_id, timeout=timeout)
        except ChromeError:
            self.attach(self.target_id)
            return self.cdp.call(method, params, session_id=self.session_id, timeout=timeout)

    def navigate(self, url: str) -> int:
        """Page.navigate + readiness poll. Returns nav_ms. Does not wait for load."""
        t0 = time.perf_counter()
        if self.target_id:
            try:
                self.cdp.call("Target.activateTarget", {"targetId": self.target_id})
            except ChromeError:
                pass
        self.call_page("Page.navigate", {"url": url})
        self.wait_ready()
        return int((time.perf_counter() - t0) * 1000)

    def wait_ready(self, cap: float = READY_CAP_S, interval: float = READY_POLL_S) -> list[tuple[str, int]]:
        samples: list[tuple[str, int]] = []
        deadline = time.monotonic() + cap
        while time.monotonic() < deadline:
            try:
                result = self.call_page(
                    "Runtime.evaluate",
                    {
                        "expression": "document.readyState + '|' + document.querySelectorAll('a[href]').length",
                        "returnByValue": True,
                    },
                    timeout=2.0,
                )
            except ChromeError:
                time.sleep(interval)
                continue
            inner = (result.get("result") or {}).get("value")
            if isinstance(inner, str) and "|" in inner:
                state, _, rest = inner.partition("|")
                try:
                    count = int(rest)
                except ValueError:
                    count = 0
                samples.append((state, count))
                if page_is_ready(samples):
                    return samples
            time.sleep(interval)
        return samples

    def extract(self, js_source: str, need_text: bool = True) -> dict:
        expression = "(" + js_source + ")()"
        if not need_text:
            expression = (
                "((fn)=>{const r=fn(); if(r && typeof r==='object') r.text=''; return r;})("
                + js_source + ")"
            )
        result = self.call_page(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        return _evaluate_value(result)

    def eval_value(self, expression: str, timeout: float | None = None):
        result = self.call_page(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            det = result["exceptionDetails"]
            text = det.get("text") or det.get("exception", {}).get("description") or det
            raise ChromeError(f"Runtime.evaluate exception: {text}")
        return (result.get("result") or {}).get("value")

    def click_text(self, text: str, stay_url: str | None = None) -> bool:
        want = json.dumps(text)
        stay = json.dumps(stay_url or "")
        expression = (
            "((want,stay)=>{const n=s=>(s||'').replace(/\\s+/g,' ').trim();"
            "const board=/(career|job|opening|position|hiring|join-?us|search)/i;"
            "const sameBoard=(href)=>{if(!href) return true; try{"
            "const a=new URL(href,location.href); const b=new URL(stay||location.href);"
            "const ap=(a.pathname||'/').replace(/\\/$/,'')||'/';"
            "const bp=(b.pathname||'/').replace(/\\/$/,'')||'/';"
            "if(ap===bp) return true;"
            "if(board.test(a.pathname)||board.test(a.hostname)) return true;"
            "return false;}catch(e){return false;}};"
            "const nodes=[...document.querySelectorAll("
            "'button,label,[role=option],[role=menuitemcheckbox],[role=menuitem],"
            "[role=checkbox],[role=combobox],a')];"
            "const match=nodes.filter(e=>n(e.innerText)===want||n(e.getAttribute('aria-label'))===want);"
            "const el=match.find(e=>e.tagName!=='A')||match.find(e=>sameBoard(e.href));"
            "if(!el) return false; el.click(); return true;})(" + want + "," + stay + ")"
        )
        try:
            return bool(self.eval_value(expression))
        except ChromeError:
            return False

    def type_into_focused(self, text: str) -> bool:
        q = json.dumps(text)
        expression = (
            "((q)=>{const el=document.activeElement; if(!el) return false;"
            "const tag=(el.tagName||'').toLowerCase();"
            "const ok=tag==='input'||tag==='textarea'||!!el.isContentEditable;"
            "if(!ok) return false; el.focus();"
            "const desc=Object.getOwnPropertyDescriptor(el.constructor.prototype,'value');"
            "if(desc&&desc.set) desc.set.call(el,q); else if('value' in el) el.value=q;"
            "else el.textContent=q;"
            "el.dispatchEvent(new Event('input',{bubbles:true}));"
            "el.dispatchEvent(new Event('change',{bubbles:true}));"
            "el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));"
            "el.dispatchEvent(new KeyboardEvent('keyup',{key:'Enter',bubbles:true}));"
            "return true;})(" + q + ")"
        )
        try:
            return bool(self.eval_value(expression))
        except ChromeError:
            return False

    def scroll_down(self) -> dict:
        expression = (
            "(()=>{const b=document.body; const before=b?b.scrollHeight:0;"
            "window.scrollBy(0, Math.max(window.innerHeight*0.9, 400));"
            "return {y:window.scrollY,h:b?b.scrollHeight:0,inner:window.innerHeight,before};})()"
        )
        got = self.eval_value(expression)
        return got if isinstance(got, dict) else {}

    def scroll_to_bottom(self) -> dict:
        expression = (
            "(()=>{const b=document.body;"
            "const h=Math.max((b&&b.scrollHeight)||0,document.documentElement.scrollHeight||0);"
            "window.scrollTo(0,h);"
            "return {y:window.scrollY,h:b?b.scrollHeight:0,inner:window.innerHeight};})()"
        )
        got = self.eval_value(expression)
        return got if isinstance(got, dict) else {}


def extract_from_chrome(expected_url: str | None, js_source: str) -> tuple[dict, dict, int]:
    """List tabs, pick one, extract — one CDP connection. Returns (page, tab, 1-based index)."""
    session = BrowserSession.open()
    try:
        picked = pick_page(session.list_pages(), expected_url)
        tid = str(picked.get("id") or picked.get("targetId") or "")
        if not tid:
            raise ChromeError("matching tab has no target id")
        session.attach(tid)
        data = session.extract(js_source, need_text=True)
        return data, picked, session.tab_index()
    finally:
        session.close()


def extract_page(ws_url: str, js_source: str) -> dict:
    """Run extract.js on a page websocket. Falls back to browser attach on M144+."""
    if _is_browser_ws(ws_url):
        raise ChromeError("extract_page needs a page websocket, not the browser endpoint")
    try:
        with _connect(ws_url, timeout=min(3.0, CDP_TIMEOUT)) as ws:
            return _eval_on(_Cdp(ws, timeout=CDP_TIMEOUT), js_source)
    except ChromeError:
        target_id = _page_id_from_ws(ws_url)
        port = _port_from_ws(ws_url) or find_devtools_port()
        if not target_id or not port:
            raise
        browser = browser_ws_url(port)
        if not browser:
            raise
        with _connect(browser) as ws:
            cdp = _Cdp(ws)
            session_id = _attach(cdp, target_id)
            return _eval_on(cdp, js_source, session_id=session_id)


def navigate(ws_url: str, url: str) -> None:
    """Page.navigate. Host MCP still navigates in slow-motion mode."""
    def _nav(cdp: _Cdp, session_id: str | None = None) -> None:
        cdp.call("Page.navigate", {"url": url}, session_id=session_id)

    try:
        with _connect(ws_url, timeout=min(3.0, CDP_TIMEOUT)) as ws:
            _nav(_Cdp(ws, timeout=CDP_TIMEOUT))
            return
    except ChromeError:
        target_id = _page_id_from_ws(ws_url)
        port = _port_from_ws(ws_url) or find_devtools_port()
        if not target_id or not port:
            raise
        browser = browser_ws_url(port)
        if not browser:
            raise
        with _connect(browser) as ws:
            cdp = _Cdp(ws)
            session_id = _attach(cdp, target_id)
            _nav(cdp, session_id)
