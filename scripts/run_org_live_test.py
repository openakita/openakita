"""
真实组织任务执行测试 — 验证节点 agent 的完整工作链路。

场景：
  1. 创建一个 3 人小型研究团队（CEO、研究员、分析师）
  2. CEO 带 research + planning 工具
  3. 研究员带 research 工具
  4. 分析师无外部工具（纯协作）
  5. 给 CEO 下达任务：调研一个话题并写入黑板
  6. 观察 CEO 是否真的使用了外部工具（web_search 等）
  7. 测试 CEO 委派任务给研究员
  8. 测试分析师申请工具

用法：python scripts/run_org_live_test.py
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openakita.orgs.manager import OrgManager
from openakita.orgs.runtime import OrgRuntime
from openakita.orgs.models import OrgNode, Organization, OrgEdge, EdgeType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("org_live_test")

DATA_DIR = Path(__file__).resolve().parent.parent / "tmp_live_test_data"


def create_research_org(manager: OrgManager) -> Organization:
    nodes = [
        OrgNode(
            id="ceo",
            role_title="CEO / 首席执行官",
            role_goal="领导团队完成调研任务，制定战略方向",
            role_backstory="你是一位经验丰富的科技公司CEO，善于快速决策和任务分配",
            level=0,
            department="管理层",
            external_tools=["research", "planning"],
        ),
        OrgNode(
            id="researcher",
            role_title="高级研究员",
            role_goal="深入调研技术趋势，产出高质量分析报告",
            role_backstory="你是AI领域的资深研究员，擅长技术分析和市场调研",
            level=1,
            department="研究部",
            external_tools=["research"],
        ),
        OrgNode(
            id="analyst",
            role_title="数据分析师",
            role_goal="分析数据并提供洞察，支持团队决策",
            role_backstory="你是一位数据分析师，擅长从信息中提取关键洞察",
            level=1,
            department="研究部",
            external_tools=[],
        ),
    ]
    edges = [
        OrgEdge(source="ceo", target="researcher", edge_type=EdgeType.HIERARCHY),
        OrgEdge(source="ceo", target="analyst", edge_type=EdgeType.HIERARCHY),
        OrgEdge(source="researcher", target="analyst", edge_type=EdgeType.COLLABORATE),
    ]
    org_data = Organization(
        id="research_team",
        name="AI研究小队",
        nodes=nodes,
        edges=edges,
    ).to_dict()
    org_data["heartbeat_enabled"] = False
    return manager.create(org_data)


async def run_tests():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    manager = OrgManager(DATA_DIR)
    runtime = OrgRuntime(manager)
    await runtime.start()

    try:
        org = create_research_org(manager)
        await runtime.start_org(org.id)
        logger.info(f"组织已启动: {org.name} (ID: {org.id})")

        # ---------------------------------------------------------------
        # 测试 1：CEO 直接执行任务（使用外部工具 web_search）
        # ---------------------------------------------------------------
        print("\n" + "=" * 70)
        print("测试 1: CEO 直接执行调研任务（预期使用 web_search）")
        print("=" * 70)

        result = await asyncio.wait_for(
            runtime.send_command(
                org.id, "ceo",
                "请搜索一下2025年全球AI市场的最新发展趋势，用1-2句话总结关键发现，"
                "然后把结果写入组织黑板（org_write_blackboard）。",
            ),
            timeout=120.0,
        )

        if "result" in result:
            print(f"\n✅ CEO 回复:\n{result['result'][:500]}")
        else:
            print(f"\n❌ CEO 错误: {result.get('error', 'unknown')}")

        bb = runtime.get_blackboard(org.id)
        entries = bb.read_org(limit=5)
        if entries:
            print(f"\n📋 黑板内容 ({len(entries)} 条):")
            for e in entries:
                print(f"  - [{e.source_node}] {e.content[:100]}...")
        else:
            print("\n📋 黑板为空（CEO 可能没有写入）")

        # ---------------------------------------------------------------
        # 测试 2：CEO 委派任务给研究员
        # ---------------------------------------------------------------
        print("\n" + "=" * 70)
        print("测试 2: CEO 委派任务给研究员（预期使用 org_send_message）")
        print("=" * 70)

        result2 = await asyncio.wait_for(
            runtime.send_command(
                org.id, "ceo",
                "请使用 org_send_message 给高级研究员（researcher）分配一个任务：让他搜索 "
                "'大语言模型在企业应用中的最新案例'，并将结果写入黑板。不要自己做，只委派。",
            ),
            timeout=120.0,
        )

        if "result" in result2:
            print(f"\n✅ CEO 回复:\n{result2['result'][:500]}")
        else:
            print(f"\n❌ CEO 错误: {result2.get('error', 'unknown')}")

        await asyncio.sleep(3)

        entries2 = bb.read_org(limit=10)
        print(f"\n📋 黑板内容 ({len(entries2)} 条):")
        for e in entries2:
            print(f"  - [{e.source_node}] {e.content[:100]}...")

        # ---------------------------------------------------------------
        # 测试 3：研究员直接执行搜索任务
        # ---------------------------------------------------------------
        print("\n" + "=" * 70)
        print("测试 3: 研究员直接执行搜索（预期使用 web_search）")
        print("=" * 70)

        result3 = await asyncio.wait_for(
            runtime.send_command(
                org.id, "researcher",
                "搜索 'AI Agent 在2025年的技术突破' 并用2-3句话总结，然后写入黑板。",
            ),
            timeout=120.0,
        )

        if "result" in result3:
            print(f"\n✅ 研究员回复:\n{result3['result'][:500]}")
        else:
            print(f"\n❌ 研究员错误: {result3.get('error', 'unknown')}")

        # ---------------------------------------------------------------
        # 测试 4：分析师（无外部工具）尝试工作
        # ---------------------------------------------------------------
        print("\n" + "=" * 70)
        print("测试 4: 分析师（无外部工具）读取黑板并分析")
        print("=" * 70)

        result4 = await asyncio.wait_for(
            runtime.send_command(
                org.id, "analyst",
                "请用 org_read_blackboard 读取组织黑板上的信息，然后总结团队目前的调研成果。",
            ),
            timeout=120.0,
        )

        if "result" in result4:
            print(f"\n✅ 分析师回复:\n{result4['result'][:500]}")
        else:
            print(f"\n❌ 分析师错误: {result4.get('error', 'unknown')}")

        # ---------------------------------------------------------------
        # 测试 5：分析师申请工具
        # ---------------------------------------------------------------
        print("\n" + "=" * 70)
        print("测试 5: 分析师向 CEO 申请 research 工具")
        print("=" * 70)

        result5 = await asyncio.wait_for(
            runtime.send_command(
                org.id, "analyst",
                "你需要搜索功能来做更深入的分析，请使用 org_request_tools 向上级申请 research 工具类目，"
                "原因是'需要独立搜索能力以提高分析质量'。",
            ),
            timeout=120.0,
        )

        if "result" in result5:
            print(f"\n✅ 分析师回复:\n{result5['result'][:500]}")
        else:
            print(f"\n❌ 分析师错误: {result5.get('error', 'unknown')}")

        messenger = runtime.get_messenger(org.id)
        pending = list(messenger._pending_messages.values())
        tool_requests = [m for m in pending if m.metadata.get("_tool_request")]
        if tool_requests:
            print(f"\n📬 CEO 收到工具申请: {tool_requests[-1].content[:200]}")
        else:
            print("\n📬 未检测到工具申请消息（可能已被消费或路径不同）")

        # ---------------------------------------------------------------
        # 测试 6：CEO 批准工具申请，验证热更新
        # ---------------------------------------------------------------
        print("\n" + "=" * 70)
        print("测试 6: CEO 批准工具申请 → 验证热更新")
        print("=" * 70)

        grant_result = await runtime.handle_org_tool(
            "org_grant_tools",
            {"node_id": "analyst", "tools": ["research"]},
            org.id, "ceo",
        )
        print(f"\n授权结果: {grant_result}")

        updated_org = runtime.get_org(org.id)
        updated_analyst = updated_org.get_node("analyst")
        print(f"分析师 external_tools: {updated_analyst.external_tools}")

        result6 = await asyncio.wait_for(
            runtime.send_command(
                org.id, "analyst",
                "你现在有搜索工具了！请搜索 'AI 2025 predictions' 并用一句话总结。",
            ),
            timeout=120.0,
        )

        if "result" in result6:
            print(f"\n✅ 分析师（获得工具后）回复:\n{result6['result'][:500]}")
        else:
            print(f"\n❌ 分析师错误: {result6.get('error', 'unknown')}")

        # ---------------------------------------------------------------
        # 最终汇总
        # ---------------------------------------------------------------
        print("\n" + "=" * 70)
        print("最终黑板状态")
        print("=" * 70)
        final_entries = bb.read_org(limit=20)
        print(f"共 {len(final_entries)} 条记录:")
        for i, e in enumerate(final_entries, 1):
            print(f"  {i}. [{e.source_node}] {e.content[:120]}")

        print("\n" + "=" * 70)
        print("✅ 全部测试执行完毕")
        print("=" * 70)

    except Exception as e:
        logger.error(f"测试异常: {e}", exc_info=True)
    finally:
        await runtime.shutdown()
        import shutil
        if DATA_DIR.exists():
            shutil.rmtree(DATA_DIR, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(run_tests())

