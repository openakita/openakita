#!/usr/bin/env python3
"""
香农芯创(300475) 大单监控 - 后台静默版
只在发现大单时才提醒，支持状态查询
"""
import os
import json
import time
from datetime import datetime

# 配置
CONFIG = {
    "stock_code": "300475",
    "stock_name": "香农芯创",
    "threshold": 500000,  # 50万
    "check_interval": 10,  # 10秒
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(BASE_DIR, "status.json")
LOG_FILE = os.path.join(BASE_DIR, "monitor.log")
ALERT_FILE = os.path.join(BASE_DIR, "alerts.json")

def log(msg):
    """静默日志，只写文件不输出"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {msg}\n")

def update_status(status, last_price=None, check_count=0, alert_count=0, started_at=None):
    """更新状态文件"""
    data = {
        "status": status,
        "stock": CONFIG["stock_name"],
        "code": CONFIG["stock_code"],
        "threshold": CONFIG["threshold"],
        "last_check": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_price": last_price,
        "check_count": check_count,
        "alert_count": alert_count,
        "started_at": started_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def save_alert(alert_info):
    """保存大单提醒记录"""
    alerts = []
    if os.path.exists(ALERT_FILE):
        try:
            with open(ALERT_FILE, "r", encoding="utf-8") as f:
                alerts = json.load(f)
        except:
            pass
    alerts.append(alert_info)
    alerts = alerts[-100:]  # 只保留最近100条
    with open(ALERT_FILE, "w", encoding="utf-8") as f:
        json.dump(alerts, f, ensure_ascii=False, indent=2)

def check_big_orders():
    """检查大单"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_tick_tx_js(symbol=CONFIG["stock_code"])
        if df is not None and not df.empty:
            latest = df.iloc[0]
            price = float(latest['price'])
            volume = int(latest['volume'])
            amount = price * volume * 100
            
            if amount > CONFIG["threshold"]:
                return {
                    "found": True,
                    "price": price,
                    "volume": volume,
                    "amount": amount,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
            return {"found": False, "price": price}
        return {"found": False, "price": None}
    except Exception as e:
        log(f"数据获取错误: {e}")
        return {"found": False, "price": None, "error": str(e)}

def main():
    """主循环 - 静默运行"""
    log("监控启动")
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    update_status("running", started_at=started_at)
    
    check_count = 0
    alert_count = 0
    last_price = None
    
    while True:
        try:
            result = check_big_orders()
            check_count += 1
            
            if result.get("price"):
                last_price = result["price"]
            
            if result.get("found"):
                alert_count += 1
                alert_info = {
                    "time": result["time"],
                    "price": result["price"],
                    "volume": result["volume"],
                    "amount": result["amount"]
                }
                save_alert(alert_info)
                log(f"🚨 大单发现! 金额: ¥{result['amount']:,.2f}")
                # 只在发现大单时输出提醒
                print(f"🚨 香农芯创大单异动! 成交金额: ¥{result['amount']:,.2f}")
            
            update_status("running", last_price, check_count, alert_count, started_at)
            time.sleep(CONFIG["check_interval"])
            
        except KeyboardInterrupt:
            log("监控停止")
            update_status("stopped", last_price, check_count, alert_count, started_at)
            break
        except Exception as e:
            log(f"错误: {e}")
            time.sleep(CONFIG["check_interval"])

if __name__ == "__main__":
    main()
