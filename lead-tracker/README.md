# Home Sale Lead Tracker (Aggregate Construction Group)

A Google Sheet that updates itself every weekday with single-family homes in your zip codes that are:

- **Sold**: recorded sale in the last 6 months, at or under your max price (default $2M). Includes the new owner's name, mailing address and whether they live there.
- **Pending (off market)**: listed homes that came off the market recently with no sale recorded yet. Most are in escrow. Includes the listing agent's phone and email.

Each sold row has a **"Look up owner"** link that opens a TruePeopleSearch search pre-filled with the owner's name and city (free, done by hand). If you want phones and emails filled in automatically, turn on the paid lookup (Tracerfy, about $0.10 per match, no charge on a miss). Phones come back with Do Not Call and litigator flags.

## Cost

| Item | Cost |
|---|---|
| Google Sheet + Apps Script | Free |
| RentCast API, Developer plan | Free for 50 requests/month. 1 zip ≈ 2 requests per run ≈ 44/month on weekdays |
| TruePeopleSearch link | Free (manual) |
| Tracerfy auto-lookup (optional, off by default) | About $0.10 per match, no charge on a miss |

Adding more zip codes means more requests. Past the free 50/month you'll need a paid RentCast plan. The script stops at the monthly limit you set, so you won't get surprise overage charges.

## Setup (about 15 minutes)

1. **RentCast key:** Sign up at <https://app.rentcast.io/app/api>, pick the free Developer plan and create an API key.
2. **(Optional) Tracerfy key:** Sign up at <https://tracerfy.com>. Account settings → API key.
3. **Create the sheet:** Go to <https://sheets.new>, then **Extensions → Apps Script**.
4. Delete the sample code, paste in all of [`Code.gs`](Code.gs) and click **Save**.
5. Reload the Google Sheet. A **Lead Tracker** menu appears.
6. **Lead Tracker → 1. Set up sheet.** Approve the Google permissions prompt. It creates the `Leads`, `Settings` and `Log` tabs.
7. **Lead Tracker → 2. Set API keys.** Paste the keys. They're stored privately in the script, not in the sheet.
8. **Lead Tracker → 3. Run now.** The first run backfills the last 6 months.
9. **Lead Tracker → Turn on weekday auto-update (7am).**

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
node lead-tracker/test/run_tests.js   # logic tests with mock data
node lead-tracker/test/e2e_mock.js    # full run against a fake sheet + fake APIs
```
