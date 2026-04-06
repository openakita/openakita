"""
组织编排完整 LLM 测试 — 任务交付验收 + 协作

测试场景：3 节点小团队（CEO → CTO → Developer）
"""

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

os.environ["OPENAKITA_SKIP_DESKTOP"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("test_org_delivery")

from openakita.orgs.models import (
    EdgeType, OrgEdge, OrgNode, OrgStatus, Organization,
)
from openakita.orgs.manager import OrgManager
from openakita.orgs.runtime import OrgRuntime
from openakita.config import settings

ORG_ID = "test_delivery_org"
T0 = time.time()


def ts():
    return f"[{time.time()-T0:.1f}s]"


async def run_phase(runtime, org_id, node_id, command, phase_name):
    logger.info(f"{'='*60}")
    logger.info(f"{ts()} {phase_name}")
    logger.info(f"{'='*60}")
    t = time.time()
    try:
        r = await asyncio.wait_for(
            runtime.send_command(org_id, node_id, command),
            timeout=180,
        )
        elapsed = time.time() - t
        result_text = str(r.get("result", r.get("error", "")))[:300]
        logger.info(f"{ts()} {phase_name} completed in {elapsed:.1f}s")
        logger.info(f"Result: {result_text}")
        return r
    except asyncio.TimeoutError:
        logger.error(f"{ts()} {phase_name} TIMEOUT after {time.time()-t:.1f}s")
        return {"error": "timeout"}
    except Exception as e:
        logger.error(f"{ts()} {phase_name} ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


async def main():
    logger.info(f"{ts()} Starting test...")
    manager = OrgManager(settings.data_dir / "orgs")
    runtime = OrgRuntime(manager)

    try:
        existing = manager.get(ORG_ID)
        if existing:
            try:
                await runtime.stop_org(ORG_ID)
            except Exception:
                pass
            manager.delete(ORG_ID)
            logger.info(f"{ts()} Cleaned up existing org")
    except Exception:
        pass

    org_dict = Organization(
        id=ORG_ID,
        name="交付验收测试团队",
        status=OrgStatus.DORMANT,
        shared_memory_enabled=True,
        allow_cross_level=True,
        max_delegation_depth=4,
        heartbeat_enabled=False,
        standup_enabled=False,
        nodes=[
            OrgNode(
                id="ceo", role_title="CEO", department="管理层", level=0,
                role_goal="统筹项目，给CTO下达任务并验收最终成果",
                role_backstory="资深管理者，重视结果和质量",
                can_delegate=True, can_escalate=False, timeout_s=120,
            ),
            OrgNode(
                id="cto", role_title="CTO", department="技术部", level=1,
                role_goal="分解技术任务并分配给开发者，审核交付质量",
                role_backstory="10年技术经验，精通架构设计",
                can_delegate=True, can_escalate=True, timeout_s=120,
            ),
            OrgNode(
                id="dev", role_title="开发工程师", department="技术部", level=2,
                role_goal="完成CTO分配的开发任务，提交代码和方案",
                role_backstory="全栈工程师，擅长Python和React",
                can_delegate=False, can_escalate=True, timeout_s=120,
            ),
        ],
        edges=[
            OrgEdge(source="ceo", target="cto", edge_type=EdgeType.HIERARCHY),
            OrgEdge(source="cto", target="dev", edge_type=EdgeType.HIERARCHY),
        ],
    ).to_dict()

    manager.create(org_dict)
    logger.info(f"{ts()} Organization created")

    results = {}

    # Phase 1
    results["phase1"] = await run_phase(
        runtime, ORG_ID, "ceo",
        "请给CTO分配一个任务：设计一个用户登录模块的技术方案。"
        "使用 org_delegate_task 工具，to_node='cto'，"
        "task='设计用户登录模块技术方案，包括认证方式和API接口设计'。",
        "PHASE 1: CEO -> CTO 委派",
    )
    await asyncio.sleep(2)

    # Phase 2
    results["phase2"] = await run_phase(
        runtime, ORG_ID, "cto",
        "请把技术方案任务拆解后分配给开发工程师。"
        "使用 org_delegate_task 工具，to_node='dev'，"
        "task='实现用户登录API：POST /api/auth/login 和 POST /api/auth/register，使用JWT认证'。"
        "然后用 org_write_blackboard 记录技术方案概要。",
        "PHASE 2: CTO -> Dev 分配",
    )
    await asyncio.sleep(2)

    # Phase 3
    results["phase3"] = await run_phase(
        runtime, ORG_ID, "dev",
        "你已完成API实现。请使用 org_submit_deliverable 工具："
        "to_node='cto'，deliverable='完成了 /api/auth/login 和 /api/auth/register 接口，"
        "使用JWT+refresh token方案'，summary='登录注册API完成'。",
        "PHASE 3: Dev -> CTO 提交交付",
    )
    await asyncio.sleep(2)

    # Phase 4
    results["phase4"] = await run_phase(
        runtime, ORG_ID, "cto",
        "你收到了开发工程师的交付物。请使用 org_accept_deliverable 验收通过："
        "from_node='dev'，feedback='代码质量良好'。"
        "然后使用 org_submit_deliverable 向CEO提交："
        "to_node='ceo'，deliverable='登录模块方案已实现并审核通过'，"
        "summary='技术方案验收通过'。",
        "PHASE 4: CTO 验收并提交CEO",
    )
    await asyncio.sleep(2)

    # Phase 5
    results["phase5"] = await run_phase(
        runtime, ORG_ID, "ceo",
        "你收到了CTO的技术方案交付。请使用 org_accept_deliverable 验收通过："
        "from_node='cto'，feedback='方案通过'。"
        "然后用 org_write_blackboard 记录项目完成。",
        "PHASE 5: CEO 最终验收",
    )

    # Results
    logger.info(f"\n{'='*60}")
    logger.info(f"{ts()} RESULTS SUMMARY")
    logger.info(f"{'='*60}")

    bb = runtime.get_blackboard(ORG_ID)
    if bb:
        entries = bb.read_org(limit=20)
        logger.info(f"\nBlackboard entries: {len(entries)}")
        for e in entries:
            logger.info(f"  [{e.memory_type.value}] {e.content[:120]}")

    event_store = runtime.get_event_store(ORG_ID)
    try:
        events = event_store.get_audit_log(limit=100)
        event_types = {}
        for ev in events:
            t = ev.get("event_type", "unknown")
            event_types[t] = event_types.get(t, 0) + 1
        logger.info(f"\nEvent type distribution: {event_types}")
    except Exception as e:
        logger.warning(f"Could not read events: {e}")

    logger.info("\nPhase results:")
    all_ok = True
    for phase, r in results.items():
        status = "OK" if "result" in r else "FAIL"
        if status == "FAIL":
            all_ok = False
        detail = str(r.get("result", r.get("error", "")))[:100]
        logger.info(f"  {phase}: {status} - {detail}")

    logger.info(f"\n{ts()} OVERALL: {'PASS' if all_ok else 'FAIL'}")

    # Cleanup
    logger.info(f"\n{ts()} Cleaning up...")
    try:
        await runtime.stop_org(ORG_ID)
    except Exception:
        pass
    try:
        manager.delete(ORG_ID)
    except Exception:
        pass
    logger.info(f"{ts()} Done!")


if __name__ == "__main__":
    asyncio.run(main())
