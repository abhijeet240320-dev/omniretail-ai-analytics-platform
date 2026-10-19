import { useCallback, useEffect, useState } from 'react'
import { api, getApiKey, setApiKey as persistApiKey } from './api/client'
import type { AnalystResponse, DatabaseStatus, HealthResponse } from './api/client'
import { Sidebar, TopHeader } from './components/Shell'
import { StatusStrip } from './components/StatusStrip'
import { findPage } from './lib/routes'
import type { PageId } from './lib/routes'
import { useHashRoute } from './lib/useHashRoute'
import { describe } from './lib/useResource'
import { AnalystPanel } from './pages/Analyst'
import { AnomaliesPage } from './pages/Anomalies'
import {
  CustomersPage,
  MarketingPage,
  OperationsPage,
  ProductsPage,
  SalesPage,
} from './pages/AnalyticsPages'
import { DataSourcesPage } from './pages/DataSources'
import { ForecastPage } from './pages/Forecast'
import { OverviewPage } from './pages/Overview'

/**
 * The application shell: sidebar, header, status strip, routed page.
 *
 * WHY THE ANALYST KEEPS ITS OWN STATE HERE
 * ----------------------------------------
 * The analyst is a conversational surface, not a resource fetch: it has an
 * answer, a pending request and an error that must NOT be shared with anything
 * else. The original version shared one `error` slot with the query catalogue,
 * and the result was that every analyst failure was swallowed while the previous
 * answer stayed on screen - which looks exactly like the app ignoring your
 * question. One state object, owned by the page that renders it.
 *
 * The catalogue and every other page moved to `useResource`, which gives each
 * request its own slot, a cancellation guard and a working retry.
 *
 * The page registry in `lib/routes.ts` is what makes this exhaustive: adding a
 * page there without a branch here is a TypeScript error, not a blank screen.
 */
export function App() {
  const [page, navigate] = useHashRoute()
  const [navOpen, setNavOpen] = useState(false)

  const [apiKey, setApiKeyState] = useState(getApiKey())

  /**
   * Every change to the key goes through the API client's own setter FIRST.
   *
   * This was React's state setter passed straight to the header, which looked
   * correct - the badge changed to "Key set" - while `getApiKey()` inside the
   * fetch path kept returning the empty string, because nothing had ever
   * written to `inMemoryKey` or `sessionStorage`. Every authenticated request
   * then went out with no `X-API-Key` header and came back 401. The UI reported
   * a credential that was never sent, which is the worst failure mode a key
   * banner can have: it is reassuring, and it is lying.
   *
   * Order matters. Persisting first means there is no window in which React
   * shows one key and the fetches send another.
   */
  const setApiKey = useCallback((next: string) => {
    persistApiKey(next)
    setApiKeyState(next)
  }, [])
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [status, setStatus] = useState<DatabaseStatus | null>(null)

  const [answer, setAnswer] = useState<AnalystResponse | null>(null)
  const [asking, setAsking] = useState(false)
  const [askError, setAskError] = useState<string | null>(null)

  const hasKey = apiKey.length > 0
  const definition = findPage(page)

  // /api/health is the only unauthenticated endpoint, so this runs whether or
  // not a key exists. A user who cannot authenticate needs to be able to tell
  // "the database is down" from "my key is wrong", and only the first is
  // visible without credentials.
  useEffect(() => {
    let cancelled = false
    api
      .health()
      .then((value) => {
        if (!cancelled) setHealth(value)
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setHealth({
            status: 'unreachable',
            database_reachable: false,
            read_only_enforced: false,
            detail: describe(cause),
            statement_timeout_ms: null,
          })
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Clearing the key must clear anything it unlocked, or the next user without
  // a key sees the previous session's answers and thinks they are live.
  useEffect(() => {
    if (!hasKey) setAnswer(null)
  }, [hasKey])

  // /api/status needs the admin:read scope, so this can legitimately 403 for a
  // correctly-scoped analytics key. That is fine: the strip degrades to health
  // plus the key state, and the pages that need /api/status say so themselves.
  useEffect(() => {
    if (!hasKey) {
      setStatus(null)
      return
    }
    let cancelled = false
    api
      .status()
      .then((value) => {
        if (!cancelled) setStatus(value)
      })
      .catch(() => {
        if (!cancelled) setStatus(null)
      })
    return () => {
      cancelled = true
    }
  }, [hasKey])

  const ask = useCallback(async (question: string) => {
    setAsking(true)
    setAskError(null)
    try {
      setAnswer(await api.ask(question))
    } catch (cause) {
      setAskError(describe(cause))
      // Clear the previous answer. Leaving it next to an error banner shows a
      // result for a DIFFERENT question than the one just asked, which is the
      // most misleading state this UI can be in.
      setAnswer(null)
    } finally {
      setAsking(false)
    }
  }, [])

  return (
    <div className="min-h-screen bg-white">
      <Sidebar
        page={page}
        onNavigate={navigate}
        open={navOpen}
        onClose={() => setNavOpen(false)}
      />

      <div className="lg:pl-64">
        <TopHeader
          title={definition?.label ?? 'OmniRetail'}
          apiKey={apiKey}
          onApiKeyChange={setApiKey}
          onOpenNav={() => setNavOpen(true)}
        />
        <StatusStrip health={health} status={status} apiKey={apiKey} />

        <main className="px-4 py-6 sm:px-6 lg:px-8">
          <div className="mx-auto max-w-[100rem]">
            {page === 'overview' && <OverviewPage hasKey={hasKey} />}
            {page === 'sources' && <DataSourcesPage hasKey={hasKey} />}
            {page === 'sales' && <SalesPage hasKey={hasKey} />}
            {page === 'customers' && <CustomersPage hasKey={hasKey} />}
            {page === 'products' && <ProductsPage hasKey={hasKey} />}
            {page === 'marketing' && <MarketingPage hasKey={hasKey} />}
            {page === 'operations' && <OperationsPage hasKey={hasKey} />}
            {page === 'forecast' && <ForecastPage hasKey={hasKey} />}
            {page === 'anomalies' && <AnomaliesPage hasKey={hasKey} />}
            {page === 'analyst' && (
              <>
                <div className="mb-6">
                  <h1 className="page-title">AI Analyst</h1>
                  <p className="page-subtitle">
                    Ask in plain English. The answer is a query that ran against
                    PostgreSQL and was checked against its own rows before you
                    saw it - or a refusal that says exactly why it could not be
                    answered.
                  </p>
                </div>
                <AnalystPanel
                  result={answer}
                  loading={asking}
                  error={askError}
                  onAsk={ask}
                />
              </>
            )}
          </div>
        </main>
      </div>
    </div>
  )
}

export type { PageId }