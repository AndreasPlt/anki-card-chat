"""Card-aware chat through an agent CLI (`claude -p`, `pi -p`, ...) - no Anki imports.

The panel drives this module, and so can anything else that wants to talk about
a card: it turns a card into prompt text, keeps the session, and runs one CLI
turn at a time. The CLI owns the conversation; we only hand it a session id.
"""

from __future__ import annotations

import contextlib
import html
import os
import re
import signal
import subprocess
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

NAME = "anki-card-chat"


class TurnError(Exception):
    """The CLI failed, timed out, or could not be started."""


class TurnCancelledError(Exception):
    """The turn was cancelled (reset) before it finished."""


@dataclass(frozen=True)
class Card:
    card_id: int
    note_id: int
    deck: str
    note_type: str
    card_type: str
    fields: tuple[tuple[str, str], ...]  # (name, raw field HTML), in note-type order

    def label(self) -> str:
        """Short human name for the card: its first non-empty field."""
        for _, value in self.fields:
            text = field_text(value)
            if text:
                return text.splitlines()[0]
        return f"card {self.card_id}"


@dataclass(frozen=True)
class Config:
    first_turn: tuple[str, ...]
    next_turn: tuple[str, ...]
    cwd: str
    timeout_s: float
    preamble: str

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> Config:
        first = tuple(str(arg) for arg in values.get("first_turn") or ())
        if not first:
            raise ValueError("config: first_turn must be a non-empty list")
        return cls(
            first_turn=first,
            next_turn=tuple(str(arg) for arg in values.get("next_turn") or first),
            cwd=os.path.expanduser(str(values.get("cwd") or "~")),
            timeout_s=float(values.get("timeout_s", 120)),
            preamble=str(values.get("preamble") or "").strip(),
        )


_SOUND = re.compile(r"\[sound:[^\]]*\]")
_IMAGE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_BREAK = re.compile(r"<br\s*/?>|</?(?:div|p|li|tr)\b[^>]*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_PLACEHOLDER = re.compile(r"\{(prompt|session)\}")


def field_text(value: str) -> str:
    """A field's HTML as plain text: tags and audio dropped, line breaks kept."""
    text = _SOUND.sub("", value)
    text = _IMAGE.sub("[image]", text)
    text = _BREAK.sub("\n", text)
    text = html.unescape(_TAG.sub("", text))
    lines = (line.strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def card_block(card: Card) -> str:
    header = (
        f'<card deck="{card.deck}" note_type="{card.note_type}" '
        f'card_type="{card.card_type}" note_id="{card.note_id}">'
    )
    lines = [header]
    for name, value in card.fields:
        text = field_text(value)
        if text:
            lines.append(f"{name}: " + text.replace("\n", "\n  "))
    lines.append("</card>")
    return "\n".join(lines)


class Session:
    """One conversation with the CLI. Reset by replacing it with a new Session."""

    def __init__(self, config: Config, session_id: str | None = None) -> None:
        self.config = config
        self.id = session_id or str(uuid.uuid4())
        self.started = False
        self._sent_card_id: int | None = None

    def prompt_for(self, question: str, card: Card) -> str:
        """The preamble on the first turn, the card whenever it changed, then the question."""
        parts = []
        if not self.started and self.config.preamble:
            parts.append(self.config.preamble)
        if card.card_id != self._sent_card_id:
            parts.append(card_block(card))
        parts.append(question.strip())
        return "\n\n".join(parts)

    def argv_for(self, prompt: str) -> list[str]:
        template = self.config.next_turn if self.started else self.config.first_turn
        values = {"prompt": prompt, "session": self.id}
        return [_PLACEHOLDER.sub(lambda m: values[m[1]], arg) for arg in template]

    def turn(self, question: str, card: Card) -> Turn:
        """Build the next turn. The session only advances once that turn succeeds."""
        argv = self.argv_for(self.prompt_for(question, card))

        def advance() -> None:
            self.started = True
            self._sent_card_id = card.card_id

        return Turn(argv, self.config.cwd, self.config.timeout_s, on_success=advance)


class Turn:
    """One CLI run. `run` blocks (call it off the UI thread); `cancel` is thread-safe."""

    def __init__(
        self,
        argv: Sequence[str],
        cwd: str,
        timeout_s: float,
        on_success: Callable[[], None] = lambda: None,
    ) -> None:
        self.argv = list(argv)
        self.cwd = cwd
        self.timeout_s = timeout_s
        self._on_success = on_success
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._cancelled = False

    def run(self) -> str:
        with self._lock:
            if self._cancelled:
                raise TurnCancelledError()
            try:
                self._proc = subprocess.Popen(
                    self.argv,
                    cwd=self.cwd,
                    env=_child_env(self.argv[0]),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,  # so a kill takes the CLI's children too
                )
            except OSError as error:
                raise TurnError(f"could not start {self.argv[0]}: {error}") from error
        proc = self._proc

        try:
            out, err = proc.communicate(timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            _kill(proc)
            proc.communicate()
            raise TurnError(f"no answer after {self.timeout_s:g}s") from None

        if self._cancelled:
            raise TurnCancelledError()
        if proc.returncode != 0:
            detail = (err or out).decode(errors="replace").strip()[-800:]
            raise TurnError(f"{os.path.basename(self.argv[0])} exited {proc.returncode}: {detail}")
        self._on_success()
        return out.decode(errors="replace").strip()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            if self._proc is not None and self._proc.poll() is None:
                _kill(self._proc)


def _kill(proc: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGTERM)


def _child_env(executable: str) -> dict[str, str]:
    """Anki started from the Dock has a bare PATH; put the CLI's own directory first.

    That is where Homebrew keeps both the CLI and `node`, which script-based
    CLIs such as `pi` need.
    """
    env = dict(os.environ)
    if os.path.isabs(executable):
        env["PATH"] = os.pathsep.join([os.path.dirname(executable), env.get("PATH", "")])
    return env
