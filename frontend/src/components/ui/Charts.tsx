/**
 * Charts, hand-rolled in SVG.
 *
 * WHY NOT A CHARTING LIBRARY
 * --------------------------
 * Recharts is ~100 kB gzipped and brings its own scales, animation loop and DOM
 * shape, all of which this app does not need - every chart here is a bar chart
 * or a line chart over at most a few dozen points, drawn once, from data the
 * API already returned. The three components below cover every visualisation
 * on every page for about 5 kB and no new dependency.
 *
 * WHAT THESE CHARTS WILL NOT DO
 * -----------------------------
 * They do not aggregate, interpolate, smooth or extrapolate. A bar is one API
 * value at one position. A line connects consecutive API values and draws
 * nothing between them that the API did not return. A chart that quietly drew
 * a trend line through sparse data would be inventing data inside the one
 * component nobody thinks to audit.
 *
 * Axis numbers use `formatGrouped` (with separators) - these are for reading.
 * Table cells use `formatCell` (exact) - those are values being verified. The
 * two are deliberately different functions, in `../../format`.
 */

import { formatCompact, formatGrouped } from '../../format'

export interface SeriesPoint {
  label: string
  value: number
}

const AXIS_TEXT = '#64748b'

function niceMax(value: number): number {
  if (value <= 0) return 1
  const magnitude = 10 ** Math.floor(Math.log10(value))
  const normalised = value / magnitude
  const step = normalised <= 1 ? 1 : normalised <= 2 ? 2 : normalised <= 5 ? 5 : 10
  return step * magnitude
}

/**
 * Vertical bars. Good for a handful of categories (departments, statuses,
 * years) where the CATEGORY is what matters, not the exact value.
 */
export function BarChart({
  data,
  height = 200,
  color = '#38bdf8',
  valueFormat = formatGrouped,
  highlightIndex,
}: {
  data: SeriesPoint[]
  height?: number | undefined
  color?: string | undefined
  valueFormat?: ((value: unknown) => string) | undefined
  /** Index to draw in a brighter tone - used to mark the winning model. */
  highlightIndex?: number | undefined
}) {
  if (data.length === 0) return null

  // AXIS LABELS ARE COMPACT, BAR LABELS ARE EXACT.
  //
  // A M5 yearly total is 13,800,811. Printed in full on the y-axis it needs
  // ~62px, which does not fit the left gutter, and the browser does not clip
  // text nicely - it renders "000,000", an axis that looks like a bug and is
  // quietly unreadable. Compact keeps "13.8M"; the exact figure is printed
  // above each bar, where there is room for it.
  const axisFormat = formatCompact

  const padding = { top: 16, right: 8, bottom: 34, left: 58 }
  const width = 640
  const plotWidth = width - padding.left - padding.right
  const plotHeight = height - padding.top - padding.bottom
  const max = niceMax(Math.max(...data.map((d) => Math.abs(d.value)), 0) || 1)

  const slot = plotWidth / data.length
  const barWidth = Math.min(52, slot * 0.62)

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="h-auto w-full"
      role="img"
      aria-label={`Bar chart with ${data.length} categories`}
    >
      {[0, 0.5, 1].map((fraction) => {
        const y = padding.top + plotHeight * (1 - fraction)
        return (
          <g key={fraction}>
            <line
              x1={padding.left}
              x2={width - padding.right}
              y1={y}
              y2={y}
              stroke="#1e293b"
              strokeWidth={1}
            />
            <text
              x={padding.left - 8}
              y={y + 3}
              textAnchor="end"
              fontSize={10}
              fill={AXIS_TEXT}
              fontFamily="ui-monospace, monospace"
            >
              {axisFormat(max * fraction)}
            </text>
          </g>
        )
      })}

      {data.map((point, index) => {
        const barHeight = (Math.abs(point.value) / max) * plotHeight
        const x = padding.left + slot * index + (slot - barWidth) / 2
        const y = padding.top + plotHeight - barHeight
        const isHighlight = highlightIndex === index
        return (
          <g key={`${point.label}-${index}`}>
            <rect
              x={x}
              y={y}
              width={barWidth}
              height={Math.max(barHeight, point.value === 0 ? 1 : 0)}
              rx={3}
              fill={color}
              opacity={highlightIndex === undefined || isHighlight ? 0.9 : 0.45}
            >
              <title>{`${point.label}: ${valueFormat(point.value)}`}</title>
            </rect>
            <text
              x={x + barWidth / 2}
              y={y - 5}
              textAnchor="middle"
              fontSize={10}
              fill={isHighlight ? '#e2e8f0' : AXIS_TEXT}
              fontFamily="ui-monospace, monospace"
            >
              {valueFormat(point.value)}
            </text>
            <text
              x={x + barWidth / 2}
              y={height - padding.bottom + 15}
              textAnchor="middle"
              fontSize={10}
              fill={AXIS_TEXT}
            >
              {truncate(point.label, Math.max(6, Math.floor(slot / 7)))}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

/**
 * Horizontal bars. Better than vertical ones whenever the labels are words -
 * "Womens Clothing", "Electronics" - because the label gets the whole width
 * instead of being rotated or truncated into nonsense.
 */
export function HBarChart({
  data,
  valueFormat = formatGrouped,
  color = '#38bdf8',
  maxRows = 12,
}: {
  data: SeriesPoint[]
  valueFormat?: ((value: unknown) => string) | undefined
  color?: string | undefined
  maxRows?: number | undefined
}) {
  const shown = data.slice(0, maxRows)
  if (shown.length === 0) return null

  const max = Math.max(...shown.map((d) => Math.abs(d.value)), 0) || 1
  const labelWidth = 150
  const valueWidth = 74
  const width = 640
  const plotWidth = width - labelWidth - valueWidth
  const rowHeight = 26
  const height = shown.length * rowHeight + 6

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="h-auto w-full"
      role="img"
      aria-label={`Bar chart with ${shown.length} categories`}
    >
      {shown.map((point, index) => {
        const y = index * rowHeight + 4
        const barWidth = (Math.abs(point.value) / max) * plotWidth
        return (
          <g key={`${point.label}-${index}`}>
            <text
              x={labelWidth - 8}
              y={y + rowHeight / 2 + 1}
              textAnchor="end"
              dominantBaseline="middle"
              fontSize={11}
              fill="#cbd5e1"
            >
              {truncate(point.label, 22)}
            </text>
            <rect
              x={labelWidth}
              y={y + 5}
              width={Math.max(barWidth, 1)}
              height={rowHeight - 12}
              rx={3}
              fill={color}
              opacity={0.85}
            >
              <title>{`${point.label}: ${valueFormat(point.value)}`}</title>
            </rect>
            <text
              x={labelWidth + barWidth + 8}
              y={y + rowHeight / 2 + 1}
              dominantBaseline="middle"
              fontSize={10}
              fill={AXIS_TEXT}
              fontFamily="ui-monospace, monospace"
            >
              {valueFormat(point.value)}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

/**
 * A line over an ordered numeric axis. Every point is an API value; the line
 * only connects consecutive points. There is no smoothing and no interpolation,
 * so nothing appears on this chart that the API did not return.
 */
export function LineChart({
  data,
  height = 200,
  color = '#34d399',
  valueFormat = formatGrouped,
  labelEvery = 1,
  area = true,
}: {
  data: SeriesPoint[]
  height?: number | undefined
  color?: string | undefined
  valueFormat?: ((value: unknown) => string) | undefined
  labelEvery?: number | undefined
  area?: boolean | undefined
}) {
  if (data.length < 2) return null

  const axisFormat = formatCompact
  const padding = { top: 16, right: 12, bottom: 30, left: 58 }
  const width = 680
  const plotWidth = width - padding.left - padding.right
  const plotHeight = height - padding.top - padding.bottom

  // noUncheckedIndexedAccess: indexing is safe here because the guard above
  // proved there are at least two points, but the compiler cannot know that.
  const first = data[0] as SeriesPoint
  const last = data[data.length - 1] as SeriesPoint

  const values = data.map((d) => d.value)
  const rawMax = Math.max(...values)
  const rawMin = Math.min(...values, 0)
  // Pad the domain by 8% so the line never touches the frame, and keep the
  // baseline honest: if every value is positive, the axis starts at 0.
  const max = rawMax + (rawMax - rawMin) * 0.08
  const min = rawMin === 0 ? 0 : rawMin - (rawMax - rawMin) * 0.08
  const span = max - min || 1

  const x = (index: number) =>
    padding.left + (index / (data.length - 1)) * plotWidth
  const y = (value: number) =>
    padding.top + plotHeight - ((value - min) / span) * plotHeight

  const linePath = data.map((d, i) => `${i === 0 ? 'M' : 'L'} ${x(i)} ${y(d.value)}`).join(' ')
  const areaPath = `${linePath} L ${x(data.length - 1)} ${padding.top + plotHeight} L ${x(0)} ${
    padding.top + plotHeight
  } Z`

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="h-auto w-full"
      role="img"
      aria-label={`Line chart with ${data.length} points from ${first.label} to ${last.label}`}
    >
      {[0, 0.25, 0.5, 0.75, 1].map((fraction) => {
        const yPos = padding.top + plotHeight * (1 - fraction)
        const value = min + span * fraction
        return (
          <g key={fraction}>
            <line
              x1={padding.left}
              x2={width - padding.right}
              y1={yPos}
              y2={yPos}
              stroke="#1e293b"
              strokeWidth={1}
            />
            <text
              x={padding.left - 8}
              y={yPos + 3}
              textAnchor="end"
              fontSize={10}
              fill={AXIS_TEXT}
              fontFamily="ui-monospace, monospace"
            >
              {axisFormat(value)}
            </text>
          </g>
        )
      })}

      {area && <path d={areaPath} fill={color} opacity={0.12} />}
      <path d={linePath} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />

      {data.map((point, index) => (
        <g key={`${point.label}-${index}`}>
          <circle cx={x(index)} cy={y(point.value)} r={2.5} fill={color}>
            <title>{`${point.label}: ${valueFormat(point.value)}`}</title>
          </circle>
          {index % labelEvery === 0 && (
            <text
              x={x(index)}
              y={height - padding.bottom + 15}
              textAnchor="middle"
              fontSize={10}
              fill={AXIS_TEXT}
            >
              {truncate(point.label, 8)}
            </text>
          )}
        </g>
      ))}
    </svg>
  )
}

/**
 * A magnitude bar with no axis - used inside KPI cards where the point is
 * "how does this one number compare with the largest one", not "read the value
 * off a scale". The value is printed next to it regardless, because a bar with
 * no label is decoration.
 */
export function MiniBar({
  value,
  max,
  color = '#38bdf8',
}: {
  value: number
  max: number
  color?: string | undefined
}) {
  const fraction = max > 0 ? Math.min(1, Math.abs(value) / max) : 0
  return (
    <div className="h-1 w-full overflow-hidden rounded-full bg-slate-200">
      <div
        className="h-full rounded-full transition-[width] duration-300"
        style={{ width: `${fraction * 100}%`, backgroundColor: color }}
      />
    </div>
  )
}

function truncate(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, Math.max(1, max - 1))}\u2026` : value
}