"""Minimal Redis-backed job queue for long-running background work.

Why: CommCare syncs run for 5-15 minutes per project. When the API container
hot-reloads (dev) or restarts (deploy / OOM / EC2 cycle), any in-flight
``BackgroundTasks`` job is killed mid-loop, leaving a stuck ``running`` row
in ``sync_history`` and forcing the user to retry. By moving the work into a
separate worker process listening on a Redis list, API restarts no longer
touch in-flight syncs.

Design intentionally tiny — one Redis list per queue name, ``RPUSH`` to
enqueue, ``BLPOP`` to dequeue. We don't need ack semantics, retry policies
or priority queues yet; the sync code already records its own progress to
``sync_history`` / ``sync_config``, so observability is unchanged.

Two clients live side-by-side:
  * ``get_async_redis()`` — used by FastAPI handlers to enqueue.
  * ``get_sync_redis()``  — used by ``app/sync_worker.py`` for the blocking
    BLPOP loop (the worker runs in its own event loop and the blocking
    client is simpler).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import redis
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Defaults map to docker-compose service names. Production override via env.
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
SYNC_QUEUE_NAME = "sync:queue"

_async_client: Optional[aioredis.Redis] = None
_sync_client:  Optional[redis.Redis]   = None


def get_async_redis() -> aioredis.Redis:
    """Lazy-singleton async client. Safe to call from FastAPI handlers."""
    global _async_client
    if _async_client is None:
        _async_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _async_client


def get_sync_redis() -> redis.Redis:
    """Lazy-singleton sync client. Used by the worker's BLPOP loop."""
    global _sync_client
    if _sync_client is None:
        _sync_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _sync_client


async def enqueue_sync_job(
    project_id: int,
    *,
    queue: str = SYNC_QUEUE_NAME,
    source: str = "manual",
) -> dict:
    """Push a CommCare sync job onto the queue. Returns the job payload.

    The worker reads this payload and calls ``run_sync(project_id)``. The job
    body is intentionally minimal — the sync function already pulls its own
    config (credentials, form list, watermark) from the database.

    ``source`` is logged by the worker on pickup so auto-sync runs can be
    distinguished from admin-triggered ones in the container logs. Default
    is ``"manual"``; the scheduler passes ``"auto"``.
    """
    job = {
        "project_id":  int(project_id),
        "enqueued_at": datetime.now(timezone.utc).isoformat(),
        "source":      source,
    }
    client = get_async_redis()
    await client.rpush(queue, json.dumps(job))
    qlen = await client.llen(queue)
    logger.info(
        "Enqueued sync job for project %s (source=%s) — queue depth now %d",
        project_id, source, qlen,
    )
    return {**job, "queue": queue, "queue_depth": int(qlen)}


def dequeue_sync_job(*, queue: str = SYNC_QUEUE_NAME, timeout: int = 5) -> Optional[dict]:
    """Blocking dequeue used by the worker. Returns None on idle-timeout.

    The timeout lets the worker periodically check shutdown signals / health
    instead of blocking forever inside Redis.
    """
    client = get_sync_redis()
    item = client.blpop([queue], timeout=timeout)
    if item is None:
        return None
    _q, payload = item
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        logger.exception("Malformed job dropped from %s: %r", queue, payload)
        return None


async def queue_depth(*, queue: str = SYNC_QUEUE_NAME) -> int:
    client = get_async_redis()
    return int(await client.llen(queue))


async def health_check() -> bool:
    """Returns True iff Redis is reachable. Used by /api/sync/run preflight
    so we fail fast with a useful 503 instead of silently dropping the job."""
    try:
        client = get_async_redis()
        return bool(await client.ping())
    except Exception as e:  # noqa: BLE001 — surface the error to caller
        logger.warning("Redis health-check failed: %s", e)
        return False
