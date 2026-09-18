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
