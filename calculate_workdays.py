from datetime import date, timedelta

def calculate_workdays(start_date, days_count):
    """计算从start_date开始days_count天内的工作日（周一到周五），排除法定假日"""
    
    # 2026年法定假日（只列出落在周一到周五的假日）
    # 根据国务院办公厅关于2026年部分节假日安排的通知
    holidays_2026 = {
        # 清明节：4月4日（周六）至6日（周一）
        date(2026, 4, 6),   # 周一，假日
        
        # 劳动节：5月1日（周五）至5日（周二）
        date(2026, 5, 1),   # 周五，假日
        date(2026, 5, 4),   # 周一，假日
        date(2026, 5, 5),   # 周二，假日
        
        # 端午节：6月19日（周五）至21日（周日）
        date(2026, 6, 19),  # 周五，假日
    }
    
    end_date = start_date + timedelta(days=days_count-1)  # 包括起始日
    
    print(f"日期范围: {start_date} 到 {end_date}（共{days_count}天）")
    print(f"法定假日（周一到周五）: {sorted(holidays_2026)}")
    
    workdays = 0
    holiday_count = 0
    
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:  # 周一到周五
            if current in holidays_2026:
                holiday_count += 1
                print(f"  {current} ({['周一','周二','周三','周四','周五','周六','周日'][current.weekday()]}) - 法定假日，排除")
            else:
                workdays += 1
        current += timedelta(days=1)
    
    print(f"\n计算结果:")
    print(f"  总天数: {days_count}")
    print(f"  周一到周五天数: {workdays + holiday_count}")
    print(f"  法定假日天数（落在周一到周五）: {holiday_count}")
    print(f"  工作日天数: {workdays}")
    
    return workdays

if __name__ == "__main__":
    start = date(2026, 4, 1)
    total_days = 100
    
    result = calculate_workdays(start, total_days)
    print(f"\n从 {start} 开始 {total_days} 天内的工作日数量为: {result}")