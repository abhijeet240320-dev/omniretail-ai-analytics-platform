import { describe, expect, it } from 'vitest'
import {
  caption,
  extent,
  hasGaps,
  isPercentAxis,
  niceDomain,
  planChart,
} from './chart'

/**
 * What a chart must never do.
 *
 * These are not snapshot tests of geometry. Each one is a way a dashboard can
 * lie without a single line of the code being wrong-looking: plot an
 * identifier as a magnitude, draw a gap as a straight line, imply 50 rows
 * show the whole result, refuse to draw anything and leave a blank space.
 */

const monthly = [
  { month: '2016-09', revenue: 100, orders: 5 },
  { month: '2016-10', revenue: 180, orders: 7 },
  { month: '2016-11', revenue: 260, orders: 9 },
]

describe('planChart - refusing', () => {
  it('returns null for an empty result', () => {
    expect(planChart(['a'], [])).toBeNull()
  })

  it('returns null for a single row, because one point is not a trend', () => {
    expect(planChart(['month', 'revenue'], [{ month: '2016-09', revenue: 100 }])).toBeNull()
  })

  it('returns null when there is no numeric column', () => {
    expect(
      planChart(['state', 'note'], [
        { state: 'SP', note: 'good' },
        { state: 'RJ', note: 'bad' },
      ]),
    ).toBeNull()
  })

  it('returns null when the x axis repeats a value', () => {
    // Two rows for the same month cannot be a line. Drawing them would either
    // drop one silently or plot the same x twice.
    expect(
      planChart(
        ['month', 'revenue'],
        [
          { month: '2016-09', revenue: 1 },
          { month: '2016-09', revenue: 2 },
        ],
      ),
    ).toBeNull()
  })
})

describe('planChart - refusing to plot an identifier as a magnitude', () => {
  it('will not use an id column as the x axis', () => {
    const plan = planChart(
      ['customer_id', 'revenue'],
      [
        { customer_id: 900, revenue: 10 },
        { customer_id: 901, revenue: 20 },
        { customer_id: 902, revenue: 30 },
      ],
    )
    expect(plan).toBeNull()
  })

  it('will not plot an id column as a series', () => {
    // With `order_id` excluded there is nothing left to plot, so the honest
    // outcome is to refuse rather than draw an axis with no data on it.
    const plan = planChart(
      ['month', 'order_id'],
      [
        { month: '2016-09', order_id: 1 },
        { month: '2016-10', order_id: 2 },
      ],
    )
    expect(plan).toBeNull()
  })

  it('drops an id series but keeps a real one beside it', () => {
    const plan = planChart(
      ['month', 'order_id', 'revenue'],
      [
        { month: '2016-09', order_id: 1, revenue: 10 },
        { month: '2016-10', order_id: 2, revenue: 20 },
      ],
    )
    expect(plan?.series.ys).toEqual(['revenue'])
  })

  it('still plots a real category x axis of non-numeric labels', () => {
    const plan = planChart(
      ['state', 'revenue'],
      [
        { state: 'SP', revenue: 10 },
        { state: 'RJ', revenue: 20 },
        { state: 'MG', revenue: 30 },
      ],
    )
    expect(plan?.series).toMatchObject({ x: 'state', kind: 'category', ys: ['revenue'] })
  })
})

describe('planChart - choosing a time axis', () => {
  it('recognises a date column as a time series', () => {
    const plan = planChart(['month', 'revenue'], monthly)!
    expect(plan.series).toMatchObject({ x: 'month', kind: 'time', ys: ['revenue'] })
  })

  it('does not call a text column a time series', () => {
    const plan = planChart(
      ['state', 'revenue'],
      [
        { state: 'AA', revenue: 1 },
        { state: 'BB', revenue: 2 },
        { state: 'CC', revenue: 3 },
      ],
    )
    expect(plan?.series.kind).toBe('category')
  })

  it('does not call a column a time series when only some values are dates', () => {
    const plan = planChart(
      ['when', 'revenue'],
      [
        { when: '2016-09-01', revenue: 1 },
        { when: 'unknown', revenue: 2 },
        { when: '2016-11-01', revenue: 3 },
      ],
    )
    // `when` is not uniformly a date, so it cannot be a time axis.
    expect(plan?.series.kind).toBe('category')
  })
})

describe('planChart - numbers, not numbers that look like numbers', () => {
  it('parses Postgres numeric strings, which arrive exact as text', () => {
    const plan = planChart(
      ['month', 'revenue'],
      [
        { month: '2016-09', revenue: '100.50' },
        { month: '2016-10', revenue: '180.75' },
      ],
    )!
    expect(plan.points[0]?.values.revenue).toBeCloseTo(100.5)
    expect(plan.points[1]?.values.revenue).toBeCloseTo(180.75)
  })

  it('keeps a non-numeric cell as null instead of coercing it to zero', () => {
    const plan = planChart(
      ['month', 'revenue'],
      [
        { month: '2016-09', revenue: 100 },
        { month: '2016-10', revenue: null },
        { month: '2016-11', revenue: 260 },
      ],
    )!
    expect(plan.points[1]?.values.revenue).toBeNull()
  })

  it('does not reorder the rows the API returned', () => {
    const plan = planChart(['month', 'revenue'], monthly)!
    expect(plan.points.map((p) => p.x)).toEqual(['2016-09', '2016-10', '2016-11'])
  })
})

describe('hasGaps', () => {
  it('is false when every value is present', () => {
    const plan = planChart(['month', 'revenue'], monthly)!
    expect(hasGaps(plan.points, 'revenue')).toBe(false)
  })

  it('is true when a value is missing, so the line can break rather than bridge', () => {
    const plan = planChart(
      ['month', 'revenue'],
      [
        { month: '2016-09', revenue: 100 },
        { month: '2016-10', revenue: null },
        { month: '2016-11', revenue: 260 },
      ],
    )!
    expect(hasGaps(plan.points, 'revenue')).toBe(true)
  })
})

describe('extent and niceDomain', () => {
  it('ignores gaps when measuring', () => {
    const e = extent(
      [
        { values: { r: 10 } },
        { values: { r: null } },
        { values: { r: 30 } },
      ],
      ['r'],
    )!
    expect(e).toEqual({ min: 10, max: 30 })
  })

  it('returns null when there is nothing to measure', () => {
    expect(
      extent(
        [
          { values: { r: null } },
          { values: { r: null } },
        ],
        ['r'],
      ),
    ).toBeNull()
  })

  it('gives a flat series a band, because a zero range divides by zero', () => {
    const d = niceDomain(5, 5)
    expect(d.max).toBeGreaterThan(d.min)
    expect(d.ticks.length).toBeGreaterThan(1)
  })

  it('rounds the axis out to numbers a reader recognises', () => {
    const d = niceDomain(0, 263)
    expect(d.min).toBeLessThanOrEqual(0)
    expect(d.max).toBeGreaterThanOrEqual(263)
    expect(d.max).toBe(300)
  })

  it('never produces an unbounded tick loop', () => {
    const d = niceDomain(0, 1e12)
    expect(d.ticks.length).toBeLessThanOrEqual(12)
  })
})

describe('caption - the chart cannot hide that it is partial', () => {
  it('says every row is plotted when nothing was cut', () => {
    const plan = planChart(['month', 'revenue'], monthly)!
    expect(caption(plan, 'q')).toContain('every row')
  })

  it('names the total when the chart is one page of many', () => {
    const plan = planChart(['month', 'revenue'], monthly, {
      truncated: true,
      totalRows: 1204,
    })!
    const text = caption(plan, 'q')
    expect(text).toContain('1,204')
    expect(text).toContain('first page only')
  })

  it('admits the total is unmeasured rather than implying it is complete', () => {
    const plan = planChart(['month', 'revenue'], monthly, {
      truncated: true,
      totalRows: null,
    })!
    const text = caption(plan, 'q')
    expect(text).toContain('not measured')
    expect(text).not.toContain('every row')
  })

  it('never says "every row" about a truncated result', () => {
    for (const total of [null, 1204, 51]) {
      const plan = planChart(['month', 'revenue'], monthly, {
        truncated: true,
        totalRows: total,
      })!
      expect(caption(plan, 'q')).not.toContain('every row')
    }
  })
})

describe('isPercentAxis', () => {
  it('recognises a percentage column', () => {
    expect(isPercentAxis('cancel_rate')).toBe(true)
    expect(isPercentAxis('conversion_pct')).toBe(true)
  })

  it('does not claim a plain measure is a percentage', () => {
    expect(isPercentAxis('revenue')).toBe(false)
  })
})