"""
触发器定义

支持三种触发类型:
- OnceTrigger: 一次性（指定时间执行）
- IntervalTrigger: 间隔（每 N 分钟/小时）
- CronTrigger: Cron 表达式
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class Trigger(ABC):
    """触发器基类"""

    @abstractmethod
    def get_next_run_time(self, last_run: datetime | None = None) -> datetime | None:
        """
        计算下一次运行时间

        Args:
            last_run: 上次运行时间（None 表示从未运行）

        Returns:
            下一次运行时间，None 表示不再运行
        """
        pass

    @abstractmethod
    def should_run(self, last_run: datetime | None = None) -> bool:
        """
        检查是否应该运行

        Args:
            last_run: 上次运行时间

        Returns:
            是否应该运行
        """
        pass

    @classmethod
    def from_config(cls, trigger_type: str, config: dict) -> "Trigger":
        """
        从配置创建触发器

        Args:
            trigger_type: 触发器类型 (once/interval/cron)
            config: 触发器配置

        Returns:
            触发器实例
        """
        if trigger_type == "once":
            return OnceTrigger.from_config(config)
        elif trigger_type == "interval":
            return IntervalTrigger.from_config(config)
        elif trigger_type == "cron":
            return CronTrigger.from_config(config)
        else:
            raise ValueError(f"Unknown trigger type: {trigger_type}")


class OnceTrigger(Trigger):
    """
    一次性触发器

    在指定时间执行一次
    """

    def __init__(self, run_at: datetime):
        self.run_at = run_at
        self._fired = False

    def get_next_run_time(self, last_run: datetime | None = None) -> datetime | None:
        if last_run is not None or self._fired:
            return None
        return self.run_at

    def should_run(self, last_run: datetime | None = None) -> bool:
        if last_run is not None or self._fired:
            return False
        return datetime.now() >= self.run_at

    def mark_fired(self) -> None:
        self._fired = True

    @classmethod
    def from_config(cls, config: dict) -> "OnceTrigger":
        run_at = config.get("run_at")
        if isinstance(run_at, str):
            run_at = datetime.fromisoformat(run_at)
        elif isinstance(run_at, (int, float)):
            run_at = datetime.fromtimestamp(run_at)

        if not run_at:
            raise ValueError("OnceTrigger requires 'run_at' in config")

        return cls(run_at=run_at)


class IntervalTrigger(Trigger):
    """
    间隔触发器

    每隔固定时间执行一次
    """

    def __init__(
        self,
        interval_seconds: int = 0,
        interval_minutes: int = 0,
        interval_hours: int = 0,
        interval_days: int = 0,
        start_time: datetime | None = None,
    ):
        """
        Args:
            interval_seconds: 间隔秒数
            interval_minutes: 间隔分钟数
            interval_hours: 间隔小时数
            interval_days: 间隔天数
            start_time: 起始时间（默认为当前时间）
        """
        self.interval = timedelta(
            seconds=interval_seconds,
            minutes=interval_minutes,
            hours=interval_hours,
            days=interval_days,
        )

        if self.interval.total_seconds() <= 0:
            raise ValueError("Interval must be positive")

        self.start_time = start_time or datetime.now()

    def get_next_run_time(self, last_run: datetime | None = None) -> datetime:
        now = datetime.now()

        if last_run is None:
            # 首次运行：计算从 start_time 开始的下一个间隔时间点
            # 注意：不立即执行，而是等到下一个间隔
            if now < self.start_time:
                # start_time 还没到，返回 start_time
                return self.start_time

            # start_time 已过，计算下一个对齐的时间点
            elapsed = now - self.start_time
            intervals_passed = int(elapsed.total_seconds() / self.interval.total_seconds())
            next_run = self.start_time + self.interval * (intervals_passed + 1)
            return next_run

        # 计算下一次运行时间
        next_run = last_run + self.interval

        # 如果下一次运行时间已过，计算最近的下一次
        while next_run < now:
            next_run += self.interval

        return next_run

    def should_run(self, last_run: datetime | None = None) -> bool:
        next_run = self.get_next_run_time(last_run)
        return datetime.now() >= next_run

    @classmethod
    def from_config(cls, config: dict) -> "IntervalTrigger":
        interval_seconds = config.get("interval_seconds", 0)
        interval_minutes = config.get("interval_minutes", 0)
        interval_hours = config.get("interval_hours", 0)
        interval_days = config.get("interval_days", 0)

        # 简化配置：如果只指定了 interval，默认为分钟
        if "interval" in config:
            interval_minutes = config["interval"]

        start_time = config.get("start_time")
        if isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time)

        return cls(
            interval_seconds=interval_seconds,
            interval_minutes=interval_minutes,
            interval_hours=interval_hours,
            interval_days=interval_days,
            start_time=start_time,
        )


class CronTrigger(Trigger):
    """
    Cron 表达式触发器

    支持标准 cron 表达式:
    分 时 日 月 周

    示例:
    - "0 9 * * *"     每天 9:00
    - "*/15 * * * *"  每 15 分钟
    - "0 9 * * 1"     每周一 9:00
    - "0 0 1 * *"     每月 1 日 0:00
    """

    def __init__(self, cron_expression: str):
        """
        Args:
            cron_expression: cron 表达式
        """
        self.expression = cron_expression
        self._parse_expression()

    def _parse_expression(self) -> None:
        """解析 cron 表达式"""
        parts = self.expression.strip().split()

        if len(parts) != 5:
            raise ValueError(
                f"Invalid cron expression: {self.expression}. "
                "Expected 5 fields: minute hour day month weekday"
            )

        self.minute_spec = self._parse_field(parts[0], 0, 59)
        self.hour_spec = self._parse_field(parts[1], 0, 23)
        self.day_spec = self._parse_field(parts[2], 1, 31)
        self.month_spec = self._parse_field(parts[3], 1, 12)
        self.weekday_spec = self._parse_field(parts[4], 0, 6)  # 0=周日

    def _parse_field(self, field: str, min_val: int, max_val: int) -> set[int]:
        """
        解析单个字段

        支持:
        - *: 所有值
        - N: 单个值
        - N-M: 范围
        - */N: 步进
        - N,M,K: 列表
        """
        result = set()

        for part in field.split(","):
            if part == "*":
                result.update(range(min_val, max_val + 1))
            elif "/" in part:
                # 步进 (*/N 或 M-N/S)
                base, step = part.split("/")
                step = int(step)

                if base == "*":
                    result.update(range(min_val, max_val + 1, step))
                elif "-" in base:
                    start, end = map(int, base.split("-"))
                    result.update(range(start, end + 1, step))
                else:
                    start = int(base)
                    result.update(range(start, max_val + 1, step))
            elif "-" in part:
                # 范围
                start, end = map(int, part.split("-"))
                result.update(range(start, end + 1))
            else:
                # 单个值
                result.add(int(part))

        return result

    def get_next_run_time(self, last_run: datetime | None = None) -> datetime:
        """计算下一次运行时间（层级跳跃搜索，避免逐分钟遍历）"""
        if last_run:
            start = last_run + timedelta(minutes=1)
        else:
            start = datetime.now() + timedelta(minutes=1)

        start = start.replace(second=0, microsecond=0)

        current = start
        # 每次迭代最少前进 1 分钟，最多搜索约 4 年（48 月 × 31 天）
        max_iterations = 48 * 31

        for _ in range(max_iterations):
            if current.month not in self.month_spec:
                # 跳到下一个匹配的月份
                current = self._next_matching_month(current)
                if current is None:
                    break
                continue

            if current.day not in self.day_spec or \
               current.weekday() not in self._convert_weekday(self.weekday_spec):
                # 跳到下一天
                current = (current + timedelta(days=1)).replace(hour=0, minute=0)
                if current > start + timedelta(days=max_iterations):
                    break
                continue

            if current.hour not in self.hour_spec:
                # 跳到下一个匹配的小时
                next_hour = self._next_in_set(current.hour, self.hour_spec)
                if next_hour is not None and next_hour > current.hour:
                    current = current.replace(hour=next_hour, minute=0)
                else:
                    current = (current + timedelta(days=1)).replace(hour=0, minute=0)
                continue

            if current.minute not in self.minute_spec:
                next_min = self._next_in_set(current.minute, self.minute_spec)
                if next_min is not None and next_min > current.minute:
                    current = current.replace(minute=next_min)
                else:
                    # 跳到下一小时
                    current = (current + timedelta(hours=1)).replace(minute=0)
                continue

            return current

        logger.warning(f"Could not find next run time for cron: {self.expression}")
        return start + timedelta(days=365)

    @staticmethod
    def _next_in_set(current_val: int, spec: set[int]) -> int | None:
        """找到 spec 中 > current_val 的最小值"""
        candidates = [v for v in spec if v > current_val]
        return min(candidates) if candidates else None

    def _next_matching_month(self, current: datetime) -> datetime | None:
        """跳到下一个匹配 month_spec 的月份首日"""
        for _ in range(48):
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1, day=1, hour=0, minute=0)
            else:
                current = current.replace(month=current.month + 1, day=1, hour=0, minute=0)
            if current.month in self.month_spec:
                return current
        return None

    def _matches(self, dt: datetime) -> bool:
        """检查时间是否匹配 cron 表达式"""
        return (
            dt.minute in self.minute_spec
            and dt.hour in self.hour_spec
            and dt.day in self.day_spec
            and dt.month in self.month_spec
            and dt.weekday() in self._convert_weekday(self.weekday_spec)
        )

    def _convert_weekday(self, weekday_spec: set[int]) -> set[int]:
        """
        转换星期规范

        cron: 0=周日, 1=周一, ..., 6=周六, 7=周日(兼容)
        Python: 0=周一, 1=周二, ..., 6=周日
        """
        result = set()
        for w in weekday_spec:
            if w == 0 or w == 7:
                result.add(6)  # 周日
            else:
                result.add(w - 1)
        return result

    def should_run(self, last_run: datetime | None = None) -> bool:
        next_run = self.get_next_run_time(last_run)
        return datetime.now() >= next_run

    @classmethod
    def from_config(cls, config: dict) -> "CronTrigger":
        cron = config.get("cron")
        if not cron:
            raise ValueError("CronTrigger requires 'cron' in config")
        return cls(cron_expression=cron)

    def describe(self) -> str:
        """生成人类可读的描述"""
        # 简化描述
        descriptions = {
            "* * * * *": "每分钟",
            "0 * * * *": "每小时",
            "0 0 * * *": "每天午夜",
            "0 9 * * *": "每天上午9点",
            "0 9 * * 1": "每周一上午9点",
            "0 0 1 * *": "每月1日午夜",
        }

        return descriptions.get(self.expression, f"Cron: {self.expression}")
