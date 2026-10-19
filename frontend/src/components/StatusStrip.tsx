import type { DatabaseStatus, HealthResponse } from '../api/client'

/**
 * The status strip.
 *
 * WHY THIS IS ALWAYS VISIBLE RATHER THAN ON A SETTINGS PAGE
 * ---------------------------------------------------------
 * `/api/health` is the only unauthenticated endpoint, so this works before a key
 * exists and after one is cleared. That matters for the exact failure a user is
 * most likely to hit: they paste a wrong key and see a wall of 401s. Being able
 * to see "database reachable / read-only enforced / no key" separates "the
 * backend is down" from "my credential is wrong" at a glance.
 *
 * It also states the row count. A dashboard showing numbers is not much use if
 * you cannot see that it is looking at 106 million rows.
 */
export function StatusStrip({
  health,
  status,
  apiKey,
}: {
  health: HealthResponse | null
  status: DatabaseStatus | null
  apiKey: string
}) {
  const dbOk = health?.database_reachable === true
  const readOnly = health?.read_only_enforced === true
  const selectedOnly = status?.is_select_only === true

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-slate-200 bg-white/60 px-4 py-2 text-2xs sm:px-6">
      <StatusItem
        ok={dbOk}
        label="Database"
        value={health ? (dbOk ? 'reachable' : 'unreachable') : 'checking'}
      />
      <StatusItem
        ok={readOnly}
        label="Read-only"
        value={
          health
            ? readOnly
              ? 'enforced'
              : 'NOT enforced'
            : 'checking'
        }
      />
      {status && (
        <>
          <StatusItem
            ok={selectedOnly}
            label="Role"
            value={status.role}
          />
          <StatusItem
            ok={true}
            label="Schema"
            value={`${status.table_count} tables / ~${status.total_rows.toLocaleString('en-US')} rows`}
          />
          <StatusItem
            ok={status.alembic_revision !== null}
            label="Migration"
            value={status.alembic_revision ?? 'not stamped'}
          />
        </>
      )}
      <StatusItem
        ok={apiKey.length > 0}
        label="API key"
        value={apiKey.length > 0 ? 'authenticated' : 'not set'}
        tone={apiKey.length > 0 ? 'ok' : 'warn'}
      />
    </div>
  )
}

function StatusItem({
  ok,
  label,
  value,
  tone,
}: {
  ok: boolean
  label: string
  value: string
  tone?: 'ok' | 'warn'
}) {
  const toneClass =
    tone === 'warn'
      ? 'text-amber-600'
      : ok
        ? 'text-ink-700'
        : 'text-red-600'
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 rounded-full ${
          tone === 'warn' ? 'bg-amber-400' : ok ? 'bg-emerald-400' : 'bg-red-400'
        }`}
      />
      <span className="text-ink-500">{label}</span>
      <span className={`font-mono ${toneClass}`}>{value}</span>
    </span>
  )
}