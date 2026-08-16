"""Счётчики блокировок входа: сколько раз каждая причина отсекла сигнал.

Позволяют видеть, какой фильтр режет входы чаще всего: retest-зона,
размер импульса, R:R до стены, лента и т.д. — и крутить пороги в .env.
"""

import time
from collections import defaultdict

from core.logger import get_logger

log = get_logger("counters")


class EntryCounters:
    def __init__(self):
        self._counts = defaultdict(int)
        self._period_started = time.time()

    def inc(self, name: str, n: int = 1) -> None:
        self._counts[name] += n

    def get(self, name: str) -> int:
        return int(self._counts.get(name, 0))

    def total(self) -> int:
        return sum(self._counts.values())

    def reset(self) -> None:
        self._counts.clear()
        self._period_started = time.time()

    def summary_parts(self) -> list:
        return [f"{name}={n}" for name, n in
                sorted(self._counts.items(), key=lambda kv: -kv[1]) if n > 0]

    def log_summary(self, label: str = "Блокировки входа") -> None:
        parts = self.summary_parts()
        if not parts:
            return
        log.info(f"{label}: {', '.join(parts)}")

    def log_if_due(self, interval_s: float = 600.0, label: str = "Блокировки входа") -> bool:
        """Логирует накопленные счётчики раз в interval_s секунд и сбрасывает их."""
        if time.time() - self._period_started < interval_s:
            return False
        self.log_summary(label)
        self.reset()
        return True


entry_counters = EntryCounters()
