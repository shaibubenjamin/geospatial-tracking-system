"""Tiny in-process TTL cache for boundary / coverage GeoJSON.

These layers (LGA / ward / settlement, web *and* the app's /api/app/geo/*) are
expensive to generate and change rarely — only on a data reload or sync. The web
settlement layer alone is ~22 MB and tens of seconds, and it was regenerated on
every map load. This caches the *serialized* JSON (already encoded to bytes) so a
request never recomputes within the TTL. The GZip middleware compresses the
bytes on the way out.

Scope safety: the caller's LGA scope is part of the cache key, so an
LGA-restricted user can only ever hit — and be served — their own scope's entry,
never another scope's data.

Prod runs a single uvicorn worker, so a module-level dict is shared across all
requests. The TTL bounds how stale the coverage baked into a layer can be;
``clear()`` is also called after a manual spatial recompute. (A sync-worker
recompute runs in a separate process, so its changes surface on the next TTL
expiry — at most ``TTL_SECONDS`` later.)
"""
import hashlib
import json
import os
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Optional, Tuple

from starlette.requests import Request
from starlette.responses import Response

TTL_SECONDS = int(os.environ.get("BOUNDARY_CACHE_TTL", "300"))
MAX_ENTRIES = int(os.environ.get("BOUNDARY_CACHE_MAX_ENTRIES", "48"))

# key -> (expires_at_monotonic, body_bytes, etag)
_store: "OrderedDict[str, Tuple[float, bytes, str]]" = OrderedDict()


def make_key(layer: str, project_id: int, scope: Optional[set], **filters: Any) -> str:
    """Build a cache key. ``scope`` is the caller's allowed_lgas set (or None for
    no restriction); it is always part of the key so scopes never cross."""
    scope_key = "all" if scope is None else ",".join(sorted(scope))
    parts = [layer, str(project_id), scope_key]
    for k in sorted(filters):
        v = filters[k]
        parts.append(f"{k}={'' if v is None else v}")
    return "|".join(parts)


def get(key: str) -> Optional[Tuple[bytes, str]]:
    entry = _store.get(key)
    if not entry:
        return None
    expires_at, body, etag = entry
    if expires_at < time.monotonic():
        _store.pop(key, None)
        return None
    _store.move_to_end(key)  # LRU touch
    return body, etag


def put(key: str, obj: Any) -> Tuple[bytes, str]:
    body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    etag = 'W/"' + hashlib.md5(body).hexdigest() + '"'  # weak: survives gzip
    _store[key] = (time.monotonic() + TTL_SECONDS, body, etag)
    _store.move_to_end(key)
    while len(_store) > MAX_ENTRIES:
        _store.popitem(last=False)  # evict oldest
    return body, etag


def clear() -> None:
    _store.clear()


async def respond(
    request: Request,
    cache_key: str,
    producer: Callable[[], Awaitable[dict]],
) -> Response:
    """Serve a GeoJSON layer from cache (generating on a miss), with an ETag +
    private Cache-Control so the browser/app caches it too. Honours
    If-None-Match with a 304 (no body). GZip middleware compresses the body."""
    cached = get(cache_key)
    if cached is None:
        data = await producer()
        body, etag = put(cache_key, data)
    else:
        body, etag = cached
    headers = {"Cache-Control": f"private, max-age={TTL_SECONDS}", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)
