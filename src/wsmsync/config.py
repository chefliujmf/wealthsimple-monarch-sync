"""Config file (TOML). One [[profile]] per person, each with its own logins and account map."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

VALID_MODES = {"transactions", "investments", "cash_flows", "balance_only"}


@dataclass
class AccountLink:
    ws_account_id: str
    monarch_account_id: str
    mode: str = "transactions"     # see mapping.to_monarch; balance_only = no transactions
    update_balance: bool = False   # push the WS balance to Monarch (manual accounts only)
    start_date: str | None = None  # per-account first import day (default: the profile's)
    name: str = ""                 # human label, e.g. "WS TFSA -> Monarch TFSA"
    hide_imported: bool = False    # import as hidden (e.g. keep a shared account out of reports)


@dataclass
class Profile:
    name: str
    ws_username: str
    start_date: str                # first day to import (YYYY-MM-DD)
    accounts: list[AccountLink] = field(default_factory=list)
    monarch_category: str = "Uncategorized"
    balance_start: str = ""  # first day of balance history to push (default: start_date)
    monarch_profile: str | None = None  # reuse another profile's Monarch login (one shared household)


@dataclass
class Config:
    data_dir: Path
    timezone: str
    ntfy_url: str | None
    profiles: list[Profile]


def load(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text())
    profiles = []
    for p in raw.get("profile", []):
        links = [AccountLink(**a) for a in p.pop("account", [])]
        for link in links:
            if link.mode not in VALID_MODES:
                raise ValueError(f"profile {p.get('name')}: bad mode {link.mode!r}, use one of {sorted(VALID_MODES)}")
        profiles.append(Profile(accounts=links, **p))
    names = [p.name for p in profiles]
    if len(set(names)) != len(names):
        raise ValueError("profile names must be unique")
    # A joint account is visible from both owners' logins; syncing it twice would duplicate
    # every transaction. Each Wealthsimple account may belong to exactly one profile.
    owner: dict[str, str] = {}
    for p in profiles:
        for link in p.accounts:
            if link.ws_account_id in owner:
                raise ValueError(f"{link.ws_account_id} is configured in both profile "
                                 f"{owner[link.ws_account_id]!r} and {p.name!r}; sync it from one only")
            owner[link.ws_account_id] = p.name
    return Config(
        data_dir=Path(raw.get("data_dir", "data")),
        timezone=raw.get("timezone", "America/Toronto"),
        ntfy_url=raw.get("ntfy_url"),
        profiles=profiles,
    )
