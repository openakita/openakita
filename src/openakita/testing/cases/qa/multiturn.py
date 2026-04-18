"""
Multi-turn conversation test cases (35)
"""

from openakita.testing.runner import TestCase

MULTITURN_TESTS = [
    # Context memory
    TestCase(
        id="qa_context_001",
        category="qa",
        subcategory="context",
        description="Remember name",
        input=[
            {"role": "user", "content": "My name is Xiao Ming"},
            {"role": "assistant", "content": "Hello Xiao Ming!"},
            {"role": "user", "content": "What is my name?"},
        ],
        expected="contains:Xiao Ming",
        tags=["context", "memory"],
    ),
    TestCase(
        id="qa_context_002",
        category="qa",
        subcategory="context",
        description="Remember number",
        input=[
            {"role": "user", "content": "Remember this number: 42"},
            {"role": "assistant", "content": "Okay, I have remembered 42."},
            {"role": "user", "content": "What is that number multiplied by 2?"},
        ],
        expected="contains:84",
        tags=["context", "memory", "math"],
    ),
    TestCase(
        id="qa_context_003",
        category="qa",
        subcategory="context",
        description="Coreference resolution",
        input=[
            {"role": "user", "content": "Python is a programming language"},
            {"role": "assistant", "content": "Yes, Python is a popular programming language."},
            {"role": "user", "content": "When was it invented?"},
        ],
        expected="contains:1991",
        tags=["context", "coreference"],
    ),
    # Topic tracking
    TestCase(
        id="qa_topic_001",
        category="qa",
        subcategory="topic",
        description="Topic continuation",
        input=[
            {"role": "user", "content": "Tell me about machine learning"},
            {"role": "assistant", "content": "Machine learning is a branch of artificial intelligence..."},
            {"role": "user", "content": "What is the difference between it and deep learning?"},
        ],
        expected="length>=50",
        tags=["topic", "ml"],
    ),
    TestCase(
        id="qa_topic_002",
        category="qa",
        subcategory="topic",
        description="Topic switch",
        input=[
            {"role": "user", "content": "How is the weather today?"},
            {"role": "assistant", "content": "Sorry, I cannot access real-time weather information."},
            {"role": "user", "content": "Then help me write some Python code to print hello"},
        ],
        expected="contains:print",
        tags=["topic", "switch"],
    ),
    # Instruction tracking
    TestCase(
        id="qa_instruction_001",
        category="qa",
        subcategory="instruction",
        description="Continue following instructions",
        input=[
            {"role": "user", "content": "From now on, answer my questions in English"},
            {"role": "assistant", "content": "Sure, I will answer in English."},
            {"role": "user", "content": "Hello"},
        ],
        expected="regex:(Hello|Hi|Greetings)",
        tags=["instruction", "follow"],
    ),
    TestCase(
        id="qa_instruction_002",
        category="qa",
        subcategory="instruction",
        description="Format persistence",
        input=[
            {"role": "user", "content": "Answer questions in JSON format"},
            {"role": "assistant", "content": "Okay, I will answer in JSON format."},
            {"role": "user", "content": "What is 1+1?"},
        ],
        expected="contains:{",
        tags=["instruction", "format"],
    ),
    # Correction and clarification
    TestCase(
        id="qa_correct_001",
        category="qa",
        subcategory="correction",
        description="Accept correction",
        input=[
            {"role": "user", "content": "Who invented Python?"},
            {"role": "assistant", "content": "Python was invented by Guido van Rossum."},
            {"role": "user", "content": "No, I asked who invented it, not when"},
        ],
        expected="contains:Guido",
        tags=["correction"],
    ),
    TestCase(
        id="qa_correct_002",
        category="qa",
        subcategory="clarification",
        description="Request clarification",
        input=[
            {"role": "user", "content": "Help me process that file"},
        ],
        expected="contains:which",
        tags=["clarification"],
    ),
]


def get_tests() -> list[TestCase]:
    return MULTITURN_TESTS
