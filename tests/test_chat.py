from __future__ import annotations

import os
import sys
import threading
import time

import pytest

from anki_card_chat import chat

CARD = chat.Card(
    card_id=11,
    note_id=1,
    deck="Mandarin::Vocab",
    note_type="Chinese Vocab",
    card_type="Recognition",
    fields=(
        ("ZH", "<b>差不多</b>"),
        ("Audio", "[sound:chabuduo.mp3]"),
        ("EN", "almost;&nbsp;about the same<br>roughly"),
        ("Empty", ""),
    ),
)
OTHER = chat.Card(12, 2, "Mandarin::Vocab", "Chinese Vocab", "Recognition", (("ZH", "而"),))


def config(**overrides) -> chat.Config:
    values = {
        "first_turn": ["cli", "-p", "{prompt}", "--session-id", "{session}"],
        "next_turn": ["cli", "-p", "{prompt}", "--resume", "{session}"],
        "cwd": "/tmp",
        "preamble": "Be brief.",
    }
    return chat.Config.from_mapping({**values, **overrides})


def test_field_text_strips_markup_audio_and_entities():
    assert chat.field_text("<b>差不多</b>[sound:x.mp3]") == "差不多"
    assert chat.field_text("a&nbsp;b<br>c<div>d</div>") == "a\xa0b\nc\nd"
    assert chat.field_text('<img src="x.png">') == "[image]"


def test_card_block_skips_empty_fields_and_indents_continuation_lines():
    block = chat.card_block(CARD)
    assert block.splitlines() == [
        '<card deck="Mandarin::Vocab" note_type="Chinese Vocab" '
        'card_type="Recognition" note_id="1">',
        "ZH: 差不多",
        "EN: almost;\xa0about the same",
        "  roughly",
        "</card>",
    ]


def test_card_label_is_first_non_empty_field():
    assert CARD.label() == "差不多"


def test_first_prompt_has_preamble_card_and_question():
    session = chat.Session(config(), session_id="s1")
    prompt = session.prompt_for("  what does it mean?  ", CARD)
    assert prompt == "\n\n".join(["Be brief.", chat.card_block(CARD), "what does it mean?"])


def test_card_is_only_resent_when_it_changes():
    session = chat.Session(config(), session_id="s1")
    session.started, session._sent_card_id = True, CARD.card_id
    assert session.prompt_for("again?", CARD) == "again?"
    assert session.prompt_for("and this?", OTHER) == chat.card_block(OTHER) + "\n\nand this?"


def test_argv_switches_template_after_the_first_turn():
    session = chat.Session(config(), session_id="s1")
    assert session.argv_for("hi") == ["cli", "-p", "hi", "--session-id", "s1"]
    session.started = True
    assert session.argv_for("hi") == ["cli", "-p", "hi", "--resume", "s1"]


def test_placeholders_in_the_prompt_are_not_expanded():
    session = chat.Session(config(), session_id="s1")
    assert session.argv_for("{session}")[2] == "{session}"


def test_config_defaults():
    cfg = chat.Config.from_mapping({"first_turn": ["cli"], "cwd": "~/x"})
    assert cfg.next_turn == ("cli",)
    assert cfg.cwd == os.path.expanduser("~/x")
    assert cfg.timeout_s == 120
    with pytest.raises(ValueError):
        chat.Config.from_mapping({"first_turn": []})


def python_cli(script: str) -> list[str]:
    return [sys.executable, "-c", script, "{prompt}", "{session}"]


def test_successful_turn_returns_stdout_and_advances_the_session():
    cli = python_cli("import sys; print('answer to', repr(sys.argv[1][-3:]))")
    session = chat.Session(config(first_turn=cli, next_turn=cli), session_id="s1")
    assert session.turn("ok?", CARD).run() == "answer to 'ok?'"
    assert session.started
    assert session.prompt_for("more", CARD) == "more"


def test_failed_turn_raises_and_leaves_the_session_where_it_was():
    cli = python_cli("import sys; print('boom', file=sys.stderr); sys.exit(3)")
    session = chat.Session(config(first_turn=cli), session_id="s1")
    with pytest.raises(chat.TurnError, match="exited 3: boom"):
        session.turn("ok?", CARD).run()
    assert not session.started
    assert session.prompt_for("ok?", CARD).startswith("Be brief.")


def test_missing_executable_is_a_turn_error():
    with pytest.raises(chat.TurnError, match="could not start"):
        chat.Turn(["/nonexistent/cli"], "/tmp", 5).run()


def test_timeout_kills_the_cli():
    turn = chat.Turn([sys.executable, "-c", "import time; time.sleep(30)"], "/tmp", 0.5)
    started = time.monotonic()
    with pytest.raises(chat.TurnError, match="no answer after 0.5s"):
        turn.run()
    assert time.monotonic() - started < 5


def test_cancel_stops_a_running_turn():
    turn = chat.Turn([sys.executable, "-c", "import time; time.sleep(30)"], "/tmp", 60)
    outcome: list[BaseException] = []

    def run() -> None:
        try:
            turn.run()
        except BaseException as error:  # noqa: BLE001
            outcome.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(0.3)
    turn.cancel()
    worker.join(5)
    assert not worker.is_alive()
    assert isinstance(outcome[0], chat.TurnCancelledError)


def test_absolute_cli_directory_goes_first_on_path():
    env = chat._child_env("/opt/tools/bin/cli")
    assert env["PATH"].split(os.pathsep)[0] == "/opt/tools/bin"
