/**
 * SARMAAN II — Frontend Test Suite
 * React | Vitest + React Testing Library
 *
 * Install deps:
 *   npm install -D vitest @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom
 *
 * Add to vite.config.js:
 *   test: { environment: 'jsdom', setupFiles: './src/tests/setup.js' }
 *
 * Run:
 *   npx vitest run --reporter=verbose
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import '@testing-library/jest-dom'


// ─────────────────────────────────────────────────────────────
// Replace these imports with your actual component paths
// ─────────────────────────────────────────────────────────────
// import DemographicOverview from '../components/DemographicOverview'
// import QualityChecks from '../components/QualityChecks'
// import DrilldownFilters from '../components/DrilldownFilters'
// import BugReportModal from '../components/BugReportModal'
// import CompletionTable from '../components/CompletionTable'
// import GeospatialView from '../components/GeospatialView'
// import { useDashboardData } from '../hooks/useDashboardData'
// ─────────────────────────────────────────────────────────────


// ═══════════════════════════════════════════════════
// 1. DEMOGRAPHIC OVERVIEW COMPONENT
// ═══════════════════════════════════════════════════

describe('DemographicOverview', () => {

  const mockData = {
    households_planned: 1200,
    households_reached: 960,
    coverage_pct: 80,
    household_members: 4320,
    mothers_caregivers: 890,
    children_0_59m: 1050,
    children_0_28d: 120,
    children_1_59m: 930,
    nasal_samples: 1010,
    rectal_samples: 1005,
    total_samples: 2015,
    research_assistants: 48,
    lga_reached: 5,
    ward_reached: 23,
    settlements_reached: 112
  }

  it('renders all KPI cards', () => {
    render(<DemographicOverview data={mockData} />)
    expect(screen.getByText('1,200')).toBeInTheDocument()   // households_planned
    expect(screen.getByText('960')).toBeInTheDocument()     // households_reached
    expect(screen.getByText('80%')).toBeInTheDocument()     // coverage
  })

  it('shows loading state when data is null', () => {
    render(<DemographicOverview data={null} loading={true} />)
    expect(screen.getByText(/loading/i)).toBeInTheDocument()
  })

  it('shows dashes when data is undefined', () => {
    render(<DemographicOverview data={undefined} />)
    // Should show "—" placeholder, not crash
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThan(0)
  })

  it('formats total_samples correctly', () => {
    render(<DemographicOverview data={mockData} />)
    expect(screen.getByText('2,015')).toBeInTheDocument()
  })

  it('displays coverage percentage colour correctly for low coverage', () => {
    const lowCoverage = { ...mockData, coverage_pct: 30 }
    const { container } = render(<DemographicOverview data={lowCoverage} />)
    const coverageEl = container.querySelector('[data-testid="coverage-pct"]')
    // Expect a warning/red class when coverage is below threshold
    expect(coverageEl).toHaveClass('text-red')
  })

})


// ═══════════════════════════════════════════════════
// 2. DRILLDOWN FILTER PANEL
// ═══════════════════════════════════════════════════

describe('DrilldownFilters', () => {

  it('renders LGA, Ward, Community, and Date dropdowns', () => {
    render(<DrilldownFilters onApply={vi.fn()} onClear={vi.fn()} />)
    expect(screen.getByLabelText(/lga/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/ward/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/community/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/date/i)).toBeInTheDocument()
  })

  it('calls onApply with selected values when Apply is clicked', async () => {
    const user = userEvent.setup()
    const mockApply = vi.fn()
    render(<DrilldownFilters onApply={mockApply} onClear={vi.fn()} lgas={['Sokoto North', 'Wamako']} />)

    await user.selectOptions(screen.getByLabelText(/lga/i), 'Sokoto North')
    await user.click(screen.getByRole('button', { name: /apply/i }))

    expect(mockApply).toHaveBeenCalledWith(expect.objectContaining({ lga: 'Sokoto North' }))
  })

  it('resets all fields when Clear is clicked', async () => {
    const user = userEvent.setup()
    const mockClear = vi.fn()
    render(<DrilldownFilters onApply={vi.fn()} onClear={mockClear} lgas={['Sokoto North']} />)

    await user.selectOptions(screen.getByLabelText(/lga/i), 'Sokoto North')
    await user.click(screen.getByRole('button', { name: /clear/i }))

    expect(mockClear).toHaveBeenCalled()
    expect(screen.getByLabelText(/lga/i)).toHaveValue('All LGAs')
  })

  it('disables Ward dropdown when no LGA is selected', () => {
    render(<DrilldownFilters onApply={vi.fn()} onClear={vi.fn()} />)
    expect(screen.getByLabelText(/ward/i)).toBeDisabled()
  })

  it('enables Ward dropdown after LGA is selected', async () => {
    const user = userEvent.setup()
    render(<DrilldownFilters onApply={vi.fn()} onClear={vi.fn()} lgas={['Wamako']} />)
    await user.selectOptions(screen.getByLabelText(/lga/i), 'Wamako')
    expect(screen.getByLabelText(/ward/i)).not.toBeDisabled()
  })

})


// ═══════════════════════════════════════════════════
// 3. QUALITY CHECKS COMPONENT
// ═══════════════════════════════════════════════════

describe('QualityChecks', () => {

  const mockQuality = {
    health_score: 87,
    duplicate_uuids: 2,
    duplicate_hh_ids: 5,
    duplicate_child_ids: 0,
    duplicate_mother_ids: 1,
    hh_name_short: 3,
    child_name_short: 0,
    blank_child_dob: 7,
    close_dobs: 1,
    missing_username: 0,
    missing_phone: 4,
    wrong_datetime: 2,
    not_validated: 28
  }

  it('renders health score prominently', () => {
    render(<QualityChecks data={mockQuality} />)
    expect(screen.getByText('87')).toBeInTheDocument()
  })

  it('shows all duplicate detection fields', () => {
    render(<QualityChecks data={mockQuality} />)
    expect(screen.getByText(/duplicate uuids/i)).toBeInTheDocument()
    expect(screen.getByText(/duplicate hh ids/i)).toBeInTheDocument()
    expect(screen.getByText(/duplicate child ids/i)).toBeInTheDocument()
  })

  it('highlights non-zero error counts visually', () => {
    const { container } = render(<QualityChecks data={mockQuality} />)
    const duplicateUUIDEl = container.querySelector('[data-testid="duplicate_uuids"]')
    expect(duplicateUUIDEl).toHaveClass('text-red')  // or whatever your warning class is
  })

  it('shows zero errors as green or neutral', () => {
    const { container } = render(<QualityChecks data={mockQuality} />)
    const dupChildEl = container.querySelector('[data-testid="duplicate_child_ids"]')
    expect(dupChildEl).toHaveClass('text-green')
  })

  it('shows "Load quality data" button when data not loaded', () => {
    render(<QualityChecks data={null} />)
    expect(screen.getByRole('button', { name: /load quality data/i })).toBeInTheDocument()
  })

})


// ═══════════════════════════════════════════════════
// 4. BUG REPORT MODAL
// ═══════════════════════════════════════════════════

describe('BugReportModal', () => {

  it('is hidden by default', () => {
    render(<BugReportModal />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('opens when trigger is clicked', async () => {
    const user = userEvent.setup()
    render(<BugReportModal />)
    await user.click(screen.getByText(/report a bug/i))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('closes when × button is clicked', async () => {
    const user = userEvent.setup()
    render(<BugReportModal />)
    await user.click(screen.getByText(/report a bug/i))
    await user.click(screen.getByRole('button', { name: /✕/ }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('does not submit if title is empty', async () => {
    const user = userEvent.setup()
    const mockSubmit = vi.fn()
    render(<BugReportModal onSubmit={mockSubmit} />)

    await user.click(screen.getByText(/report a bug/i))
    await user.click(screen.getByRole('button', { name: /submit/i }))

    expect(mockSubmit).not.toHaveBeenCalled()
    expect(screen.getByText(/title is required/i)).toBeInTheDocument()
  })

  it('submits with valid data', async () => {
    const user = userEvent.setup()
    const mockSubmit = vi.fn()
    render(<BugReportModal onSubmit={mockSubmit} />)

    await user.click(screen.getByText(/report a bug/i))
    await user.type(screen.getByLabelText(/title/i), 'Charts not loading')
    await user.selectOptions(screen.getByLabelText(/severity/i), 'high')
    await user.click(screen.getByRole('button', { name: /submit/i }))

    expect(mockSubmit).toHaveBeenCalledWith(expect.objectContaining({
      title: 'Charts not loading',
      severity: 'high'
    }))
  })

})


// ═══════════════════════════════════════════════════
// 5. COMPLETION TABLE
// ═══════════════════════════════════════════════════

describe('CompletionTable', () => {

  const mockSettlements = [
    { lga: 'Wamako', ward: 'Gidan Dare', community: 'Kalambaina', code: 'W001', planned: 40, reached: 40, status: 'complete' },
    { lga: 'Sokoto North', ward: 'Mabera', community: 'Runjin Sambo', code: 'S002', planned: 35, reached: 18, status: 'incomplete' },
    { lga: 'Kware', ward: 'Kware Central', community: 'Tudun Wada', code: 'K003', planned: 50, reached: 0, status: 'not_started' }
  ]

  it('renders all settlements', () => {
    render(<CompletionTable settlements={mockSettlements} />)
    expect(screen.getByText('Kalambaina')).toBeInTheDocument()
    expect(screen.getByText('Runjin Sambo')).toBeInTheDocument()
    expect(screen.getByText('Tudun Wada')).toBeInTheDocument()
  })

  it('sorts complete settlements first when that option is selected', async () => {
    const user = userEvent.setup()
    render(<CompletionTable settlements={mockSettlements} />)
    await user.click(screen.getByText(/complete first/i))
    const rows = screen.getAllByRole('row')
    // First data row should be the complete one
    expect(rows[1]).toHaveTextContent('Kalambaina')
  })

  it('exports CSV when button is clicked', async () => {
    const user = userEvent.setup()
    const mockExport = vi.fn()
    render(<CompletionTable settlements={mockSettlements} onExport={mockExport} />)
    await user.click(screen.getByText(/csv/i))
    expect(mockExport).toHaveBeenCalled()
  })

})


// ═══════════════════════════════════════════════════
// 6. DASHBOARD DATA HOOK
// ═══════════════════════════════════════════════════

describe('useDashboardData hook', () => {

  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('starts with loading state', async () => {
    fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ households_planned: 100 })
    })

    const { result } = renderHook(() => useDashboardData())
    expect(result.current.loading).toBe(true)
  })

  it('sets data after successful fetch', async () => {
    const mockResponse = { households_planned: 1200, households_reached: 960 }
    fetch.mockResolvedValueOnce({
      ok: true,
      json: async () => mockResponse
    })

    const { result } = renderHook(() => useDashboardData())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data.households_planned).toBe(1200)
  })

  it('sets error state on fetch failure', async () => {
    fetch.mockRejectedValueOnce(new Error('Network error'))

    const { result } = renderHook(() => useDashboardData())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBeTruthy()
  })

})
