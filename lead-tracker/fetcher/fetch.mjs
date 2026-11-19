// Runs in GitHub Actions (Node 20+). RentCast refuses connections from Google's servers,
// so this fetches RentCast and posts the raw results to the Google Sheet's web app,
// which does all the lead logic (Code.gs: doGet / doPost).
//
// Env:
//   RENTCAST_API_KEY  RentCast key
//   SHEET_SYNC_URL    Web app link from Lead Tracker > Show GitHub sync link (includes ?token=)
//   RENTCAST_BASE     optional override (tests)
//
// Logs only counts, never names/addresses (Actions logs are kept by GitHub).

const RENTCAST_BASE = process.env.RENTCAST_BASE || 'https://api.rentcast.io/v1';
const KEY = process.env.RENTCAST_API_KEY;
const SHEET = process.env.SHEET_SYNC_URL;
const PAGE = 500;

const SOLD_FIELDS = ['id', 'formattedAddress', 'addressLine1', 'addressLine2', 'city', 'state', 'zipCode',
  'propertyType', 'bedrooms', 'bathrooms', 'squareFootage', 'lotSize', 'yearBuilt',
  'lastSaleDate', 'lastSalePrice', 'owner', 'ownerOccupied'];
const LISTING_FIELDS = ['id', 'formattedAddress', 'addressLine1', 'addressLine2', 'city', 'state', 'zipCode',
  'propertyType', 'bedrooms', 'bathrooms', 'squareFootage', 'lotSize', 'yearBuilt',
  'status', 'price', 'listedDate', 'removedDate', 'daysOnMarket', 'mlsNumber', 'listingAgent', 'listingOffice'];

const pick = (o, keys) => Object.fromEntries(keys.filter(k => o[k] !== undefined).map(k => [k, o[k]]));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const withParam = (url, k, v) => url + (url.includes('?') ? '&' : '?') + k + '=' + encodeURIComponent(v);

async function fetchRetry(url, opts = {}, tries = 3) {
  for (let i = 1; ; i++) {
    try {
      const res = await fetch(url, { ...opts, signal: AbortSignal.timeout(90_000) });
      if ((res.status === 429 || res.status >= 500) && i < tries) { await sleep(3000 * i); continue; }
      return res;
    } catch (e) {
      if (i >= tries) throw e;
      await sleep(3000 * i);
    }
  }
}

async function main() {
  if (!KEY || !SHEET) throw new Error('Missing RENTCAST_API_KEY or SHEET_SYNC_URL secret.');

  const cfgRes = await fetchRetry(withParam(SHEET, 'action', 'config'));
  const cfgText = await cfgRes.text();
  let cfg;
  try { cfg = JSON.parse(cfgText); } catch { throw new Error(`Sheet did not return JSON (HTTP ${cfgRes.status}). Is the web app deployed with access "Anyone"?`); }
  if (!cfg.ok) throw new Error('Sheet refused config request: ' + cfg.error);

  let remaining = cfg.remainingRequests;
  let requests = 0;
  const messages = [];

  async function fetchAll(path, query, fields) {
    const all = [];
    for (let offset = 0; offset < 5000; offset += PAGE) {
      if (remaining <= 0) { messages.push(`Monthly RentCast limit (${cfg.monthlyLimit}) reached; skipped remaining lookups.`); break; }
      const qs = new URLSearchParams({ ...query, limit: PAGE, offset }).toString();
      const res = await fetchRetry(`${RENTCAST_BASE}${path}?${qs}`, { headers: { 'X-Api-Key': KEY, Accept: 'application/json' } });
      if (res.status === 200) { requests++; remaining--; }
      if (res.status === 404) break;
      if (res.status !== 200) throw new Error(`RentCast ${path} HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
      const page = await res.json();
      const arr = Array.isArray(page) ? page : (page ? [page] : []);
      all.push(...arr.map(o => pick(o, fields)));
      if (arr.length < PAGE) break;
    }
    return all;
  }

  const results = [];
  for (const z of cfg.zips) {
    const item = { zip: z.zip, sold: [], listings: [] };
    try { item.sold = await fetchAll('/properties', z.sold, SOLD_FIELDS); }
    catch (e) { messages.push(`${z.zip} sold: ${e.message}`); }
    try { item.listings = await fetchAll('/listings/sale', z.pending, LISTING_FIELDS); }
    catch (e) { messages.push(`${z.zip} pending: ${e.message}`); }
    console.log(`${z.zip}: ${item.sold.length} sale records, ${item.listings.length} inactive listings`);
    results.push(item);
  }

  const postRes = await fetchRetry(SHEET, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ requests, results, messages }),
    redirect: 'follow'
  }, 2);
  const postText = await postRes.text();
  let out;
  try { out = JSON.parse(postText); } catch { throw new Error(`Sheet did not return JSON after upload (HTTP ${postRes.status}).`); }
  if (!out.ok) throw new Error('Sheet rejected upload: ' + out.error);

  console.log(`RentCast requests used: ${requests}. Sheet: ${out.added} new, ${out.updated} updated, ${out.traced} skip traced (${out.hits} hits).`);
  messages.forEach(m => console.log('note:', m.replace(/https?:\/\/\S+/g, '[url]')));
  if (messages.some(m => /sold:|pending:/.test(m))) process.exitCode = 1; // GitHub emails you on failure
}

main().catch(e => { console.error('FAILED:', e.message); process.exit(1); });
