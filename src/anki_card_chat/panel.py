"""The chat panel: a dock on the main window's right, hidden until asked for."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future

from aqt.qt import (
    QDockWidget,
    QEvent,
    QHBoxLayout,
    QKeyEvent,
    QKeySequence,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    Qt,
    QTextBrowser,
    QTimer,
    QVBoxLayout,
    QWidget,
)

from . import chat

RESET_KEYS = QKeySequence("Ctrl+L")
_TYPING_MODIFIERS = Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.KeypadModifier


class _Input(QPlainTextEdit):
    """Enter sends, Shift+Enter breaks the line, Esc closes, Ctrl+L (⌘L on macOS) resets.

    The reviewer's shortcuts (1-4, space, Enter, e, ...) are registered on the main
    window and would fire while typing here, answering the card. Claiming the
    shortcut override for plain keys sends them to this widget instead.
    """

    def __init__(
        self,
        on_send: Callable[[], None],
        on_escape: Callable[[], None],
        on_reset: Callable[[], None],
    ) -> None:
        super().__init__()
        self._on_send = on_send
        self._on_escape = on_escape
        self._on_reset = on_reset

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ShortcutOverride and self._claims(event):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.insertPlainText("\n")
            else:
                self._on_send()
        elif event.key() == Qt.Key.Key_Escape:
            self._on_escape()
        elif _is_reset(event):
            self._on_reset()
        else:
            super().keyPressEvent(event)

    @staticmethod
    def _claims(event: QKeyEvent) -> bool:
        plain = not (event.modifiers() & ~_TYPING_MODIFIERS)
        return plain or _is_reset(event)


def _is_reset(event: QKeyEvent) -> bool:
    return QKeySequence(event.keyCombination()) == RESET_KEYS


class ChatPanel(QDockWidget):
    def __init__(
        self,
        mw,
        load_config: Callable[[], chat.Config],
        current_card: Callable[[], chat.Card | None],
    ) -> None:
        super().__init__("Card chat", mw)
        self.mw = mw
        self._load_config = load_config
        self._current_card = current_card
        self._session: chat.Session | None = None
        self._turn: chat.Turn | None = None
        self._transcript: list[str] = []
        self._shown_card_id: int | None = None
        self._started_at = 0.0

        self.setObjectName(chat.NAME)
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )

        self.status = QLabel()
        reset = QPushButton("Reset")
        native = RESET_KEYS.toString(QKeySequence.SequenceFormat.NativeText)
        reset.setToolTip(f"Start a new conversation ({native})")
        reset.clicked.connect(self.reset)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.input = _Input(self._send, self.hide, self.reset)
        self.input.setPlaceholderText("Ask about this card - Enter sends, Esc closes")
        self.input.setMaximumHeight(90)

        header = QHBoxLayout()
        header.addWidget(self.status, 1)
        header.addWidget(reset)
        body = QVBoxLayout()
        body.addLayout(header)
        body.addWidget(self.view, 1)
        body.addWidget(self.input)
        container = QWidget()
        container.setLayout(body)
        self.setWidget(container)

        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)
        self.visibilityChanged.connect(self._on_visibility)

        mw.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self)
        self.hide()
        self.reset()

    def open_panel(self) -> None:
        self.show()
        self.raise_()
        self.input.setFocus()

    def reset(self) -> None:
        """Drop the conversation (and any answer still coming) and start a new one."""
        self.cancel()
        self._transcript.clear()
        self._shown_card_id = None
        try:
            self._session = chat.Session(self._load_config())
            self.status.setText("New conversation")
        except (ValueError, TypeError) as error:
            self._session = None
            self.status.setText(f"Config problem: {error}")
        self._render()

    def cancel(self) -> None:
        if self._turn is not None:
            self._turn.cancel()
            self._turn = None
        self._clock.stop()

    def _send(self) -> None:
        question = self.input.toPlainText().strip()
        if not question or self._turn is not None:
            return
        card = self._current_card()
        if card is None:
            self.status.setText("Flip the card first")
            return
        if self._session is None:
            self.reset()
            if self._session is None:
                return

        if card.card_id != self._shown_card_id:
            self._transcript.append(f"*— {card.label()} —*")
            self._shown_card_id = card.card_id
        self._transcript.append("\n".join(f"> {line}" for line in question.splitlines()))
        self.input.clear()
        self._render()

        turn = self._session.turn(question, card)
        self._turn = turn
        self._started_at = time.monotonic()
        self._tick()
        self._clock.start()
        self.mw.taskman.run_in_background(
            turn.run, lambda future: self._finished(turn, future), uses_collection=False
        )

    def _finished(self, turn: chat.Turn, future: Future) -> None:
        if turn is not self._turn:  # reset while it ran
            return
        self._turn = None
        self._clock.stop()
        elapsed = time.monotonic() - self._started_at
        try:
            self._transcript.append(future.result())
            self.status.setText(f"Answered in {elapsed:.1f}s")
        except chat.TurnCancelledError:
            return
        except Exception as error:  # noqa: BLE001 - show every failure in the panel
            self._transcript.append(f"**Error**\n\n```\n{error}\n```")
            self.status.setText(f"Failed after {elapsed:.1f}s")
        self._render()

    def _tick(self) -> None:
        self.status.setText(f"Thinking… {time.monotonic() - self._started_at:.0f}s")

    def _render(self) -> None:
        self.view.setMarkdown("\n\n".join(self._transcript))
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_visibility(self, visible: bool) -> None:
        if not visible and self.mw.web is not None:
            self.mw.web.setFocus()  # back to the reviewer, so 1-4 answer again
