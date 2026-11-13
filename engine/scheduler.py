from __future__ import annotations

from datetime import datetime, timedelta
import threading
import time

from config.settings import settings
from domain.b3_rules import BRT, get_brt_now


class CycleScheduler:
    def __init__(
        self,
        trading_interval: int | None = None,
        closed_interval: int | None = None,
        second_offset: int | None = None,
    ) -> None:
        self.trading_interval: int = max(
            5,
            int(trading_interval if trading_interval is not None else settings.TRADING_CYCLE_INTERVAL),
        )
        self.closed_interval: int = max(
            10,
            int(closed_interval if closed_interval is not None else settings.CLOSED_CHECK_INTERVAL),
        )
        self.second_offset: int = int(
            second_offset if second_offset is not None else settings.CYCLE_SECOND_OFFSET
        ) % 60
        self._wake_event = threading.Event()

    def compute_sleep_seconds(self, market_open: bool, now: datetime | None = None) -> float:
        ref = (
            (now.astimezone(BRT) if now.tzinfo is not None else now.replace(tzinfo=BRT))
            if now is not None
            else get_brt_now()
        )
        interval = float(self.trading_interval if market_open else self.closed_interval)
        anchor = float(self.second_offset)
        elapsed = (ref.timestamp() - anchor) % interval
        wait_sec = interval - elapsed
        min_guard = min(2.0, interval * 0.25)
        if wait_sec < min_guard:
            wait_sec += interval
        return round(wait_sec, 3)

    def get_sleep_duration(self, market_open: bool, now: datetime | None = None) -> float:
        return self.compute_sleep_seconds(market_open=market_open, now=now)

    def next_run_at(self, market_open: bool, now: datetime | None = None) -> datetime:
        ref = (
            (now.astimezone(BRT) if now.tzinfo is not None else now.replace(tzinfo=BRT))
            if now is not None
            else get_brt_now()
        )
        wait_sec = self.compute_sleep_seconds(market_open=market_open, now=ref)
        return ref + timedelta(seconds=wait_sec)

    def trigger_immediate(self) -> None:
        self._wake_event.set()

    def interrupt(self) -> None:
        self._wake_event.set()

    def wait_next_cycle(
        self,
        market_open: bool,
        stop_event: threading.Event | None = None,
    ) -> bool:
        total_wait = self.compute_sleep_seconds(market_open=market_open)
        deadline = time.monotonic() + total_wait
        while True:
            if stop_event is not None and stop_event.is_set():
                return False
            if self._wake_event.is_set():
                self._wake_event.clear()
                return not (stop_event is not None and stop_event.is_set())
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return not (stop_event is not None and stop_event.is_set())
            chunk = remaining if stop_event is None else min(0.5, remaining)
            woken = self._wake_event.wait(timeout=chunk)
            if woken:
                self._wake_event.clear()
                return not (stop_event is not None and stop_event.is_set())

    def wait(
        self,
        market_open: bool,
        stop_event: threading.Event | None = None,
    ) -> bool:
        return self.wait_next_cycle(market_open=market_open, stop_event=stop_event)
