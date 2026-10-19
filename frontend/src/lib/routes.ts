/**
 * The page registry - the single source of truth for navigation.
 *
 * WHY A REGISTRY INSTEAD OF SWITCH STATEMENTS IN THE SHELL
 * ---------------------------------------------------------
 * The sidebar, the header and the router all need the same list of pages. The
 * failure mode when that list is written three times is that a page gets added
 * to the sidebar and the shell renders nothing for it - a nav entry that 404s
 * into a blank screen. One array, one place to add a page, and a compile error
 * if the shell forgets to handle one.
 *
 * The analytics pages (Sales, Customers, Products, Marketing, Operations) are
 * all the same component with a different set of catalogued queries. That is
 * not laziness, it is the truth about this system: the backend's unit of work
 * is a named, verified query, and five hand-written bespoke pages would
 * duplicate the same fetch/format/table code five times with five slightly
 * different bugs.
 */

export type PageId =
  | 'overview'
  | 'sources'
  | 'sales'
  | 'customers'
  | 'products'
  | 'marketing'
  | 'operations'
  | 'forecast'
  | 'anomalies'
  | 'analyst'

export type PageGroup = 'platform' | 'analytics' | 'intelligence'

export interface PageDefinition {
  id: PageId
  label: string
  group: PageGroup
  /** One line, shown under the page title. Describes what the page IS. */
  description: string
  /** Hand-written inline SVG path data on a 24x24 grid. */
  icon: string
}

export const PAGES: PageDefinition[] = [
  {
    id: 'overview',
    label: 'Overview',
    group: 'platform',
    description:
      'Per-source KPIs, database health and the four independent datasets. Every figure is queried live.',
    icon: 'M3 12h4l3 8 4-16 3 8h4',
  },
  {
    id: 'sources',
    label: 'Data Sources',
    group: 'platform',
    description:
      'What is loaded, how many rows, which time axis, and what each dataset can and cannot answer.',
    icon: 'M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  },
  {
    id: 'sales',
    label: 'Sales',
    group: 'analytics',
    description: 'Revenue, order flow and geographic concentration, per source.',
    icon: 'M3 17l5-5 4 3 8-8M17 7h4v4',
  },
  {
    id: 'customers',
    label: 'Customers',
    group: 'analytics',
    description: 'Segments, repeat behaviour and household participation.',
    icon: 'M16 20v-1a4 4 0 00-4-4H7a4 4 0 00-4 4v1M9.5 7a3.5 3.5 0 100 7 3.5 3.5 0 000-7M20 20v-1a4 4 0 00-3-3.9M16 3.6a4 4 0 010 6.8',
  },
  {
    id: 'products',
    label: 'Products',
    group: 'analytics',
    description: 'Category margin, category hierarchy and demand scale.',
    icon: 'M20.6 13.4L12 22l-9-9V3h10l7.6 7.6a2 2 0 010 2.8zM7.5 7.5h.01',
  },
  {
    id: 'marketing',
    label: 'Marketing',
    group: 'analytics',
    description: 'Campaign performance, coupon redemption and promotional exposure.',
    icon: 'M3 11l14-6v14L3 13v-2zM7 12.5V18a2 2 0 004 0v-4M20 9.5a2.5 2.5 0 010 5',
  },
  {
    id: 'operations',
    label: 'Operations',
    group: 'analytics',
    description: 'Order flow, delivery and shipping performance, and status mix.',
    icon: 'M3 7h11v8H3zM14 10h4l3 3v2h-7zM7 19a2 2 0 100-4 2 2 0 000 4zM18 19a2 2 0 100-4 2 2 0 000 4z',
  },
  {
    id: 'forecast',
    label: 'Forecast',
    group: 'intelligence',
    description:
      'A completed demand-forecasting benchmark over every M5 series, with its train/test split and accuracy metrics.',
    icon: 'M3 17l5-6 4 4 4-7 5 5M3 21h18',
  },
  {
    id: 'anomalies',
    label: 'Anomalies',
    group: 'intelligence',
    description:
      'Points that departed from their own recent history, each with the baseline and threshold that flagged it.',
    icon: 'M12 3l9 16H3l9-16zM12 9v5M12 17h.01',
  },
  {
    id: 'analyst',
    label: 'AI Analyst',
    group: 'intelligence',
    description:
      'Ask a question in plain English. It routes to the right dataset, generates SQL, validates it, runs it read-only, and verifies the answer against the returned rows.',
    icon: 'M12 3a4 4 0 014 4v1a3 3 0 013 3v3a3 3 0 01-3 3H9a4 4 0 01-4-4V7a4 4 0 014-4zM9 20v1a2 2 0 002 2h2a2 2 0 002-2v-1M2 12h3M19 12h3',
  },
]

export const GROUP_LABELS: Record<PageGroup, string> = {
  platform: 'Platform',
  analytics: 'Analytics',
  intelligence: 'Intelligence',
}

export const DEFAULT_PAGE: PageId = 'overview'

export function findPage(id: string): PageDefinition | undefined {
  return PAGES.find((page) => page.id === id)
}