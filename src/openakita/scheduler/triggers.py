"""
Trigger definitions

Supports three trigger types:
- OnceTrigger: one-shot (execute at a specified time)
- IntervalTrigger: recurring (every N minutes/hours)
- CronTrigger: cron expression
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class Trigger(ABC):
    """Trigger base class"""

    @abstractmethod
    def get_next_run_time(self, last_run: datetime | None = None) -> datetime | None:
        """
        Calculate the next run time.

        Args:
            last_run: Previous run time (None = never run)

        Returns:
            Next run time, or None if no further runs
        """
        pass

    @abstractmethod
    def should_run(self, last_run: datetime | None = None) -> bool:
        """
        Check whether the trigger should fire.

        Args:
            last_run: Previous run time

        Returns:
            Whether the trigger should run
        """
        pass

    @classmethod
    def from_config(cls, trigger_type: str, config: dict) -> "Trigger":
        """
        Create a trigger from config.

        Args:
            trigger_type: Trigger type (once/interval/cron)
            config: Trigger configuration

        Returns:
            Trigger instance
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
    One-shot trigger

    Executes once at the specified time.
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
    Interval trigger

    Executes at a fixed interval.
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
            interval_seconds: Interval in seconds
            interval_minutes: Interval in minutes
            interval_hours: Interval in hours
            interval_days: Interval in days
            start_time: Start time (defaults to now)
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
            # First run: calculate the next aligned interval from start_time
            # Note: do not execute immediately; wait for the next interval
            if now < self.start_time:
                # start_time has not yet been reached
                return self.start_time

            # start_time has passed; calculate next aligned time point
            elapsed = now - self.start_time
            intervals_passed = int(elapsed.total_seconds() / self.interval.total_seconds())
            next_run = self.start_time + self.interval * (intervals_passed + 1)
            return next_run

        # Calculate next run time
        next_run = last_run + self.interval

        # If the next run time has passed, find the nearest upcoming one
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

        # Convenience: if only "interval" is specified, treat it as minutes
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
    Cron expression trigger

    Supports standard cron expressions:
    minute hour day month weekday

    Examples:
    - "0 9 * * *"     Every day at 9:00
    - "*/15 * * * *"  Every 15 minutes
    - "0 9 * * 1"     Every Monday at 9:00
    - "0 0 1 * *"     First of every month at 0:00
    """

    def __init__(self, cron_expression: str):
        """
        Args:
            cron_expression: Cron expression
        """
        self.expression = cron_expression
        self._parse_expression()

    def _parse_expression(self) -> None:
        """Parse the cron expression."""
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
        self.weekday_spec = self._parse_field(parts[4], 0, 6)  # 0=Sunday

    def _parse_field(self, field: str, min_val: int, max_val: int) -> set[int]:
        """
        Parse a single field.

        Supports:
        - *: all values
        - N: single value
        - N-M: range
        - */N: step
        - N,M,K: list
        """
        result = set()

        for part in field.split(","):
            if part == "*":
                result.update(range(min_val, max_val + 1))
            elif "/" in part:
                # Step (*/N or M-N/S)
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
                # Range
                start, end = map(int, part.split("-"))
                result.update(range(start, end + 1))
            else:
                # Single value
                result.add(int(part))

        return result

    def get_next_run_time(self, last_run: datetime | None = None) -> datetime:
        """Calculate the next run time (hierarchical skip search, avoids per-minute iteration)."""
        if last_run:
            start = last_run + timedelta(minutes=1)
        else:
            start = datetime.now() + timedelta(minutes=1)

        start = start.replace(second=0, microsecond=0)

        current = start
        # Each iteration advances at least 1 minute; search up to ~4 years (48 months x 31 days)
        max_iterations = 48 * 31

        for _ in range(max_iterations):
            if current.month not in self.month_spec:
                # Skip to next matching month
                current = self._next_matching_month(current)
                if current is None:
                    break
                continue

            if current.day not in self.day_spec or current.weekday() not in self._convert_weekday(
                self.weekday_spec
            ):
                # Skip to next day
                current = (current + timedelta(days=1)).replace(hour=0, minute=0)
                if current > start + timedelta(days=max_iterations):
                    break
                continue

            if current.hour not in self.hour_spec:
                # Skip to next matching hour
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
                    # Skip to next hour
                    current = (current + timedelta(hours=1)).replace(minute=0)
                continue

            return current

        logger.warning(f"Could not find next run time for cron: {self.expression}")
        return start + timedelta(days=365)

    @staticmethod
    def _next_in_set(current_val: int, spec: set[int]) -> int | None:
        """Find the smallest value in spec that is > current_val."""
        candidates = [v for v in spec if v > current_val]
        return min(candidates) if candidates else None

    def _next_matching_month(self, current: datetime) -> datetime | None:
        """Skip to the first day of the next month matching month_spec."""
        for _ in range(48):
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1, day=1, hour=0, minute=0)
            else:
                current = current.replace(month=current.month + 1, day=1, hour=0, minute=0)
            if current.month in self.month_spec:
                return current
        return None

    def _matches(self, dt: datetime) -> bool:
        """Check whether a datetime matches the cron expression."""
        return (
            dt.minute in self.minute_spec
            and dt.hour in self.hour_spec
            and dt.day in self.day_spec
            and dt.month in self.month_spec
            and dt.weekday() in self._convert_weekday(self.weekday_spec)
        )

    def _convert_weekday(self, weekday_spec: set[int]) -> set[int]:
        """
        Convert weekday spec.

        cron: 0=Sunday, 1=Monday, ..., 6=Saturday, 7=Sunday (compat)
        Python: 0=Monday, 1=Tuesday, ..., 6=Sunday
        """
        result = set()
        for w in weekday_spec:
            if w == 0 or w == 7:
                result.add(6)  # Sunday
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
        """Generate a human-readable description."""
        # Simplified descriptions
        descriptions = {
            "* * * * *": "Every minute",
            "0 * * * *": "Every hour",
            "0 0 * * *": "Every day at midnight",
            "0 9 * * *": "Every day at 9:00 AM",
            "0 9 * * 1": "Every Monday at 9:00 AM",
            "0 0 1 * *": "First of every month at midnight",
        }

        return descriptions.get(self.expression, f"Cron: {self.expression}")
