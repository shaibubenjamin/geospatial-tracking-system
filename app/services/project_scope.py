"""
app/services/project_scope.py — per-project LGA scope for the sidebar.

Some campaigns run in a subset of a state's LGAs. The **map** still shows
every LGA polygon (so operators can see the full state context), but the
**sidebar LGA index** — where each LGA is listed with its Visitation and
Completeness pills — is trimmed to only the LGAs where data collection is
actually happening.

Keyed by ``(state_name.lower().strip(), round_number)``. A return of
``None`` means "no filter — show every LGA the boundaries table has for
this project", which is the default for every project that isn't listed
below. Sokoto R4, Sokoto R5, and Kano Pilot fall through to that default
and behave exactly as they always have.
"""
from __future__ import annotations

from typing import Optional, Set


# ── Kano Round 3 — 36 LGAs where SARMAAN R3 data collection happens ─────────
# Boundaries for the other 8 Kano LGAs (Bagwai, Dawakin Kudu, Doguwa, Gezawa,
# Gwarzo, Kibiya, Makoda, Sumaila) stay in the database and remain visible
# on the map — they just don't clutter the sidebar LGA list. Operator
# handed us this list on 2026-07-02.
_KANO_R3: Set[str] = {
    "Ajingi", "Albasu", "Bebeji", "Bichi", "Bunkure", "Dala",
    "Dambatta", "Dawakin Tofa", "Fagge", "Gabasawa", "Garko",
    "Garum Mallam", "Gaya", "Gwale", "Kabo", "Kano Municipal",
    "Karaye", "Kiru", "Kumbotso", "Kunchi", "Kura", "Madobi",
    "Minjibir", "Nassarawa", "Rano", "Rimin Gado", "Rogo",
    "Shanono", "Takai", "Tarauni", "Tofa", "Tsanyawa",
    "Tudun Wada", "Ungogo", "Warawa", "Wudil",
}

# Registry — add more per-project scopes here as they arrive.
# Key: (state_name lowercased and stripped, round_number as int).
IN_SCOPE_LGAS: dict[tuple[str, int], Set[str]] = {
    ("kano", 3): _KANO_R3,
}


def in_scope_lgas_for(
    state_name: Optional[str],
    round_number: Optional[int],
) -> Optional[Set[str]]:
    """Return the in-scope LGA name set for a project, or None to mean
    "no filter — show every LGA in this project's boundaries".

    Matching is done case-insensitively downstream, so callers can pass the
    raw ``geo_projects.state_name`` / ``round_number`` values without any
    pre-normalisation.
    """
    if not state_name or round_number is None:
        return None
    key = (str(state_name).strip().lower(), int(round_number))
    return IN_SCOPE_LGAS.get(key)


def normalise_lga_name(name: Optional[str]) -> str:
    """Same normalisation the SQL filter uses — case-insensitive + trimmed.

    Kept alongside the scope set so tests and helpers stay consistent
    with whatever the query does.
    """
    return (name or "").strip().lower()
