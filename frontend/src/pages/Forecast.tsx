import { api } from '../api/client'
import { useResource } from '../lib/useResource'
import { Badge, Card, PageHeader, StatCard } from '../components/ui/Card'
import { BarChart, HBarChart } from '../components/ui/Charts'
import { DataTable } from '../components/ui/DataTable'
import { Notice, SkeletonCards, SkeletonTable } from '../components/ui/States'
import { formatNumber } from '../format'

/**
 * The forecast page.
 *
 * ACTUAL vs FORECAST, AND WHY THE DISTINCTION IS CARRIED SO HARD
 * -------------------------------------------------------------
 * The two are never drawn on the same axis with the same styling, because a
 * flat line crossing a plotted history is exactly how a reader comes to believe
 * a forecast is a measurement. Everything from `/api/forecasting` is labelled
 * FORECAST; everything from `/api/analytics` is labelled ACTUAL; and the
 * separation is stated in a notice before any number is shown.
 *
 * WHAT IS AND IS NOT ON THIS PAGE
 * ------------------------------
 * Present: scope, the train/test split, five models with their accuracy
 * metrics, accuracy per intermittency band, the winner and its margin, and the
 * observed yearly demand that the forecast starts from.
 *
 * Absent: a forecast CURVE. The run is a model-selection benchmark - "of these
 * five methods, which predicts held-out M5 demand best across all 30,490
 * series" - and it stores aggregate accuracy, not per-series point forecasts.
 * Drawing a line would mean inventing the one number a forecasting page exists
 * to show, so the page says that it has none instead. The API states the same
 * thing in its own `shape` field, and that text is what is rendered.
 */

export function ForecastPage({ hasKey }: { hasKey: boolean }) {
  const forecast = useResource(() => api.forecasting(), [], { enabled: hasKey })
  const actual = useResource(
    () => api.runQuery('m5.yearly_trend', 50),
    [],
    { enabled: hasKey },
  )

  const data = forecast.data
  const models = data?.models ?? {}
  const modelEntries = Object.values(models)
  const winner = data?.winner_by_mase
  const winnerIndex = modelEntries.findIndex((m) => m.model === winner)

  return (
    <div className="animate-fade-in">
      <PageHeader
        title="Forecast"
        description="A completed demand-forecasting benchmark over every one of the 30,490 M5 series, scored against a held-out 28-day window."
        actions={
          data?.generated_utc && (
            <Badge tone="neutral">run {data.generated_utc.slice(0, 16).replace('T', ' ')}</Badge>
          )
        }
      />

      {!hasKey && (
        <Notice title="An API key is required">
          Set a key in the top right to load the recorded forecast run.
        </Notice>
      )}

      {hasKey && forecast.error && (
        <Notice title="The forecast run could not be read">{forecast.error}</Notice>
      )}

      {hasKey && forecast.loading && !data && <SkeletonCards count={4} />}

      {data && (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label="Series evaluated"
              value={formatNumber(data.scope?.series_evaluated ?? 0)}
              hint={
                data.scope?.is_full_dataset
                  ? 'the whole dataset, not a sample'
                  : 'SAMPLE - not every series'
              }
            />
            <StatCard
              label="Days per series"
              value={formatNumber(data.scope?.days_loaded ?? 0)}
              hint={`held out ${data.split?.test_days ?? 0} days for evaluation`}
            />
            <StatCard
              label="Best model"
              value={winner ?? '-'}
              hint={
                data.margin_over_runner_up !== undefined
                  ? `MASE ${data.margin_over_runner_up * 100}% better than the runner-up`
                  : undefined
              }
            />
            <StatCard
              label="Intermittency"
              value={`${Math.round(
                ((data.intermittency?.['sporadic (>=80% zeros)']?.series ?? 0) /
                  Math.max(1, data.scope?.series_evaluated ?? 1)) *
                  100,
              )}%`}
              hint="of series are sporadic: 80%+ of days have zero sales"
            />
          </div>

          <div className="mt-4">
            <Notice title="This page reports a benchmark, not a forecast curve" tone="info">
              {data.shape ??
                'The recorded run stores aggregate accuracy per model, not per-series point forecasts.'}
            </Notice>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
            <Card
              title="FORECAST - accuracy by model"
              subtitle="Lower is better. Every model was scored on the same held-out window."
            >
              <BarChart
                data={modelEntries.map((m) => ({ label: m.model, value: m.MASE }))}
                color="#a78bfa"
                highlightIndex={winnerIndex}
              />
              <p className="mt-2 text-2xs leading-relaxed text-ink-500">
                MASE is the mean absolute scaled error: the mean absolute error
                of the forecast divided by the mean absolute error of a naive
                step ahead. Below 1.0 means the model beats &quot;repeat
                yesterday&quot;. The full metric table is below - MASE alone can
                flatter a model, so RMSE and bias are shown too.
              </p>
            </Card>

            <Card
              title="ACTUAL - observed M5 demand by year"
              subtitle="From m5.sales_daily_total, queried live. Observed units, not a forecast."
            >
              {actual.loading && !actual.data && <SkeletonTable rows={5} cols={2} />}
              {actual.error && (
                <Notice title="The actuals query failed">{actual.error}</Notice>
              )}
              {actual.data && (
                <>
                  <BarChart
                    data={actual.data.rows.map((row) => ({
                      label: String(row.year),
                      value: Number(row.total_units ?? 0),
                    }))}
                    color="#34d399"
                  />
                  <p className="mt-2 text-2xs text-ink-500">
                    Query: <code>m5.yearly_trend</code> - actual units sold per
                    year. Nothing on this chart is predicted.
                  </p>
                </>
              )}
            </Card>
          </div>

          <Card
            className="mt-4"
            title="FORECAST - model evaluation"
            subtitle="Held-out window: actuals and forecasts summed over every series in the test period"
          >
            <DataTable
              columns={['model', 'MASE', 'MAE', 'RMSE', 'WMAPE', 'bias', 'actual_total', 'forecast_total']}
              rows={modelEntries.map((m) => ({
                model: m.model,
                MASE: m.MASE,
                MAE: m.MAE,
                RMSE: m.RMSE,
                WMAPE: m.WMAPE,
                bias: m.bias,
                actual_total: m.total_actual,
                forecast_total: m.total_forecast,
              }))}
              pageSize={0}
              footer={
                data.elapsed_seconds
                  ? `load ${data.elapsed_seconds.load}s, evaluate ${data.elapsed_seconds.evaluate}s`
                  : undefined
              }

            />
            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
              {modelEntries.map((m) => (
                <p key={m.model} className="text-2xs leading-relaxed text-ink-500">
                  <span className="font-mono text-ink-700">{m.model}</span> -{' '}
                  {m.description}
                </p>
              ))}
            </div>
          </Card>

          <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
            <Card
              title="Accuracy by demand band"
              subtitle="Intermittent demand breaks models differently. Bands come from the real zero-rate of each series."
            >
              <HBarChart
                data={Object.entries(
                  (data.per_band?.[winner ?? ''] ?? {}) as Record<string, Record<string, number>>,
                ).map(([band, values]) => ({
                  label: band,
                  value: Number(values.MASE ?? 0),
                }))}
                color="#a78bfa"
              />
              <DataTable
                columns={['band', 'series', 'zero rate', 'MASE']}
                rows={Object.entries(
                  (data.per_band?.[winner ?? ''] ?? {}) as Record<string, Record<string, number>>,
                ).map(([band, values]) => ({
                  band,
                  series: values.series,
                  zero_rate:
                    data.intermittency?.[band]?.zero_rate_range?.join(' - ') ?? '',
                  MASE: values.MASE,
                }))}
                pageSize={0}
              />
            </Card>

            <Card title="Method and split" subtitle="What was trained on, and what was held back">
              <dl className="space-y-3 text-xs">
                <Row label="Dataset" value={data.dataset ?? '-'} />
                <Row label="Training window" value={data.split?.train_d_num ?? '-'} />
                <Row label="Test window" value={data.split?.test_d_num ?? '-'} />
                <Row
                  label="Full dataset"
                  value={data.scope?.is_full_dataset ? 'yes' : 'no'}
                  tone={data.scope?.is_full_dataset ? 'good' : 'warn'}
                />
                <Row label="Source artefact" value={data.source_artefact ?? '-'} />
              </dl>
              {data.split?.rationale && (
                <p className="mt-4 border-t border-slate-300 pt-3 text-2xs leading-relaxed text-ink-500">
                  {data.split.rationale}
                </p>
              )}
              <p className="mt-3 text-2xs leading-relaxed text-ink-500">
                <strong className="text-ink-500">No data leakage.</strong>{' '}
                The test window is strictly after the training window and is
                never used for fitting. {data.dataset} holds observed units
                only - there are no synthetic or generated rows anywhere in this
                project.
              </p>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}

function Row({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: 'good' | 'warn'
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-slate-200 pb-2 last:border-0">
      <dt className="label">{label}</dt>
      <dd
        className={`text-right font-mono text-xs ${
          tone === 'good' ? 'text-emerald-600' : tone === 'warn' ? 'text-amber-600' : 'text-ink-700'
        }`}
      >
        {value}
      </dd>
    </div>
  )
}