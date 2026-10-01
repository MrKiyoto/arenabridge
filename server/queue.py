"""
Модуль очереди запросов.
Обеспечивает:
- Ограничение параллелизма (max_concurrency, по умолчанию 1);
- FIFO-очередь с таймаутом ожидания;
- Настраиваемую паузу между запросами (min_request_interval) для защиты от rate-limit (429);
- Учет размера очереди для мониторинга (/health).
"""

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger("arena_bridge.queue")


class QueueSlotContext:
    def __init__(self, queue: "RequestQueue", timeout: Optional[float] = None):
        self.queue = queue
        self.timeout = timeout if timeout is not None else queue.default_timeout
        self._acquired = False

    async def __aenter__(self):
        self.queue._waiting_count += 1
        try:
            # Ожидание слота в семафоре с учетом таймаута
            await asyncio.wait_for(self.queue._semaphore.acquire(), timeout=self.timeout)
            self._acquired = True
        except asyncio.TimeoutError:
            self.queue._waiting_count -= 1
            logger.warning("Время ожидания в очереди запросов истекло (таймаут).")
            raise TimeoutError("Время ожидания в очереди запросов истекло.")

        self.queue._waiting_count -= 1
        self.queue._active_count += 1

        # Соблюдение минимального интервала между запросами к arena.ai
        if self.queue.min_interval > 0 and self.queue._last_finished_at > 0:
            elapsed = time.monotonic() - self.queue._last_finished_at
            remaining = self.queue.min_interval - elapsed
            if remaining > 0:
                logger.debug(f"Ожидание перед следующим запросом: {remaining:.2f} сек.")
                await asyncio.sleep(remaining)

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._acquired:
            self.queue._last_finished_at = time.monotonic()
            self.queue._active_count -= 1
            self.queue._semaphore.release()
            self._acquired = False


class RequestQueue:
    def __init__(
        self,
        max_concurrency: int = 1,
        min_interval: float = 1.5,
        default_timeout: float = 180.0,
    ):
        self.max_concurrency = max(1, max_concurrency)
        self.min_interval = max(0.0, min_interval)
        self.default_timeout = max(5.0, default_timeout)

        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._waiting_count = 0
        self._active_count = 0
        self._last_finished_at = 0.0

    @property
    def waiting_count(self) -> int:
        """Количество запросов, ожидающих в очереди."""
        return max(0, self._waiting_count)

    @property
    def active_count(self) -> int:
        """Количество активных запросов, выполняющихся прямо сейчас."""
        return max(0, self._active_count)

    @property
    def total_in_flight(self) -> int:
        """Общее число запросов в системе (активные + ожидающие)."""
        return self.active_count + self.waiting_count

    def acquire(self, timeout: Optional[float] = None) -> QueueSlotContext:
        """Возвращает асинхронный контекстный менеджер для выполнения запроса в очереди."""
        return QueueSlotContext(self, timeout=timeout)
