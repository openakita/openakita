#!/usr/bin/env python3
"""
香农芯创(300475) 大单监控脚本
- 大单标准: 单笔成交金额 > 50万元
- 监控频率: 每10秒
"""

import akshare as ak
import pandas as pd
import json
import os
from datetime import datetime, time as dtime

# 配置
STOCK_CODE = "300475"
STOCK_NAME = "香农芯创"
BIG_ORDER_THRESHOLD = 500000  # 50万元
STATE_FILE = "stock_monitor/last_check_state.json"

def is_trading_time():
    """检查是否在交易时间内"""
    now = datetime.now().time()
    morning_start = dtime(9, 30)
    morning_end = dtime(11, 30)
    afternoon_start = dtime(13, 0)
    afternoon_end = dtime(15, 0)
    
    return (morning_start <= now <= morning_end) or (afternoon_start <= now <= afternoon_end)

def load_state():
    """加载上次检查状态"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {"last_time": None, "alerted_trades": []}

def save_state(state):
    """保存检查状态"""
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def get_stock_trades():
    """获取股票成交数据"""
    try:
        # 尝试获取分时成交数据
        df = ak.stock_zh_a_tick_tx_js(symbol=STOCK_CODE)
        if df is not None and not df.empty:
            return df
    except Exception as e:
        print(f"方法1失败: {e}")
    
    try:
        # 备用方法：获取实时行情
        df = ak.stock_zh_a_spot_em()
        stock_data = df[df['代码'] == STOCK_CODE]
        if not stock_data.empty:
            return stock_data
    except Exception as e:
        print(f"方法2失败: {e}")
    
    return None

def check_big_orders():
    """检查大单"""
    state = load_state()
    alerts = []
    
    # 检查是否在交易时间
    if not is_trading_time():
        weekday = datetime.now().weekday()
        if weekday >= 5:  # 周末
            return None, "非交易日（周末）"
        return None, f"非交易时间，当前时间: {datetime.now().strftime('%H:%M:%S')}"
    
    df = get_stock_trades()
    
    if df is None or df.empty:
        return None, "无法获取数据"
    
    # 处理分时成交数据
    if 'price' in df.columns and 'volume' in df.columns:
        for idx, row in df.iterrows():
            try:
                price = float(row['price'])
                volume = float(row['volume'])
                amount = price * volume * 100  # 成交金额
                
                trade_time = str(row.get('time', ''))
                trade_id = f"{trade_time}_{price}_{volume}"
                
                # 检查是否已提醒过
                if trade_id in state.get('alerted_trades', []):
                    continue
                
                if amount > BIG_ORDER_THRESHOLD:
                    direction = row.get('type', '未知')
                    if direction == 'buy' or direction == '买盘':
                        direction = '🔴 买入'
                    elif direction == 'sell' or direction == '卖盘':
                        direction = '🟢 卖出'
                    else:
                        direction = '⚪ ' + str(direction)
                    
                    alert = {
                        "time": trade_time,
                        "price": price,
                        "volume": volume,
                        "amount": amount,
                        "direction": direction,
                        "trade_id": trade_id
                    }
                    alerts.append(alert)
                    
                    # 记录已提醒
                    if 'alerted_trades' not in state:
                        state['alerted_trades'] = []
                    state['alerted_trades'].append(trade_id)
                    
                    # 只保留最近100条记录
                    if len(state['alerted_trades']) > 100:
                        state['alerted_trades'] = state['alerted_trades'][-100:]
            except Exception as e:
                continue
    
    # 保存状态
    state['last_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    save_state(state)
    
    return alerts, None

def format_alert(alert):
    """格式化提醒消息"""
    return f"""
🚨 **大单异动提醒** 🚨
━━━━━━━━━━━━━━━━━━
📌 股票: {STOCK_NAME} ({STOCK_CODE})
⏰ 时间: {alert['time']}
💰 价格: ¥{alert['price']:.2f}
📊 成交量: {int(alert['volume'])}手
💵 成交金额: ¥{alert['amount']:,.0f}
📈 方向: {alert['direction']}
━━━━━━━━━━━━━━━━━━
"""

def main():
    """主函数"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 检查 {STOCK_NAME}({STOCK_CODE}) 大单...")
    
    alerts, error = check_big_orders()
    
    if error:
        print(f"  状态: {error}")
        return {"status": "skip", "reason": error}
    
    if alerts:
        print(f"  发现 {len(alerts)} 笔大单!")
        result = {"status": "alert", "alerts": []}
        for alert in alerts:
            msg = format_alert(alert)
            print(msg)
            result["alerts"].append(alert)
        return result
    else:
        print("  未发现大单")
        return {"status": "ok", "message": "未发现大单"}

if __name__ == "__main__":
    result = main()
    # 输出JSON结果供外部调用
    print(f"\n__RESULT__:{json.dumps(result, ensure_ascii=False)}")
