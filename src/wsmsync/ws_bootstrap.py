"""Our own Wealthsimple login bootstrap (reverse-engineered 2026-11-03).

Before login, the client needs two values from the public login page:
  - `wssdi`: a device id, set as a cookie on GET https://my.wealthsimple.com/app/login
  - `client_id`: the OAuth client id, embedded in the app bundle that page loads
    (`https://assets.wealthsimple.com/app-<hash>.js`, inside its "production" config)

ws-api scans the page line by line for `<script ... src=".../app-<hash>.js"`. In early
Nov 2026 Wealthsimple reformatted the HTML so `<script` and `src=` sit on separate lines,
which breaks that scan ("Couldn't find app JS URL"). We parse the whole document instead.
"""

from __future__ import annotations

import re

from ws_api.exceptions import UnexpectedException
from ws_api.wealthsimple_api import WealthsimpleAPIBase

LOGIN_PAGE = "https://my.wealthsimple.com/app/login"
APP_JS_RE = re.compile(r'<script\b[^>]*?\bsrc="([^"]+/app-[a-f0-9]+\.js)"', re.IGNORECASE | re.DOTALL)
CLIENT_ID_RE = re.compile(r'"production"[^}]*clientId:"([a-f0-9]+)"', re.IGNORECASE)
WSSDI_RE = re.compile(r"wssdi=([a-f0-9-]+);", re.IGNORECASE)


def find_app_js(html: str) -> str | None:
    match = APP_JS_RE.search(html)
    return match.group(1) if match else None


def find_client_id(js: str) -> str | None:
    match = CLIENT_ID_RE.search(js)
    return match.group(1) if match else None


def _bootstrap(self: WealthsimpleAPIBase) -> None:
    if self.session.wssdi and self.session.client_id:
        return
    resp = self.send_get(LOGIN_PAGE, return_response=True)
    if not self.session.wssdi:
        if "wssdi" in resp.cookies:
            self.session.wssdi = resp.cookies["wssdi"]
        else:
            for key, value in resp.headers.items():
                if key.lower() == "set-cookie" and (m := WSSDI_RE.search(value)):
                    self.session.wssdi = m.group(1)
                    break
    if not self.session.wssdi:
        raise UnexpectedException("Couldn't find wssdi cookie on the Wealthsimple login page.")
    if not self.session.client_id:
        app_js = find_app_js(resp.text)
        if not app_js:
            raise UnexpectedException("Couldn't find the app bundle URL on the Wealthsimple login page.")
        client_id = find_client_id(self.send_get(app_js, return_response=True).text)
        if not client_id:
            raise UnexpectedException(f"Couldn't find the production clientId in {app_js}.")
        self.session.client_id = client_id


def install() -> None:
    """Replace ws-api's bootstrap with ours. Call once before any login or session use."""
    WealthsimpleAPIBase._bootstrap_device_id_and_client = _bootstrap
