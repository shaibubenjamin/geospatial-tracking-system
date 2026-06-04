#!/bin/bash
# SARMAAN II — Run full test suite
# Usage: bash scripts/run_all.sh

set -e

echo ""
echo "========================================"
echo "  SARMAAN II — Full Test Suite"
echo "========================================"

# ── Backend ──────────────────────────────────
echo ""
echo "▶  Backend (pytest)"
echo "────────────────────────────────────────"
pytest backend/ -v --cov=app --cov-report=term-missing
BACKEND_EXIT=$?

# ── Frontend ─────────────────────────────────
echo ""
echo "▶  Frontend (Vitest)"
echo "────────────────────────────────────────"
npx vitest run --reporter=verbose
FRONTEND_EXIT=$?

# ── E2E ──────────────────────────────────────
echo ""
echo "▶  E2E (Playwright)"
echo "────────────────────────────────────────"
npx playwright test
E2E_EXIT=$?

# ── Summary ──────────────────────────────────
echo ""
echo "========================================"
echo "  Results"
echo "========================================"
[ $BACKEND_EXIT -eq 0 ]  && echo "  ✅ Backend  — PASSED" || echo "  ❌ Backend  — FAILED"
[ $FRONTEND_EXIT -eq 0 ] && echo "  ✅ Frontend — PASSED" || echo "  ❌ Frontend — FAILED"
[ $E2E_EXIT -eq 0 ]      && echo "  ✅ E2E      — PASSED" || echo "  ❌ E2E      — FAILED"
echo "========================================"
echo ""

# Exit non-zero if anything failed
[ $BACKEND_EXIT -eq 0 ] && [ $FRONTEND_EXIT -eq 0 ] && [ $E2E_EXIT -eq 0 ]
