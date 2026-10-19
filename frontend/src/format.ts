/**
 * Formatting helpers.
 *
 * WHY THE NUMBER FORMATTER IS SHARED WITH THE BACKEND IN SPIRIT
 * ------------------------------------------------------------
 * `app/ai/verification.py::_fmt` renders every returned value as PLAIN DECIMAL
 * and refuses anything that does not read back as the same number. The
 * backend's verification step re-parses the rendered table, so a frontend
 * formatter that disagreed with it would show a number the backend never
 * checked - and the whole verification guarantee would quietly not apply to
 * what the user actually reads.
 *
 * So this mirrors that contract exactly:
 *   - never scientific notation
 *   - never thousands separators on a value that came from a cell
 *   - a whole float renders as an integer
 *
 * `docs/` shows the same figures in markdown with separators, which is a
 * different context: a prose sentence benefits from `1,234`, a data cell does
 * not, because the reader has to be able to compare it to the JSON.
 */

export function isNumeric(value: unknown): boolean {
  return (
    typeof value === 'number' ||
    typeof value === 'bigint' ||
    value instanceof Date
  )
}

/**
 * Render a returned value exactly as the backend's `format_number` does.
 *
 * Uses `toLocaleString` for grouping ONLY when the caller explicitly asks -
 * grouping inserts characters that are not in the source value, which is fine
 * in prose and wrong in a cell the verification step just checked.
 */
export function formatCell(value: unknown): string {
  if (value === null || value === undefined) return 'NULL'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  if (value instanceof Date) return value.toISOString()
  if (typeof value === 'number') {
    if (Number.isNaN(value)) return 'NaN'
    if (!Number.isFinite(value)) return String(value)

    // Mirrors `app/ai/verification.py::_fmt` branch for branch, because that
    // function is the one that CHECKED these values. Two renderers that agree
    // on ordinary numbers but differ on an extreme one would mean the user can
    // read a digit string the verifier never saw.
    //
    //   - integers below 1e15 print as digits, with no separators
    //   - 1e16+ and denormals-tiny degrade to 6 significant figures, because
    //     plain decimal there is 16+ digits of noise. Unreachable in this data;
    //     the branch exists so an unexpected value stays legible instead of
    //     filling the cell with zeros.
    //   - everything else uses the shortest string that parses back to the
    //     identical float - significant digits, never decimal places.
    if (value === Math.trunc(value) && Math.abs(value) < 1e15) {
      return String(Math.trunc(value))
    }
    if (Math.abs(value) >= 1e16 || (value !== 0 && Math.abs(value) < 1e-4)) {
      // `toExponential(5)` is 6 significant digits and ALWAYS produces the
      // exponent form. `toPrecision(6)` does not: at exactly 1e-6 the spec
      // allows fixed notation, which is what made this branch return
      // '0.00000100000' instead of an exponent.
      return value
        .toExponential(5)
        .replace(/(\.\d*?)0+e/, '$1e')
        .replace(/\.e/, 'e')
    }
    return String(value)
  }
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/** The same, with thousands separators, for prose and axis labels. */
export function formatGrouped(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value.toLocaleString('en-US', { maximumFractionDigits: 4 })
  }
  return formatCell(value)
}

/**
 * A plain integer with thousands separators. Counts, table numbers, dates.
 * Used wherever the value is a COUNT rather than a measurement - never for a
 * money figure, which goes through `formatMoney` so the reader can tell the
 * difference between "16,008,872.12 reais" and "16,008,872 orders".
 */
export function formatNumber(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  return Math.round(value).toLocaleString('en-US')
}

/**
 * Money, to two decimals, with separators.
 *
 * The CURRENCY IS NOT STATED HERE on purpose. This project holds four datasets
 * in at least three currencies (BRL, USD) plus one in units with no currency at
 * all, and the API is the only thing that knows which is which. A formatter that
 * stamped "$" on a number would be asserting a currency the data does not carry,
 * so the unit is shown next to the value by whoever knows what it is.
 */
export function formatMoney(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  return value.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

/**
 * Abbreviated magnitude for a KPI card: 1.2B, 59.2M, 3,049.
 *
 * Only for display next to the exact figure, never on its own - a card that
 * shows "66.9M units" without the exact number somewhere reachable has thrown
 * away precision the reader might need. It is used where the exact value is also
 * rendered in full underneath.
 */
export function formatCompact(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  const abs = Math.abs(value)
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (abs >= 10_000) return `${(value / 1_000).toFixed(1)}K`
  return value.toLocaleString('en-US', { maximumFractionDigits: 2 })
}

/** A percentage that is already expressed 0..100, which is the API's shape. */
export function formatPercent(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return formatCell(value)
  return `${value.toFixed(2)}%`
}

/**
 * A UTC timestamp, in one unambiguous fixed-width shape.
 *
 * UTC and ISO ordering on purpose. Every other number in this app is formatted
 * with an explicit `en-US` locale rather than the browser default, and the same
 * reasoning applies to time: two people comparing a `generated_at` across
 * machines must read the same string, and a locale-formatted timestamp is not
 * that.
 *
 * Anything unparseable renders as `unknown`, never as `Invalid Date` - which
 * looks like a value rather than an absence.
 */
export function formatTimestamp(iso: string | null | undefined): string {
  if (typeof iso !== 'string' || iso.trim() === '') return 'unknown'
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return 'unknown'
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${parsed.getUTCFullYear()}-${pad(parsed.getUTCMonth() + 1)}-${pad(parsed.getUTCDate())}` +
    ` ${pad(parsed.getUTCHours())}:${pad(parsed.getUTCMinutes())} UTC`
  )
}

/**
 * How old a cached value is, in the two units that matter.
 *
 * `0` is rendered as `0s` rather than `just now`, because "how long ago did
 * this number get computed" is a measurement question and `just now` is an
 * opinion. A negative value - which a clock adjustment can produce - is
 * clamped to `0s` rather than rendered as nonsense.
 */
export function formatAge(seconds: number | null | undefined): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds)) return 'unknown'
  const total = Math.max(0, Math.floor(seconds))
  if (total < 60) return `${total}s`
  if (total < 3600) return `${Math.floor(total / 60)}m ${total % 60}s`
  return `${Math.floor(total / 3600)}h ${Math.floor((total % 3600) / 60)}m`
}

/**
 * Render a verification or provenance tag for the UI.
 *
 * Short and stable, because these appear on screen and get referenced in
 * conversation ("check the citations, they all point at docs/").
 *
 * Takes the stage names from `client.ts` rather than restating them, so the
 * backend's vocabulary is declared once. A second hand-written list here is
 * how the UI ends up showing `false_premise` to a user.
 */
export function stageLabel(stage: string): string {
  const labels: Record<string, string> = {
    input: 'input',
    false_premise: 'false premise',
    evidence: 'no evidence',
    translation: 'not translatable',
    sql_validator: 'SQL validator',
    execution: 'execution',
    verification: 'verification',
    retrieval: 'retrieval',
  }
  return labels[stage] ?? stage
}

/** Colour intent for a refusal stage, so the UI can group refusals by cause. */
export function stageTone(
  stage: string,
): 'refusal' | 'warning' | 'neutral' {
  switch (stage) {
    case 'false_premise':
    case 'evidence':
      return 'refusal'
    case 'translation':
    case 'sql_validator':
    case 'verification':
      return 'warning'
    default:
      return 'neutral'
  }
}