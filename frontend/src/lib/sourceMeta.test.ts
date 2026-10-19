import { describe, expect, it } from 'vitest'
import {
  NO_COMBINED_TOTAL,
  SOURCE_COLORS,
  SOURCE_DOMAIN,
  SOURCE_SHORT,
  isPercentMetric,
  metricLabel,
  sourceColor,
} from './sourceMeta'
import { PAGES, findPage } from './routes'
import tailwindConfig from '../../tailwind.config'

/**
 * The source palette is the one piece of design data that has to be right in
 * TWO places - `tailwind.config.js` (so `bg-source-olist` resolves) and
 * `sourceMeta.ts` (so charts and inline SVG fills use the same colour). If they
 * drift, Olist renders orange in a badge and a different orange in a chart, on
 * the same screen, and nothing errors - it just looks wrong.
 *
 * `tailwind.config.ts` is TypeScript precisely so this can be a real import
 * rather than a grep: the assertion below then compares two typed values, and
 * a rename on either side is a compile error instead of a stale string match.
 */
describe('source palette', () => {
  const configured = (
    tailwindConfig as unknown as {
      theme: { extend: { colors: { source: Record<string, string> } } }
    }
  ).theme.extend.colors.source

  for (const [source, color] of Object.entries(SOURCE_COLORS)) {
    it(`${source} uses the same colour in Tailwind and in sourceMeta`, () => {
      expect(configured[source]).toBe(color)
    })
  }

  it('covers exactly the four sources', () => {
    const expected = ['dataco', 'dunnhumby', 'm5', 'olist']
    expect(Object.keys(SOURCE_COLORS).sort()).toEqual(expected)
    expect(Object.keys(configured).sort()).toEqual(expected)
  })

  it('has a short name and a domain for every source', () => {
    for (const source of Object.keys(SOURCE_COLORS)) {
      expect(SOURCE_SHORT[source as keyof typeof SOURCE_SHORT]).toBeTruthy()
      expect(SOURCE_DOMAIN[source as keyof typeof SOURCE_DOMAIN]).toBeTruthy()
    }
  })

  it('falls back to grey for an unknown source rather than throwing', () => {
    expect(sourceColor('not_a_source')).toBe('#94a3b8')
  })
})

describe('metric labels', () => {
  it('turns snake_case into words', () => {
    expect(metricLabel('households_transacting')).toBe('Households Transacting')
  })

  it('marks a percentage without guessing its scale', () => {
    // The suffix is kept because `delivered_pct` is already 0..100 and
    // `avg_margin_pct` is a 0..1 fraction. The formatter, not the label,
    // decides which - and it has to be told explicitly.
    expect(metricLabel('delivered_pct')).toBe('Delivered %')
    expect(isPercentMetric('delivered_pct')).toBe(true)
    expect(isPercentMetric('orders')).toBe(false)
  })

  it('never invents a unit for a count', () => {
    expect(metricLabel('stores')).toBe('Stores')
  })
})

describe('the no-combined-total rule is stated in one place', () => {
  it('is a sentence, not a fragment, so it can be rendered as-is', () => {
    expect(NO_COMBINED_TOTAL.length).toBeGreaterThan(40)
    expect(NO_COMBINED_TOTAL).toContain('no shared key space')
  })
})

/**
 * The navigation registry is what makes the shell exhaustive. If a page can be
 * added to `PAGES` but never routed, it renders a blank screen with a working
 * link - so the ids are pinned here and the union is asserted.
 */
describe('page registry', () => {
  it('contains every page the brief asked for', () => {
    expect(PAGES.map((p) => p.id).sort()).toEqual([
      'analyst',
      'anomalies',
      'customers',
      'forecast',
      'marketing',
      'operations',
      'overview',
      'products',
      'sales',
      'sources',
    ])
  })

  it('gives every page a unique id, a label and a description', () => {
    const ids = new Set<string>()
    for (const page of PAGES) {
      expect(ids.has(page.id)).toBe(false)
      ids.add(page.id)
      expect(page.label).toBeTruthy()
      expect(page.description.length).toBeGreaterThan(30)
      expect(page.icon.length).toBeGreaterThan(4)
    }
  })

  it('resolves a page by id and returns undefined for an unknown one', () => {
    expect(findPage('forecast')?.label).toBe('Forecast')
    expect(findPage('nope')).toBeUndefined()
    expect(findPage('')).toBeUndefined()
  })
})