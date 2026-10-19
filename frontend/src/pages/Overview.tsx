import { api } from '../api/client'
import type { SourceKpi } from '../api/client'
import { useResource } from '../lib/useResource'
import {
  NO_COMBINED_TOTAL,
  isFractionMetric,
  isMoneyMetric,
  isPercentMetric,
  metricLabel,
  sourceColor,
  sourceLabel,
} from '../lib/sourceMeta'
import {
  Badge,
  Card,
  Freshness,
  Hero,
  HeroFigure,
  PageHeader,
  SourceChip,
  Stagger,
} from '../components/ui/Card'
import { Notice, SkeletonTable } from '../components/ui/States'
import { formatAge, formatCompact, formatMoney, formatNumber, formatTimestamp } from '../format'

/**
 * The overview dashboard.
 *
 * THE ONE RULE ON THIS PAGE
 * -------------------------
 * There is no combined total, and the layout makes that hard to get wrong
 * rather than merely discouraging it. Four sources, four equal-weight cards,
 * no "total revenue" tile anywhere. A user who wants one figure across all four
 * datasets is asking for something that has no referent - these are unrelated
 * companies with no shared key space - and the most honest thing the interface
 * can do is show them four numbers instead of one wrong one.
 *
 * `NO_COMBINED_TOTAL` is rendered verbatim from the API's own `disclaimer`
 * when it arrives, so the rule on screen is the rule the backend enforces.
 */
export function OverviewPage({ hasKey }: { hasKey: boolean }) {
  const overview = useResource(() => api.overview(), [], { enabled: hasKey })
  const status = useResource(() => api.status(), [], { enabled: hasKey })
  const catalogue = useResource(() => api.listQueries(), [], { enabled: hasKey })

  return (
    <div className="animate-fade-in">
      {hasKey && (
        <Hero
          eyebrow={
            <span className="flex flex-wrap items-center gap-2">
              <Badge tone="info">4 sources</Badge>
              <Badge tone="neutral">read-only</Badge>
              {status.data && (
                <Badge tone={status.data.is_select_only ? 'good' : 'bad'}>
                  {status.data.is_select_only ? 'SELECT-only role' : 'ROLE NOT READ-ONLY'}
                </Badge>
              )}
            </span>
          }
          title="OmniRetail Analytics"
          description="Four independent public datasets in one PostgreSQL instance. Every figure below belongs to exactly one of them, because there is no shared key space between them and a combined total would have no referent."
          actions={
            overview.data ? (
              <Freshness
                generatedAt={formatTimestamp(overview.data.generated_at)}
                ageSeconds={overview.data.age_seconds}
                cached={overview.data.cached}
              />
            ) : null
          }
        >
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-4">
            {overview.loading && !overview.data
              ? Array.from({ length: 4 }, (_, i) => (
                  <div key={i} className="min-w-0 border-l-2 border-slate-200 pl-3">
                    <div className="skeleton h-2.5 w-20" />
                    <div className="skeleton mt-2 h-5 w-24" />
                  </div>
                ))
              : [
                  {
                    label: 'Tables',
                    value: status.data
                      ? formatNumber(status.data.table_count)
                      : '-',
                    hint: 'across 4 schemas',
                  },
                  {
                    label: 'Rows',
                    value: status.data
                      ? formatCompact(status.data.total_rows)
                      : '-',
                    hint: 'approximate, from reltuples',
                  },
                  {
                    label: 'SQL queries',
                    value: catalogue.data ? String(catalogue.data.total) : '-',
                    hint: 'catalogued and verified',
                  },
                  {
                    label: 'Database',
                    value: status.data?.database ?? '-',
                    hint: status.data?.role ?? undefined,
                  },
                ].map((figure) => (
                  <HeroFigure
                    key={figure.label}
                    label={figure.label}
                    value={figure.value}
                    hint={figure.hint}
                  />
                ))}
          </div>
        </Hero>
      )}

      {!hasKey && (
        <>
          <PageHeader
            title="Overview"
            description="Per-source KPIs computed from PostgreSQL, with the health of the database underneath them. Every figure belongs to exactly one dataset."
          />
          <Notice title="An API key is required">
            Every endpoint except <code>/api/health</code> needs a key. Set one in
            the top right to load the dashboard. Issue one with{' '}
            <code>backend/scripts/db/08_issue_api_key.py issue --label you --scopes analytics:read,ai:read,admin:read</code>.
          </Notice>
        </>
      )}

      {hasKey && (
        <>
          {overview.error && (
            <Card className="mb-4">
              <Notice title="The overview could not be loaded">{overview.error}</Notice>
            </Card>
          )}

          <Stagger
            className="grid grid-cols-1 gap-4 lg:grid-cols-2"
            step={50}
          >
            {overview.loading &&
              !overview.data &&
              Array.from({ length: 4 }, (_, i) => <SourceCardSkeleton key={i} />)}

            {overview.data?.sources.map((source) => (
              <SourceCard key={source.source} kpi={source} />
            ))}
          </Stagger>

          {overview.data && (
            <Card className="mt-4">
              <p className="text-xs leading-relaxed text-ink-500">
                <strong className="text-ink-700">
                  There is deliberately no combined total.
                </strong>{' '}
                {overview.data.disclaimer}
              </p>
              <p className="mt-2 text-2xs text-ink-500">
                Computed {formatTimestamp(overview.data.generated_at)}
                {overview.data.cached
                  ? ` \u00b7 served from the API cache, ${formatAge(overview.data.age_seconds)} old`
                  : ' \u00b7 computed for this request'}
                . The figures only change when the data is reloaded, which is a
                separate batch job.
              </p>
            </Card>
          )}

          <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
            <Card
              title="Database"
              subtitle="Live connection state reported by /api/status"
              actions={
                status.loading ? (
                  <Badge tone="neutral">checking</Badge>
                ) : status.data ? (
                  <Badge tone="good">connected</Badge>
                ) : null
              }
            >
              {status.loading && !status.data && <SkeletonTable rows={4} cols={2} />}
              {status.error && (
                <Notice title="/api/status needs the admin:read scope">
                  {status.error}
                </Notice>
              )}
              {status.data && (
                <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                  <Metric label="Database" value={status.data.database} />
                  <Metric
                    label="Role"
                    value={status.data.role}
                    tone={status.data.is_select_only ? 'good' : 'bad'}
                  />
                  <Metric
                    label="SELECT-only"
                    value={status.data.is_select_only ? 'yes' : 'no'}
                    tone={status.data.is_select_only ? 'good' : 'bad'}
                  />
                  <Metric label="Tables" value={formatNumber(status.data.table_count)} />
                  <Metric
                    label="Rows (approx)"
                    value={status.data.total_rows.toLocaleString('en-US')}
                  />
                  <Metric
                    label="Alembic"
                    value={status.data.alembic_revision ?? 'not stamped'}
                  />
                </dl>
              )}
            </Card>

            <Card
              title="Analytics catalogue"
              subtitle="Named, verified, read-only SQL"
              actions={
                catalogue.data && (
                  <Badge tone="info">{catalogue.data.total} queries</Badge>
                )
              }
            >
              {catalogue.loading && !catalogue.data && <SkeletonTable rows={4} cols={2} />}
              {catalogue.error && (
                <Notice title="The catalogue could not be loaded">{catalogue.error}</Notice>
              )}
              {catalogue.data && (
                <ul className="space-y-2.5">
                  {(['olist', 'dataco', 'dunnhumby', 'm5'] as const).map((source) => {
                    const count = catalogue.data!.queries.filter(
                      (q) => q.source === source,
                    ).length
                    return (
                      <li key={source} className="flex items-center gap-3">
                        <span className="w-24 shrink-0">
                          <SourceChip source={source} />
                        </span>
                        <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                          <span
                            className="block h-full rounded-full"
                            style={{
                              width: `${
                                (count /
                                  Math.max(
                                    1,
                                    ...['olist', 'dataco', 'dunnhumby', 'm5'].map(
                                      (s) =>
                                        catalogue.data!.queries.filter((q) => q.source === s)
                                          .length,
                                    ),
                                  )) *
                                100
                              }%`,
                              backgroundColor: sourceColor(source),
                            }}
                          />
                        </span>
                        <span className="w-6 text-right font-mono text-xs text-ink-700">
                          {count}
                        </span>
                      </li>
                    )
                  })}
                </ul>
              )}
              <p className="mt-4 text-2xs leading-relaxed text-ink-500">
                {NO_COMBINED_TOTAL}
              </p>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}

function SourceCard({ kpi }: { kpi: SourceKpi }) {
  // Coverage keys are pulled out first: a date range is not a KPI, and putting
  // it in the same grid as revenue would imply they are comparable.
  const { coverage_start: start, coverage_end: end, ...metrics } = kpi.metrics
  const coverage =
    typeof start === 'string' && typeof end === 'string' ? `${start} to ${end}` : null

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: sourceColor(kpi.source) }}
          />
          {sourceLabel(kpi.source)}
        </span>
      }
      subtitle={kpi.label.replace(/^[A-Za-z0-9]+\s*-\s*/, '')}
      // A coloured left edge rather than a source chip: the chip would repeat
      // the card title, and the colour already carries the attribution.
      className="border-l-2"
      style={{ borderLeftColor: sourceColor(kpi.source) }}
    >
      {kpi.note && (
        <div className="mb-3">
          <Notice title="Some metrics could not be computed">{kpi.note}</Notice>
        </div>
      )}

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3.5 sm:grid-cols-3">
        {Object.entries(metrics).map(([key, value]) => (
          <div key={key}>
            <dt className="label truncate" title={metricLabel(key)}>
              {metricLabel(key)}
            </dt>
            <dd className="mt-1 font-mono text-base font-semibold tabular-nums text-ink-900">
              {renderMetric(key, value)}
            </dd>
          </div>
        ))}
      </dl>

      {coverage && (
        <p className="mt-4 border-t border-slate-300 pt-3 font-mono text-2xs text-ink-500">
          coverage {coverage}
        </p>
      )}
    </Card>
  )
}

function renderMetric(key: string, value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'string') return value
  if (typeof value !== 'number') return String(value)
  // Order matters: `avg_margin_pct` is a 0..1 fraction while `delivered_pct` is
  // already 0..100. Both end in `_pct`, so the fraction case has to be tested
  // first or every margin is shown 1000x too small.
  if (isPercentMetric(key)) {
    return isFractionMetric(key) ? `${(value * 100).toFixed(2)}%` : `${value.toFixed(2)}%`
  }
  if (isMoneyMetric(key)) return formatMoney(value)
  if (Math.abs(value) >= 1_000_000) return formatCompact(value)
  return formatNumber(value)
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: 'good' | 'bad'
}) {
  return (
    <div>
      <dt className="label">{label}</dt>
      <dd
        className={`mt-1 font-mono text-sm tabular-nums ${
          tone === 'good' ? 'text-emerald-600' : tone === 'bad' ? 'text-red-600' : 'text-ink-900'
        }`}
      >
        {value}
      </dd>
    </div>
  )
}

function SourceCardSkeleton() {
  return (
    <div className="surface p-4 sm:p-5">
      <div className="skeleton h-3 w-24" />
      <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i}>
            <div className="skeleton h-2.5 w-16" />
            <div className="skeleton mt-2 h-5 w-20" />
          </div>
        ))}
      </div>
    </div>
  )
}
