// Run: node lead-tracker/test/e2e_mock.js
// End-to-end run of runUpdate() against a fake Google Sheet + fake RentCast/Tracerfy.
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

function fakeSheet(name) {
  let grid = [];
  const formulasOf = () => grid.map(r => r.map(v => (typeof v === 'string' && v[0] === '=') ? v : ''));
  const valuesOf = () => grid.map(r => r.map(v => (typeof v === 'string' && v[0] === '=') ? 'Look up owner' : v));
  const width = () => grid.reduce((m, r) => Math.max(m, r.length), 0);
  const range = (row, col, nr, nc) => ({
    setValues(vals) { vals.forEach((r, i) => r.forEach((v, j) => { (grid[row - 1 + i] = grid[row - 1 + i] || [])[col - 1 + j] = v; })); return this; },
    getValues() { return Array.from({ length: nr }, (_, i) => Array.from({ length: nc }, (_, j) => valuesOf()[row - 1 + i]?.[col - 1 + j] ?? '')); },
    clearContent() { for (let i = 0; i < nr; i++) for (let j = 0; j < nc; j++) if (grid[row - 1 + i]) grid[row - 1 + i][col - 1 + j] = ''; grid = grid.filter((r, i) => i < row - 1 || r.some(v => v !== '')); return this; },
    setFontWeight() { return this; }, setBackground() { return this; }, setFontColor() { return this; }, setDataValidation() { return this; }
  });
  return {
    name, grid: () => grid,
    getLastRow: () => grid.length, getLastColumn: () => width(),
    getRange: (r, c, nr = 1, nc = 1) => range(r, c, nr, nc),
    getDataRange() { const v = valuesOf(), f = formulasOf(); return { getValues: () => v, getFormulas: () => f }; },
    appendRow(r) { grid.push(r); }, setFrozenRows() {}, setFrozenColumns() {}, setColumnWidth() {}, setConditionalFormatRules() {},
    getParent: () => ss, hideSheet() {}, clearContents() { grid = []; }
  };
}

const sheets = {};
const ss = {
  getSheetByName: n => sheets[n] || null,
  insertSheet: n => (sheets[n] = fakeSheet(n)),
  getSheets: () => Object.values(sheets), deleteSheet() {}
};
const store = (init = {}) => { const m = { ...init }; return { getProperty: k => m[k] ?? null, setProperty: (k, v) => { m[k] = v; }, _m: m }; };
const scriptProps = store({ RENTCAST_API_KEY: 'rc_test', TRACERFY_API_KEY: 'tf_test' });
const docProps = store();
const calls = [];
let failListings = 0;

const day = 86400000;
const now = Date.now();
const iso = d => new Date(now - d * day).toISOString();
const rentcast = {
  '/properties': [
    { id: '2400-Overland-Ave,-Los-Angeles,-CA-90064', formattedAddress: '2400 Overland Ave, Los Angeles, CA 90064', addressLine1: '2400 Overland Ave', city: 'Los Angeles', state: 'CA', zipCode: '90064', propertyType: 'Single Family', bedrooms: 3, bathrooms: 2, squareFootage: 1600, yearBuilt: 1947, lastSaleDate: iso(40), lastSalePrice: 1850000, owner: { names: ['Jane Doe'], type: 'Individual' }, ownerOccupied: true },
    { id: '2500-Military-Ave,-Los-Angeles,-CA-90064', formattedAddress: '2500 Military Ave, Los Angeles, CA 90064', addressLine1: '2500 Military Ave', city: 'Los Angeles', state: 'CA', zipCode: '90064', propertyType: 'Single Family', lastSaleDate: iso(20), lastSalePrice: 2600000, owner: { names: ['Rich Buyer'], type: 'Individual' } }
  ],
  '/listings/sale': [
    { id: '11000-Rose-Ave,-Los-Angeles,-CA-90064', formattedAddress: '11000 Rose Ave, Los Angeles, CA 90064', addressLine1: '11000 Rose Ave', city: 'Los Angeles', state: 'CA', zipCode: '90064', propertyType: 'Single Family', status: 'Inactive', price: 1695000, listedDate: iso(45), removedDate: iso(12), daysOnMarket: 33, listingAgent: { name: 'Sam Agent', phone: '3105551212', email: 'sam@example.com' } }
  ]
};
const UrlFetchApp = {
  fetch(url, opts) {
    calls.push({ url, opts });
    if (url.startsWith('https://api.rentcast.io/v1')) {
      assert.strictEqual(opts.headers['X-Api-Key'], 'rc_test');
      const p = url.slice('https://api.rentcast.io/v1'.length).split('?')[0];
      const q = new URLSearchParams(url.split('?')[1]);
      assert.strictEqual(q.get('zipCode'), '90064');
      assert.strictEqual(q.get('propertyType'), 'Single Family');
      if (p === '/properties') assert.strictEqual(q.get('saleDateRange'), '*:180');
      if (p === '/listings/sale') {
        assert.strictEqual(q.get('status'), 'Inactive'); assert.strictEqual(q.get('price'), '*:2000000'); assert.strictEqual(q.get('daysOld'), '*:180');
        if (failListings > 0) { failListings--; throw new Error('Timeout: ' + url); }
      }
      return { getResponseCode: () => 200, getContentText: () => JSON.stringify(rentcast[p]) };
    }
    if (url.startsWith('https://tracerfy.com')) {
      const body = JSON.parse(opts.payload);
      assert.ok(Array.isArray(body));
      const results = body.map(b => ({ hit: true, persons: [{ first_name: b.first_name, last_name: b.last_name, property_owner: true,
        phones: [{ number: '3105550002', type: 'Mobile', dnc: false, rank: 1 }], emails: [{ email: 'jane@example.com', rank: 1 }] }] }));
      return { getResponseCode: () => 200, getContentText: () => JSON.stringify({ results, credits_deducted: 5 * results.length }) };
    }
    throw new Error('unexpected url ' + url);
  }
};
const builder = () => new Proxy({}, { get: (t, k) => k === 'build' ? () => ({}) : () => builder() });
const ctx = {
  module: { exports: {} }, console,
  SpreadsheetApp: { getActive: () => ss, getUi: () => ({ alert() {} }), newDataValidation: builder, newConditionalFormatRule: builder },
  PropertiesService: { getScriptProperties: () => scriptProps, getDocumentProperties: () => docProps },
  LockService: { getScriptLock: () => ({ tryLock: () => true, releaseLock() {} }) },
  UrlFetchApp,
  Utilities: { sleep() {} }
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'apps-script', 'Code.gs'), 'utf8'), ctx);

ctx.setupSheet();
// Turn paid lookup on for this test
const st = sheets.Settings.grid(); st.find(r => r[0] === 'Skip Trace Enabled')[1] = 'TRUE';

ctx.runUpdate();
const table = () => { const g = sheets.Leads.grid(); const h = g[0]; return g.slice(1).map(r => Object.fromEntries(h.map((k, i) => [k, r[i]]))); };
let rows = table();
assert.strictEqual(rows.length, 2, 'one sold (under $2M) + one pending');
const sold = rows.find(r => r.Status === 'Sold');
const pend = rows.find(r => r.Status !== 'Sold');
assert.strictEqual(sold['Owner Name(s)'], 'Jane Doe');
assert.strictEqual(sold['Phone 1'], '(310) 555-0002');
assert.strictEqual(sold['Email 1'], 'jane@example.com');
assert.match(sold['Free Lookup'], /^=HYPERLINK\("https:\/\/www\.truepeoplesearch\.com/);
assert.strictEqual(pend['Agent Phone'], '(310) 555-1212');
assert.strictEqual(sold['Outreach Status'], 'New');
const traceBody = JSON.parse(calls.find(c => c.url.includes('tracerfy')).opts.payload);
assert.strictEqual(traceBody.length, 1);
assert.strictEqual(traceBody[0].first_name, 'Jane');

// Team edits a row, then the script runs again
const g = sheets.Leads.grid(); const h = g[0];
g[1][h.indexOf('Outreach Status')] = 'Contacted';
g[1][h.indexOf('Notes')] = 'Left voicemail';
calls.length = 0;
ctx.runUpdate();
rows = table();
assert.strictEqual(rows.length, 2, 'no duplicates on rerun');
const edited = rows.find(r => r['Notes'] === 'Left voicemail');
assert.ok(edited, 'notes survive rerun');
assert.strictEqual(edited['Outreach Status'], 'Contacted');
assert.ok(!calls.some(c => c.url.includes('tracerfy')), 'already-traced rows are not paid for twice');
assert.match(rows.find(r => r.Status === 'Sold')['Free Lookup'], /^=HYPERLINK/, 'link formula survives rerun');
assert.strictEqual(scriptProps._m[Object.keys(scriptProps._m).find(k => k.startsWith('RENTCAST_USED_'))], '4', 'quota counter: 2 requests x 2 runs');
assert.strictEqual(sheets._data.grid().length, 2, 'lookup details stored on hidden tab');

// Listings time out on both tries -> sold leads still saved, error logged per lookup
sheets.Leads.getRange(2, 1, 10, 50).clearContent(); sheets._data.clearContents();
failListings = 2;
ctx.runUpdate();
rows = table();
assert.strictEqual(rows.length, 1, 'sold kept when pending lookup fails');
assert.strictEqual(rows[0].Status, 'Sold');
assert.match(String(sheets.Log.grid().at(-1)[6]), /90064 pending: Timeout/);
// Times out once, retry succeeds
failListings = 1;
ctx.runUpdate();
assert.strictEqual(table().length, 2, 'retry recovers pending lead');
assert.strictEqual(String(sheets.Log.grid().at(-1)[6]), '');

// Quota guard
scriptProps.setProperty(Object.keys(scriptProps._m).find(k => k.startsWith('RENTCAST_USED_')), '50');
calls.length = 0;
ctx.runUpdate();
assert.ok(!calls.some(c => c.url.includes('rentcast')), 'stops at monthly limit');
assert.match(String(sheets.Log.grid().at(-1)[6]), /limit reached/);
console.log('Log tab:'); sheets.Log.grid().forEach(r => console.log('  ', r.map(String).join(' | ')));
console.log('\ne2e mock run passed');
