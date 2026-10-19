import type { CSSProperties, ReactNode } from 'react'

/**
 * Layout primitives.
 *
 * Every page is built from these five, which is what makes ten pages look like
 * one product rather than ten. Nothing here knows about data.
 */

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = '',
  style,
  padded = true,
}: {
  title?: ReactNode | undefined
  subtitle?: ReactNode | undefined
  actions?: ReactNode | undefined
  children: ReactNode
  className?: string | undefined
  /** For the per-source accent border; a token colour cannot express that. */
  style?: CSSProperties | undefined
  padded?: boolean | undefined
}) {
  return (
    <section
      className={`surface ${padded ? 'p-4 sm:p-5' : ''} ${className}`}
      style={style}
    >
      {(title || actions) && (
        <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            {title && <h2 className="section-title">{title}</h2>}
            {subtitle && <p className="mt-1 text-xs text-ink-500">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  )
}

export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: string
  description: string
  actions?: ReactNode | undefined
  /**
   * A short qualifier above the title - the source, or the grain, or what the
   * page is derived from. It is optional because most pages do not need one,
   * and a decorative eyebrow on every page is noise.
   */
  eyebrow?: ReactNode | undefined
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <div className="mb-2">{eyebrow}</div>}
        <h1 className="page-title">{title}</h1>
        <p className="page-subtitle">{description}</p>
      </div>
      {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
    </div>
  )
}

/**
 * A staggered entrance for a grid of cards.
 *
 * `both` fill mode is used HERE, deliberately, where it was removed from the
 * generic `animate-fade-in`. The difference matters: the children of a stagger
 * are skeletons or cards that are about to be replaced by real content, and a
 * page that flashes unstyled content for one frame is worse than one that
 * animates in. `prefers-reduced-motion` collapses the duration to 1ms, so the
 * stall risk is only reachable when motion is explicitly wanted.
 */
export function Stagger({
  children,
  step = 40,
  className = '',
}: {
  children: ReactNode[]
  step?: number | undefined
  className?: string | undefined
}) {
  return (
    <div className={className}>
      {children.map((child, index) => (
        <div
          key={index}
          style={{ animationDelay: `${Math.min(index, 5) * step}ms` }}
        >
          {child}
        </div>
      ))}
    </div>
  )
}

/**
 * The freshness indicator.
 *
 * EVERY number in this product has a moment it became true, and the age of that
 * moment is part of the number. A dashboard that shows "1,941 days" without
 * saying when it was computed is asking the reader to assume it is current,
 * and for a 55-second query behind a 120-second cache that assumption is
 * wrong up to two minutes after it is made.
 *
 * The wording is deliberately plain - "2 min ago", "computed 14:03:11" - and
 * never "fresh" or "live", because those words are claims and this component
 * only has a timestamp.
 */
export function Freshness({
  generatedAt,
  ageSeconds,
  cached,
  className = '',
}: {
  generatedAt?: string | undefined
  ageSeconds?: number | undefined
  cached?: boolean | undefined
  className?: string | undefined
}) {
  const tone = cached ? 'text-ink-500' : 'text-gold-600'
  return (
    <p className={`flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs ${tone} ${className}`}>
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 rounded-full ${
          cached ? 'bg-slate-300' : 'bg-gold-500'
        }`}
      />
      <span>{formatAgeText(ageSeconds)}</span>
      {generatedAt && <span className="text-ink-500">&middot; {generatedAt}</span>}
      <span className="text-ink-500">
        {cached ? 'served from the API cache' : 'computed for this request'}
      </span>
    </p>
  )
}

function formatAgeText(ageSeconds?: number): string {
  if (ageSeconds === undefined || ageSeconds === null) return 'age unknown'
  if (ageSeconds < 5) return 'just now'
  if (ageSeconds < 90) return `${Math.round(ageSeconds)} seconds ago`
  const minutes = Math.round(ageSeconds / 60)
  if (minutes < 90) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  return `${hours} h ago`
}

/**
 * The KPI card.
 *
 * Three decisions worth stating:
 *
 * 1. The label is the metric's real name, not a prettified one. A reader who
 *    cannot tell `avg_order_value` from `revenue` is being asked to trust
 *    rather than to read.
 * 2. The value is monospaced and tabular. In a row of KPIs the digits have to
 *    line up on the decimal point or the row reads as noise.
 * 3. There is no trend arrow unless a real prior value was supplied. A green
 *    up-arrow that is not attached to a real comparison is a decoration that
 *    looks like a measurement, which is the one thing this project will not
 *    put on screen.
 */
export function StatCard({
  label,
  value,
  hint,
  accent,
  loading = false,
  unit,
  title,
}: {
  label: string
  value: string
  hint?: ReactNode | undefined
  /** A source colour, so a KPI can be traced to its dataset at a glance. */
  accent?: string | undefined
  loading?: boolean | undefined
  unit?: string | undefined
  /** Full, unabbreviated metric name for hover and for screen readers. */
  title?: string | undefined
}) {
  return (
    <div className="surface group relative overflow-hidden p-4 transition-colors duration-150 hover:border-slate-300 border-t-2 border-transparent" style={{ borderTopColor: accent || undefined }}>
      {accent && (
        <span
          aria-hidden="true"
          className="absolute inset-x-0 top-0 h-0.5"
          style={{ backgroundColor: accent }}
        />
      )}
      <p className="label truncate" title={title ?? label}>
        {label}
      </p>
      {loading ? (
        <div className="skeleton mt-2 h-7 w-24" />
      ) : (
        <p className="mt-1.5 flex items-baseline gap-1.5">
          <span className="font-mono text-2xl font-semibold tracking-tight text-ink-900 tabular-nums">
            {value}
          </span>
          {unit && <span className="text-2xs text-ink-500">{unit}</span>}
        </p>
      )}
      {hint && !loading && (
        <p className="mt-1.5 text-2xs leading-relaxed text-ink-500">{hint}</p>
      )}
    </div>
  )
}

export function Badge({
  children,
  tone = 'neutral',
  className = '',
}: {
  children: ReactNode
  tone?: 'neutral' | 'good' | 'warn' | 'bad' | 'info' | undefined
  className?: string | undefined
}) {
  const tones: Record<string, string> = {
    neutral: 'badge-neutral',
    good: 'badge-good',
    warn: 'badge-warn',
    bad: 'badge-bad',
    info: 'badge-info',
  }
  return <span className={`badge ${tones[tone]} ${className}`}>{children}</span>
}

/**
 * The source chip.
 *
 * Colour comes from `sourceMeta`, never from a literal here, and the text is
 * the source name - so the attribution is readable by someone who cannot
 * distinguish the colours, which is the whole point of colour-coding a thing by
 * dataset.
 */
export function SourceChip({
  source,
  className = '',
}: {
  source: string
  className?: string | undefined
}) {
  const color = sourceColor(source)
  return (
    <span
      className={`badge ${className}`}
      style={{
        borderColor: `${color}59`,
        backgroundColor: `${color}1a`,
        color,
      }}
    >
      <span
        aria-hidden="true"
        className="h-1.5 w-1.5 rounded-full"
        style={{ backgroundColor: color }}
      />
      {sourceLabel(source)}
    </span>
  )
}

export function Divider({ className = '' }: { className?: string }) {
  return <div className={`divider ${className}`} />
}

/**
 * The hero panel.
 *
 * The one place in the product with a gradient and a larger type scale. It is
 * on the Overview page and nowhere else, on purpose: a hero on every page is a
 * hero on no page. What it carries is the three things a reader needs before
 * they trust anything else - what this is, how much data, and how fresh it is.
 */
export function Hero({
  eyebrow,
  title,
  description,
  children,
  actions,
}: {
  eyebrow?: ReactNode | undefined
  title: string
  description: string
  children?: ReactNode | undefined
  actions?: ReactNode | undefined
}) {
  return (
    <section className="surface relative mb-6 overflow-hidden bg-hero-wash p-6 sm:p-8">
      <div className="relative flex flex-wrap items-end justify-between gap-6">
        <div className="min-w-0 max-w-2xl">
          {eyebrow && <div className="mb-3">{eyebrow}</div>}
          <h1 className="text-2xl font-semibold tracking-tight text-ink-900 sm:text-3xl">
            {title}
          </h1>
          <p className="mt-2 text-sm leading-relaxed text-ink-700">{description}</p>
        </div>
        {actions && <div className="flex shrink-0 gap-2">{actions}</div>}
      </div>
      {children && <div className="relative mt-6">{children}</div>}
    </section>
  )
}

/**
 * A number inside the hero.
 *
 * Smaller and quieter than `StatCard` on purpose: these are context for the
 * source cards below, not the page's primary content, and giving them the same
 * weight as a KPI would flatten the hierarchy the hero exists to create.
 */
export function HeroFigure({
  label,
  value,
  hint,
  accent,
}: {
  label: string
  value: string
  hint?: string | undefined
  accent?: string | undefined
}) {
  return (
    <div className="min-w-0 border-l-2 border-slate-300 pl-3">
      <p className="label flex items-center gap-1.5">
        {accent && (
          <span
            aria-hidden="true"
            className="h-1.5 w-1.5 rounded-full"
            style={{ backgroundColor: accent }}
          />
        )}
        <span className="truncate">{label}</span>
      </p>
      <p className="mt-1 font-mono text-lg font-semibold text-ink-900 tabular-nums">
        {value}
      </p>
      {hint && <p className="mt-0.5 text-2xs text-ink-500">{hint}</p>}
    </div>
  )
}

/**
 * A labelled figure with an explicit unit. The unit is never inferred from the
 * magnitude of the number - a value of 9505985.27 is dollars in one source and
 * units in another, and the API says which.
 */
export function Figure({
  label,
  value,
  unit,
  tone,
}: {
  label: string
  value: string
  unit?: string | undefined
  tone?: 'good' | 'warn' | 'bad' | 'neutral' | undefined
}) {
  return (
    <div>
      <p className="label">{label}</p>
      <p
        className={`mt-1 font-mono text-sm tabular-nums ${
          tone === 'good'
            ? 'text-emerald-600'
            : tone === 'warn'
              ? 'text-amber-600'
              : tone === 'bad'
                ? 'text-red-600'
                : 'text-ink-900'
        }`}
      >
        {value}
        {unit && <span className="ml-1 text-2xs text-ink-500">{unit}</span>}
      </p>
    </div>
  )
}

import { sourceColor, sourceLabel } from '../../lib/sourceMeta'
