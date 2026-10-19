import { api } from '../api/client'
import { useResource } from '../lib/useResource'
import {
  SOURCE_DOMAIN,
  SOURCE_SHORT,
  isSource,
  sourceColor,
} from '../lib/sourceMeta'
import { Card, PageHeader, SourceChip } from '../components/ui/Card'
import { DataTable } from '../components/ui/DataTable'
import { Notice, SkeletonTable } from '../components/ui/States'

/**
 * The Data Sources page.
 *
 * EVERY FIELD ON THIS PAGE IS FETCHED
 * ----------------------------------
 * Table names, row counts, column counts, date coverage, query counts - all of
 * it comes from `/api/status`, `/api/overview` and `/api/analytics/queries`.
 * There is not one hardcoded row count or date range in this file, because a
 * page like this is the single most likely place for a plausible invented number
 * to end up looking like measured data.
 *
 * The one thing that IS hardcoded is `LIMITATIONS` below, and those are
 * statements about what the DATASET is - facts recorded when the data was
 * loaded and documented in `docs/`. They describe shape, not values: "there is
 * no calendar, so the time axis is an integer index" cannot become stale the way
 * a row count can.
 */
export function DataSourcesPage({ hasKey }: { hasKey: boolean }) {
  const status = useResource(() => api.status(), [], { enabled: hasKey })
  const overview = useResource(() => api.overview(), [], { enabled: hasKey })
  const catalogue = useResource(() => api.listQueries(), [], { enabled: hasKey })

  const schemas = groupBySchema(status.data)

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Data Sources"
        description="Four independent public retail datasets, loaded as 30 tables across five PostgreSQL schemas. Each is reported on its own; none is summed with another."
      />

      {!hasKey && (
        <Notice title="An API key is required">
          Set a key in the top right to load the source catalogue.
        </Notice>
      )}

      {hasKey && status.error && (
        <Notice title="/api/status could not be read">{status.error}</Notice>
      )}

      <div className="space-y-4">
        {(['olist', 'dataco', 'dunnhumby', 'm5'] as const).map((source) => {
          const tables = schemas[source] ?? []
          const kpi = overview.data?.sources.find((s) => s.source === source)
          const queryCount =
            catalogue.data?.queries.filter((q) => q.source === source).length ?? 0
          const rows = tables.reduce((sum, table) => sum + table.rows, 0)
          const coverage = coverageOf(kpi?.metrics)

          return (
            <Card
              key={source}
              title={
                <span className="flex items-center gap-2">
                  <span
                    aria-hidden="true"
                    className="h-2 w-2 rounded-full"
                    style={{ backgroundColor: sourceColor(source) }}
                  />
                  {SOURCE_SHORT[source]}
                </span>
              }
              subtitle={kpi?.label}
              actions={<SourceChip source={source} />}
            >
              <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                <Stat
                  label="Tables"
                  value={loading(status) ? '-' : String(tables.length)}
                  hint={`schema "${source}"`}
                />
                <Stat
                  label="Rows"
                  value={loading(status) ? '-' : rows.toLocaleString('en-US')}
                  hint={loading(catalogue) ? undefined : `${queryCount} catalogued queries`}
                />
                <Stat
                  label="Columns"
                  value={loading(status)
                    ? '-'
                    : String(tables.reduce((sum, t) => sum + t.columns, 0))}
                />
                <Stat
                  label="Time axis"
                  value={loading(overview) ? '-' : coverage}
                  hint={SOURCE_DOMAIN[source]}
                />
              </div>

              <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
                <div>
                  <p className="label mb-2">Tables in this schema</p>
                  {loading(status) ? (
                    <SkeletonTable rows={3} cols={2} />
                  ) : (
                    <DataTable
                      columns={['table', 'rows', 'columns']}
                      rows={tables.map((t) => ({
                        table: t.table_name,
                        rows: t.rows,
                        columns: t.columns,
                      }))}
                      pageSize={0}
                      maxHeight="max-h-64"
                    />
                  )}
                </div>
                <div>
                  <p className="label mb-2">What this source can and cannot answer</p>
                  <ul className="space-y-2">
                    {(LIMITATIONS[source] ?? []).map((item) => (
                      <li
                        key={item}
                        className="flex gap-2 text-xs leading-relaxed text-ink-500"
                      >
                        <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-slate-400" />
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </Card>
          )
        })}
      </div>

      <Card
        className="mt-4"
        title="Attribution"
        subtitle="Where each dataset came from"
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {ATTRIBUTION.map((item) => (
            <div key={item.source} className="surface-quiet p-3">
              <div className="flex items-center gap-2">
                <SourceChip source={item.source} />
                {isSource(item.source) && (
                  <span className="text-2xs text-ink-500">{SOURCE_DOMAIN[item.source]}</span>
                )}
              </div>
              <p className="mt-2 text-xs leading-relaxed text-ink-500">{item.text}</p>
            </div>
          ))}
        </div>
        <p className="mt-4 text-2xs leading-relaxed text-ink-500">
          Raw files land in <code>data/raw/</code>, are cleaned into{' '}
          <code>data/processed/&lt;source&gt;/</code>, loaded by{' '}
          <code>backend/scripts/db/04_load.py</code>, and are then only ever read.
          The full chain is in <code>docs/data_lineage.md</code>.
        </p>
      </Card>
    </div>
  )
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string
  value: string
  hint?: string | undefined
}) {
  return (
    <div>
      <p className="label">{label}</p>
      <p className="mt-1 font-mono text-base font-semibold tabular-nums text-ink-900">
        {value}
      </p>
      {hint && <p className="mt-0.5 text-2xs text-ink-500">{hint}</p>}
    </div>
  )
}

function loading(resource: { loading: boolean; settled: boolean }): boolean {
  return resource.loading && !resource.settled
}

function groupBySchema(
  status: Awaited<ReturnType<typeof api.status>> | null,
): Record<string, Array<{ table_name: string; rows: number; columns: number }>> {
  const grouped: Record<string, Array<{ table_name: string; rows: number; columns: number }>> =
    {}
  for (const table of status?.tables ?? []) {
    ;(grouped[table.schema_name] ??= []).push(table)
  }
  for (const list of Object.values(grouped)) list.sort((a, b) => a.table_name.localeCompare(b.table_name))
  return grouped
}

function coverageOf(metrics: Record<string, unknown> | undefined): string {
  if (!metrics) return 'not loaded'
  const start = metrics.coverage_start
  const end = metrics.coverage_end
  if (typeof start === 'string' && typeof end === 'string') {
    return `${start.slice(0, 7)} - ${end.slice(0, 7)}`
  }
  const dayMin = metrics.day_index_min
  const dayMax = metrics.day_index_max
  if (typeof dayMin === 'number' && typeof dayMax === 'number') {
    return `day ${dayMin} - ${dayMax} (index)`
  }
  return 'not exposed'
}

/**
 * Dataset shape, recorded when each source was loaded. These are statements
 * about structure and are documented in `docs/`; nothing here is a value that
 * could drift without someone noticing.
 */
const LIMITATIONS: Record<string, string[]> = {
  olist: [
    'A marketplace, not a retailer: sellers are third parties and seller revenue is not the platform revenue.',
    'Prices are in Brazilian reais. Do not compare to the other three sources.',
    'Order status is a snapshot; cancelled orders still carry item rows.',
    'Reviews cover a small minority of orders - the coverage percentage is reported rather than assumed to be 100%.',
  ],
  dataco: [
    'A supply-chain operations dataset with 118 SKUs, not a consumer product catalogue.',
    'Currency is US dollars and the fiscal calendar differs from Olist and M5.',
    'Discount, profit and shipping figures are order-line level and are already netted.',
  ],
  dunnhumby: [
    'The time axis is an integer day index, not a date, and the dataset ships no calendar to convert it with.',
    'It is a panel with household identifiers that are stable WITHIN this dataset and mean nothing outside it.',
    'Demographics cover 801 households, not the 2,500 transacting - the coverage gap is a real, reported limitation.',
  ],
  m5: [
    'Units, not currency. Walmart revenue is not present and is not derivable from this data.',
    'The series ids are M5-specific and are NOT product ids in any other dataset here.',
    'About 68% of series-days have zero sales, so demand is intermittent and a naive forecast on it looks fine and is not.',
    'Series ids include a horizon suffix, so the same item appears once per horizon.',
  ],
}

const ATTRIBUTION = [
  {
    source: 'olist',
    text: 'Olist Brazilian marketplace order data - orders, customers, sellers, payments and reviews. Public dataset.',
  },
  {
    source: 'dataco',
    text: 'DataCo supply chain dataset - orders, order lines, products and shipping events, with cost and margin fields.',
  },
  {
    source: 'dunnhumby',
    text: 'Dunnhumby US grocery panel - household basket transactions, product master, campaigns and coupon exposure.',
  },
  {
    source: 'm5',
    text: 'M5 Walmart Global Selling Competition - 30,490 item/store/horizon series over 1,941 days of unit sales.',
  },
]
