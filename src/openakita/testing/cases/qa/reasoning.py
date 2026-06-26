"""
推理和逻辑测试用例 (35个)
"""

from openakita.testing.runner import TestCase

REASONING_TESTS = [
    # 逻辑推理
    TestCase(
        id="qa_logic_001",
        category="qa",
        subcategory="logic",
        description="简单三段论",
        input="所有人都会死，苏格拉底是人，所以？",
        expected="contains:苏格拉底会死",
        tags=["logic", "syllogism"],
    ),
    TestCase(
        id="qa_logic_002",
        category="qa",
        subcategory="logic",
        description="数列规律",
        input="找出规律：2, 4, 8, 16, ?",
        expected="contains:32",
        tags=["logic", "sequence"],
    ),
    TestCase(
        id="qa_logic_003",
        category="qa",
        subcategory="logic",
        description="年龄推理",
        input="小明今年10岁，他妈妈的年龄是他的3倍，5年后妈妈多少岁？",
        expected="contains:35",
        tags=["logic", "math"],
    ),
    TestCase(
        id="qa_logic_004",
        category="qa",
        subcategory="logic",
        description="排列组合",
        input="5个人排成一排，有多少种排法？",
        expected="contains:120",
        tags=["logic", "permutation"],
    ),
    TestCase(
        id="qa_logic_005",
        category="qa",
        subcategory="logic",
        description="概率计算",
        input="抛两次硬币，至少有一次正面朝上的概率是多少？",
        expected="contains:75%",
        tags=["logic", "probability"],
    ),
    # 代码理解
    TestCase(
        id="qa_code_001",
        category="qa",
        subcategory="code",
        description="Python 代码输出",
        input="这段代码输出什么？\n```python\nfor i in range(3):\n    print(i, end=' ')\n```",
        expected="contains:0 1 2",
        tags=["code", "python"],
    ),
    TestCase(
        id="qa_code_002",
        category="qa",
        subcategory="code",
        description="列表切片",
        input="lst = [1,2,3,4,5]，lst[1:4] 的结果是？",
        expected="contains:[2, 3, 4]",
        tags=["code", "python", "slice"],
    ),
    TestCase(
        id="qa_code_003",
        category="qa",
        subcategory="code",
        description="字典操作",
        input="d = {'a': 1, 'b': 2}，d.get('c', 0) 返回什么？",
        expected="contains:0",
        tags=["code", "python", "dict"],
    ),
    TestCase(
        id="qa_code_004",
        category="qa",
        subcategory="code",
        description="递归理解",
        input="```python\ndef f(n):\n    if n <= 1: return n\n    return f(n-1) + f(n-2)\n```\nf(6) 的结果是？",
        expected="contains:8",
        tags=["code", "python", "recursion"],
    ),
    TestCase(
        id="qa_code_005",
        category="qa",
        subcategory="code",
        description="时间复杂度",
        input="二分查找的时间复杂度是多少？",
        expected="contains:log",
        tags=["code", "algorithm"],
    ),
    # 多步推理
    TestCase(
        id="qa_multi_001",
        category="qa",
        subcategory="multi_step",
        description="多步数学",
        input="一个数加3乘2减4等于10，求这个数",
        expected="contains:4",
        tags=["multi_step", "math"],
    ),
    TestCase(
        id="qa_multi_002",
        category="qa",
        subcategory="multi_step",
        description="工程问题",
        input="甲单独完成需要6小时，乙需要3小时，两人合作需要多少小时？",
        expected="contains:2",
        tags=["multi_step", "math"],
    ),
    TestCase(
        id="qa_multi_003",
        category="qa",
        subcategory="multi_step",
        description="路程问题",
        input="甲乙相距100km，甲60km/h向乙走，乙40km/h向甲走，多久相遇？",
        expected="contains:1",
        tags=["multi_step", "math"],
    ),
    # 类比推理
    TestCase(
        id="qa_analogy_001",
        category="qa",
        subcategory="analogy",
        description="词语类比",
        input="医生:医院 = 教师:?",
        expected="contains:学校",
        tags=["analogy"],
    ),
    TestCase(
        id="qa_analogy_002",
        category="qa",
        subcategory="analogy",
        description="关系推理",
        input="手:手套 = 脚:?",
        expected="contains:鞋",
        tags=["analogy"],
    ),
]


def get_tests() -> list[TestCase]:
    return REASONING_TESTS
