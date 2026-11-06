**shortcut** — key that opens the panel in the reviewer (answer side only). Esc closes it, Ctrl+L (⌘L on macOS) starts a new conversation.

**cwd** — directory the CLI runs in, so it picks up that project's CLAUDE.md / AGENTS.md, skills and files. `~` is expanded. Empty: your home folder.

**first_turn** / **next_turn** — the CLI command as an argument list, used for the first message of a conversation and for every message after it. Placeholders: `{prompt}` (the message, including the card) and `{session}` (a fresh UUID per conversation). `claude` needs `--session-id` once and `--resume` after; `pi` (the default) takes `--session-id` for both. The `claude` equivalent:

    "first_turn": ["/opt/homebrew/bin/claude", "-p", "{prompt}", "--session-id", "{session}", "--model", "sonnet", "--effort", "low", "--tools", "Read,Grep,Glob", "--strict-mcp-config"],
    "next_turn":  ["/opt/homebrew/bin/claude", "-p", "{prompt}", "--resume", "{session}", "--model", "sonnet", "--effort", "low", "--tools", "Read,Grep,Glob", "--strict-mcp-config"]

Use absolute paths: Anki started from the Dock does not get your shell's PATH. The CLI's own directory is put on PATH for it (that is where Homebrew keeps `node`). The tool list is what keeps the chat read-only.

**timeout_s** — give up on an answer after this many seconds.

**preamble** — sent ahead of the first message of each conversation.
