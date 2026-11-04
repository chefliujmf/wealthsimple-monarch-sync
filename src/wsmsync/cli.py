"""wsmsync — Wealthsimple -> Monarch sync.

  wsmsync login    --profile NAME   # interactive, once (and when the WS session expires)
  wsmsync accounts --profile NAME   # list account ids on both sides to fill the config
  wsmsync sync [--dry-run] [--profile NAME]
  wsmsync rebuild-balances --profile NAME --account WS_ID --since YYYY-MM-DD [--until YYYY-MM-DD] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import urllib.request

from . import config as config_mod
from .sources import NeedsLogin, login_monarch, login_wealthsimple, monarch, wealthsimple
from .state import State
from .sync import rebuild_balances, sync_profile

log = logging.getLogger("wsmsync")


def notify(cfg, title: str, message: str) -> None:
    if not cfg.ntfy_url:
        return
    req = urllib.request.Request(cfg.ntfy_url, data=message.encode(), headers={"Title": title})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:  # alerting must never break the run
        log.warning("ntfy failed: %s", exc)


def _profiles(cfg, name: str | None):
    picked = [p for p in cfg.profiles if name in (None, p.name)]
    if not picked:
        raise SystemExit(f"no profile named {name!r}")
    return picked


async def cmd_accounts(cfg, profile) -> None:
    ws = wealthsimple(cfg.data_dir, profile.name, profile.ws_username)
    print("Wealthsimple:")
    for acct in ws.get_accounts():
        print(f"  {acct['id']:<28} {acct.get('description', '')}")
    mm = await monarch(cfg.data_dir, profile.monarch_profile or profile.name)
    print("Monarch:")
    for acct in (await mm.get_accounts())["accounts"]:
        inst = (acct.get("institution") or {}).get("name", "")
        manual = "manual" if acct.get("isManual") else "linked"
        print(f"  {acct['id']:<28} {acct['displayName']}  [{inst}, {manual}]")


async def cmd_sync(cfg, profiles, dry_run: bool, limit=None, account=None) -> int:
    state = State(cfg.data_dir / "state.sqlite")
    failures = 0
    for profile in profiles:
        try:
            result = await sync_profile(cfg, profile, state, dry_run, limit=limit, account=account)
            log.info("%s: %s", profile.name, json.dumps(result))
            if result.get("reversed_after_import") and not dry_run:
                notify(cfg, "Wealthsimple->Monarch: reversed after import",
                       f"{profile.name}: " + "; ".join(result["reversed_after_import"]) +
                       " -- review/delete in Monarch")
        except NeedsLogin as exc:
            failures += 1
            log.error("%s", exc)
            notify(cfg, "Wealthsimple->Monarch: login needed",
                   f"{exc}. On the server run: wsmsync login --profile {profile.name}")
        except Exception as exc:
            failures += 1
            log.exception("%s sync failed", profile.name)
            notify(cfg, "Wealthsimple->Monarch: sync failed", f"{profile.name}: {exc}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="wsmsync")
    parser.add_argument("--config", default="config.toml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("login", "login-monarch", "accounts"):
        sp = sub.add_parser(name)
        sp.add_argument("--profile", required=True)
    sp = sub.add_parser("sync")
    sp.add_argument("--profile")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--limit", type=int, help="create at most N transactions (testing)")
    sp.add_argument("--account", help="only this Wealthsimple account id")
    sp = sub.add_parser("rebuild-balances", help="re-upload an account's balance history from a date")
    sp.add_argument("--profile", required=True)
    sp.add_argument("--account", required=True, help="Wealthsimple account id")
    sp.add_argument("--since", required=True, help="first day to rebuild, YYYY-MM-DD")
    sp.add_argument("--until", help="last day to rebuild, YYYY-MM-DD (default: today)")
    sp.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = config_mod.load(args.config)

    if args.cmd == "login":
        profile = _profiles(cfg, args.profile)[0]
        login_wealthsimple(cfg.data_dir, profile.name, profile.ws_username)
        if profile.monarch_profile:
            print(f"Monarch: reusing profile {profile.monarch_profile!r}'s login (shared household).")
        else:
            asyncio.run(login_monarch(cfg.data_dir, profile.name))
    elif args.cmd == "login-monarch":
        profile = _profiles(cfg, args.profile)[0]
        asyncio.run(login_monarch(cfg.data_dir, profile.name))
    elif args.cmd == "rebuild-balances":
        from datetime import date
        asyncio.run(rebuild_balances(cfg, _profiles(cfg, args.profile)[0], State(cfg.data_dir / "state.sqlite"),
                                     args.account, date.fromisoformat(args.since), args.dry_run,
                                     date.fromisoformat(args.until) if args.until else None))
    elif args.cmd == "accounts":
        asyncio.run(cmd_accounts(cfg, _profiles(cfg, args.profile)[0]))
    else:
        sys.exit(asyncio.run(cmd_sync(cfg, _profiles(cfg, args.profile), args.dry_run, args.limit, args.account)))
