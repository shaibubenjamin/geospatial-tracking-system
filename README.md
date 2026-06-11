# ERITAS

### Evidence through Real-time Intelligence, Tracking, and Accountability Systems

ERITAS is a coverage and data-quality monitoring platform for large-scale
public-health campaigns. It turns the flood of records coming in from the field
into a clear, real-time picture: **how far the campaign has reached, where the
gaps are, and whether the data can be trusted.**

It was built for **Mass Drug Administration (MDA)** campaigns — where teams move
community to community distributing medicines against a deadline — but the model
generalises to any campaign that collects geo-tagged activity in the field.

---

## What it does

- **Real-time coverage.** Track progress as it happens, drilling from the whole
  state down through LGA → ward → settlement.
- **Data quality you can act on.** Automated checks surface records worth a
  second look — locations outside the expected area, implausibly fast or
  after-hours submissions, duplicates, and refusals.
- **A geographic view.** See coverage on the map, so "where are we behind?" is a
  glance, not a spreadsheet exercise.
- **Team & trend insight.** Active teams, throughput per day, and how coverage
  is building over the life of the campaign.
- **Built for the field, too.** A native mobile companion puts the same
  real-time coverage in the hands of the people doing the work.

---

## Who it's for

- **Program managers & M&E teams** who need an accurate, current picture without
  waiting for end-of-day reports.
- **Field supervisors** who need to know which areas still need attention.
- **Implementing partners & health agencies** accountable for campaign outcomes.

---

## The platform

ERITAS has two complementary surfaces over one source of truth:

- **Web platform** — a dashboard for analysts and managers: overview,
  coverage analysis, data-quality checks, team performance, trends, and an
  interactive coverage map.
- **Mobile app (Android)** — a native companion for field teams: campaign
  overview, coverage drill-downs, a map explorer, and an on-the-ground "where am
  I and what's left to cover" guide.

Both are **campaign-aware** — the same tools work across different states and
campaign rounds, each with its own coverage and quality picture.

---

## How coverage is measured

Field teams submit geo-tagged records as they work. Those records roll up from
the ground level — settlement, then ward, then LGA — into coverage you can read
at any altitude. A place counts as reached once enough of it has been covered,
and everything above it reflects what's happening beneath.

Areas that aren't part of a given campaign are shown as out of scope rather than
as "zero coverage," so the picture stays honest.

---

## Technology

- **Backend:** FastAPI · PostGIS (geospatial PostgreSQL)
- **Web:** MapLibre GL JS · Chart.js
- **Mobile:** Android — Kotlin · Jetpack Compose · Material 3

---

## Running locally

ERITAS runs as a set of containers, so a local instance needs only Docker.
Copy the provided example environment file to `.env`, start the stack with
Docker Compose, and open the app in your browser. Default local credentials are
provisioned for first sign-in and should be changed before sharing any
environment.

> This README is an overview. Operational setup, data loading, and deployment
> are covered in the team's internal documentation.
