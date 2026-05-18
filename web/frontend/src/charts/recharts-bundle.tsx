import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  LineChart,
  Line,
} from 'recharts';
import type { ModelChartEntry, TimeSeriesPoint } from '../pages/Cost';

interface CostChartsProps {
  modelEntries: ModelChartEntry[];
  timeSeries: TimeSeriesPoint[];
}

export default function CostCharts({ modelEntries, timeSeries }: CostChartsProps) {
  return (
    <>
      <section className="cost-section">
        <h2>모델별 비용 (USD)</h2>
        {modelEntries.length === 0 ? (
          <p className="cost-empty">데이터 없음</p>
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={modelEntries} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="model" tick={{ fill: 'var(--text-muted)', fontSize: 12 }} />
              <YAxis
                tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
                tickFormatter={(v: number) => `$${v.toFixed(4)}`}
              />
              <Tooltip
                contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)' }}
                formatter={(v: number) => [`$${v.toFixed(4)}`, '비용(USD)']}
              />
              <Bar dataKey="cost_usd" name="비용(USD)" fill="var(--accent)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}

        <h2 style={{ marginTop: '1.5rem' }}>모델별 토큰</h2>
        {modelEntries.length === 0 ? (
          <p className="cost-empty">데이터 없음</p>
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={modelEntries} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="model" tick={{ fill: 'var(--text-muted)', fontSize: 12 }} />
              <YAxis
                tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
                tickFormatter={(v: number) => v.toLocaleString()}
              />
              <Tooltip
                contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)' }}
                formatter={(v: number, name: string) => [v.toLocaleString(), name]}
              />
              <Legend wrapperStyle={{ color: 'var(--text-muted)', fontSize: 12 }} />
              <Bar dataKey="input_tokens" name="입력 토큰" fill="var(--success)" stackId="tok" />
              <Bar dataKey="output_tokens" name="출력 토큰" fill="var(--warning)" stackId="tok" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </section>

      <section className="cost-section">
        <h2>분당 비용 시계열 (USD/min)</h2>
        {timeSeries.length === 0 ? (
          <p className="cost-empty">시계열 데이터 없음 — events 필드가 2개 이상이거나 cost_per_min_series 필드가 있어야 합니다</p>
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={timeSeries} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="time" tick={{ fill: 'var(--text-muted)', fontSize: 11 }} />
              <YAxis
                tick={{ fill: 'var(--text-muted)', fontSize: 11 }}
                tickFormatter={(v: number) => `$${v.toFixed(4)}`}
              />
              <Tooltip
                contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)' }}
                formatter={(v: number) => [`$${v.toFixed(4)}/min`, 'USD/min']}
              />
              <Line
                type="monotone"
                dataKey="usd_per_min"
                stroke="var(--danger)"
                dot={false}
                strokeWidth={2}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </section>
    </>
  );
}
