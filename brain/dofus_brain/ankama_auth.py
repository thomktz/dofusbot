"""Local Ankama OAuth + PKCE login used by the Zaap emulator."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import string
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

AUTH_URL = "https://auth.ankama.com/login/ankama"
TOKEN_URL = "https://auth.ankama.com/token"
CLIENT_ID = "102"
REDIRECT_URI = "zaap://login"
HAAPI_REFRESH = "https://haapi.ankama.com/json/Ankama/v5/Api/RefreshApiKey"
HAAPI_SIGN_ON = "https://haapi.ankama.com/json/Ankama/v5/Account/SignOnWithApiKey"
HAAPI_CREATE = "https://haapi.ankama.com/json/Ankama/v5/Account/CreateToken"
# AstraBot distinguishes the Zaap/account context (102) from Dofus proper
# (1). The former is used for SignOn; the latter is the only valid game value
# for CreateToken.
ZAAP_GAME_ID = 102
DOFUS_GAME_ID = 1


def _verifier() -> str:
    return "".join(secrets.choice(string.ascii_letters + string.digits + "-._~") for _ in range(96))


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def login_url(verifier: str) -> str:
    return AUTH_URL + "?" + urlencode({"code_challenge": _challenge(verifier), "redirect_uri": REDIRECT_URI,
                                        "client_id": CLIENT_ID, "direct": "true",
                                        "origin_tracker": "https://www.ankama-launcher.com/launcher"})


def _exchange(code: str, verifier: str) -> dict:
    r = requests.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": code,
                                       "code_verifier": verifier, "client_id": CLIENT_ID,
                                       "redirect_uri": REDIRECT_URI}, timeout=30)
    r.raise_for_status()
    return r.json()


def _game_token(tokens: dict) -> tuple[str, dict]:
    access = tokens["access_token"]
    refresh = tokens["refresh_token"]
    headers = {"apikey": access, "if-none-match": "null", "user-agent": "Zaap 3.15.15",
               "accept": "*/*", "accept-language": "fr"}
    r = requests.post(HAAPI_REFRESH, data={"refresh_token": refresh, "long_life_token": "true",
                                           "shop_key": "ZAAP", "payment_mode": "OK", "lang": "fr"},
                      headers=headers, timeout=30)
    _raise(r, "refresh HAAPI key")
    api = r.json()
    key = api.get("key") or api.get("apiKey")
    if not key:
        raise RuntimeError("Ankama did not return a HAAPI key")
    session_headers = {**headers, "apikey": key}
    # AstraBot establishes the game account context before asking for the
    # short-lived game token. Skipping this produces Ankama's opaque 422.
    signed_on = requests.post(HAAPI_SIGN_ON, json={"game": ZAAP_GAME_ID}, headers=session_headers, timeout=30)
    _raise(signed_on, "sign on to Dofus")
    token = requests.get(HAAPI_CREATE, params={"game": DOFUS_GAME_ID}, headers=session_headers, timeout=30)
    _raise(token, "create Dofus game token")
    game = token.json().get("token")
    if not game:
        raise RuntimeError("Ankama did not return a Dofus game token")
    # Dofus calls userInfo_get through the local Zaap service during startup.
    # AstraBot returns the real SignOn payload here; an empty object is not a
    # valid substitute and can make the game reject the following connection.
    return game, {"haapi_key": key, "refresh_token": api.get("refresh_token", refresh),
                  "user_info": signed_on.json(), "saved_at": int(time.time())}


def create_game_token(haapi_key: str) -> str:
    """Create the short-lived game token at Dofus' actual request time.

    This deliberately mirrors AstraBot's ``Handler.auth_getGameToken`` rather
    than caching a token at browser-login time.
    """
    headers = {"apikey": haapi_key, "if-none-match": "null", "user-agent": "Zaap 3.15.15",
               "accept": "*/*", "accept-language": "fr"}
    response = requests.get(HAAPI_CREATE, params={"game": DOFUS_GAME_ID}, headers=headers, timeout=30)
    _raise(response, "create Dofus game token")
    token = response.json().get("token")
    if not token:
        raise RuntimeError("Ankama did not return a Dofus game token")
    return str(token)


def _raise(response: requests.Response, action: str) -> None:
    if response.ok:
        return
    body = response.text.strip().replace("\n", " ")[:600]
    raise RuntimeError(f"Ankama {action} failed ({response.status_code}): {body}")


async def authenticate(path: Path) -> dict:
    """Open a headed Chrome login and capture the zaap:// callback URL."""
    from playwright.async_api import async_playwright
    from playwright.async_api import Error as PlaywrightError

    verifier = _verifier()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        page = await browser.new_page()
        await page.goto(login_url(verifier), wait_until="domcontentloaded", timeout=60_000)
        code: str | None = None
        for _ in range(180):
            await asyncio.sleep(1)
            url = page.url
            if "zaap://login?code=" in url:
                code = url.split("zaap://login?code=", 1)[1].split("&", 1)[0]
                break
            # Redirecting from Ankama through the custom zaap:// callback can
            # briefly invalidate the document. It is not an auth failure—just
            # wait for the next polling tick and inspect the new URL/content.
            try:
                html = await page.content()
            except PlaywrightError:
                continue
            marker = "zaap://login?code="
            if marker in html:
                code = html.split(marker, 1)[1].split("&", 1)[0].split("\"", 1)[0]
                break
        await browser.close()
    if not code:
        raise RuntimeError("Timed out waiting for Ankama OAuth login")
    tokens = await asyncio.to_thread(_exchange, code, verifier)
    game_token, stored = await asyncio.to_thread(_game_token, tokens)
    stored.update({"access_token": tokens["access_token"], "game_token": game_token})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stored) + "\n")
    os.chmod(path, 0o600)
    return stored


def load(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
        return data if data.get("game_token") else None
    except (OSError, ValueError, TypeError):
        return None
