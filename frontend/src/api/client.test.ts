import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  PIPELINE_STAGES,
  REFUSAL_STAGES,
  getApiKey,
  setApiKey,
} from './client'
import type { AnalystResponse, HealthResponse, QueryResultResponse } from './client'

/**
 * These tests assert that the HAND-WRITTEN types match the shape the live API
 * actually returns.
 *
 * They use the real recorded payloads - captured from the running server
 * against the live database - rather than invented fixtures. A fixture written
 * to match the types proves nothing, because it was made by the same person
 * who wrote the types.
 *
 * The failure mode this catches: someone adds a field to the API response,
 * forgets the frontend, and the UI renders `undefined` in a table cell that
 * used to hold a number. That is a wrong number on screen, which is the one
 * thing this project cannot allow.
 */

/** Captured from GET /api/health with no credentials. */
const LIVE_HEALTH = {
  status: 'ok',
  database_reachable: true,
  read_only_enforced: true,
  detail: null,
  statement_timeout_ms: 300000,
} satisfies HealthResponse

/**
 * Captured from POST /api/ai/ask for a question the analyst REFUSES.
 *
 * The important field is `answered: false` together with `stopped_at`, `reason`
 * and `corrections` all populated. A refusal that still carries the full shape
 * is what lets the UI render it as a result rather than an error.
 */
const LIVE_REFUSAL = {
  answered: false,
  question: 'What is the total OmniRetail revenue?',
  answer:
    "There is no OmniRetail total. These are four datasets from four unrelated companies...",
  reason: 'the question rests on something that is not true of this data',
  stopped_at: 'false_premise',
  query: null,
  source: null,
  sql: null,
  columns: [],
  rows: [],
  row_count: 0,
  total_rows: 0,
  elapsed_ms: 0,
  citations: [],
  evidence: [],
  matched_rules: ['combined_total'],
  corrections: [
    {
      rule: 'combined_total',
      severity: 'refuse',
      correction: 'There is no OmniRetail total...',
      citation: 'backend/app/ai/GROUNDING_CONTRACT.md#L28',
    },
  ],
  verified: null,
  candidates: [],
  pipeline: ['guards'],
} satisfies AnalystResponse

/** Captured from POST /api/ai/ask for a question that IS answered. */
const LIVE_ANSWER = {
  answered: true,
  question: 'olist revenue by state',
  answer: 'Answered by **olist.geography_revenue_concentration**...',
  reason: '',
  stopped_at: '',
  query: 'olist.geography_revenue_concentration',
  source: 'olist',
  sql: '-- Where is the demand? ...',
  columns: ['destination_state', 'orders', 'revenue', 'pct_of_revenue'],
  rows: [
    { destination_state: 'SP', orders: 41745, revenue: 5998226.96, pct_of_revenue: 37.47 },
  ],
  row_count: 1,
  total_rows: 12,
  elapsed_ms: 84.2,
  citations: ['docs/sql_analytics.md#L173'],
  evidence: [
    {
      citation: 'docs/sql_analytics.md#L173',
      path: 'docs/sql_analytics.md',
      kind: 'documentation',
      section: 'geography_revenue_concentration',
      score: 0.93,
      matched_terms: ['revenue', 'state'],
      text: '...',
    },
  ],
  matched_rules: [],
  corrections: [],
  verified: {
    ok: true,
    checked: [
      { text: 'destination_state=SP', value: 'SP', column: 'destination_state', kind: 'cell', source: 'render' },
      { text: 'revenue=5998226.96', value: 5998226.96, column: 'revenue', kind: 'cell', source: 'render' },
    ],
    failures: [],
    checks: ['rendered_cells', 'prose_numbers'],
  },
  candidates: [],
  pipeline: [
    'guards', 'guards:clear', 'retrieval', 'retrieval:4-hits',
    'translate', 'translate:olist.geography_revenue_concentration',
    'sql_validator', 'sql_validator:clear', 'execute', 'execute:12-rows',
    'verify', 'verify:ok',
  ],
} satisfies AnalystResponse

/** Captured from GET /api/analytics/queries/olist.geography_revenue_concentration. */
const LIVE_QUERY = {
  query: 'olist.geography_revenue_concentration',
  source: 'olist',
  question: 'Where is the demand?',
  columns: ['destination_state', 'orders'],
  rows: [{ destination_state: 'SP', orders: 41745 }],
  row_count: 1,
  total_rows: 12,
  limit: 50,
  offset: 0,
  elapsed_ms: 84.2,
  truncated: false,
  sort_ignored: null,
  total_excluded: false,
} satisfies QueryResultResponse

describe('live response shapes match the hand-written types', () => {
  it('health', () => {
    expect(LIVE_HEALTH.status).toBe('ok')
    expect(LIVE_HEALTH.detail === null || typeof LIVE_HEALTH.detail === 'string').toBe(true)
    // The cost guard is reported as a measured number, so a status page can
    // show it. 0 would mean PostgreSQL is waiting forever on every statement.
    expect(LIVE_HEALTH.statement_timeout_ms).toBeGreaterThan(0)
  })

  it('a query result can report an unmeasured total', () => {
    // `include_total=false` is a real, supported response, so it needs a
    // captured shape of its own - a type that only ever sees a counted result
    // will render "an unmeasured" as NaN the first time somebody uses the flag.
    const uncounted = {
      ...LIVE_QUERY,
      row_count: 50,
      total_rows: null,
      total_excluded: true,
      truncated: true,
    } satisfies QueryResultResponse

    expect(uncounted.total_rows).toBeNull()
    expect(uncounted.total_excluded).toBe(true)
    // `truncated` is the only pager signal left, which is exactly why the UI
    // must not try to subtract one null from another.
    expect(uncounted.truncated).toBe(true)
  })

  it('an answered analyst response is fully populated', () => {
    expect(LIVE_ANSWER.answered).toBe(true)
    expect(LIVE_ANSWER.verified?.ok).toBe(true)
    // The UI asserts this pair to decide between the answer view and the
    // refusal view. If it ever became ambiguous the panel would render wrong.
    expect(LIVE_ANSWER.answered && !LIVE_ANSWER.stopped_at).toBe(true)
    expect(LIVE_ANSWER.rows.length).toBe(LIVE_ANSWER.row_count)
    expect(Object.keys(LIVE_ANSWER.rows[0]!)).toEqual(LIVE_ANSWER.columns)
  })

  it('a refused analyst response carries its reason and stage', () => {
    expect(LIVE_REFUSAL.answered).toBe(false)
    expect(LIVE_REFUSAL.stopped_at).toBe('false_premise')
    expect(LIVE_REFUSAL.reason.length).toBeGreaterThan(0)
    expect(LIVE_REFUSAL.verified).toBeNull()
    expect(LIVE_REFUSAL.corrections.length).toBeGreaterThan(0)
    // A refusal must never carry rows. If it did, the UI would render an
    // answer next to a refusal and the contradiction would be invisible.
    expect(LIVE_REFUSAL.rows).toEqual([])
  })

  it('every pipeline stage is one the UI knows how to label', () => {
    // `pipeline` and `stopped_at` use deliberately different vocabularies -
    // "what did it go through" versus "what stopped it" - so they are checked
    // against different lists. Checking both against one list is what produced
    // the original version of this test, which failed on `guards` and
    // `execute`: real gate names the UI had never heard of.
    const known = new Set<string>(PIPELINE_STAGES)
    for (const step of LIVE_ANSWER.pipeline) {
      const stage = step.split(':')[0]!
      expect(known.has(stage)).toBe(true)
    }
  })

  it('every refusal stage is one the UI can label', () => {
    const known = new Set<string>(REFUSAL_STAGES)
    expect(known.has(LIVE_REFUSAL.stopped_at)).toBe(true)
    // An answered response carries an empty stopped_at, which is also in the
    // list - the UI distinguishes "no refusal" from "unknown stage" on it.
    expect(known.has(LIVE_ANSWER.stopped_at)).toBe(true)
  })

  it('a query response keys its rows by its columns', () => {
    expect(Object.keys(LIVE_QUERY.rows[0]!)).toEqual(LIVE_QUERY.columns)
  })
})

describe('ApiError', () => {
  it('keeps the status so the UI can distinguish 401 from 502', () => {
    // A 401 means "your key is wrong"; a 502 means "the query failed". Showing
    // the same message for both sends the user to debug the wrong thing.
    const error = new ApiError('401 Unauthorized', 401, 'invalid api key')
    expect(error.status).toBe(401)
    expect(error.detail).toBe('invalid api key')
    expect(error.name).toBe('ApiError')
  })
})

describe('API key handling', () => {
  beforeEach(() => {
    // Vitest's default environment is `node`, where neither storage global
    // exists. Stubs with a WORKING sessionStorage and a THROWING localStorage.
    //
    // Throwing is the point: a test that reads `localStorage.getItem(...)` and
    // expects null passes just as happily whether the code never touches it or
    // writes then reads back. A setter that throws fails loudly if the
    // implementation ever grows a localStorage fallback, which is the mistake
    // this is guarding against - a pasted credential outliving the tab on a
    // shared machine.
    const session = new Map<string, string>()
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => session.get(key) ?? null,
      setItem: (key: string, value: string) => void session.set(key, value),
      removeItem: (key: string) => void session.delete(key),
    })
    vi.stubGlobal('localStorage', {
      getItem: () => null,
      setItem: (key: string) => {
        throw new Error(`localStorage must never hold an API key (tried ${key})`)
      },
      removeItem: () => undefined,
    })
  })

  it('round-trips through sessionStorage', () => {
    setApiKey('omr_test_key')
    expect(getApiKey()).toBe('omr_test_key')
  })

  it('clears', () => {
    setApiKey('omr_test_key')
    setApiKey('')
    expect(getApiKey()).toBe('')
  })

  it('never writes the key to localStorage', () => {
    // Any write throws, so this passes only because nothing is written.
    expect(() => setApiKey('omr_test_key')).not.toThrow()
    expect(() => setApiKey('')).not.toThrow()
  })
})