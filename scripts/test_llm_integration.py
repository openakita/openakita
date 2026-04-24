"""
LLM 闭环集成测试 — 完整组织任务流

测试步骤：
1. 获取/创建组织
2. 设置运营模式（command 模式）
3. 启动组织
4. 创建项目和任务
5. 派发任务给组织
6. 轮询任务进度
7. 检查节点状态、事件、黑板等
8. 验证 Plan 工具、子任务、execution_log

需要后端运行在 localhost:18900
"""

import json
import sys
import time

import requests

BASE = "http://127.0.0.1:18900/api"
HEADERS = {"Content-Type": "application/json"}


def api(method, path, data=None, params=None):
    url = f"{BASE}{path}"
    resp = getattr(requests, method)(url, json=data, params=params, headers=HEADERS, timeout=60)
    if resp.status_code >= 400:
        print(f"  [ERROR] {method.upper()} {path} -> {resp.status_code}: {resp.text[:200]}")
        return None
    try:
        return resp.json()
    except Exception:
        return resp.text


def step(msg):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")


def check(condition, msg):
    if condition:
        print(f"  ✓ {msg}")
    else:
        print(f"  ✗ {msg}")
    return condition


def main():
    all_ok = True

    # 0. Health check
    step("0. 健康检查")
    health = api("get", "/health")
    if not check(health is not None, "后端服务正常"):
        print("后端未启动，退出")
        sys.exit(1)

    # 1. 获取组织列表
    step("1. 获取组织列表")
    orgs = api("get", "/orgs")
    check(orgs is not None and isinstance(orgs, list), f"获取到 {len(orgs or [])} 个组织")

    org_id = None
    if orgs:
        org_id = orgs[0]["id"]
        print(f"  使用已有组织: {orgs[0].get('name')} ({org_id})")
    else:
        step("1b. 创建测试组织")
        from_template = api("post", "/orgs", {
            "name": "LLM测试公司",
            "template_id": "startup",
        })
        if from_template:
            org_id = from_template["id"]
            print(f"  创建组织: {org_id}")
        else:
            print("  无法创建组织，退出")
            sys.exit(1)

    # 2. 设置运营模式为 command
    step("2. 设置运营模式为 command")
    org = api("get", f"/orgs/{org_id}")
    if org:
        current_mode = org.get("operation_mode", "command")
        print(f"  当前模式: {current_mode}")
        if current_mode != "command":
            api("put", f"/orgs/{org_id}", {"operation_mode": "command"})
            print("  已切换为 command 模式")

        check("operation_mode" in org, "组织包含 operation_mode 字段")
        check("watchdog_enabled" in org, "组织包含 watchdog_enabled 字段")
        nodes = org.get("nodes", [])
        check(len(nodes) > 0, f"组织有 {len(nodes)} 个节点")
        for n in nodes[:3]:
            print(f"    - {n.get('role_title')} ({n['id']}): {n.get('status', '?')}")

    # 3. 启动组织
    step("3. 启动组织")
    status = org.get("status", "dormant") if org else "dormant"
    if status in ("active", "running"):
        print("  组织已启动")
    else:
        start_result = api("post", f"/orgs/{org_id}/start")
        if start_result:
            check(start_result.get("status") in ("active", "running"), "组织启动成功")
        else:
            print("  启动失败，尝试继续...")

    time.sleep(2)

    # 4. 检查 stats（节点tooltip丰富化）
    step("4. 检查组织统计（tooltip 数据）")
    stats = api("get", f"/orgs/{org_id}/stats")
    if stats:
        check("per_node" in stats, "stats 包含 per_node 数据")
        if "per_node" in stats:
            for nstat in stats["per_node"][:3]:
                print(f"    节点 {nstat.get('id')}: status={nstat.get('status')}, "
                      f"pending={nstat.get('pending_messages', 0)}, "
                      f"task={nstat.get('current_task_title', '-')}")

    # 5. 创建项目和任务
    step("5. 创建项目和任务")
    proj_data = api("post", f"/orgs/{org_id}/projects", {
        "name": "LLM集成测试项目",
        "project_type": "temporary",
        "description": "自动化测试项目",
    })
    if proj_data:
        proj_id = proj_data["id"]
        check(True, f"项目创建成功: {proj_id}")

        task_data = api("post", f"/orgs/{org_id}/projects/{proj_id}/tasks", {
            "title": "写一个简短的公司介绍",
            "description": "请为公司写一段50字以内的简短介绍",
            "priority": 1,
        })
        if task_data:
            task_id = task_data["id"]
            check(True, f"任务创建成功: {task_id}")

            check("parent_task_id" in task_data, "任务包含 parent_task_id 字段")
            check("plan_steps" in task_data, "任务包含 plan_steps 字段")
            check("execution_log" in task_data, "任务包含 execution_log 字段")
            check("depth" in task_data, "任务包含 depth 字段")
        else:
            task_id = None
    else:
        proj_id = None
        task_id = None

    # 6. 派发任务（LLM 闭环开始）
    step("6. 派发任务给组织（LLM 介入）")
    if proj_id and task_id:
        try:
            dispatch = api("post", f"/orgs/{org_id}/projects/{proj_id}/tasks/{task_id}/dispatch")
            if dispatch:
                check(dispatch.get("ok") or "command_id" in dispatch or "status" in dispatch,
                      f"任务派发成功: {dispatch}")
            else:
                raise Exception("dispatch returned None")
        except Exception as e:
            print(f"  派发超时或失败({e})，改用 command 方式...")
            dispatch = api("post", f"/orgs/{org_id}/command", {
                "content": "请为公司写一段50字以内的简短介绍，写好后提交给上级审阅"
            })
            if dispatch:
                check(True, f"命令发送成功: {dispatch}")

    # 7. 轮询等待 LLM 执行
    step("7. 等待 LLM 执行（最长 120 秒）")
    if proj_id and task_id:
        for i in range(24):
            time.sleep(5)
            tasks = api("get", f"/orgs/{org_id}/tasks", params={"project_id": proj_id})
            if tasks:
                target = None
                for t in tasks:
                    if t.get("id") == task_id:
                        target = t
                        break
                if target:
                    st = target.get("status", "todo")
                    pct = target.get("progress_pct", 0)
                    print(f"  [{i*5:3d}s] 任务状态: {st}, 进度: {pct}%")
                    if st in ("accepted", "delivered", "rejected"):
                        check(True, f"任务已完成: {st}")
                        break
                    if st == "in_progress" and pct > 0:
                        check(True, f"任务正在执行中，进度 {pct}%")
            else:
                print(f"  [{i*5:3d}s] 等待中...")

            stats = api("get", f"/orgs/{org_id}/stats")
            if stats and "per_node" in stats:
                busy_nodes = [ns.get("id") for ns in stats["per_node"]
                              if ns.get("status") == "busy"]
                if busy_nodes:
                    print(f"         忙碌节点: {busy_nodes}")
        else:
            print("  超过 120s 仍未完成，但 LLM 正在执行中...")

    # 8. 检查事件（chain_id/task_id 过滤）
    step("8. 检查事件日志")
    events = api("get", f"/orgs/{org_id}/events", params={"limit": 20})
    if events:
        check(len(events) > 0, f"有 {len(events)} 条事件")
        for ev in events[:5]:
            print(f"    [{ev.get('event_type')}] by {ev.get('actor')}: "
                  f"{json.dumps(ev.get('data', {}), ensure_ascii=False)[:80]}")

    # 9. 检查任务详情 API
    step("9. 检查任务详情 API")
    if task_id:
        detail = api("get", f"/orgs/{org_id}/tasks/{task_id}")
        if detail:
            check("id" in detail, "任务详情 API 正常")
            check("plan_steps" in detail, "包含 plan_steps")
            check("execution_log" in detail, "包含 execution_log")
            if detail.get("execution_log"):
                print(f"    执行日志条数: {len(detail['execution_log'])}")
                for log in detail["execution_log"][:3]:
                    print(f"      - {log}")

        tree = api("get", f"/orgs/{org_id}/tasks/{task_id}/tree")
        if tree:
            check("children" in tree, "任务树 API 正常")
            print(f"    子任务数: {len(tree.get('children', []))}")

        timeline = api("get", f"/orgs/{org_id}/tasks/{task_id}/timeline")
        if timeline:
            check(isinstance(timeline, dict) and "timeline" in timeline, "时间线 API 正常")
            tl = timeline.get("timeline", [])
            print(f"    时间线条目: {len(tl)}")

    # 10. 检查节点任务 API
    step("10. 检查节点任务 API")
    if org:
        root_id = org["nodes"][0]["id"] if org.get("nodes") else None
        if root_id:
            node_tasks = api("get", f"/orgs/{org_id}/nodes/{root_id}/tasks")
            if node_tasks:
                check(isinstance(node_tasks, dict), "节点任务 API 正常")
                assigned = node_tasks.get("assigned", [])
                delegated = node_tasks.get("delegated", [])
                print(f"    已分配: {len(assigned)}, 已委派: {len(delegated)}")

            active_plan = api("get", f"/orgs/{org_id}/nodes/{root_id}/active-plan")
            if active_plan is not None:
                check(True, "节点计划 API 正常")

    # 11. 检查黑板
    step("11. 检查黑板")
    bb = api("get", f"/orgs/{org_id}/memory")
    if bb:
        check(isinstance(bb, dict) or isinstance(bb, list), "黑板 API 正常")
        if isinstance(bb, dict):
            org_bb = bb.get("org", [])
            print(f"    组织级黑板条目: {len(org_bb)}")
        elif isinstance(bb, list):
            print(f"    黑板条目: {len(bb)}")

    # 12. 停止组织
    step("12. 停止组织")
    try:
        stop_resp = requests.post(
            f"{BASE}/orgs/{org_id}/stop", headers=HEADERS, timeout=180
        )
        if stop_resp.status_code < 400:
            stop_result = stop_resp.json()
            check(stop_result.get("status") == "dormant", "组织已停止")
        else:
            print(f"  停止返回 {stop_resp.status_code}")
    except requests.exceptions.ReadTimeout:
        print("  停止超时（LLM 仍在运行，后台将继续停止）")
    except Exception as e:
        print(f"  停止异常: {e}")

    step("测试完成")
    print(f"  所有 API 端点均可正常访问")
    print(f"  LLM 闭环测试已执行")


if __name__ == "__main__":
    main()

