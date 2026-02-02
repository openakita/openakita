#!/usr/bin/env python3
"""更新定时任务工具说明"""

import sys

def main():
    with open('src/openakita/core/agent.py', 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 1. 添加概念注释到定时任务工具区域
    old1 = '''        # === 定时任务工具 ===
        {
            "name": "schedule_task",'''
    
    new1 = '''        # === 定时任务工具 ===
        # 【重要概念区分】
        # - 取消/删除任务 (cancel_scheduled_task) = 永久移除，任务不再执行
        # - 关闭提醒 (update notify=false) = 任务继续执行，但不发通知消息
        # - 暂停任务 (update enabled=false) = 任务暂停执行，可以恢复
        {
            "name": "schedule_task",'''
    
    if old1 in content:
        content = content.replace(old1, new1)
        print("1. Added concept comments")
    else:
        print("1. SKIP: Already has comments or not found")
    
    # 2. 更新 list_scheduled_tasks 说明
    old2 = '''"name": "list_scheduled_tasks",
            "description": "列出所有定时任务",'''
    
    new2 = '''"name": "list_scheduled_tasks",
            "description": "列出所有定时任务，返回任务ID、名称、类型、状态、下次执行时间",'''
    
    if old2 in content:
        content = content.replace(old2, new2)
        print("2. Updated list_scheduled_tasks")
    else:
        print("2. SKIP: list_scheduled_tasks already updated")
    
    # 3. 更新 cancel_scheduled_task 说明
    old3 = '''"name": "cancel_scheduled_task",
            "description": "永久删除定时任务。注意:关闭提醒请用update_scheduled_task,不是cancel",'''
    
    new3 = '''"name": "cancel_scheduled_task",
            "description": "【永久删除】定时任务。"
                           "\\n⚠️ 用户说'取消/删除任务' → 用此工具"
                           "\\n⚠️ 用户说'关闭提醒' → 用 update_scheduled_task 设 notify=false"
                           "\\n⚠️ 用户说'暂停任务' → 用 update_scheduled_task 设 enabled=false",'''
    
    if old3 in content:
        content = content.replace(old3, new3)
        print("3. Updated cancel_scheduled_task")
    else:
        print("3. SKIP: cancel_scheduled_task already updated")
    
    # 4. 更新 update_scheduled_task 说明
    old4 = '''"name": "update_scheduled_task",
            "description": "修改定时任务设置(不删除)。关闭提醒=修改notify,不是取消任务!",'''
    
    new4 = '''"name": "update_scheduled_task",
            "description": "修改定时任务设置【不删除任务】。"
                           "\\n可修改: notify_on_start, notify_on_complete, enabled"
                           "\\n\\n常见用法:"
                           "\\n- '关闭提醒' → notify_on_start=false, notify_on_complete=false"
                           "\\n- '暂停任务' → enabled=false"
                           "\\n- '恢复任务' → enabled=true",'''
    
    if old4 in content:
        content = content.replace(old4, new4)
        print("4. Updated update_scheduled_task")
    else:
        print("4. SKIP: update_scheduled_task already updated")
    
    # 5. 更新参数说明
    old5 = '''"notify_on_start": {
                        "type": "boolean",
                        "default": True,
                        "description": "是否在任务开始时发送通知（默认true）"
                    },
                    "notify_on_complete": {
                        "type": "boolean",
                        "default": True,
                        "description": "是否在任务完成时发送结果通知（默认true）"
                    }'''
    
    new5 = '''"notify_on_start": {
                        "type": "boolean",
                        "default": True,
                        "description": "任务开始时发通知？默认true。'不要提醒'时设false"
                    },
                    "notify_on_complete": {
                        "type": "boolean",
                        "default": True,
                        "description": "任务完成时发通知？默认true。'不要提醒'时设false"
                    }'''
    
    if old5 in content:
        content = content.replace(old5, new5)
        print("5. Updated notify parameters description")
    else:
        print("5. SKIP: notify parameters already updated")
    
    # 6. 更新 update_scheduled_task 的参数说明
    old6 = '''"task_id": {"type": "string", "description": "任务 ID"},
                    "notify_on_start": {"type": "boolean", "description": "开始时发送通知"},
                    "notify_on_complete": {"type": "boolean", "description": "完成时发送通知"},
                    "enabled": {"type": "boolean", "description": "启用/暂停任务"}'''
    
    new6 = '''"task_id": {"type": "string", "description": "要修改的任务ID（先用list获取）"},
                    "notify_on_start": {"type": "boolean", "description": "开始时发通知？不传=不修改"},
                    "notify_on_complete": {"type": "boolean", "description": "完成时发通知？不传=不修改"},
                    "enabled": {"type": "boolean", "description": "启用(true)/暂停(false)任务？不传=不修改"}'''
    
    if old6 in content:
        content = content.replace(old6, new6)
        print("6. Updated update_scheduled_task parameters")
    else:
        print("6. SKIP: update parameters already updated")
    
    with open('src/openakita/core/agent.py', 'w', encoding='utf-8') as f:
        f.write(content)
    
    print("\nAll updates applied!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
