#!/usr/bin/env python3
"""查询监控状态"""
import os
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(BASE_DIR, "status.json")
ALERT_FILE = os.path.join(BASE_DIR, "alerts.json")

def show_status():
    print("=" * 40)
    print("📊 香农芯创(300475) 监控状态")
    print("=" * 40)
    
    if os.path.exists(STATUS_FILE):
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        
        status_icon = "🟢" if d.get("status") == "running" else "🔴"
        print(f"状态: {status_icon} {d.get('status', 'unknown')}")
        print(f"股票: {d.get('stock')} ({d.get('code')})")
        print(f"大单阈值: ¥{d.get('threshold', 0):,}")
        print(f"最新价格: ¥{d.get('last_price') or 'N/A'}")
        print(f"检查次数: {d.get('check_count', 0)}")
        print(f"发现大单: {d.get('alert_count', 0)} 次")
        print(f"启动时间: {d.get('started_at', 'N/A')}")
        print(f"最后检查: {d.get('last_check', 'N/A')}")
    else:
        print("🔴 监控未启动")
    
    # 显示最近的大单记录
    if os.path.exists(ALERT_FILE):
        with open(ALERT_FILE, "r", encoding="utf-8") as f:
            alerts = json.load(f)
        if alerts:
            print("\n📋 最近大单记录:")
            for a in alerts[-5:]:
                print(f"  {a['time']} | ¥{a['amount']:,.2f}")
    
    print("=" * 40)

if __name__ == "__main__":
    show_status()
