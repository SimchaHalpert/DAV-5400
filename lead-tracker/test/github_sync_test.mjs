// Run: node lead-tracker/test/github_sync_test.mjs
// Runs the real fetcher (fetcher/fetch.mjs) against a local fake RentCast server and a fake
// "web app" that executes Code.gs doGet/doPost on a fake Google Sheet.
// Set LIVE_RENTCAST_KEY to hit the real RentCast API instead of the fake (uses 2 requests).
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import http from 'node:http';
import assert from 'node:assert';
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const dir = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(dir, '..');

// ---- fake Google Sheet ----
function fakeSheet(ss) {
  let grid = [];
  const isF = v => typeof v === 'string' && v[0] === '=';
  const values = () => grid.map(r => r.map(v => isF(v) ? 'Look up owner' : v));
  const range = (row, col, nr, nc) => ({
    setValues(vals) { vals.forEach((r, i) => r.forEach((v, j) => { (grid[row - 1 + i] ||= [])[col - 1 + j] = v; })); return this; },
    getValues() { return Array.from({ length: nr }, (_, i) => Array.from({ length: nc }, (_, j) => values()[row - 1 + i]?.[col - 1 + j] ?? '')); },
    clearContent() { for (let i = 0; i < nr; i++) for (let j = 0; j < nc; j++) if (grid[row - 1 + i]) grid[row - 1 + i][col - 1 + j] = ''; grid = grid.filter((r, i) => i < row - 1 || r.some(v => v !== '')); return this; },
    setFontWeight() { return this; }, setBackground() { return this; }, setFontColor() { return this; }, setDataValidation() { return this; }
  });
  return {
    grid: () => grid, getLastRow: () => grid.length, getLastColumn: () => grid.reduce((m, r) => Math.max(m, r.length), 0),
    getRange: (r, c, nr = 1, nc = 1) => range(r, c, nr, nc),
    getDataRange() { const v = values(), f = grid.map(r => r.map(x => isF(x) ? x : '')); return { getValues: () => v, getFormulas: () => f }; },
    appendRow(r) { grid.push(r); }, setFrozenRows() {}, setFrozenColumns() {}, setColumnWidth() {}, setConditionalFormatRules() {},
    getParent: () => ss, hideSheet() {}, clearContents() { grid = []; }
  };
}
const sheets = {};
const ss = { getSheetByName: n => sheets[n] || null, insertSheet: n => (sheets[n] = fakeSheet(ss)), getSheets: () => Object.values(sheets), deleteSheet() {} };
const store = (m = {}) => ({ getProperty: k => m[k] ?? null, setProperty: (k, v) => { m[k] = v; }, _m: m });
const scriptProps = store();
const builder = () => new Proxy({}, { get: (t, k) => k === 'build' ? () => ({}) : () => builder() });
const ctx = {
  module: { exports: {} }, console,
  SpreadsheetApp: { getActive: () => ss, getUi: () => ({ alert() {} }), newDataValidation: builder, newConditionalFormatRule: builder },
  PropertiesService: { getScriptProperties: () => scriptProps, getDocumentProperties: () => store() },
  LockService: { getScriptLock: () => ({ tryLock: () => true, releaseLock() {} }) },
  ContentService: { MimeType: { JSON: 'json' }, createTextOutput: t => ({ setMimeType() { return this; }, text: t }) },
  Utilities: { getUuid: () => 'abcd-1234-efgh-5678', sleep() {} },
  UrlFetchApp: { fetch() { throw new Error('Google cannot reach RentCast in this test'); } }
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(root, 'apps-script', 'Code.gs'), 'utf8'), ctx);
ctx.setupSheet();
const token = ctx.getSyncToken_();

// ---- fake RentCast ----
const day = 86400000, now = Date.now(), iso = d => new Date(now - d * day).toISOString();
const rc = {
  '/properties': [
    { id: 'A', formattedAddress: '2400 Overland Ave, Los Angeles, CA 90064', addressLine1: '2400 Overland Ave', city: 'Los Angeles', state: 'CA', zipCode: '90064', propertyType: 'Single Family', lastSaleDate: iso(40), lastSalePrice: 1850000, owner: { names: ['DOE, JANE & JOHN'] }, taxAssessments: { big: 'dropped by fetcher' } }
  ],
  '/listings/sale': [
    { id: 'B', formattedAddress: '11000 Rose Ave, Los Angeles, CA 90064', addressLine1: '11000 Rose Ave', city: 'Los Angeles', state: 'CA', zipCode: '90064', propertyType: 'Single Family', status: 'Inactive', price: 1695000, listedDate: iso(45), removedDate: iso(12), listingAgent: { name: 'Sam Agent', phone: '3105551212' } }
  ]
};
const seen = [];
const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://x');
  if (u.pathname.startsWith('/v1/')) {
    seen.push(u.pathname + '?' + u.searchParams);
    if (req.headers['x-api-key'] !== 'rc_test') { res.writeHead(401); return res.end('{}'); }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify(rc[u.pathname.slice(3)]));
  }
  if (u.pathname === '/exec') {
    const e = { parameter: Object.fromEntries(u.searchParams) };
    if (req.method === 'POST') {
      let body = ''; for await (const c of req) body += c;
      e.postData = { contents: body };
      // Mimic Apps Script: run doPost, answer with a 302 to a GET that returns the output
      const out = ctx.doPost(e);
      server.lastPost = JSON.parse(body);
      server.pending = out.text;
      res.writeHead(302, { Location: '/echo' }); return res.end();
    }
    const out = ctx.doGet(e);
    res.writeHead(200, { 'Content-Type': 'application/json' }); return res.end(out.text);
  }
  if (u.pathname === '/echo') { res.writeHead(200, { 'Content-Type': 'application/json' }); return res.end(server.pending); }
  res.writeHead(404); res.end();
});
await new Promise(r => server.listen(0, r));
const port = server.address().port;

const live = process.env.LIVE_RENTCAST_KEY;
function runFetcher(sheetUrl) {
  return new Promise(resolve => execFile(process.execPath, [path.join(root, 'fetcher', 'fetch.mjs')], {
    env: { ...process.env, RENTCAST_API_KEY: live || 'rc_test', SHEET_SYNC_URL: sheetUrl,
      ...(live ? {} : { RENTCAST_BASE: `http://localhost:${port}/v1` }) }
  }, (err, stdout, stderr) => resolve({ code: err ? err.code : 0, stdout, stderr })));
}

// Bad token is rejected and nothing is written
let r = await runFetcher(`http://localhost:${port}/exec?token=wrong`);
assert.notStrictEqual(r.code, 0);
assert.match(r.stderr, /bad token/);

// Good run
r = await runFetcher(`http://localhost:${port}/exec?token=${token}`);
console.log(r.stdout.trim()); if (r.stderr) console.log(r.stderr.trim());
assert.strictEqual(r.code, 0);
const g = sheets.Leads.grid(), h = g[0];
const rows = g.slice(1).map(x => Object.fromEntries(h.map((k, i) => [k, x[i]])));
if (!live) {
  assert.strictEqual(rows.length, 2);
  assert.strictEqual(rows.find(x => x.Status === 'Sold')['Owner Name(s)'], 'DOE, JANE & JOHN');
  assert.match(rows.find(x => x.Status === 'Sold')['Free Lookup'], /name=Jane%20Doe/);
  assert.strictEqual(rows.find(x => x.Status !== 'Sold')['Agent Phone'], '(310) 555-1212');
  assert.ok(!JSON.stringify(server.lastPost).includes('dropped by fetcher'), 'fetcher strips unused fields');
  assert.ok(seen.some(s => s.includes('saleDateRange=*%3A180')) && seen.some(s => s.includes('daysOld=*%3A180')));
  assert.ok(!r.stdout.includes('Overland'), 'no addresses in GitHub logs');
} else {
  console.log(`LIVE: ${rows.length} leads written (${rows.filter(x => x.Status === 'Sold').length} sold, ${rows.filter(x => x.Status !== 'Sold').length} pending)`);
  assert.ok(rows.length > 0);
}
const usedKey = Object.keys(scriptProps._m).find(k => k.startsWith('RENTCAST_USED_'));
assert.strictEqual(scriptProps._m[usedKey], '2', 'sheet counts the requests GitHub used');
assert.match(String(sheets.Log.grid().at(-1)[6]), /via GitHub/);

// Monthly limit respected
scriptProps.setProperty(usedKey, '50');
seen.length = 0;
r = await runFetcher(`http://localhost:${port}/exec?token=${token}`);
if (!live) assert.strictEqual(seen.length, 0, 'no RentCast calls once limit reached');
assert.match(r.stdout, /limit/);

server.close();
console.log('\ngithub sync test passed' + (live ? ' (LIVE RentCast)' : ''));
