# Home Sale Lead Tracker (Aggregate Construction Group)

A Google Sheet that updates itself every weekday with single-family homes in your zip codes that are:

- **Sold**: recorded sale in the last 6 months, at or under your max price (default $2M). Includes the new owner's name, mailing address and whether they live there.
- **Pending (off market)**: listed homes that came off the market recently with no sale recorded yet. Most are in escrow. Includes the listing agent's phone and email.

Each sold row has a **"Look up owner"** link that opens a TruePeopleSearch search pre-filled with the owner's name and city (free, done by hand). If you want phones and emails filled in automatically, turn on the paid lookup (Tracerfy, about $0.10 per match, no charge on a miss). Phones come back with Do Not Call and litigator flags.

## Cost

| Item | Cost |
|---|---|
| Google Sheet + Apps Script | Free |
| GitHub Actions (private repo) | Free (uses about 1 of the 2,000 free minutes per month per run) |
| RentCast API, Developer plan | Free for 50 requests/month. 1 zip ≈ 2 requests per run ≈ 44/month on weekdays |
| TruePeopleSearch link | Free (manual) |
| Tracerfy auto-lookup (optional, off by default) | About $0.10 per match, no charge on a miss |

Adding more zip codes means more requests. Past the free 50/month you'll need a paid RentCast plan. The script stops at the monthly limit you set, so you won't get surprise overage charges.

## How it runs

RentCast refuses connections from Google's servers, so the sheet can't fetch the data itself. Instead:

```
GitHub Actions (weekdays 7am PT) ──► RentCast API
        │  reads zip codes / price cap from the sheet, then posts the results
        ▼
Google Sheet web app (Code.gs doGet/doPost) ──► builds, merges and writes leads
```

## Setup

**Google Sheet**
1. Go to <https://sheets.new>, then **Extensions → Apps Script**. Replace the sample code with [`apps-script/Code.gs`](apps-script/Code.gs) and click **Save**.
2. Reload the sheet. Click **Lead Tracker → 1. Set up sheet** and approve the permissions prompt.
3. In Apps Script, click **Deploy → New deployment**. Click the gear icon and pick **Web app**. Set Execute as **Me** and Who has access **Anyone**, then click **Deploy**.
4. In the sheet, click **Lead Tracker → 2. Show GitHub sync link** and copy the link. Keep it private: anyone with it can send data to the sheet.

**GitHub** (this repo → Settings → Secrets and variables → Actions → New repository secret)

5. `RENTCAST_API_KEY`: your RentCast key.
6. `SHEET_SYNC_URL`: the link from step 4.
7. Go to **Actions → Daily lead update → Run workflow** to run it once now. After that it runs every weekday automatically.

If you change `Code.gs` later, update the existing deployment instead of creating a new one: **Deploy → Manage deployments → ✏️ → Version: New version**. That keeps the same link.

## Settings tab

| Setting | Default |
|---|---|
| Zip Codes | `90064` (add more separated by commas) |
| Max Price | `2000000` |
| Sold Lookback (days) | `180` (6 months) |
| Pending Lookback (days) | `60` |
| RentCast Monthly Request Limit | `50` (match your RentCast plan) |
| Skip Trace Enabled | `FALSE` (set `TRUE` for the paid auto phone/email lookup) |
| Max Skip Traces Per Run | `40` (caps paid lookups per run) |

## Leads tab

- Leads found in the last 3 days are highlighted yellow.
- **Outreach Status** (dropdown) and **Notes** are for your team. The script never overwrites them.
- When a pending home's sale records, its row switches to **Sold** and picks up the owner info automatically.
- The **Log** tab shows each run: requests used, new leads and any errors.

## Things to know

- **"Pending" is inferred.** RentCast only reports whether a listing is on or off the market. A listing that went off market with no sale recorded is usually in escrow, but it can also be a withdrawn or expired listing. The agent can confirm.
- **The buyer's name isn't public until the sale records.** For pending homes, reach out through the listing agent.
- **Sold data runs about 3 months behind.** County records are slow. In the Nov 2026 test, the newest recorded 90064 sale was from August. Pending homes are the freshest leads, and they become Sold rows once the deed records.
- **Owners held in trusts or LLCs** are common in LA. For those, the lookup link searches by address instead of name.
- **Compliance:** Don't cold-call or text numbers flagged DNC or litigator. Email outreach must follow CAN-SPAM: identify your company, include a physical address and an unsubscribe option.

## Tests

```bash
node test/run_tests.js            # logic tests with mock data
node test/e2e_mock.js             # direct mode against a fake sheet + fake APIs
node test/github_sync_test.mjs    # GitHub fetcher -> fake web app -> fake sheet
```
