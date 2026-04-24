#!/usr/bin/env python3
"""
获取当前时间的脚本

用法:
    python get_time.py [options]

选项:
    --timezone <tz>     时区 (默认: 本地时区)
    --format <fmt>      日期格式 (默认: %Y-%m-%d %H:%M:%S)
    --diff <d1> <d2>    计算两个日期的差值
    --convert <dt>      转换时间
    --from-tz <tz>      源时区
    --to-tz <tz>        目标时区
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def get_current_time(timezone: str = None, fmt: str = None) -> dict:
    """获取当前时间"""
    if timezone:
        try:
            tz = ZoneInfo(timezone)
        except KeyError:
            return {"error": f"Invalid timezone: {timezone}"}
    else:
        tz = None
        timezone = "local"
    
    now = datetime.now(tz)
    fmt = fmt or "%Y-%m-%d %H:%M:%S"
    
    weekday_names = [
        "Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday"
    ]
    
    return {
        "datetime": now.strftime(fmt),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "hour": now.hour,
        "minute": now.minute,
        "second": now.second,
        "weekday": weekday_names[now.weekday()],
        "weekday_num": now.weekday(),
        "timestamp": int(now.timestamp()),
        "iso_format": now.isoformat(),
        "timezone": timezone,
    }


def calculate_diff(date1: str, date2: str) -> dict:
    """计算两个日期的差值"""
    try:
        d1 = datetime.fromisoformat(date1.replace('/', '-'))
        d2 = datetime.fromisoformat(date2.replace('/', '-'))
    except ValueError as e:
        return {"error": f"Invalid date format: {e}"}
    
    diff = abs(d2 - d1)
    
    return {
        "date1": date1,
        "date2": date2,
        "days": diff.days,
        "seconds": diff.seconds,
        "total_seconds": int(diff.total_seconds()),
        "weeks": diff.days // 7,
        "months_approx": diff.days // 30,
        "years_approx": diff.days // 365,
    }


def convert_timezone(dt_str: str, from_tz: str, to_tz: str) -> dict:
    """转换时区"""
    try:
        from_zone = ZoneInfo(from_tz)
        to_zone = ZoneInfo(to_tz)
    except KeyError as e:
        return {"error": f"Invalid timezone: {e}"}
    
    try:
        dt = datetime.fromisoformat(dt_str)
        dt = dt.replace(tzinfo=from_zone)
        converted = dt.astimezone(to_zone)
    except ValueError as e:
        return {"error": f"Invalid datetime format: {e}"}
    
    return {
        "original": dt_str,
        "from_timezone": from_tz,
        "to_timezone": to_tz,
        "converted": converted.strftime("%Y-%m-%d %H:%M:%S"),
        "converted_iso": converted.isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description="DateTime Tool")
    parser.add_argument("--timezone", "-tz", help="Timezone (e.g., Asia/Shanghai)")
    parser.add_argument("--format", "-f", help="Date format string")
    parser.add_argument("--diff", nargs=2, metavar=("DATE1", "DATE2"),
                        help="Calculate difference between two dates")
    parser.add_argument("--convert", help="DateTime to convert")
    parser.add_argument("--from-tz", help="Source timezone for conversion")
    parser.add_argument("--to-tz", help="Target timezone for conversion")
    
    args = parser.parse_args()
    
    if args.diff:
        result = calculate_diff(args.diff[0], args.diff[1])
    elif args.convert:
        if not args.from_tz or not args.to_tz:
            result = {"error": "Both --from-tz and --to-tz required for conversion"}
        else:
            result = convert_timezone(args.convert, args.from_tz, args.to_tz)
    else:
        result = get_current_time(args.timezone, args.format)
    
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

