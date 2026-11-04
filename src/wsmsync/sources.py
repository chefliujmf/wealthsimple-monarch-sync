"""Session handling for both services. Credentials are typed once by the account holder (`wsmsync login`);
only the resulting session tokens are stored, in files readable by the service user only."""

from __future__ import annotations

import getpass
import os
import re
from pathlib import Path

from monarchmoney import MonarchMoney, RequireMFAException
from ws_api import LoginFailedException, OTPRequiredException, WealthsimpleAPI, WSAPISession

from . import ws_bootstrap

ws_bootstrap.install()

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)


class NeedsLogin(Exception):
    """Raised when a stored session is gone or rejected; the account holder must run `wsmsync login`."""


def _write_secret(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def ws_session_path(data_dir: Path, profile: str) -> Path:
    return data_dir / profile / "wealthsimple.session.json"


def monarch_session_path(data_dir: Path, profile: str) -> Path:
    return data_dir / profile / "monarch.session.pickle"


def wealthsimple(data_dir: Path, profile: str, username: str) -> WealthsimpleAPI:
    """Open the stored session. ws-api refreshes the access token itself and calls back to persist it."""
    WealthsimpleAPI.set_user_agent(USER_AGENT)
    path = ws_session_path(data_dir, profile)
    if not path.exists():
        raise NeedsLogin(f"no Wealthsimple session for {profile}")
    try:
        return WealthsimpleAPI.from_token(
            WSAPISession.from_json(path.read_text()),
            lambda sess, _user: _write_secret(path, sess),
            username,
        )
    except Exception as exc:  # expired refresh token, revoked session, ...
        raise NeedsLogin(f"Wealthsimple session for {profile} rejected: {exc}") from exc


def login_wealthsimple(data_dir: Path, profile: str, username: str) -> None:
    WealthsimpleAPI.set_user_agent(USER_AGENT)
    path = ws_session_path(data_dir, profile)
    password = getpass.getpass(f"Wealthsimple password for {username}: ")
    otp = None
    while True:
        try:
            WealthsimpleAPI.login(username, password, otp,
                                  persist_session_fct=lambda sess, _u: _write_secret(path, sess),
                                  approval_callback=lambda: print(
                                      "Approve the login in the Wealthsimple mobile app now (waiting)...",
                                      flush=True))
            print(f"Wealthsimple session saved for {profile}.")
            return
        except OTPRequiredException:
            otp = input("Wealthsimple 2FA code: ").strip()
        except LoginFailedException as exc:
            raise SystemExit(f"Wealthsimple login failed: {exc}")


async def monarch(data_dir: Path, profile: str) -> MonarchMoney:
    path = monarch_session_path(data_dir, profile)
    if not path.exists():
        raise NeedsLogin(f"no Monarch session for {profile}")
    mm = MonarchMoney(session_file=str(path))
    mm.load_session(str(path))
    mm._headers.update(monarch_client_headers(data_dir, profile))
    return mm


MONARCH_LOGIN_URL = "https://api.monarch.com/auth/login/"
MONARCH_WEB_URL = "https://app.monarch.com/login"
APP_VERSION_RE = re.compile(r"__APP_VERSION__\s*=\s*[\"'](v?[0-9]+(?:\.[0-9]+)+)[\"']")


def monarch_app_version() -> str:
    """Monarch rejects logins from clients it considers outdated ("Please update to the latest
    version of the app"). The web app identifies itself with Monarch-Client-Version, which the
    public login page publishes as window.__APP_VERSION__ = "v<x.y.z>", sent as-is, "v" included (reverse-engineered
    2026-11-03). Read it fresh so we never fall behind a Monarch release."""
    import urllib.request

    req = urllib.request.Request(MONARCH_WEB_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        match = APP_VERSION_RE.search(resp.read().decode("utf-8", "replace"))
    if not match:
        raise SystemExit("Couldn't read Monarch's web app version from app.monarch.com/login.")
    return match.group(1)


def monarch_client_headers(data_dir: Path, profile: str) -> dict:
    """Identify as Monarch's own web client: platform, client name, current version and a stable
    per-profile device id (so Monarch sees one trusted device, not a new one every run)."""
    import uuid

    device_file = data_dir / profile / "monarch.device-uuid"
    if not device_file.exists():
        _write_secret(device_file, str(uuid.uuid4()))
    return {
        "Client-Platform": "web",
        "Monarch-Client": "monarch-core-web-app-graphql",
        "Monarch-Client-Version": monarch_app_version(),
        "Device-UUID": device_file.read_text().strip(),
        "User-Agent": USER_AGENT,
    }


def _monarch_post(payload: dict, headers: dict) -> tuple[int, dict]:
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        MONARCH_LOGIN_URL, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"{}")
        except ValueError:
            return err.code, {}


def _second_factor_field(body: dict) -> str:
    """Monarch's 403 says which second factor it wants; email codes and app codes use different fields."""
    if body.get("error_code") == "EMAIL_OTP_REQUIRED":
        return "email_otp"
    hint = " ".join(str(body.get(k, "")) for k in ("error_code", "detail", "message")).lower()
    return "email_otp" if "email" in hint else "totp"


async def login_monarch(data_dir: Path, profile: str) -> None:
    """Own Monarch login: shows what Monarch asks for (app code vs emailed code) instead of
    assuming an authenticator app, and stores only the long-lived trusted-device token."""
    path = monarch_session_path(data_dir, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    email = input("Monarch email: ").strip()
    password = getpass.getpass("Monarch password: ")
    headers = monarch_client_headers(data_dir, profile)
    # supports_email_otp is what Monarch's version gate checks: without it the server answers
    # "Please update to the latest version of the app" instead of emailing a code (from the
    # web app's own login call, main.<hash>.js, 2026-11-03). Then it replies EMAIL_OTP_REQUIRED
    # and expects the code back as email_otp.
    payload = {"username": email, "password": password, "supports_mfa": True,
               "supports_email_otp": True, "trusted_device": True}
    status, body = _monarch_post(payload, headers)
    while status == 403:
        import json as _json
        print("Monarch replied 403:", _json.dumps(body)[:600], flush=True)
        error_code = body.get("error_code") or "?"
        if "update to the latest version" in str(body.get("detail", "")).lower():
            raise SystemExit("Monarch says this client is outdated; its version check changed again.")
        if error_code == "CAPTCHA_REQUIRED":
            raise SystemExit("Monarch requires a CAPTCHA for this login; cannot continue from the server.")
        field = _second_factor_field(body)
        where = "the email Monarch just sent you" if field == "email_otp" else "your authenticator app"
        print(f"Monarch asks for a second factor ({error_code}): enter the code from {where}.")
        payload[field] = input("Monarch code: ").strip()
        status, body = _monarch_post(payload, headers)
    if status != 200 or not body.get("token"):
        detail = body.get("detail") or body.get("error_code") or f"HTTP {status}"
        raise SystemExit(f"Monarch login failed: {detail}")
    mm = MonarchMoney(session_file=str(path))
    mm.set_token(body["token"])
    mm.save_session(str(path))
    os.chmod(path, 0o600)
    print(f"Monarch session saved for {profile}.")
