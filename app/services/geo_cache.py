"""Tiny in-process TTL cache for boundary GeoJSON.

The boundary layers (LGA / ward / settlement) are large and change rarely — only
on a data reload or sync. The settlement layer alone is ~22 MB and tens of
seconds to generate + serialize, and it was being recomputed on every single map
load. This caches the *serialized* JSON (already encoded to bytes) so a request
never recomputes within the TTL.

Scope safety: the caller's LGA scope is part of the cache key, so an
LGA-restricted user can only ever hit — and be served — their own scope's entry,
never another scope's data.

Prod runs a single uvicorn worker, so a module-level dict is shared across all
requests. The TTL bounds how stale the coverage stats baked into a layer can be;
``clear()`` is also called after a manual spatial recompute for instant
freshness. (A sync-worker recompute runs in a separate process, so its changes
surface on the next TTL expiry — at most ``TTL_SECONDS`` later.)
"""
import hashlib
import json
import os
import time
from collections import OrderedDict
from typing import Any, Optional, Tuple

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
