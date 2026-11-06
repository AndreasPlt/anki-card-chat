# anki-card-chat

Anki add-on: ask an agent CLI (`claude -p`, `pi -p`, ...) about the card you are reviewing,
in a panel next to the reviewer.

- `c` on the answer side opens the panel and focuses the input; `Esc` hides it and hands the
  keys back to the reviewer. It also hides itself when the next question appears — it never
  sees a card before you flipped it.
- One conversation per review session (entering the reviewer starts a new one). `Reset` or
  `Ctrl+L` (`⌘L` on macOS) starts over.
- The card's fields go along with your first question about it, and again whenever the card
  changes; follow-ups on the same card only send the question.
- The CLI runs in your study repo (`cwd`), so it reads that project's CLAUDE.md, skills and
  notes. It is read-only by its tool allowlist.

## Install

```bash
uv run python scripts/install.py
```

This builds `dist/anki_card_chat.ankiaddon` and opens it with Anki. Confirm the install and
restart Anki. `uv run python scripts/build.py` only builds the file.

Configure under Tools → Add-ons → Card Chat → Config; the keys are documented in
[`config.md`](src/anki_card_chat/config.md).

## Layout

- `chat.py` — prompt, session and CLI runs. No Anki imports, so it is unit-tested and reusable
  outside Anki.
- `panel.py` — the Qt dock.
- `__init__.py` — hooks: the shortcut, hiding on the next question, a new session per review.

## Development

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

`scripts/bench.py` times the CLI configurations on real cards and typical questions — see its
docstring.
