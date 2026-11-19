// Run: node lead-tracker/test/run_tests.js
// Tests the pure logic in Code.gs with mock RentCast / Tracerfy responses (no API calls).
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const src = fs.readFileSync(path.join(__dirname, '..', 'Code.gs'), 'utf8');
const ctx = { module: { exports: {} }, console };
vm.createContext(ctx);
vm.runInContext(src, ctx);
const L = ctx.module.exports;

const now = new Date('2026-11-19T12:00:00Z');
const settings = { maxPrice: 2000000, soldDays: 180, pendingDays: 60, propertyType: 'Single Family' };
const daysAgo = (n) => new Date(now.getTime() - n * 86400000).toISOString();

const prop = (id, o) => Object.assign({
  id, formattedAddress: id.replace(/-/g, ' '), addressLine1: '2400 Overland Ave', city: 'Los Angeles',
  state: 'CA', zipCode: '90064', propertyType: 'Single Family', bedrooms: 3, bathrooms: 2,
  squareFootage: 1650, lotSize: 6000, yearBuilt: 1948,
  owner: { names: ['Jane Doe'], type: 'Individual', mailingAddress: { formattedAddress: '2400 Overland Ave, Los Angeles, CA 90064' } },
  ownerOccupied: true
}, o);
const listing = (id, o) => Object.assign({
  id, formattedAddress: id, addressLine1: '1 Test St', city: 'Los Angeles', state: 'CA', zipCode: '90064',
  propertyType: 'Single Family', status: 'Inactive', price: 1800000,
  listedDate: daysAgo(70), removedDate: daysAgo(20), daysOnMarket: 50, mlsNumber: '25-123',
  listingAgent: { name: 'Sam Agent', phone: '3105551212', email: 'sam@example.com' },
  listingOffice: { name: 'Westside Realty', phone: '13105550000' }
}, o);

let passed = 0;
function test(name, fn) { fn(); passed++; console.log('ok -', name); }

test('sold within 6 months and under cap is kept, with owner info', () => {
  const leads = L.buildLeads([prop('A', { lastSaleDate: daysAgo(30), lastSalePrice: 1750000 })], [], settings, now);
  assert.strictEqual(leads.length, 1);
  const l = leads[0];
  assert.strictEqual(l['Status'], L.STATUS_SOLD);
  assert.strictEqual(l['Owner Name(s)'], 'Jane Doe');
  assert.strictEqual(l['Owner Occupied'], 'Yes');
  assert.strictEqual(l['Sale Price'], 1750000);
  assert.match(l['Free Lookup'], /truepeoplesearch\.com\/resultname\?name=Jane%20Doe&citystatezip=Los%20Angeles%2C%20CA%2090064/);
});

test('sold over $2M is dropped; older than 180 days is dropped; unknown price kept', () => {
  const leads = L.buildLeads([
    prop('OVER', { lastSaleDate: daysAgo(10), lastSalePrice: 2100000 }),
    prop('OLD', { lastSaleDate: daysAgo(200), lastSalePrice: 1000000 }),
    prop('NOPRICE', { lastSaleDate: daysAgo(10) }),
    prop('CONDO', { lastSaleDate: daysAgo(10), lastSalePrice: 900000, propertyType: 'Condo' })
  ], [], settings, now);
  assert.deepStrictEqual(Array.from(leads, l => l['Property ID']), ['NOPRICE']);
});

test('recently delisted listing with no recorded sale = pending, with agent contact', () => {
  const leads = L.buildLeads([], [listing('P1'), listing('STALE', { removedDate: daysAgo(90) }),
    listing('PRICEY', { price: 2500000 })], settings, now);
  assert.strictEqual(leads.length, 1);
  assert.strictEqual(leads[0]['Status'], L.STATUS_PENDING);
  assert.strictEqual(leads[0]['Agent Phone'], '(310) 555-1212');
  assert.strictEqual(leads[0]['Office Phone'], '(310) 555-0000');
  assert.strictEqual(leads[0]['Free Lookup'], '');
});

test('listing that already recorded a sale shows as Sold with agent info merged', () => {
  const leads = L.buildLeads([prop('S1', { lastSaleDate: daysAgo(10), lastSalePrice: 1790000 })],
    [listing('S1', { listedDate: daysAgo(60) })], settings, now);
  assert.strictEqual(leads.length, 1);
  assert.strictEqual(leads[0]['Status'], L.STATUS_SOLD);
  assert.strictEqual(leads[0]['Listing Agent'], 'Sam Agent');
});

test('merge: new rows added, pending upgrades to sold, manual columns kept, sold never downgraded', () => {
  const existing = [];
  let m = L.mergeLeads(existing, L.buildLeads([], [listing('X')], settings, now), now);
  assert.strictEqual(m.added, 1);
  m.rows[0]['Outreach Status'] = 'Contacted';
  m.rows[0]['Notes'] = 'Talked to agent';
  const later = new Date(now.getTime() + 10 * 86400000);
  const sold = L.buildLeads([prop('X', { lastSaleDate: daysAgo(-5), lastSalePrice: 1800000 })], [], settings, later);
  m = L.mergeLeads(m.rows, sold, later);
  assert.strictEqual(m.added, 0);
  assert.strictEqual(m.updated, 1);
  const r = m.rows[0];
  assert.strictEqual(r['Status'], L.STATUS_SOLD);
  assert.strictEqual(r['Owner Name(s)'], 'Jane Doe');
  assert.strictEqual(r['Listing Agent'], 'Sam Agent');
  assert.strictEqual(r['Outreach Status'], 'Contacted');
  assert.strictEqual(r['Notes'], 'Talked to agent');
  m = L.mergeLeads(m.rows, L.buildLeads([], [listing('X')], settings, later), later);
  assert.strictEqual(m.rows[0]['Status'], L.STATUS_SOLD);
});

test('names: individuals searched by name, trusts/LLCs fall back to address', () => {
  assert.deepStrictEqual(JSON.parse(JSON.stringify(L.pickPersonName(['John Q Public Jr'], 'Individual'))), { first: 'John', last: 'Public' });
  assert.strictEqual(L.pickPersonName(['Smith Family Trust'], 'Individual'), null);
  assert.strictEqual(L.pickPersonName(['Acme Holdings LLC'], 'Organization'), null);
  const trustLead = L.buildLeads([prop('T', { lastSaleDate: daysAgo(5), lastSalePrice: 1e6,
    owner: { names: ['Smith Living Trust'], type: 'Organization' } })], [], settings, now)[0];
  assert.match(trustLead['Free Lookup'], /resultaddress\?streetaddress=2400%20Overland%20Ave/);
  const req = L.buildTraceRequest(trustLead);
  assert.strictEqual(req.find_owner, true);
  const req2 = L.buildTraceRequest(L.buildLeads([prop('I', { lastSaleDate: daysAgo(5), lastSalePrice: 1e6 })], [], settings, now)[0]);
  assert.strictEqual(req2.find_owner, false);
  assert.strictEqual(req2.first_name, 'Jane');
  assert.strictEqual(req2.zip, '90064');
});

test('skip trace parse: callable phones first, DNC flagged, litigator flagged', () => {
  const parsed = L.parseTraceResult({ hit: true, persons: [{
    first_name: 'JANE', last_name: 'DOE', property_owner: true, litigator: false,
    phones: [{ number: '3105550001', type: 'Mobile', dnc: true, rank: 1 }, { number: '3105550002', type: 'Mobile', dnc: false, rank: 2 }],
    emails: [{ email: 'jane@example.com', rank: 1 }]
  }]});
  const row = {};
  L.applyTrace(row, parsed, now);
  assert.strictEqual(row['Phone 1'], '(310) 555-0002');
  assert.strictEqual(row['Phone 1 DNC'], 'No');
  assert.strictEqual(row['Phone 2 DNC'], 'YES - DNC');
  assert.strictEqual(row['Email 1'], 'jane@example.com');
  assert.strictEqual(row['Litigator Flag'], 'No');
  const miss = {}; L.applyTrace(miss, L.parseTraceResult({ hit: false }), now);
  assert.match(miss['Skip Traced On'], /no match/);
});

test('settings parse: zips, defaults, paid lookup off unless TRUE', () => {
  const s = L.parseSettings({ 'Zip Codes': '90064, 90025;abc', 'Max Price': 2000000, 'Skip Trace Enabled': 'FALSE' });
  assert.deepStrictEqual(Array.from(s.zips), ['90064', '90025']);
  assert.strictEqual(s.soldDays, 180);
  assert.strictEqual(s.skipTrace, false);
  assert.strictEqual(L.parseSettings({ 'Zip Codes': '90064' }).skipTrace, false);
  assert.strictEqual(L.parseSettings({ 'Zip Codes': '90064', 'Skip Trace Enabled': true }).skipTrace, true);
  assert.throws(() => L.parseSettings({ 'Zip Codes': '' }));
});

test('every column header exists once', () => {
  assert.strictEqual(new Set(L.ALL_COLUMNS).size, L.ALL_COLUMNS.length);
  assert.strictEqual(L.columnLetter_(28), 'AB');
});

console.log(`\n${passed} tests passed`);
