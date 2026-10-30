# Changelog

## v2.2.4 — the source checked against the Telegram Ads API documentation

- **Replacement ads reach the same audience.** A replacement used to copy only the listed channels or bots. Ads targeting channels by language or topic, excluded channels or topics, user demographics or search queries were refused, and a channel list lost its exclusions. Telegram's whole target is now copied into the new ad: languages, topics (included, excluded, "all topics"), channels (included, excluded; @username, or the numeric id this account already uses), countries, locations, audiences (included, excluded), device, the political-channel options and search queries. A replacement is still refused when part of the target cannot be named, so the audience never changes.
- **Replacement ads keep the old photo or video and settings.** Before, a replacement without a new photo was created with no picture. When the replacement is created, the old ad is read from Telegram, and its photo or video carries over (a new photo still wins), along with impressions per user, daily budget limit, hour schedule, channel picture, website name, website photo, button, extra info, conversion pixel and a future start or stop date. These are saved on the job before `createAd`, so a retry sends exactly the same request under its idempotency key.
- **Money follows the account currency's precision.** Telegram rounds amounts: 5 decimals for TON/EUR and 3 for Stars, and budgets to 2 decimals or whole Stars. Withdrawals, top-ups and budget transfers are now rounded down to the account's precision, and so are the initial budget and daily limit of new ads. A Stars account can no longer have 1.6 rounded up to 2 and refused for lack of balance.
- `CHANNEL_ID_UNKNOWN` / `BOT_ID_UNKNOWN`, for a numeric id this account never resolved, now show a clear message asking for the @username.
- The fake API used by the tests now follows the same rules: it rounds money like Telegram, returns user targets in the documented shape, accepts the token as the `access_token` parameter and returns `CHANNEL_ID_UNKNOWN`/`BOT_ID_UNKNOWN`. New tests in `tests/test_v224.py` include a round trip of every target type through createAd.

## v2.2.3 — why Telegram ignores an edit, and editing paused ads

- On the real account the edit test showed that none of the three request formats changes the text, while ads.telegram.org accepts the same edit. The test now goes further when no format works:
  - it changes the ad's title (never shown to users, not reviewed) and puts it back, to tell an API that stores nothing on the ad from one that refuses only the text;
  - with an extra confirmation, it pauses the ad, edits the text and resumes it. If Telegram takes the edit on a paused ad, every later in-place edit (bulk and direct) does the same, pausing each ad only for the moment of the edit.
- "جایگزینی همین آگهی" on the ad page opens the replacement dialog for that one ad (a new ad with the new text or photo, same placement and CPM), the way that works when the API will not edit the ad.

## v2.2.2 — edits Telegram did not apply, and the whole Ads API under test

**Edits that Telegram answered "ok" to but did not store**
- A real account showed it: `editAd` answered ok, the ad stayed active with its old text. New "آزمایش ویرایش در API" on the ad page (with explicit confirmation): the same edit is sent in each documented request format — JSON, `application/x-www-form-urlencoded`, URL query string — and the ad is read back after each try. The first format whose text sticks is saved and used for every later edit (bulk jobs and direct edits). The full trace (no token) can be copied.
- When no format works, in-place text/link edits are refused with a pointer to replacement mode (which creates a new ad and always works); until the test has run, the preview warns.
- An edit Telegram sent to review but still shows with the old text waits in review and is checked again after approval; an edit that did not go to review and changed nothing fails at once, quoting the text, link and status Telegram returned.

**The Telegram Ads API documentation, all of it**
- The client has every documented method (45) with their documented parameter names, and can send parameters as JSON, form or query string (lists and booleans JSON-serialized as documented).
- The fake API used by the tests implements the documentation: request formats and GET, case-insensitive method names, idempotency (header or parameter, replay header, parameter mismatch, keys still running, 24 hours, nothing stored for failed validation), required parameters and limits (title 1-128 **bytes**, text 1-160 characters, URL 1-256, impression frequency 1-4, dates within a year, schedules), photo/video rules, all four target types (numeric channel ids only after resolving them by username), statuses (`ready_for_review` without budget), budgets and the 10 minutes of inactivity before `decreaseAdBudget`/`deleteAd`, related accounts and transfers, transactions, stats and reports, audiences, pixel and events, target lists.
- `tests/test_tgads_api.py` covers it rule by rule (69 tests), including a check that every documented method is in the client and the fake API.
- Found by the documentation: a job Telegram refused (e.g. a withdrawal refused with `AD_ACTIVE_RECENTLY` because the ad was paused less than 10 minutes ago) was retried with the same idempotency key, and Telegram replays the stored answer of a key for 24 hours, so the withdrawal could never succeed. A refused job now retries with a new key; after a network error or a non-JSON answer it keeps the key, so a request that may have gone through can never run twice.
- Found by the documentation: ad titles are limited to 128 bytes, not characters; replacement and new ads now cut Persian titles by bytes instead of sending titles Telegram would refuse.

## v2.2.1 — edits that never reached Telegram's review

- Since September 2026 the Telegram Ads API saves an edited (or new) ad without budget as `ready_for_review` and does **not** send it to review until `submitAdForReview` is called; until then the ad keeps showing the old version. The panel now calls `submitAdForReview` right after such an edit or creation, after a top-up of such an ad, and from the bulk engine for items left in that state. Telegram's refusal to submit is shown on the item.
- Every edit is checked against the ad Telegram returns: if Telegram answers "ok" but the text, link or photo did not change, the bulk item fails with that message (and a direct edit returns an error) instead of being shown as sent; the panel keeps what Telegram really has.
- `ready_for_review` is its own status: "Ready for Review" in the ads table (it was shown as "In Review"), "ارسال‌نشده برای بررسی" elsewhere, a "send for review" action on each such ad and a banner on the ads page that submits all of them at once.

## v2.2.0 — Telegram-style ads table, 1 TON minimum and waves, random referral links, today

**Photos only where Telegram shows them**
- One rule (`supports_photo`) decides whether an ad can carry a photo: channel ads can, bot ads cannot. It is used by the bulk preview, by replacement creation and by the new-ad form (which hides the photo field for bots).
- A bulk photo change on a mix of ads changes the photo of the channel ads only. Bot ads get the new text when there is one; with a photo only they are quietly left out ("رد شد — آگهی ربات عکس ندارد"), not blocked. The preview says how many channel ads get the photo and how many bot ads only change their text or stay as they are.

**Minimum initial budget**
- `min_initial_budget` (default 1 TON) replaces `bulk_min_budget`. No path creates an ad under it: the panel shows a Persian error before asking Telegram, and Telegram's budget-minimum errors are translated.
- The replacement preview states "N new ads need N × 1 TON; the balance is M TON". With too little balance the operation runs in waves (stoppable, with progress per wave); the next wave starts with the money the old ads give back. With not even enough for one ad, the preview refuses.
- "Transfer from the old ad" moves only the excess of the old budget over the minimum; for old ads under 1 TON the net extra cost is shown.
- Initial budgets of replacement ads have their own daily cap (`bulk_daily_new_budget_cap`) and no longer count against the daily top-up cap.

**Referral links: three options**
- From the referrer pool (as before), one shared link for all selected ads (a referrer id or an existing tag, even one already in use, with a warning that sales are only seen for the whole group), or a random unique 10-digit code per ad with its own tag. Each random code is checked with the bot (`users → user`) not to be a real user.
- "Random code tracking test" in the account settings: the panel makes a test code and link, you start the bot from a new Telegram account, and the check reads `affiliates_users` and the user count to store `works`, `ignored` or `unknown`. Until it says `works`, the random option shows a warning with the request text for the bot's developer.
- The former "campaign code" layer in `tracking.py` became the random mode; the replacement and in-place flows are the same for all three options.

**Ads table like Telegram Ads**
- The list part of the ads page (table, filters, bulk bar, mobile cards) is English and left-to-right: Latin digits, dates as "MMM d, yyyy", statuses Active / On Hold / In Review / Declined / Stopped.
- Columns Ad, Status, CPM, Views, Clicks, Actions, CTR, CVR, CPC, CPA, Spent, Budget, Target, Date Added, plus Buyers, Cost/Buyer, Referral and Empty for; sortable headers and a Columns menu remembered in the browser.
- Combinable filters kept in the address: search, status, placement, period (Today, Yesterday, Last 7 days, Last 30 days, This month, All time, Custom), CPM and budget ranges, budget left, target type, with/without referral, text variant, has photo, date added, with a result count and "Reset filters". "Select all N results" selects only the filtered ads.
- `GET /ads` takes `period` (or `from`/`to`) and returns each ad's numbers for that period. Today comes from per-ad baselines recorded at local midnight, because Telegram's daily stats are per UTC day.
- On mobile: cards, filters in a sheet and a fixed bulk bar.

**Dashboard "today"**
- A "today" tab next to 7 and 28 days (7 days stays the default): from 00:00 in the bot's time zone (Asia/Tehran by default), compared with yesterday up to the same hour, including cost per customer from referral tags.

## v2.1.0 — budget history, bulk operations, bot diagnostics

**Budget-aware rules**
- Budget history per ad (`ad_budget_events`): funded (from empty), empty, top-up and withdrawal. Top-ups made directly on ads.telegram.org are detected from a jump of `remaining_budget`; the panel's own top-ups and withdrawals are recorded by the job runner. Existing ads count as funded "from now" after the upgrade, so none is judged stalled by mistake.
- The "withdraw idle budget" rule only measures days with budget: spend since `max(28 days ago, last funding from empty)`, and only after the ad has had budget continuously for `stalled_min_age_days`. The cost per start used by every rule is measured on the ad's last 28 funded days.
- New proposal **refill**: a good ad that ran out of budget gets a top-up of about twice its monthly spend (capped per top-up), with the number of days it has had no budget. Ads list and ad page show "بی‌بودجه از N روز پیش" and the budget history.
- New proposal **replace**: an ad Telegram declined (with its decline reason), an ad with budget but no views for N days while other ads of the account are shown, or a text that probably breaks Telegram's ad guidelines.

**Bulk operations** (new "عملیات گروهی" page in every account)
- Select ads (checkboxes, "select every filtered result", a bottom bar on mobile), then change the text and/or photo or give each ad its own referral link. Every operation shows a preview (what changes per ad, money from the account balance or from the old ad, warnings) and runs only after explicit confirmation.
- Two modes: edit in place (`editAd`, statistics kept) or replacement (default): a new ad with the same placement, CPM and target; only after Telegram approves it the old ad is paused, its budget withdrawn 10 minutes later and optionally moved to the new ad, and the old ad deleted (when the API has `deleteAd`) or archived (stopped, no budget, hidden behind an "archived" filter). A declined new ad leaves the old one untouched; declined ads can be edited and resubmitted.
- Every Telegram call is a queued job with an idempotency key; ads are submitted in batches (size, interval and the number waiting in review are account settings); a running operation can be stopped (queued jobs are cancelled). Per-ad and daily top-up caps apply to initial budgets and transfers.
- Text checks against Telegram's ad guidelines (length, emoji, decorative characters, line breaks, invented urgency, clickbait, extra or mismatching links, shorteners, language) with a "fix with Claude" rewrite the user edits before use. New texts are saved in the text library and linked to the ads. The photo is uploaded once and reused for all ads.
- Referral links from a pool of the advertiser's own referrer accounts (checked with `users → user`, with a warning when a referrer already has invites); with fewer referrers than ads the ads are grouped by channel/bot, CPM or placement. A "campaign code" mode exists as a disabled option with a request text for the bot's developer. The sales page shows ads without a tag and a "create tags for all" shortcut; the texts page selects all ads of a text.
- Telegram Ads API capabilities are detected without touching any ad: `deleteAd` with an empty body, and `editAd` on ad 0 with an invalid photo id; a one-ad test settles photo editing when the probe is inconclusive.

**Sales-bot diagnostics**
- "Bot connection diagnostics" tries every request separately (statbot, each ledger request shape and the per-type query, affiliates of every referrer, user, setting_info, invoices when needed) and shows method, path, action, body, HTTP status, time, the bot's message, the start of a non-JSON answer, rows and fields, with a cause and a fix in Persian (DNS, TLS, timeout, 404, redirect/HTML, invalid key, missing permission, 405, 429, internal error, empty or non-JSON or unknown answer, zero rows).
- Checks that the right bot is connected: user count next to the username, the username reported by `setting_info`, and a warning when the user count jumps.
- The bot sync stores the result of each part (stats, ledger, each referral tag); "no transactions read" is its own state with the filters sent. A copyable report for the bot developer never contains the API key or any token (scrubbed everywhere; covered by tests).

## v2.0.0 — Telegram Ads Manager

A rebuild of the project as a multi-account Telegram Ads manager.

**New**
- Several Telegram Ads accounts, each with its own route (`/accounts/<slug>`), dashboard, ads, rules, texts, sales and Claude analysis.
- Connections are entered in the dashboard: Telegram Ads token and the sales-bot API (base URL, bot username, per-bot API key), each with a health check. Secrets are stored encrypted (Fernet) and never shown in full.
- Sales-bot integration ("VPN Bot — Admin API", subscription edition): bot stats, transactions and referral users. Per-ad sales attribution through referral tags (`?start=` invite links of separate referrer accounts) with cost per customer per ad, per tag and per text.
- Periodic sync per account with configurable intervals (ads/balance, daily stats, sales bot), exponential backoff, an immediate re-sync when credentials change, and a live "today" view; the dashboard polls every few seconds (configurable).
- Text library: manual editing with versioning, Claude analysis (score, strengths, issues) and rewrites that can be edited before saving; performance of each text.
- New React frontend: Vite, TypeScript, Tailwind v4, shadcn/ui (Radix), TanStack Query, Recharts; Persian RTL, Shamsi dates, dark mode, fully responsive (tables become cards on mobile).
- New backend layout: FastAPI with layered app (core, models, schemas, services, integrations, api/v1, workers), SQLAlchemy 2 async, Alembic migrations, Pydantic v2 settings, CSRF-protected session auth with login rate limiting.
- Installer renamed to `tgads` (`/opt/tgads`): asks only for a domain and a password, generates the session secret and encryption key, migrates v1 (`psychonet-ads`) automatically; new `tgads password` command; backups include the encryption key.
- Tests: pytest with mocked APIs, Playwright end-to-end tests (desktop and mobile, no horizontal overflow), Vitest unit tests, a stubbed installer test; CI builds and smoke-tests a multi-arch image.

**Removed**
- The CSV/API purchase import of v1 (replaced by the bot API) and the server-rendered templates.

## v1.0.0
- Sync of all ads and daily stats from the Telegram Ads API, hourly.
- Rules with proposals: withdraw idle budget, stop expensive ads, top up good ads, refresh tired ads.
- Approval-based execution queue with idempotency keys and top-up caps.
- New ads with tracking links, text library and A/B assignment.
- Export bundle for analysis with Claude; optional Claude API analysis.
- Persian RTL web panel; one-line installer and `psychonet-ads` command.
