import type { ReactNode } from 'react'

/**
 * Loading, empty and error states.
 *
 * THE RULE THESE ENCODE
 * ---------------------
 * A backend failure is never hidden and never dressed up as "no data".
 *
 * The empty state says the request SUCCEEDED and there was nothing to show.
 * The error state says the request FAILED, shows the message the API actually
 * returned, and offers a retry that refetches. Collapsing the two is how a
 * broken backend ends up looking like an empty database, which is a much worse
 * failure because it is silently wrong rather than visibly broken.
 */

export function Skeleton({ className = '' }: { className?: string | undefined }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />
}

export function SkeletonCards({
  count = 4,
  className = '',
}: {
  count?: number | undefined
  className?: string | undefined
}) {
  return (
    <div className={`grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4 ${className}`}>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="surface p-4">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="mt-3 h-7 w-28" />
          <Skeleton className="mt-3 h-3 w-20" />
        </div>
      ))}
    </div>
  )
}

export function SkeletonTable({ rows = 6, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-2" aria-hidden="true">
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="flex gap-3">
          {Array.from({ length: cols }, (_, c) => (
            <Skeleton
              key={c}
              className="h-6 flex-1"
              // Varying widths read as a table; a uniform grid reads as a bug.
            />
          ))}
        </div>
      ))}
    </div>
  )
}

export function LoadingBlock({
  label = 'Loading',
  rows = 3,
}: {
  label?: string | undefined
  rows?: number | undefined
}) {
  return (
    <div role="status" aria-live="polite" aria-busy="true">
      <span className="sr-only">{label}</span>
      <div className="mb-3 flex items-center gap-2 text-xs text-ink-500">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-gold-500" />
        {label}
      </div>
      <div className="space-y-2">
        {Array.from({ length: rows }, (_, i) => (
          <Skeleton key={i} className="h-5 w-full" />
        ))}
      </div>
    </div>
  )
}

export function EmptyState({
  title,
  description,
  action,
  icon = 'M4 7h16M4 12h16M4 17h10',
}: {
  title: string
  // `| undefined` because callers pass through values that may be absent and
  // exactOptionalPropertyTypes distinguishes that from "not provided".
  description?: string | undefined
  action?: ReactNode | undefined
  icon?: string | undefined
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50 px-6 py-10 text-center">
      <svg
        viewBox="0 0 24 24"
        className="h-7 w-7 text-ink-600"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        aria-hidden="true"
      >
        <path d={icon} />
      </svg>
      <p className="mt-3 text-sm font-medium text-ink-900">{title}</p>
      {description && (
        <p className="mt-1 max-w-md text-xs leading-relaxed text-ink-500">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

/**
 * The error state. `hint` is where a page tells the user what to do about it -
 * usually the exact command that produces the missing artefact, because in this
 * project the most common API errors are "the run has not happened yet".
 */
export function ErrorState({
  title = 'Could not load this data',
  message,
  hint,
  onRetry,
  retrying = false,
}: {
  title?: string | undefined
  message: string
  hint?: ReactNode | undefined
  onRetry?: (() => void) | undefined
  retrying?: boolean | undefined
}) {
  return (
    <div
      role="alert"
      className="rounded-lg border border-red-400/30 bg-red-500/[0.06] px-4 py-4"
    >
      <div className="flex items-start gap-3">
        <svg
          viewBox="0 0 24 24"
          className="mt-0.5 h-4 w-4 shrink-0 text-red-600"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          aria-hidden="true"
        >
          <path d="M12 8v5M12 16h.01M10.3 3.9 2.4 17.5A2 2 0 004.1 20.5h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" />
        </svg>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-red-900">{title}</p>
          <p className="mt-1 break-words text-xs leading-relaxed text-red-700/80">
            {message}
          </p>
          {hint && <div className="mt-2 text-2xs leading-relaxed text-ink-500">{hint}</div>}
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              disabled={retrying}
              className="btn btn-secondary mt-3 px-2.5 py-1.5 text-xs"
            >
              {retrying ? 'Retrying...' : 'Retry'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * A callout for something the reader must not miss: the no-combined-total rule,
 * the false-positive caveat on anomalies, the forecast-is-a-benchmark note.
 *
 * Deliberately quieter than an error. These are facts about the data, not
 * faults in the system, and dressing them in red would train people to ignore
 * the one state that does mean something is broken.
 */
export function Notice({
  title,
  children,
  tone = 'neutral',
}: {
  title: string
  children: ReactNode
  tone?: 'neutral' | 'warn' | 'info' | undefined
}) {
  const tones = {
    neutral: 'border-slate-300 bg-slate-100/60',
    warn: 'border-amber-400/30 bg-amber-500/[0.08]',
    info: 'border-sky-400/30 bg-sky-500/[0.08]',
  }
  return (
    <div className={`rounded-lg border px-4 py-3 ${tones[tone]}`}>
      <p className="text-xs font-medium text-ink-900">{title}</p>
      <div className="mt-1 text-xs leading-relaxed text-ink-500">{children}</div>
    </div>
  )
}