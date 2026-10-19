import { useMemo, useState } from 'react'
import { api } from '../api/client'
import { useResource } from '../lib/useResource'
import { sourceColor, sourceLabel } from '../lib/sourceMeta'
import { Badge, Card, PageHeader, SourceChip, StatCard } from '../components/ui/Card'
import { HBarChart } from '../components/ui/Charts'
import { DataTable } from '../components/ui/DataTable'
import { Notice, SkeletonCards, SkeletonTable } from '../components/ui/States'
import { formatNumber } from '../format'

/**
 * The anomaly monitor.
 *
 * NO ANOMALY IS SHOWN WITHOUT ITS ARITHMETIC
 * -----------------------------------------
 * Every row carries the observed value, the baseline it was compared against,
 * the threshold, and how many standard deviations away it was. A row that only
 * said "anomaly" would be unfalsifiable, and a user could not tell a real signal
 * from a bug in the detector. The numbers are the product.
 *
 * SEVERITY IS SHOWN, NOT INVENTED
 * -------------------------------
 * `severity` comes from the run. The UI does not compute its own grade, and it
 * does not upgrade one: a "warning" is a warning. The visual weight difference
 * between critical and warning is a single colour change.
 *
 * AND THE FRAMING IS NOT OPTIONAL
 * -------------------------------
 * There is no labelled ground truth for any of these datasets. A firing point
 * means "unusual against its own recent history", not "wrong". The strongest
 * evidence that this matters is in the data itself: every M5 flag is Christmas
 * Day, which the page surfaces rather than hiding - a detector that fires only
 * on a known holiday has told you about the calendar, not about demand.
 */

const PAGE_SIZE = 50

export function AnomaliesPage({ hasKey }: { hasKey: boolean }) {
  const [source, setSource] = useState('all')
  const [severity, setSeverity] = useState('all')

  const resource = useResource(
    () =>
      api.anomalies({
        limit: PAGE_SIZE,
        source: source === 'all' ? undefined : source,
      }),
    [source, hasKey],
    { enabled: hasKey },
  )

  const rows = useMemo(() => {
    const all = resource.data?.rows ?? []
    return severity === 'all' ? all : all.filter((row) => row.severity === severity)
  }, [resource.data, severity])

  const blocks = resource.data?.metrics ?? []
  const severities = useMemo(
    () => [...new Set((resource.data?.rows ?? []).map((r) => r.severity))],
    [resource.data],
  )

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Anomalies"
        description="Points that departed from their own recent history, each with the baseline and threshold that flagged it."
        actions={
          resource.data && (
            <Badge tone="info">
              {formatNumber(resource.data.total)} firings across{' '}
              {resource.data.metrics_count} metrics
            </Badge>
          )
        }
      />

      {!hasKey && (
        <Notice title="An API key is required">
          Set a key in the top right to load the recorded anomaly run.
        </Notice>
      )}

      {hasKey && resource.error && (
        <Notice title="The anomaly run could not be read">{resource.error}</Notice>
      )}

      {hasKey && resource.loading && !resource.data && <SkeletonCards count={3} />}

      {resource.data && (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label="Metrics scanned"
              value={String(resource.data.metrics_count)}
              hint={`${formatNumber(
                blocks.reduce((sum, b) => sum + b.points_evaluated, 0),
              )} points evaluated`}
            />
            <StatCard
              label="Points flagged"
              value={formatNumber(resource.data.total)}
              hint={`${(
                (100 * resource.data.total) /
                Math.max(1, blocks.reduce((sum, b) => sum + b.points_evaluated, 0))
              ).toFixed(2)}% of points evaluated`}
            />
            <StatCard
              label="Sources with flags"
              value={String(new Set(resource.data.rows.map((r) => r.source)).size)}
              hint={blocks.map((b) => sourceLabel(b.source)).join(', ')}
            />
            <StatCard
              label="Recorded"
              value={resource.data.generated_utc ? resource.data.generated_utc.slice(0, 10) : '-'}
              hint={resource.data.source_artefact}
            />
          </div>

          <div className="mt-4">
            <Notice title="What a flag does and does not mean" tone="warn">
              {resource.data.caveat}
            </Notice>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
            <Card
              title="Firings by source and metric"
              subtitle="Count of flagged points, not a severity score"
            >
              <HBarChart
                data={blocks.map((block) => ({
                  label: `${sourceLabel(block.source)} ${block.metric}`,
                  value: block.anomalies_found,
                }))}
                color="#f472b6"
              />
            </Card>

            <Card title="Detection method per metric" subtitle="Every metric is scored differently, on purpose">
              <div className="space-y-3">
                {blocks.map((block) => (
                  <div key={block.source} className="surface-quiet p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <SourceChip source={block.source} />
                      <span className="font-mono text-2xs text-ink-700">{block.metric}</span>
                      <Badge tone="warn">
                        {block.anomalies_found} / {block.points_evaluated} points
                      </Badge>
                    </div>
                    <p className="mt-2 text-2xs leading-relaxed text-ink-500">
                      <strong className="text-ink-700">Method.</strong> {block.method}
                    </p>
                    <p className="mt-1 text-2xs leading-relaxed text-ink-500">
                      <strong className="text-ink-700">Baseline.</strong> {block.baseline}
                    </p>
                    {block.notes?.map((note) => (
                      <p key={note} className="mt-1 text-2xs leading-relaxed text-amber-600/80">
                        {note}
                      </p>
                    ))}
                  </div>
                ))}
              </div>
            </Card>
          </div>

          <Card
            className="mt-4"
            title="Flagged points"
            subtitle="Every row carries the arithmetic behind it"
            actions={
              <div className="flex flex-wrap items-center gap-2">
                <select
                  className="field w-auto py-1 text-xs"
                  value={source}
                  onChange={(event) => setSource(event.target.value)}
                  aria-label="Filter by source"
                >
                  <option value="all">All sources</option>
                  {blocks.map((block) => (
                    <option key={block.source} value={block.source}>
                      {sourceLabel(block.source)}
                    </option>
                  ))}
                </select>
                <select
                  className="field w-auto py-1 text-xs"
                  value={severity}
                  onChange={(event) => setSeverity(event.target.value)}
                  aria-label="Filter by severity"
                >
                  <option value="all">All severities</option>
                  {severities.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </div>
            }
          >
            {resource.loading && rows.length === 0 ? (
              <SkeletonTable rows={8} cols={5} />
            ) : (
              <DataTable
                columns={[
                  'source',
                  'date',
                  'metric',
                  'observed',
                  'baseline',
                  'threshold',
                  'sigma',
                  'direction',
                  'severity',
                ]}
                rows={rows.map((row) => ({
                  source: row.source,
                  date: row.date,
                  metric: row.metric,
                  observed: row.observed,
                  baseline: row.baseline,
                  threshold: row.threshold,
                  sigma: row.score_sigma,
                  direction: row.direction,
                  severity: row.severity,
                  // Carried through for the cell renderers, not for display.
                  time_axis: row.time_axis,
                }))}
                emptyTitle="No points match those filters"
                emptyDescription="Clear a filter to see the rest of the run."
                footer={`${rows.length} shown${
                  resource.data.total > rows.length
                    ? ` of ${formatNumber(resource.data.total)} firings - page the API with ?limit= to see more`
                    : ''
                }`}
                renderCell={(column, row) => {
                  // Severity: a dot, not a louder word. The level itself is the
                  // run's judgement and is printed in full beside the dot, so
                  // nothing is hidden behind colour alone.
                  if (column === 'severity') {
                    const level = String(row.severity)
                    return (
                      <span className="inline-flex items-center gap-1.5">
                        <SeverityDot severity={level} />
                        <span className="text-xs">{level}</span>
                      </span>
                    )
                  }
                  // The time label is NOT always a date. Dunnhumby's axis is an
                  // integer `day` index and the dataset ships no calendar to
                  // convert it with, so the cell says "day N" rather than
                  // inviting the reader to look 643 up as a calendar date.
                  if (column === 'date' && row.time_axis !== 'calendar date') {
                    return (
                      <span className="font-mono text-xs">
                        day {String(row.date)}
                        <span className="ml-1.5 text-2xs text-amber-600/80">
                          index
                        </span>
                      </span>
                    )
                  }
                  if (column === 'source') {
                    return <SourceChip source={String(row.source)} />
                  }
                  return undefined
                }}
              />
            )}

            <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
              <div className="surface-quiet p-3">
                <p className="label mb-2">Known retail events in the run window</p>
                <p className="text-2xs leading-relaxed text-ink-500">
                  Check any flagged date against these before acting on it.
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {resource.data.known_events.slice(0, 16).map((event) => (
                    <span
                      key={`${event.date}-${event.name}`}
                      className="badge badge-neutral font-mono"
                      title={event.name}
                    >
                      {event.date} {event.name}
                    </span>
                  ))}
                  {resource.data.known_events.length > 16 && (
                    <span className="text-2xs text-ink-500">
                      +{resource.data.known_events.length - 16} more
                    </span>
                  )}
                </div>
              </div>
              <div className="surface-quiet p-3">
                <p className="label mb-2">Severity scale</p>
                <ul className="space-y-1.5">
                  {['critical', 'warning', 'info'].map((level) => (
                    <li key={level} className="flex items-center gap-2">
                      <SeverityDot severity={level} />
                      <span className="text-2xs text-ink-500">
                        {level === 'critical'
                          ? 'far outside the threshold band'
                          : level === 'warning'
                            ? 'outside the threshold band'
                            : 'near the threshold'}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </Card>
        </>
      )}
    </div>
  )
}

function SeverityDot({ severity }: { severity: string }) {
  const color =
    severity === 'critical'
      ? '#f87171'
      : severity === 'warning'
        ? '#fbbf24'
        : sourceColor(severity)
  return (
    <span
      aria-hidden="true"
      className="h-2 w-2 shrink-0 rounded-full"
      style={{ backgroundColor: color }}
    />
  )
}