"""
Basic QA test cases (30 items)
"""

from openakita.testing.runner import TestCase

# Basic knowledge QA test cases
QA_BASIC_TESTS = [
    # Math
    TestCase(
        id="qa_math_001",
        category="qa",
        subcategory="math",
        description="Basic addition",
        input="123 + 456 等于多少？",
        expected="contains:579",
        tags=["math", "basic"],
    ),
    TestCase(
        id="qa_math_002",
        category="qa",
        subcategory="math",
        description="Basic multiplication",
        input="12 x 12 等于多少？",
        expected="contains:144",
        tags=["math", "basic"],
    ),
    TestCase(
        id="qa_math_003",
        category="qa",
        subcategory="math",
        description="Percentage calculation",
        input="200 的 15% 是多少？",
        expected="contains:30",
        tags=["math", "percentage"],
    ),
    TestCase(
        id="qa_math_004",
        category="qa",
        subcategory="math",
        description="Fraction calculation",
        input="1/4 + 1/2 等于多少？",
        expected="contains:3/4",
        tags=["math", "fraction"],
    ),
    TestCase(
        id="qa_math_005",
        category="qa",
        subcategory="math",
        description="Square root calculation",
        input="144 的平方根是多少？",
        expected="contains:12",
        tags=["math", "sqrt"],
    ),
    # Programming
    TestCase(
        id="qa_prog_001",
        category="qa",
        subcategory="programming",
        description="Python list comprehension",
        input="用 Python 列表推导式生成 1-10 的平方数列表",
        expected="contains:[x**2",
        tags=["python", "list_comprehension"],
    ),
    TestCase(
        id="qa_prog_002",
        category="qa",
        subcategory="programming",
        description="What is recursion",
        input="解释什么是递归",
        expected="length>=50",
        tags=["concept", "recursion"],
    ),
    TestCase(
        id="qa_prog_003",
        category="qa",
        subcategory="programming",
        description="HTTP status code 404",
        input="HTTP 状态码 404 是什么意思？",
        expected="contains:找不到",
        tags=["http", "status_code"],
    ),
    TestCase(
        id="qa_prog_004",
        category="qa",
        subcategory="programming",
        description="Basic Git commands",
        input="如何用 git 查看提交历史？",
        expected="contains:git log",
        tags=["git", "command"],
    ),
    TestCase(
        id="qa_prog_005",
        category="qa",
        subcategory="programming",
        description="JSON format",
        input="给出一个包含姓名和年龄的 JSON 示例",
        expected="regex:\\{.*name.*\\}",
        tags=["json", "format"],
    ),
    # General knowledge
    TestCase(
        id="qa_common_001",
        category="qa",
        subcategory="common",
        description="Days in a year",
        input="一年有多少天？",
        expected="contains:365",
        tags=["common", "time"],
    ),
    TestCase(
        id="qa_common_002",
        category="qa",
        subcategory="common",
        description="Chemical formula for water",
        input="水的化学式是什么？",
        expected="contains:H2O",
        tags=["common", "chemistry"],
    ),
    TestCase(
        id="qa_common_003",
        category="qa",
        subcategory="common",
        description="Earth orbiting the sun",
        input="地球绕太阳一周需要多长时间？",
        expected="contains:一年",
        tags=["common", "astronomy"],
    ),
    TestCase(
        id="qa_common_004",
        category="qa",
        subcategory="common",
        description="Number of bones in the human body",
        input="成人有多少块骨骼？",
        expected="contains:206",
        tags=["common", "biology"],
    ),
    TestCase(
        id="qa_common_005",
        category="qa",
        subcategory="common",
        description="Speed of light",
        input="光在真空中的速度是多少？",
        expected="regex:30\\d+",
        tags=["common", "physics"],
    ),
]


# Export
def get_tests() -> list[TestCase]:
    return QA_BASIC_TESTS
