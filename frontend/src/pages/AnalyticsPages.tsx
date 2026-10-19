import { AnalyticsPage } from './AnalyticsPage'
import type { AnalyticsPageConfig } from './AnalyticsPage'

/**
 * The five analytics pages.
 *
 * Each one is a configuration, not a component: which sources it draws from,
 * which query leads, and what it is for. The descriptions say plainly that the
 * figures are per-source and not comparable across sources, because that is the
 * single most important thing to know before reading a number on any of them.
 *
 * The `featured` query is chosen from the catalogue by name, so if a query is
 * renamed or removed the page falls back to the catalogue list rather than
 * showing a broken panel - and the breakage is visible rather than silent.
 */

const SALES: AnalyticsPageConfig = {
  title: 'Sales',
  description:
    'Revenue, order flow and geographic concentration, reported per source. The three revenue figures are in three different currencies and three different senses of what a sale is, so they are never added.',
  sources: ['olist', 'dataco', 'dunnhumby', 'm5'],
  featured: 'olist.monthly_revenue_trend',
  related: ['dataco.revenue_by_market', 'dunnhumby.sales_mix_by_department'],
}

const CUSTOMERS: AnalyticsPageConfig = {
  title: 'Customers',
  description:
    'Segments and repeat behaviour. Olist customers, DataCo buyers and Dunnhumby households are three unrelated customer bases with no shared identifier.',
  sources: ['olist', 'dataco', 'dunnhumby'],
  featured: 'olist.customer_repeat_behaviour',
  related: ['dataco.customer_segments', 'dunnhumby.rfm_segment_value'],
}

const PRODUCTS: AnalyticsPageConfig = {
  title: 'Products',
  description:
    'Category margin and demand scale. Product identifiers are source-local: a DataCo product_card_id and an M5 item_id do not refer to the same thing.',
  sources: ['dataco', 'm5', 'olist'],
  featured: 'dataco.margin_by_department',
  related: ['dataco.worst_categories_by_margin', 'm5.demand_scale_by_hierarchy'],
}

const MARKETING: AnalyticsPageConfig = {
  title: 'Marketing',
  description:
    'Campaign performance, coupon redemption and promotional exposure from the Dunnhumby panel, plus event-day impact in the M5 calendar.',
  sources: ['dunnhumby', 'm5'],
  featured: 'dunnhumby.campaign_performance',
  related: ['dunnhumby.coupon_redemption', 'm5.event_day_impact'],
}

const OPERATIONS: AnalyticsPageConfig = {
  title: 'Operations',
  description:
    'Order flow, delivery and shipping. Status vocabularies differ between sources, so the status mix is reported per source rather than merged.',
  sources: ['olist', 'dataco'],
  featured: 'olist.order_status_funnel',
  related: ['dataco.shipping_performance', 'olist.delivery_performance'],
}

export const SALES_PAGE = SALES
export const CUSTOMERS_PAGE = CUSTOMERS
export const PRODUCTS_PAGE = PRODUCTS
export const MARKETING_PAGE = MARKETING
export const OPERATIONS_PAGE = OPERATIONS

export function SalesPage({ hasKey }: { hasKey: boolean }) {
  return <AnalyticsPage config={SALES} hasKey={hasKey} />
}
export function CustomersPage({ hasKey }: { hasKey: boolean }) {
  return <AnalyticsPage config={CUSTOMERS} hasKey={hasKey} />
}
export function ProductsPage({ hasKey }: { hasKey: boolean }) {
  return <AnalyticsPage config={PRODUCTS} hasKey={hasKey} />
}
export function MarketingPage({ hasKey }: { hasKey: boolean }) {
  return <AnalyticsPage config={MARKETING} hasKey={hasKey} />
}
export function OperationsPage({ hasKey }: { hasKey: boolean }) {
  return <AnalyticsPage config={OPERATIONS} hasKey={hasKey} />
}