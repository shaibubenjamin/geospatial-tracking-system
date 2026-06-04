/**
 * SARMAAN II — End-to-End Test Suite
 * Playwright — simulates a real user in a real browser
 *
 * Install:
 *   npm install -D @playwright/test
 *   npx playwright install chromium
 *
 * Run:
 *   npx playwright test --reporter=html
 *   npx playwright test e2e/ --headed   ← watch the browser as tests run
 *
 * Config: playwright.config.js (see bottom of this file)
 */

import { test, expect } from '@playwright/test'

const BASE_URL = 'https://sarmaan-amr.ehealthnigeria.org'
const DASHBOARD = `${BASE_URL}/dashboard`

// ─── Shared login helper ───────────────────────────────────
async function login(page, username = 'test_user', password = 'test_password') {
  await page.goto(`${BASE_URL}/login`)
  await page.fill('[name="username"]', username)
  await page.fill('[name="password"]', password)
  await page.click('button[type="submit"]')
  await page.waitForURL(`${BASE_URL}/dashboard`)
}
// ──────────────────────────────────────────────────────────


// ═══════════════════════════════════════════════════
// 1. AUTHENTICATION FLOW
// ═══════════════════════════════════════════════════

test.describe('Authentication', () => {

  test('redirects unauthenticated user to login', async ({ page }) => {
    await page.goto(DASHBOARD)
    await expect(page).toHaveURL(/login/)
  })

  test('logs in with valid credentials', async ({ page }) => {
    await login(page)
    await expect(page).toHaveURL(DASHBOARD)
    await expect(page.locator('text=SARMAAN II')).toBeVisible()
  })

  test('shows error on wrong password', async ({ page }) => {
    await page.goto(`${BASE_URL}/login`)
    await page.fill('[name="username"]', 'test_user')
    await page.fill('[name="password"]', 'wrongpassword')
    await page.click('button[type="submit"]')
    await expect(page.locator('text=/invalid|incorrect|unauthorized/i')).toBeVisible()
  })

  test('logs out successfully', async ({ page }) => {
    await login(page)
    await page.click('text=Logout')
    await expect(page).toHaveURL(/login/)
  })

})


// ═══════════════════════════════════════════════════
// 2. DASHBOARD LOAD & CORE UI
// ═══════════════════════════════════════════════════

test.describe('Dashboard Core', () => {

  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('page title is correct', async ({ page }) => {
    await expect(page).toHaveTitle(/SARMAAN II/)
  })

  test('navigation menu items are all visible', async ({ page }) => {
    const navItems = ['Overview', 'Analysis', 'Completion', 'Quality Checks',
      'Supportive Supervision', 'Geospatial', 'Advanced Analytics', 'Admin Panel']
    for (const item of navItems) {
      await expect(page.locator(`text=${item}`)).toBeVisible()
    }
  })

  test('demographic KPI cards render with values', async ({ page }) => {
    // Wait for data to load
    await page.waitForSelector('[data-testid="households-planned"]', { timeout: 10000 })
    const value = await page.textContent('[data-testid="households-planned"]')
    // Should not be "—" (placeholder) once data loads
    expect(value).not.toBe('—')
  })

  test('tab links navigate to correct sections', async ({ page }) => {
    await page.click('text=Main')
    await expect(page).toHaveURL(DASHBOARD)

    await page.click('text=Validators')
    await expect(page).toHaveURL(`${BASE_URL}/validator-dashboard`)

    await page.click('text=Lab')
    await expect(page).toHaveURL(`${BASE_URL}/lab`)
  })

})


// ═══════════════════════════════════════════════════
// 3. DRILLDOWN FILTERS
// ═══════════════════════════════════════════════════

test.describe('Drilldown Filters', () => {

  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('filter panel opens when triggered', async ({ page }) => {
    await page.click('text=🔽 Drill-down Filters')
    await expect(page.locator('select[aria-label="LGA"]')).toBeVisible()
  })

  test('selecting LGA enables Ward dropdown', async ({ page }) => {
    await page.click('text=🔽 Drill-down Filters')
    const wardSelect = page.locator('select[aria-label="Ward"]')
    await expect(wardSelect).toBeDisabled()

    await page.selectOption('select[aria-label="LGA"]', { index: 1 })
    await expect(wardSelect).not.toBeDisabled()
  })

  test('applying LGA filter updates KPI cards', async ({ page }) => {
    await page.click('text=🔽 Drill-down Filters')
    await page.selectOption('select[aria-label="LGA"]', { index: 1 })
    await page.click('button:has-text("Apply Filters")')
    // Active filter badge should appear
    await expect(page.locator('.active-filters')).toBeVisible()
  })

  test('clearing filters removes active filter badges', async ({ page }) => {
    await page.click('text=🔽 Drill-down Filters')
    await page.selectOption('select[aria-label="LGA"]', { index: 1 })
    await page.click('button:has-text("Apply Filters")')
    await page.click('text=✕ Clear all')
    await expect(page.locator('.active-filters')).not.toBeVisible()
  })

})


// ═══════════════════════════════════════════════════
// 4. QUALITY CHECKS SECTION
// ═══════════════════════════════════════════════════

test.describe('Quality Checks', () => {

  test.beforeEach(async ({ page }) => {
    await login(page)
    await page.click('text=Quality Checks')
  })

  test('quality panel is visible', async ({ page }) => {
    await expect(page.locator('text=Overall Data Health')).toBeVisible()
  })

  test('load quality data button triggers data fetch', async ({ page }) => {
    await page.click('button:has-text("Load quality data")')
    await page.waitForSelector('[data-testid="health-score"]', { timeout: 15000 })
    const score = await page.textContent('[data-testid="health-score"]')
    expect(score).not.toBe('—')
  })

  test('error summary table renders after load', async ({ page }) => {
    await page.click('button:has-text("Load quality data")')
    await page.waitForSelector('.error-summary-table', { timeout: 15000 })
    await expect(page.locator('.error-summary-table')).toBeVisible()
  })

  test('CSV export button downloads a file', async ({ page }) => {
    await page.click('button:has-text("Load quality data")')
    await page.waitForSelector('[data-testid="error-csv-export"]', { timeout: 10000 })

    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.click('[data-testid="error-csv-export"]')
    ])
    expect(download.suggestedFilename()).toMatch(/\.csv$/)
  })

})


// ═══════════════════════════════════════════════════
// 5. GEOSPATIAL VIEW
// ═══════════════════════════════════════════════════

test.describe('Geospatial View', () => {

  test.beforeEach(async ({ page }) => {
    await login(page)
    await page.click('text=Geospatial View')
  })

  test('GPS summary stats are visible', async ({ page }) => {
    await expect(page.locator('text=Total GPS Points')).toBeVisible()
    await expect(page.locator('text=Inside Settlement')).toBeVisible()
    await expect(page.locator('text=Outside Settlement')).toBeVisible()
  })

  test('map renders without crashing', async ({ page }) => {
    // The map container should exist
    await expect(page.locator('.leaflet-container, .mapboxgl-map, [data-testid="map"]')).toBeVisible({ timeout: 15000 })
  })

  test('LGA dropdown loads settlement extents', async ({ page }) => {
    await page.selectOption('[data-testid="lga-map-filter"]', { index: 1 })
    // Give it time to load settlement polygons
    await page.waitForTimeout(3000)
    // The legend should update
    await expect(page.locator('text=Inside')).toBeVisible()
  })

  test('clicking legend key filters map points', async ({ page }) => {
    await page.click('text=● Outside')
    // Map should filter to only outside points
    await expect(page.locator('[data-testid="active-filter-outside"]')).toBeVisible()
    // Click again to clear
    await page.click('text=● Outside')
    await expect(page.locator('[data-testid="active-filter-outside"]')).not.toBeVisible()
  })

})


// ═══════════════════════════════════════════════════
// 6. BUG REPORT MODAL
// ═══════════════════════════════════════════════════

test.describe('Bug Report Modal', () => {

  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('modal opens on click', async ({ page }) => {
    await page.click('text=Report a Bug')
    await expect(page.locator('[role="dialog"]')).toBeVisible()
  })

  test('modal closes on ✕ click', async ({ page }) => {
    await page.click('text=Report a Bug')
    await page.click('button:has-text("✕")')
    await expect(page.locator('[role="dialog"]')).not.toBeVisible()
  })

  test('submitting empty form shows validation error', async ({ page }) => {
    await page.click('text=Report a Bug')
    await page.click('button:has-text("Submit")')
    await expect(page.locator('text=/required|cannot be empty/i')).toBeVisible()
  })

  test('submitting valid bug report closes modal and confirms', async ({ page }) => {
    await page.click('text=Report a Bug')
    await page.fill('[name="title"]', 'E2E test bug report')
    await page.selectOption('[name="severity"]', 'medium')
    await page.click('button:has-text("Submit")')
    // Should see success or modal close
    await expect(page.locator('[role="dialog"]')).not.toBeVisible({ timeout: 5000 })
  })

})


// ═══════════════════════════════════════════════════
// 7. AI CHAT ("Ask Your Data")
// ═══════════════════════════════════════════════════

test.describe('AI Chat Widget', () => {

  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('chat panel opens when triggered', async ({ page }) => {
    await page.click('[data-testid="chat-toggle"], text=💬')
    await expect(page.locator('text=Ask Your Data')).toBeVisible()
  })

  test('quick-action buttons are visible', async ({ page }) => {
    await page.click('[data-testid="chat-toggle"], text=💬')
    await expect(page.locator('text=Coverage %')).toBeVisible()
    await expect(page.locator('text=Top LGA')).toBeVisible()
  })

  test('typing a message and submitting shows a response', async ({ page }) => {
    await page.click('[data-testid="chat-toggle"], text=💬')
    await page.fill('textarea, input[type="text"]', 'What is the overall coverage?')
    await page.click('button:has-text("➤")')
    // Wait for Groq to respond
    await page.waitForSelector('.chat-response, [data-testid="chat-response"]', { timeout: 20000 })
    const response = await page.textContent('.chat-response')
    expect(response.length).toBeGreaterThan(5)
  })

})


// ═══════════════════════════════════════════════════
// 8. PERFORMANCE BASELINE
// ═══════════════════════════════════════════════════

test.describe('Performance', () => {

  test('dashboard loads within 5 seconds', async ({ page }) => {
    await login(page)
    const start = Date.now()
    await page.waitForSelector('[data-testid="households-planned"]', { timeout: 10000 })
    const elapsed = Date.now() - start
    expect(elapsed).toBeLessThan(5000)
  })

  test('page has no console errors on load', async ({ page }) => {
    const errors = []
    page.on('console', msg => {
      if (msg.type() === 'error') errors.push(msg.text())
    })
    await login(page)
    await page.waitForTimeout(3000)
    expect(errors).toHaveLength(0)
  })

  test('no failed network requests on initial load', async ({ page }) => {
    const failedRequests = []
    page.on('response', response => {
      if (response.status() >= 400) {
        failedRequests.push({ url: response.url(), status: response.status() })
      }
    })
    await login(page)
    await page.waitForTimeout(3000)
    // Filter out known non-critical endpoints
    const criticalFailures = failedRequests.filter(r => !r.url.includes('analytics'))
    expect(criticalFailures).toHaveLength(0)
  })

})


// ═══════════════════════════════════════════════════
// playwright.config.js (paste into project root)
// ═══════════════════════════════════════════════════
/*
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  retries: 1,
  reporter: 'html',
  use: {
    baseURL: 'https://sarmaan-amr.ehealthnigeria.org',
    headless: true,
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
    { name: 'firefox',  use: { browserName: 'firefox' } },
  ],
})
*/
