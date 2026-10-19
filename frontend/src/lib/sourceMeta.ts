/**
 * Per-source presentation metadata.
 *
 * WHAT IS IN HERE AND WHAT IS NOT
 * --------------------------------
 * In here: colour tokens, short names, and the one-line "what this company is"
 * that comes from the API's own `/api/overview` label.
 *
 * NOT in here: row counts, date ranges, table counts, revenue, or anything else
 * measurable. Those are queried and arrive in the response. This module must
 * never grow a number, because a number written here is a number nothing
 * verified - it would render identically to a real one and there would be no
 * way to tell them apart later.
 *
 * The colour map is keyed by source id and matches `tailwind.config.js`. Both
 * files have to agree or a source changes colour between the badge and the
 * chart; `SOURCE_COLORS` is asserted against the Tailwind tokens in
 * `sourceMeta.test.ts`.
 */

export const SOURCES = ['olist', 'dataco', 'dunnhumby', 'm5'] as const

export type SourceId = (typeof SOURCES)[number]

/** Short display names. The API supplies the long form; this is the chip text. */
export const SOURCE_SHORT: Record<SourceId, string> = {
  olist: 'Olist',
  dataco: 'DataCo',
  dunnhumby: 'Dunnhumby',
  m5: 'M5',
}

/**
 * Which business domain each source belongs to. Used to group analytics pages,
 * and stated plainly in the UI so nobody assumes the four are comparable.
 */
export const SOURCE_DOMAIN: Record<SourceId, string> = {
  olist: 'Marketplace orders',
  dataco: 'Supply chain',
  dunnhumby: 'Grocery panel',
  m5: 'Retail demand',
}

export const SOURCE_COLORS: Record<SourceId, string> = {
  olist: '#f97316',
  dataco: '#38bdf8',
  dunnhumby: '#a78bfa',
  m5: '#34d399',
}

/** The rule that keeps a total from being constructed by accident. */
export const NO_COMBINED_TOTAL =
  'These four datasets are unrelated companies with no shared key space and no shared definition of a sale. A total across them would have no referent, so none is computed here and none is shown.'

export function isSource(value: string): value is SourceId {
  return (SOURCES as readonly string[]).includes(value)
}

export function sourceColor(source: string): string {
  return isSource(source) ? SOURCE_COLORS[source] : '#94a3b8'
}

export function sourceLabel(source: string): string {
  return isSource(source) ? SOURCE_SHORT[source] : source
}

/**
 * Turn a snake_case API metric name into something readable, without inventing
 * units. `_pct` becomes "percent" and the number is formatted accordingly by
 * the caller; nothing else is rewritten, because a prettier name is not worth a
 * wrong one.
 */
export function metricLabel(key: string): string {
  const words = key.replace(/_pct$/, '').split('_')
  const label = words.map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')
  return key.endsWith('_pct') ? `${label} %` : label
}

/** True when a metric is already expressed 0..100 by the API. */
export function isPercentMetric(key: string): boolean {
  return key.endsWith('_pct')
}

/** True when a metric is a currency-like money figure. */
export function isMoneyMetric(key: string): boolean {
  return /revenue|sales|profit|discount|spend|value|amount/.test(key)
}

/** True when a metric is a percentage that is ALREADY a fraction (0..1). */
export function isFractionMetric(key: string): boolean {
  return /margin_pct$/.test(key) && !key.startsWith('avg_')
}