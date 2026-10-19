import { useState } from 'react'
import type { PageDefinition, PageId } from '../lib/routes'
import { GROUP_LABELS, PAGES } from '../lib/routes'
import type { PageGroup } from '../lib/routes'

/**
 * The navigation sidebar.
 *
 * Collapses to a top bar on narrow viewports and to an off-canvas drawer below
 * `lg`. Three states rather than one, because a dashboard that cannot be
 * navigated on a phone is not responsive - it is desktop with a narrow window.
 *
 * The groups are NOT decoration. "Analytics" and "Intelligence" being separate
 * says something true about the system: the first runs queries a human wrote,
 * the second runs models and a constrained agent.
 */

export function Sidebar({
  page,
  onNavigate,
  open,
  onClose,
}: {
  page: PageId
  onNavigate: (page: PageId) => void
  open: boolean
  onClose: () => void
}) {
  const groups: PageGroup[] = ['platform', 'analytics', 'intelligence']

  return (
    <>
      {/* Scrim. Only rendered when the drawer is open on small screens. */}
      {open && (
        <div
          className="fixed inset-0 z-30 bg-white/70 backdrop-blur-sm lg:hidden"
          onClick={onClose}
          aria-hidden="true"
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-slate-200 bg-white
          transition-transform duration-200 lg:translate-x-0
          ${open ? 'translate-x-0' : '-translate-x-full'}`}
        aria-label="Main navigation"
      >
        <div className="flex h-16 shrink-0 items-center gap-3 border-b border-slate-200 px-5">
          <Logo />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold tracking-tight text-ink-900">
              OmniRetail
            </p>
            <p className="truncate text-2xs text-ink-500">AI Analytics Platform</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="btn btn-ghost ml-auto px-1.5 py-1 text-ink-500 lg:hidden"
            aria-label="Close navigation"
          >
            <svg
              viewBox="0 0 24 24"
              className="h-4 w-4"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            >
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-4">
          {groups.map((group) => (
            <div key={group} className="mb-5 last:mb-0">
              <p className="label px-3 pb-2">{GROUP_LABELS[group]}</p>
              <ul className="space-y-0.5">
                {PAGES.filter((p) => p.group === group).map((definition) => (
                  <li key={definition.id}>
                    <NavLink
                      definition={definition}
                      active={page === definition.id}
                      onClick={() => {
                        onNavigate(definition.id)
                        onClose()
                      }}
                    />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <div className="shrink-0 border-t border-slate-200 px-5 py-4">
          <p className="text-2xs leading-relaxed text-ink-500">
            Read-only. Four independent public datasets, no synthetic rows, and
            no total across sources.
          </p>
        </div>
      </aside>
    </>
  )
}

function NavLink({
  definition,
  active,
  onClick,
}: {
  definition: PageDefinition
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
      className={`focus-ring group relative flex w-full items-center gap-2.5 rounded-lg
        py-2 pl-3 pr-3 text-left text-sm transition-colors duration-150
        ${active ? 'bg-gold-500/10 font-medium text-gold-600' : 'text-ink-700 hover:bg-slate-100 hover:text-ink-900'}`}
    >
      {/*
        A rail rather than a filled pill. A pill changes the box, so the label
        shifts horizontally when you navigate; a rail changes only the colour,
        so the text stays exactly where it was. With ten pages that movement is
        the difference between a sidebar that feels solid and one that feels
        like it is re-rendering under you.
      */}
      <span
        aria-hidden="true"
        className={`absolute inset-y-1.5 left-0 w-0.5 rounded-full transition-colors duration-150 ${
          active ? 'bg-gold-500' : 'bg-transparent'
        }`}
      />
      <svg
        viewBox="0 0 24 24"
        className={`h-4 w-4 shrink-0 transition-colors duration-150 ${
          active ? 'text-gold-600' : 'text-ink-500 group-hover:text-ink-700'
        }`}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d={definition.icon} />
      </svg>
      <span className="truncate">{definition.label}</span>
      {active && (
        <span className="ml-auto h-1.5 w-1.5 rounded-full bg-sky-400" aria-hidden="true" />
      )}
    </button>
  )
}

function Logo() {
  return (
    <div
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-sky-500 to-indigo-600"
      aria-hidden="true"
    >
      <svg
        viewBox="0 0 24 24"
        className="h-5 w-5 text-white"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M3 17l5-6 4 4 4-7 5 5" />
      </svg>
    </div>
  )
}

/**
 * The top header.
 *
 * It carries the three things a user needs before trusting anything on screen:
 * where they are, whether the database is reachable, and whether the role is
 * actually read-only. Those two status facts are on every page rather than
 * hidden on a settings screen, because "is this live?" is the first question
 * about any number in a data product.
 */
export function TopHeader({
  title,
  apiKey,
  onApiKeyChange,
  onOpenNav,
}: {
  title: string
  apiKey: string
  onApiKeyChange: (value: string) => void
  onOpenNav: () => void
}) {
  const [draft, setDraft] = useState(apiKey)
  const [open, setOpen] = useState(false)

  return (
    <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-slate-200 bg-white/95 px-4 backdrop-blur sm:px-6">
      <button
        type="button"
        onClick={onOpenNav}
        className="btn btn-ghost px-1.5 py-1.5 lg:hidden"
        aria-label="Open navigation"
      >
        <svg
          viewBox="0 0 24 24"
          className="h-5 w-5"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        >
          <path d="M4 7h16M4 12h16M4 17h16" />
        </svg>
      </button>

      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-ink-900">{title}</p>
      </div>

      <div className="relative">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="btn btn-secondary px-3 py-1.5 text-xs"
          aria-expanded={open}
          aria-haspopup="dialog"
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              apiKey ? 'bg-emerald-400' : 'bg-amber-400'
            }`}
            aria-hidden="true"
          />
          {apiKey ? 'Key set' : 'No key'}
        </button>

        {open && (
          <>
            <div
              className="fixed inset-0 z-10"
              onClick={() => setOpen(false)}
              aria-hidden="true"
            />
            <div
              role="dialog"
              aria-label="API key"
              className="absolute right-0 z-20 mt-2 w-[min(22rem,calc(100vw-2rem))] rounded-xl
                border border-slate-300 bg-slate-50 p-4 shadow-pop animate-slide-up"
            >
              <p className="text-xs font-medium text-ink-700">API key</p>
              <p className="mt-1 text-2xs leading-relaxed text-ink-500">
                Stored in memory and this tab&rsquo;s <code>sessionStorage</code>
                only. Never written to the bundle or to disk.
              </p>
              <label htmlFor="header-api-key" className="sr-only">
                API key
              </label>
              <input
                id="header-api-key"
                type="password"
                className="field mt-3 font-mono text-xs"
                value={draft}
                placeholder="omr_..."
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => setDraft(event.target.value.trim())}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    onApiKeyChange(draft)
                    setOpen(false)
                  }
                }}
              />
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  className="btn btn-primary flex-1 text-xs"
                  onClick={() => {
                    onApiKeyChange(draft)
                    setOpen(false)
                  }}
                >
                  Apply
                </button>
                {apiKey && (
                  <button
                    type="button"
                    className="btn btn-danger text-xs"
                    onClick={() => {
                      setDraft('')
                      onApiKeyChange('')
                      setOpen(false)
                    }}
                  >
                    Clear
                  </button>
                )}
              </div>
              <p className="mt-3 border-t border-slate-300 pt-3 text-2xs leading-relaxed text-ink-500">
                Issue one with{' '}
                <code className="text-ink-500">
                  backend/scripts/db/08_issue_api_key.py issue --label you
                </code>
              </p>
            </div>
          </>
        )}
      </div>
    </header>
  )
}
