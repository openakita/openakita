"""
基础问答测试用例 (30个)
"""

from openakita.testing.runner import TestCase

# 基础知识问答测试用例
QA_BASIC_TESTS = [
    # 数学计算
    TestCase(
        id="qa_math_001",
        category="qa",
        subcategory="math",
        description="基础加法计算",
        input="123 + 456 等于多少？",
        expected="contains:579",
        tags=["math", "basic"],
    ),
    TestCase(
        id="qa_math_002",
        category="qa",
        subcategory="math",
        description="基础乘法计算",
        input="12 x 12 等于多少？",
        expected="contains:144",
        tags=["math", "basic"],
    ),
    TestCase(
        id="qa_math_003",
        category="qa",
        subcategory="math",
        description="百分比计算",
        input="200 的 15% 是多少？",
        expected="contains:30",
        tags=["math", "percentage"],
    ),
    TestCase(
        id="qa_math_004",
        category="qa",
        subcategory="math",
        description="分数计算",
        input="1/4 + 1/2 等于多少？",
        expected="contains:3/4",
        tags=["math", "fraction"],
    ),
    TestCase(
        id="qa_math_005",
        category="qa",
        subcategory="math",
        description="平方根计算",
        input="144 的平方根是多少？",
        expected="contains:12",
        tags=["math", "sqrt"],
    ),
    # 编程知识
    TestCase(
        id="qa_prog_001",
        category="qa",
        subcategory="programming",
        description="Python 列表推导式",
        input="用 Python 列表推导式生成 1-10 的平方数列表",
        expected="contains:[x**2",
        tags=["python", "list_comprehension"],
    ),
    TestCase(
        id="qa_prog_002",
        category="qa",
        subcategory="programming",
        description="什么是递归",
        input="解释什么是递归",
        expected="length>=50",
        tags=["concept", "recursion"],
    ),
    TestCase(
        id="qa_prog_003",
        category="qa",
        subcategory="programming",
        description="HTTP 状态码 404",
        input="HTTP 状态码 404 是什么意思？",
        expected="contains:找不到",
        tags=["http", "status_code"],
    ),
    TestCase(
        id="qa_prog_004",
        category="qa",
        subcategory="programming",
        description="Git 基本命令",
        input="如何用 git 查看提交历史？",
        expected="contains:git log",
        tags=["git", "command"],
    ),
    TestCase(
        id="qa_prog_005",
        category="qa",
        subcategory="programming",
        description="JSON 格式",
        input="给出一个包含姓名和年龄的 JSON 示例",
        expected="regex:\\{.*name.*\\}",
        tags=["json", "format"],
    ),
    # 常识问答
    TestCase(
        id="qa_common_001",
        category="qa",
        subcategory="common",
        description="一年有多少天",
        input="一年有多少天？",
        expected="contains:365",
        tags=["common", "time"],
    ),
    TestCase(
        id="qa_common_002",
        category="qa",
        subcategory="common",
        description="水的化学式",
        input="水的化学式是什么？",
        expected="contains:H2O",
        tags=["common", "chemistry"],
    ),
    TestCase(
        id="qa_common_003",
        category="qa",
        subcategory="common",
        description="地球绕太阳一周",
        input="地球绕太阳一周需要多长时间？",
        expected="contains:一年",
        tags=["common", "astronomy"],
    ),
    TestCase(
        id="qa_common_004",
        category="qa",
        subcategory="common",
        description="人体骨骼数量",
        input="成人有多少块骨骼？",
        expected="contains:206",
        tags=["common", "biology"],
    ),
    TestCase(
        id="qa_common_005",
        category="qa",
        subcategory="common",
        description="光速",
        input="光在真空中的速度是多少？",
        expected="regex:30\\d+",
        tags=["common", "physics"],
    ),
]


# 导出
def get_tests() -> list[TestCase]:
    return QA_BASIC_TESTS
