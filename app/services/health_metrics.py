"""In-process rolling record of recent 5xx responses / unhandled errors.

Powers the platform health probe (/api/sync/health) so an external monitor
(n8n) can alert on error spikes — the common signal behind every incident we've
had: a slow query blowing the statement_timeout, the DB pool saturating, an
endpoint 500ing, the app cascade. Single uvicorn worker, so a module-level
deque is shared across all requests. Bounded + time-windowed, so memory is
trivial and old errors age out.
"""
import time
from collections import deque
from typing import Any, Deque, Dict, Tuple

WINDOW_SECONDS = int(__import__("os").environ.get("HEALTH_ERROR_WINDOW", "900"))  # 15 min
_MAX = 500  # ring buffer cap; plenty for a 15-min window

# (timestamp, method, path, status)
_events: "Deque[Tuple[float, str, str, int]]" = deque(maxlen=_MAX)


def record_error(method: str, path: str, status: int) -> None:
    """Record a 5xx response / unhandled error. Called from the request
    middleware. Cheap; never raises."""
    try:
        _events.append((time.time(), method or "?", path or "?", int(status)))
    except Exception:
        pass


def summary(window: int = WINDOW_SECONDS) -> Dict[str, Any]:
    """Count + sample of 5xx events in the trailing ``window`` seconds."""
    now = time.time()
    recent = [(t, m, p, s) for (t, m, p, s) in _events if now - t <= window]
    return {
        "count": len(recent),
        "window_minutes": round(window / 60),
        "sample": [
            {"method": m, "path": p, "status": s, "seconds_ago": round(now - t)}
            for (t, m, p, s) in recent[-5:]
        ],
    }
