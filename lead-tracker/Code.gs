/**
 * Aggregate Construction Group — Home Sale Lead Tracker
 *
 * Runs inside a Google Sheet (Extensions > Apps Script). Pulls, for each zip code
 * on the Settings tab:
 *   - SOLD:    single-family homes sold in the last N days at or under the max price
 *              (RentCast property records: owner name, mailing address, sale price)
 *   - PENDING: single-family listings at or under the max price that recently came
 *              off the market with no sale recorded yet (likely in escrow)
 *              (RentCast sale listings: listing agent phone/email)
 * Then finds owner phone/email for SOLD homes (Tracerfy instant trace, pay per hit).
 *
 * API keys are stored in Script Properties (never in the sheet):
 *   RENTCAST_API_KEY, TRACERFY_API_KEY
 */

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

var RENTCAST_BASE = 'https://api.rentcast.io/v1';
var TRACERFY_LOOKUP = 'https://tracerfy.com/v1/api/trace/lookup/';
var TRACERFY_BATCH_MAX = 15; // API limit per array request

var SHEET_LEADS = 'Leads';
var SHEET_SETTINGS = 'Settings';
var SHEET_LOG = 'Log';

var STATUS_SOLD = 'Sold';
var STATUS_PENDING = 'Pending (off market)';

// Columns written by the script. Order = sheet order.
var LEAD_COLUMNS = [
  'Property ID', 'First Found', 'Last Updated', 'Status', 'Address', 'Zip',
  'Sale Date', 'Sale Price', 'List Price', 'Listed Date', 'Off Market Date', 'Days on Market',
  'Beds', 'Baths', 'Sq Ft', 'Lot Sq Ft', 'Year Built',
  'Owner Name(s)', 'Owner Type', 'Owner Occupied', 'Owner Mailing Address',
  'Phone 1', 'Phone 1 Type', 'Phone 1 DNC', 'Phone 2', 'Phone 2 Type', 'Phone 2 DNC',
  'Email 1', 'Email 2', 'Litigator Flag', 'Skip Traced On', 'Free Lookup',
  'Listing Agent', 'Agent Phone', 'Agent Email', 'Listing Office', 'Office Phone', 'MLS #'
];
// Columns the team edits by hand. The script never overwrites these.
var MANUAL_COLUMNS = ['Outreach Status', 'Notes'];
var ALL_COLUMNS = LEAD_COLUMNS.concat(MANUAL_COLUMNS);

var DEFAULT_SETTINGS = [
  ['Setting', 'Value', 'Notes'],
  ['Zip Codes', '90064', 'Comma-separated, e.g. 90064, 90025, 90034'],
  ['Max Price', 2000000, 'Only homes sold/listed at or under this price'],
  ['Sold Lookback (days)', 180, 'How far back to look for recorded sales (180 = 6 months)'],
  ['Pending Lookback (days)', 60, 'Listings that went off market within this many days'],
  ['Property Type', 'Single Family', 'RentCast property type'],
  ['RentCast Monthly Request Limit', 50, 'Script stops calling RentCast after this many requests per month (free plan = 50)'],
  ['Skip Trace Enabled', 'FALSE', 'TRUE/FALSE — auto look up owner phone & email on sold homes (Tracerfy, ~$0.10 per hit). FALSE = use the free TruePeopleSearch link on each row instead'],
  ['Max Skip Traces Per Run', 40, 'Caps Tracerfy spend per run']
];

// ---------------------------------------------------------------------------
// Menu & setup
// ---------------------------------------------------------------------------

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Lead Tracker')
    .addItem('1. Set up sheet', 'setupSheet')
    .addItem('2. Set API keys', 'setApiKeys')
    .addItem('3. Run now', 'runUpdate')
    .addSeparator()
    .addItem('Test connection', 'testConnection')
    .addItem('Turn on weekday auto-update (7am)', 'installTriggers')
    .addItem('Turn off auto-update', 'removeTriggers')
    .addToUi();
}

function setupSheet() {
  var ss = SpreadsheetApp.getActive();

  var settings = ss.getSheetByName(SHEET_SETTINGS) || ss.insertSheet(SHEET_SETTINGS);
  if (settings.getLastRow() === 0) {
    settings.getRange(1, 1, DEFAULT_SETTINGS.length, 3).setValues(DEFAULT_SETTINGS);
    settings.getRange(1, 1, 1, 3).setFontWeight('bold');
    settings.setColumnWidth(1, 240); settings.setColumnWidth(2, 200); settings.setColumnWidth(3, 520);
  }

  var leads = ss.getSheetByName(SHEET_LEADS) || ss.insertSheet(SHEET_LEADS, 0);
  if (leads.getLastRow() === 0) {
    leads.getRange(1, 1, 1, ALL_COLUMNS.length).setValues([ALL_COLUMNS])
      .setFontWeight('bold').setBackground('#1f2937').setFontColor('#ffffff');
    leads.setFrozenRows(1);
    leads.setFrozenColumns(5);
    // Dropdown for outreach tracking
    var outreachCol = ALL_COLUMNS.indexOf('Outreach Status') + 1;
    var rule = SpreadsheetApp.newDataValidation()
      .requireValueInList(['New', 'Contacted', 'Follow Up', 'Meeting Set', 'Bid Sent', 'Won', 'Not Interested', 'Do Not Contact'], true)
      .build();
    leads.getRange(2, outreachCol, 5000, 1).setDataValidation(rule);
    // Highlight leads found in the last 3 days
    var firstFoundLetter = columnLetter_(ALL_COLUMNS.indexOf('First Found') + 1);
    var cf = SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied('=AND($' + firstFoundLetter + '2<>"", TODAY()-$' + firstFoundLetter + '2<=3)')
      .setBackground('#fff7cc')
      .setRanges([leads.getRange(2, 1, 5000, ALL_COLUMNS.length)])
      .build();
    leads.setConditionalFormatRules([cf]);
  }

  var log = ss.getSheetByName(SHEET_LOG) || ss.insertSheet(SHEET_LOG);
  if (log.getLastRow() === 0) {
    log.getRange(1, 1, 1, 7).setValues([['Run At', 'RentCast Requests', 'New Leads', 'Updated Leads', 'Skip Traces (hits)', 'Month Requests Used', 'Messages']])
      .setFontWeight('bold');
  }
  var sheet1 = ss.getSheetByName('Sheet1');
  if (sheet1 && sheet1.getLastRow() === 0 && ss.getSheets().length > 1) ss.deleteSheet(sheet1);

  SpreadsheetApp.getUi().alert('Sheet is set up. Next: Lead Tracker > 2. Set API keys.');
}

function setApiKeys() {
  var ui = SpreadsheetApp.getUi();
  var props = PropertiesService.getScriptProperties();
  var r1 = ui.prompt('RentCast API key', 'Paste your RentCast API key (leave blank to keep the current one):', ui.ButtonSet.OK_CANCEL);
  if (r1.getSelectedButton() !== ui.Button.OK) return;
  if (r1.getResponseText().trim()) props.setProperty('RENTCAST_API_KEY', r1.getResponseText().trim());
  var r2 = ui.prompt('Tracerfy API key', 'Paste your Tracerfy API key (leave blank to skip / keep current):', ui.ButtonSet.OK_CANCEL);
  if (r2.getSelectedButton() !== ui.Button.OK) return;
  if (r2.getResponseText().trim()) props.setProperty('TRACERFY_API_KEY', r2.getResponseText().trim());
  ui.alert('Keys saved. Run Lead Tracker > 3. Run now.');
}

/** Diagnose Google -> RentCast connectivity with one tiny request (limit=1). */
function testConnection() {
  var key = PropertiesService.getScriptProperties().getProperty('RENTCAST_API_KEY');
  var lines = [];
  function probe(label, url, headers) {
    var t0 = Date.now();
    try {
      var r = UrlFetchApp.fetch(url, { headers: headers || {}, muteHttpExceptions: true });
      lines.push(label + ': HTTP ' + r.getResponseCode() + ' in ' + ((Date.now() - t0) / 1000).toFixed(1) + 's' +
        (r.getResponseCode() !== 200 ? ' - ' + r.getContentText().slice(0, 150) : ''));
    } catch (e) {
      lines.push(label + ': FAILED after ' + ((Date.now() - t0) / 1000).toFixed(1) + 's - ' + String(e.message || e).slice(0, 150));
    }
  }
  probe('Google (control)', 'https://www.google.com/generate_204');
  probe('RentCast (no key)', 'https://api.rentcast.io/v1/properties?zipCode=90064&limit=1');
  if (key) probe('RentCast (your key)', 'https://api.rentcast.io/v1/properties?zipCode=90064&limit=1', { 'X-Api-Key': key, 'Accept': 'application/json' });
  else lines.push('RentCast (your key): no key saved');
  var msg = lines.join('\n');
  var log = SpreadsheetApp.getActive().getSheetByName(SHEET_LOG);
  if (log) log.appendRow([new Date(), '', '', '', '', '', 'Connection test: ' + lines.join(' | ')]);
  SpreadsheetApp.getUi().alert('Connection test', msg, SpreadsheetApp.getUi().ButtonSet.OK);
}

function installTriggers() {
  removeTriggers();
  [ScriptApp.WeekDay.MONDAY, ScriptApp.WeekDay.TUESDAY, ScriptApp.WeekDay.WEDNESDAY,
   ScriptApp.WeekDay.THURSDAY, ScriptApp.WeekDay.FRIDAY].forEach(function (day) {
    ScriptApp.newTrigger('runUpdate').timeBased().onWeekDay(day).atHour(7).create();
  });
  SpreadsheetApp.getUi().alert('Auto-update is on: every weekday around 7am (sheet time zone).');
}

function removeTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'runUpdate') ScriptApp.deleteTrigger(t);
  });
}

// ---------------------------------------------------------------------------
// Main run
// ---------------------------------------------------------------------------

function runUpdate() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return;
  var messages = [];
  var stats = { requests: 0, added: 0, updated: 0, traced: 0, hits: 0 };
  try {
    var ss = SpreadsheetApp.getActive();
    var settings = readSettings_(ss);
    var props = PropertiesService.getScriptProperties();
    var rentcastKey = props.getProperty('RENTCAST_API_KEY');
    if (!rentcastKey) throw new Error('RentCast API key missing. Use Lead Tracker > 2. Set API keys.');

    var quota = new RequestQuota_(props, settings.monthlyLimit, new Date());
    var now = new Date();
    var fetched = [];

    settings.zips.forEach(function (zip) {
      // Each lookup is independent: if one fails, keep the other's leads.
      var sold = [], listings = [];
      try {
        sold = rentcastFetchAll_(rentcastKey, '/properties', {
          zipCode: zip,
          propertyType: settings.propertyType,
          saleDateRange: '*:' + settings.soldDays
        }, quota, stats);
      } catch (e) {
        messages.push(zip + ' sold: ' + e.message);
      }
      try {
        // Listings are only searchable by listed date (daysOld). A 1-year window times out,
        // and homes that sell rarely sit 150+ days, so look back pendingDays + 120 (min 180).
        listings = rentcastFetchAll_(rentcastKey, '/listings/sale', {
          zipCode: zip,
          propertyType: settings.propertyType,
          status: 'Inactive',
          price: '*:' + settings.maxPrice,
          daysOld: '*:' + Math.max(180, settings.pendingDays + 120)
        }, quota, stats);
      } catch (e) {
        messages.push(zip + ' pending: ' + e.message);
      }
      fetched = fetched.concat(buildLeads(sold, listings, settings, now));
    });

    var leadsSheet = ss.getSheetByName(SHEET_LEADS);
    if (!leadsSheet) throw new Error('Run Lead Tracker > 1. Set up sheet first.');
    var existing = readLeads_(leadsSheet);
    var merged = mergeLeads(existing, fetched, now);
    stats.added = merged.added;
    stats.updated = merged.updated;

    // Skip trace sold homes that haven't been traced yet
    var tracerfyKey = props.getProperty('TRACERFY_API_KEY');
    if (settings.skipTrace && tracerfyKey) {
      var toTrace = merged.rows.filter(function (r) {
        return r['Status'] === STATUS_SOLD && !r['Skip Traced On'] && r._addressLine1;
      }).slice(0, settings.maxTraces);
      if (toTrace.length) {
        var res = skipTrace_(tracerfyKey, toTrace, now);
        stats.traced = res.traced; stats.hits = res.hits;
        if (res.error) messages.push('Skip trace: ' + res.error);
      }
    } else if (settings.skipTrace && !tracerfyKey) {
      messages.push('Skip trace on, but no Tracerfy key set.');
    }

    writeLeads_(leadsSheet, merged.rows);
    if (quota.exhausted) messages.push('Stopped early: monthly RentCast request limit reached (' + settings.monthlyLimit + ').');
    logRun_(ss, now, stats, quota.used(), messages);
  } catch (e) {
    messages.push(e.message);
    logRun_(SpreadsheetApp.getActive(), new Date(), stats, null, messages);
    throw e;
  } finally {
    lock.releaseLock();
  }
}

// ---------------------------------------------------------------------------
// Pure logic (no Google services) — unit tested in test/run_tests.js
// ---------------------------------------------------------------------------

/**
 * Turn RentCast property records (recent sales) and inactive sale listings into lead rows.
 * @param {Object[]} soldProps  /properties results
 * @param {Object[]} listings   /listings/sale results (status Inactive)
 * @param {Object} settings     {maxPrice, soldDays, pendingDays}
 * @param {Date} now
 * @return {Object[]} lead objects keyed by LEAD_COLUMNS (+ private _fields)
 */
function buildLeads(soldProps, listings, settings, now) {
  var leads = {};
  var listingById = {};
  (listings || []).forEach(function (l) { if (l && l.id) listingById[l.id] = l; });

  (soldProps || []).forEach(function (p) {
    if (!p || !p.id || !p.lastSaleDate) return;
    if (p.propertyType && settings.propertyType && p.propertyType !== settings.propertyType) return;
    if (daysBetween_(new Date(p.lastSaleDate), now) > settings.soldDays) return;
    // Unknown price is kept (could be a good lead); known price must be within the cap.
    if (isNum_(p.lastSalePrice) && p.lastSalePrice > settings.maxPrice) return;
    var lead = baseLead_(p);
    lead['Status'] = STATUS_SOLD;
    lead['Sale Date'] = isoDate_(p.lastSaleDate);
    lead['Sale Price'] = isNum_(p.lastSalePrice) ? p.lastSalePrice : '';
    var owner = p.owner || {};
    lead['Owner Name(s)'] = (owner.names || []).join('; ');
    lead['Owner Type'] = owner.type || '';
    lead['Owner Occupied'] = p.ownerOccupied === true ? 'Yes' : (p.ownerOccupied === false ? 'No' : '');
    lead['Owner Mailing Address'] = (owner.mailingAddress && owner.mailingAddress.formattedAddress) || '';
    lead._ownerNames = owner.names || [];
    lead._ownerType = owner.type || '';
    lead['Free Lookup'] = truePeopleSearchLink(lead);
    var l = listingById[p.id];
    if (l && (!l.listedDate || new Date(l.listedDate) <= new Date(p.lastSaleDate))) addListingInfo_(lead, l);
    leads[p.id] = lead;
  });

  (listings || []).forEach(function (l) {
    if (!l || !l.id || leads[l.id]) return;
    if (l.status && l.status !== 'Inactive') return;
    if (!l.removedDate) return;
    if (daysBetween_(new Date(l.removedDate), now) > settings.pendingDays) return;
    if (isNum_(l.price) && l.price > settings.maxPrice) return;
    if (l.propertyType && settings.propertyType && l.propertyType !== settings.propertyType) return;
    var lead = baseLead_(l);
    lead['Status'] = STATUS_PENDING;
    addListingInfo_(lead, l);
    leads[l.id] = lead;
  });

  return Object.keys(leads).map(function (k) { return leads[k]; });
}

/**
 * Merge freshly fetched leads into existing sheet rows.
 * - New property -> appended, First Found = today, Outreach Status = New
 * - Existing property -> script columns refreshed (blank fetched values never erase
 *   existing data), manual columns and skip-trace results kept
 * - Pending -> Sold transition picks up the owner info and gets skip traced
 */
function mergeLeads(existingRows, fetched, now) {
  var byId = {};
  existingRows.forEach(function (r) { byId[r['Property ID']] = r; });
  var added = 0, updated = 0;
  var today = isoDate_(now);
  var keep = { 'Property ID': 1, 'First Found': 1, 'Skip Traced On': 1,
    'Phone 1': 1, 'Phone 1 Type': 1, 'Phone 1 DNC': 1, 'Phone 2': 1, 'Phone 2 Type': 1,
    'Phone 2 DNC': 1, 'Email 1': 1, 'Email 2': 1, 'Litigator Flag': 1 };

  fetched.forEach(function (f) {
    var row = byId[f['Property ID']];
    if (!row) {
      row = {};
      ALL_COLUMNS.forEach(function (c) { row[c] = f[c] !== undefined ? f[c] : ''; });
      row['First Found'] = today;
      row['Last Updated'] = today;
      row['Outreach Status'] = 'New';
      copyPrivate_(f, row);
      existingRows.push(row);
      byId[row['Property ID']] = row;
      added++;
      return;
    }
    // Never downgrade a Sold row back to Pending.
    if (row['Status'] === STATUS_SOLD && f['Status'] === STATUS_PENDING) return;
    var changed = false;
    LEAD_COLUMNS.forEach(function (c) {
      if (keep[c]) return;
      var v = f[c];
      if (v === undefined || v === '' || v === null) return;
      if (c === 'Last Updated') return;
      if (String(row[c]) !== String(v)) { row[c] = v; changed = true; }
    });
    copyPrivate_(f, row);
    if (changed) { row['Last Updated'] = today; updated++; }
  });
  return { rows: existingRows, added: added, updated: updated };
}

/**
 * Build Tracerfy request bodies. Individuals with a known name -> search that person at
 * the address (targets the NEW owner, not the seller). Trusts/companies -> find owner.
 */
function buildTraceRequest(row) {
  var body = { address: row._addressLine1, city: row._city, state: row._state, zip: row._zip };
  var name = pickPersonName((row._ownerNames && row._ownerNames.length) ? row._ownerNames : splitNames_(row['Owner Name(s)']),
    row._ownerType || row['Owner Type']);
  if (name) {
    body.find_owner = false;
    body.first_name = name.first;
    body.last_name = name.last;
  } else {
    body.find_owner = true;
  }
  return body;
}

/**
 * Extract one person's first/last name from owner names. Handles county format
 * ("LAST, FIRST M & SPOUSE", "WEBB, WILLIS L LIVING TRUST") and plain "First Last".
 * Companies return null (search by address instead). ownerType is unused: LA records
 * often leave it blank and list trusts as organizations even when a person is named.
 */
function pickPersonName(names, ownerType) {
  if (!names || !names.length) return null;
  var company = /\b(llc|l\.l\.c|inc|corp|corporation|company|ltd|lp|llp|bank|partners|partnership|properties|holdings|investments?|ventures|group|association|church|city|county|state)\b/i;
  var trustWords = /\b(the|revocable|irrevocable|living|family|survivors?|separate|property|trusts?|trustees?|tr|tste|et\s+al|etal|estate\s+of|dated|dtd|u\/?d\/?t|agreement|declaration|of)\b/gi;
  for (var i = 0; i < names.length; i++) {
    var n = String(names[i]).trim();
    if (!n || company.test(n)) continue;
    n = n.replace(/\d[\d\/-]*/g, ' ').replace(trustWords, ' ').replace(/\s+/g, ' ').trim();
    var first, last;
    if (n.indexOf(',') >= 0) {
      last = n.split(',')[0].trim();
      var rest = n.split(',').slice(1).join(' ').split(/\s*&\s*|\s+and\s+/i)[0].trim();
      first = rest.split(/\s+/)[0];
    } else {
      var parts = n.split(/\s*&\s*|\s+and\s+/i)[0].replace(/\./g, ' ').split(/\s+/).filter(Boolean);
      if (parts.length > 2 && /^(jr|sr|ii|iii|iv)$/i.test(parts[parts.length - 1])) parts.pop();
      if (parts.length < 2) continue;
      if (parts.length >= 3 && /^[a-z]$/i.test(parts[parts.length - 1]) && !/^[a-z]$/i.test(parts[1])) {
        // County style without comma: "CAIDEN GERALD E" = LAST FIRST MIDDLE-INITIAL
        first = parts[1]; last = parts[0];
      } else {
        first = parts[0]; last = parts[parts.length - 1];
      }
    }
    last = (last || '').split(/\s+/).filter(function (w) { return !/^(jr|sr|ii|iii|iv)$/i.test(w); }).join(' ');
    if (!first || !last || first.length < 2) continue;
    return { first: titleCase_(first), last: titleCase_(last) };
  }
  return null;
}

function titleCase_(s) {
  return String(s).toLowerCase().replace(/(^|[\s'-])([a-z])/g, function (m, p, c) { return p + c.toUpperCase(); });
}

/** Pull best phones/emails out of one Tracerfy lookup result. */
function parseTraceResult(result) {
  var out = { hit: false, phones: [], emails: [], litigator: false };
  if (!result || !result.hit || !result.persons || !result.persons.length) return out;
  out.hit = true;
  var persons = result.persons.filter(function (p) { return !p.deceased; });
  if (!persons.length) persons = result.persons;
  persons.sort(function (a, b) { return (b.property_owner ? 1 : 0) - (a.property_owner ? 1 : 0); });
  persons.forEach(function (p) {
    if (p.litigator) out.litigator = true;
    (p.phones || []).slice().sort(byRank_).forEach(function (ph) {
      if (ph.number && !out.phones.some(function (x) { return x.number === ph.number; })) out.phones.push(ph);
    });
    (p.emails || []).slice().sort(byRank_).forEach(function (em) {
      if (em.email && out.emails.indexOf(em.email) < 0) out.emails.push(em.email);
    });
  });
  // Prefer callable numbers first, keep DNC numbers after (flagged)
  out.phones.sort(function (a, b) { return (a.dnc ? 1 : 0) - (b.dnc ? 1 : 0); });
  return out;
}

function applyTrace(row, parsed, now) {
  var p1 = parsed.phones[0], p2 = parsed.phones[1];
  row['Phone 1'] = p1 ? formatPhone_(p1.number) : '';
  row['Phone 1 Type'] = p1 ? (p1.type || '') : '';
  row['Phone 1 DNC'] = p1 ? (p1.dnc ? 'YES - DNC' : 'No') : '';
  row['Phone 2'] = p2 ? formatPhone_(p2.number) : '';
  row['Phone 2 Type'] = p2 ? (p2.type || '') : '';
  row['Phone 2 DNC'] = p2 ? (p2.dnc ? 'YES - DNC' : 'No') : '';
  row['Email 1'] = parsed.emails[0] || '';
  row['Email 2'] = parsed.emails[1] || '';
  row['Litigator Flag'] = parsed.litigator ? 'YES - do not call' : (parsed.hit ? 'No' : '');
  row['Skip Traced On'] = parsed.hit ? isoDate_(now) : isoDate_(now) + ' (no match)';
  row['Last Updated'] = isoDate_(now);
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

function RequestQuota_(props, limit, now) {
  var month = now.getFullYear() + '-' + (now.getMonth() + 1);
  var key = 'RENTCAST_USED_' + month;
  this.limit = limit;
  this.count = Number(props.getProperty(key) || 0);
  this.exhausted = false;
  this.take = function () {
    if (this.count >= this.limit) { this.exhausted = true; return false; }
    this.count++;
    props.setProperty(key, String(this.count));
    return true;
  };
  this.used = function () { return this.count; };
}

function rentcastFetchAll_(apiKey, path, params, quota, stats) {
  var all = [];
  var limit = 500;
  for (var offset = 0; offset < 5000; offset += limit) {
    if (!quota.take()) break;
    var q = Object.keys(params).map(function (k) {
      return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
    }).join('&') + '&limit=' + limit + '&offset=' + offset;
    var resp = fetchWithRetry_(RENTCAST_BASE + path + '?' + q, {
      method: 'get',
      headers: { 'X-Api-Key': apiKey, 'Accept': 'application/json' },
      muteHttpExceptions: true
    });
    stats.requests++;
    var code = resp.getResponseCode();
    if (code === 404) break; // no results
    if (code !== 200) throw new Error('RentCast ' + path + ' HTTP ' + code + ': ' + resp.getContentText().slice(0, 200));
    var page = JSON.parse(resp.getContentText());
    if (!Array.isArray(page)) page = page ? [page] : [];
    all = all.concat(page);
    if (page.length < limit) break;
  }
  return all;
}

/** UrlFetchApp throws on timeouts; retry once (and once on 429/5xx) after a short pause. */
function fetchWithRetry_(url, opts) {
  for (var attempt = 1; ; attempt++) {
    try {
      var resp = UrlFetchApp.fetch(url, opts);
      var code = resp.getResponseCode();
      if ((code === 429 || code >= 500) && attempt < 2) { Utilities.sleep(3000); continue; }
      return resp;
    } catch (e) {
      if (attempt >= 2) throw new Error(String(e.message || e).replace(/apiKey=[^&]+/g, ''));
      Utilities.sleep(3000);
    }
  }
}

function skipTrace_(apiKey, rows, now) {
  var traced = 0, hits = 0;
  for (var i = 0; i < rows.length; i += TRACERFY_BATCH_MAX) {
    var chunk = rows.slice(i, i + TRACERFY_BATCH_MAX);
    var bodies = chunk.map(buildTraceRequest);
    var resp = UrlFetchApp.fetch(TRACERFY_LOOKUP, {
      method: 'post',
      contentType: 'application/json',
      headers: { 'Authorization': 'Bearer ' + apiKey, 'User-Agent': 'ACG-LeadTracker/1.0' },
      payload: JSON.stringify(bodies),
      muteHttpExceptions: true
    });
    var code = resp.getResponseCode();
    if (code === 402) return { traced: traced, hits: hits, error: 'Out of Tracerfy credits.' };
    if (code !== 200) return { traced: traced, hits: hits, error: 'HTTP ' + code + ': ' + resp.getContentText().slice(0, 200) };
    var data = JSON.parse(resp.getContentText());
    var results = Array.isArray(data) ? data : (data.results || [data]);
    chunk.forEach(function (row, j) {
      var parsed = parseTraceResult(results[j]);
      // Named search missed (e.g. new owner not in their data yet) -> leave for a later run,
      // unless it's been 30+ days since the sale.
      if (!parsed.hit && bodies[j].find_owner === false && daysBetween_(new Date(row['Sale Date']), now) < 30) {
        traced++;
        return;
      }
      applyTrace(row, parsed, now);
      traced++;
      if (parsed.hit) hits++;
    });
  }
  return { traced: traced, hits: hits };
}

// ---------------------------------------------------------------------------
// Sheet I/O
// ---------------------------------------------------------------------------

function readSettings_(ss) {
  var sh = ss.getSheetByName(SHEET_SETTINGS);
  if (!sh) throw new Error('Run Lead Tracker > 1. Set up sheet first.');
  var map = {};
  sh.getDataRange().getValues().forEach(function (r) { map[String(r[0]).trim()] = r[1]; });
  return parseSettings(map);
}

function parseSettings(map) {
  var zips = String(map['Zip Codes'] || '').split(/[\s,;]+/).filter(function (z) { return /^\d{5}$/.test(z); });
  if (!zips.length) throw new Error('No valid zip codes on the Settings tab.');
  return {
    zips: zips,
    maxPrice: Number(map['Max Price']) || 2000000,
    soldDays: Number(map['Sold Lookback (days)']) || 180,
    pendingDays: Number(map['Pending Lookback (days)']) || 60,
    propertyType: String(map['Property Type'] || 'Single Family').trim(),
    monthlyLimit: Number(map['RentCast Monthly Request Limit']) || 50,
    skipTrace: String(map['Skip Trace Enabled']).toUpperCase() === 'TRUE',
    maxTraces: Number(map['Max Skip Traces Per Run']) || 40
  };
}

function readLeads_(sheet) {
  var range = sheet.getDataRange();
  var values = range.getValues();
  if (values.length < 2) return [];
  var formulas = range.getFormulas(); // keep HYPERLINK formulas instead of their display text
  var header = values[0];
  var meta = readMeta_(sheet.getParent());
  return values.slice(1).map(function (r, idx) { return { r: r, f: formulas[idx + 1] }; })
    .filter(function (x) { return x.r[0]; }).map(function (x) {
    var r = x.r;
    var row = {};
    header.forEach(function (h, i) {
      var v = x.f[i] ? x.f[i] : r[i];
      if (v instanceof Date) v = isoDate_(v);
      row[h] = v;
    });
    var m = meta[row['Property ID']];
    if (m) { row._addressLine1 = m.a; row._city = m.c; row._state = m.s; row._zip = m.z; row._ownerNames = m.n; row._ownerType = m.t; }
    return row;
  });
}

function writeLeads_(sheet, rows) {
  // Header might have extra user-added columns; write by header name.
  var header = sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0];
  rows.sort(function (a, b) {
    var da = a['Sale Date'] || a['Off Market Date'] || '';
    var db = b['Sale Date'] || b['Off Market Date'] || '';
    return String(db).localeCompare(String(da));
  });
  var existingValues = sheet.getLastRow() > 1
    ? sheet.getRange(2, 1, sheet.getLastRow() - 1, header.length).getValues() : [];
  var extraById = {};
  // Preserve any user-added columns by Property ID
  existingValues.forEach(function (r) { extraById[r[0]] = r; });
  var out = rows.map(function (row) {
    var old = extraById[row['Property ID']];
    return header.map(function (h, i) {
      if (ALL_COLUMNS.indexOf(h) >= 0) return row[h] !== undefined ? row[h] : '';
      return old ? old[i] : '';
    });
  });
  if (sheet.getLastRow() > 1) sheet.getRange(2, 1, sheet.getLastRow() - 1, header.length).clearContent();
  if (out.length) sheet.getRange(2, 1, out.length, header.length).setValues(out);

  writeMeta_(sheet.getParent(), rows);
}

// Per-lead lookup details (street, city, raw owner names) kept on a hidden tab.
// (Document properties cap at 9KB, which ~70 leads would exceed.)
var SHEET_META = '_data';

function readMeta_(ss) {
  var meta = {};
  var sh = ss.getSheetByName(SHEET_META);
  if (!sh || sh.getLastRow() < 1) return meta;
  sh.getRange(1, 1, sh.getLastRow(), 2).getValues().forEach(function (r) {
    if (!r[0]) return;
    try { meta[r[0]] = JSON.parse(r[1]); } catch (e) {}
  });
  return meta;
}

function writeMeta_(ss, rows) {
  var sh = ss.getSheetByName(SHEET_META);
  if (!sh) { sh = ss.insertSheet(SHEET_META); sh.hideSheet(); }
  sh.clearContents();
  var out = rows.map(function (r) {
    return [r['Property ID'], JSON.stringify({ a: r._addressLine1, c: r._city, s: r._state, z: r._zip, n: r._ownerNames, t: r._ownerType })];
  });
  if (out.length) sh.getRange(1, 1, out.length, 2).setValues(out);
}

function logRun_(ss, now, stats, monthUsed, messages) {
  var log = ss.getSheetByName(SHEET_LOG);
  if (!log) return;
  log.appendRow([now, stats.requests, stats.added, stats.updated,
    stats.traced + ' (' + stats.hits + ')', monthUsed === null ? '' : monthUsed, messages.join(' | ')]);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function baseLead_(r) {
  var lead = {};
  LEAD_COLUMNS.forEach(function (c) { lead[c] = ''; });
  lead['Property ID'] = r.id;
  lead['Address'] = r.formattedAddress || '';
  lead['Zip'] = r.zipCode || '';
  lead['Beds'] = valOr_(r.bedrooms);
  lead['Baths'] = valOr_(r.bathrooms);
  lead['Sq Ft'] = valOr_(r.squareFootage);
  lead['Lot Sq Ft'] = valOr_(r.lotSize);
  lead['Year Built'] = valOr_(r.yearBuilt);
  lead._addressLine1 = [r.addressLine1, r.addressLine2].filter(Boolean).join(' ');
  lead._city = r.city || '';
  lead._state = r.state || '';
  lead._zip = r.zipCode || '';
  return lead;
}

function addListingInfo_(lead, l) {
  lead['List Price'] = valOr_(l.price);
  lead['Listed Date'] = l.listedDate ? isoDate_(l.listedDate) : '';
  lead['Off Market Date'] = l.removedDate ? isoDate_(l.removedDate) : '';
  lead['Days on Market'] = valOr_(l.daysOnMarket);
  var a = l.listingAgent || {}, o = l.listingOffice || {};
  lead['Listing Agent'] = a.name || '';
  lead['Agent Phone'] = formatPhone_(a.phone);
  lead['Agent Email'] = a.email || '';
  lead['Listing Office'] = o.name || '';
  lead['Office Phone'] = formatPhone_(o.phone);
  lead['MLS #'] = l.mlsNumber || '';
  ['Beds', 'Baths', 'Sq Ft', 'Lot Sq Ft', 'Year Built'].forEach(function (c, i) {
    var key = ['bedrooms', 'bathrooms', 'squareFootage', 'lotSize', 'yearBuilt'][i];
    if (lead[c] === '' && isNum_(l[key])) lead[c] = l[key];
  });
}

function copyPrivate_(from, to) {
  ['_addressLine1', '_city', '_state', '_zip', '_ownerNames', '_ownerType'].forEach(function (k) {
    if (from[k] !== undefined && from[k] !== '' && !(Array.isArray(from[k]) && !from[k].length)) to[k] = from[k];
  });
}

/**
 * One-click manual lookup on TruePeopleSearch (free). Searches the owner's name in the
 * property's city/zip when we have an individual's name, otherwise searches the address.
 * Opened by a person in the browser — the script never visits the site.
 */
function truePeopleSearchLink(lead) {
  var cityStateZip = [lead._city, [lead._state, lead._zip].filter(Boolean).join(' ')].filter(Boolean).join(', ');
  var name = pickPersonName(lead._ownerNames, lead._ownerType);
  var url = name
    ? 'https://www.truepeoplesearch.com/resultname?name=' + encodeURIComponent(name.first + ' ' + name.last) +
      '&citystatezip=' + encodeURIComponent(cityStateZip)
    : 'https://www.truepeoplesearch.com/resultaddress?streetaddress=' + encodeURIComponent(lead._addressLine1 || '') +
      '&citystatezip=' + encodeURIComponent(cityStateZip);
  return '=HYPERLINK("' + url.replace(/"/g, '%22') + '","Look up owner")';
}

function splitNames_(s) { return s ? String(s).split(/\s*;\s*/).filter(Boolean) : []; }
function byRank_(a, b) { return (a.rank || 99) - (b.rank || 99); }
function isNum_(v) { return typeof v === 'number' && !isNaN(v); }
function valOr_(v) { return v === undefined || v === null ? '' : v; }
function isoDate_(d) {
  var dt = d instanceof Date ? d : new Date(d);
  if (isNaN(dt.getTime())) return '';
  return dt.toISOString().slice(0, 10);
}
function daysBetween_(a, b) { return Math.floor((b.getTime() - a.getTime()) / 86400000); }
function formatPhone_(p) {
  if (!p) return '';
  var d = String(p).replace(/\D/g, '');
  if (d.length === 11 && d[0] === '1') d = d.slice(1);
  return d.length === 10 ? '(' + d.slice(0, 3) + ') ' + d.slice(3, 6) + '-' + d.slice(6) : String(p);
}
function columnLetter_(n) {
  var s = '';
  while (n > 0) { var m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26); }
  return s;
}

// Node test hook (ignored by Apps Script)
if (typeof module !== 'undefined') {
  module.exports = { buildLeads: buildLeads, mergeLeads: mergeLeads, buildTraceRequest: buildTraceRequest,
    pickPersonName: pickPersonName, parseTraceResult: parseTraceResult, applyTrace: applyTrace,
    parseSettings: parseSettings, truePeopleSearchLink: truePeopleSearchLink, STATUS_SOLD: STATUS_SOLD, STATUS_PENDING: STATUS_PENDING,
    ALL_COLUMNS: ALL_COLUMNS, formatPhone_: formatPhone_, columnLetter_: columnLetter_ };
}
