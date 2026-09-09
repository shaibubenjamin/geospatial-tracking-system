#!/usr/bin/env python3
"""Generate docs/sarmaan-mda-architecture.excalidraw.

The layer stack comes from the ASCII diagram in
.planning/codebase/ARCHITECTURE.md; each layer is FILLED with the technology
and versions from .planning/codebase/STACK.md + INTEGRATIONS.md (not
filenames or route paths).

This platform has TWO flows, so the diagram is read in two directions:
  * the read path flows DOWN   (steps 1-5): browser -> API -> services -> stores
  * the ingest path flows UP   (steps 6-8): CommCare/files -> worker -> stores
The stores sit in the middle where the two meet.

Re-run after editing either doc:  python3 scripts/build-architecture-diagram.py

NOTE ON TEXT WIDTH: Excalidraw draws a text element clipped to the `width`
stored in the file and only re-measures when you edit that element by hand.
An underestimate therefore truncates the label ("SARMAAN MDA" -> "SARMAAN MD").
So widths come from the real Helvetica advance-width table below plus a safety
margin, and every centred label is emitted as a full-width box with
textAlign=center so its position never depends on the estimate at all.
"""
import json
import random

random.seed(20260909)
OUT = "docs/sarmaan-mda-architecture.excalidraw"

# ------------------------------------------------------- palette (indigo)
# Deliberately a different theme from the AMR platform diagram (slate/teal).
TITLE = "#241f3d"      # main title + column headings
LAYER = "#4c3a8c"      # layer band titles + icons (the single accent)
BODY = "#443f5c"       # body lines
MUTED = "#6f6a89"      # secondary / captions
EDGE = "#a09ac0"       # every band + store border, one weight
RULE = "#dad6ea"       # internal divider lines
BG_A = "#f8f7fc"       # alternating band fills separate the layers
BG_B = "#efecf9"
BG_EXT = "#fbfaff"     # external / dashed blocks
BG_INFRA = "#f4f3f8"

# ------------------------------------------------- Helvetica advance widths
# units per 1000 em, from the standard Helvetica AFM. Arial (what browsers
# actually substitute for fontFamily 2) matches these closely enough.
_W = {
    " ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667,
    "'": 191, "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333,
    ".": 278, "/": 278, ":": 278, ";": 278, "<": 584, "=": 584, ">": 584,
    "?": 556, "@": 1015, "[": 278, "\\": 278, "]": 278, "^": 469, "_": 556,
    "`": 333, "{": 334, "|": 260, "}": 334, "~": 584,
    "A": 667, "B": 667, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778,
    "H": 722, "I": 278, "J": 500, "K": 667, "L": 556, "M": 833, "N": 722,
    "O": 778, "P": 667, "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722,
    "V": 667, "W": 944, "X": 667, "Y": 667, "Z": 611,
    "a": 556, "b": 556, "c": 500, "d": 556, "e": 556, "f": 278, "g": 556,
    "h": 556, "i": 222, "j": 222, "k": 500, "l": 222, "m": 833, "n": 556,
    "o": 556, "p": 556, "q": 556, "r": 333, "s": 500, "t": 278, "u": 556,
    "v": 500, "w": 722, "x": 500, "y": 500, "z": 500,
    "·": 278, "→": 1000, "≥": 584, "≤": 584, "×": 584, "–": 556, "—": 1000,
}
for _d in "0123456789":
    _W[_d] = 556


def tw(s, fs):
    """Width of one line, with headroom so Excalidraw can never clip it."""
    return sum(_W.get(c, 600) for c in s) / 1000.0 * fs * 1.08 + 6


# ---------------------------------------------------------------- geometry
X0, W = 40, 1080                 # content rail: x 40 .. 1120
ICOL = (60, 420, 780)            # icon gutter, one per column
COLS = (90, 450, 810)            # text rail (icon gutter + 30), every layer
DIVS = (400, 760)                # column divider x positions
CCX = (220, 580, 940)            # column centres (arrow anchors)
TITLE_ROW = 56                   # band top -> header divider (title + note)
PAD = 18                         # content bottom -> band bottom
GAP = 30                         # band -> band arrow gap

els = []
_seq = [0]


def nonce():
    return random.randint(10**8, 2 * 10**9)


def eid():
    _seq[0] += 1
    return f"n{_seq[0]}"


BASE = dict(angle=0, fillStyle="solid", strokeStyle="solid", roughness=0,
            opacity=100, groupIds=[], frameId=None, isDeleted=False,
            boundElements=[], updated=1, link=None, locked=False)


def box(x, y, w, h, bg="transparent", dashed=False):
    els.append(dict(BASE, id=eid(), type="rectangle", x=x, y=y,
                    width=w, height=h, strokeColor=EDGE, backgroundColor=bg,
                    strokeWidth=1, strokeStyle="dashed" if dashed else "solid",
                    roundness=None, seed=nonce(), version=1,
                    versionNonce=nonce()))


def seg(x, y, dx, dy, color=RULE, sw=1, arrow=False, up=False):
    """A line or arrow. `up` draws bottom-to-top so the head lands at `y`."""
    pts = [[0, abs(dy)], [0, 0]] if up else [[0, 0], [dx, dy]]
    els.append(dict(BASE, id=eid(), type="arrow" if arrow else "line",
                    x=x, y=y, width=abs(dx), height=abs(dy),
                    strokeColor=color, backgroundColor="transparent",
                    strokeWidth=sw, roundness=None, seed=nonce(), version=1,
                    versionNonce=nonce(), points=pts,
                    lastCommittedPoint=None, startBinding=None, endBinding=None,
                    startArrowhead=None, endArrowhead="arrow" if arrow else None))


def _text(x, y, s, fs, color, width, align):
    els.append(dict(BASE, id=eid(), type="text", x=round(x), y=y,
                    width=round(width),
                    height=round(len(s.split("\n")) * fs * 1.25),
                    strokeColor=color, backgroundColor="transparent",
                    strokeWidth=1, roundness=None, seed=nonce(), version=1,
                    versionNonce=nonce(), text=s, fontSize=fs, fontFamily=2,
                    textAlign=align, verticalAlign="top", containerId=None,
                    originalText=s, lineHeight=1.25))


def txt(x, y, s, fs=11, color=BODY):
    """Left-aligned label; box is sized from real metrics + headroom."""
    _text(x, y, s, fs, color, max(tw(l, fs) for l in s.split("\n")), "left")


def ctxt(y, s, fs, color, x=X0, w=W):
    """Centred label: full-width box + textAlign centre, so it cannot drift."""
    _text(x, y, s, fs, color, w, "center")


# ---------------------------------------------------------------- icons
# 20x20 line glyphs from excalidraw primitives, one per component.
def _r(x, y, w, h, fill="transparent"):
    els.append(dict(BASE, id=eid(), type="rectangle", x=x, y=y, width=w,
                    height=h, strokeColor=LAYER, backgroundColor=fill,
                    strokeWidth=1, roundness=None, seed=nonce(), version=1,
                    versionNonce=nonce()))


def _e(x, y, w, h, fill="transparent"):
    els.append(dict(BASE, id=eid(), type="ellipse", x=x, y=y, width=w, height=h,
                    strokeColor=LAYER, backgroundColor=fill, strokeWidth=1,
                    roundness=None, seed=nonce(), version=1, versionNonce=nonce()))


def _l(x, y, dx, dy):
    seg(x, y, dx, dy, LAYER, 1)


def ic_browser(x, y):          # web dashboard
    _r(x, y + 2, 20, 16)
    _l(x, y + 7, 20, 0)
    _e(x + 2, y + 3.5, 2.5, 2.5, LAYER)
    _e(x + 6, y + 3.5, 2.5, 2.5, LAYER)


def ic_map(x, y):              # map views
    _r(x + 1, y + 3, 18, 14)
    _l(x + 7, y + 3, 0, 14)
    _l(x + 13, y + 3, 0, 14)
    _e(x + 8, y + 7, 4, 4, LAYER)


def ic_phone(x, y):            # android app
    _r(x + 5, y, 11, 20)
    _l(x + 8, y + 17, 5, 0)


def ic_hub(x, y):              # routing
    _e(x + 7, y + 7, 6, 6)
    _l(x + 10, y + 1, 0, 6)
    _l(x + 10, y + 13, 0, 6)
    _l(x + 1, y + 10, 6, 0)
    _l(x + 13, y + 10, 6, 0)


def ic_shield(x, y):           # guards
    _r(x + 3, y + 1, 14, 10)
    _l(x + 3, y + 11, 7, 8)
    _l(x + 17, y + 11, -7, 8)
    _l(x + 7, y + 6, 3, 3)
    _l(x + 10, y + 9, 4, -5)


def ic_bolt(x, y):             # caching (fast path)
    _l(x + 12, y, -7, 11)
    _l(x + 5, y + 11, 5, 0)
    _l(x + 8, y + 20, 7, -11)
    _l(x + 15, y + 9, -5, 0)


def ic_chart(x, y):            # aggregation
    _r(x + 2, y + 12, 4, 8)
    _r(x + 8, y + 7, 4, 13)
    _r(x + 14, y + 3, 4, 17)


def ic_pin(x, y):              # spatial
    _e(x + 4, y + 1, 12, 12)
    _e(x + 8, y + 5, 4, 4, LAYER)
    _l(x + 6, y + 11, 4, 8)
    _l(x + 14, y + 11, -4, 8)


def ic_flag(x, y):             # quality control
    _l(x + 4, y, 0, 20)
    _r(x + 4, y + 2, 12, 8)


def ic_sync(x, y):             # sync / ingestion
    _e(x + 2, y + 2, 16, 16)
    _l(x + 11, y, 4, 3)
    _l(x + 15, y + 3, -4, 3)


def ic_layers(x, y):           # ORM / models
    _r(x + 2, y + 3, 16, 4)
    _r(x + 2, y + 9, 16, 4)
    _r(x + 2, y + 15, 16, 4)


def ic_plug(x, y):             # drivers
    _r(x + 5, y + 6, 10, 10)
    _l(x + 8, y + 1, 0, 5)
    _l(x + 12, y + 1, 0, 5)
    _l(x + 10, y + 16, 0, 4)


def ic_lock(x, y):             # config & secrets
    _r(x + 3, y + 8, 14, 12)
    _e(x + 6, y + 1, 8, 10)


def ic_db(x, y):               # postgres
    _e(x + 1, y + 1, 18, 6)
    _e(x + 1, y + 7, 18, 6)
    _e(x + 1, y + 13, 18, 6)


def ic_queue(x, y):            # redis queue
    _r(x + 1, y + 4, 5, 12)
    _r(x + 8, y + 4, 5, 12)
    _r(x + 15, y + 4, 4, 12)


def ic_worker(x, y):           # sync worker
    _e(x + 4, y + 4, 12, 12)
    _e(x + 8, y + 8, 4, 4)
    _r(x + 9, y, 2, 4)
    _r(x + 9, y + 16, 2, 4)
    _r(x, y + 9, 4, 2)
    _r(x + 16, y + 9, 4, 2)


def ic_clock(x, y):            # scheduler
    _e(x + 1, y + 1, 18, 18)
    _l(x + 10, y + 5, 0, 5)
    _l(x + 10, y + 10, 4, 0)


def ic_upload(x, y):           # manual uploads
    _r(x + 1, y + 13, 18, 6)
    _l(x + 10, y + 11, 0, -10)
    _l(x + 6, y + 5, 4, -4)
    _l(x + 14, y + 5, -4, -4)


def ic_cloud_ext(x, y):        # commcare HQ (external service)
    _e(x + 1, y + 1, 18, 18)
    _l(x + 1, y + 10, 18, 0)
    _e(x + 6, y + 1, 8, 18)


def ic_forms(x, y, fill=BG_EXT):   # other survey platforms
    _r(x + 5, y, 13, 17)
    _r(x + 1, y + 3, 13, 17, fill)
    for i in range(3):
        _l(x + 4, y + 8 + i * 4, 7, 0)


def ic_file(x, y):             # file drops
    _r(x + 4, y, 12, 20)
    _l(x + 7, y + 6, 6, 0)
    _l(x + 7, y + 11, 6, 0)
    _l(x + 7, y + 16, 4, 0)


def ic_cube(x, y):             # container runtime
    _r(x + 2, y + 4, 16, 15)
    _l(x + 2, y + 9, 16, 0)
    _l(x + 10, y + 4, 0, 5)


def ic_rack(x, y):             # AWS platform
    _r(x + 1, y + 2, 18, 5)
    _r(x + 1, y + 9, 18, 5)
    _r(x + 1, y + 16, 18, 4)
    _e(x + 3, y + 3.5, 2, 2, LAYER)
    _e(x + 3, y + 10.5, 2, 2, LAYER)


def ic_pipeline(x, y):         # delivery / CI
    _e(x + 1, y + 7, 6, 6)
    _e(x + 13, y + 7, 6, 6)
    _l(x + 7, y + 10, 6, 0)


# ================================================================ header
txt(X0, 36, "SARMAAN MDA", 30, TITLE)
txt(X0, 80, "Architecture · technology stack by layer", 15, BODY)
txt(X0, 106, "Python 3.11 · JavaScript / HTML / CSS · Kotlin · SQL (PostGIS) "
             "· HCL", 11, BODY)
txt(X0, 126, "Read path flows DOWN (1-5) · ingest path flows UP (6-8) · "
             "the stores sit where they meet", 10, MUTED)
txt(X0, 144, "Generated from .planning/codebase/ARCHITECTURE.md + STACK.md · "
             "2026-09-09", 10, MUTED)


def band(y, title, note, rows, bg=BG_A, dashed=False):
    """One layer band.

    title/note give the architecture layer + a brief explanation of it.
    `rows` = (icon, heading, what-it-does, *stack lines) per column, so each
    column carries the component, its explanation, and its technology.
    """
    top = y
    ty = top + TITLE_ROW
    head_y = ty + 12
    line_ys = [head_y + 26 + 16 * i for i in range(max(len(r) for r in rows) - 2)]
    bottom = (line_ys[-1] if line_ys else head_y + 14) + 14 + PAD
    box(X0, top, W, bottom - top, bg, dashed)
    ctxt(top + 10, title, 15, LAYER)
    ctxt(top + 32, note, 11, MUTED)
    seg(X0, ty, W, 0)
    for dx in DIVS:
        seg(dx, ty, 0, bottom - ty)
    for ix, cx, row in zip(ICOL, COLS, rows):
        row[0](ix, head_y - 2)
        txt(cx, head_y, row[1], 13, TITLE)
        for i, ln in enumerate(row[2:]):
            if ln:                       # blank entries pad a short column
                txt(cx, line_ys[i], ln, 11, BODY if i == 0 else MUTED)
    return bottom


def flow(y, xs, step, label, lx=None, up=False):
    """A numbered flow hop between two layers, with a brief explanation."""
    for x in xs:
        seg(x, y, 0, GAP, MUTED, 1.5, arrow=True, up=up)
    txt((lx or xs[len(xs) // 2]) + 16, y + 4, f"{step} · {label}", 10, MUTED)
    return y + GAP


# ==================================== read path, flowing down =============
y = band(188, "Static Frontend Layer",
         "dashboards and field views · every call carries a bearer token", [
    (ic_browser, "Web Dashboard", "campaign KPIs and coverage maps",
     "MapLibre GL JS 3.6.2", "Chart.js 4.4.1 + datalabels",
     "Inter · Font Awesome 6.5.0"),
    (ic_map, "Mobile Web Views", "field map + dashboard in a WebView",
     "MapLibre GL JS (blob: workers)", "HTML + CSS + JS, no framework",
     "served directly by FastAPI"),
    (ic_phone, "Android App", "native shell around the web views",
     "Kotlin · Jetpack Compose", "Retrofit 2.11.0 · Moshi 1.15.1",
     "version-gated (426 upgrade)"),
], bg=BG_A)
y = flow(y, CCX, 1, "HTTPS request with a JWT, scoped to the user's states/LGAs")

y = band(y, "FastAPI Application Layer",
         "one async uvicorn process · 10 route modules by domain", [
    (ic_hub, "Routing & schemas", "endpoint handlers and validation",
     "FastAPI 0.111.0", "Uvicorn 0.29.0 (port 8080)",
     "pydantic request/response"),
    (ic_shield, "Guards & middleware", "auth, scope, throttle, version gate",
     "pyjwt 2.8.1 · python-jose", "bcrypt 4.2.1",
     "slowapi 0.1.9 (120 req/min)"),
    (ic_bolt, "Caching", "TTL cache for expensive aggregates",
     "geo_cache (in-process)", "Redis-backed response cache",
     "flushed after every sync"),
], bg=BG_B)
y = flow(y, CCX, 2, "the route handler calls a stateless service")

y = band(y, "Service Layer",
         "12 stateless async modules · business logic and integrations", [
    (ic_chart, "Aggregation", "LGA / ward / settlement rollups",
     "pandas 2.2.2 · numpy 1.26.4", "pre-computed settlement analytics",
     "recomputed after each sync"),
    (ic_pin, "Spatial engine", "coverage, geometry, grid visits",
     "GeoAlchemy2 0.15.1", "shapely 2.0.4 · pyproj 3.6.1",
     "PostGIS ST_Intersects / ST_DWithin"),
    (ic_flag, "Quality control", "GPS, duplicate and fast-form flags",
     "SQL window functions", "operator-tuned thresholds",
     "flags stored on the household row"),
], bg=BG_A)
y = flow(y, CCX, 3, "services open an async session and query PostGIS")

y = band(y, "Data Access Layer",
         "async SQLAlchemy · the only tier that touches Postgres and Redis", [
    (ic_layers, "Async ORM", "models, sessions, transactions",
     "SQLAlchemy 2.0.30 (asyncio)", "pool 10 + 20 overflow",
     "45s statement timeout"),
    (ic_plug, "Drivers", "async for the API, sync for the worker",
     "asyncpg 0.29.0 (API)", "psycopg2-binary 2.9.9 (worker)",
     "all geometry in EPSG:4326"),
    (ic_lock, "Migrations & config", "schema and environment",
     "Alembic 1.13.1", "python-dotenv 1.0.1",
     "AWS Secrets Manager (prod)"),
], bg=BG_B)

# ==================================== the stores, where both paths meet ===
flow(y, [360], 4, "reads and writes over the pool")
flow(y, [950], 5, "job queue and cache", lx=950)
sy = y + GAP

STORE_H = 162
box(X0, sy, 640, STORE_H, "#ffffff")
ic_db(ICOL[0], sy + 15)
txt(COLS[0], sy + 14, "PostgreSQL + PostGIS", 15, TITLE)
txt(COLS[0], sy + 40, "engine · PostgreSQL 14+ with PostGIS", 11, MUTED)
for i, s in enumerate([
    "AWS RDS · encrypted · automated backups",
    "boundaries · lgas, wards, settlements, grids",
    "MDA data · households, individuals, baseline",
    "analytics · settlement_analytics rollups",
    "sync metadata · config, history, watermarks",
]):
    txt(COLS[0], sy + 66 + i * 16, s, 11, BODY)

box(780, sy, 340, STORE_H, "#ffffff")
ic_queue(ICOL[2], sy + 15)
txt(COLS[2], sy + 14, "Redis", 15, TITLE)
txt(COLS[2], sy + 40, "engine · Redis 7-alpine", 11, MUTED)
for i, s in enumerate([
    "sync job queue (BLPOP)",
    "TTL cache for aggregates",
    "redis 5.0.7 · sync + async",
    "pending sync signals",
]):
    txt(COLS[2], sy + 66 + i * 16, s, 11, BODY)

# ==================================== ingest path, flowing up =============
y = sy + STORE_H + GAP
flow(y - GAP, [360], 6, "worker writes rows, then recomputes analytics", up=True)
flow(y - GAP, [950], 7, "dequeue · cache flush", lx=950, up=True)

y = band(y, "Background Worker & Ingestion",
         "a separate container on the same image · decoupled from the API", [
    (ic_worker, "Sync Worker", "long-running CommCare pulls",
     "Redis BLPOP loop (5s)", "30 min job cap · retry",
     "graceful shutdown"),
    (ic_clock, "Auto-scheduler", "enqueues syncs that are due",
     "60-second tick", "per-project interval",
     "honours cancel requests"),
    (ic_upload, "Manual uploads", "operator-driven imports",
     "openpyxl 3.1.2 · pyshp 2.3.1", "python-multipart 0.0.9",
     "aiofiles 23.2.1"),
], bg=BG_A)

y = flow(y, [220, 940], 8,
         "watermarked pull · only rows modified since the last run", lx=220,
         up=True)

y = band(y, "Field Data Sources",
     "external systems · CommCare HQ is the primary feed", [
    (ic_cloud_ext, "CommCare HQ", "primary MDA form source",
     "OData feed over httpx 0.27.0", "Basic auth, Fernet-encrypted",
     "incremental by watermark"),
    (ic_forms, "Other platforms", "secondary and historical feeds",
     "Kobo · ODK · DHIS2", "SurveyCTO · Google Sheets",
     "imported rather than polled"),
    (ic_file, "File drops", "operator-supplied files",
     "CSV · Excel (.xlsx)", "Shapefile boundary imports",
     "stored on the EBS upload volume"),
], bg=BG_EXT, dashed=True)

# ==================================== runtime, outside both paths =========
# deliberately NOT wired into either flow: it is where the stack runs, not a
# tier that requests or data pass through.
band(y + 40, "Runtime & Infrastructure",
     "where the stack runs · not a tier requests pass through", [
    (ic_cube, "Container runtime", "one image, three services",
     "Docker · python:3.11-slim", "Compose · api, redis, sync_worker",
     "ECR registry · tagged per commit"),
    (ic_rack, "Compute & edge", "single instance behind a balancer",
     "EC2 us-east-1 · ALB", "EBS upload volume · S3 for the APK",
     "RDS PostgreSQL with PostGIS"),
    (ic_pipeline, "Delivery & ops", "OIDC deploys, no stored keys",
     "GitHub Actions · SSM Run Command", "Terraform · Secrets Manager",
     "CloudWatch logs · Sentry 2.18.0"),
], bg=BG_INFRA)

doc = {"type": "excalidraw", "version": 2, "source": "https://excalidraw.com",
       "elements": els,
       "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"},
       "files": {}}
with open(OUT, "w") as f:
    json.dump(doc, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(f"wrote {OUT}: {len(els)} elements")
