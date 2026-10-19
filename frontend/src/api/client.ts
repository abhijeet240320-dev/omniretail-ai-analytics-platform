/**
 * Typed client for the OmniRetail API.
 *
 * WHY THE TYPES ARE HAND-WRITTEN AND NOT GENERATED
 * ------------------------------------------------
 * OpenAPI generates a client too, and it would drift: the point of these types
 * is that a change to the API's SHAPE breaks the build here rather than
 * producing `undefined` at runtime. They are asserted against the live app by
 * `src/api/client.test.ts`, which runs the real server's responses through
 * these types.
 *
 * WHERE THE API KEY LIVES
 * -----------------------
 * In memory, and optionally in `sessionStorage`. Never in the bundle, never in
 * `localStorage`, never in a source file.
 *
 *   - a key in the bundle is a key in git history
 *   - `localStorage` survives on a shared machine indefinitely
 *   - `sessionStorage` dies with the tab, which is the right lifetime for a
 *     credential someone pasted to look at a dashboard
 *
 * The key is sent as `X-API-Key` and is never put in a URL. A key in a path is
 * a key in `access.log`, in `Referer` headers and in browser history.
 */

export interface QuerySummary {
  name: string
  source: string
  question: string
  file: string
}

export interface QueryListResponse {
  total: number
  sources: string[]
  queries: QuerySummary[]
}

export interface QueryResultResponse {
  query: string
  source: string
  question: string
  columns: string[]
  rows: Array<Record<string, unknown>>
  row_count: number
  /**
   * null when the caller passed `include_total=false`, which skips the
   * unpaginated count - on the m5 aggregates that count costs as much as the
   * page itself. null means "not measured"; it is never a stand-in for
   * `row_count`, and this page must not render it as a number.
   */
  total_rows: number | null
  total_excluded: boolean
  limit: number
  offset: number
  elapsed_ms: number
  truncated: boolean
  sort_ignored: string | null
}

export interface HealthResponse {
  status: string
  database_reachable: boolean
  read_only_enforced: boolean
  detail: string | null
  /**
   * The statement timeout a live read-only connection reports, in ms. Measured
   * by the API rather than copied from configuration, so it is evidence the
   * query cost guard is actually armed. 0 means unlimited.
   */
  statement_timeout_ms: number | null
}

/** One source's KPI block from /api/overview. Never summed across sources. */
export interface SourceKpi {
  source: string
  label: string
  metrics: Record<string, number | string | null>
  note: string | null
}

export interface OverviewResponse {
  /**
   * When these numbers were computed. NOT when this response was served - a
   * cached response carries the timestamp of the computation it reused.
   */
  generated_at: string
  sources: SourceKpi[]
  disclaimer: string
  /**
   * True when the API reused its cached result. The overview is 26 metric
   * queries over tables with 59 million rows and takes about 80 seconds cold,
   * so it is reused for a short window - and the reuse is always visible here.
   */
  cached: boolean
  age_seconds: number
}

export interface TableInfo {
  schema_name: string
  table_name: string
  rows: number
  columns: number
}

export interface DatabaseStatus {
  database: string
  reachable: boolean
  role: string
  is_select_only: boolean
  role_default_read_only: boolean
  alembic_revision: string | null
  table_count: number
  total_rows: number
  tables: TableInfo[]
}

/**
 * /api/forecasting - the recorded M5 benchmark, served verbatim.
 *
 * The response is deliberately typed as an open shape rather than a fixed
 * interface: it is the contents of `_forecast_results.json`, which is written by
 * a long batch run. A hand-maintained interface over that file would be a
 * second thing to keep in step, and it would break the moment the run writes a
 * new model. The fields the UI actually reads are named below; the rest passes
 * through and is ignored rather than silently dropped.
 *
 * `shape` and `source_artefact` are added by the endpoint so the UI can say
 * what it is looking at without hardcoding the filename itself.
 */
export interface ForecastResponse {
  generated_utc?: string
  dataset?: string
  is_full_dataset?: boolean
  scope?: {
    series_evaluated: number
    series_total_in_database: number
    days_loaded: number
    is_full_dataset: boolean
  }
  split?: {
    train_d_num: string
    test_d_num: string
    test_days: number
    rationale: string
  }
  intermittency?: Record<
    string,
    { series: number; zero_rate_range: [number, number] }
  >
  models?: Record<
    string,
    {
      model: string
      description: string
      MAE: number
      RMSE: number
      MASE: number
      WMAPE: number
      bias: number
      total_actual: number
      total_forecast: number
    }
  >
  model_descriptions?: Record<string, string>
  per_band?: Record<string, Record<string, Record<string, number>>>
  winner_by_mase?: string
  margin_over_runner_up?: number
  elapsed_seconds?: Record<string, number>
  shape?: string
  source_artefact?: string
}

export interface AnomalyRow {
  source: string
  metric: string
  date: string
  observed: number
  baseline: number
  threshold: number
  score_sigma: number
  direction: string
  severity: string
  method: string
  baseline_definition: string
  /** "calendar date" or "integer index - not a date". Never assumed. */
  time_axis: string
}

export interface AnomalyMetricBlock {
  source: string
  metric: string
  method: string
  baseline: string
  points_evaluated: number
  anomalies_found: number
  firing_rate_pct: number
  false_positive_caveat: string
  anomalies: Array<Record<string, unknown>>
  notes: string[]
}

export interface AnomaliesResponse {
  generated_utc: string | null
  method_note: string | null
  source_artefact: string
  known_events: Array<{ date: string; name: string }>
  metrics: AnomalyMetricBlock[]
  metrics_count: number
  /** Number of individual firings, which is NOT `metrics_count`. */
  total: number
  limit: number
  offset: number
  rows: AnomalyRow[]
  caveat: string
}

export interface ValidatorStatusResponse {
  total: number
  validator_ok: number
  executable: number
  failures: Array<Record<string, unknown>>
}

/**
 * The gate that refused, as reported in `stopped_at`.
 *
 * Deliberately a different vocabulary from `pipeline`: `stopped_at` answers
 * "what stopped this?" and `pipeline` answers "what did it go through?".
 * Keeping them separate stopped a bug where an empty `stopped_at` on an
 * answered response could be confused with a stage that had not been reached.
 */
export const REFUSAL_STAGES = [
  'input',
  'false_premise',
  'evidence',
  'translation',
  'sql_validator',
  'execution',
  'verification',
  'retrieval',
  '',
] as const

export type Stage = (typeof REFUSAL_STAGES)[number]

/**
 * The gates the pipeline trace reports, WITHOUT the `detail` suffix.
 *
 * `pipeline` entries look like `guards:clear` or `execute:12-rows`; this is
 * the part before the colon. Exported because the backend and the frontend
 * have to agree on these names, and two hand-maintained lists that drift
 * produce a UI that shows a raw identifier to a user - which is exactly what
 * `client.test.ts` checks for.
 */
export const PIPELINE_STAGES = [
  'guards',
  'retrieval',
  'translate',
  'sql_validator',
  'execute',
  'verify',
  'quote',
] as const

export type PipelineStage = (typeof PIPELINE_STAGES)[number]

export interface VerificationDetail {
  ok: boolean
  checked: Array<Record<string, unknown>>
  failures: Array<Record<string, string>>
  checks?: string[]
}

export interface Correction {
  rule: string
  severity: string
  correction: string
  citation: string
}

export interface AnalystResponse {
  /**
   * False means a refusal. The request succeeded; the answer is "no".
   *
   * This is a NORMAL response and the UI renders it as one - with the reason
   * and the stage, in full - rather than as an error. A refusal rendered as an
   * error banner teaches users that the system is broken, and it is the single
   * most common outcome of asking a well-formed question.
   */
  answered: boolean
  question: string
  answer: string
  reason: string
  stopped_at: Stage
  query: string | null
  source: string | null
  sql: string | null
  columns: string[]
  rows: Array<Record<string, unknown>>
  row_count: number
  total_rows: number
  elapsed_ms: number
  citations: string[]
  evidence: Array<Record<string, unknown>>
  matched_rules: string[]
  corrections: Correction[]
  verified: VerificationDetail | null
  candidates: Array<Record<string, unknown>>
  pipeline: string[]
}

export interface CandidateQuery {
  query: string
  source: string
  question: string
  score: number
  reasons: string[]
}

export interface TranslateResponse {
  ok: boolean
  question: string
  query: string | null
  sql: string | null
  reason: string
  sources_named: string[]
  sources_required: string[]
  notes: string[]
  candidates: CandidateQuery[]
  validation: { ok: boolean; violations: Array<Record<string, string>> } | null
}

export interface ValidateResponse {
  ok: boolean
  sql: string
  tables: string[]
  source_schemas: string[]
  violations: Array<Record<string, string>>
  explanation: string
}

export interface SearchHit {
  citation: string
  path: string
  kind: string
  section: string
  score: number
  matched_terms: string[]
  text: string
}

export interface SearchResponse {
  supported: boolean
  question: string
  corpus_chunks: number
  corpus_documents: number
  hits: SearchHit[]
  citations: string[]
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: string | null,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

const KEY_STORAGE = 'omniretail.apiKey'
let inMemoryKey = ''

export function getApiKey(): string {
  if (inMemoryKey) return inMemoryKey
  if (typeof sessionStorage === 'undefined') return ''
  return sessionStorage.getItem(KEY_STORAGE) ?? ''
}

export function setApiKey(value: string): void {
  inMemoryKey = value
  if (typeof sessionStorage === 'undefined') return
  if (value) sessionStorage.setItem(KEY_STORAGE, value)
  else sessionStorage.removeItem(KEY_STORAGE)
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((init.headers as Record<string, string>) ?? {}),
  }
  const key = getApiKey()
  if (key) headers['X-API-Key'] = key

  const response = await fetch(path, { ...init, headers })

  if (!response.ok) {
    // The API's own 500 handler returns {error, detail} and deliberately omits
    // the traceback. Surfacing `detail` verbatim is safe because it was built
    // to be surfaced.
    let detail: string | null = null
    try {
      const body = (await response.json()) as { detail?: string }
      detail = body.detail ?? null
    } catch {
      detail = null
    }
    throw new ApiError(
      `${response.status} ${response.statusText}`,
      response.status,
      detail,
    )
  }
  return (await response.json()) as T
}

export const api = {
  health: () => request<HealthResponse>('/api/health'),

  status: () => request<DatabaseStatus>('/api/status'),

  overview: () => request<OverviewResponse>('/api/overview'),

  forecasting: () => request<ForecastResponse>('/api/forecasting'),

  anomalies: (params: {
    // `| undefined` on every key: callers legitimately pass `undefined` for a
    // filter they are not setting, and exactOptionalPropertyTypes treats that
    // as different from omitting the key.
    source?: string | undefined
    severity?: string | undefined
    limit?: number | undefined
    offset?: number | undefined
  } = {}) => {
    const q = new URLSearchParams()
    if (params.source) q.set('source', params.source)
    if (params.severity) q.set('severity', params.severity)
    if (params.limit !== undefined) q.set('limit', String(params.limit))
    if (params.offset !== undefined) q.set('offset', String(params.offset))
    const suffix = q.toString()
    return request<AnomaliesResponse>(`/api/anomalies${suffix ? `?${suffix}` : ''}`)
  },

  listQueries: (source?: string) =>
    request<QueryListResponse>(
      `/api/analytics/queries${source ? `?source=${encodeURIComponent(source)}` : ''}`,
    ),

  runQuery: (name: string, limit = 50, offset = 0) =>
    request<QueryResultResponse>(
      `/api/analytics/queries/${encodeURIComponent(name)}` +
        `?limit=${limit}&offset=${offset}`,
    ),

  ask: (question: string) =>
    request<AnalystResponse>('/api/ai/ask', {
      method: 'POST',
      body: JSON.stringify({ question }),
    }),

  askDocument: (question: string) =>
    request<AnalystResponse>('/api/ai/ask/document', {
      method: 'POST',
      body: JSON.stringify({ question }),
    }),

  translate: (question: string) =>
    request<TranslateResponse>('/api/ai/translate', {
      method: 'POST',
      body: JSON.stringify({ question }),
    }),

  validate: (sql: string) =>
    request<ValidateResponse>('/api/ai/validate', {
      method: 'POST',
      body: JSON.stringify({ sql }),
    }),

  search: (question: string, limit = 5) =>
    request<SearchResponse>('/api/ai/search', {
      method: 'POST',
      body: JSON.stringify({ question, limit }),
    }),
}