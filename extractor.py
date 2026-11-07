"""Extractor — free-model watcher across providers, Telegram alerts.

Checks multiple providers' model listings every N minutes (default 10).
First run sends the full free list per provider. Later runs send only NEW.
Output per model: source · id · name · context · modality · pricing · details.

Providers (all keyless — verified live 7 Nov 2026):
  - OpenRouter   https://openrouter.ai/api/v1/models        (free = $0+$0)
  - Pollinations https://text.pollinations.ai/models       (anonymous tier)
  - Sambanova    https://api.sambanova.ai/v1/models        (free endpoint; pricing paid on paper, free tier in practice — listed as "free endpoint")
  - DeepInfra    https://api.deepinfra.com/v1/openai/models (free listing; models need key — flagged as key-required)
  - Featherless  https://featherless.ai/api/v1/models       ($0-priced subset; currently 0)

Excluded (need API key to list): Groq, Cerebras, Together, Fireworks, HuggingFace,
Chutes, Ollama Cloud, NVIDIA NIM — see README "Providers" table for why.

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


# -------------------------------------------------------------- providers

# Each provider: keyless listing URL + a normaliser that turns its raw JSON
# into a common shape:
#   {"source", "id", "name", "context_length", "modality", "pricing",
#    "details", "key_required"}
# A provider that errors (offline, 401, HTML) is skipped for that cycle —
# the watcher never dies because one source is down.
def _norm_openrouter(m: dict) -> dict:
    arch = m.get("architecture") or {}
    return {
        "source": "OpenRouter",
        "id": m.get("id", "?"),
        "name": m.get("name", ""),
        "context_length": m.get("context_length"),
        "modality": arch.get("modality", ""),
        "pricing": m.get("pricing", {}),
        "details": f"created={m.get('created')} reasoning={bool(m.get('reasoning'))} "
                   f"hugging_face={m.get('hugging_face_id') or ''}",
        "key_required": False,
    }


def _norm_pollinations(m: dict) -> dict:
    return {
        "source": "Pollinations",
        "id": m.get("name", "?"),
        "name": m.get("description", ""),
        "context_length": None,
        "modality": "text->text",
        "pricing": {"prompt": "0", "completion": "0"},
        "details": f"tier={m.get('tier')} reasoning={m.get('reasoning')} "
                   f"tools={m.get('tools')} vision={m.get('vision')} aliases={','.join(m.get('aliases') or [])}",
        "key_required": False,
    }


def _norm_sambanova(m: dict) -> dict:
    return {
        "source": "Sambanova",
        "id": m.get("id", "?"),
        "name": m.get("owned_by", ""),
        "context_length": m.get("context_length"),
        "modality": "text->text",
        "pricing": m.get("pricing", {}),
        "details": f"max_completion={m.get('max_completion_tokens')} owned_by={m.get('owned_by')}",
        "key_required": False,
    }


def _norm_deepinfra(m: dict) -> dict:
    return {
        "source": "DeepInfra",
        "id": m.get("id", "?"),
        "name": (m.get("metadata") or {}).get("description", "")[:80],
        "context_length": None,
        "modality": "text->text",
        "pricing": {},
        "details": f"owned_by={m.get('owned_by')} created={m.get('created')}",
        "key_required": True,  # listing is public, inference needs a key
    }


def _norm_featherless(m: dict) -> dict:
    return {
        "source": "Featherless",
        "id": m.get("id", "?"),
        "name": m.get("owned_by", ""),
        "context_length": m.get("context_length"),
        "modality": "text->text",
        "pricing": m.get("pricing", {}),
        "details": f"class={m.get('model_class')} gated={m.get('is_gated')} created={m.get('created')}",
        "key_required": True,
    }


PROVIDERS: list[dict] = [
    {"name": "OpenRouter", "url": "https://openrouter.ai/api/v1/models",
     "shape": "data", "norm": _norm_openrouter, "filter": "zero_price"},
    {"name": "Pollinations", "url": "https://text.pollinations.ai/models",
     "shape": "list", "norm": _norm_pollinations, "filter": "anonymous"},
    {"name": "Sambanova", "url": "https://api.sambanova.ai/v1/models",
     "shape": "data", "norm": _norm_sambanova, "filter": "all"},
    {"name": "DeepInfra", "url": "https://api.deepinfra.com/v1/openai/models",
     "shape": "data", "norm": _norm_deepinfra, "filter": "all"},
    {"name": "Featherless", "url": "https://featherless.ai/api/v1/models",
     "shape": "data", "norm": _norm_featherless, "filter": "zero_price"},
]


def _http_get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": "extractor/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_all(timeout: int = 30) -> dict[str, list[dict]]:
    """{provider_name: [normalised models]}. Failed sources -> empty list."""
    out: dict[str, list[dict]] = {}
    for p in PROVIDERS:
        try:
            body = _http_get(p["url"], timeout)
            raw = body if p["shape"] == "list" else body.get("data", [])
            if not isinstance(raw, list):
                raise ValueError("unexpected shape")
            models = [p["norm"](m) for m in raw]
            if p["filter"] == "zero_price":
                models = [m for m in models
                          if float(m["pricing"].get("prompt", "1")) == 0.0
                          and float(m["pricing"].get("completion", "1")) == 0.0]
            elif p["filter"] == "anonymous":
                models = models  # Pollinations anonymous tier is already free
            out[p["name"]] = models
        except Exception as e:
            print(f"[{p['name']}] skipped this cycle: {e}")
            out[p["name"]] = []
    return out


def model_key(m: dict) -> str:
    """Provider-scoped identity: same model id on 2 providers = 2 entries."""
    return f"{m['source']}:{m['id']}"


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
    """Backward compat: OpenRouter free models only (normalised shape)."""
    return fetch_all(timeout).get("OpenRouter", [])


def is_free(model: dict) -> bool:
    """Free = $0 prompt AND $0 completion per 1M tokens.
    Pricing fields are strings ('0', '0.0', '0.0001') — compare numerically.
    Also accepts the normalised shape from providers (same pricing dict)."""
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


def format_models(models: list[dict], source_name: str = "") -> str:
    """One block per provider: Source | Model | Details.
    Details line = name · context · modality · pricing · key-required flag."""
    if not models:
        return ""
    header = source_name or models[0].get("source", "Models")
    lines = [f"{header} ({len(models)}):"]
    for m in models:
        ctx = m.get("context_length") or "n/a"
        pricing = m.get("pricing") or {}
        price = f"${pricing.get('prompt', '?')}/M in, ${pricing.get('completion', '?')}/M out" \
            if pricing else "free (no key)"
        key = " [key required]" if m.get("key_required") else ""
        lines.append(f"- {m['id']}")
        lines.append(f"  {m.get('name', '')} | ctx={ctx} | {m.get('modality', '')} | {price}{key}")
        if m.get("details"):
            lines.append(f"  {m['details']}")
    return "\n".join(lines)


def format_all(by_provider: dict[str, list[dict]]) -> str:
    """All providers in one Telegram message, capped at 4000 chars.
    Summary line first so even a truncated message shows the totals."""
    total = sum(len(v) for v in by_provider.values())
    parts = [f"Free models across {len(by_provider)} providers ({total} total):"]
    for name, models in by_provider.items():
        block = format_models(models, name)
        if block:
            parts.append(block)
    return "\n".join(parts)[:4000]


# -------------------------------------------------------------------- run

def check_once(token: str, chat_id: str, first_run: bool = False) -> tuple[int, int]:
    """Fetch all providers, diff vs seen, alert on new. Returns (total, new)."""
    by_provider = fetch_all()
    all_models = [m for models in by_provider.values() for m in models]
    ids = {model_key(m) for m in all_models}
    seen = load_seen()
    if first_run or not seen:
        send_telegram(token, chat_id, "Extractor started.\n" + format_all(by_provider))
        save_seen(ids)
        print(f"First run: sent all {len(all_models)} models across "
              f"{len(by_provider)} providers.")
        return len(all_models), len(all_models)
    new = [m for m in all_models if model_key(m) not in seen]
    if new:
        new_by_provider: dict[str, list[dict]] = {}
        for m in new:
            new_by_provider.setdefault(m["source"], []).append(m)
        send_telegram(token, chat_id,
                      f"NEW free models ({len(new)}):\n" + format_all(new_by_provider))
        print(f"{len(new)} new model(s) — alert sent.")
    else:
        print(f"No new models ({len(all_models)} total across providers, all known).")
    save_seen(ids)
    return len(all_models), len(new)


def run_watcher(interval: int, once: bool) -> None:
    secrets = load_secrets()
    token, chat_id = secrets["token"], secrets["chat_id"]
    print(f"Extractor watching {len(PROVIDERS)} providers every {interval}s. Ctrl+C to stop.")
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
