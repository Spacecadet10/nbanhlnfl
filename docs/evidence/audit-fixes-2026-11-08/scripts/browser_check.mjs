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
  if (c.daysAhead) await s.send('Page.addScriptToEvaluateOnNewDocument', { source: `(() => { const now = Date.now; Date.now = () => now() + ${c.daysAhead} * 864e5; })()` });
  // Set the theme for every case: the browser profile keeps the last choice between cases and runs.
  await s.send('Page.addScriptToEvaluateOnNewDocument', { source: `try { localStorage.setItem('courtside-theme-choice', '${c.theme === 'dark' ? 'dark' : 'light'}'); } catch (e) {}` });
  await s.send('Page.navigate', { url: c.url });
  await sleep(c.wait ?? 3500);
  const evaluate = async (expression) => (await s.send('Runtime.evaluate', { expression, returnByValue: true })).result.value;
  const facts = await evaluate(`(() => ({ innerWidth: window.innerWidth, scrollWidth: document.documentElement.scrollWidth,
    visible: document.body.innerText, all: document.body.textContent,
    wide: [...document.querySelectorAll('body *')].filter((e) => e.getBoundingClientRect().right > window.innerWidth + 1 && getComputedStyle(e).position !== 'fixed' && e.getBoundingClientRect().width > 0).slice(0, 5).map((e) => e.tagName + '.' + e.className) }))()`);
  // Readability of the live forecast card: WCAG contrast of each text against the first opaque background behind it.
  const contrast = c.contrast ? await evaluate(`(() => {
    const rgb = (v) => (v.match(/[\\d.]+/g) || []).map(Number);
    const lum = ([r, g, b]) => { const f = (x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
    const behind = (e) => { for (let n = e; n; n = n.parentElement) { const c = rgb(getComputedStyle(n).backgroundColor); if (c.length === 3 || (c.length === 4 && c[3] >= 0.99)) return c.slice(0, 3); } return [255, 255, 255]; };
    const out = { theme: document.documentElement.dataset.theme, cards: document.querySelectorAll('.soccer-live-card').length, min: null, worst: null };
    for (const e of document.querySelectorAll('.soccer-live-card header strong, .soccer-live-prob strong, .soccer-live-prob span')) {
      const a = lum(rgb(getComputedStyle(e).color).slice(0, 3)), b = lum(behind(e));
      const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      if (out.min === null || ratio < out.min) { out.min = Math.round(ratio * 100) / 100; out.worst = e.tagName + ':' + e.textContent.slice(0, 20); }
    }
    return out; })()`) : null;
  const api = s.events.filter((e) => e.method === 'Network.responseReceived' && e.params.response.url.includes('/api/'))
    .map((e) => [e.params.response.status, e.params.response.url.replace(/^https?:\/\/[^/]+/, '')]);
  const failedApi = api.filter(([status]) => status >= 400);
  const shot = await s.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  writeFileSync(`${out}/${c.name}${c.theme === 'dark' ? '-dark' : ''}-${c.width}.png`, Buffer.from(shot.data, 'base64'));
  await fetch(`http://127.0.0.1:${port}/json/close/${s.targetId}`); s.close();
  const exceptions = s.events.filter((e) => e.method === 'Runtime.exceptionThrown').map((e) => (e.params.exceptionDetails.exception?.description ?? e.params.exceptionDetails.text).slice(0, 160));
  const crashed = /could not be displayed/i.test(facts.visible);
  const forbidden = c.oldApi ? null : facts.all.match(FORBIDDEN);   // an older API still sends its old capability sentences
  const missing = (c.expect ?? []).filter((re) => !re.test(c.hidden ? facts.all : facts.visible)).map(String);
  const overflow = facts.scrollWidth > facts.innerWidth;
  const readable = !contrast || (contrast.cards > 0 && contrast.min >= 4.5 && contrast.theme === (c.theme ?? 'light'));
  const ok = !forbidden && !missing.length && !overflow && facts.innerWidth === c.width && (c.allowApiFailures || !failedApi.length) && readable && !exceptions.length && !crashed;
  return { name: c.name, theme: c.theme ?? 'light', contrast, width: c.width, viewport: facts.innerWidth, scrollWidth: facts.scrollWidth, overflow, wide: overflow ? facts.wide : [],
    forbidden: forbidden ? forbidden[0] : null, exceptions, crashed, missing, apiCalls: api.length, failedApi, ok };
}

const web = process.env.WEB ?? 'http://127.0.0.1:4191', stale = process.env.STALE ?? 'http://127.0.0.1:4192';
const oldApi = process.env.OLD_API_WEB;   // this frontend served against the API of the base commit (update in progress)
const cases = [
  { name: 'recorded-uefa-nations', url: `${web}/?sport=soccer&competition=uefa.nations`,
    expect: [/Live soccer forecasts:/, /Upcoming forecasts/, /90-minute forecast: .* win \d+\.\d% · Draw \d+\.\d% · .* win \d+\.\d%\./, /The live soccer job last checked this competition/] },
  { name: 'awaiting-premier-league-played-day', url: `${web}/?sport=soccer&competition=eng.1`,
    expect: [/No Premier League forecast is recorded yet:/, /No live Premier League forecast for the coming matches yet/, /No forecast was recorded for this match\./] },
  { name: 'recorded-day-all-awaiting-nations-nov15', url: `${web}/?sport=soccer&competition=uefa.nations&date=2026-11-15`,
    expect: [/Live soccer forecasts:/, /Awaiting forecast: recorded by .* at the latest/] },
  { name: 'cutoff-passed-nations-nov15', url: `${web}/?sport=soccer&competition=uefa.nations&date=2026-11-15`, daysAhead: 10,
    expect: [/No forecast: none was recorded before this match’s cutoff \(.*\), and none is made after it\./] },
  { name: 'mixed-day-friendlies-nov14', url: `${web}/?sport=soccer&competition=fifa.friendly&date=2026-11-14`,
    expect: [/90-minute forecast: .* win \d+\.\d% · Draw/, /Awaiting forecast: recorded by .* at the latest/] },
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
if (oldApi) {
  cases.push({ name: 'old-api-recorded-day', oldApi: true, url: `${oldApi}/?sport=soccer&competition=uefa.nations`, expect: [/UEFA Nations League matches/, /Live soccer forecasts:/, /Upcoming forecasts/] },
    { name: 'old-api-awaiting-day', oldApi: true, url: `${oldApi}/?sport=soccer&competition=eng.1`, expect: [/Premier League matches/, /Forecast status not reported:/, /Latest Premier League scoreboard request\./] },
    { name: 'old-api-match', oldApi: true, url: `${oldApi}/games/${encodeURIComponent('soccer:uefa.nations:401861145')}?sport=soccer`, expect: [/Match facts/, /Forecast/] });
}
// The live forecast card must be readable in both themes (review finding: white card with invisible text in dark).
for (const name of ['recorded-uefa-nations', 'match-with-forecast']) {
  const base = cases.find((c) => c.name === name);
  base.contrast = true;
  cases.push({ ...base, theme: 'dark' });
}
cases.push({ ...cases.find((c) => c.name === 'awaiting-premier-league-played-day'), theme: 'dark' });
const results = [];
if (process.env.ONLY_CONTRAST) cases.splice(0, cases.length, ...cases.filter((c) => c.contrast));   // before/after of the card fix
if (process.env.ONLY_OLD_API) cases.splice(0, cases.length, ...cases.filter((c) => c.oldApi));     // before/after of the old-API guards
for (const c of cases) for (const [width, height] of [[1280, 900], [390, 844]]) results.push(await check({ ...c, width, height }));
for (const r of results) console.log(JSON.stringify(r));
console.log(`SUMMARY ${results.filter((r) => r.ok).length}/${results.length} ok; failed: ${results.filter((r) => !r.ok).map((r) => r.name + '/' + r.theme + '@' + r.width).join(', ') || 'none'}`);
process.exit(results.every((r) => r.ok) ? 0 : 1);
