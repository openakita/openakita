"""Backend-owned continuation context for normal ask_user replies."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AskUserReplyContext:
    """Structured continuation for a normal ask_user answer."""

    answer: str
    message_id: str = ""

    def to_prompt_context(self) -> dict[str, str]:
        return {
            "answer": self.answer,
            "message_id": self.message_id,
        }
