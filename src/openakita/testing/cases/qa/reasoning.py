"""
Reasoning and logic test cases (35)
"""

from openakita.testing.runner import TestCase

REASONING_TESTS = [
    # Logical reasoning
    TestCase(
        id="qa_logic_001",
        category="qa",
        subcategory="logic",
        description="Simple syllogism",
        input="All men are mortal, Socrates is a man, therefore?",
        expected="contains:Socrates is mortal",
        tags=["logic", "syllogism"],
    ),
    TestCase(
        id="qa_logic_002",
        category="qa",
        subcategory="logic",
        description="Sequence pattern",
        input="Find the pattern: 2, 4, 8, 16, ?",
        expected="contains:32",
        tags=["logic", "sequence"],
    ),
    TestCase(
        id="qa_logic_003",
        category="qa",
        subcategory="logic",
        description="Age reasoning",
        input="Xiao Ming is 10 years old. His mother is 3 times his age. How old will his mother be in 5 years?",
        expected="contains:35",
        tags=["logic", "math"],
    ),
    TestCase(
        id="qa_logic_004",
        category="qa",
        subcategory="logic",
        description="Permutations",
        input="How many ways can 5 people be arranged in a line?",
        expected="contains:120",
        tags=["logic", "permutation"],
    ),
    TestCase(
        id="qa_logic_005",
        category="qa",
        subcategory="logic",
        description="Probability calculation",
        input="A coin is tossed twice. What is the probability of getting at least one head?",
        expected="contains:75%",
        tags=["logic", "probability"],
    ),
    # Code understanding
    TestCase(
        id="qa_code_001",
        category="qa",
        subcategory="code",
        description="Python code output",
        input="What does this code output?\n```python\nfor i in range(3):\n    print(i, end=' ')\n```",
        expected="contains:0 1 2",
        tags=["code", "python"],
    ),
    TestCase(
        id="qa_code_002",
        category="qa",
        subcategory="code",
        description="List slicing",
        input="lst = [1,2,3,4,5], what is lst[1:4]?",
        expected="contains:[2, 3, 4]",
        tags=["code", "python", "slice"],
    ),
    TestCase(
        id="qa_code_003",
        category="qa",
        subcategory="code",
        description="Dictionary operations",
        input="d = {'a': 1, 'b': 2}, what does d.get('c', 0) return?",
        expected="contains:0",
        tags=["code", "python", "dict"],
    ),
    TestCase(
        id="qa_code_004",
        category="qa",
        subcategory="code",
        description="Recursion understanding",
        input="```python\ndef f(n):\n    if n <= 1: return n\n    return f(n-1) + f(n-2)\n```\nWhat is f(6)?",
        expected="contains:8",
        tags=["code", "python", "recursion"],
    ),
    TestCase(
        id="qa_code_005",
        category="qa",
        subcategory="code",
        description="Time complexity",
        input="What is the time complexity of binary search?",
        expected="contains:log",
        tags=["code", "algorithm"],
    ),
    # Multi-step reasoning
    TestCase(
        id="qa_multi_001",
        category="qa",
        subcategory="multi_step",
        description="Multi-step math",
        input="A number plus 3, multiplied by 2, minus 4 equals 10. What is the number?",
        expected="contains:4",
        tags=["multi_step", "math"],
    ),
    TestCase(
        id="qa_multi_002",
        category="qa",
        subcategory="multi_step",
        description="Work problem",
        input="A can finish a job alone in 6 hours, B in 3 hours. How long does it take if they work together?",
        expected="contains:2",
        tags=["multi_step", "math"],
    ),
    TestCase(
        id="qa_multi_003",
        category="qa",
        subcategory="multi_step",
        description="Distance problem",
        input="A and B are 100 km apart. A travels at 60 km/h toward B, B travels at 40 km/h toward A. How long until they meet?",
        expected="contains:1",
        tags=["multi_step", "math"],
    ),
    # Analogical reasoning
    TestCase(
        id="qa_analogy_001",
        category="qa",
        subcategory="analogy",
        description="Word analogy",
        input="doctor:hospital = teacher:?",
        expected="contains:school",
        tags=["analogy"],
    ),
    TestCase(
        id="qa_analogy_002",
        category="qa",
        subcategory="analogy",
        description="Relational reasoning",
        input="hand:glove = foot:?",
        expected="contains:shoe",
        tags=["analogy"],
    ),
]


def get_tests() -> list[TestCase]:
    return REASONING_TESTS
