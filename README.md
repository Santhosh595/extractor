# Extractor

Watches **free LLM models across 5 providers** every 10 minutes and alerts your **Telegram bot** when new ones appear.

**Output per model:** source · model id · name · context length · modality · pricing · details (reasoning, tools, aliases...).

Stdlib only — no `pip install`. Terminal only — no UI. Linux + Windows.

---

## Providers (verified live 7 Nov 2026)

| Provider | Listing endpoint (keyless) | What's included | Free = |
|---|---|---|---|
| OpenRouter | `openrouter.ai/api/v1/models` | 16 models | `$0` prompt + `$0` completion |
| Pollinations | `text.pollinations.ai/models` | 1 model (`openai-fast`, anonymous tier, no signup) | no key needed at all |
| Sambanova | `api.sambanova.ai/v1/models` | 5 models (Meta-Llama-3.3-70B, Qwen, etc.) | free endpoint, keyless list |
| DeepInfra | `api.deepinfra.com/v1/openai/models` | 178 models | listing public; inference needs a key (flagged `[key required]`) |
| Featherless | `featherless.ai/api/v1/models` | 0 today (10,244 listed, none $0-priced right now) | `$0`-priced subset — watched for new ones |

**Why not Groq / Cerebras / Together / Fireworks / HuggingFace / Chutes / Ollama / NVIDIA NIM?**
Their model listings return `401 Invalid API Key` without an account key. Since extractor is keyless by design (only your Telegram token is stored), those can't be watched. If you add a provider API key later, that's a small code change — the normaliser pattern in `extractor.py` is built for it.

**If a provider is down or changes shape:** that source is skipped for that cycle with a log line. The watcher never dies because one provider failed.

---

## Sample output

```
Free models across 5 providers (200 total):
OpenRouter (16):
- liquid/lfm-3-1.7b:free
  LiquidAI: LFM3-1.7B (free) | ctx=262144 | text->text | $0/M in, $0/M out
  created=1794009477 reasoning=True hugging_face=LiquidAI/LFM3-1.7B
Pollinations (1):
- openai-fast
  GPT-OSS 20B Reasoning LLM (OVH) | ctx=n/a | text->text | free (no key)
  tier=anonymous reasoning=True tools=True vision=False aliases=openai,gpt-oss
```

First run sends the **full** list. Later runs send only models that are **new** since the last check (tracked per provider — the same model id on two providers counts as two entries).

---

## One-line install

**Linux/macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/Santhosh595/extractor/main/install.sh | bash
```

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/Santhosh595/extractor/main/install.ps1 | iex
```

The installer will:

1. Check for **Git** → install if missing (apt/dnf/pacman/brew or winget)
2. Check for **Python 3** → install if missing
3. Clone this repo → `~/extractor`
4. Run `setup` — prompts for **Telegram bot token** + **chat id** + a **master password** (once)

That's it. Everything after is your choice of next command.

---

## Before setup: get your Telegram values

1. Open Telegram, message **@BotFather** → `/newbot` → copy the **bot token** (looks like `123456:ABC-DEF...`).
2. Message your new bot once (any text) so it knows you exist.
3. Message **@userinfobot** → it replies with your numeric **chat id** (looks like `123456789`).

Use those two values in setup. No other keys needed.

---

## Run it

```bash
python3 extractor.py run            # check every 10 min, forever (Ctrl+C to stop)
python3 extractor.py run --once     # single check, then exit
python3 extractor.py run --interval 300   # every 5 min (optional)
```

**First run:** sends the FULL current free list to Telegram.
**Later runs:** sends only models that are **new** since the last check. No spam.

---

## Security — how your token is protected

| Threat | What extractor does |
|---|---|
| Token readable on disk | Stored **encrypted** (PBKDF2 + XOR cipher, key from your master password). Plaintext never touches disk. File is `chmod 600`. |
| Someone runs it on your machine | It asks for the master password. |
| Wrong password ×3 | **Credentials are wiped completely.** No recovery. Run setup again. (As requested.) |
| Someone uses your token | Alerts only go to the chat id you authorised. Token alone without the right chat id can't receive them. |
| Network sniffing | HTTPS to OpenRouter and Telegram only. No other URLs, no telemetry. |

**Honest limit:** this is at-rest encryption with a password-derived key, not a hardware vault. A password in the millions of guesses range still falls to offline brute force. Use a strong master password. (For OS-keyring-grade storage, use Windows Credential Manager / macOS Keychain instead — happy to add that later.)

---

## What counts as "free"

A model is free only if **both** pricing fields are exactly `$0`:

```
pricing.prompt == 0 AND pricing.completion == 0
```

Cheapest-but-not-free models (e.g. `$0.01/M`) do **not** count. IDs are stored in `~/.extractor/seen.json`; a model that disappears and returns is treated as new again.

---

## State files

| File | What |
|---|---|
| `~/.extractor/secrets.enc` | Encrypted token + chat id (chmod 600) |
| `~/.extractor/seen.json` | Model IDs already reported |

Delete `seen.json` to resend the full list on next run. Delete `secrets.enc` and re-run `setup` to change accounts.

---

## Test it anywhere (no Telegram needed)

```bash
python3 -m unittest discover -s tests -v   # 7 tests
```

All tests pass in CI on Ubuntu + Windows.

---

## Files

| File | What |
|---|---|
| `extractor.py` | The watcher — run this |
| `install.sh` | One-line installer (Linux/macOS) |
| `install.ps1` | One-line installer (Windows) |
| `tests/test_extractor.py` | Unit tests (free-detection, encryption, diff, format) |

## License

MIT — do whatever, no warranty.
