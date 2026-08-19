# SARMAAN MDA Dashboard — Data Analysis Reference

**Internal document. Local-only (this whole `docs/` folder is in `.gitignore`).**
**Updated:** 2026-05-21. Maintainer: whoever last shipped a dashboard PR.

This document is the single source of truth for every number you see on the
dashboard:

* What the metric **means** in plain English.
* The **column(s)** in the database it reads from.
* The exact **math** applied to those columns.
* Any **filters / round scoping / rounding rules** that change the result.
* Where in the codebase the SQL lives, so future-you can find it fast.

It does NOT live in the deployed app — keep that pretty and short — but every
KPI on the dashboard has a one-line formula in its subtitle that mirrors
what's here.

---

## Table of contents

| Section | Page in the dashboard |
|---|---|
| [0. Universal scoping rules](#0-universal-scoping-rules-applied-everywhere) | applies to every endpoint below |
| [1. Overview page](#1-overview-page) | landing page (`view-dashboard`) |
| [2. Coverage Analysis page](#2-coverage-analysis-page) | `view-coverage` |
| [3. Quality Checks page](#3-quality-checks-page) | `view-qc` |
| [3a. Teams Performance page](#3a-teams-performance-page) | `view-teams` |
| [4. Campaign Trends page](#4-campaign-trends-page) | `view-trends` |
| [5. Geographic View](#5-geographic-view) | `view-geo` |
| [5a. Placeholder pages (no live data)](#5a-pages-with-no-live-data-placeholders) | drug tracker, supervisor, reports |
| [5b. Admin panel pages](#5b-admin-panel-pages-mda-admin) | `/mda-admin` |
| [6. Source files quick reference](#6-source-files-quick-reference) | — |
| [7. Change history of metric definitions](#7-change-history-of-the-metric-definitions) | — |

**How to read each metric section.** Every metric below follows the same shape:
1. **What you see** — one sentence describing the KPI tile / chart / table on the dashboard.
2. **What we compute** — the formula, in the form `output = function(columns)`.
3. **Why** — the campaign rationale (where applicable: thresholds, scoping choices, edge cases).

---

## 0. Universal scoping rules (applied EVERYWHERE)

Every metric below is **scoped to a single project** via `project_id` (round-
isolation). Two helpers enforce this throughout the codebase:

* **`resolve_pid(project_id)`** — query param, falls back to the project with
  `is_active = TRUE`, else the lowest id. See `app/routes/mda.py:118`.
* **`_scoped_where(pid, filters, params, alias)`** — adds
  `<alias>.project_id = :pid` to every WHERE clause **and** the
  `campaign_start_date` filter below. See `app/routes/mda.py:138`.

### Campaign-window filter (added May 2026)

`geo_projects` has two optional columns:
| Column | Type | Meaning |
|---|---|---|
| `campaign_start_date` | `date` | Official Day-1 of the round (e.g. R5 = 2026-05-19). |
| `campaign_end_date` | `date` | Planned final day. |

When `campaign_start_date` is set, **every received_on-based query in the
dashboard filters out rows submitted before that date**. The implementation
is in `_scoped_where`:

```sql
(received_on AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Lagos')::date
>= COALESCE(
    (SELECT campaign_start_date FROM geo_projects WHERE id = :pid),
    '1900-01-01'::date
)
```

The two May-18 test submissions on R5 are excluded by this filter. They are
**not deleted** — clearing `campaign_start_date` brings them back instantly.

### Timezone

All `received_on`, `started_time`, `completed_time` are stored as
`TIMESTAMP WITH TIME ZONE` in UTC and converted to **Africa/Lagos (UTC+1)**
at query time when extracting day-of-week, hour-of-day, or
`received_on::date`. The conversion expression is always:

```sql
(received_on AT TIME ZONE 'UTC' AT TIME ZONE 'Africa/Lagos')::date
```

---

## 1. Overview page

Endpoint: `GET /api/mda/overview`  (see `app/routes/mda.py:1314`)

### 1.1 Forms Submitted

**What you see.** The "Forms Submitted" KPI tile — a single integer counting every household form a field team has uploaded for the active round.

**What we compute.** `COUNT(*)` over `mda_households`, scoped by Section 0. One row = one household visit submitted by CommCare. Refusing households are counted (they still produce a real form — just with `flag_refusal = TRUE`); only the consenting forms also have child-level individual rows in `mda_individuals`.

**Why this column.** The form is the smallest unit the platform can verify came from the field. Counting children directly would over- or under-count whenever a team forgot to fill a number or doubled up a household.

### 1.2 Children Treated

**What you see.** The "Children Treated" KPI tile — total doses of azithromycin administered so far this round.

**What we compute.** `COALESCE(SUM(number_of_treated), 0)` over `mda_households`. `number_of_treated` is filled by the team at form submission and represents the count of children aged 1–59 months in that household who actually swallowed the dose. Refusing households contribute `0` to the sum.

**Why this column (not the individual rows).** `mda_households.number_of_treated` is the team's own attestation, captured in CommCare's main form. `mda_individuals.treatment_status = '1'` is a parallel record from the child repeat-group. They usually agree; we use the household count for the Overview tile because it's the team's single number and is set even when the individual repeat-group is incomplete.

### 1.3 Administrative Coverage

**What you see.** The "Administrative Coverage" KPI tile — a percentage showing how close the campaign is to the planned target, plus a colour band (green ≥ 80 %, amber 60–79 %, red < 60 %).

**What we compute.**
```
coverage_pct = children_treated / baseline_total * 100
```
| Component | Source |
|---|---|
| `children_treated` | `SUM(mda_households.number_of_treated)` for this project (same as Section 1.2) |
| `baseline_total` | `SUM(mda_baseline.total_treated)` for this project |

**Why "administrative".** This is the *administrative* coverage — what the campaign team itself reports — not a population-survey coverage. The denominator is the SARMAAN micro-planning target (the population the round set out to reach), not the true under-five population from the census. WHO's protective-threshold reference of **80 %** assumes the micro-plan target is reasonably close to the eligible-children total.

**Where the baseline comes from.** The active `mda_baseline` rows are uploaded from the SARMAAN target Excel. `total_treated` in that table is the **enumeration target** (planned children to reach), NOT the prior round's treated count. For Sokoto R5 the upload prefers the `R5 Target (Ward)` sheet of `SARMAAN II R5 Target for Sokoto.xlsx` (totals to 1,076,419 across 20 LGAs); the loader (`app/routes/mda.py:upload_baseline()`) falls back to the `Raw Data` sheet, then to the legacy R4 settlement pivot if neither is present.

### 1.4 Active Field Teams

**What you see.** The "Active Field Teams" KPI tile — a count of the distinct field teams who have uploaded at least one form for this round and matching any active filters.

**What we compute.**
```
teams_active = COUNT(DISTINCT hq_user)   -- over the scoped mda_households rows
```
Returned as `teams_active` in `/api/mda/overview`, rendered with the subtitle *"Distinct field teams that submitted ≥ 1 form in scope."*

**What "team" means.** Each CommCare field team submits under a single user account; that account name is stored as `hq_user` on every form they upload. So one distinct `hq_user` value = one team. The count is **derived from submitted forms**, not from a separate team roster.

**What "active" means.** A team is counted as active for the current scope as soon as they have submitted **≥ 1 form** matching the active filters. There is no minimum threshold (no "must submit N forms today"). This is why the number can never exceed the number of distinct user accounts that have *ever* uploaded for this project + filter combination.

**Filter behaviour.** The count obeys every filter the page applies — project (always), and optionally LGA, ward, and date range. "Active Field Teams" in an LGA-filtered view means *teams that submitted ≥ 1 form in that LGA*, not the global team count. Per-LGA counts are NOT additive: a team that crossed two LGAs counts in both per-LGA views but as `1` in the global view.

**Edge cases worth knowing.**
- `hq_user IS NULL` rows (rare — broken sync, mis-mapped form) are still counted by `COUNT(DISTINCT)` as one "team" called `NULL`. Per-team analytics endpoints (`/teams/lga`, `/teams/by-error`) explicitly filter `hq_user IS NOT NULL` to avoid this, but the overview KPI does not — if you ever see the count off by 1, this is the usual cause.
- The source field name from CommCare exports is sometimes `hq.username` / `username`; we normalise to `hq_user` at ingest time (see `app/routes/mda.py` import code).

> **Cross-page note — same label, two different KPIs.**
> The Teams Performance page (`view-teams`) has its own KPI tile **also labelled "Active Teams"** (`#kpi-teams-total`, see Section 3a.1). Despite the matching label, it is *not* computed the same way:
> * Overview (`teams_active`) → `COUNT(DISTINCT hq_user)` → one team active in 3 LGAs counts as **1**.
> * Teams Performance (`teamsData.length`) → row count of the `(hq_user, lga)` grouped result → one team active in 3 LGAs counts as **3**.
>
> The two numbers **agree** when no team works across LGAs (the common case in SARMAAN, where teams are assigned per LGA), and **diverge** the moment any team submits in multiple LGAs. Treat Section 1.4 (Overview) as the canonical distinct-team figure; Section 3a.1 is a per-page convenience derived from the productivity table. If we ever need them guaranteed identical, the fix is in Section 3a.1.

### 1.5 Campaign Days Active

**What you see.** The "Campaign Days Active" KPI tile — shows `Day N (starts YYYY-MM-DD) · X submission days so far`. The big number ticks forward every calendar day from the official start; the subtitle counts the actual days teams uploaded forms.

**What we compute.** Three numbers, two of which are displayed:
| Field | Formula | Used for |
|---|---|---|
| `current_campaign_day` (the big "Day N") | `(today_lagos − campaign_start_date) + 1`, clamped to `[1, planned_duration_days]` | The KPI tile's primary number. Returns `0` before the campaign starts, `NULL` if `campaign_start_date` is unset. |
| `days_active` (the subtitle "X submission days") | `COUNT(DISTINCT received_on::date)` in Africa/Lagos | The smaller number after the "·" separator. |
| `planned_duration_days` | `(campaign_end_date − campaign_start_date) + 1` | Just for subtitle context. |

**Why two numbers.** Earlier versions of the tile only showed `days_active`, which froze on Sundays / off days when no forms came in — making it look like the campaign had paused. The campaign team wanted the calendar to tick forward regardless of whether teams uploaded that day, so we added `current_campaign_day` (calendar-driven) but kept `days_active` (data-driven) visible as a secondary signal.

### 1.6 QC Flags (the headline number)

**What you see.** The "Quality Issues" KPI tile and the headline number at the top of the Quality Checks page — a single integer summing every quality flag the system has raised.

**What we compute.**
```
total_qc_flags = fast_forms + slow_forms + after_hours
               + gps_outside_lga + gps_poor_accuracy + duplicate_gps
```
This is a **sum of flag counts**, so a single form carrying three flags contributes `3` here.

Companion fields on the same payload:
| Field | Formula |
|---|---|
| `forms_with_error` | Count of *distinct forms* with at least one error flag (a form contributes **once** regardless of how many flags it carries). Includes two extra flags (`flag_duplicate`, `flag_gps_zero`) that the headline above excludes — see Section 3.2 for why. |
| `error_rate_pct` | `forms_with_error / total_forms * 100` |

**Why these six flags and not others.**
* **Refusals** are excluded — they are a *campaign outcome*, not a data-quality problem.
* **Sync lag** is excluded — it is an *operations* metric (a slow phone / bad signal), not something the team did wrong.
* **`flag_duplicate` and `flag_gps_zero`** are excluded from the headline sum because they are usually device or sync hiccups (the same form arriving twice, a phone reporting `(0,0)` coords); we still count them on the *per-form* tally (`forms_with_error`) because if you're looking at a single form you do want to know it's broken.

---

## 2. Coverage Analysis page

### 2.1 Baseline Total / Total Treated / Overall Coverage

**What you see.** Three KPI tiles at the top of the Coverage Analysis page — the campaign target, the doses administered so far, and the percentage that is of the target.

**What we compute.** Same underlying numbers as Section 1.2 and Section 1.3 — just rendered as separate tiles instead of one combined Overview tile:
| Tile | Source | Formula |
|---|---|---|
| Baseline Total | `mda_baseline.total_treated` | `SUM(total_treated)` for this project |
| Total Treated | `mda_households.number_of_treated` | `SUM(number_of_treated)` for this project |
| Overall Coverage | Both | `treated / baseline * 100` |

These three tiles should always match the Overview's "Children Treated" and "Administrative Coverage" figures — if they ever drift, the cause is almost always an LGA / ward filter being applied on one page but not the other.

### 2.2 LGAs on Target ≥ 80 % / Below 60 %

**What you see.** Two KPI tiles — how many of the state's LGAs have crossed the WHO protective threshold (≥ 80 %), and how many are still in the at-risk band (< 60 %). When zero LGAs meet either condition the subtitle falls back to naming the highest- or lowest-coverage LGA so the tile is never blank.

**What we compute.** Endpoint `GET /api/mda/coverage/lga` (see `app/routes/mda.py:1500`) returns one row per LGA. For each:
```
coverage_pct = SUM(h.number_of_treated) / b.baseline_total * 100
```
where `b.baseline_total` is `SUM(mda_baseline.total_treated)` grouped by `INITCAP(TRIM(b.lga))` (the `INITCAP(TRIM(...))` is the spelling-normaliser that makes "ILLELA " and "Illela" match).

The frontend then counts:
* `cov-kpi-good`  = LGAs with `coverage_pct ≥ 80`
* `cov-kpi-below` = LGAs with `coverage_pct < 60`

**Why these thresholds.** 80 % is the standard WHO MDA protective-effectiveness threshold. 60 % is the campaign team's internal "needs attention now" line — below it, retreatment is almost always required.

### 2.3 Coverage by Age Category per LGA (the stacked-bar chart)

**What you see.** A horizontal stacked bar per LGA. Each bar is the LGA's full age-1-to-59 target = 100 %, split into three coloured segments: infants reached (1–11 months), older children reached (12–59 months), and the gap still untreated.

**What we compute.** Endpoint `GET /api/mda/coverage/lga-by-age` (`app/routes/mda.py:1796`). Two per-band coverage ratios first:
```
coverage_1_11_pct  = treated_1_11  / baseline_1_11  * 100
coverage_12_59_pct = treated_12_59 / baseline_12_59 * 100
```
| Component | Source |
|---|---|
| `baseline_1_11`  | `SUM(target_1_11_f + target_1_11_m)` from `mda_baseline` |
| `baseline_12_59` | `SUM(target_12_59_f + target_12_59_m)` from `mda_baseline` |
| `treated_1_11`   | `COUNT(mda_individuals)` where `age_in_months BETWEEN 1 AND 11 AND treatment_status = '1'` (joined to households for LGA scope) |
| `treated_12_59`  | same for `12–59` |

Then for the chart segments (so the three pieces of every bar sum to 100):
* `treated_1_11_pct  = treated_1_11  / (baseline_1_11 + baseline_12_59) * 100`
* `treated_12_59_pct = treated_12_59 / (baseline_1_11 + baseline_12_59) * 100`
* `untreated_pct     = 100 − treated_1_11_pct − treated_12_59_pct`

**Why two age bands.** The 1–11 month band is operationally harder (less mobile, often nursing, frequently missed) and clinically more sensitive. Splitting the stack makes it visible at a glance whether an LGA's gap is concentrated in the harder band.

### 2.4 Missed Households & Refusals card

**What you see.** A multi-line card on Coverage Analysis with three figures: refusing households, refusal rate (%), and an *estimated* number of children we couldn't reach because of those refusals. Beneath it, two breakdowns: reasons given by caregivers (donut chart of codes 1/2/3/9) and the top-15 free-text reasons.

**What we compute.** Endpoint `GET /api/mda/coverage/refusals-analysis` (`app/routes/mda.py:1681`).

**Refusing households**
```
refusing_households = COUNT(*) WHERE flag_refusal = TRUE
```
`flag_refusal` is set during ingest in `_map_household` when CommCare's `consent_trt = '0'`.

**Refusal rate**
```
refusal_rate_pct = refusing_households / total_forms * 100
```

**Children missed (estimated)** — the formula the campaign team asked us to document explicitly.
A refusing household never enters the **individual repeat-group** in CommCare (the team refuses before the child enumeration step), so `mda_individuals` contains **zero rows** for refusing households. We cannot count their children directly. The estimator applies the round's own mean children per consenting household:

```
mean_children_per_consenting_hh = consenting_individuals / consenting_households

  where
  consenting_individuals = COUNT(mda_individuals i
                                 JOIN mda_households h ON h.formid = i.hh_formid
                                 WHERE h.project_id = pid
                                   AND NOT h.flag_refusal)

  consenting_households  = COUNT(mda_households h
                                 WHERE h.project_id = pid
                                   AND NOT h.flag_refusal)

estimated_missed_children = refusing_households * mean_children_per_consenting_hh
```

**Worked example.** If a round has 77,000 consenting households containing 253,000 enumerated children, that gives `mean_children_per_consenting_hh ≈ 3.28`. Then 230 refusing households means an estimate of `230 × 3.28 ≈ 755` missed children. Live figures vary every sync — the dashboard subtitle prints the actual numbers using the same formula, so the card always speaks for itself.

**Why we trust each round's own ratio.** R4 had a mean of ~4.34 children per consenting household (smaller, 4-LGA sample); R5 is ~3.28 across all 20 LGAs. Demographics and average family size vary between rounds and states; using *this* round's ratio (computed live from the same dataset that produces the refusal count) avoids transplanting an outdated denominator.

#### Reasons given (codes)
Read from `mda_households.reasons_for_refusal` (CommCare choice code, values
seen in R5 so far: `1`, `2`, `3`, `9`). The chart maps these to:
| Code | Display label |
|---|---|
| `1` | Caregiver / parent declined |
| `2` | Child temporarily absent |
| `3` | Religious / cultural reason |
| `9` | Other (free-text) |

Free-text reasons (when code = `9`) come from
`mda_households.others_reasons_for_refusal`, lower-cased + trimmed to
deduplicate `INSECURITY ISSUE` / `insecurity issue`, top 15 by count.

---

## 3. Quality Checks page

**What this page is for.** Surfacing data-quality issues fast enough for the campaign team to coach a specific team or LGA the same day. Every flag below is computed at sync time (or right after) so the dashboard is always reading pre-computed booleans — not re-evaluating rules on each request.

Endpoint: `GET /api/mda/qc/summary`  (`app/routes/mda.py:907`)

### 3.1 Flag definitions and exact thresholds

Each flag is a boolean column on `mda_households`, set either during ingest (immediate, per-form) or in a post-insert SQL `UPDATE` (spatial joins after all rows for the sync are loaded):

| Flag column | Set at | Rule (plain English) | Calibrated value |
|---|---|---|---|
| `flag_gps_zero` | sync ingest | The phone reported coordinates `(0.0, 0.0)` — i.e. GPS never locked. | always flag |
| `flag_gps_poor_accuracy` | sync ingest | Reported GPS accuracy is worse than the threshold. | **`> 20 m`** (May 2026 calibration; was 10 m) |
| `flag_after_hours` | sync ingest | The form was completed outside the working window. Local-time hour is `< 6` or `≥ 19`. | **window 6 AM – 7 PM Lagos** |
| `flag_fast_form` | sync ingest | The form took less time than physically plausible for a household visit. | **`< 5 min`** |
| `flag_slow_form` | sync ingest | The form took longer than expected (likely paused / phone left open). | **`> 60 min`** |
| `flag_sync_lag` | sync ingest | More than 48 hours passed between form completion and upload to CommCare HQ. | **`> 48 h`** — operations metric, NOT counted in `total_qc_flags` |
| `flag_refusal` | sync ingest | CommCare's `consent_trt = '0'`. | campaign outcome, NOT counted in `total_qc_flags` |
| `flag_gps_outside_lga` | post-insert UPDATE | The form's typed LGA doesn't match the LGA polygon that actually contains the GPS point. | spatial join |
| `flag_gps_outside_ward` | post-insert UPDATE | The GPS point falls outside every ward polygon in the state. | spatial join |
| `flag_gps_outside_state` | post-insert UPDATE | The GPS point falls outside every LGA polygon in the state. | spatial join |
| `flag_duplicate_gps` | post-insert UPDATE | Same `(latitude, longitude)` appears on multiple forms in the same project. | exact-match |

Threshold definitions live in `app/services/commcare_sync.py:232-247`. Spatial flags are computed in `app/services/commcare_sync.py:534-590` after each sync. Calibration history is in Section 7.

### 3.2 KPI tile formulas (mirror what shows on the dashboard)

**What you see.** Four large numbers on the Quality Checks page header — Total QC Flags, Forms with Errors, Error Rate %, plus two breakdowns (GPS anomalies, Timing issues).

**What we compute.**
```
total_qc_flags  = fast_forms + slow_forms + after_hours
                + gps_outside_lga + gps_poor_accuracy + duplicate_gps

forms_with_error = COUNT WHERE (flag_fast_form OR flag_slow_form OR flag_after_hours
                                OR flag_gps_outside_lga OR flag_gps_poor_accuracy
                                OR flag_duplicate_gps  OR flag_duplicate
                                OR flag_gps_zero)

error_rate_pct  = forms_with_error / total_forms * 100

gps_anomalies   = gps_outside_lga + duplicate_gps + gps_poor_accuracy
timing_issues   = after_hours + fast_forms + slow_forms
```

**The headline `total_qc_flags` and `forms_with_error` use different flag sets — that's deliberate.** They answer two different questions:
* `total_qc_flags` asks *"how many quality issues are there?"* — it is a **sum of counts**. One form with three flags contributes 3. Includes only the six flags the campaign team can act on (fast / slow / after-hours / outside-LGA / poor-accuracy / duplicate-GPS).
* `forms_with_error` asks *"how many forms have any issue?"* — it is a **distinct-form count**. One form with three flags contributes 1. It additionally includes `flag_duplicate` and `flag_gps_zero` because if you are looking at a single form, you do care that it's a sync-duplicate or has a `(0,0)` reading, even though those aren't team-coachable behaviours.

If you are scanning the dashboard quickly: the small number is "issues per form" (worse if larger), the big number is "total issues across the round" (worse if rising faster than `total_forms`).

### 3.3 Refusal Rate (on the Quality Checks page)

**What you see.** A small KPI tile labelled "Refusal Rate" on Quality Checks, showing a percentage to two decimal places.

**What we compute.** Same formula as Section 2.4 (`refusing_households / total_forms * 100`); the only difference is precision — this endpoint returns `refusal_pct` to four decimal places so the UI can format it as `X.XX%`.

### 3.4 Household Refusals per LGA chart

**What you see.** A horizontal bar chart on Quality Checks listing every LGA with its refusal count and refusal rate as a tooltip — sorted descending so the worst LGAs surface to the top.

**What we compute.** Endpoint `GET /api/mda/qc/refusals-by-lga` (`app/routes/mda.py:1071`):
```
For each LGA:
  refusals    = COUNT WHERE flag_refusal = TRUE
  refusal_pct = refusals / COUNT(*) * 100
```

### 3.5 Unusually Fast / Slow Forms by LGA (stacked bar)

**What you see.** A stacked horizontal bar per LGA — deep-green for fast forms (`< 5 min`), gold for slow forms (`> 60 min`) — with the LGA's average form duration printed beside the bar.

**What we compute.** Endpoint `GET /api/mda/qc/duration-by-lga` (`app/routes/mda.py:1117`). For each LGA:
* `fast_count` = `COUNT WHERE flag_fast_form = TRUE`
* `slow_count` = `COUNT WHERE flag_slow_form = TRUE`
* `avg_min`    = `AVG(form_duration_min)`
* `min_min`, `max_min` — surfaced in the tooltip for context

### 3.6 Quality Checks by Team table

**What you see.** A long table at the bottom of the Quality Checks page — one row per `(team, LGA)` pair, with one column per flag type and an aggregate `error_rate` percentage. Used by the campaign team to coach specific field teams.

**What we compute.** Endpoint `GET /api/mda/qc/teams-summary` (`app/routes/mda.py:1129`):
```
For each (hq_user, lga):
  total_forms      = COUNT(*)
  forms_with_error = COUNT WHERE (any of the 8 flags listed in Section 3.2)
  dup_forms        = COUNT WHERE flag_duplicate
  dup_gps          = COUNT WHERE flag_duplicate_gps
  gps_outside_lga  = COUNT WHERE flag_gps_outside_lga
  poor_gps         = COUNT WHERE flag_gps_poor_accuracy
  after_hours      = COUNT WHERE flag_after_hours
  fast_forms       = COUNT WHERE flag_fast_form
  slow_forms       = COUNT WHERE flag_slow_form
  sync_lag         = COUNT WHERE flag_sync_lag
  refusals         = COUNT WHERE flag_refusal
  error_rate       = forms_with_error / total_forms * 100
```
The frontend rolls up a display-only `gps_issues = gps_outside_lga + poor_gps + dup_gps` to keep the column count manageable.

---

## 3a. Teams Performance page

**What this page is for.** Productivity-vs-quality view of every field team. Used to identify stars worth modelling, low-performing teams that need coaching, and volume-rusher teams whose form-completion speed has dropped below acceptable.

Endpoint: `GET /api/mda/teams/performance`  (see `app/routes/mda.py:1452`)

The endpoint returns one row per `(hq_user, lga)` pair:
| Field | Formula |
|---|---|
| `total_forms`   | `COUNT(*)` on `mda_households` for this team |
| `days_active`   | `COUNT(DISTINCT received_on::date)` for this team |
| `avg_per_day`   | `ROUND(total_forms / days_active, 1)` — productivity per active day |
| `total_treated` | `COALESCE(SUM(number_of_treated), 0)` |
| `after_hours`   | `COUNT WHERE flag_after_hours = TRUE` |
| `fast_forms`    | `COUNT WHERE flag_fast_form  = TRUE` |
| `meets_target`  | `avg_per_day >= 80` (Boolean) |

Scoping: same `_scoped_where` as every other endpoint — active project, within
the campaign window, optional LGA + ward filters from the query string.

### 3a.1 Top-line KPI tiles

```
active_teams        = teamsData.length
teams_meeting       = COUNT WHERE avg_per_day >= 80
teams_below         = active_teams − teams_meeting
avg_forms_per_team  = AVG(avg_per_day)
```

The "≥ 80 forms/day" target is a project convention — calibrated for SARMAAN
Sokoto. Tweakable in the frontend by editing `loadTeams()`.

> **`active_teams` here ≠ Overview `teams_active`.** The `/api/mda/teams/performance` SQL does `GROUP BY hq_user, lga`, so a team that submitted forms in 2 LGAs returns **2 rows** and `teamsData.length` counts that team twice. The canonical distinct-team count lives in Section 1.4 (Overview `teams_active`, which is `COUNT(DISTINCT hq_user)`). For SARMAAN Sokoto the two usually match because teams are assigned per LGA, but the formulas are not identical. If we ever need exact parity here, dedupe by `hq_user` in `loadTeams()` (frontend) or change the endpoint to group by `hq_user` only and aggregate LGAs into an array.

### 3a.2 Productivity vs Quality scatter (the bubble chart)

Each bubble is one team. Two derived axes:

```
x_axis = team.avg_per_day                       # productivity
y_axis = 100 − min(100,
           (team.after_hours + team.fast_forms) / max(team.total_forms, 1) * 100
         )                                      # "quality score"
```

**Interpretation:**
* Top-right bubbles (high productivity, high quality) = stars
* Bottom-right (high productivity, low quality) = volume rushers — review forms
* Top-left (low volume, high quality) = small teams or new starts
* Bottom-left = need supervision

The quality-score definition is intentionally simple — only the two **team-
controllable** flags (after-hours + fast-form) feed into it. GPS-outside-LGA
and duplicate-GPS are excluded because those can be caused by polygon
inaccuracies or co-located households, not by the team's behaviour.

### 3a.3 Top / Bottom-5 lists

Frontend sorts the same `teamsData` array by `avg_per_day`:
```
top_teams = teamsData[0:5]                                       # descending by avg
low_teams = sorted(teamsData, key=avg_per_day)[0:5]               # ascending
```

### 3a.4 What the page does NOT show

* No per-team-per-day breakdown (we'd need a separate endpoint for that — flagged
  as a possible follow-up).
* No GPS-issue contribution to the quality score (deliberate, see Section 3a.2).
* No refusal counts in the team table on this page — they're surfaced in the
  Quality Checks → Teams table (see Section 3.6) where the audience is different.

---

## 4. Campaign Trends page

**What this page is for.** Time-based view of the campaign — daily progress, cumulative doses, and a round-over-round comparison so the campaign team can see whether this round is on or off pace vs. the prior round.

### 4.1 Daily Form Submissions (active round only)

**What you see.** A line/bar chart of forms submitted per day across the campaign window. X-axis is calendar days in Africa/Lagos; Y-axis is forms (with treated counts in a secondary series).

**What we compute.** Endpoint `GET /api/mda/trends/daily-by-round` (`app/routes/mda.py:758`). For each `(project_id, day)`:
* `forms`   = `COUNT(*)`
* `treated` = `COALESCE(SUM(number_of_treated), 0)`

`day` is `received_on::date` converted to Africa/Lagos. The series is bounded by the project's `campaign_start_date` (test-day submissions are filtered out — see Section 0). Only the project where `is_active = TRUE` is rendered.

### 4.2 Cumulative Doses Administered

**What you see.** A monotonically increasing line showing the running total of doses across the campaign — quickly tells you whether you're tracking towards the baseline target by end-of-round.

**What we compute.** Frontend running total over the Section 4.1 series:
```
cumul[i] = cumul[i-1] + day[i].treated
```

### 4.3 Round-over-Round Comparison table

**What you see.** A table comparing the current round side-by-side with prior rounds (R4, R3, …). One row per round with target, administered, coverage %, refusals, and QC flag totals. The campaign team hides R4 by default for SARMAAN Sokoto since it was a partial 4-LGA pilot and would distort the comparison.

**What we compute.** Endpoint `GET /api/mda/rounds/summary` (`app/routes/mda.py:683`). One row per round (`geo_projects` × aggregates):
| Field | Formula |
|---|---|
| `targeted` | `SUM(mda_baseline.total_treated)` for the project |
| `administered` | `SUM(mda_households.number_of_treated)` for the project, within `campaign_start_date` window |
| `period_start` / `period_end` | `MIN(received_on::date)` / `MAX(...)` within the window |
| `refusals` | `COUNT WHERE flag_refusal = TRUE` |
| `total_forms` | `COUNT(*)` |
| `refusal_pct` | `refusals / total_forms * 100` |
| `coverage_pct` | `administered / targeted * 100` |
| `qc_flags` | `SUM` of the six error-flag CASEs (same definition as section 1.6) |

The frontend hides R4 by default per the campaign team's request — only the
active round is rendered in the table.

### 4.4 Per-LGA Comparison: Coverage Progress table

**What you see.** A table on Campaign Trends — one row per LGA — with the LGA's target, doses given so far, and coverage %. Same numbers as the Section 2.2 LGA tiles but presented as a full sortable table.

**What we compute.** Endpoint `GET /api/mda/rounds/lga-compare` (`app/routes/mda.py:739`). For each `(project_id, lga)`:
* `baseline` = `SUM(mda_baseline.total_treated)` for that `(project, INITCAP-trimmed lga)`
* `forms`    = `COUNT(mda_households)` (within campaign window)
* `treated`  = `SUM(mda_households.number_of_treated)` (within campaign window)
* `coverage_pct` = `treated / baseline * 100`

The frontend renders only `LGA · Target · R5 Treated · R5 Coverage` (R4 columns were removed per the campaign team's request, since R4 only covered 4 LGAs and the side-by-side made R5-only LGAs look like nulls).

---

## 5. Geographic View

**What this page is for.** Spatial confirmation of campaign coverage — the only page that can answer *"did the team physically go to the right place?"*. Mixes per-settlement completeness rollups with an interactive map of GPS points and team movement lines.

### 5.1 Visitation Ratio / Not Yet Visited / Overall Completeness

**What you see.** Three KPI tiles at the top of Geographic View: percentage of settlements with at least one GPS visit, count of settlements never visited, and a state-wide completeness percentage (how thoroughly the visited settlements were covered).

**What we compute.** Endpoint `GET /api/mda/geo/completeness` (`app/routes/mda.py:2003`). Reads `settlement_analytics` (one pre-computed row per settlement, recomputed after each sync) and aggregates project-wide:

```
visited_settlements   = COUNT WHERE point_count > 0
total_settlements     = COUNT(*)
visitation_pct        = visited_settlements / total_settlements * 100

overall_completeness  = AVG( CASE WHEN completeness_pct >= 70 THEN 100
                              ELSE completeness_pct END )
completed_60          = COUNT WHERE completeness_pct >= 60
completed_70          = COUNT WHERE completeness_pct >= 70
```

**Why the 70 %→100 % round-up.** Campaign-team rule of thumb (May 2026 agreement): *"near-complete is complete."* A settlement at 75 % grid coverage is operationally the same as fully covered — the remaining grids are usually unpopulated. Without this rule, even fully canvassed LGAs were averaging out to ~65 %, which under-sold the actual coverage achieved. The cap is applied at the per-settlement level THEN averaged, not the other way round.

### 5.2 Per-settlement completeness (the underlying number)

**What you see (downstream).** The per-settlement number that feeds the Section 5.1 rollup, Section 5.3 LGA/Ward rollups, and the map's settlement-polygon shading.

**What we compute.** Pre-computed by `compute_settlement_analytics()` (`app/services/spatial_engine.py:393`) after every sync, stored in `settlement_analytics`:

```
For each settlement:
  total_grids      = COUNT(DISTINCT grids in this settlement polygon)
  visited_grids    = COUNT(DISTINCT grids that contain ≥1 mda_household point)
  completeness_pct = visited_grids / total_grids * 100
  point_count      = COUNT(mda_households inside settlement polygon)
  is_visited       = (point_count > 0)                            # May 2026 rule
```

**Why visitation is point-count-based, not grid-based.** Old definition was "≥ 70 % of grids visited" — but a single GPS point inside a polygon is unambiguous evidence the team got there. The grid-based completeness still feeds the *quality* of the visit; visitation is now strictly a yes/no.

### 5.3 LGA / Ward rollups

**What you see.** The LGA and Ward bars on the Geographic View sidebar plus the polygon-shading on the map.

**What we compute.** Endpoints `GET /api/projects/{id}/analytics/{lgas|wards|settlements}` (`app/services/aggregation_engine.py`). For each LGA / Ward:
* `total_settlements`   = `COUNT(DISTINCT s.id)` where `s` joins to `settlement_analytics`
* `visited_settlements` = `COUNT WHERE point_count > 0`
* `visitation_pct`      = `visited / total * 100`
* `completeness_pct`    = `AVG(CASE WHEN sa.completeness_pct >= 70 THEN 100 ELSE sa.completeness_pct END)`

**Important nuance.** The LGA/Ward completeness is a **mean of the capped per-settlement values**, not a grid-weighted ratio. A tiny one-grid settlement at 100 % and a huge 100-grid settlement at 75 % both contribute equally to the average. This matches how the campaign team thinks about *settlement-level* progress, not *grid-level* progress.

### 5.4 GPS point colouring on the map

**What you see.** Every household form is rendered as a coloured dot — green if it landed inside expected bounds, red if it landed outside.

**What we compute.** A point is "in bounds" if it falls inside *either* a grid cell *or* a settlement polygon:
```
in_grid       = boolean stored on mda_households, set at sync time
in_settlement = EXISTS subquery against settlements (computed at query time)
in_bounds     = in_grid OR in_settlement
```
Green dot = `in_bounds = TRUE`. Red dot = outside every settlement *and* every grid. The OR (rather than AND) means a settlement-polygon match is sufficient even when grids haven't been generated yet — useful in new states where settlement boundaries arrive before the grid layer.

### 5.5 Team Movement line confinement

**What you see.** Coloured LineStrings on the map tracing each team's path through the day — but only inside a single settlement polygon. The team's path between two settlements is intentionally not drawn (would imply a straight-line "we went directly there" which we can't verify).

**What we compute.** Endpoint `GET /api/mda/teams/movement-geojson` (`app/routes/mda.py:1996`). Each movement point is spatially tagged at query time with the `unique_cod` of the settlement polygon containing it. The frontend groups points by `(hq_user, settlement_uniq_cod)`, sorts each group by `started_time` ASC, and draws one LineString per group. Crossing settlement boundaries breaks the line.

---

## 5a. Pages with no live data (placeholders)

The following nav items exist in the dashboard sidebar but are **not yet
backed by an endpoint** — their KPI cells render `&mdash;` and the tables
show "no data available" empty states. Documenting them here so it's
explicit which numbers on the dashboard are real vs. placeholder.

### 5a.1 LGA Drug Tracker (`view-lga`)
Intent: azithromycin tablet + suspension inventory across LGAs.
| Tile | Status |
|---|---|
| Total Tablets Received / Tablets Administered / Tablets Returned / Pending Reconciliation | placeholder — no API call |
| Drug Inventory by LGA table | empty-state card |

Backend work needed to populate: a `drug_inventory` table (per-LGA tablet
receipts + dispensations + cold-chain status). Not in scope for R5.

### 5a.2 Supervisor Forms (`view-supervisor`)
Intent: field supervision visits & data-quality assessment (DQA) observations.
| Tile | Status |
|---|---|
| Total Visits / Visits Passed DQA / Visits with Findings / Avg Visits / Supervisor | placeholder |
| Recent Supervision Visits table | empty-state |

Backend work needed: a `supervisor_visits` table populated from the CommCare
supervisor app. Not yet integrated.

### 5a.3 Reports & Export (`view-reports`)
Intent: scheduled / on-demand exports for SARMAAN, MoH, WHO ESPEN, donors.
| Tile | Status |
|---|---|
| Reports Generated / Scheduled Auto-Reports / WHO Submissions / Donor Briefs Sent | placeholder |
| State Daily Brief / LGA Coverage Workbook / WHO ESPEN Submission / QC & DQA Report download buttons | mocked — clicking them does nothing yet |

Some real export functionality lives elsewhere (e.g. the per-LGA CSV
downloads on the LGA-Coverage card; the Geographic-View Excel exports under
Admin / Superadmin). This page would unify them once the
`/api/mda/reports/*` endpoints are built. Not in scope for R5.

---

## 5b. Admin panel pages (`/mda-admin`)

Superadmin / Admin only. Numbers here are about the platform itself, not
about the campaign. Documenting them in the same doc keeps a single source
of truth.

### 5b.1 Data Sources (`view-sync`)
Endpoint: `GET /api/sync/config?project_id=X`, `GET /api/sync/history?...`,
`GET /api/sync/queue/depth`, `POST /api/sync/run`, `POST /api/sync/test`,
`POST /api/sync/run-onprem-mirror`.

Displays:
* CommCare connection form (base URL, app slug, username, password, form IDs).
* Last sync card with the progress bar driven by `sync_config.last_progress_step / last_progress_total`.
* Sync History table — last 5 runs from `sync_history`.
* **Sync queue chip** in the page header (added in PR #9):
  ```
  worker_available + depth = 0    →  "Sync worker idle"        (muted)
  worker_available + depth ≥ 1    →  "Sync queue: N job(s) ahead" (blue)
  Redis unreachable               →  "Inline mode — API restart can interrupt" (amber)
  ```
  Polled every 5 s while the view is active.
* **On-prem mirror banner** (only when `ONPREM_BACKUP_DATABASE_URL` is set, i.e. dev laptops on VPN): pushes the active project's `mda_households` to the on-prem Postgres watermark-style. State tracked in `onprem_mirror_state` table.

### 5b.2 Data Uploads (`view-uploads`)
| Card | Endpoint | Replaces |
|---|---|---|
| **MDA Household Data** (xlsx) | `POST /api/mda/upload` | n/a (incremental ingest is via CommCare sync) |
| **Baseline / Target Data** (xlsx) | `POST /api/mda/upload-baseline` | `mda_baseline` rows for the active project |
| **Settlement Master List (MLOS)** (xlsx) | `POST /api/mda/upload-mlos` | `mlos_settlements` rows for the active project |
| **Boundary Shapefiles** (LGA/Ward/Settlement/Grid ZIP) | `POST /api/projects/{id}/boundaries/{level}` | per-project boundary tables (state-shared) |
| **Upload History** | `GET /api/mda/upload-history` | last N upload records |

### 5b.3 Projects (`view-projects`)
| Action | Endpoint |
|---|---|
| List projects | `GET /api/projects` |
| Create | `POST /api/projects` — name, slug, description, state_name, round_number, campaign_start_date, campaign_end_date |
| Update | `PATCH /api/projects/{id}` |
| Delete (only when not active) | `DELETE /api/projects/{id}` |
| Set Active | `PATCH /api/projects/{id}` with `is_active=true` — auto-deactivates all others |

### 5b.4 Users (`view-users`)
| Action | Endpoint |
|---|---|
| List | `GET /api/auth/users` |
| Create | `POST /api/auth/users` (only superadmin can create admins/superadmins) |
| Promote / Demote | `POST /api/auth/users/{id}/promote` / `.../demote` |
| Reset password (superadmin-only) | `POST /api/auth/users/{id}/reset-password` |
| Delete | `DELETE /api/auth/users/{id}` |

Account self-service: `POST /api/auth/change-password` (any logged-in user;
requires current_password + new_password ≥ 8 chars).

### 5b.5 System Status (`view-system`)
Endpoint: `GET /api/mda/system/counts`. Returns one row of counts used to
render four tiles:
| Tile | Meaning |
|---|---|
| Households | rows in `mda_households` for the active project |
| Individuals | rows in `mda_individuals` for the active project |
| Baseline records | rows in `mda_baseline` for the active project |
| LGAs / Wards in scope | distinct `lgacode` / `wardcode` across all projects in this state |

Used by the platform operator to spot under-sized rounds at a glance.

### 5b.6 Change Password (`view-account`)
Endpoint: `POST /api/auth/change-password`. Available to any authenticated
user. Requires current_password + new_password ≥ 8 chars. Used by both
admin tiers and analysts.

---

## 6. Source files quick reference

| File | What lives there |
|---|---|
| `app/routes/mda.py` | Most dashboard endpoints. Long file; search for the path you want. |
| `app/routes/sync.py` | CommCare sync trigger, config, history, queue depth. |
| `app/services/commcare_sync.py` | OData fetch + row mapping + flag computation + spatial UPDATE. |
| `app/services/aggregation_engine.py` | LGA / Ward / Settlement rollups for the analytics endpoints. |
| `app/services/spatial_engine.py` | GeoJSON endpoints + `compute_settlement_analytics()`. |
| `app/services/job_queue.py` | Redis enqueue/dequeue helpers used by `/api/sync/run`. |
| `app/sync_worker.py` | Standalone sync worker that reads the Redis queue. |
| `static/mda.html` | Public dashboard — all KPI cards + charts. |
| `static/mda-admin.html` | Admin panel — projects, users, uploads, sync, system status. |

## 7. Change history of the metric definitions

| Date | Change | Reason |
|---|---|---|
| 2026-05-19 | Initial R5 baseline upload from SARMAAN II R5 Target file. | R5 starts. |
| 2026-05-20 | Visitation Coverage redefined: was "≥70 % grid completeness", now "≥1 GPS point inside polygon". | Campaign-team agreement. |
| 2026-05-20 | 70 %→100 % round-up rule added to completeness display + rollup. | "Near-complete is complete." |
| 2026-05-20 | GPS-accuracy threshold loosened 10 m → 20 m, then settled at 20 m. | 10 m over-flagged usable points. |
| 2026-05-20 | Fast-form threshold 5 → 2 → 5 min (settled at 5). | Calibration iteration. |
| 2026-05-20 | `total_qc_flags` unified across `/overview`, `/qc/summary`, `/qc/teams-summary`. | Three different definitions had drifted. |
| 2026-05-20 | `campaign_start_date` + `campaign_end_date` added to `geo_projects` and plumbed through every received_on query. | Hide pre-campaign test submissions. |
| 2026-05-21 | Campaign Days Active KPI switched from "count of submission days" to "calendar day of campaign". | Tile wasn't ticking forward on days with no data. |
| 2026-05-21 | Doc-wide rewrite: every metric section now opens with **What you see / What we compute / Why**. Added TOC. Generalised stale R5 example numbers in Section 2.4. Reconciled Section 1.6 (`total_qc_flags`, 6 flags) vs Section 3.2 (`forms_with_error`, 8 flags). Cross-referenced Section 1.4 ↔ Section 3a.1 "Active Teams" mismatch. | Pass for clarity / consistency — "doc should be clear and easily explained." |

---

_Maintenance: when you add a new metric or change a formula, add a row to
section 7 and update the relevant section above. The source files referenced
in section 6 are the truth — this doc is a translation, keep them in sync._
