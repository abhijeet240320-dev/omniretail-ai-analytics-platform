import { describe, expect, it } from 'vitest'
// Read as raw text through Vite rather than through `node:fs`. The first
// version of this file used `readFileSync`, which worked under vitest and
// FAILED `tsc --noEmit` - the frontend tsconfig sets `"types": ["vite/client"]`
// and deliberately has no `@types/node`, so Node builtins are untyped here.
// A test that passes while the typecheck is red is worse than no test, because
// CI reports the green half.
import analystTsx from '../pages/Analyst.tsx?raw'
import analystPy from '../../../backend/app/ai/analyst.py?raw'

/**
 * The pipeline rail and the backend must agree on what the stages are called.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The Analyst page renders a rail of six gates. The names in it were written
 * by hand as `guards / evidence / translate / validate / execute / verify`.
 * The backend emits `guards / retrieval / translate / sql_validator / execute
 * / verify`.
 *
 * Two names were wrong, and the UI looked completely fine: the two mismatched
 * stages rendered in the "did not run" colour, which is exactly what a stage
 * that has not been reached yet looks like. So the SQL validator was invisible
 * on every answered query and nothing errored.
 *
 * That is the failure mode this file exists to prevent. The stage names are
 * read out of both files and compared, so a rename on either side is a red
 * build rather than a quietly wrong screen.
 */

const ANALYST_TSX_TEXT = analystTsx
const ANALYST_PY_TEXT = analystPy

describe('the analyst pipeline rail matches the backend', () => {
  it('reads the stage list the component actually renders', () => {
    const tsx = ANALYST_TSX_TEXT
    const block = tsx.slice(tsx.indexOf('const STAGES = ['))
    const rendered = [...block.slice(0, block.indexOf(']')).matchAll(/'([^']+)'/g)].map(
      (m) => m[1],
    )

    expect(rendered.length).toBeGreaterThanOrEqual(6)
    expect(new Set(rendered).size).toBe(rendered.length)
  })

  it('renders every stage name the backend can emit', () => {
    const tsx = ANALYST_TSX_TEXT
    const block = tsx.slice(tsx.indexOf('const STAGES = ['))
    const rendered = new Set(
      [...block.slice(0, block.indexOf(']')).matchAll(/'([^']+)'/g)].map((m) => m[1]),
    )

    const py = ANALYST_PY_TEXT
    const emitted = new Set(
      [...py.matchAll(/pipeline\.append\(\s*["']([a-z_]+)["']/g)].map((m) => m[1]),
    )

    expect(
      emitted.size,
      'no stage names were parsed out of analyst.py; this test has stopped ' +
        'testing anything and would pass for the wrong reason',
    ).toBeGreaterThanOrEqual(6)

    const missing = [...emitted].filter((stage) => !rendered.has(stage))
    expect(
      missing,
      `the backend emits ${missing.join(', ')} but the rail does not render it, ` +
        `so that gate would be invisible on every answer`,
    ).toEqual([])
  })

  it('does not render a stage the backend never emits', () => {
    const tsx = ANALYST_TSX_TEXT
    const block = tsx.slice(tsx.indexOf('const STAGES = ['))
    const rendered = [...block.slice(0, block.indexOf(']')).matchAll(/'([^']+)'/g)].map(
      (m) => m[1],
    )

    const py = ANALYST_PY_TEXT
    const emitted = new Set(
      [...py.matchAll(/pipeline\.append\(\s*["']([a-z_]+)["']/g)].map((m) => m[1]),
    )

    const invented = rendered.filter((stage) => !emitted.has(stage))
    expect(
      invented,
      `the rail renders ${invented.join(', ')}, which the backend never emits; ` +
        `a gate that never runs would sit grey forever and read as "not reached"`,
    ).toEqual([])
  })

  it('the pipeline invariant still holds - an answer ends at verify:ok', () => {
    const py = ANALYST_PY_TEXT
    expect(py).toContain('pipeline.append("verify:ok")')
    // The LLM layer deliberately does NOT append to `pipeline`; its state is
    // reported in the separate `llm` field so the safety trace stays exactly
    // the six gates above. Asserted because changing that would silently alter
    // the meaning of the rail.
    expect(py).toContain('Deliberately NOT appended to `pipeline`')
  })
})
