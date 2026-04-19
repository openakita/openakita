---
name: openakita/skills@datetime-tool
description: Get current time, format dates, calculate date differences, and convert timezones.
license: MIT
metadata:
  author: openakita
  version: "1.0.0"
---

# DateTime Tool

Handle time and date-related operations.

## When to Use

- User asks for the current time or date
- Format dates into specific formats
- Calculate the difference between two dates
- Convert between timezones
- Natural language time expressions (e.g., "what time is it 3 hours from now")
- Calculate week numbers, day of week, etc.

## Pre-built Scripts

### scripts/get_time.py
Get the current time and date.

```bash
python scripts/get_time.py
```

### scripts/format_date.py
Format a date string.

```bash
python scripts/format_date.py --date "2024-01-15" --format "%Y-%m-%d"
```

### scripts/date_diff.py
Calculate the difference between two dates.

```bash
python scripts/date_diff.py --start "2024-01-01" --end "2024-12-31"
```

### scripts/timezone_convert.py
Convert time between timezones.

```bash
python scripts/timezone_convert.py --time "2024-01-15 10:30:00" --from "Asia/Shanghai" --to "America/New_York"
```

## Output Format

All scripts return JSON format:

```json
{
  "success": true,
  "operation": "get_time",
  "data": {
    "current_time": "2024-01-15 10:30:00",
    "timezone": "Asia/Shanghai",
    "unix_timestamp": 1705289400
  }
}
```

## Notes

- All times default to the system timezone
- Timezone names follow the IANA timezone database
- Date difference returns both days and a human-readable format
- Supported date formats: YYYY-MM-DD, YYYY/MM/DD, DD-MM-YYYY, and more
