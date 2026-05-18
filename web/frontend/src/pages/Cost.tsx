import { useEffect, useState } from 'react';
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
import './cost.css';

interface ModelEntry {
  cost_usd?: number;
  input_tokens?: number;
  output_tokens?: number;
}

interface BudgetLimits {
  time_elapsed_sec?: number;
  time_limit_sec?: number;
  cost_spent_usd?: number;
  cost_limit_usd?: number;
  turns_used?: number;
  turns_limit?: number;
}

interface BudgetEvent {
  ts?: string;
  cost_usd?: number;
}

interface BudgetData {
  total_cost_usd?: number;
  per_model?: Record<string, ModelEntry>;
  limits?: BudgetLimits;
  events?: BudgetEvent[];
  cost_per_min_series?: { time: string; value: number }[];
}

interface ModelChartEntry {
  model: string;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
}

interface TimeSeriesPoint {
  time: string;
  usd_per_min: number;
}

function computePerMinSeries(events: BudgetEvent[]): TimeSeriesPoint[] {
  if (events.length < 2) return [];
  const sorted = [...events].sort((a, b) =>
    (a.ts ?? '').localeCompare(b.ts ?? '')
  );
  const points: TimeSeriesPoint[] = [];
  for (let i = 1; i < sorted.length; i++) {
    const prev = sorted[i - 1];
    const curr = sorted[i];
    if (!prev.ts || !curr.ts) continue;
    const dtMs = new Date(curr.ts).getTime() - new Date(prev.ts).getTime();
    if (dtMs <= 0) continue;
    const dtMin = dtMs / 60000;
    const costDelta = (curr.cost_usd ?? 0) - (prev.cost_usd ?? 0);
    points.push({
      time: curr.ts.slice(11, 16),
      usd_per_min: parseFloat((costDelta / dtMin).toFixed(6)),
    });
  }
  return points;
}

interface GaugeBarProps {
  label: string;
  used: number;
  limit: number;
  formatValue: (v: number) => string;
  color: string;
}

function GaugeBar({ label, used, limit, formatValue, color }: GaugeBarProps) {
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  return (
    <div className="gauge-item">
      <div className="gauge-label">
        <span>{label}</span>
        <span className="gauge-values">
          {formatValue(used)} / {limit > 0 ? formatValue(limit) : '—'}
        </span>
      </div>
      <div className="gauge-track">
        <div
          className="gauge-fill"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <div className="gauge-pct">{limit > 0 ? `${pct.toFixed(1)}%` : '한도 미설정'}</div>
    </div>
  );
}

export default function Cost() {
  const [data, setData] = useState<BudgetData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function fetchBudget() {
      try {
        const res = await fetch('/api/budget', { signal: controller.signal });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const json: BudgetData = await res.json();
        setData(json);
        setError(null);
      } catch (e) {
        if ((e as Error).name !== 'AbortError') {
          setError((e as Error).message);
        }
      } finally {
        setLoading(false);
      }
    }

    fetchBudget();
    const id = setInterval(fetchBudget, 5000);
    return () => {
      clearInterval(id);
      controller.abort();
    };
  }, []);

  if (loading) return <div className="cost-page">로딩 중…</div>;
  if (error && !data) {
    return (
      <div className="cost-page" style={{ color: 'var(--state-failed)' }}>
        오류: {error}
      </div>
    );
  }

  const modelEntries: ModelChartEntry[] = Object.entries(data?.per_model ?? {}).map(
    ([model, entry]) => ({
      model,
      cost_usd: entry?.cost_usd ?? 0,
      input_tokens: entry?.input_tokens ?? 0,
      output_tokens: entry?.output_tokens ?? 0,
    })
  );

  let timeSeries: TimeSeriesPoint[] = [];
  if (data?.cost_per_min_series && data.cost_per_min_series.length > 0) {
    timeSeries = data.cost_per_min_series.map(p => ({
      time: p.time,
      usd_per_min: p.value,
    }));
  } else if (data?.events && data.events.length > 1) {
    timeSeries = computePerMinSeries(data.events);
  }

  const limits = data?.limits ?? {};
  const timeElapsed = limits.time_elapsed_sec ?? 0;
  const timeLimit = limits.time_limit_sec ?? 0;
  const costSpent = limits.cost_spent_usd ?? (data?.total_cost_usd ?? 0);
  const costLimit = limits.cost_limit_usd ?? 0;
  const turnsUsed = limits.turns_used ?? 0;
  const turnsLimit = limits.turns_limit ?? 0;

  const fmtTime = (v: number) => `${Math.floor(v / 60)}m ${Math.round(v % 60)}s`;
  const fmtCost = (v: number) => `$${v.toFixed(4)}`;
  const fmtTurns = (v: number) => `${Math.round(v)}턴`;

  return (
    <div className="cost-page">
      <h1>비용 현황</h1>
      {error && <div className="cost-error">마지막 폴링 오류: {error}</div>}

      {/* 섹션 1: 모델별 비용 막대차트 */}
      <section className="cost-section">
        <h2>모델별 비용 (USD)</h2>
        {modelEntries.length === 0 ? (
          <p className="cost-empty">데이터 없음</p>
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={modelEntries} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#313244" />
              <XAxis dataKey="model" tick={{ fill: '#a6adc8', fontSize: 12 }} />
              <YAxis
                tick={{ fill: '#a6adc8', fontSize: 11 }}
                tickFormatter={(v: number) => `$${v.toFixed(4)}`}
              />
              <Tooltip
                contentStyle={{ background: '#1e1e2e', border: '1px solid #313244', color: '#cdd6f4' }}
                formatter={(v: number) => [`$${v.toFixed(4)}`, '비용(USD)']}
              />
              <Bar dataKey="cost_usd" name="비용(USD)" fill="#89b4fa" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}

        <h2 style={{ marginTop: '1.5rem' }}>모델별 토큰</h2>
        {modelEntries.length === 0 ? (
          <p className="cost-empty">데이터 없음</p>
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={modelEntries} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#313244" />
              <XAxis dataKey="model" tick={{ fill: '#a6adc8', fontSize: 12 }} />
              <YAxis tick={{ fill: '#a6adc8', fontSize: 11 }} tickFormatter={(v: number) => v.toLocaleString()} />
              <Tooltip
                contentStyle={{ background: '#1e1e2e', border: '1px solid #313244', color: '#cdd6f4' }}
                formatter={(v: number, name: string) => [v.toLocaleString(), name]}
              />
              <Legend wrapperStyle={{ color: '#a6adc8', fontSize: 12 }} />
              <Bar dataKey="input_tokens" name="입력 토큰" fill="#a6e3a1" stackId="tok" />
              <Bar dataKey="output_tokens" name="출력 토큰" fill="#cba6f7" stackId="tok" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </section>

      {/* 섹션 2: 분당 비용 시계열 */}
      <section className="cost-section">
        <h2>분당 비용 시계열 (USD/min)</h2>
        {timeSeries.length === 0 ? (
          <p className="cost-empty">시계열 데이터 없음 — events 필드가 2개 이상이거나 cost_per_min_series 필드가 있어야 합니다</p>
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={timeSeries} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#313244" />
              <XAxis dataKey="time" tick={{ fill: '#a6adc8', fontSize: 11 }} />
              <YAxis
                tick={{ fill: '#a6adc8', fontSize: 11 }}
                tickFormatter={(v: number) => `$${v.toFixed(4)}`}
              />
              <Tooltip
                contentStyle={{ background: '#1e1e2e', border: '1px solid #313244', color: '#cdd6f4' }}
                formatter={(v: number) => [`$${v.toFixed(4)}/min`, 'USD/min']}
              />
              <Line
                type="monotone"
                dataKey="usd_per_min"
                stroke="#f38ba8"
                dot={false}
                strokeWidth={2}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </section>

      {/* 섹션 3: 한도 게이지 3종 */}
      <section className="cost-section">
        <h2>한도 게이지</h2>
        <div className="gauge-grid">
          <GaugeBar
            label="시간 경과"
            used={timeElapsed}
            limit={timeLimit}
            formatValue={fmtTime}
            color="#89b4fa"
          />
          <GaugeBar
            label="비용 소진"
            used={costSpent}
            limit={costLimit}
            formatValue={fmtCost}
            color="#f38ba8"
          />
          <GaugeBar
            label="턴 사용"
            used={turnsUsed}
            limit={turnsLimit}
            formatValue={fmtTurns}
            color="#a6e3a1"
          />
        </div>
      </section>
    </div>
  );
}
