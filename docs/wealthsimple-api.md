# Wealthsimple API (reverse-engineered)

Captured in November 2026 from the live web app (my.wealthsimple.com) with a fetch hook on a
logged-in session, by navigating Home → Activity → cash account → credit card.
Everything below was observed first-hand; no guesses.

## Transport & auth

- **Single endpoint:** `POST https://my.wealthsimple.com/graphql` — every data call, no REST.
- **Auth:** OAuth2 bearer token. The web app keeps its session in
  `localStorage["wsstg-identity::identity-<canonical-id>"]`; tokens refresh in place.
- The [`ws-api`](https://github.com/gboudreau/ws-api-python) library implements this same
  flow (login + TOTP + auto-refresh) — it is the right client. Validation: every operation
  our sync depends on matches what the live app sends (names, variables, response keys).

## Account ids

Account ids look like `ca-cash-<id>`, `ca-cash-msb-<id>` (joint/shared cash), `ca-credit-card-<id>`,
`tfsa-<id>`, `rrsp-<id>`, `lira-<id>`, `non-registered-<id>`, `non-registered-crypto-<id>`.
`wsmsync accounts` lists yours. Aggregators mask them as: MX = last 4 characters of the WS
account id, Plaid = last 4 of the custodian account number.

## Key operations observed

### Transactions / activity (all account types, incl. credit card)
`FetchActivityFeedItems` — variables:
- `orderBy: ["OCCURRED_AT_ASC"]` (app uses ASC on the activity page; DESC also valid)
- `accountScope: "OWN"`, `first: 50` (cursor-paginated)
- `condition: { types: [...], startDate, unifiedStatuses: [ACTION_REQUIRED, CANCELLED,
  CANCEL_PENDING, COMPLETED, CONTINGENT, IN_PROGRESS, IN_REVIEW, PARTIALLY_FILLED, PENDING,
  REFUNDED, REVERSE…], accountIds: [...] }`
- Response root: `activityFeedItems`. This is exactly ws-api `get_activities()`; the
  credit-card page uses the same operation for its transaction list.

### Balances / financials
- `FetchIdentityCurrentFinancials`, `FetchAccountCombinedNlv`, `FetchAccountCombinedReturns` (home)
- `FetchAccountCurrentFinancials <- id, currency[, startDate]` (per account; net value for
  the balance push) — ws-api `get_accounts()` / `get_account_historical_financials()`
- `FetchAccountCurrentFinancialPositions <- id, positionFilter` (holdings)
- `FetchAccountPerformanceDetails <- id`

### Credit card specifics
- `FetchCreditCardAccount <- id` (current balance; ws-api `get_creditcard_account()`)
- `FetchCreditCardLatestStatement <- id`, `FetchCreditCardStatements <- creditCardAccountId`
- `FetchCreditCardBalanceHistory <- identityId, accountId, startDate, endDate, resolution: DAILY`
- `FetchCreditCardRewardMonthlyReport`, `FetchCardRewardRedeemableBalance`, `FetchCardRewardPrograms`
- `FetchCreditScheduledPayments <- destinationAccountId`

### Transfers & details
- `FetchInternalTransfer <- id`, `FetchAccountTransfers <- id, limit[, pending|states]`
- `FetchAccountFundsTransferSchedules <- limit, accountId` (recurring WS transfers)

### Also seen (account-details page, not needed for sync)
`FetchAllAccountFeatures`, `FetchAccountTypeMetadata`, `FetchWireReceiveDetails`,
`FetchAccounts <- ids`, `FetchClient <- id`, `FetchCashAccount <- accountId`,
`PlocOverdraftAvailabilities`, suitability/risk/goal property sets.

## Consequences for this sync

1. ws-api is confirmed current — no custom GraphQL client needed.
2. `config.toml` maps each WS account id to a Monarch account id.
3. Credit card: transactions flow through the same activity feed (`type CREDIT_CARD`,
   pending holds `status authorized` → skipped until settled), already handled in `mapping.py`.
4. Registered accounts: `cash_flows` mode + `update_balance` gives Monarch the deposits,
   dividends and a correct balance without trade noise.

## Capture technique (repeatable for other banks)

Hook `window.fetch` in the logged-in tab, record `operationName` + variable keys per call,
navigate the SPA with `history.pushState` + `PopStateEvent` so the hook survives, then read
the captured list. Full query texts sit in the request bodies if ever needed.

## Login bootstrap

Before the OAuth login, the client needs two values from the **public** login page:
1. `wssdi` — device id cookie set by `GET https://my.wealthsimple.com/app/login`.
2. `client_id` — the OAuth client id inside the app bundle that page loads
   (`https://assets.wealthsimple.com/app-<hash>.js`, `"production"{… clientId:"<hex>"}`).

Early Nov 2026 Wealthsimple reformatted the login HTML so `<script` and `src="…/app-<hash>.js"`
are on separate lines. ws-api (≤ 0.40.0) scans line by line and fails with "Couldn't find app
JS URL". `src/wsmsync/ws_bootstrap.py` replaces that step with a
whole-document parse (regex over the full HTML, `DOTALL`) — unit-tested on both layouts and
verified live. Then the login itself: password → optional TOTP → mobile
"Approve login?" (ws-api ≥ 0.40 polls it; we print a prompt).
