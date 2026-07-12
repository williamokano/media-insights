"""Periodic deep scan via APScheduler."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from media_insights.config import AppConfig
from media_insights.scanner.service import scan_all

log = logging.getLogger(__name__)


class ScanScheduler:
    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self._scheduler: BackgroundScheduler | None = None

    def start(self) -> None:
        if not self._cfg.schedule.enabled:
            log.info("scheduler disabled by config")
            return
        scheduler = BackgroundScheduler(timezone="UTC")
        try:
            trigger = CronTrigger.from_crontab(self._cfg.schedule.cron)
        except ValueError as exc:
            log.error("invalid cron %r: %s", self._cfg.schedule.cron, exc)
            return
        scheduler.add_job(self._run, trigger, id="deep-scan", replace_existing=True)
        scheduler.start()
        self._scheduler = scheduler
        log.info("scheduler started with cron=%r", self._cfg.schedule.cron)

    def stop(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            log.info("scheduler stopped")

    def _run(self) -> None:
        try:
            results = scan_all(self._cfg, force=False)
            log.info("periodic scan finished: %s", results)
        except Exception as exc:
            log.exception("periodic scan failed: %s", exc)
