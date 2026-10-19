import { useCallback, useEffect, useState } from 'react'
import { DEFAULT_PAGE, findPage } from './routes'
import type { PageId } from './routes'

/**
 * A ~30 line hash router instead of react-router.
 *
 * The application has exactly ten sibling pages and no nesting, no params, no
 * layout routes and no loaders. react-router would add ~50 kB and an API to
 * learn in order to switch between ten strings. A hash also means the app works
 * from `file://` and from any static host with no server rewrite rules, and it
 * keeps the page in the URL so a view is linkable and the back button works.
 *
 * Unknown or empty hashes fall back to the default page rather than rendering
 * nothing: an old bookmark should still land somewhere useful.
 */
export function useHashRoute(): [PageId, (page: PageId) => void] {
  const read = (): PageId => {
    if (typeof window === 'undefined') return DEFAULT_PAGE
    const raw = window.location.hash.replace(/^#\/?/, '')
    const found = findPage(raw)
    return found ? found.id : DEFAULT_PAGE
  }

  const [page, setPage] = useState<PageId>(read)

  useEffect(() => {
    const onChange = () => setPage(read())
    window.addEventListener('hashchange', onChange)
    // Normalise the URL on first load so an empty hash is not left dangling.
    if (!window.location.hash) window.location.hash = `#/${DEFAULT_PAGE}`
    return () => window.removeEventListener('hashchange', onChange)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const navigate = useCallback((next: PageId) => {
    window.location.hash = `#/${next}`
    // scrollTo is a nicety on navigation, not a behaviour the user depends on.
    window.scrollTo({ top: 0, behavior: 'auto' })
  }, [])

  return [page, navigate]
}