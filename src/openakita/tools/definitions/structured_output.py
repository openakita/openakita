"""
StructuredOutput 工具定义

参考 CC SyntheticOutputTool：仅在 API/SDK 模式下启用，
让模型以指定 JSON Schema 格式输出结构化数据。
"""

STRUCTURED_OUTPUT_TOOLS: list[dict] = [
    {
        "name": "structured_output",
        "category": "System",
        "should_defer": True,
        "description": (
            "Return a structured JSON response conforming to a pre-defined schema. "
            "Only available in API/SDK mode (non-interactive). Use when the caller "
            "requests structured output."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "description": "The structured data to return, conforming to the requested schema.",
                },
            },
            "required": ["data"],
        },
    },
]
