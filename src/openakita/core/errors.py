"""
核心异常类
"""


class UserCancelledError(Exception):
    """用户主动取消当前任务。

    当用户发送停止指令（如"停止"、"stop"、"取消"）时抛出，
    用于中断正在执行的 LLM 调用或工具执行。

    Attributes:
        reason: 取消原因（通常是用户发送的原始指令）
        source: 取消发生的阶段 ("llm_call" / "tool_exec")
    """

    def __init__(self, reason: str = "", source: str = ""):
        self.reason = reason
        self.source = source
        super().__init__(f"User cancelled ({source}): {reason}")
