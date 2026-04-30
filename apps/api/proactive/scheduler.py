"""Asyncio-based scheduler for the proactive briefing.

We deliberately don't pull in APScheduler / Celery — a single periodic task
fits in 30 lines of asyncio and avoids a new dependency. The scheduler is
started from FastAPI's lifespan hook and cancelled cleanly on shutdown.
"""
from __future__ import annotations

import asyncio
import logging

from apps.api.env_context import active_env, active_system
from apps.api.proactive.briefing import run_briefing
from apps.api.settings import get_settings

log = logging.getLogger("sherlock.proactive")


class BriefingScheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def _loop(self) -> None:
        s = get_settings()
        interval = max(60, s.sherlock_briefing_interval_seconds)
        # Set context for cron-triggered runs so probes/causes use defaults
        # rather than raising on missing contextvars.
        active_env.set(s.sherlock_default_env.lower())
        active_system.set("mssql")

        if s.sherlock_briefing_on_startup:
            log.info("[sherlock.proactive] running startup briefing")
            try:
                rec = await run_briefing(triggered_by="cron")
                log.info(
                    "[sherlock.proactive] startup briefing id=%s severity=%s anomalies=%s",
                    rec["id"], rec["severity"], rec["anomalies"],
                )
            except Exception as e:
                log.exception("[sherlock.proactive] startup briefing failed: %s", e)

        while not self._stop.is_set():
            try:
                # Sleep, but wake early if stop is signalled.
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                if self._stop.is_set():
                    break
            except asyncio.TimeoutError:
                pass
            try:
                rec = await run_briefing(triggered_by="cron")
                log.info(
                    "[sherlock.proactive] tick id=%s severity=%s anomalies=%s",
                    rec["id"], rec["severity"], rec["anomalies"],
                )
            except Exception as e:
                # Log + keep going. Don't let a probe blip kill the scheduler.
                log.exception("[sherlock.proactive] tick failed: %s", e)

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="sherlock-proactive-loop")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()


_scheduler: BriefingScheduler | None = None


def get_scheduler() -> BriefingScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BriefingScheduler()
    return _scheduler
