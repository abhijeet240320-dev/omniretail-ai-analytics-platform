import { useState } from 'react'
import type { AnalystResponse } from '../api/client'
import { stageLabel, stageTone } from '../format'
import { sourceColor } from '../lib/sourceMeta'
import { Badge, SourceChip } from '../components/ui/Card'
import { DataTable } from '../components/ui/DataTable'
import { Notice } from '../components/ui/States'

/**
 * The AI analyst conversation.
 *
 * THE DESIGN DECISION THAT MATTERS
 * --------------------------------
 * A refusal is rendered as a RESULT - in full, with its reason, the stage that
 * refused it, the rule that fired and any correction the system suggests - not
 * as an error banner, not as an empty state, and not behind "something went
 * wrong".
 *
 * The reason is that a refusal is the EXPECTED outcome for most questions worth
 * asking. "What is the total OmniRetail revenue?" has no answer, and saying so
 * clearly is the single most useful thing the system can do. Rendering that as a
 * failure teaches users the analyst is broken and makes correct behaviour look
 * like an outage.
 *
 * The stage is shown prominently because it is what makes a refusal debuggable:
 * "no evidence in the corpus" and "the SQL validator refused it" are different
 * problems with completely different fixes.
 *
 * WHAT IS NEVER RENDERED AS HTML
 * ------------------------------
 * The answer body is backend-assembled markdown shown in a `<pre>`. Converting it
 * to HTML in the browser would be an injection surface with no benefit, and the
 * verified numbers inside it must stay greppable against the JSON.
 */

export interface AnalystProps {
  result: AnalystResponse | null
  loading: boolean
  error: string | null
  onAsk: (question: string) => void
}

/**
 * Example questions, deliberately covering BOTH outcomes.
 *
 * Three of them are refusals, and that is the point: the interesting
 * behaviour of this analyst is what it declines to answer, so the examples a
 * new user clicks should include "What is the total OmniRetail revenue?" and
 * the keyless-table question. If the only examples were the ones that produce
 * numbers, the feature that distinguishes this from a SQL console would be
 * invisible until someone typed a question that triggered it.
 */
const EXAMPLES = [
  { label: 'olist revenue by state', source: 'olist' as const },
  { label: 'dataco margin by department', source: 'dataco' as const },
  { label: 'How many dunnhumby customers are there?', source: 'dunnhumby' as const },
  { label: 'm5 store comparison', source: 'm5' as const },
  { label: 'What is the total OmniRetail revenue?', source: null },
  {
    label: 'what is the primary key of dunnhumby.coupon_redemptions?',
    source: null,
  },
]

export function AnalystPanel({ result, loading, error, onAsk }: AnalystProps) {
  const [question, setQuestion] = useState('')

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    if (question.trim()) onAsk(question.trim())
  }

  return (
    <div className="animate-fade-in">
      <form onSubmit={submit} className="surface p-4 sm:p-5">
        <label htmlFor="ask-input" className="field-label">
          Ask a question about the data
        </label>
        <div className="flex flex-col gap-3 sm:flex-row">
          <input
            id="ask-input"
            type="text"
            value={question}
            className="field flex-1"
            placeholder="e.g. olist revenue by state"
            onChange={(event) => setQuestion(event.target.value)}
            autoComplete="off"
            spellCheck={false}
            aria-describedby="ask-hint"
          />
          <button
            type="submit"
            className="btn btn-primary sm:w-28"
            disabled={loading || !question.trim()}
          >
            {loading ? 'Asking...' : 'Ask'}
          </button>
        </div>
        <p id="ask-hint" className="mt-2 text-2xs leading-relaxed text-ink-500">
          The analyst routes the question to a dataset, retrieves the relevant
          schema, generates SQL, validates it, runs it read-only, and verifies
          the answer against the rows the query actually returned. It refuses
          rather than guesses.
        </p>

        <div className="mt-4" role="group" aria-label="Example questions">
          <p className="label mb-2">Try one</p>
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLES.map((example) => (
              <button
                key={example.label}
                type="button"
                onClick={() => {
                  setQuestion(example.label)
                  onAsk(example.label)
                }}
                className="focus-ring rounded-md border border-slate-300 bg-slate-100 px-2 py-1
                  text-2xs text-ink-700 transition-colors hover:border-ink-500 hover:text-ink-900"
              >
                {example.source && (
                  <span
                    aria-hidden="true"
                    className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle"
                    style={{ backgroundColor: sourceColor(example.source) }}
                  />
                )}
                {example.label}
              </button>
            ))}
          </div>
        </div>
      </form>

      {error && (
        <div className="mt-4">
          <Notice title="The request failed" tone="warn">
            {error}
          </Notice>
        </div>
      )}

      {loading && !result && (
        <div className="surface mt-4 p-5" role="status" aria-live="polite" aria-busy="true">
          <div className="flex items-center gap-2 text-sm text-ink-700">
            <span className="h-2 w-2 animate-pulse-ring rounded-full bg-gold-400" />
            Working through the pipeline
          </div>
          <PipelineRail />
        </div>
      )}

      {result && !result.answered && !loading && <Refusal result={result} />}
      {result && result.answered && !loading && <Answer result={result} />}
    </div>
  )
}

function Refusal({ result }: { result: AnalystResponse }) {
  const tone = stageTone(result.stopped_at)
  const border =
    tone === 'refusal'
      ? 'border-red-500/30 bg-red-500/[0.06]'
      : tone === 'warning'
        ? 'border-amber-400/30 bg-amber-500/[0.08]'
        : 'border-slate-300 bg-slate-100/40'

  return (
    <section className={`mt-4 rounded-xl border p-5 ${border}`} role="status">
      <div className="flex flex-wrap items-center gap-2">
        {/* This is the requested behaviour, not a failure - so it is labelled as
            a deliberate decision rather than dressed as an error. */}
        <span
          className={`badge ${
            tone === 'refusal' ? 'badge-bad' : tone === 'warning' ? 'badge-warn' : 'badge-neutral'
          }`}
        >
          refused at: {stageLabel(result.stopped_at)}
        </span>
        <span className="text-sm font-medium text-ink-900">No answer given</span>
        {result.source && <SourceChip source={result.source} />}
      </div>

      <p className="mt-3 whitespace-pre-line text-sm leading-relaxed text-ink-700">
        {result.reason}
      </p>

      {result.matched_rules.length > 0 && (
        <p className="mt-3 text-2xs text-ink-500">
          Guard rule{matched(result.matched_rules.length)}:{' '}
          <span className="font-mono text-ink-500">
            {result.matched_rules.join(', ')}
          </span>
        </p>
      )}

      {result.corrections.map((correction) => (
        <div key={correction.rule} className="mt-3 surface-quiet p-3">
          <p className="text-xs leading-relaxed text-ink-700">{correction.correction}</p>
          <p className="mt-1 font-mono text-2xs text-ink-500">{correction.citation}</p>
        </div>
      ))}

      {result.candidates.length > 0 && (
        <details className="mt-4 group">
        <summary className="cursor-pointer text-xs font-medium text-ink-700 hover:text-ink-900">
            Closest answerable questions
          </summary>
          <ul className="mt-2 space-y-1.5">
            {result.candidates.map((candidate, index) => {
              const c = candidate as unknown as {
                query: string
                question: string
                score: number
              }
              return (
                <li key={`${c.query}-${index}`} className="text-xs text-ink-500">
                  <span className="font-mono text-ink-700">{c.query}</span>
                  <span className="mx-1.5 text-ink-600">-</span>
                  {c.question}
                </li>
              )
            })}
          </ul>
        </details>
      )}

      {result.pipeline.length > 0 && <Pipeline result={result} />}
    </section>
  )
}

function Answer({ result }: { result: AnalystResponse }) {
  return (
    <section className="surface mt-4 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="good">answered</Badge>
        {result.verified?.ok ? (
          <Badge tone="good">
            {result.verified.checked.length} value
            {result.verified.checked.length === 1 ? '' : 's'} verified
          </Badge>
        ) : (
          <Badge tone="warn">not verified</Badge>
        )}
        {result.source && <SourceChip source={result.source} />}
        {result.query && (
          <span className="font-mono text-2xs text-ink-500">{result.query}</span>
        )}
        <span className="ml-auto text-2xs text-ink-500">
          {result.row_count} of {result.total_rows.toLocaleString('en-US')} rows in{' '}
          {Math.round(result.elapsed_ms)} ms
        </span>
      </div>

      {/*
        The pipeline rail is at the TOP of an answer, not hidden in a
        disclosure. "Which of these six stages ran" is the first question about
        any AI answer, and burying it under a <summary> means nobody asks.
      */}
      <PipelineRail done={result.pipeline} />

      {/* A short, screen-reader-visible list of what actually happened. */}
      <p className="sr-only">
        Pipeline stages completed: {result.pipeline.join(', ')}
      </p>

      <pre className="mt-4 whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-ink-900">
        {result.answer}
      </pre>

      {result.columns.length > 0 && (
        <div className="mt-5">
          <p className="label mb-2">Result rows</p>
          <DataTable columns={result.columns} rows={result.rows} pageSize={10} />
        </div>
      )}

      {result.sql && (
        <details className="mt-4">
          <summary className="cursor-pointer text-xs font-medium text-ink-700 hover:text-ink-900">
            SQL executed (read-only)
          </summary>
          <pre className="code-block mt-2 whitespace-pre-wrap">{result.sql}</pre>
        </details>
      )}

      <Provenance result={result} />
    </section>
  )
}

function Provenance({ result }: { result: AnalystResponse }) {
  return (
    <div className="mt-5 grid grid-cols-1 gap-4 border-t border-slate-300 pt-4 lg:grid-cols-3">
      <div>
        <p className="label mb-2">Pipeline</p>
        <ol className="space-y-1">
          {result.pipeline.map((step, index) => (
            <li key={`${step}-${index}`} className="font-mono text-2xs text-ink-500">
              {String(index + 1).padStart(2, '0')} {step}
            </li>
          ))}
        </ol>
      </div>

      <div>
        <p className="label mb-2">Verification</p>
        {result.verified ? (
          <div className="space-y-1.5 text-2xs text-ink-500">
            <p>
              <span
                className={result.verified.ok ? 'text-emerald-600' : 'text-red-600'}
              >
                {result.verified.ok ? 'passed' : 'failed'}
              </span>{' '}
              - {result.verified.checked.length} value
              {result.verified.checked.length === 1 ? '' : 's'} re-read from the
              returned rows, {result.verified.failures.length} failure
              {result.verified.failures.length === 1 ? '' : 's'}.
            </p>
            {result.verified.checks?.length ? (
              <p className="font-mono text-ink-500">{result.verified.checks.join(' + ')}</p>
            ) : null}
            {result.verified.failures.slice(0, 5).map((failure, index) => (
              <p key={index} className="text-red-600/80">
                {String(failure.detail ?? failure)}
              </p>
            ))}
          </div>
        ) : (
          <p className="text-2xs text-ink-500">No verification block returned.</p>
        )}
      </div>

      <div>
        <p className="label mb-2">
          Evidence{result.citations.length > 0 ? ` (${result.citations.length})` : ''}
        </p>
        {result.citations.length === 0 ? (
          <p className="text-2xs text-ink-500">No documents were cited.</p>
        ) : (
          <ul className="space-y-1">
            {result.citations.map((citation) => (
              <li key={citation} className="break-all font-mono text-2xs text-gold-600/80">
                {citation}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

/**
 * The gates, shown as a rail.
 *
 * THE STAGE NAMES ARE THE SERVER'S, NOT MINE.
 *
 * `analyst.g_analyse` appends to `pipeline` using these six names, each with a
 * `:`-suffixed outcome. An earlier version of this rail guessed the names
 * (`evidence`, `validate`) and rendered two stages that were never emitted -
 * grey, so it looked correct, while the SQL validator was actually invisible.
 * A stage that appears here has to have come back from the server, and a stage
 * the server did not send must stay grey.
 *
 * `test_the_pipeline_rail_uses_the_stage_names_the_server_emits` is what keeps
 * the two lists from drifting again.
 */
const STAGES = [
  'guards',
  'retrieval',
  'translate',
  'sql_validator',
  'execute',
  'verify',
] as const

/**
 * Outcomes that mean the gate REFUSED, and nothing else.
 *
 * An allowlist rather than a denylist on purpose. `retrieval:4-hits` and
 * `execute:5-rows` are successes carrying a count, and an earlier version that
 * only knew `clear` and `ok` painted every one of them red - which is worse
 * than showing nothing, because a rail full of red says the safety gates are
 * failing on every query. New success outcomes added on the backend therefore
 * render correctly here without a frontend change; a NEW REFUSAL outcome would
 * need adding, which is the safer direction to require.
 */
const REFUSED = new Set([
  'rejected',
  'refused',
  'failed',
  'no-evidence',
  'corrected',
])

function PipelineRail({ done = [] }: { done?: readonly string[] }) {
  // The server writes `stage:outcome`; an unadorned `stage` counts as passed.
  const outcomes = new Map<string, string | null>()
  for (const entry of done) {
    const colon = entry.indexOf(':')
    const name = colon === -1 ? entry : entry.slice(0, colon)
    const outcome = colon === -1 ? null : entry.slice(colon + 1)
    outcomes.set(name, outcome)
  }

  return (
    <ol
      className="mt-4 flex flex-wrap items-center gap-x-1 gap-y-2"
      aria-hidden="true"
    >
      {STAGES.map((stage, index) => {
        const reached = outcomes.has(stage)
        const outcome = outcomes.get(stage)
        // `guards:corrected-a,b` refuses the question but names the rule.
        const refused =
          outcome !== undefined &&
          outcome !== null &&
          (REFUSED.has(outcome) || outcome.startsWith('corrected-'))

        const tone = !reached
          ? 'bg-slate-100 text-ink-600'
          : refused
            ? 'bg-red-500/15 text-red-600'
            : 'bg-gold-500/15 text-gold-600'

        return (
          <li key={stage} className="flex items-center gap-1">
            <span
              className={`rounded px-1.5 py-0.5 font-mono text-2xs transition-colors duration-150 ${tone}`}
            >
              {stage}
            </span>
            {index < STAGES.length - 1 && (
              <span className="text-2xs text-ink-700" aria-hidden="true">
                &rsaquo;
              </span>
            )}
          </li>
        )
      })}
    </ol>
  )
}

function Pipeline({ result }: { result: AnalystResponse }) {
  return (
    <details className="mt-4">
      <summary className="cursor-pointer text-xs font-medium text-ink-700 hover:text-ink-900">
        How far it got ({result.pipeline.length} stages)
      </summary>
      <ol className="mt-2 space-y-1">
        {result.pipeline.map((step, index) => (
          <li key={`${step}-${index}`} className="font-mono text-2xs text-ink-500">
            {String(index + 1).padStart(2, '0')} {step}
          </li>
        ))}
      </ol>
    </details>
  )
}

function matched(count: number): string {
  return count > 1 ? 's' : ''
}
