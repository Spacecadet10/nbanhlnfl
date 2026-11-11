// Headless Chrome check of the soccer Markets panel and markets history (DevTools protocol; real viewport metrics).
// node markets_browser_check.mjs <devtools port> <out dir>
import { writeFileSync } from 'node:fs';
const [port, out] = process.argv.slice(2);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function session() {
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener('open', r));
  let id = 0; const pending = new Map(); const events = [];
  ws.addEventListener('message', (m) => { const d = JSON.parse(m.data); if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } else if (d.method) events.push(d); });
  const send = (method, params = {}) => new Promise((resolve, reject) => { const n = ++id; pending.set(n, (d) => (d.error ? reject(new Error(method + ': ' + d.error.message)) : resolve(d.result))); ws.send(JSON.stringify({ id: n, method, params })); });
  return { send, events, close: () => ws.close(), targetId: target.id };
}
const BASE = 'http://127.0.0.1:4191';
const cases = [];
for (const width of [1280, 390]) {
  const h = width < 600 ? 844 : 900;
  cases.push({ name: 'gameday-esp1', url: `${BASE}/?sport=soccer&competition=esp.1`, width, height: h, open: true,
    expect: [/Markets: goals, corners, double chance, draw no bet/, /Athletic Club vs Espanyol/, /DraftKings/, /Model line/, /No proven skill/, /Bookmaker offer/, /Over 2\.5 goals/] });
  cases.push({ name: 'gameday-ger1', url: `${BASE}/?sport=soccer&competition=ger.1`, width, height: h, open: true,
    expect: [/Bayern Munich vs FC Cologne/, /Corners/, /Bayern Munich -4\.5 corners/, /Both teams to score: yes/, /Bayern Munich or draw/, /split stake/] });
  cases.push({ name: 'history-ger1', url: `${BASE}/history?sport=soccer&competition=ger.1`, width, height: h,
    expect: [/Goal and corner forecasts: live record/, /No settled live/] });
  cases.push({ name: 'gameday-eng1-api-down', url: `${BASE}/?sport=soccer&competition=eng.1`, width, height: h, block: ['*/api/soccer/markets*'],
    expect: [/Markets: goals, corners/] });
}
const results = [];
for (const c of cases) {
  const s = await session();
  await s.send('Page.enable'); await s.send('Network.enable'); await s.send('Runtime.enable');
  await s.send('Emulation.setDeviceMetricsOverride', { width: c.width, height: c.height, deviceScaleFactor: c.width < 600 ? 2 : 1, mobile: c.width < 600 });
  if (c.block) await s.send('Network.setBlockedURLs', { urls: c.block });
  await s.send('Page.navigate', { url: c.url });
  await sleep(4000);
  const evaluate = async (expression) => (await s.send('Runtime.evaluate', { expression, returnByValue: true })).result.value;
  if (c.open) { await evaluate(`document.querySelectorAll('.soccer-markets-match, .soccer-markets-ranking').forEach((d) => { d.open = true; })`); await sleep(600); }
  const facts = await evaluate(`(() => ({ innerWidth: window.innerWidth, scrollWidth: document.documentElement.scrollWidth, text: document.body.innerText,
    wide: [...document.querySelectorAll('body *')].filter((e) => e.getBoundingClientRect().right > window.innerWidth + 1 && getComputedStyle(e).position !== 'fixed' && e.getBoundingClientRect().width > 0 && !e.closest('.table-wrap')).slice(0, 5).map((e) => e.tagName + '.' + e.className) }))()`);
  const api = s.events.filter((e) => e.method === 'Network.responseReceived' && e.params.response.url.includes('/api/')).map((e) => [e.params.response.status, e.params.response.url.replace(/^https?:\/\/[^/]+/, '')]);
  const exceptions = s.events.filter((e) => e.method === 'Runtime.exceptionThrown').map((e) => (e.params.exceptionDetails.exception?.description ?? e.params.exceptionDetails.text).slice(0, 160));
  const shot = await s.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
  writeFileSync(`${out}/${c.name}-${c.width}.png`, Buffer.from(shot.data, 'base64'));
  const missing = c.expect.filter((re) => !re.test(facts.text)).map(String);
  const errorShown = /couldn.t load|could not|failed|error/i.test(facts.text.split('Markets: goals')[1]?.slice(0, 400) ?? '');
  results.push({ case: c.name, width: c.width, innerWidth: facts.innerWidth, scrollWidth: facts.scrollWidth, overflow: facts.scrollWidth > facts.innerWidth,
    wide: facts.wide, missing, exceptions, apiErrors: api.filter(([st]) => st >= 400), marketsApi: api.filter(([, u]) => u.includes('/soccer/markets')),
    blockedErrorShown: c.block ? errorShown : undefined });
  await fetch(`http://127.0.0.1:${port}/json/close/${s.targetId}`); s.close();
}
console.log(JSON.stringify(results, null, 1));
