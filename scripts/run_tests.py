#!/usr/bin/env python
"""
OpenAkita 测试运行脚本
"""

import asyncio
import sys
from pathlib import Path

# 确保项目在 path 中 (脚本在 scripts/ 目录下)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from openakita.tools.shell import ShellTool
from openakita.tools.file import FileTool
from openakita.tools.web import WebTool
from openakita.testing.judge import Judge
from openakita.core.agent import Agent
from openakita.prompt.retriever import retrieve_memory


async def test_shell():
    """测试 Shell 工具"""
    print("\n" + "=" * 60)
    print("Shell 工具测试")
    print("=" * 60)
    
    shell = ShellTool()
    judge = Judge()
    
    tests = [
        ("echo hello", "hello", "echo 命令"),
        ("python --version", "contains:Python", "Python 版本"),
        ('python -c "print(2+2)"', "4", "Python 计算"),
    ]
    
    passed = 0
    for cmd, expected, desc in tests:
        print(f"\n  测试: {desc}")
        result = await shell.run(cmd)
        actual = result.stdout.strip()
        judge_result = await judge.evaluate(actual, expected, desc)
        
        status = "✓ PASS" if judge_result.passed else "✗ FAIL"
        print(f"  命令: {cmd}")
        print(f"  输出: {actual[:50]}")
        print(f"  结果: {status}")
        
        if judge_result.passed:
            passed += 1
    
    print(f"\n  Shell 测试: {passed}/{len(tests)} 通过")
    return passed, len(tests)


async def test_file():
    """测试 File 工具"""
    print("\n" + "=" * 60)
    print("File 工具测试")
    print("=" * 60)
    
    file = FileTool()
    judge = Judge()
    
    passed = 0
    total = 3
    
    # 写入测试
    print("\n  测试: 写入文件")
    await file.write("/tmp/openakita_test.txt", "Hello OpenAkita!")
    print("  结果: ✓ PASS")
    passed += 1
    
    # 读取测试
    print("\n  测试: 读取文件")
    content = await file.read("/tmp/openakita_test.txt")
    if content == "Hello OpenAkita!":
        print("  结果: ✓ PASS")
        passed += 1
    else:
        print("  结果: ✗ FAIL")
    
    # 存在测试
    print("\n  测试: 检查存在")
    exists = await file.exists("/tmp/openakita_test.txt")
    if exists:
        print("  结果: ✓ PASS")
        passed += 1
    else:
        print("  结果: ✗ FAIL")
    
    print(f"\n  File 测试: {passed}/{total} 通过")
    return passed, total


async def test_qa():
    """测试 QA 问答"""
    print("\n" + "=" * 60)
    print("QA 问答测试 (Agent 交互)")
    print("=" * 60)
    
    agent = Agent()
    await agent.initialize()
    judge = Judge()
    
    tests = [
        ("1+1等于几？", "contains:2", "基础数学"),
        # 兼容中文/英文名
        ("Python 的作者是谁？", "regex:(Guido|吉多)", "编程知识"),
        # LLM 输出可能使用“找不到/未找到/Not Found”等同义表达，使用 regex 提升稳定性
        ("HTTP 404 是什么意思？", "regex:(找不到|未找到|Not\\s*Found)", "HTTP 状态码"),
    ]
    
    passed = 0
    for question, expected, desc in tests:
        print(f"\n  测试: {desc}")
        print(f"  问题: {question}")
        
        response = await agent.chat(question)
        judge_result = await judge.evaluate(response, expected, desc)
        
        status = "✓ PASS" if judge_result.passed else "✗ FAIL"
        print(f"  回答: {response[:60]}...")
        print(f"  结果: {status}")
        
        if judge_result.passed:
            passed += 1
    
    print(f"\n  QA 测试: {passed}/{len(tests)} 通过")
    return passed, len(tests)

async def test_prompt_and_memory():
    """测试 prompt 分段与 memory 注入关键行为"""
    print("\n" + "=" * 60)
    print("Prompt/Memory 测试")
    print("=" * 60)
    
    passed = 0
    total = 2
    
    # 1) retrieve_memory：query 为空也应返回 core memory（至少不为空）
    print("\n  测试: retrieve_memory 空 query 注入 core memory")
    tmp_dir = Path("data/temp/test_memory")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    mem_path = tmp_dir / "MEMORY.md"
    mem_path.write_text("# Core Memory\n\nTEST_CORE\n", encoding="utf-8")
    
    class _FakeVectorStore:
        enabled = False
    
    class _FakeMemoryManager:
        memory_md_path = mem_path
        vector_store = _FakeVectorStore()
        _memories = {}
    
    mem_text = retrieve_memory(query="", memory_manager=_FakeMemoryManager(), max_tokens=200)
    if "TEST_CORE" in mem_text:
        print("  结果: ✓ PASS")
        passed += 1
    else:
        print("  结果: ✗ FAIL")
    
    # 2) Agent tools：不应暴露 send_to_chat，应包含 deliver_artifacts
    print("\n  测试: Agent 工具列表不暴露 send_to_chat，包含 deliver_artifacts")
    agent = Agent()
    await agent.initialize()
    tool_names = {t.get("name") for t in getattr(agent, "_tools", []) if isinstance(t, dict)}
    if "send_to_chat" not in tool_names and "deliver_artifacts" in tool_names:
        print("  结果: ✓ PASS")
        passed += 1
    else:
        print(f"  结果: ✗ FAIL (send_to_chat={'send_to_chat' in tool_names}, deliver_artifacts={'deliver_artifacts' in tool_names})")
    
    print(f"\n  Prompt/Memory 测试: {passed}/{total} 通过")
    return passed, total


async def main():
    print("=" * 60)
    print("OpenAkita 功能测试")
    print("=" * 60)
    
    total_passed = 0
    total_tests = 0
    
    # Shell 测试
    p, t = await test_shell()
    total_passed += p
    total_tests += t
    
    # File 测试
    p, t = await test_file()
    total_passed += p
    total_tests += t
    
    # QA 测试
    p, t = await test_qa()
    total_passed += p
    total_tests += t
    
    # Prompt/Memory 测试
    p, t = await test_prompt_and_memory()
    total_passed += p
    total_tests += t
    
    print("\n" + "=" * 60)
    print(f"总计: {total_passed}/{total_tests} 通过 ({total_passed/total_tests*100:.1f}%)")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

