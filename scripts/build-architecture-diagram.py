#!/usr/bin/env python3
"""Generate docs/sarmaan-mda-architecture.excalidraw.

The diagram is a DATA-FLOW STACK, read top to bottom in the direction the data
actually travels:

    field sources -> ingestion -> stores -> data access -> services -> API
    -> web + Android clients

Each band is one stage of that flow: a numbered badge, the stage title, a
one-line explanation, and colour-coded cards. Every card carries a 20x20 icon,
the component name, what it does, and its technology WITH VERSIONS from
.planning/codebase/STACK.md + INTEGRATIONS.md - never filenames or route paths.
A legend at the top maps each colour to a kind of stage.

Runtime & Infrastructure is drawn last and is deliberately NOT wired into the
flow: it is where the stack runs, not a stage the data passes through.

Re-run after editing either doc:  python3 scripts/build-architecture-diagram.py

NOTE ON TEXT WIDTH: Excalidraw draws a text element clipped to the `width`
stored in the file and only re-measures when you edit that element by hand.
An underestimate therefore truncates the label ("SARMAAN MDA" -> "SARMAAN MD").
So widths come from the real Helvetica advance-width table below plus a safety
margin, every centred label is emitted as a full-width box with
textAlign=center, and the build fails loudly if any line overflows its card.
"""
import json
import random
import sys

random.seed(20260910)
OUT = "docs/sarmaan-mda-architecture.excalidraw"

# ------------------------------------------------------------- base palette
TITLE = "#241f3d"      # main title + card headings
BODY = "#443f5c"       # body / stack lines
MUTED = "#6f6a89"      # captions, notes, flow labels
PILL_BG = "#f1f0f7"    # flow-label pill fill
PILL_EDGE = "#dad6ea"
PAGE_EDGE = "#c9c4de"  # band border when a band has no accent of its own

# Per-stage accents: (line/heading colour, card fill). The legend below maps
# each of these to the kind of stage it marks.
A_SOURCE = ("#b91c1c", "#fee2e2")   # external systems, outside our control
A_INGEST = ("#b45309", "#fef3c7")   # ingestion + scheduling
A_STORE = ("#1e293b", "#e6ebf2")    # durable stores (source of truth)
A_DATA = ("#6b21a8", "#f3e8ff")     # data access tier
A_SVC = ("#0e7490", "#cffafe")      # business logic
A_API = ("#047857", "#d1fae5")      # HTTP surface
A_CLIENT = ("#86198f", "#fae8ff")   # what people actually look at
A_INFRA = ("#3730a3", "#eef2ff")    # where it all runs (off-flow)

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
X0, W = 40, 1140                 # content rail: x 40 .. 1180
PADX = 18                        # band edge -> card
GAPC = 14                        # card -> card
HEAD = 54                        # band top -> first card
FOOT = 18                        # last card -> band bottom
HOP = 56                         # band -> band, holds the pill + arrow

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


def box(x, y, w, h, bg="transparent", stroke=PAGE_EDGE, dashed=False,
        sw=1, round_=True):
    els.append(dict(BASE, id=eid(), type="rectangle", x=x, y=y,
                    width=w, height=h, strokeColor=stroke, backgroundColor=bg,
                    strokeWidth=sw, strokeStyle="dashed" if dashed else "solid",
                    roundness={"type": 3} if round_ else None,
                    seed=nonce(), version=1, versionNonce=nonce()))


def seg(x, y, dx, dy, color=PILL_EDGE, sw=1, arrow=False):
    els.append(dict(BASE, id=eid(), type="arrow" if arrow else "line",
                    x=x, y=y, width=abs(dx), height=abs(dy),
                    strokeColor=color, backgroundColor="transparent",
                    strokeWidth=sw, roundness=None, seed=nonce(), version=1,
                    versionNonce=nonce(), points=[[0, 0], [dx, dy]],
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
    _text(x, y, s, fs, color, max(tw(ln, fs) for ln in s.split("\n")), "left")


def ctxt(y, s, fs, color, x=X0, w=W):
    """Centred label: full-width box + textAlign centre, so it cannot drift."""
    _text(x, y, s, fs, color, w, "center")


# ---------------------------------------------------------------- icons
# 20x20 line glyphs from excalidraw primitives, one per component. They are
# drawn in the accent colour of whichever band is being emitted.
_ACC = [TITLE]


def _r(x, y, w, h, fill="transparent"):
    els.append(dict(BASE, id=eid(), type="rectangle", x=x, y=y, width=w,
                    height=h, strokeColor=_ACC[0], backgroundColor=fill,
                    strokeWidth=1, roundness=None, seed=nonce(), version=1,
                    versionNonce=nonce()))


def _e(x, y, w, h, fill="transparent"):
    els.append(dict(BASE, id=eid(), type="ellipse", x=x, y=y, width=w, height=h,
                    strokeColor=_ACC[0], backgroundColor=fill, strokeWidth=1,
                    roundness=None, seed=nonce(), version=1, versionNonce=nonce()))


def _l(x, y, dx, dy):
    seg(x, y, dx, dy, _ACC[0], 1)


def ic_browser(x, y):          # web dashboard
    _r(x, y + 2, 20, 16)
    _l(x, y + 7, 20, 0)
    _e(x + 2, y + 3.5, 2.5, 2.5, _ACC[0])
    _e(x + 6, y + 3.5, 2.5, 2.5, _ACC[0])


def ic_map(x, y):              # map views
    _r(x + 1, y + 3, 18, 14)
    _l(x + 7, y + 3, 0, 14)
    _l(x + 13, y + 3, 0, 14)
    _e(x + 8, y + 7, 4, 4, _ACC[0])


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
    _e(x + 8, y + 5, 4, 4, _ACC[0])
    _l(x + 6, y + 11, 4, 8)
    _l(x + 14, y + 11, -4, 8)


def ic_flag(x, y):             # quality control
    _l(x + 4, y, 0, 20)
    _r(x + 4, y + 2, 12, 8)


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


def ic_forms(x, y):            # other survey platforms
    _r(x + 5, y, 13, 17)
    _r(x + 1, y + 3, 13, 17, "#ffffff")
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
    _e(x + 3, y + 3.5, 2, 2, _ACC[0])
    _e(x + 3, y + 10.5, 2, 2, _ACC[0])


def ic_pipeline(x, y):         # delivery / CI
    _e(x + 1, y + 7, 6, 6)
    _e(x + 13, y + 7, 6, 6)
    _l(x + 7, y + 10, 6, 0)


# ------------------------------------------------------------- band drawing
_overflow = []


def _fits(line, fs, avail, where):
    if tw(line, fs) > avail:
        _overflow.append(f"{where}: {line!r} needs {tw(line, fs):.0f}px "
                         f"of {avail:.0f}px")


def band(y, num, title, note, cards, accent, dashed=False, weights=None,
         flow=True):
    """One stage of the flow.

    `cards` = (icon, heading, what-it-does, *stack lines). The band is sized
    from its content, so no stage carries dead space.
    """
    acc, tint = accent
    _ACC[0] = acc
    n = len(cards)
    weights = weights or [1] * n
    lines = max(len(c) - 3 for c in cards)
    card_h = 52 + lines * 16 + 12
    height = HEAD + card_h + FOOT

    box(X0, y, W, height, "#ffffff", PAGE_EDGE, dashed)
    seg(X0 + 2, y + 2, W - 4, 0, acc, 4)              # accent rule on top
    tx = X0 + PADX
    if num is not None:                               # off-flow bands get none
        box(tx, y + 17, 24, 22, acc, acc, sw=1)       # number badge
        ctxt(y + 21, str(num), 13, "#ffffff", x=tx, w=24)
        tx += 36
    txt(tx, y + 19, title, 16, acc)
    if note:
        txt(tx + 8 + tw(title, 16), y + 23, note, 11, MUTED)

    avail = W - 2 * PADX - (n - 1) * GAPC
    unit = avail / sum(weights)
    cx = X0 + PADX
    for card, wt in zip(cards, weights):
        cw = unit * wt
        icon, head, desc = card[0], card[1], card[2]
        box(cx, y + HEAD, cw, card_h, tint, acc)
        icon(cx + 12, y + HEAD + 11)
        inner = cw - 24
        _fits(head, 13, cw - 52, f"{title}/{head}")
        _fits(desc, 10.5, inner, f"{title}/{head}")
        txt(cx + 40, y + HEAD + 10, head, 13, acc)
        txt(cx + 12, y + HEAD + 34, desc, 10.5, MUTED)
        for i, ln in enumerate(card[3:]):
            if not ln:
                continue
            _fits(ln, 11, inner, f"{title}/{head}")
            txt(cx + 12, y + HEAD + 54 + i * 16, ln, 11, BODY)
        cx += cw + GAPC

    bottom = y + height
    if not flow:
        return bottom
    return bottom


def hop(y, step, label):
    """A numbered flow hop drawn between two bands: pill label + arrow."""
    text = f"{step} · {label}"
    pw = tw(text, 11) + 26
    px = X0 + (W - pw) / 2
    box(px, y + 6, pw, 22, PILL_BG, PILL_EDGE)
    ctxt(y + 11, text, 11, MUTED, x=px, w=pw)
    seg(X0 + W / 2, y + 34, 0, 16, MUTED, 1.5, arrow=True)
    return y + HOP


# ================================================================ header
txt(X0, 34, "SARMAAN MDA", 30, TITLE)
txt(X0, 78, "Architecture · data flow and technology stack, stage by stage",
    15, BODY)
txt(X0, 102, "Field sources → ingestion → stores → data access → services "
             "→ API → web + Android clients", 12, BODY)
txt(X0, 124, "Python 3.11 · JavaScript / HTML / CSS · Kotlin · SQL (PostGIS) "
             "· HCL", 11, MUTED)
txt(X0, 142, "Generated by scripts/build-architecture-diagram.py from "
             ".planning/codebase/ARCHITECTURE.md + STACK.md · 2026-09-10",
    10, MUTED)

# ---------------------------------------------------------------- legend
LEG_Y = 168
box(X0, LEG_Y, W, 56, "#fbfaff", PILL_EDGE)
_legend = [
    (A_SOURCE, "external source"),
    (A_INGEST, "ingestion & scheduling"),
    (A_STORE, "data store · source of truth"),
    (A_DATA, "data access tier"),
    (A_SVC, "service logic"),
    (A_API, "API surface"),
    (A_CLIENT, "client / frontend"),
    (A_INFRA, "runtime (not in the flow)"),
]
# every entry sits on the same 5-column rail, so the row reads as one even
# list instead of a packed left half and two notes stranded on the right.
_LEG_COLS = 5
_leg_w = (W - 2 * PADX) / _LEG_COLS
_entries = [(a, lbl) for a, lbl in _legend] + [("arrow", "numbered flow hop"),
                                              ("dash", "dashed · off the flow")]
for i, (mark, lbl) in enumerate(_entries):
    lx = X0 + PADX + (i % _LEG_COLS) * _leg_w
    ly = LEG_Y + 12 + (i // _LEG_COLS) * 20
    if mark == "arrow":
        seg(lx, ly + 6, 14, 0, MUTED, 1.5, arrow=True)
    elif mark == "dash":
        seg(lx, ly + 6, 14, 0, MUTED, 1.5)
        els[-1]["strokeStyle"] = "dashed"
    else:
        acc, tint = mark
        box(lx, ly, 12, 12, tint, acc, round_=False)
    txt(lx + 19, ly, lbl, 10.5, BODY)
    _fits(lbl, 10.5, _leg_w - 24, "legend")

# ============================================ 1. where the data comes from
y = 250
y = band(y, 1, "Field Data Sources",
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
], A_SOURCE, dashed=True)
y = hop(y, "1", "watermarked pull · only rows modified since the last run")

# ============================================ 2. how it gets in
y = band(y, 2, "Ingestion & Scheduling",
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
], A_INGEST)
y = hop(y, "2", "worker writes rows, then recomputes settlement analytics")

# ============================================ 3. where it lands
y = band(y, 3, "Data Stores",
         "PostgreSQL is the source of truth · Redis carries queue and cache", [
    (ic_db, "PostgreSQL + PostGIS", "every row and every geometry",
     "PostgreSQL 14+ with PostGIS", "AWS RDS · encrypted · backups",
     "boundaries · lgas, wards, settlements, grids",
     "MDA data · households, individuals, baseline",
     "analytics · settlement_analytics rollups",
     "sync metadata · config, history, watermarks"),
    (ic_queue, "Redis", "queue and hot reads",
     "Redis 7-alpine · redis 5.0.7", "sync job queue (BLPOP)",
     "TTL cache for aggregates", "pending sync signals", ""),
], A_STORE, weights=[2, 1])
y = hop(y, "3", "the only tier that opens a connection to either store")

# ============================================ 4. how the code reaches it
y = band(y, 4, "Data Access Layer",
         "async SQLAlchemy · sessions, pooling and migrations", [
    (ic_layers, "Async ORM", "models, sessions, transactions",
     "SQLAlchemy 2.0.30 (asyncio)", "pool 10 + 20 overflow",
     "45s statement timeout"),
    (ic_plug, "Drivers", "async for the API, sync for the worker",
     "asyncpg 0.29.0 (API)", "psycopg2-binary 2.9.9 (worker)",
     "all geometry in EPSG:4326"),
    (ic_lock, "Migrations & config", "schema and environment",
     "Alembic 1.13.1", "python-dotenv 1.0.1",
     "AWS Secrets Manager (prod)"),
], A_DATA)
y = hop(y, "4", "typed rows and geometries handed to the stateless services")

# ============================================ 5. what turns rows into answers
y = band(y, 5, "Service Layer",
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
], A_SVC)
y = hop(y, "5", "route handlers call a service and shape the response")

# ============================================ 6. the HTTP surface
y = band(y, 6, "FastAPI Application Layer",
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
], A_API)
y = hop(y, "6", "served over HTTPS · JWT scoped to the user's states / LGAs")

# ============================================ 7. who reads it
y = band(y, 7, "Client Layer",
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
], A_CLIENT)

# ==================================== runtime, deliberately outside the flow
band(y + 34, None, "Runtime & Infrastructure",
     "where every stage above runs · not a stage the data passes through", [
    (ic_cube, "Container runtime", "one image, three services",
     "Docker · python:3.11-slim", "Compose · api, redis, sync_worker",
     "ECR registry · tagged per commit"),
    (ic_rack, "Compute & edge", "single instance behind a balancer",
     "EC2 us-east-1 · ALB", "EBS upload volume · S3 for the APK",
     "RDS PostgreSQL with PostGIS"),
    (ic_pipeline, "Delivery & ops", "OIDC deploys, no stored keys",
     "GitHub Actions · SSM Run Command", "Terraform · Secrets Manager",
     "CloudWatch logs · Sentry 2.18.0"),
], A_INFRA, dashed=True)

if _overflow:
    print("TEXT OVERFLOW:", *_overflow, sep="\n  ")
    sys.exit(1)

# nothing may spill past the page rail (this is how the legend used to get
# clipped: an estimate that looked fine in the JSON but ran off the edge)
_spill = [e for e in els if e["x"] + e["width"] > X0 + W + 1]
if _spill:
    print("SPILLS PAST THE RAIL:",
          *[f'{e.get("text", e["type"])} -> {e["x"] + e["width"]:.0f}'
            for e in _spill], sep="\n  ")
    sys.exit(1)

doc = {"type": "excalidraw", "version": 2, "source": "https://excalidraw.com",
       "elements": els,
       "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"},
       "files": {}}
with open(OUT, "w") as f:
    json.dump(doc, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(f"wrote {OUT}: {len(els)} elements")
