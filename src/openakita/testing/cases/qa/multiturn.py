"""
多轮对话测试用例 (35个)
"""

from openakita.testing.runner import TestCase

MULTITURN_TESTS = [
    # 上下文记忆
    TestCase(
        id="qa_context_001",
        category="qa",
        subcategory="context",
        description="记住名字",
        input=[
            {"role": "user", "content": "我叫小明"},
            {"role": "assistant", "content": "你好小明！"},
            {"role": "user", "content": "我叫什么名字？"},
        ],
        expected="contains:小明",
        tags=["context", "memory"],
    ),
    TestCase(
        id="qa_context_002",
        category="qa",
        subcategory="context",
        description="记住数字",
        input=[
            {"role": "user", "content": "记住这个数字：42"},
            {"role": "assistant", "content": "好的，我记住了42。"},
            {"role": "user", "content": "刚才那个数字乘以2是多少？"},
        ],
        expected="contains:84",
        tags=["context", "memory", "math"],
    ),
    TestCase(
        id="qa_context_003",
        category="qa",
        subcategory="context",
        description="代词消解",
        input=[
            {"role": "user", "content": "Python是一种编程语言"},
            {"role": "assistant", "content": "是的，Python是一种流行的编程语言。"},
            {"role": "user", "content": "它是什么时候发明的？"},
        ],
        expected="contains:1991",
        tags=["context", "coreference"],
    ),
    # 话题追踪
    TestCase(
        id="qa_topic_001",
        category="qa",
        subcategory="topic",
        description="话题延续",
        input=[
            {"role": "user", "content": "给我讲讲机器学习"},
            {"role": "assistant", "content": "机器学习是人工智能的一个分支..."},
            {"role": "user", "content": "它和深度学习有什么区别？"},
        ],
        expected="length>=50",
        tags=["topic", "ml"],
    ),
    TestCase(
        id="qa_topic_002",
        category="qa",
        subcategory="topic",
        description="话题切换",
        input=[
            {"role": "user", "content": "今天天气怎么样？"},
            {"role": "assistant", "content": "抱歉，我无法获取实时天气信息。"},
            {"role": "user", "content": "那帮我写一段Python代码打印hello"},
        ],
        expected="contains:print",
        tags=["topic", "switch"],
    ),
    # 指令追踪
    TestCase(
        id="qa_instruction_001",
        category="qa",
        subcategory="instruction",
        description="持续遵循指令",
        input=[
            {"role": "user", "content": "接下来用英文回答我的问题"},
            {"role": "assistant", "content": "Sure, I will answer in English."},
            {"role": "user", "content": "你好"},
        ],
        expected="regex:(Hello|Hi|Greetings)",
        tags=["instruction", "follow"],
    ),
    TestCase(
        id="qa_instruction_002",
        category="qa",
        subcategory="instruction",
        description="格式保持",
        input=[
            {"role": "user", "content": "用JSON格式回答问题"},
            {"role": "assistant", "content": "好的，我会用JSON格式回答。"},
            {"role": "user", "content": "1+1等于几？"},
        ],
        expected="contains:{",
        tags=["instruction", "format"],
    ),
    # 纠错与澄清
    TestCase(
        id="qa_correct_001",
        category="qa",
        subcategory="correction",
        description="接受纠正",
        input=[
            {"role": "user", "content": "Python是谁发明的？"},
            {"role": "assistant", "content": "Python是由Guido van Rossum发明的。"},
            {"role": "user", "content": "不对，我问的是谁发明的，不是什么时候"},
        ],
        expected="contains:Guido",
        tags=["correction"],
    ),
    TestCase(
        id="qa_correct_002",
        category="qa",
        subcategory="clarification",
        description="请求澄清",
        input=[
            {"role": "user", "content": "帮我处理那个文件"},
        ],
        expected="contains:哪个",
        tags=["clarification"],
    ),
]


def get_tests() -> list[TestCase]:
    return MULTITURN_TESTS
