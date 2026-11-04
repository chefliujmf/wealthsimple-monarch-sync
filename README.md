# Wealthsimple → Monarch Money sync (self-hosted)

**Sync your Wealthsimple transactions and balances into Monarch Money when the MX / Plaid
connection is broken or disconnected.**

`wealthsimple-monarch-sync` is a small **self-hosted Python script** (Docker + a systemd timer)
that logs in to your Wealthsimple account, reads your activity, and creates the missing
transactions in Monarch Money, without duplicates. It also keeps your account balances and
investment values up to date.

> ⚠️ **This is not a hosted service or an app.** You run it yourself, on a computer or server you
> control (a home server, NAS, Raspberry Pi, VPS, or any always-on Linux box with Docker). Your
> passwords are never stored and nothing passes through a third party.

> ℹ️ **Not a supported product.** This is a personal project shared as-is. It isn't maintained
> on a regular schedule, and there's no guarantee of fixes or updates when Wealthsimple or
> Monarch change their apps. It works today for the author; if it breaks for you, you may need
> to fix it yourself (pull requests welcome).

Works with: Wealthsimple **Cash / Chequing**, **Credit Card (Visa)**, **TFSA**, **RRSP**,
**FHSA**, **LIRA**, **non-registered**, **crypto** and **joint** accounts.

## Why this exists

In fall 2026, Wealthsimple stopped syncing to Monarch Money through both MX and Plaid:
accounts showed "disconnected", balances froze, and new transactions stopped appearing.
This script talks to Wealthsimple directly, so it doesn't depend on any aggregator.

If you searched for any of these, this is for you:

- Wealthsimple not syncing with Monarch / Monarch Money Wealthsimple connection broken
- Monarch Wealthsimple MX error, Plaid Wealthsimple disconnected
- import Wealthsimple transactions into Monarch Money automatically
- Wealthsimple Visa credit card transactions missing in Monarch
- update TFSA / RRSP balance in Monarch from Wealthsimple

## Features

- **Imports missing transactions** in Monarch's format, flagged **Needs review**.
- **No duplicates.** Each transaction is tracked by its Wealthsimple id (in a local SQLite ledger
  and as `ws:<id>` in the Monarch notes). Before creating anything, the script matches what is
  already in Monarch: transactions from MX or Plaid, hidden transactions and split transactions
  included.
- **Only settled money.** Pending, in-progress, rejected, cancelled and reversed activity is
  skipped, so declined e-transfers or card holds never show up. If something already imported
  is reversed later, you get an alert; nothing is ever deleted automatically.
- **Balances and investment values.** Uploads daily balance history, so a TFSA or RRSP whose
  value moves with the market stays correct even without transactions.
- **Investment accounts the way MX did them:** dividends, deposits, withdrawals, fees and,
  optionally, buys/sells (`SYM Dividend`, `SYM Invested Cash`, ...).
- **Clean merchant names** for internal transfers, EFTs, e-transfers and cheques.
- **Several people, one Monarch household.** One profile per Wealthsimple login (for example you
  and a partner) feeding the same Monarch account. Joint accounts are synced once.
- **Per-account mode:** `transactions`, `investments`, `cash_flows` or `balance_only`, plus
  optional "hide from reports".
- **Unattended.** Runs every 2 hours from 08:00 to 22:00 (configurable), with optional
  [ntfy](https://ntfy.sh) phone alerts on failure or when a login expires.
- **Read-only on Wealthsimple.** It never places trades or moves money.

## Requirements

- An always-on machine with **Docker** (Linux recommended; systemd for the timer).
- A **Wealthsimple** account (with the mobile app, to approve the first login).
- A **Monarch Money** account (email-code or authenticator-app 2FA both work).

**Tested with:** Wealthsimple with **authenticator-app 2FA** (including the login approval in the
Wealthsimple mobile app), and Monarch Money with **email 2FA** (emailed code). Other
combinations, like SMS codes for Wealthsimple or an authenticator app for Monarch, are handled
by the code but haven't been tested yet; reports welcome.

## Setup, step by step

### 1. Get the code and build it

On the machine that will run the sync:

```sh
git clone https://github.com/chefliujmf/wealthsimple-monarch-sync.git
cd wealthsimple-monarch-sync
cp config.example.toml config.toml
docker compose build
```

### 2. Put your Wealthsimple email in `config.toml`

Open `config.toml` and set, in the `[[profile]]` block:

- `name`: a short name for this person, e.g. `me`. You'll use it as `--profile me`.
- `ws_username`: the email you use to sign in to Wealthsimple.
- `start_date`: the first day to import, e.g. the day before Monarch stopped receiving
  Wealthsimple transactions. Duplicates are detected, so a few days of overlap is fine.

Leave the `[[profile.account]]` blocks for step 4.

### 3. Log in (once)

Have your phone with the Wealthsimple app nearby, and access to the email inbox of your
Monarch account. Then run:

```sh
docker compose run --rm wsmsync login --profile me
```

It asks, in this order:

1. **`Wealthsimple password for you@example.com:`** Type your Wealthsimple password (nothing is
   shown as you type) and press Enter.
2. Then either:
   - **`Approve the login in the Wealthsimple mobile app now (waiting)...`**: open the
     Wealthsimple app on your phone and tap **Approve** on the login prompt. You have about two
     minutes. If no prompt appears, open the app and check its notifications.
   - or **`Wealthsimple 2FA code:`**: type the code from your SMS or authenticator app.
3. **`Wealthsimple session saved for me.`** Wealthsimple is done.
4. **`Monarch email:`** and **`Monarch password:`**: your Monarch Money login. If you sign in
   to Monarch with Google or Apple, first set a password in Monarch ("Forgot password" on its
   login page).
5. **`Monarch asks for a second factor ... enter the code from the email Monarch just sent you`**
   (or "from your authenticator app"): type the code at **`Monarch code:`**.
6. **`Monarch session saved for me.`** Done.

Your passwords are not saved. Only the session tokens are kept, in `data/me/` with mode 600.
Never commit `config.toml` or the `data/` folder.

To redo only the Monarch part, run `docker compose run --rm wsmsync login-monarch --profile me`.

**When do I log in again?** Wealthsimple renews its session on its own, but eventually asks
you to log in again (the sync then fails with "needs login"; set `ntfy_url` to get a phone
alert). Just re-run the `login` command above.

### 4. Map your accounts

```sh
docker compose run --rm wsmsync accounts --profile me
```

This prints two lists. The output looks like this (made-up example):

```text
Wealthsimple:
  ca-cash-ab12cd34             Chequing
  ca-credit-card-Xy9Zt8Qw7e    Credit card
  tfsa-a1b2c3d4                TFSA
  rrsp-e5f6g7h8                RRSP
Monarch:
  100000000000000001           WS Chequing  [Wealthsimple, linked]
  100000000000000002           WS Visa  [Wealthsimple, linked]
  100000000000000003           TFSA (...c3d4)  [Wealthsimple, linked]
  100000000000000004           RRSP  [Wealthsimple, linked]
  100000000000000009           Groceries cash  [, manual]
```

The left column is the **id** you put in `config.toml`. Mapping means telling the script
"this Wealthsimple account goes into that Monarch account".

**a. Match each Wealthsimple account to its Monarch account.** Usually the names make it
obvious. When they don't (two TFSAs, renamed accounts), use the last 4 characters Monarch
shows in parentheses:
- accounts Monarch linked through **MX**: the last 4 characters of the **Wealthsimple id**
  (`tfsa-a1b2c3d4` → `...c3d4`);
- accounts linked through **Plaid**: the last 4 digits of the **account number** shown in the
  Wealthsimple app under the account's details.

Pick the Monarch account that already holds this account's past transactions (the old,
now-broken MX/Plaid one), so the history continues in the same place. If there's none,
create a **manual** account in Monarch first, then re-run `accounts` to get its id.

**b. Pick a mode for each account:**

| Wealthsimple account | `mode` | What lands in Monarch |
|---|---|---|
| Chequing / Cash, joint | `transactions` | every settled transaction |
| Credit card (Visa) | `transactions` | every settled purchase, refund and payment |
| TFSA, RRSP, FHSA, LIRA, non-registered, crypto | `investments` | everything, including buys and sells, named like MX did (`VFV Invested Cash`, `VAB Dividend`) |
| same, but you don't want trades in your budget | `cash_flows` | deposits, withdrawals, dividends, interest and fees; no buys/sells |
| an account you only want the value of | `balance_only` | no transactions, just the balance |

Add `update_balance = true` to keep Monarch's balance right, especially for investment accounts,
whose value moves with the market without any transaction. Skip an account by simply not
listing it.

**c. Write one block per account in `config.toml`:**

```toml
  [[profile.account]]
  name = "TFSA -> TFSA (...c3d4)"          # optional label, for you
  ws_account_id = "tfsa-a1b2c3d4"           # from the Wealthsimple list
  monarch_account_id = "100000000000000003" # from the Monarch list
  mode = "investments"
  update_balance = true
```

[`config.example.toml`](config.example.toml) has a complete example with a chequing account, a
credit card, a TFSA, an RRSP and a second person.

### 5. Try it, then sync

```sh
docker compose run --rm wsmsync sync --dry-run     # prints what it would create, changes nothing
docker compose run --rm wsmsync sync               # does it
```

Check Monarch: new transactions are flagged **Needs review**.

### A second person (partner, joint household)

Add a second `[[profile]]` block with their own `name` and Wealthsimple `ws_username`, and run
`login --profile <their name>` with **their** phone for the approval. If you share one Monarch
household, set `monarch_profile = "me"` in their profile: their login then skips Monarch and
reuses yours. List a joint account in one profile only.

### Run it on a schedule

```sh
sudo mkdir -p /opt/wealthsimple-monarch-sync    # put the repo + config.toml here
cd /opt/wealthsimple-monarch-sync && docker compose build
sudo cp deploy/wsmsync.service deploy/wsmsync.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now wsmsync.timer
```

Change the schedule with `OnCalendar` in `deploy/wsmsync.timer`. Each run makes a handful of
read calls plus writes for new activity only.

### Repair balance history

Signs that Monarch's balance history is wrong: spikes or dips in the **Net worth** chart, or an
account's balance chart that drops to $0 for a few days and comes back. MX and Plaid both left
such gaps when they were failing, and MX stored Wealthsimple credit cards as positive (Monarch
then counts the debt as money you're owed).

Rebuild the affected days from Wealthsimple's own data:

```sh
# a past window only (e.g. days MX left at $0)
docker compose run --rm wsmsync rebuild-balances --profile me --account ca-credit-card-XXXX --since 2026-08-27 --until 2026-09-05 --dry-run
docker compose run --rm wsmsync rebuild-balances --profile me --account ca-credit-card-XXXX --since 2026-08-27 --until 2026-09-05
# everything from a date to today
docker compose run --rm wsmsync rebuild-balances --profile me --account ca-credit-card-XXXX --since 2026-09-30
```

Each day from `--since` to `--until` (default: today) is re-uploaded and replaces what Monarch
had; other days are untouched. Credit card debt is always written as a negative balance, the way
Monarch stores every liability.

Good to know:
- **Credit cards** are rebuilt day by day from settled transactions, so any past date is exact.
  MX and Plaid counted pending charges too, so their old values can be a bit higher; that's
  expected.
- **Other accounts:** Wealthsimple only keeps weekly values for older dates, so days between
  two weekly points get the earlier value. Prefer short windows for those.
- **Closed (old aggregator) accounts still count** in Monarch's past net worth. If an account
  moved from Plaid to MX, its older history lives in the closed one, so don't rebuild those dates
  into the new account or they'll be counted twice.

## Configuration

See [`config.example.toml`](config.example.toml). Per profile: Wealthsimple email, first day to
import, default Monarch category. Per account: Wealthsimple account id, Monarch account id,
`mode`, `update_balance`, optional `start_date` and `hide_imported`.

For best results, use Monarch accounts that were linked through MX/Plaid (now dead) or manual
accounts. The script fills the gap from your chosen start date and continues from there.

## FAQ

**Does this replace the Monarch ↔ Wealthsimple connection?**
Yes, for the accounts you map. It reads Wealthsimple directly and writes to Monarch, with no
MX, Plaid or other aggregator in between.

**Is it a website or app I can sign up for?**
No. It's open-source code you run yourself. There is no hosted version.

**Is my password stored?**
No. You type it once at login; only the session tokens are saved on your own machine.

**Will it create duplicates of transactions MX or Plaid already imported?**
No. It matches existing Monarch transactions (by amount within 3 days, including hidden and
split ones) before creating anything, and never imports the same Wealthsimple activity twice.

**Does it work for TFSA, RRSP, FHSA, LIRA and crypto?**
Yes. Use `investments` or `cash_flows` mode for the transactions, and `update_balance = true` to
keep the value right as markets move. `balance_only` just tracks the value.

**What about pending transactions?**
They're skipped until they settle, because Monarch's API can't mark a transaction as pending and
holds often change amount or disappear.

**How often should it run?**
Every 1 to 2 hours during the day is plenty. The included timer runs every 2 hours from 08:00
to 22:00.

**Will Monarch show the account as "updated"?**
Transactions and balances update, but Monarch keeps showing the old aggregator's "last updated"
date for a linked account. That's a Monarch limitation.

**Is it maintained? Can I get support?**
Not on a regular basis. It's a personal tool shared in case it helps. Issues and pull requests
are welcome, but there's no promise of a fix or a reply, so be ready to adapt it yourself if
Wealthsimple or Monarch change something.

**Does it work with other banks?**
Not yet. `docs/wealthsimple-api.md` describes how the Wealthsimple API was mapped; the same
technique applies to other web banking apps.

## How it works

- **Wealthsimple:** its GraphQL API (`my.wealthsimple.com/graphql`) through
  [`ws-api`](https://github.com/gboudreau/ws-api-python), with its own login bootstrap
  (`ws_bootstrap.py`). API notes: [`docs/wealthsimple-api.md`](docs/wealthsimple-api.md).
- **Monarch Money:** [`monarchmoneycommunity`](https://github.com/bradleyseanf/monarchmoneycommunity)
  (`api.monarch.com`), with Monarch's current web-client headers and email OTP login.
- `mapping.py` turns Wealthsimple activity into neutral transactions and `sync.py` sends them,
  so other destinations (Actual Budget, Firefly III, YNAB, CSV) could be added.

## Development

```sh
python3.12 -m venv .venv && .venv/bin/pip install -e '.[test]' && .venv/bin/pytest -q
```

Pull requests are welcome, especially when Wealthsimple or Monarch change their app. Maintenance is best-effort only.

## Disclaimer

Unofficial. Not affiliated with, endorsed by, or supported by Wealthsimple or Monarch Money.
Provided as-is, with no warranty and no commitment to regular maintenance or support.
Both APIs are undocumented and can change at any time; use at your own risk and check your
providers' terms.

## License

[GPL-3.0](LICENSE) (it builds on `ws-api`, which is GPL-3.0).
