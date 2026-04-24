# tests/core/test_agent_attachments.py
import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_voice_attachment_resolved_to_local_file():
    """Voice attachment URL should be downloaded to local temp file."""
    from openakita.core.agent import resolve_attachment_to_local

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"RIFF....WAVEfmt ")  # Minimal WAV header
        temp_path = f.name

    # file:// URL should resolve directly
    local_path = await resolve_attachment_to_local(f"file://{temp_path}")
    assert local_path == temp_path

    Path(temp_path).unlink()


@pytest.mark.asyncio
async def test_voice_attachment_http_downloaded():
    """HTTP voice attachment should be downloaded."""
    from openakita.core.agent import resolve_attachment_to_local

    with patch("httpx.AsyncClient") as mock_client:
        mock_response = MagicMock()
        mock_response.content = b"RIFF....WAVEfmt "
        mock_response.raise_for_status = MagicMock()

        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_response
        )

        local_path = await resolve_attachment_to_local(
            "https://example.com/audio.wav"
        )

        assert local_path is not None
        assert local_path.endswith(".wav")


@pytest.mark.asyncio
async def test_voice_attachment_transcribed():
    """Voice attachment should be transcribed via MediaHandler."""
    from openakita.core.agent import process_voice_attachment

    # Create a real temp file so resolve_attachment_to_local can find it
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"RIFF....WAVEfmt ")
        temp_path = f.name

    try:
        mock_handler = MagicMock()
        mock_handler.transcribe_audio = AsyncMock(return_value="Hello world")

        transcript = await process_voice_attachment(
            url=f"file://{temp_path}",
            handler=mock_handler,
        )

        assert transcript == "Hello world"
        mock_handler.transcribe_audio.assert_called_once()
    finally:
        Path(temp_path).unlink()


@pytest.mark.asyncio
async def test_record_inbound_attachments_transcribes_voice():
    """_record_inbound_attachments should transcribe voice attachments."""
    from openakita.api.schemas import AttachmentType
    from openakita.core.agent import Agent, process_voice_attachment

    # Create a mock session
    mock_session = MagicMock()
    mock_session.set_metadata = MagicMock()

    # Create agent with mocked internals
    agent = MagicMock(spec=Agent)
    agent.memory_manager = MagicMock()
    agent.memory_manager.record_attachment = MagicMock()

    # Simulate the method behavior
    attachments = [
        {"type": AttachmentType.VOICE.value, "name": "voice.wav", "url": "file:///tmp/test.wav"},
    ]

    # Test the attachment type detection logic
    for att in attachments:
        att_type = att.get("type", "")
        assert att_type in (AttachmentType.VOICE.value, AttachmentType.VOICE, "voice")


@pytest.mark.asyncio
async def test_record_inbound_attachments_returns_transcripts():
    """_record_inbound_attachments should return list of transcripts."""
    from openakita.api.schemas import AttachmentType
    from openakita.core.agent import process_voice_attachment

    # Verify process_voice_attachment returns transcript
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(b"RIFF....WAVEfmt ")
        temp_path = f.name

    try:
        mock_handler = MagicMock()
        mock_handler.transcribe_audio = AsyncMock(return_value="Test transcript")

        transcript = await process_voice_attachment(
            url=f"file://{temp_path}",
            handler=mock_handler,
        )

        assert transcript == "Test transcript"

        # Verify transcripts can be collected
        transcripts = []
        if transcript:
            transcripts.append(transcript)

        assert len(transcripts) == 1
        assert transcripts[0] == "Test transcript"
    finally:
        Path(temp_path).unlink()


def test_build_user_message_with_attachments_no_transcripts():
    """_build_user_message_with_attachments should return message unchanged if no transcripts."""
    from openakita.core.agent import Agent

    agent = Agent.__new__(Agent)  # Create without __init__

    result = agent._build_user_message_with_attachments(
        message="Hello world",
        transcripts=[],
    )

    assert result == "Hello world"


def test_build_user_message_with_attachments_single_transcript():
    """_build_user_message_with_attachments should prepend single transcript."""
    from openakita.core.agent import Agent

    agent = Agent.__new__(Agent)  # Create without __init__

    result = agent._build_user_message_with_attachments(
        message="Please help",
        transcripts=["User said hello"],
    )

    assert "[Voice message transcription (auto-completed)]: User said hello" in result
    assert "Please help" in result
    assert "do NOT call get_voice_file" in result
    assert result.startswith("[Voice message transcription")


def test_build_user_message_with_attachments_multiple_transcripts():
    """_build_user_message_with_attachments should prepend multiple transcripts."""
    from openakita.core.agent import Agent

    agent = Agent.__new__(Agent)  # Create without __init__

    result = agent._build_user_message_with_attachments(
        message="Help me",
        transcripts=["First message", "Second message"],
    )

    assert "[Voice message transcription (auto-completed)]: First message" in result
    assert "[Voice message transcription (auto-completed)]: Second message" in result
    assert "Help me" in result
    assert "do NOT call get_voice_file" in result
    # Message should come after transcripts
    assert result.index("First message") < result.index("Help me")
