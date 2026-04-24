---
name: openakita/skills@datetime-tool
description: Get current time, format dates, calculate date differences, and convert timezones. Use this skill when the user asks about time, dates, timezone conversions, weekdays, or needs date calculations and formatting in any locale.
license: MIT
metadata:
  author: myagent
  version: "1.0.0"
---

# DateTime Tool

处理时间和日期相关的操作。

## When to Use

- 用户询问当前时间或日期
- 需要格式化日期输出
- 计算两个日期之间的差值
- 时区转换
- 获取星期几、月份名称等

## Instructions

### 获取当前时间

运行脚本获取当前时间:

```bash
python scripts/get_time.py
```

支持的参数:
- `--timezone <tz>`: 指定时区 (如 Asia/Shanghai, UTC)
- `--format <fmt>`: 日期格式 (如 %Y-%m-%d %H:%M:%S)

### 计算日期差值

```bash
python scripts/get_time.py --diff "2024-01-01" "2024-12-31"
```

### 时区转换

```bash
python scripts/get_time.py --convert "2024-01-01 12:00:00" --from-tz UTC --to-tz Asia/Shanghai
```

## Output Format

脚本输出 JSON 格式:

```json
{
  "datetime": "2024-01-15 10:30:00",
  "date": "2024-01-15",
  "time": "10:30:00",
  "timezone": "Asia/Shanghai",
  "weekday": "Monday",
  "timestamp": 1705285800
}
```

## Common Formats

| 格式 | 示例 |
|------|------|
| ISO | 2024-01-15T10:30:00+08:00 |
| 中文 | 2024年01月15日 10:30:00 |
| 美式 | 01/15/2024 |
| 欧式 | 15/01/2024 |

