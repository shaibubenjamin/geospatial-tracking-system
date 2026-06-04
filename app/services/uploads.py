"""
Shared upload-validation helpers.

Bound size + extension on every multipart upload endpoint so a malicious
or accidental large file can't exhaust memory/disk, and only the file
types we actually process are accepted.
"""
from __future__ import annotations

import pathlib

from fastapi import HTTPException, UploadFile


# Per-extension whitelist. Generous on purpose — boundary bundles can be
# ZIPs containing shapefiles; CSVs and Excel for MLOS/baseline; GeoJSON
# for boundary-by-layer endpoints.
ALLOWED_EXTENSIONS: set[str] = {
    ".geojson", ".json",
    ".zip",
    ".csv", ".xlsx", ".xls",
    ".shp", ".dbf", ".shx", ".prj", ".cpg",
}

# Hard ceiling for a single multipart upload. Boundary bundles are usually
# under 30 MB; 100 MB leaves headroom without exposing us to abuse.
MAX_UPLOAD_BYTES: int = 100 * 1024 * 1024


def validate_upload(
    file: UploadFile,
    *,
    allowed: set[str] | None = None,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> str:
    """Validate the filename extension and report the sanitised extension.

    Size is enforced at read-time by the caller (see read_with_cap()).
    Raises HTTPException 400 on bad extension, 413 on size at read-time.
    """
    allowed = allowed or ALLOWED_EXTENSIONS
    name = file.filename or ""
    ext = pathlib.Path(name).suffix.lower()
    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: '{ext or '(none)'}'. "
                   f"Allowed: {sorted(allowed)}",
        )
    return ext


async def read_with_cap(file: UploadFile, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
    """Read up to max_bytes + 1 from the upload and 413 if it overflows."""
    blob = await file.read(max_bytes + 1)
    if len(blob) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {max_bytes // (1024 * 1024)} MB).",
        )
    return blob
