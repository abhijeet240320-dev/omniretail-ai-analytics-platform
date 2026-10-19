import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * One hook for every async read in the app.
 *
 * WHY PAGES DO NOT KEEP THEIR OWN `loading`/`error` STATE
 * -------------------------------------------------------
 * The original App had four pieces of state and one bug came straight out of
 * that: the analyst's error and the catalogue's error shared a single
 * `error` slot, so an analyst failure was swallowed while the previous answer
 * stayed on screen. Shared state between unrelated requests is the failure
 * mode, not shared loading spinners.
 *
 * This hook gives every request its OWN slot, and adds the two things a page
 * always forgets:
 *
 *   - a `cancelled` flag, so a response that arrives after the user has moved
 *     on does not overwrite the new page's state;
 *   - a `reload`, so every error state has a retry button that actually
 *     refetches instead of telling the user to refresh.
 */

export interface Resource<T> {
  data: T | null
  error: string | null
  loading: boolean
  /** True once a request has completed at least once, successfully or not. */
  settled: boolean
  reload: () => void
}

export function useResource<T>(
  fetcher: () => Promise<T>,
  deps: unknown[],
  options: { enabled?: boolean } = {},
): Resource<T> {
  const enabled = options.enabled ?? true
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [settled, setSettled] = useState(false)
  const [nonce, setNonce] = useState(0)

  // Kept in a ref so changing the fetcher identity does not re-trigger the
  // effect. `api.ask` and friends are stable members of the `api` object, and an
  // inline arrow in a page would otherwise refetch on every render.
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    if (!enabled) {
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)

    fetcherRef
      .current()
      .then((value) => {
        if (cancelled) return
        setData(value)
      })
      .catch((cause: unknown) => {
        if (cancelled) return
        setError(describe(cause))
        // A failed refetch must not leave a stale success on screen: the user
        // would be reading the previous answer as if it were the new one.
        setData(null)
      })
      .finally(() => {
        if (cancelled) return
        setLoading(false)
        setSettled(true)
      })

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce, enabled])

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  return { data, error, loading, settled, reload }
}

export function describe(cause: unknown): string {
  if (cause instanceof Error) {
    // The API client already folds `detail` into the message where one exists.
    return cause.message
  }
  return String(cause)
}