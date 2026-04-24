"""
完整任务执行测试 — 真实大模型端到端
 
创建一个组织 → 启动 → 下达任务 → 观察 CEO 分配给 CTO → CTO 执行 → 黑板记录
→ 检查事件链 → 检查节点状态 → 输出完整执行报告
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from openakita.orgs.manager import OrgManager
from openakita.orgs.runtime import OrgRuntime
from openakita.orgs.models import (
    EdgeType, NodeStatus, OrgEdge, OrgNode, OrgStatus, Organization,
)


def make_test_org() -> dict:
    nodes = [
        OrgNode(
            id="ceo", role_title="总经理", level=0, department="管理层",
            role_goal="统筹全局，制定战略，分配任务给下属",
            role_backstory="你是一家AI创业公司的总经理，负责公司战略方向和团队管理",
        ),
        OrgNode(
            id="cto", role_title="技术总监", level=1, department="技术部",
            role_goal="负责技术方案设计和技术团队管理",
            role_backstory="你是技术总监，精通AI和软件架构，负责技术路线制定",
        ),
        OrgNode(
            id="product", role_title="产品经理", level=1, department="产品部",
            role_goal="负责产品规划、需求分析和用户体验",
            role_backstory="你是产品经理，擅长用户需求分析和产品设计",
        ),
    ]
    edges = [
        OrgEdge(source="ceo", target="cto", edge_type=EdgeType.HIERARCHY),
        OrgEdge(source="ceo", target="product", edge_type=EdgeType.HIERARCHY),
        OrgEdge(source="cto", target="product", edge_type=EdgeType.COLLABORATE),
    ]
    org = Organization(
        id="test_full_task",
        name="AI创业公司",
        nodes=nodes,
        edges=edges,
    )
    return org.to_dict()


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


async def main():
    import tempfile
    data_dir = Path(tempfile.mkdtemp(prefix="org_test_"))
    print(f"[数据目录] {data_dir}")

    manager = OrgManager(data_dir)
    runtime = OrgRuntime(manager)
    await runtime.start()

    org = manager.create(make_test_org())
    print(f"[组织创建] {org.name} (id={org.id})")

    print_section("第1步：启动组织")
    t0 = time.time()
    await runtime.start_org(org.id)
    print(f"  ✓ 组织已启动，耗时 {time.time()-t0:.1f}s")
    print(f"  节点状态:")
    for n in org.nodes:
        print(f"    - {n.role_title}: {n.status.value}")

    print_section("第2步：向总经理下达任务")
    task_command = (
        "请完成以下工作：\n"
        "1. 制定一个简短的'AI智能客服产品'方案（2-3句话即可）\n"
        "2. 把方案写入组织黑板（用 org_write_blackboard）\n"
        "3. 给技术总监分配一个任务：评估实现这个方案需要的技术栈（用 org_delegate_task）\n"
        "4. 给产品经理分配一个任务：列出3个核心用户场景（用 org_delegate_task）\n"
        "\n注意：每个步骤都要实际执行工具调用，不要只是描述。"
    )
    print(f"  任务内容:\n{task_command}")

    t1 = time.time()
    result = await asyncio.wait_for(
        runtime.send_command(org.id, "ceo", task_command),
        timeout=180.0,
    )
    elapsed_cmd = time.time() - t1
    print(f"\n  ✓ 总经理执行完毕，耗时 {elapsed_cmd:.1f}s")

    if "result" in result:
        print(f"  回复 ({len(result['result'])} 字):")
        for line in result["result"].split("\n")[:10]:
            print(f"    {line}")
        if result["result"].count("\n") > 10:
            print(f"    ... (共 {result['result'].count(chr(10))+1} 行)")
    elif "error" in result:
        print(f"  ❌ 错误: {result['error']}")

    print_section("第3步：等待下属节点处理消息")
    print("  等待 10 秒让消息传递和任务执行...")
    await asyncio.sleep(10)

    print_section("第4步：检查组织黑板")
    bb = runtime.get_blackboard(org.id)
    org_entries = bb.read_org(limit=20)
    if org_entries:
        print(f"  共 {len(org_entries)} 条组织级记录:")
        for i, e in enumerate(org_entries, 1):
            print(f"  [{i}] ({e.memory_type.value}) 来自 {e.source_node}:")
            print(f"      {e.content[:150]}")
            if e.tags:
                print(f"      标签: {e.tags}")
    else:
        print("  (黑板为空)")

    print_section("第5步：检查节点最终状态")
    refreshed = runtime.get_org(org.id)
    for n in refreshed.nodes:
        messenger = runtime.get_messenger(org.id)
        pending = messenger.get_pending_count(n.id) if messenger else 0
        print(f"  {n.role_title}: 状态={n.status.value}, 待处理消息={pending}")

    print_section("第6步：检查事件存储")
    es = runtime.get_event_store(org.id)
    events = es.query(limit=50)
    print(f"  共 {len(events)} 个事件:")
    event_type_counts: dict[str, int] = {}
    for evt in events:
        et = evt.get("event_type", "unknown")
        event_type_counts[et] = event_type_counts.get(et, 0) + 1
    for et, count in sorted(event_type_counts.items()):
        print(f"    {et}: {count}次")

    print(f"\n  最近 10 个事件详情:")
    for evt in events[:10]:
        actor = evt.get("actor", "?")
        etype = evt.get("event_type", "?")
        ts = evt.get("timestamp", "?")
        data = evt.get("data", {})
        preview = ""
        if "task" in data:
            preview = f" task={data['task'][:60]}"
        elif "result_preview" in data:
            preview = f" result={data['result_preview'][:60]}"
        elif "content" in data:
            preview = f" content={data['content'][:60]}"
        elif "prompt" in data:
            preview = f" prompt={data['prompt'][:60]}"
        print(f"    [{ts[11:19] if len(ts)>19 else ts}] {etype} by {actor}{preview}")

    print_section("第7步：统计摘要")
    print(f"  组织: {refreshed.name}")
    print(f"  状态: {refreshed.status.value}")
    print(f"  完成任务总数: {refreshed.total_tasks_completed}")
    print(f"  交换消息总数: {refreshed.total_messages_exchanged}")
    print(f"  事件总数: {len(events)}")
    print(f"  黑板记录数: {len(org_entries)}")

    tool_events = [e for e in events if e.get("event_type") in ("task_assigned", "blackboard_written")]
    print(f"  工具调用事件: {len(tool_events)}")

    has_delegate = any(e.get("event_type") == "task_assigned" for e in events)
    has_bb_write = len(org_entries) > 0
    multi_node = len(set(e.get("actor", "") for e in events)) > 1

    print(f"\n  ✓ 任务委派: {'是' if has_delegate else '否'}")
    print(f"  ✓ 黑板写入: {'是' if has_bb_write else '否'}")
    print(f"  ✓ 多节点参与: {'是' if multi_node else '否'}")

    if has_delegate and has_bb_write and multi_node:
        print(f"\n  🎉 完整任务执行验证通过！")
    else:
        missing = []
        if not has_delegate:
            missing.append("任务委派")
        if not has_bb_write:
            missing.append("黑板写入")
        if not multi_node:
            missing.append("多节点参与")
        print(f"\n  ⚠ 部分验证未通过: {', '.join(missing)}")

    print_section("清理")
    await runtime.stop_org(org.id)
    await runtime.shutdown()
    print("  ✓ 组织已停止，运行时已关闭")
    print(f"\n{'='*60}")
    print(f"  测试完成，总耗时 {time.time()-t0:.1f}s")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())

