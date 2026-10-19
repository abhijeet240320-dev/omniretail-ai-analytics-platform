import { useId, useMemo, useState } from 'react'
import {
  caption,
  extent,
  hasGaps,
  isPercentAxis,
  niceDomain,
  planChart,
  type ChartPlan,
} from '../../lib/chart'

/**
 * Charts, drawn as SVG, with no charting library.
 *
 * WHY NO LIBRARY
 * --------------
 * Recharts or Chart.js would be the reflexive choice. It is also a large
 * dependency for four chart types, and this project has a hard rule against
 * shipping code it cannot read. A line, an area, a bar and a stack are a few
 * hundred lines of geometry; a library is a few thousand lines of geometry
 * nobody in this repo has read.
 *
 * THE THREE RULES THESE CHARTS FOLLOW
 * -----------------------------------
 * 1. Nothing is drawn that the API did not return. No smoothing, no
 *    interpolation across a gap, no resampling, no invented zero-padding. A
 *    missing value breaks the line rather than being filled in, because a
 *    filled gap is a measurement this project never made.
 * 2. Colour is never the only cue. Every series carries a name in the legend,
 *    every bar carries a `<title>`, and the whole figure has an accessible
 *    name. The table beneath the chart is the real, complete, selectable
 *    version - the chart is an aid to reading it, never its replacement.
 * 3. Truncation is stated. If the API returned 50 of 1,204 rows the caption
 *    says so, in the caption, in words. A chart that silently shows 4% of a
 *    result and looks complete is the single most misleading thing a
 *    dashboard can do.
 *
 * Accessibility: the SVG is `role="img"` with a real `<title>` and `<desc>`,
 * so a screen reader gets a sentence, not a pile of path coordinates. The
 * detail is available from the `<desc>` and from the table below.
 */

const PAD = { top: 12, right: 12, bottom: 34, left: 56 }
const HEIGHT = 260
const WIDTH = 720

/** Series colours, chosen to stay distinct from each other and from ink-*. */
const SERIES_COLORS = ['#38bdf8', '#f97316', '#34d399', '#a78bfa', '#f472b6']

export interface ResultChartProps {
  columns: string[]
  rows: Array<Record<string, unknown>>
  query: string
  truncated?: boolean | undefined
  totalRows?: number | null | undefined
  /** Series accent, from sourceColor(), so the chart matches its page. */
  accent?: string | undefined
}

/**
 * Draw the result, or render nothing.
 *
 * Returning `null` for an unchartable result is the point: a result with no
 * shape should show its table and no chart, rather than a chart that invents
 * one.
 */
export function ResultChart(props: ResultChartProps) {
  const plan = useMemo(
    () =>
      planChart(props.columns, props.rows, {
        truncated: props.truncated ?? false,
        totalRows: props.totalRows ?? null,
      }),
    [props.columns, props.rows, props.truncated, props.totalRows],
  )

  if (!plan) return null
  return (
    <figure className="mb-4">
      <Chart plan={plan} query={props.query} accent={props.accent} />
      <figcaption className="mt-1.5 text-2xs leading-relaxed text-ink-500">
        {caption(plan, props.query)}
      </figcaption>
    </figure>
  )
}

function Chart({ plan, query, accent }: { plan: ChartPlan; query: string; accent?: string | undefined }) {
  const titleId = useId()
  const descId = useId()
  const [hover, setHover] = useState<number | null>(null)
  const { series, points } = plan

  const domain = useMemo(() => {
    const e = extent(points, series.ys)
    return niceDomain(e?.min ?? 0, e?.max ?? 1)
  }, [points, series.ys])

  const plotW = WIDTH - PAD.left - PAD.right
  const plotH = HEIGHT - PAD.top - PAD.bottom

  // Bars need room; lines need room for the stroke. Both are laid out on the
  // same axis so switching kind does not move the frame.
  const bandW = plotW / points.length
  // `planChart` refuses a result with no numeric column, so `ys` is non-empty
  // here; the guard exists to satisfy the compiler, not to change behaviour.
  const primary = series.ys[0] ?? ''
  const gap = hasGaps(points, primary) && series.kind === 'time'

  const xAt = (i: number) => PAD.left + bandW * (i + 0.5)
  const yAt = (v: number) =>
    PAD.top + plotH - ((v - domain.min) / (domain.max - domain.min || 1)) * plotH

  const percent = isPercentAxis(primary)
  const axisTitle = `${series.x.replace(/_/g, ' ')} against ${series.ys
    .map((y) => y.replace(/_/g, ' '))
    .join(', ')}`

  // Every plotted number, in words, for the accessible description. This is
  // what a screen reader gets instead of "image".
  const described = points
    .slice(0, 200)
    .map(
      (p) =>
        `${p.x}: ${series.ys
          .map((y) => (p.values[y] === null ? `${y} missing` : `${y} ${p.values[y]}`))
          .join(', ')}`,
    )
    .join('. ')

  const xLabelEvery = Math.ceil(points.length / Math.max(3, Math.floor(plotW / 90)))

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-auto w-full"
        role="img"
        aria-labelledby={`${titleId} ${descId}`}
        preserveAspectRatio="xMidYMid meet"
      >
        <title id={titleId}>{`${query} - ${axisTitle}`}</title>
        <desc id={descId}>{`${caption(plan, query)}. ${described}`}</desc>

        {/* Horizontal gridlines and the y axis, labelled with real numbers. */}
        {domain.ticks.map((t) => (
          <g key={t}>
            <line
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={yAt(t)}
              y2={yAt(t)}
              stroke="currentColor"
              className="text-ink-700"
              strokeWidth={1}
            />
            <text
              x={PAD.left - 8}
              y={yAt(t) + 3}
              textAnchor="end"
              className="fill-ink-500 font-mono"
              fontSize={10}
            >
              {formatTick(t)}
              {percent ? '%' : ''}
            </text>
          </g>
        ))}

        {/* Zero line, only when zero is inside the domain and not the floor. */}
        {domain.min < 0 && domain.max > 0 && (
          <line
            x1={PAD.left}
            x2={WIDTH - PAD.right}
            y1={yAt(0)}
            y2={yAt(0)}
            stroke="currentColor"
            className="text-ink-500"
            strokeWidth={1}
          />
        )}

        {series.ys.map((y, si) => {
          const color = si === 0 && accent ? accent : SERIES_COLORS[si % SERIES_COLORS.length]
          if (series.kind === 'category') {
            // Grouped bars: up to three series before it stops being readable.
            const inner = bandW * 0.72
            const groupW = si >= 3 ? 0 : inner / Math.min(series.ys.length, 3)
            return points.map((p, i) => {
              const v = p.values[y]
              if (v === null || v === undefined) return null
              const zero = yAt(Math.max(domain.min, Math.min(0, domain.max)))
              const top = yAt(v)
              const x =
                PAD.left + bandW * i + bandW * 0.14 + (si < 3 ? si * groupW : 0)
              return (
                <rect
                  key={`${y}-${i}`}
                  x={x}
                  y={Math.min(top, zero)}
                  width={Math.max(1, groupW - 1)}
                  height={Math.max(1, Math.abs(zero - top))}
                  rx={2}
                  fill={color}
                  fillOpacity={hover === null || hover === i ? 0.85 : 0.35}
                >
                  <title>{`${p.x} - ${y.replace(/_/g, ' ')}: ${formatTick(v)}`}</title>
                </rect>
              )
            })
          }

          // Time series. A missing value breaks the path rather than being bridged.
          // Points stay in the order the API returned them; no sorting, because
          // reordering a series the backend deliberately ordered is a silent
          // change to the result.
          const segments: string[] = []
          let current: string[] = []
          points.forEach((p, i) => {
            const v = p.values[y]
            if (v === null || v === undefined) {
              if (current.length) segments.push(current.join(' '))
              current = []
              return
            }
            current.push(`${xAt(i)},${yAt(v)}`)
            // With no gaps at all the whole series is one segment; when there
            // are gaps, flush the run so each measured stretch is its own line.
            if (!gap && i === points.length - 1) segments.push(current.join(' '))
          })
          if (current.length) segments.push(current.join(' '))

          return (
            <g key={y}>
              {series.ys.length > 1 && (
                <polyline
                  points={`${PAD.left},${yAt(domain.min)} ${WIDTH - PAD.right},${yAt(domain.min)}`}
                  fill="none"
                  stroke="none"
                />
              )}
              {segments.map((pts, i) => (
                <polyline
                  key={i}
                  points={pts}
                  fill="none"
                  stroke={color}
                  strokeWidth={series.ys.length > 1 ? 1.75 : 2.25}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              ))}
              {series.ys.length === 1 &&
                points.map((p, i) =>
                  p.values[y] === null ? null : (
                    <circle key={i} cx={xAt(i)} cy={yAt(p.values[y] as number)} r={2.5} fill={color}>
                      <title>{`${p.x}: ${formatTick(p.values[y] as number)}`}</title>
                    </circle>
                  ),
                )}
            </g>
          )
        })}

        {/* x axis labels, thinned so they never overlap. */}
        {points.map((p, i) =>
          i % xLabelEvery === 0 ? (
            <text
              key={p.x + i}
              x={xAt(i)}
              y={HEIGHT - PAD.bottom + 16}
              textAnchor="middle"
              className="fill-ink-500"
              fontSize={10}
            >
              {shorten(p.x)}
            </text>
          ) : null,
        )}

        {/* Hover targets. Pointer only; the values are already in <title>. */}
        {points.map((_p, i) => (
          <rect
            key={`hit-${i}`}
            x={PAD.left + bandW * i}
            y={PAD.top}
            width={bandW}
            height={plotH}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
          />
        ))}
      </svg>

      {/* Legend. Names, not swatches alone - this is the non-colour cue. */}
      {series.ys.length > 0 && (
        <ul className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
          {series.ys.map((y, si) => {
            const hovered = hover === null ? null : points[hover]?.values[y]
            return (
              <li key={y} className="flex items-center gap-1.5 text-2xs text-ink-500">
                <span
                  aria-hidden="true"
                  className="h-2 w-2 rounded-sm"
                  style={{
                    backgroundColor:
                      si === 0 && accent ? accent : SERIES_COLORS[si % SERIES_COLORS.length],
                  }}
                />
                <span className="font-mono">{y}</span>
                {series.ys.length > 1 && hovered !== null && (
                  <span className="text-ink-500">
                    : {hovered === null || hovered === undefined ? '—' : formatTick(hovered)}
                  </span>
                )}
              </li>
            )
          })}
          {hover !== null && series.ys.length === 1 &&
            (() => {
              const p = points[hover]
              if (!p) return null
              const v = p.values[series.ys[0] as string]
              return (
                <li className="text-2xs text-ink-500">
                  {p.x}: {v === null || v === undefined ? 'no value' : formatTick(v)}
                </li>
              )
            })()}
        </ul>
      )}
    </div>
  )
}

function formatTick(value: number): string {
  const abs = Math.abs(value)
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (abs >= 10_000) return `${Math.round(value / 1000)}k`
  if (Number.isInteger(value)) return String(value)
  return value.toFixed(abs < 1 ? 2 : 1)
}

function shorten(label: string): string {
  // An ISO timestamp on an axis is unreadable at 10px; the year is not.
  if (/^\d{4}-\d{2}-\d{2}T/.test(label)) return label.slice(0, 10)
  return label.length > 10 ? `${label.slice(0, 9)}…` : label
}