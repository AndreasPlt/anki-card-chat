"""Anki add-on: ask an agent CLI about the card under review.

Press the shortcut (default `c`) on the answer side and a chat panel opens; Esc
hides it again. One conversation per review session, reset from the panel.

* :mod:`anki_card_chat.chat` - prompt, session and CLI runs; no Anki imports.
* :mod:`anki_card_chat.panel` - the Qt dock.
"""

from __future__ import annotations

from . import chat

try:  # pragma: no cover - only available inside Anki
    from aqt import gui_hooks, mw
    from aqt.utils import tooltip
except ImportError:  # imported outside Anki (tests)
    gui_hooks = mw = tooltip = None

_panel = None


def _config() -> dict:
    return mw.addonManager.getConfig(__name__) or {}


def _current_card() -> chat.Card | None:
    """The card on screen, but only once its answer is showing."""
    reviewer = mw.reviewer
    if mw.state != "review" or reviewer.state != "answer" or reviewer.card is None:
        return None
    card = reviewer.card
    note = card.note()
    return chat.Card(
        card_id=card.id,
        note_id=note.id,
        deck=mw.col.decks.name(card.current_deck_id()),
        note_type=note.note_type()["name"],
        card_type=card.template()["name"],
        fields=tuple(note.items()),
    )


def _open() -> None:
    global _panel
    if _current_card() is None:
        tooltip("Card chat opens on the answer side")
        return
    if _panel is None:
        from .panel import ChatPanel

        _panel = ChatPanel(mw, lambda: chat.Config.from_mapping(_config()), _current_card)
    _panel.open_panel()


def _on_shortcuts(state: str, shortcuts: list) -> None:
    if state == "review":
        shortcuts.append((_config().get("shortcut", "c"), _open))


def _on_question(_card) -> None:
    if _panel is not None:
        _panel.hide()


def _on_state(new_state: str, old_state: str) -> None:
    if _panel is None:
        return
    if new_state == "review" and old_state != "review":
        _panel.reset()
    elif new_state != "review":
        _panel.hide()


def _on_profile_close() -> None:
    if _panel is not None:
        _panel.cancel()


if gui_hooks is not None:  # pragma: no cover - only inside Anki
    gui_hooks.state_shortcuts_will_change.append(_on_shortcuts)
    gui_hooks.reviewer_did_show_question.append(_on_question)
    gui_hooks.state_did_change.append(_on_state)
    gui_hooks.profile_will_close.append(_on_profile_close)
