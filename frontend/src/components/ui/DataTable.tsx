import type { ReactNode } from 'react'
import { useMemo, useState } from 'react'
import { formatCell, formatGrouped } from '../../format'
import { EmptyState } from './States'

/**
 * The one table in the application.
 *
 * WHY IT EXISTS RATHER THAN FIVE HAND-WRITTEN TABLES
 * --------------------------------------------------
 * Five analytics pages, the AI analyst and the anomaly monitor all render rows
 * of `Record<string, unknown>` from the API. Five copies of that is five places
 * for a number-formatting bug, and the analyst's table in particular has a hard
 * requirement: it must render values the way the backend's verification step
 * rendered them, because the user is meant to be able to check the same number
 * in both places. That is not a property five copies can hold.
 *
 * NUMBER FORMATTING IS THE INTERESTING PART
 * -----------------------------------------
 * A column is right-aligned with a tabular figure style when every value it
 * received is a number. That is decided by INSPECTING THE DATA PER COLUMN, not
 * by a hardcoded list of "numeric columns" - the API decides column names, and a
 * hardcoded list would be wrong the moment a query changed its projection.
 *
 * Cell text uses `formatCell` (exact, no separators) because those are values
 * the backend verified. Headers, axis labels and totals use `formatGrouped`
 * (with separators) because those are for reading.
 */

export interface DataTableProps {
  columns: string[]
  rows: Array<Record<string, unknown>>
  /** Rendered under the table, e.g. "showing 50 of 1,204 rows". */
  footer?: string | undefined
  emptyTitle?: string | undefined
  emptyDescription?: string | undefined
  /** Initial page size. 0 disables paging. */
  pageSize?: number | undefined
  /** Extra class on the wrapper, for height constraints. */
  maxHeight?: string | undefined
  onRowClick?: ((row: Record<string, unknown>) => void) | undefined
  /**
   * Per-cell override, for the handful of columns whose value needs more than
   * text: a severity dot, or a day index labelled so it cannot be misread as a
   * date. Returning `undefined` falls back to the default `formatCell`, so a
   * renderer only has to handle the columns it cares about.
   */
  renderCell?: ((column: string, row: Record<string, unknown>) => ReactNode | undefined) | undefined
}

export function DataTable({
  columns,
  rows,
  footer,
  emptyTitle = 'No rows returned',
  emptyDescription,
  pageSize = 25,
  maxHeight = 'max-h-[28rem]',
  onRowClick,
  renderCell,
}: DataTableProps) {
  const [page, setPage] = useState(0)

  const numericColumns = useMemo(() => {
    const flags = new Map<string, boolean>()
    for (const column of columns) {
      const values = rows.map((row) => row[column]).filter((v) => v !== null && v !== undefined)
      // A column counts as numeric only if it has values AND every one of them
      // is a number. One string in the column (a NULL rendered as 'NULL', a
      // note, a status) flips the whole column back to left-aligned, which is
      // the correct answer: a mixed column has no single alignment.
      flags.set(
        column,
        values.length > 0 && values.every((v) => typeof v === 'number' || typeof v === 'bigint'),
      )
    }
    return flags
  }, [columns, rows])

  // A column name that appears in no row is a CALLER bug - a mismatched key,
  // not a fact about the data. Rendering it as a column of NULLs is the worst
  // possible outcome, because "the database had no values" and "I mistyped the
  // key" look identical to a reader. It is surfaced above the table instead.
  const orphaned = columns.filter((column) => !rows.some((row) => column in row))

  const pages = pageSize > 0 ? Math.max(1, Math.ceil(rows.length / pageSize)) : 1
  const safePage = Math.min(page, pages - 1)
  const visible =
    pageSize > 0 ? rows.slice(safePage * pageSize, safePage * pageSize + pageSize) : rows

  if (rows.length === 0) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />
  }

  return (
    <div>
      {orphaned.length > 0 && (
        <p className="mb-2 text-2xs text-amber-600">
          {orphaned.length} column{orphaned.length === 1 ? '' : 's'} returned no
          value: <span className="font-mono">{orphaned.join(', ')}</span>. These
          names are not present in the result rows.
        </p>
      )}
      <div className={`table-wrap ${maxHeight} rounded-lg border border-slate-300`}>
        <table className="data-table">
          <caption className="sr-only">
            Query result, {rows.length} rows, {columns.length} columns
          </caption>
          <thead>
            <tr>
              {columns.map((column) => (
                <th
                  key={column}
                  scope="col"
                  className={numericColumns.get(column) ? 'text-right' : undefined}
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, index) => (
              <tr
                key={index}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={onRowClick ? 'cursor-pointer' : undefined}
              >
                {columns.map((column) => {
                  const numeric = numericColumns.get(column)
                  const custom = renderCell?.(column, row)
                  return (
                    <td
                      key={column}
                      className={
                        // A custom-rendered cell is laid out by its renderer, so
                        // the numeric alignment class is only applied when the
                        // renderer declined.
                        custom !== undefined ? undefined : numeric ? 'num' : undefined
                      }
                    >
                      {custom !== undefined ? custom : formatCell(row[column])}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
        <p className="text-2xs text-ink-500">
          {footer ?? `${rows.length} row${rows.length === 1 ? '' : 's'}`}
        </p>
        {pages > 1 && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="btn btn-ghost px-2 py-1 text-xs"
              disabled={safePage === 0}
              onClick={() => setPage(safePage - 1)}
            >
              Previous
            </button>
            <span className="font-mono text-2xs text-ink-500">
              {safePage + 1} / {pages}
            </span>
            <button
              type="button"
              className="btn btn-ghost px-2 py-1 text-xs"
              disabled={safePage >= pages - 1}
              onClick={() => setPage(safePage + 1)}
            >
              Next
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * A compact key/value table for a single result row. Used where a query returns
 * one row of scalars and a full DataTable would be four columns of header for
 * one line of data.
 */
export function MetricTable({
  metrics,
  order,
}: {
  metrics: Record<string, unknown>
  order?: string[]
}) {
  const keys = order ?? Object.keys(metrics)
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-4">
      {keys.map((key) => (
        <div key={key}>
          <dt className="label">{key}</dt>
          <dd className="mt-1 font-mono text-sm tabular-nums text-ink-900">
            {formatGrouped(metrics[key])}
          </dd>
        </div>
      ))}
    </dl>
  )
}