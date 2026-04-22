"""tts-studio engine tests (offline)."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from studio_engine import (  # noqa: E402
    concat_audio_command,
    parse_dialogue_script,
)


def test_parser_two_speakers_with_voice_map() -> None:
    s = parse_dialogue_script(
        "A: hello\nB: hi there\nA: bye",
        default_voice="V_DEF",
        voice_map={"A": "V_A", "B": "V_B"},
    )
    assert len(s.segments) == 3
    assert s.segments[0].speaker == "A" and s.segments[0].voice == "V_A"
    assert s.segments[1].speaker == "B" and s.segments[1].voice == "V_B"
    assert s.segments[2].speaker == "A" and s.segments[2].text == "bye"


def test_parser_continuation_lines_merge() -> None:
    s = parse_dialogue_script(
        "A: 第一行\n续写一下\n再续一段\nB: 我来了",
        default_voice="V",
    )
    assert len(s.segments) == 2
    assert "续写一下" in s.segments[0].text
    assert "再续一段" in s.segments[0].text
    assert s.segments[1].speaker == "B"


def test_parser_no_speaker_fallback_to_narrator() -> None:
    s = parse_dialogue_script("一段独白\n继续独白", default_voice="V")
    assert len(s.segments) == 1
    assert s.segments[0].speaker == "旁白"


def test_parser_chinese_colon() -> None:
    s = parse_dialogue_script("张三：你好\n李四：你也好", default_voice="V")
    assert len(s.segments) == 2
    assert s.segments[0].speaker == "张三"


def test_parser_empty_input_returns_empty_segments() -> None:
    s = parse_dialogue_script("", default_voice="V")
    assert s.segments == []


def test_concat_command_writes_list_file(tmp_path) -> None:
    parts = [tmp_path / "a.mp3", tmp_path / "b.mp3"]
    for p in parts:
        p.write_bytes(b"x")
    list_file = tmp_path / "list.txt"
    cmd = concat_audio_command(parts=parts, list_file=list_file,
                                output=tmp_path / "out.mp3", ffmpeg="ffmpeg")
    assert list_file.exists()
    contents = list_file.read_text(encoding="utf-8")
    assert "file '" in contents
    assert "a.mp3" in contents and "b.mp3" in contents
    joined = " ".join(cmd)
    assert "concat" in joined and "-c copy" in joined
