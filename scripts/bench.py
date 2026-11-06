"""Time the CLI configurations the add-on could run, on real cards and typical questions.

    uv run python scripts/bench.py run results.jsonl --deck DECK --repo DIR   # resumable
    uv run python scripts/bench.py summary results.jsonl
    uv run python scripts/bench.py cleanup results.jsonl    # delete the sessions it created

Every turn goes through `chat.Session`, i.e. exactly what the panel runs. Anki must be
open with AnkiConnect so the cards can be read. `--deck` holds the benchmark words; `--repo`
is where the CLI runs in repo mode.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import statistics
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from anki_card_chat import chat  # noqa: E402

CLAUDE = "/opt/homebrew/bin/claude"
PI = "/opt/homebrew/bin/pi"
BARE = Path(tempfile.gettempdir()) / "anki-card-chat-bench-empty"
WORDS = ("擦", "曾经", "劝")  # one card per repetition
REPS = len(WORDS)
TIMEOUT_S = 300
PREAMBLE = json.loads(
    (Path(__file__).resolve().parent.parent / "src/anki_card_chat/config.json").read_text()
)["preamble"]

QUESTIONS = {
    "explain": "Explain how the word is used in the example sentence.",
    "nuance": "How is it different from its closest near-synonym?",
    "examples": "Give me two more natural example sentences with it.",
    "quick": "Is it more spoken or written? One line.",
    "notes": "Have I written about this word anywhere in my notes? Check the notes/ folder.",
}
FOLLOW_UP = ("follow-up", "Can you give me a simpler sentence with it?")  # resumed after explain

CONFIGS = [
    ("claude", model, effort)
    for model in ("opus", "sonnet", "haiku")
    for effort in ("low", "medium", "high")
]
CONFIGS += [("pi", "deepseek/deepseek-flash", level) for level in ("off", "low", "medium", "high")]
# The claude CLI's opus/sonnet/haiku aliases, through pi's anthropic provider. Opus 5.5 is not
# in pi's catalog (it runs as a custom id) and rejects thinking "off".
CONFIGS += [("pi", "anthropic/claude-opus-5-5", level) for level in ("low", "medium", "high")]
CONFIGS += [
    ("pi", f"anthropic/{model}", level)
    for model in ("claude-sonnet-5", "claude-haiku-4-5")
    for level in ("off", "low", "medium", "high")
]
MODES = ("repo", "bare")


def templates(cli: str, model: str, level: str, mode: str) -> dict:
    if cli == "claude":
        extra = (
            ["--tools", "Read,Grep,Glob"]
            if mode == "repo"
            else ["--tools", "", "--disable-slash-commands"]
        )
        common = ["--model", model, "--effort", level, "--strict-mcp-config", *extra]
        return {
            "first_turn": [CLAUDE, "-p", "{prompt}", "--session-id", "{session}", *common],
            "next_turn": [CLAUDE, "-p", "{prompt}", "--resume", "{session}", *common],
        }
    extra = (
        ["--tools", "read,grep,find,ls"]
        if mode == "repo"
        else [
            "--no-tools",
            "--no-context-files",
            "--no-skills",
            "--no-extensions",
            "--no-prompt-templates",
        ]
    )
    return {
        "first_turn": [
            PI,
            "-p",
            "{prompt}",
            "--session-id",
            "{session}",
            "--model",
            model,
            "--thinking",
            level,
            *extra,
        ]
    }


def anki(action: str, **params):
    body = json.dumps({"action": action, "version": 6, "params": params}).encode()
    with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8765", body)) as r:
        reply = json.load(r)
    if reply["error"]:
        raise RuntimeError(reply["error"])
    return reply["result"]


def load_cards(deck: str) -> list[chat.Card]:
    """The first (Reading) card of each benchmark word, as the add-on would build it."""
    cards = []
    for word in WORDS:
        ids = anki("findCards", query=f'deck:"{deck}" ZH:{word} card:1')
        info = anki("cardsInfo", cards=ids[:1])[0]
        fields = sorted(info["fields"].items(), key=lambda kv: kv[1]["order"])
        cards.append(
            chat.Card(
                card_id=info["cardId"],
                note_id=info["note"],
                deck=info["deckName"],
                note_type=info["modelName"],
                card_type="Reading",
                fields=tuple((name, value["value"]) for name, value in fields),
            )
        )
    return cards


def job_key(cli, model, level, mode, usecase, rep) -> str:
    return "|".join([cli, model, level, mode, usecase, str(rep)])


def run(results: Path, workers: int, modes: list[str], deck: str, repo: Path | None) -> None:
    BARE.mkdir(exist_ok=True)
    cards = load_cards(deck)
    done = {json.loads(line)["key"] for line in results.open()} if results.exists() else set()
    jobs = [
        (cfg, mode, usecase, rep)
        for cfg in CONFIGS
        for mode in modes
        for usecase in QUESTIONS
        for rep in range(REPS)
    ]
    jobs = [j for j in jobs if job_key(*j[0], j[1], j[2], j[3]) not in done]
    random.Random(0).shuffle(jobs)  # spread drift and load evenly over configurations
    print(f"{len(jobs)} jobs to run ({len(done)} results already there)", flush=True)
    lock = threading.Lock()
    counter = [0]

    def record(cfg, mode, usecase, rep, session, seconds, answer=None, error=None) -> None:
        row = {
            "key": job_key(*cfg, mode, usecase, rep),
            "cli": cfg[0],
            "model": cfg[1],
            "level": cfg[2],
            "mode": mode,
            "usecase": usecase,
            "rep": rep,
            "word": WORDS[rep],
            "session": session.id,
            "seconds": round(seconds, 2),
            "ok": error is None,
            "error": error,
            "answer": answer,
            "at": time.time(),
        }
        with lock, results.open("a") as out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")

    def timed_turn(session, question, card):
        started = time.monotonic()
        try:
            answer = session.turn(question, card).run()
            return time.monotonic() - started, answer, None
        except chat.TurnError as error:
            return time.monotonic() - started, None, str(error)

    def one(job) -> None:
        cfg, mode, usecase, rep = job
        config = chat.Config.from_mapping(
            {
                **templates(*cfg, mode),
                "cwd": str(repo if mode == "repo" else BARE),
                "timeout_s": TIMEOUT_S,
                "preamble": PREAMBLE,
            }
        )
        session = chat.Session(config)
        seconds, answer, error = timed_turn(session, QUESTIONS[usecase], cards[rep])
        record(cfg, mode, usecase, rep, session, seconds, answer, error)
        if usecase == "explain" and error is None:
            seconds, answer, error = timed_turn(session, FOLLOW_UP[1], cards[rep])
            record(cfg, mode, FOLLOW_UP[0], rep, session, seconds, answer, error)
        with lock:
            counter[0] += 1
            print(
                f"[{counter[0]}/{len(jobs)}] {' '.join(cfg)} {mode} {usecase} "
                f"{seconds:.1f}s{' ERROR ' + error[:80] if error else ''}",
                flush=True,
            )

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, jobs))


def summary(results: Path) -> None:
    rows = [json.loads(line) for line in results.open()]
    usecases = [*QUESTIONS, FOLLOW_UP[0]]
    for mode in MODES:
        print(f"\n### {mode} mode — mean seconds per answer (n=3 each)\n")
        print("| config | " + " | ".join(usecases) + " | mean | median | errors |")
        print("|---" * (len(usecases) + 4) + "|")
        for cli, model, level in CONFIGS:
            mine = [
                r
                for r in rows
                if (r["cli"], r["model"], r["level"], r["mode"]) == (cli, model, level, mode)
            ]
            if not mine:
                continue
            ok = [r["seconds"] for r in mine if r["ok"]]
            cells = []
            for usecase in usecases:
                times = [r["seconds"] for r in mine if r["usecase"] == usecase and r["ok"]]
                cells.append(f"{statistics.mean(times):.1f}" if times else "–")
            name = f"{cli} {model.split('/')[-1]} {level}"
            stats = f"{statistics.mean(ok):.1f} | {statistics.median(ok):.1f}" if ok else "– | –"
            print(
                f"| {name} | "
                + " | ".join(cells)
                + f" | {stats} | {sum(not r['ok'] for r in mine)} |"
            )


def cleanup(results: Path) -> None:
    """Delete the CLI sessions the benchmark created (they would clutter `--resume`)."""
    ids = {json.loads(line)["session"] for line in results.open()}
    roots = [Path("~/.claude/projects").expanduser(), Path("~/.pi/agent/sessions").expanduser()]
    removed = 0
    for root in roots:
        for path in root.glob("*/*"):
            if any(session in path.name for session in ids):
                shutil.rmtree(path) if path.is_dir() else path.unlink()
                removed += 1
    for root in roots:  # the bare mode's empty working directory got project folders too
        for folder in root.glob("*anki-card-chat-bench-empty*"):
            shutil.rmtree(folder)
            removed += 1
    print(f"removed {removed} session files/folders")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["run", "summary", "cleanup"])
    parser.add_argument("results", type=Path)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--deck", help="run: the deck the benchmark words are read from")
    parser.add_argument("--repo", type=Path, help="run: the directory the CLI runs in, repo mode")
    args = parser.parse_args()
    if args.command == "run":
        if not args.deck or ("repo" in args.modes and not args.repo):
            parser.error("run needs --deck, and --repo unless --modes bare")
        repo = args.repo.expanduser() if args.repo else None
        run(args.results, args.workers, args.modes, args.deck, repo)
    elif args.command == "summary":
        summary(args.results)
    else:
        cleanup(args.results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
