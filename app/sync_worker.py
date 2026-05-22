"""Standalone CommCare sync worker.

Runs in its own container (``geo_tracker_sync_worker``) consuming jobs from
the Redis queue defined in ``app.services.job_queue``. Because this process
is not the uvicorn API process, hot-reloads / API restarts have zero effect
on an in-flight sync.

Loop shape:
  1. BLPOP the queue with a 5-second timeout (so we can react to shutdown).
  2. Run the sync inside an ``asyncio.wait_for`` with a 30-minute cap, so a
     hung CommCare request never blocks the queue forever.
  3. On any failure, the sync function itself records 'error' into
     ``sync_history`` / ``sync_config`` (the existing per-step try/except).
     We log + continue to the next job.
  4. On SIGTERM / SIGINT, finish the in-flight job (best-effort) and exit
     cleanly so docker / systemd don't have to SIGKILL us.

Stuck-job recovery: at startup we mark any ``running`` rows as errored with
``'Sync was interrupted by worker restart'``. This mirrors the same
behaviour the API lifespan already does for API-side restarts. Without it,
a worker crash leaves the UI's Sync Now button permanently disabled.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Optional

from sqlalchemy import text

from app.database import AsyncSessionLocal
from app.services.commcare_sync import run_sync
from app.services.job_queue import dequeue_sync_job, enqueue_sync_job, queue_depth as redis_queue_depth

logger = logging.getLogger("sync_worker")

# Worker tunables — env-overridable so prod / dev can differ.
SYNC_JOB_TIMEOUT_SECS    = int(os.getenv("SYNC_JOB_TIMEOUT_SECS", "1800"))   # 30 min
BLPOP_TIMEOUT_SECS       = int(os.getenv("SYNC_QUEUE_POLL_SECS",  "5"))
# Auto-sync scheduler: how often the scheduler_loop wakes up and decides
# whether to enqueue a job. 60s is fine — interval granularity for users is
# in minutes anyway.
SCHEDULER_TICK_SECS      = int(os.getenv("SYNC_SCHEDULER_TICK_SECS", "60"))

_shutdown = asyncio.Event()


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _shutdown.set)
        except NotImplementedError:
            # Windows / some sandboxes — fall back to default handlers.
            pass


async def _recover_stuck_jobs() -> None:
    """Mark any ``running`` sync rows as errored with a clear message."""
    async with AsyncSessionLocal() as db:
        try:
            r1 = await db.execute(text("""
                UPDATE sync_config SET
                  last_status         = 'error',
                  last_error          = 'Sync was interrupted by worker restart',
                  last_progress_step  = NULL,
                  last_progress_total = NULL
                WHERE last_status = 'running'
            """))
            r2 = await db.execute(text("""
                UPDATE sync_history SET
                  status        = 'error',
                  ended_at      = NOW(),
                  error_message = 'Sync was interrupted by worker restart'
                WHERE status = 'running'
            """))
            await db.commit()
            if r1.rowcount or r2.rowcount:
                logger.info(
                    "Worker startup: cleared %d sync_config + %d sync_history rows stuck at 'running'",
                    r1.rowcount, r2.rowcount,
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("Worker startup recovery skipped: %s", e)


async def _run_job(project_id: int) -> None:
    """Wrap run_sync in a hard timeout. The sync function already writes
    its own status to the DB on success/error; this layer just enforces
    that a hung HTTP call to CommCare doesn't park the queue forever."""
    try:
        await asyncio.wait_for(run_sync(project_id), timeout=SYNC_JOB_TIMEOUT_SECS)
        logger.info("Sync job for project %s completed", project_id)
    except asyncio.TimeoutError:
        logger.error(
            "Sync job for project %s exceeded %ds timeout — marking errored",
            project_id, SYNC_JOB_TIMEOUT_SECS,
        )
        # Mark the row errored ourselves since run_sync didn't get to.
        async with AsyncSessionLocal() as db:
            try:
                await db.execute(text("""
                    UPDATE sync_config SET
                      last_status = 'error',
                      last_error  = :err,
                      last_progress_step = NULL,
                      last_progress_total = NULL
                    WHERE project_id = :pid AND last_status = 'running'
                """), {"pid": project_id, "err": f"Sync exceeded {SYNC_JOB_TIMEOUT_SECS}s worker timeout"})
                await db.execute(text("""
                    UPDATE sync_history SET
                      status        = 'error',
                      ended_at      = NOW(),
                      error_message = :err
                    WHERE project_id = :pid AND status = 'running'
                """), {"pid": project_id, "err": f"Sync exceeded {SYNC_JOB_TIMEOUT_SECS}s worker timeout"})
                await db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("Failed to mark timed-out job %s as errored", project_id)
    except Exception:  # noqa: BLE001 — log + continue
        logger.exception("Sync job for project %s raised", project_id)


async def _consumer_loop() -> None:
    """BLPOP → dispatch → repeat, until shutdown.

    Single consumer of the queue. Auto-sync jobs and admin-triggered jobs
    flow through the same path — the scheduler just pushes onto the queue
    like the API does.
    """
    logger.info(
        "Worker ready — listening on queue 'sync:queue' (job timeout %ds, poll %ds)",
        SYNC_JOB_TIMEOUT_SECS, BLPOP_TIMEOUT_SECS,
    )
    while not _shutdown.is_set():
        # Run the blocking BLPOP in a thread so the asyncio loop stays
        # responsive to SIGTERM. The Redis sync client returns None on
        # timeout, giving us a regular tick to check for shutdown.
        job: Optional[dict] = await asyncio.to_thread(
            dequeue_sync_job, timeout=BLPOP_TIMEOUT_SECS,
        )
        if job is None:
            continue
        pid = job.get("project_id")
        if not isinstance(pid, int):
            logger.warning("Dropping malformed job: %r", job)
            continue
        logger.info(
            "Picked up sync job for project %s (enqueued %s, source=%s)",
            pid, job.get("enqueued_at"), job.get("source", "manual"),
        )
        await _run_job(pid)
    logger.info("Consumer loop received shutdown signal — exiting cleanly")


async def _scheduler_tick() -> None:
    """One scheduler iteration: for every project with auto-sync enabled,
    enqueue a job if (now - last_synced_at) ≥ interval. Skips when a job
    is already in flight or queued, so back-to-back ticks can't pile up.
    """
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text("""
            SELECT project_id, auto_sync_interval_minutes,
                   last_synced_at, last_status
            FROM sync_config
            WHERE auto_sync_enabled = TRUE
        """))).mappings().all()

    if not rows:
        return

    # If anything is already queued, skip every project for this tick — no
    # point stacking. (Worker is single-consumer; one queued job is one
    # sync about to happen.)
    try:
        depth = await redis_queue_depth()
    except Exception as e:  # noqa: BLE001
        logger.warning("scheduler: couldn't read queue depth (%s) — skipping tick", e)
        return
    if depth > 0:
        logger.debug("scheduler: queue depth=%d, skipping tick", depth)
        return

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    for r in rows:
        if r["last_status"] == "running":
            continue
        interval_min = int(r["auto_sync_interval_minutes"] or 60)
        last_at = r["last_synced_at"]
        if last_at is not None:
            elapsed_min = (now - last_at).total_seconds() / 60.0
            if elapsed_min < interval_min:
                continue
        pid = int(r["project_id"])
        try:
            await enqueue_sync_job(pid, source="auto")
            logger.info(
                "scheduler: enqueued auto-sync for project %s (interval=%dm, last_sync=%s)",
                pid, interval_min, last_at.isoformat() if last_at else "never",
            )
            # Only enqueue one project per tick — the consumer is single-
            # threaded so flooding doesn't help.
            return
        except Exception:  # noqa: BLE001
            logger.exception("scheduler: failed to enqueue auto-sync for project %s", pid)


async def _scheduler_loop() -> None:
    """Auto-sync scheduler. Wakes every SCHEDULER_TICK_SECS, examines
    sync_config rows with auto_sync_enabled=TRUE, and enqueues when due.
    Runs in parallel with the consumer loop; both share the same shutdown
    event.
    """
    logger.info(
        "Scheduler ready — tick=%ds (interval granularity is minutes; tick just decides when to check)",
        SCHEDULER_TICK_SECS,
    )
    while not _shutdown.is_set():
        try:
            await _scheduler_tick()
        except Exception:  # noqa: BLE001
            logger.exception("scheduler tick raised")
        # Sleep in 1-second slices so shutdown is observed within ~1s.
        for _ in range(SCHEDULER_TICK_SECS):
            if _shutdown.is_set():
                break
            await asyncio.sleep(1)
    logger.info("Scheduler loop received shutdown signal — exiting cleanly")


async def _main_async() -> None:
    """Run consumer + scheduler concurrently. Either failing should not take
    the other down — they're independent feedback loops."""
    await _recover_stuck_jobs()
    await asyncio.gather(_consumer_loop(), _scheduler_loop())


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _install_signal_handlers(loop)
    try:
        loop.run_until_complete(_main_async())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
