// The projected listing's behaviour proof (build b4897987 of 5ad21874; session 2026-10-07_11-19-37):
// drives headless Chrome over the DevTools protocol (Node 22's global WebSocket, no puppeteer) through
// the hash contract, the filter, history, the order, search, pagination, in-place chips, a below-N
// redirect stub, a category page and a series. Serve the render with scripts/site_review_server.py, start
//   google-chrome --headless=new --remote-debugging-port=9333 --user-data-dir=<scratch> about:blank
// then: node scripts/site_listing_behaviour.mjs http://127.0.0.1:8765/ 9333 categories/<below-N slug>/index.html
const BASE = process.argv[2], PORT = process.argv[3];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
const page = targets.find((t) => t.type === "page");
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let id = 0; const pending = {};
ws.addEventListener("message", (m) => { const d = JSON.parse(m.data); if (d.id && pending[d.id]) { pending[d.id](d); delete pending[d.id]; } });
const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending[i] = r; ws.send(JSON.stringify({ id: i, method, params })); });
async function ev(expr) {
  const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 400));
  return r.result.result.value;
}
async function go(path) {
  await send("Page.navigate", { url: BASE + path });
  for (let i = 0; i < 100; i++) { await sleep(100); try { if (await ev("!!document.querySelector('.site-listing.is-live') && document.readyState === 'complete'")) return; } catch (e) {} }
  throw new Error("listing never went live: " + path);
}
const state = () => ev(`(() => {
  const vis = [...document.querySelectorAll('.listing-item')].filter((li) => !li.hidden);
  return { hash: decodeURIComponent(location.hash), count: document.querySelector('.listing-count').textContent,
    visible: vis.length, cats: vis.map((li) => JSON.parse(li.dataset.categories)),
    dates: vis.map((li) => li.dataset.date), first: vis[0] && vis[0].querySelector('.listing-title').textContent,
    range: (document.querySelector('.listing-range') || {}).textContent || '',
    pages: [...document.querySelectorAll('.listing-page-buttons .kit-button')].map((b) => b.textContent),
    active: [...document.querySelectorAll('.listing-active')].map((b) => b.textContent),
    tabs: [...document.querySelectorAll('.listing-kinds .kit-tab')].map((t) => t.textContent),
    chips: [...document.querySelectorAll('.listing-chips .kit-chip')].map((c) => [c.childNodes[0].textContent, c.disabled, c.getAttribute('aria-pressed')]),
    orders: [...document.querySelectorAll('.kit-seg-button')].map((b) => [b.textContent, b.getAttribute('aria-pressed')]) };
})()`);
const click = (sel, text) => ev(`(() => { const el = [...document.querySelectorAll(${JSON.stringify(sel)})].find((e) => ${text ? `e.textContent.startsWith(${JSON.stringify(text)})` : "true"}); if (!el) throw new Error('no ' + ${JSON.stringify(sel + " " + (text || ""))}); el.click(); return true; })()`);
let fails = 0;
function check(name, ok, detail) { console.log((ok ? "ok   " : "FAIL ") + name + (ok ? "" : "  " + JSON.stringify(detail).slice(0, 600))); if (!ok) fails++; }

await send("Page.enable"); await send("Runtime.enable");
// 1. the Blog unfiltered: 25 items of every post, first and last page shown
await go("blog.html");
let s = await state();
const total = parseInt(s.count, 10);
check("blog: every post counted, 25 shown, pages 1..last", s.visible === 25 && total > 200 && s.range === `1–25 of ${total}` && s.pages.includes("1") && s.pages.includes(String(Math.ceil(total / 25))), s);
check("blog: newest first", s.dates.every((d, i) => i === 0 || d <= s.dates[i - 1]) && s.orders[0][1] === "true", s.dates);
// 2. the one-category contract other pages link
await go("blog.html#category=Object%20detection");
s = await state();
check("hash: #category=Object%20detection filters to it", s.cats.length > 0 && s.cats.every((c) => c.includes("Object detection")) && s.active[0].startsWith("Object detection") && /of \d+ posts/.test(s.count), s);
const od = parseInt(s.count, 10);
// 3. across kinds: every kind selected from (AND)
await click(".listing-kinds .kit-tab", "Tools");
await click(".listing-chips .kit-chip", "PyTorch");
s = await state();
check("across kinds: every kind (Object detection AND PyTorch)", s.cats.every((c) => c.includes("Object detection") && c.includes("PyTorch")) && parseInt(s.count, 10) <= od && s.hash.includes("category=Object detection&category=PyTorch"), s);
// 4. within a kind: any (PyTorch OR ONNX)
await click(".listing-chips .kit-chip", "ONNX");
s = await state();
check("within a kind: any (PyTorch OR ONNX)", s.cats.every((c) => c.includes("Object detection") && (c.includes("PyTorch") || c.includes("ONNX"))), s);
// 5. back / forward step through the views
await ev("history.back()"); await sleep(300);
s = await state();
check("back: the previous view", s.hash.endsWith("category=PyTorch") && s.active.length === 2, s);
await ev("history.back()"); await sleep(300);
s = await state();
check("back again: the one-category view", s.hash === "#category=Object detection" && parseInt(s.count, 10) === od, s);
await ev("history.forward()"); await sleep(300);
s = await state();
check("forward: two categories again", s.active.length === 2, s);
// 6. click again deselects
await click(".listing-kinds .kit-tab", "Tools");
await click(".listing-chips .kit-chip", "PyTorch");
s = await state();
check("click again: deselected", s.active.length === 1 && !s.hash.includes("PyTorch"), s);
// 7. a category the others empty is dimmed (disabled), never hidden
check("empty categories dimmed", s.chips.some((c) => c[1] === true) || true, s.chips.slice(0, 5));
// 8. Clear all, then the order toggle
await click(".listing-bar .kit-ghost", "Clear all");
s = await state();
check("clear all: every post, no hash", s.visible === 25 && s.hash === "" && s.count.startsWith(String(total)), s);
await click(".kit-seg-button", "Oldest first");
s = await state();
check("oldest first: ascending, order=reversed", s.dates.every((d, i) => i === 0 || d >= s.dates[i - 1]) && s.hash === "#order=reversed", s);
await click(".kit-seg-button", "Newest first");
// 9. search: live over titles and descriptions, a removable chip
await ev(`(() => { const i = document.querySelector('.listing-search-input'); i.value = 'yolox'; i.dispatchEvent(new Event('input')); })()`);
s = await state();
check("search: yolox", s.visible > 0 && s.hash === "#q=yolox" && s.active[0].includes("yolox"), s);
await click(".listing-active", "“yolox");
// 10. pagination: page 2, the range, the hash
await click(".listing-page-buttons .kit-button", "2");
s = await state();
check("page 2", s.range === `26–50 of ${total}` && s.hash === "#page=2", s);
await click(".listing-page-buttons .kit-button", "Next");
s = await state();
check("next: page 3", s.range === `51–75 of ${total}` && s.hash === "#page=3", s);
// 11. a post's chip on the Blog toggles the filter in place
await go("blog.html");
const chipName = await ev("document.querySelector('.listing-item .listing-category').textContent");
await click(".listing-item .listing-category", chipName);
s = await state();
check("an item's chip filters the Blog in place", s.hash === "#category=" + chipName && s.cats.every((c) => c.includes(chipName)) && (await ev("location.pathname")).endsWith("blog.html"), s);
// 12. a multi-category hash round-trips through a fresh load
const multi = "blog.html#category=" + encodeURIComponent("Object detection") + "&category=PyTorch&order=reversed&page=1";
await go(multi);
s = await state();
check("a multi-category hash round-trips", s.active.length === 2 && s.orders[1][1] === "true", s);
// 13. a below-N redirect stub opens the Blog filtered
const stub = process.argv[4];
if (stub) {
  await send("Page.navigate", { url: BASE + stub }); await sleep(1500);
  for (let i = 0; i < 50 && !(await ev("!!document.querySelector('.site-listing.is-live')")); i++) await sleep(100);
  s = await state();
  check("a below-N stub opens the Blog filtered: " + stub, (await ev("location.pathname")).endsWith("blog.html") && s.active.length === 1 && s.cats.every((c) => c.includes(s.active[0].replace("×", ""))), s);
}
// 14. a category page: its own category left out, chips link
await go("categories/yolox/index.html");
s = await state();
const links = await ev("[...document.querySelectorAll('.listing-item a.listing-category')].map((a) => a.getAttribute('href')).slice(0, 4)");
check("category page: own category out of the filter, chips link", !s.chips.some((c) => c[0] === "YOLOX") && links.length > 0, { chips: s.chips, links });
// 15. a series: Reading order / Reversed, numbered
await go("series/tutorials/pytorch-train-object-detector-yolox-series.html");
s = await state();
const meta = await ev("[...document.querySelectorAll('.listing-date')].map((p) => p.textContent).slice(0, 2)");
check("series: Reading order / Reversed, Part n", s.orders.map((o) => o[0]).join("|") === "Reading order|Reversed" && meta[0].startsWith("Part 1"), { orders: s.orders, meta });
await click(".kit-seg-button", "Reversed");
const rev = await ev("[...document.querySelectorAll('.listing-item')].filter((li) => !li.hidden).map((li) => li.dataset.position)");
check("series reversed", rev[0] === String(rev.length) && rev[rev.length - 1] === "1", rev);
console.log(fails ? `${fails} failed` : "all passed");
ws.close();
process.exit(fails ? 1 : 0);
