# ERITAS MDA — QA Test Suite

Production-replica QA for the ERITAS MDA platform (formerly SARMAAN MDA).

---

## Two harnesses

### 1. `scripts/qa-prod.sh` — Live production smoke tests *(the default)*

A self-contained bash + curl + jq script that runs a real 3-layer QA pass against the live ALB without needing any Python/Node setup. This is what gates a build.

```bash
bash eritas-mda-tests/scripts/qa-prod.sh
```

**Layers it covers (33 checks):**

| Layer | Checks | Examples |
|---|---|---|
| Backend (FastAPI) | 12 | Health, auth (wrong-pw → 401, empty body → 422), `/api/mda/overview` non-empty, QC summary, LGA + Ward coverage, GeoJSON, teams/performance, project list, authz on write endpoints, mutation gating, 404 handling |
| Infrastructure (AWS) | 10 | EC2 running, RDS encrypted + 7d backup, ALB target healthy, TLS cert valid, SG ports locked down, CloudWatch metrics flowing, RDS autoscale ceiling, SSM agent registered, S3 reachable, CI/CD workflows present |
| Frontend (Static UI) | 11 | `/dashboard` 200, login form + password field, `/mda-admin` 200, MapLibre + Chart.js referenced, HTML structure, `<title>`, favicon, viewport meta, 404 handling |

**Output format:** three per-layer tables + overall scorecard (`31/33 passed, 2 skipped (informational). Score: 93.9%`).

**Skipped checks (informational):**
- Write/upload endpoint smoke — would mutate prod data.
- Interactive flows (drill-down, modal, exports) — needs Playwright.

---

### 2. `backend/test_api.py` + `frontend/dashboard.test.jsx` + `e2e/dashboard.spec.js` — Deep test scaffold *(future / staging)*

A scaffold of 82 pytest + Vitest + Playwright tests for when we move to a React frontend and want unit + component + E2E coverage with `data-testid` hooks.

| File | Framework | Status |
|---|---|---|
| `backend/test_api.py` | pytest + httpx | Scaffold — needs route remap to `/api/mda/*` |
| `frontend/dashboard.test.jsx` | Vitest + RTL | Scaffold — React-only; activate post-React migration |
| `e2e/dashboard.spec.js` | Playwright | Scaffold — needs `data-testid` attrs in `static/mda.html` |
| `scripts/run_all.sh` | wrapper | Runs all three once they're wired |

---

## Structure

```
eritas-mda-tests/
├── backend/
│   └── test_api.py          # pytest scaffold (FastAPI + httpx)
├── frontend/
│   └── dashboard.test.jsx   # Vitest scaffold (React + RTL)
├── e2e/
│   └── dashboard.spec.js    # Playwright scaffold (browser flows)
└── scripts/
    ├── qa-prod.sh           # ★ Live prod smoke test — re-run on every build
    └── run_all.sh           # Wrapper for the scaffold (when wired)
```

---

## Standard for build sign-off

Run `bash scripts/qa-prod.sh` against prod after every deploy. Acceptance criteria:

- Backend: ≥ 11 / 12 pass
- Infrastructure: ≥ 9 / 10 pass
- Frontend: ≥ 11 / 11 pass
- Overall: ≥ 90 % score
- No P0 defects (anon data exposure, exposed admin ports, missing TLS)

Failures get filed as Asana sub-tasks under the build's parent QA task.

---

## Latest run (2026-06-04 — pre-rebrand)

- **Result:** 31 / 33 passed, 2 skipped (informational) — 93.9 %
- **Blocking defects (3):**
  1. P0 — Read APIs (`/api/mda/overview`, `/api/mda/coverage/lga`, `/api/projects`) return data without auth.
  2. P1 — ALB security group has SSH (port 22) open to 0.0.0.0/0.
  3. P1 — `/api/boundaries/lga/geojson` returns 404.
- **Follow-ups raised:** observability (`/health` + `/metrics` + Sentry), PII redaction, brand rename (this commit), wire QA into CI.

---

## Setup for the deep scaffold (when activating)

### Backend tests
```bash
pip install pytest pytest-asyncio httpx pytest-cov
# In test_api.py, uncomment: from app.main import app
pytest backend/ -v --cov=app --cov-report=term-missing
```

### Frontend tests *(only after React migration)*
```bash
npm install -D vitest @testing-library/react @testing-library/jest-dom \
  @testing-library/user-event jsdom
npx vitest run --reporter=verbose
```

### E2E tests
```bash
npm install -D @playwright/test
npx playwright install chromium firefox
npx playwright test --reporter=html
```

---

## Coverage targets

| Layer | Minimum target |
|---|---|
| Live prod smoke (`qa-prod.sh`) | ≥ 90 % pass on every build |
| Backend (when activated) | 80 % line coverage |
| Frontend (when activated) | All critical components have at least one test |
| E2E (when activated) | Every user-facing flow has a happy-path test |
