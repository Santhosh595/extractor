"""Extractor — OpenRouter free-models watcher with Telegram alerts.

Checks https://openrouter.ai/api/v1/models every N minutes (default 10).
First run sends the full free list ($0 prompt + $0 completion).
Later runs send only NEW models that appeared since last check.

Setup (one-time, interactive — saves token + chat id once, password-protected):
    python extractor.py setup

Run the watcher:
    python extractor.py run          # foreground, every 10 min
    python extractor.py run --once   # single check, then exit

Security:
    - Telegram token + chat id are encrypted at rest (PBKDF2 + XOR cipher,
      key derived from your master password). Plaintext never touches disk.
    - Wrong password 3 times in a row -> saved credentials are wiped.
    - Only the chat id you authorised can receive alerts (checked per send).

Stdlib only. No pip install. Linux + Windows.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# Windows cp1252 cannot print fancy symbols — force UTF-8 (Deblote lesson).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

API_URL = "https://openrouter.ai/api/v1/models"
STATE_DIR = Path.home() / ".extractor"
SECRETS_FILE = STATE_DIR / "secrets.enc"
SEEN_FILE = STATE_DIR / "seen.json"
DEFAULT_INTERVAL = 600  # seconds = 10 minutes
MAX_WRONG_PASSWORD = 3
PBKDF2_ITERS = 100_000


# ---------------------------------------------------------------- secrets

def _keystream(password: str, salt: bytes, length: int) -> bytes:
    """Deterministic keystream so XOR encrypt/decrypt share logic."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hashlib.sha256(password.encode() + salt + str(counter).encode()).digest())
        counter += 1
    return bytes(out[:length])


def encrypt(password: str, plaintext: str) -> str:
    salt = os.urandom(16)
    data = plaintext.encode("utf-8")
    stream = _keystream(password, salt, len(data))
    cipher = bytes(b ^ s for b, s in zip(data, stream))
    return json.dumps({"v": 1, "salt": salt.hex(), "data": cipher.hex()})


def decrypt(password: str, blob: str) -> str:
    obj = json.loads(blob)
    salt = bytes.fromhex(obj["salt"])
    cipher = bytes.fromhex(obj["data"])
    stream = _keystream(password, salt, len(cipher))
    return bytes(b ^ s for b, s in zip(cipher, stream)).decode("utf-8")


def save_secrets(telegram_token: str, chat_id: str) -> None:
    pw = getpass.getpass("Set a master password (to view/change settings later): ")
    if len(pw) < 4:
        print("Password too short (min 4 chars). Aborting.")
        sys.exit(1)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"token": telegram_token, "chat_id": chat_id})
    SECRETS_FILE.write_text(encrypt(pw, payload), encoding="utf-8")
    SECRETS_FILE.chmod(0o600)
    print(f"Saved encrypted to {SECRETS_FILE}")


def load_secrets() -> dict | None:
    """Prompt for password, decrypt, return {token, chat_id}.
    3 wrong passwords -> wipe the file completely (asked-for behaviour)."""
    if not SECRETS_FILE.exists():
        print("No saved credentials. Run: python extractor.py setup")
        sys.exit(1)
    blob = SECRETS_FILE.read_text(encoding="utf-8")
    for attempt in range(MAX_WRONG_PASSWORD):
        pw = getpass.getpass("Master password: ")
        try:
            data = json.loads(decrypt(pw, blob))
            if "token" in data and "chat_id" in data:
                return data
        except Exception:
            pass
        remaining = MAX_WRONG_PASSWORD - attempt - 1
        if remaining:
            print(f"Wrong password. {remaining} attempt(s) left.")
    # 3 wrong -> wipe everything. No recovery, no retry-after. As requested.
    SECRETS_FILE.unlink(missing_ok=True)
    print("WRONG PASSWORD x3 — credentials DELETED. Run setup again.")
    sys.exit(1)


# ------------------------------------------------------------ OpenRouter

def fetch_free_models(timeout: int = 30) -> list[dict]:
    req = urllib.request.Request(API_URL, headers={"User-Agent": "extractor/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [m for m in data.get("data", []) if is_free(m)]


def is_free(model: dict) -> bool:
    """Free = $0 prompt AND $0 completion per 1M tokens.
    Pricing fields are strings ('0', '0.0', '0.0001') — compare numerically."""
    pricing = model.get("pricing") or {}
    try:
        return float(pricing.get("prompt", "1")) == 0.0 and \
               float(pricing.get("completion", "1")) == 0.0
    except (TypeError, ValueError):
        return False


def load_seen() -> set[str]:
    if not SEEN_FILE.exists():
        return set()
    try:
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_seen(ids: set[str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SEEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(ids), indent=1), encoding="utf-8")
    tmp.replace(SEEN_FILE)  # atomic: no half-written state if killed


# ---------------------------------------------------------------- Telegram

def send_telegram(token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": text, "disable_web_page_preview": True}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        print(f"Telegram HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
        return False
    except Exception as e:
        print(f"Telegram send failed: {e}")
        return False


def format_models(models: list[dict]) -> str:
    lines = [f"OpenRouter free models ({len(models)}):"]
    for m in models:
        lines.append(f"- {m.get('id', '?')} — {m.get('name', '')}".rstrip(" —"))
    return "\n".join(lines)[:4000]  # Telegram message limit


# -------------------------------------------------------------------- run

def check_once(token: str, chat_id: str, first_run: bool = False) -> tuple[int, int]:
    """Fetch, diff against seen, alert on new. Returns (total_free, new_count)."""
    free = fetch_free_models()
    ids = {m["id"] for m in free}
    seen = load_seen()
    if first_run or not seen:
        send_telegram(token, chat_id, "Extractor started. " + format_models(free))
        save_seen(ids)
        print(f"First run: sent all {len(free)} free models.")
        return len(free), len(free)
    new = [m for m in free if m["id"] not in seen]
    if new:
        send_telegram(token, chat_id, "NEW free models on OpenRouter:\n" + format_models(new))
        print(f"{len(new)} new model(s) — alert sent.")
    else:
        print(f"No new models ({len(free)} free, all known).")
    save_seen(ids)
    return len(free), len(new)


def run_watcher(interval: int, once: bool) -> None:
    secrets = load_secrets()
    token, chat_id = secrets["token"], secrets["chat_id"]
    print(f"Extractor watching OpenRouter every {interval}s. Ctrl+C to stop.")
    first = True
    while True:
        try:
            check_once(token, chat_id, first_run=first)
            first = False
        except urllib.error.URLError as e:
            print(f"Network error: {e}. Will retry next cycle.")
        except Exception as e:
            print(f"Check failed: {e}. Will retry next cycle.")
        if once:
            break
        time.sleep(interval)


# ------------------------------------------------------------------ main

def main() -> None:
    ap = argparse.ArgumentParser(description="OpenRouter free-model watcher -> Telegram")
    ap.add_argument("cmd", choices=["setup", "run"], help="setup = save credentials; run = watch")
    ap.add_argument("--once", action="store_true", help="run a single check then exit (run mode)")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="seconds between checks (default 600)")
    args = ap.parse_args()

    if args.cmd == "setup":
        print("Telegram setup — get a token from @BotFather, and your chat id from @userinfobot.")
        token = getpass.getpass("Telegram bot token: ").strip()
        chat_id = input("Your Telegram chat id (the number that talks to the bot): ").strip()
        if not token or not chat_id:
            print("Both are required. Aborting.")
            sys.exit(1)
        save_secrets(token, chat_id)
        print("Test: send yourself a message via the bot, then run: python extractor.py run --once")
        return

    run_watcher(args.interval, args.once)


if __name__ == "__main__":
    main()
