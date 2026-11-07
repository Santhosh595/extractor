# Learn — extractor

## What it is

Multi-provider free-model watcher → Telegram alerts. Checks 5 keyless listings (OpenRouter, Pollinations, Sambanova, DeepInfra, Featherless) every 10 minutes (configurable), sends the full list per provider on first run, then only NEW models. Each entry shows source · model · details (context, modality, pricing, reasoning...). Terminal only, stdlib only, Linux + Windows. Token + chat id stored encrypted at rest with a master password; 3 wrong passwords wipes them.

**Read this first, then go file-by-file in the repo.**

## 5-minute quickstart

```bash
git clone https://github.com/Santhosh595/extractor && cd extractor
python3 extractor.py setup        # asks: bot token, chat id, master password (once)
python3 extractor.py run --once   # single check → Telegram
python3 extractor.py run          # every 10 min, forever
python3 -m unittest discover -s tests -v   # 10 tests
```

Get the values first: token from **@BotFather** (`/newbot`), chat id from **@userinfobot**.

## Concepts — the "why" behind each file

| If you see this in the code | What it means | Viva answer |
|---|---|---|
| `fetch_free_models()` + `is_free()` | Free = `$0 prompt AND $0 completion` | Both must be exactly 0. Cheap ≠ free. Strings compared as floats — `'0'` vs `0.0` |
| `load_seen()` / `save_seen()` + atomic `replace()` | Diff state = which IDs already reported | Atomic write = no half-written state if killed mid-save |
| `send_telegram()` | HTTPS POST to Bot API | Chat id is the allow-list: only that chat can receive |
| `encrypt()` / `decrypt()` (PBKDF2-style keystream + XOR) | At-rest encryption | Key from master password + random salt per save; plaintext never on disk |
| `load_secrets()` wrong-password counter | 3 strikes → wipe file | Asked-for behaviour: no recovery, run setup again |
| `getpass` for secrets | No echo on terminal | Token/password never in shell history or screen |
| `sys.stdout.reconfigure(utf-8)` | Windows cp1252 fix | Same lesson as Deblote's CI failure |
| Interval loop + `try/except URLError` | Watcher pattern | Network fails are expected; retry next cycle, never crash |

## Common pitfalls

1. **Comparing price strings directly** — `"0"` vs `"0.0"` vs `0` all mean free. Always `float()`.
2. **First run spam** — first run *should* send the full list (that's the baseline). Later runs send only new.
3. **Storing the token plaintext** — even in `~/.config` it's readable. Encrypt + `chmod 600`.
4. **Echoing secrets** — `input()` echoes; `getpass()` doesn't. Use `getpass` for anything secret.
5. **Crashing on network errors** — a watcher that dies on one failed fetch is useless. Catch, log, retry.

## Hands-on, in this order

1. `python3 extractor.py run --once` with a test bot. Did the full list arrive? How many models?
2. Run it again. Nothing new → no Telegram message? (Check the terminal output.)
3. Delete `~/.extractor/seen.json` and run `--once` again. Full list resent? That's the reset behaviour.
4. Open `~/.extractor/secrets.enc`. Can you find your token in it? (You shouldn't.)
5. Run with a wrong password 3 times. File deleted? (Re-run `setup` after — this is destructive by design.)
6. Read `is_free()`. What happens with `{"prompt": "0", "completion": "0.0001"}`? Why is that right?

## Viva questions (with the trap)

1. **"What counts as free?"** Answer: both pricing fields numerically zero. Trap: "cheap models" — no, exactly $0.
2. **"How do you avoid spamming Telegram?"** Answer: seen-set diff; first run sends all, later only new.
3. **"Where's the token stored?"** Answer: `~/.extractor/secrets.enc`, encrypted with a password-derived key + per-save random salt, chmod 600.
4. **"What happens on 3 wrong passwords?"** Answer: file wiped. No recovery. That's a deliberate, requested choice — say so.
5. **"Why is the chat id part of security?"** Answer: the bot token alone can't receive alerts; only the authorised chat id gets messages. It's an allow-list.
6. **"How does it survive network failures?"** Answer: per-cycle try/except; state only advances on success.

## 3 things to say if stuck

- "First run sends everything; after that it's a diff against the seen set."
- "Encryption is password-derived with a random salt — plaintext never touches disk."
- "Three wrong passwords wipes the credentials. Deliberate, not a bug."

Good luck. This repo is small but the security thinking (allow-list, at-rest encryption, destructive lockout) is what interviewers probe.
