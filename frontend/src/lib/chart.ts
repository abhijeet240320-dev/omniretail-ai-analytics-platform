/**
 * Deciding what to draw, from a query result, without drawing it.
 *
 * WHY THIS IS SEPARATE FROM THE RENDERER
 * ---------------------------------------
 * Every judgement worth making about a chart - is this result chartable, which
 * column is the x axis, is this a time series or a ranked bar, is this value
 * numeric or a string that merely looks like one - is a decision about the
 * DATA, not about pixels. Those decisions are where charts go wrong: a bar
 * chart of a customer identifier, a time axis sorted alphabetically so January
 * follows December. Keeping them here, as pure functions over plain values,
 * means they can be tested without a DOM and without a renderer.
 *
 * THE HONESTY RULE
 * ----------------
 * Nothing here invents, imputes, resamples, sorts or truncates. The series is
 * the rows the API returned, in the order it returned them, and `truncated`
 * travels all the way to the caption so a chart can never imply it shows more
 * of the data than it does. If a result is not chartable, the honest outcome is
 * `null` and the table stands alone - a bad chart is worse than no chart.
 *
 * A numeric-looking STRING is deliberately NOT treated as a number. Postgres
 * hands back `numeric` as a string for exactness, which `Number()` parses
 * correctly; but a column called `order_id` also arrives as digits, and
 * plotting those as a magnitude would invent a relationship between rows that
 * have none. So the x axis must be non-numeric by name AND by content, and the
 * numeric series must be chosen from the remaining columns.
 */

/** A column the result can be drawn against. */
export interface Series {
  /** The x column. */
  x: string
  /** The numeric columns drawn against x, in column order. */
  ys: string[]
  /** 'time' draws a line/area, 'category' draws bars. */
  kind: 'time' | 'category'
}

export interface ChartPlan {
  series: Series
  /** One entry per row, in the order the API returned them. */
  points: Array<{ x: string; values: Record<string, number | null> }>
  /** True when the API returned fewer rows than the query produces. */
  truncated: boolean
  rowCount: number
  totalRows: number | null
}

const DATE_LIKE = /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2})?$/
const MONTH_LIKE = /^\d{4}-\d{2}$/
/** Integer-looking, so `20170101` is not mistaken for a date and `4711` is not either. */
const PERIOD_LIKE = /^\d{4}(-\d{2}){0,2}$/

/** Names that look numeric but are identifiers. Never plotted as a magnitude. */
const IDENTIFIER_NAME =
  /(^|_)(id|ids|key|keys|count|number|no|zip|code)$|(^id$)|(_id$)|(^n$)|(_hash$)/
const PERCENT_NAME = /(^|_)(pct|percent|rate|ratio)$/

function isNumber(value: unknown): value is number {
  if (typeof value === 'number') return Number.isFinite(value)
  if (typeof value !== 'string') return false
  const trimmed = value.trim()
  if (trimmed === '' || !/^-?[\d,]*\.?\d+(?:e[-+]?\d+)?$/i.test(trimmed)) return false
  return Number.isFinite(Number(trimmed.replace(/,/g, '')))
}

function toNumber(value: unknown): number {
  if (typeof value === 'number') return value
  return Number(String(value).trim().replace(/,/g, ''))
}

/** The rows are only a time series if the x column is a date on EVERY row. */
function dateKind(values: string[]): 'time' | 'category' {
  const dated = values.filter((v) => DATE_LIKE.test(v) || MONTH_LIKE.test(v))
  if (dated.length === values.length && values.length > 1) {
    // All YYYY-MM or YYYY-MM-DD. A pure year count of 1 is not a series.
    return 'time'
  }
  return 'category'
}

/**
 * Choose the x column and the numeric columns, or refuse.
 *
 * Refusal is a first-class outcome. `null` means "draw a table, not a chart",
 * and it is returned for the many real results that have no shape at all:
 * one row of aggregates, a key-value listing, free text.
 */
export function planChart(
  columns: string[],
  rows: Array<Record<string, unknown>>,
  options: { truncated?: boolean; totalRows?: number | null } = {},
): ChartPlan | null {
  if (!columns?.length || !rows?.length || rows.length < 2) return null

  // An x column has to be constant-width for the bars to line up. A column
  // that holds 40 distinct long names is a table.
  const categoricalCapable = columns.filter((c) => {
    const distinct = new Set(rows.map((r) => String(r[c] ?? '')))
    return distinct.size >= 2 && distinct.size <= Math.max(rows.length, 12)
  })

  const x = columns.find((c) => {
    if (IDENTIFIER_NAME.test(c)) return false
    const values = rows.map((r) => String(r[c] ?? ''))
    if (values.some((v) => v === '')) return false
    return values.every((v) => DATE_LIKE.test(v) || MONTH_LIKE.test(v) || PERIOD_LIKE.test(v))
  })

  // A real x column that is NOT date-like still works as a category axis
  // (product name, state code, band), provided it is not numeric.
  const categoryX =
    x ??
    categoricalCapable.find((c) => !rows.some((r) => isNumber(r[c])))

  const axis = categoryX
  if (!axis) return null

  const xValues = rows.map((r) => String(r[axis]))

  // Two distinct positions per value means the x axis repeats, which a bar or
  // line chart cannot represent without silently collapsing rows.
  if (new Set(xValues).size !== xValues.length) return null

  const ys = columns.filter(
    (c) =>
      c !== axis &&
      rows.some((r) => isNumber(r[c])) &&
      !IDENTIFIER_NAME.test(c),
  )
  if (ys.length === 0) return null

  const kind = x ? dateKind(xValues) : 'category'

  const points = rows.map((row) => {
    const values: Record<string, number | null> = {}
    for (const y of ys) {
      values[y] = isNumber(row[y]) ? toNumber(row[y]) : null
    }
    return { x: String(row[axis] ?? ''), values }
  })

  return {
    series: { x: axis, ys, kind },
    points,
    truncated: options.truncated ?? false,
    rowCount: rows.length,
    totalRows: options.totalRows ?? null,
  }
}

/**
 * A line chart must not have gaps filled in.
 *
 * `null` is preserved as a break in the line rather than interpolated, because
 * a missing month plotted as a straight segment between its neighbours is a
 * value this project never measured.
 */
export function hasGaps(points: Array<{ values: Record<string, number | null> }>, y: string): boolean {
  return points.some((p) => p.values[y] === null)
}

/** Domain padded to a round number, so the axis top is a number a reader recognises. */
export function niceDomain(
  min: number,
  max: number,
): { min: number; max: number; ticks: number[] } {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { min: 0, max: 1, ticks: [0, 1] }
  if (min === max) {
    // A flat series would divide by zero. Give it a band around the value.
    const pad = Math.abs(min) || 1
    min -= pad * 0.5
    max += pad * 0.5
  }
  const span = max - min
  const step = Math.pow(10, Math.floor(Math.log10(span / 4)))
  const err = (span / 4) / step
  const niceStep = (err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1) * step
  const lo = Math.floor(min / niceStep) * niceStep
  const hi = Math.ceil(max / niceStep) * niceStep
  const ticks: number[] = []
  // Guard the loop: a pathological step would otherwise run until memory ends.
  for (let v = lo; v <= hi + niceStep / 2 && ticks.length < 12; v += niceStep) {
    ticks.push(Number(v.toFixed(10)))
  }
  return { min: lo, max: hi, ticks }
}

/** Every value a chart will draw, ignoring the gaps it will break on. */
export function extent(points: Array<{ values: Record<string, number | null> }>, ys: string[]) {
  let min = Infinity
  let max = -Infinity
  for (const p of points) {
    for (const y of ys) {
      const v = p.values[y]
      // `undefined` can only mean the column is absent from this point, which
      // is the same situation as a gap: not measured, so not plotted.
      if (v === null || v === undefined) continue
      if (v < min) min = v
      if (v > max) max = v
    }
  }
  return min === Infinity ? null : { min, max }
}

/** The caption states what is actually on screen, including what is missing. */
export function caption(plan: ChartPlan, query: string): string {
  const { rowCount, totalRows, truncated } = plan
  const base = `${query}: ${rowCount.toLocaleString('en-US')} row${rowCount === 1 ? '' : 's'} plotted`
  if (!truncated) return `${base} - every row the query returned.`
  if (totalRows === null) {
    return `${base}, the page size. More rows exist; the total was not measured, so the chart is not the whole result.`
  }
  return `${base} of ${totalRows.toLocaleString('en-US')}. The chart shows the first page only - the rows below it are not drawn.`
}

/** True when the axis is a percentage, so the axis says so rather than the reader guessing. */
export function isPercentAxis(name: string): boolean {
  return PERCENT_NAME.test(name)
}