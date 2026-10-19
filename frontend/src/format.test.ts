import { describe, expect, it } from 'vitest'
import {
  formatAge,
  formatCell,
  formatCompact,
  formatGrouped,
  formatMoney,
  formatNumber,
  formatTimestamp,
  isNumeric,
  stageLabel,
  stageTone,
} from './format'

/**
 * These lock down the FORMATTING CONTRACT between the backend and this UI.
 *
 * The important one is `formatCell`. The backend's verification step re-reads
 * the values it rendered and re-parses them against the raw query output, so the
 * numbers a user READS in a table have to be exactly the numbers that were
 * verified. A formatter that added a thousands separator, rounded to two
 * decimals, or fell back to scientific notation for a large value would break
 * that guarantee in the one place it is visible.
 *
 * `formatGrouped` and friends are the opposite: they are for prose, axis labels
 * and KPI cards, where a human reading `16,008,872` is better served than one
 * reading `16008872`. Both are correct; using the wrong one is the bug.
 */

describe('formatCell - exact, verification-safe', () => {
  it('renders an integer without separators', () => {
    expect(formatCell(21038745)).toBe('21038745')
  })

  it('never uses scientific notation at ordinary business magnitudes', () => {
    // `toLocaleString()` with default options CAN render a large value as
    // 1.600887212E7 depending on the runtime. This asserts it never does at
    // any magnitude this data can actually produce.
    expect(formatCell(16008872.12)).not.toMatch(/E/i)
    expect(formatCell(106723846)).toBe('106723846')
    expect(formatCell(66927173)).toBe('66927173')
    expect(formatCell(-106723846)).toBe('-106723846')
  })

  it('degrades at magnitudes no value in this dataset reaches', () => {
    // Mirrors the documented guard in `_fmt`. Unreachable here; asserted so a
    // future change to one renderer cannot drift away from the other.
    expect(formatCell(1e21)).toBe('1e+21')
    expect(formatCell(0.000001)).toBe('1e-6')
  })

  it('keeps a whole float as an integer', () => {
    expect(formatCell(42.0)).toBe('42')
  })

  it('does not round away precision', () => {
    // Rounding here would show a number the backend never checked.
    expect(formatCell(1.005)).toBe('1.005')
    expect(formatCell(0.1234567890123456789)).toContain('0.1234567890123456')
  })

  it('labels a missing value NULL rather than rendering an empty cell', () => {
    expect(formatCell(null)).toBe('NULL')
    expect(formatCell(undefined)).toBe('NULL')
  })

  it('renders booleans and dates plainly', () => {
    expect(formatCell(true)).toBe('true')
    expect(formatCell(false)).toBe('false')
    expect(formatCell(new Date('2017-11-24T00:00:00Z'))).toBe('2017-11-24T00:00:00.000Z')
  })

  it('stringifies an object rather than printing [object Object]', () => {
    expect(formatCell({ a: 1 })).toBe('{"a":1}')
  })

  it('passes a label through untouched, including the digit-only labels the detector emits', () => {
    // Dunnhumby's time axis is an integer index and the labels really are
    // strings of digits. A formatter that stripped digits would corrupt them,
    // and one that coerced to a number would render them with separators.
    expect(formatCell('643')).toBe('643')
    expect(formatCell('2017-11-24')).toBe('2017-11-24')
  })
})

describe('formatGrouped - prose and axis labels', () => {
  it('adds separators', () => {
    expect(formatGrouped(1234567)).toBe('1,234,567')
  })

  it('caps at four fraction digits', () => {
    expect(formatGrouped(1.234567)).toBe('1.2346')
  })

  it('degrades to formatCell for anything non-numeric', () => {
    expect(formatGrouped(null)).toBe('NULL')
    expect(formatGrouped('abc')).toBe('abc')
  })
})

describe('display formatters', () => {
  it('formats counts with separators and no decimals', () => {
    expect(formatNumber(106723846)).toBe('106,723,846')
    expect(formatNumber(3049)).toBe('3,049')
  })

  it('formats money to exactly two decimals', () => {
    expect(formatMoney(16008872.12)).toBe('16,008,872.12')
    expect(formatMoney(0)).toBe('0.00')
  })

  it('states no currency, because the data does not carry one', () => {
    // BRL, USD and "units, not currency" all appear in this project. The
    // formatter must not stamp a symbol the API never supplied.
    expect(formatMoney(1000)).not.toMatch(/[$€£₹]/)
  })

  it('abbreviates only large magnitudes', () => {
    expect(formatCompact(66927173)).toBe('66.9M')
    expect(formatCompact(59181090)).toBe('59.2M')
    expect(formatCompact(1_500_000_000)).toBe('1.50B')
    expect(formatCompact(3049)).toBe('3,049')
  })

  it('renders a dash rather than NaN for a missing value', () => {
    expect(formatNumber(null)).toBe('-')
    expect(formatNumber(Number.NaN)).toBe('-')
    expect(formatMoney(undefined)).toBe('-')
    expect(formatCompact(null)).toBe('-')
  })
})

describe('isNumeric', () => {
  it('accepts numbers, bigints and dates', () => {
    expect(isNumeric(1)).toBe(true)
    expect(isNumeric(1n)).toBe(true)
    expect(isNumeric(new Date())).toBe(true)
  })

  it('rejects strings, because a numeric string is not a number here', () => {
    // This drives the DataTable's column alignment. Aligning a column of
    // digit-strings right would right-align labels and ids, which are not
    // quantities.
    expect(isNumeric('123')).toBe(false)
    expect(isNumeric(null)).toBe(false)
  })
})

describe('stage labels', () => {
  it('gives every refusal stage a human label', () => {
    expect(stageLabel('false_premise')).toBe('false premise')
    expect(stageLabel('evidence')).toBe('no evidence')
    expect(stageLabel('sql_validator')).toBe('SQL validator')
  })

  it('passes an unknown stage through rather than blanking it', () => {
    expect(stageLabel('brand_new_gate')).toBe('brand_new_gate')
  })

  it('tones a false premise and a missing-evidence refusal as refusals', () => {
    // These are the system's CORRECT behaviour, so they must not share a tone
    // with a validator rejection - the UI shows them differently.
    expect(stageTone('false_premise')).toBe('refusal')
    expect(stageTone('evidence')).toBe('refusal')
    expect(stageTone('sql_validator')).toBe('warning')
    expect(stageTone('anything_else')).toBe('neutral')
  })
})

describe('provenance timestamps', () => {
  it('renders an ISO timestamp as UTC, not as the browser locale', () => {
    // The API sends `2026-10-04T17:49:29.336339+00:00`. Two people comparing
    // generated_at across machines must read the same string, which a
    // locale-formatted timestamp does not guarantee.
    expect(formatTimestamp('2026-10-04T17:49:29.336339+00:00')).toBe(
      '2026-10-04 17:49 UTC',
    )
  })

  it('renders an absent or unparseable timestamp as `unknown`, not `Invalid Date`', () => {
    // `Invalid Date` reads like a value. `unknown` reads like an absence, which
    // is what it is - and this project never invents a placeholder number.
    expect(formatTimestamp(null)).toBe('unknown')
    expect(formatTimestamp(undefined)).toBe('unknown')
    expect(formatTimestamp('')).toBe('unknown')
    expect(formatTimestamp('not a date')).toBe('unknown')
  })

  it('reports cache age in units a reader can act on', () => {
    expect(formatAge(0)).toBe('0s')
    expect(formatAge(0.4)).toBe('0s')
    expect(formatAge(47)).toBe('47s')
    expect(formatAge(60)).toBe('1m 0s')
    expect(formatAge(200)).toBe('3m 20s')
    expect(formatAge(3600)).toBe('1h 0m')
    expect(formatAge(7500)).toBe('2h 5m')
  })

  it('clamps a negative age rather than rendering nonsense', () => {
    // A clock adjustment can produce one. `-3s` on screen would look like a bug
    // in the API rather than what it is.
    expect(formatAge(-3)).toBe('0s')
    expect(formatAge(null)).toBe('unknown')
    expect(formatAge(Number.NaN)).toBe('unknown')
  })
})
