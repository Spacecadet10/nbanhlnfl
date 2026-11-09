// Real-viewport check of the soccer screens through headless Chrome (DevTools protocol; no extension, no resize limits).
// node browser_check.mjs <devtools port> <out dir>
import { writeFileSync } from 'node:fs';
const [port, out] = process.argv.slice(2);
const FORBIDDEN = /Soccer predictions are not available|no validated model exists|Not refreshed automatically|No live soccer forecast, odds|No soccer model exists/i;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function session() {
  const target = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' })).json();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener('open', r));
  let id = 0; const pending = new Map(); const events = [];
  ws.addEventListener('message', (m) => {
    const d = JSON.parse(m.data);
    if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); } else if (d.method) events.push(d);
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const n = ++id; pending.set(n, (d) => (d.error ? reject(new Error(method + ': ' + d.error.message)) : resolve(d.result)));
    ws.send(JSON.stringify({ id: n, method, params }));
  });
  return { send, events, close: () => ws.close(), targetId: target.id };
}

async function check(c) {
  const s = await session();
  await s.send('Page.enable'); await s.send('Network.enable'); await s.send('Runtime.enable');
  await s.send('Emulation.setDeviceMetricsOverride', { width: c.width, height: c.height, deviceScaleFactor: c.width < 600 ? 2 : 1, mobile: c.width < 600 });
  if (c.block) await s.send('Network.setBlockedURLs', { urls: c.block });
  await s.send('Page.navigate', { url: c.url });
  await sleep(c.wait ?? 3500);
  const evaluate = async (expression) => (await s.send('Runtime.evaluate', { expression, returnByValue: true })).result.value;
  const facts = await evaluate(`(() => ({ innerWidth: window.innerWidth, scrollWidth: document.documentElement.scrollWidth,
    visible: document.body.innerText, all: document.body.textContent,
    wide: [...document.querySelectorAll('body *')].filter((e) => e.getBoundingClientRect().right > window.innerWidth + 1 && getComputedStyle(e).position !== 'fixed' && e.getBoundingClientRect().width > 0).slice(0, 5).map((e) => e.tagName + '.' + e.className) }))()`);
  const api = s.events.filter((e) => e.method === 'Network.responseReceived' && e.params.response.url.includes('/api/'))
    .map((e) => [e.params.response.status, e.params.response.url.replace(/^https?:\/\/[^/]+/, '')]);
  const failedApi = api.filter(([status]) => status >= 400);
  const shot = await s.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  writeFileSync(`${out}/${c.name}-${c.width}.png`, Buffer.from(shot.data, 'base64'));
  await fetch(`http://127.0.0.1:${port}/json/close/${s.targetId}`); s.close();
  const forbidden = facts.all.match(FORBIDDEN);
  const missing = (c.expect ?? []).filter((re) => !re.test(c.hidden ? facts.all : facts.visible)).map(String);
  const overflow = facts.scrollWidth > facts.innerWidth;
  const ok = !forbidden && !missing.length && !overflow && facts.innerWidth === c.width && (c.allowApiFailures || !failedApi.length);
  return { name: c.name, width: c.width, viewport: facts.innerWidth, scrollWidth: facts.scrollWidth, overflow, wide: overflow ? facts.wide : [],
    forbidden: forbidden ? forbidden[0] : null, missing, apiCalls: api.length, failedApi, ok };
}

const web = 'http://127.0.0.1:4191', stale = 'http://127.0.0.1:4192';
const cases = [
  { name: 'recorded-uefa-nations', url: `${web}/?sport=soccer&competition=uefa.nations`,
    expect: [/Live soccer forecasts:/, /Upcoming forecasts/, /90-minute forecast: .* win \d+\.\d% · Draw \d+\.\d% · .* win \d+\.\d%\./, /The live soccer job last checked this competition/] },
  { name: 'awaiting-premier-league-played-day', url: `${web}/?sport=soccer&competition=eng.1`,
    expect: [/No Premier League forecast is recorded yet:/, /No live Premier League forecast for the coming matches yet/, /No forecast was recorded for this match\./] },
  { name: 'recorded-day-all-awaiting-nations-nov15', url: `${web}/?sport=soccer&competition=uefa.nations&date=2026-11-15`,
    expect: [/Live soccer forecasts:/, /Awaiting forecast: recorded before kickoff/] },
  { name: 'mixed-day-friendlies-nov14', url: `${web}/?sport=soccer&competition=fifa.friendly&date=2026-11-14`,
    expect: [/90-minute forecast: .* win \d+\.\d% · Draw/, /Awaiting forecast: recorded before kickoff/] },
  { name: 'out-of-scope-europa', url: `${web}/?sport=soccer&competition=uefa.europa`,
    expect: [/No live forecast for UEFA Europa League:/, /did not pass its held-out test/, /outside the live model’s tested scope/] },
  { name: 'empty-competition-euro-qualifying', url: `${web}/?sport=soccer&competition=uefa.euroq`,
    expect: [/matches are stored: ESPN listed none on the \d+ days requested so far/] },
  { name: 'source-stale-ligue1', url: `${stale}/?sport=soccer&competition=fra.1`,
    expect: [/ESPN data for .* is out of date\./, /more than 26 h ago/, /more than 26 h old, so new forecasts are refused/] },
  { name: 'source-failure-live-read-blocked', url: `${web}/?sport=soccer&competition=uefa.nations`, block: ['*/api/soccer/live*'], allowApiFailures: true,
    expect: [/Live forecasts could not be loaded/, /Fixtures and results below are not affected/, /Latest UEFA Nations League scoreboard request\./] },
  { name: 'match-with-forecast', url: `${web}/games/${encodeURIComponent('soccer:uefa.nations:401861145')}?sport=soccer`,
    expect: [/Forecast/, /Home win/, /Match forecasts: available\./] },
  { name: 'match-without-forecast', url: `${web}/games/${encodeURIComponent('soccer:eng.1:401878759')}?sport=soccer`,
    expect: [/No forecast was recorded for this match\. No probability, pick, odds or accuracy is shown\./] },
  { name: 'performance-page', url: `${web}/performance?sport=soccer&competition=uefa.nations`,
    expect: [/Not available for soccer yet\./, /Not built for live soccer forecasts yet/] },
  { name: 'data-note-recorded', url: `${web}/?sport=soccer&competition=uefa.nations`, hidden: true,
    expect: [/Live forecasts cover the 90-minute result only\. No soccer odds or injury information is collected\./] },
];
const results = [];
for (const c of cases) for (const [width, height] of [[1280, 900], [390, 844]]) results.push(await check({ ...c, width, height }));
for (const r of results) console.log(JSON.stringify(r));
console.log(`SUMMARY ${results.filter((r) => r.ok).length}/${results.length} ok; failed: ${results.filter((r) => !r.ok).map((r) => r.name + '@' + r.width).join(', ') || 'none'}`);
process.exit(results.every((r) => r.ok) ? 0 : 1);
