import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, setApiKey } from './client'
import type { AnomaliesResponse, ForecastResponse, OverviewResponse } from './client'

/**
 * Contract tests for the three endpoints the dashboard reads.
 *
 * As in `client.test.ts`, the fixtures are RECORDED RESPONSES captured from the
 * running API against the live database, not shapes invented to match the
 * types. A fixture written to fit the type proves nothing.
 *
 * What these catch specifically:
 *   - the frontend calling a path that does not exist (a silent 404 on a page);
 *   - a filter silently ignored, so a user picks "M5" and gets all sources;
 *   - a number the UI would render being absent from the payload.
 */

const FORECAST: ForecastResponse = {
  generated_utc: '2026-10-03T19:34:14.936840+00:00',
  dataset: 'm5.sales_long',
  scope: {
    series_evaluated: 30490,
    series_total_in_database: 30490,
    days_loaded: 1941,
    is_full_dataset: true,
  },
  split: {
    train_d_num: '1..1913',
    test_d_num: '1914..1941',
    test_days: 28,
    rationale: 'The 28-day test window matches the 28 forecast-horizon days.',
  },
  intermittency: {
    'sporadic (>=80% zeros)': { series: 11783, zero_rate_range: [0.8, 1] },
  },
  models: {
    sma_7: {
      model: 'sma_7',
      description: 'Mean of the last 7 observed days, held flat.',
      MAE: 1.0683,
      RMSE: 2.2487,
      MASE: 1.6717,
      WMAPE: 0.7404,
      bias: -0.1062,
      total_actual: 1231764,
      total_forecast: 1141116,
    },
  },
  winner_by_mase: 'sma_7',
  margin_over_runner_up: 0.0147,
  elapsed_seconds: { load: 283.03, evaluate: 9.05 },
  shape: 'aggregate model-selection benchmark; no per-series point forecast is stored',
  source_artefact: 'data/processed/_forecast_results.json',
}

const ANOMALIES: AnomaliesResponse = {
  generated_utc: '2026-10-03T19:42:05.305667+00:00',
  method_note: 'Every anomaly carries its own baseline and threshold.',
  source_artefact: 'data/processed/_anomaly_results.json',
  known_events: [{ date: '2015-12-25', name: 'Christmas Day' }],
  metrics: [
    {
      source: 'm5',
      metric: 'total_units_per_day',
      method: 'rolling mean and standard deviation, 56-day lookback',
      baseline: 'mean of the previous 56 days',
      points_evaluated: 1913,
      anomalies_found: 1,
      firing_rate_pct: 0.0523,
      false_positive_caveat: 'no labelled ground truth',
      anomalies: [],
      notes: [],
    },
  ],
  metrics_count: 1,
  total: 1,
  limit: 50,
  offset: 0,
  rows: [
    {
      source: 'm5',
      metric: 'total_units_per_day',
      date: '2015-12-25',
      observed: 59635,
      baseline: 20343,
      threshold: 4021,
      score_sigma: 12.8,
      direction: 'above',
      severity: 'critical',
      method: 'rolling mean and standard deviation, 56-day lookback',
      baseline_definition: 'mean of the previous 56 days',
      time_axis: 'calendar date',
    },
  ],
  caveat: 'A firing point means unusual relative to its own recent history.',
}

const OVERVIEW: OverviewResponse = {
  generated_at: '2026-10-03T21:24:22.341387Z',
  cached: false,
  age_seconds: 0,
  sources: [
    {
      source: 'olist',
      label: 'Olist - Brazilian marketplace',
      metrics: {
        orders: 99441,
        revenue: 16008872.12,
        coverage_start: '2016-09-01',
        coverage_end: '2018-08-31',
      },
      note: null,
    },
  ],
  disclaimer: 'No combined total is provided, by design.',
}

function stubFetch(body: unknown, ok = true, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok,
    status,
    statusText: ok ? 'OK' : 'Error',
    json: async () => body,
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('the recorded payloads satisfy the hand-written types', () => {
  it('forecasting', () => {
    expect(FORECAST.scope?.is_full_dataset).toBe(true)
    expect(FORECAST.winner_by_mase).toBeTruthy()
    // The page computes a "sporadic share" from these two; if either were
    // renamed the KPI card would silently render 0%.
    // The recorded run reports 11,783 sporadic series out of 30,490. If the
    // band key were renamed the KPI card would divide by a missing key and
    // render 0% instead of failing.
    expect(FORECAST.intermittency?.['sporadic (>=80% zeros)']?.series).toBe(11783)
  })

  it('anomalies', () => {
    expect(ANOMALIES.rows[0]?.observed).toBeGreaterThan(ANOMALIES.rows[0]!.baseline)
    expect(ANOMALIES.rows[0]?.time_axis).toBe('calendar date')
  })

  it('overview carries a coverage range per source', () => {
    const metrics = OVERVIEW.sources[0]!.metrics
    expect(metrics.coverage_start).toBe('2016-09-01')
    expect(metrics.coverage_end).toBe('2018-08-31')
  })
})

describe('endpoint wiring', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    // Vitest's default environment is `node`, which has no sessionStorage.
    // `client.ts` guards with `typeof sessionStorage === 'undefined'`, so
    // without a stub the key path is skipped entirely and the header assertion
    // below would pass for the wrong reason.
    const session = new Map<string, string>()
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => session.get(key) ?? null,
      setItem: (key: string, value: string) => void session.set(key, value),
      removeItem: (key: string) => void session.delete(key),
    })
  })

  it('forecasting GETs /api/forecasting', async () => {
    const fetchMock = stubFetch(FORECAST)
    await api.forecasting()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/forecasting',
      expect.objectContaining({ headers: expect.any(Object) }),
    )
  })

  it('anomalies sends the source filter as a query parameter', async () => {
    const fetchMock = stubFetch(ANOMALIES)
    await api.anomalies({ source: 'm5', limit: 25 })
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/anomalies?source=m5&limit=25')
  })

  it('anomalies omits a filter that is not set rather than sending an empty one', async () => {
    const fetchMock = stubFetch(ANOMALIES)
    await api.anomalies({ limit: 10 })
    // `source=` with an empty value would be a different request to a validating
    // backend; the parameter has to be absent, not blank.
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/anomalies?limit=10')
  })

  it('anomalies with no arguments requests the default page', async () => {
    const fetchMock = stubFetch(ANOMALIES)
    await api.anomalies()
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/anomalies')
  })

  it('runQuery sends both limit and offset so a table can page', async () => {
    const fetchMock = stubFetch({})
    await api.runQuery('olist.monthly_revenue_trend', 50, 100)
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/analytics/queries/olist.monthly_revenue_trend?limit=50&offset=100',
    )
  })

  it('sends the API key as a header and never in the URL', async () => {
    const fetchMock = stubFetch(FORECAST)
    setApiKey('omr_test_key')
    await api.forecasting()
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    // A key in a URL is a key in access.log, in Referer and in history.
    expect(url).not.toContain('omr_test_key')
    expect((init.headers as Record<string, string>)['X-API-Key']).toBe('omr_test_key')
    setApiKey('')
  })

  it('raises a typed error carrying the status and detail', async () => {
    stubFetch({ detail: 'missing X-API-Key header' }, false, 401)
    await expect(api.overview()).rejects.toBeInstanceOf(ApiError)
    try {
      await api.overview()
    } catch (error) {
      expect((error as ApiError).status).toBe(401)
      expect((error as ApiError).detail).toBe('missing X-API-Key header')
    }
  })
})