"""综合验收测试：模拟用户向组织下达多种类型指令"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:18900"
ORG_ID = "org_a1a4e03d4668"


def log(msg: str = "") -> None:
    print(msg, flush=True)


async def main() -> None:
    client = httpx.AsyncClient(base_url=BASE, timeout=600)

    log("=" * 70)
    log("综合验收测试：组织指令执行")
    log("=" * 70)

    # 0. 健康检查
    log("\n[0] 健康检查...")
    r = await client.get("/api/health")
    health = r.json()
    log(f"  状态: {health['status']}, 版本: {health['version']}")

    # 1. 确保组织已启动
    log(f"\n[1] 启动组织 {ORG_ID}...")
    r = await client.post(f"/api/orgs/{ORG_ID}/start")
    if r.status_code == 200:
        data = r.json()
        log(f"  组织状态: {data.get('status')}")
    else:
        log(f"  启动响应: {r.status_code} — {r.text[:200]}")

    await asyncio.sleep(5)

    # 2. 查看初始节点状态
    log("\n[2] 查看节点状态...")
    r = await client.get(f"/api/orgs/{ORG_ID}")
    org_data = r.json()
    for node in org_data.get("nodes", [])[:10]:
        log(f"  {node['role_title']:15s} | {node['id']:12s} | {node.get('status', '?')}")

    # 测试指令列表
    commands = [
        ("全员会议",
         "立即召开全员工作会议，让所有部门负责人汇报近期工作进展和遇到的问题，需要使用会议工具"),
        ("具体任务委派",
         "请 CTO 制定一份技术选型方案，包括前端框架、后端语言、数据库选择，并让产品经理配合提供需求清单"),
        ("调研任务",
         "让市场部做一份竞品分析报告，分析3个主要竞争对手的产品特点和定价策略"),
        ("写入战略决策",
         "将以下决策写入组织黑板：Q2目标是完成MVP产品开发并获取首批100个种子用户，预算上限50万"),
        ("跨部门协作",
         "产品部和技术部需要协作完成用户注册登录模块的开发，产品部负责PRD，技术部负责实现，一周内完成"),
    ]

    results = []
    for i, (name, text) in enumerate(commands, start=3):
        log(f"\n{'=' * 70}")
        log(f"[{i}] 指令: {name}")
        log(f"  内容: {text[:80]}")

        t0 = time.time()
        try:
            r = await client.post(
                f"/api/orgs/{ORG_ID}/command",
                json={"content": text},
                timeout=300,
            )
            elapsed = time.time() - t0
            data = r.json()
            result_text = data.get("result", data.get("error", json.dumps(data, ensure_ascii=False)))
            log(f"  耗时: {elapsed:.1f}s | HTTP {r.status_code}")
            log(f"  结果预览({len(result_text)}字):")
            for line in result_text[:500].split("\n")[:8]:
                log(f"    {line}")
            results.append({"name": name, "elapsed": elapsed, "ok": r.status_code == 200, "len": len(result_text)})
        except Exception as e:
            elapsed = time.time() - t0
            log(f"  ❌ 异常 ({elapsed:.1f}s): {e}")
            results.append({"name": name, "elapsed": elapsed, "ok": False, "len": 0})

        await asyncio.sleep(3)

    # ── 验收检查 ──
    log(f"\n{'=' * 70}")
    log("[验收] 检查组织产出")

    log("\n  [黑板]")
    r = await client.get(f"/api/orgs/{ORG_ID}/memory?limit=15")
    bb = r.json() if r.status_code == 200 else []
    if isinstance(bb, list):
        for entry in bb[:6]:
            c = entry.get("content", "")[:80].replace("\n", " ")
            log(f"    [{entry.get('scope', '?')}] {entry.get('source_node', '?')}: {c}")
        log(f"    共 {len(bb)} 条")
    else:
        log(f"    {str(bb)[:200]}")

    log("\n  [事件]")
    r = await client.get(f"/api/orgs/{ORG_ID}/events?limit=50")
    events = r.json() if r.status_code == 200 else []
    if isinstance(events, list):
        tc: dict[str, int] = {}
        for ev in events:
            t = ev.get("event_type", "?")
            tc[t] = tc.get(t, 0) + 1
        for t, c in sorted(tc.items(), key=lambda x: -x[1]):
            log(f"    {t}: {c}")
        log(f"    共 {len(events)} 条")

    log("\n  [节点状态]")
    r = await client.get(f"/api/orgs/{ORG_ID}")
    org_final = r.json()
    sc: dict[str, int] = {}
    for node in org_final.get("nodes", []):
        s = node.get("status", "?")
        sc[s] = sc.get(s, 0) + 1
    for s, c in sc.items():
        log(f"    {s}: {c}")

    # 汇总
    log(f"\n{'=' * 70}")
    log("验收汇总")
    log(f"{'=' * 70}")
    log(f"{'指令':15s} | {'耗时':>6s} | {'结果长度':>6s} | 状态")
    log("-" * 55)
    for r in results:
        st = "✅" if r["ok"] else "❌"
        log(f"{r['name']:15s} | {r['elapsed']:5.1f}s | {r['len']:6d} | {st}")

    bb_count = len(bb) if isinstance(bb, list) else 0
    ev_count = len(events) if isinstance(events, list) else 0
    log(f"\n黑板: {bb_count} 条 | 事件: {ev_count} 条")
    log(f"\n{'✅ 全部通过' if all(r['ok'] for r in results) else '❌ 存在失败'}")

    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
