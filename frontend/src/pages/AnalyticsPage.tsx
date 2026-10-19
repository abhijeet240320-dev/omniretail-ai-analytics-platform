import { useMemo, useState } from 'react'
import { api } from '../api/client'
import type { QueryResultResponse } from '../api/client'
import { useResource } from '../lib/useResource'
import { sourceColor, sourceLabel } from '../lib/sourceMeta'
import { Badge, Card, PageHeader, SourceChip } from '../components/ui/Card'
import { DataTable } from '../components/ui/DataTable'
import { ResultChart } from '../components/ui/Chart'
import { formatCell } from '../format'
import { EmptyState, Notice, SkeletonTable } from '../components/ui/States'

/**
 * The analytics explorer - one component behind five pages.
 *
 * WHY FIVE PAGES SHARE ONE COMPONENT
 * ---------------------------------
 * The backend's unit of work is a NAMED, VERIFIED QUERY. Forty-one of them
 * exist and each one is a reviewable SQL file. The honest UI for that is a
 * catalogue the user picks from and runs, not five bespoke pages each with its
 * own bespoke fetch and its own bespoke bug. Every number on every analytics
 * page arrives from the same endpoint, is formatted by the same code, and is
 * paginated the same way - so a bug fixed here is fixed on all five.
 *
 * WHAT THE PAGE ADDS OVER THE OLD FLAT BROWSER
 * ---------------------------------------------
 *  - a source filter that starts on the page's OWN sources rather than "all",
 *    so Sales never opens showing Dunnhumby coupons;
 *  - a featured slot that promotes the first query of the page into a chart or
 *    summary, because a wall of 41 identical buttons is not a dashboard;
 *  - per-source counts and colour, so attribution is never ambiguous.
 *
 * The featured query is chosen by the caller, not guessed here, because "which
 * query is the headline for Marketing" is a decision about the data and belongs
 * with the other decisions about each page.
 */

export interface AnalyticsPageConfig {
  title: string
  description: string
  /** Sources this page draws from, in display order. */
  sources: string[]
  /** The query shown in the featured panel. */
  featured: string
  /** Extra catalogued queries listed under "more on this page". */
  related?: string[]
}

const PAGE_SIZE = 50

export function AnalyticsPage({
  config,
  hasKey,
}: {
  config: AnalyticsPageConfig
  hasKey: boolean
}) {
  const catalogue = useResource(() => api.listQueries(), [], { enabled: hasKey })
  const [selected, setSelected] = useState(config.featured)
  const [source, setSource] = useState<string>(config.sources[0] ?? 'all')
  const [filter, setFilter] = useState('')

  const result = useResource(
    () => api.runQuery(selected, PAGE_SIZE),
    [selected, hasKey],
    { enabled: hasKey },
  )

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    return (catalogue.data?.queries ?? []).filter((query) => {
      if (source !== 'all' && query.source !== source) return false
      if (!needle) return true
      return (
        query.name.toLowerCase().includes(needle) ||
        query.question.toLowerCase().includes(needle)
      )
    })
  }, [catalogue.data, source, filter])

  const active = catalogue.data?.queries.find((q) => q.name === selected)

  return (
    <div className="animate-fade-in">
      <PageHeader
        title={config.title}
        description={config.description}
        actions={
          active && <SourceChip source={active.source} />
        }
      />

      {!hasKey && (
        <Notice title="An API key is required">
          Set a key in the top right to run analytics queries.
        </Notice>
      )}

      {hasKey && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[19rem_1fr]">
          <Card
            title="Queries"
            subtitle={`${visible.length} of ${catalogue.data?.total ?? 0} catalogued`}
            className="h-fit xl:sticky xl:top-32"
          >
            <div className="mb-3 space-y-2">
              <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by dataset">
                {config.sources.map((name) => {
                  const count =
                    catalogue.data?.queries.filter((q) => q.source === name).length ?? 0
                  return (
                    <button
                      key={name}
                      type="button"
                      onClick={() => setSource(name)}
                      aria-pressed={source === name}
                      className={`rounded-md border px-2 py-1 text-2xs font-medium transition-colors ${
                        source === name
                          ? 'border-transparent text-ink-800'
                          : 'border-slate-300 bg-slate-100 text-ink-700 hover:border-slate-400'
                      }`}
                      style={
                        source === name
                          ? { backgroundColor: sourceColor(name) }
                          : undefined
                      }
                    >
                      {sourceLabel(name)} ({count})
                    </button>
                  )
                })}
              </div>
              <input
                type="search"
                className="field"
                value={filter}
                placeholder="Filter by name or question"
                aria-label="Filter queries"
                onChange={(event) => setFilter(event.target.value)}
              />
            </div>

            {catalogue.loading && !catalogue.data && <SkeletonTable rows={6} cols={1} />}
            {catalogue.error && (
              <Notice title="The catalogue could not be loaded">{catalogue.error}</Notice>
            )}

            {catalogue.data && (
              <ul className="max-h-[26rem] space-y-1 overflow-y-auto pr-1">
                {visible.map((query) => (
                  <li key={query.name}>
                    <button
                      type="button"
                      onClick={() => setSelected(query.name)}
                      aria-pressed={selected === query.name}
                      className={`w-full rounded-lg border px-2.5 py-2 text-left transition-colors ${
                        selected === query.name
                          ? 'border-gold-500/40 bg-gold-500/[0.07]'
                          : 'border-transparent hover:bg-slate-100'
                      }`}
                    >
                      <span className="flex items-center gap-1.5">
                        <span
                          aria-hidden="true"
                          className="h-1.5 w-1.5 shrink-0 rounded-full"
                          style={{ backgroundColor: sourceColor(query.source) }}
                        />
                        <span className="truncate font-mono text-2xs text-ink-700">
                          {query.name}
                        </span>
                      </span>
                      <span className="mt-1 block text-2xs leading-relaxed text-ink-500">
                        {query.question}
                      </span>
                    </button>
                  </li>
                ))}
                {visible.length === 0 && (
                  <li>
                    <EmptyState
                      title="No query matches"
                      description="Try a different source or clear the filter."
                    />
                  </li>
                )}
              </ul>
            )}
          </Card>

          <div className="min-w-0 space-y-4">
            <ResultPanel result={result.data} loading={result.loading} error={result.error} reload={result.reload} query={selected} question={active?.question} source={active?.source} />
          </div>
        </div>
      )}
    </div>
  )
}

function ResultPanel({
  result,
  loading,
  error,
  reload,
  query,
  question,
  source,
}: {
  result: QueryResultResponse | null
  loading: boolean
  error: string | null
  reload: () => void
  query: string
  question?: string | undefined
  source?: string | undefined
}) {
  return (
    <Card
      title={<span className="font-mono normal-case tracking-normal">{query}</span>}
      subtitle={question}
      actions={
        result && (
          <div className="flex items-center gap-2">
            <Badge tone="info">live</Badge>
            <Badge tone="neutral">{Math.round(result.elapsed_ms)} ms</Badge>
          </div>
        )
      }
    >
      {error && (
        <Notice title="The query did not run">
          {error}
          <div className="mt-2">
            <button type="button" className="btn btn-secondary px-2.5 py-1 text-xs" onClick={reload}>
              Retry
            </button>
          </div>
        </Notice>
      )}

      {loading && !result && <SkeletonTable rows={8} cols={4} />}
      {loading && result && (
        <p className="mb-2 text-2xs text-ink-500">Running against PostgreSQL...</p>
      )}

      {result && !loading && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Badge tone="neutral">
              {result.row_count} of{' '}
              {result.total_rows === null
                ? 'an unmeasured'
                : result.total_rows.toLocaleString('en-US')}{' '}
              rows
            </Badge>
            {result.truncated && result.total_rows !== null && (
              <Badge tone="warn">
                limited to {result.limit};{' '}
                {(result.total_rows - result.row_count).toLocaleString('en-US')}{' '}
                more exist
              </Badge>
            )}
            {result.truncated && result.total_rows === null && (
              <Badge tone="warn">
                limited to {result.limit}; more rows exist (total not measured)
              </Badge>
            )}
            {result.sort_ignored && (
              <Badge tone="warn">sort &quot;{result.sort_ignored}&quot; ignored</Badge>
            )}
          </div>

          {result.sort_ignored && (
            <div className="mb-3">
              <Notice title="The requested sort key was dropped" tone="warn">
                <code>{result.sort_ignored}</code> is not a column this query
                returns. Sending a sort key the statement does not filter on
                would be SQL injection, so the key is ignored rather than
                interpolated - the API says the same thing in the{' '}
                <code>X-Sort-Ignored</code> header.
              </Notice>
            </div>
          )}

          <ResultChart
            columns={result.columns}
            rows={result.rows}
            query={query}
            truncated={result.truncated}
            totalRows={result.total_rows}
            accent={source ? sourceColor(source) : undefined}
          />

          {result.rows.length === 1 && <SingleRowFigures row={result.rows[0] as Record<string, unknown>} />}

          <DataTable
            columns={result.columns}
            rows={result.rows}
            emptyTitle="This query returned no rows"
            emptyDescription="The query ran successfully and the result is genuinely empty."
          />
        </>
      )}

      {!result && !loading && !error && (
        <EmptyState title="Pick a query" description="Choose one from the list to run it." />
      )}
    </Card>
  )
}
/**
 * One row of aggregates, shown as figures rather than as a chart.
 *
 * A headline query like `olist.customer_repeat_behaviour` answers in a single
 * row - eight numbers about the whole customer base. A chart cannot show that:
 * there is no axis, because there is no second point. `planChart` correctly
 * refuses, and without this the page would render a one-row table and no
 * visual at all, which is the least useful way to present the answer to the
 * question the page is named for.
 *
 * Every value here is the API's, formatted by the same `formatCell` the table
 * uses. Nothing is rounded to a "nicer" number, no unit is inferred from the
 * magnitude, and a null stays null - `formatCell` renders that as an em dash
 * rather than as a zero.
 */
function SingleRowFigures({ row }: { row: Record<string, unknown> }) {
  const entries = Object.entries(row)
  if (entries.length === 0) return null
  return (
    <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
      {entries.map(([key, value]) => (
        <div key={key} className="surface p-3">
          <p className="label truncate font-mono text-2xs" title={key}>
            {key}
          </p>
          <p className="mt-1 font-mono text-base font-semibold text-ink-900 tabular-nums">
            {formatCell(value)}
          </p>
        </div>
      ))}
    </div>
  )
}
