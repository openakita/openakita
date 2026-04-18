"""
Core exception classes.
"""


class UserCancelledError(Exception):
    """Raised when the user actively cancels the current task.

    Thrown when the user sends a stop command (e.g. "stop", "cancel",
    or the Chinese equivalents), used to abort an in-progress LLM call
    or tool execution.

    Attributes:
        reason: Cancellation reason (typically the user's raw command)
        source: Phase where cancellation occurred ("llm_call" / "tool_exec")
    """

    def __init__(self, reason: str = "", source: str = ""):
        self.reason = reason
        self.source = source
        super().__init__(f"User cancelled ({source}): {reason}")
