// Access Insight (Phase 4 S3) — 급여 journey 오버레이 타임라인 + momentum 리더보드.
//
// 가설(사용자 핵심 가설): 위원회(암질심/약평위) 세션 직전 특정 약제 관련 미디어 활동이
// 밀집되면 등재 가능성이 높다. momentum_score 는 그 가설을 위한 **참고 신호**일 뿐
// 확정 예측이 아니다 — 화면 어디에서든 이 점을 숨기지 않는다.
//
// 데이터 소스: agents/access_insight/aggregate.py (leaderboard/journey) — 읽기 전용.
// 로그인 사용자 누구나 조회 가능(관리자 전용 아님).

import { useEffect, useMemo, useState } from 'react';
import {
  Bar, CartesianGrid, ComposedChart, ReferenceDot, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  fetchAccessDrugs, fetchAccessDrugJourney, fetchAccessLeaderboard,
  SIGNAL_TYPES,
  type DrugJourney, type DrugListItem, type DrugMomentum, type JourneyMilestones, type SignalType,
} from '@/api/accessInsight';

// ── 색상 팔레트 ────────────────────────────────────────────────────────────────
// signal_type(막대) 과 milestone(마름모 마커) 는 형태가 다르므로 색만으로 헷갈리지
// 않도록 계열을 분리했다.

const SIGNAL_COLORS: Record<SignalType, string> = {
  GOV_STATEMENT: '#3B82F6',
  PATIENT_PETITION: '#EC4899',
  KOL_OPINION: '#8B5CF6',
  IR_RELEASE: '#F59E0B',
  RESULT_REPORT: '#10B981',
  PRE_AGENDA_LEAK: '#EF4444',
};

const SIGNAL_LABELS: Record<SignalType, string> = {
  GOV_STATEMENT: '정부·국회 발언',
  PATIENT_PETITION: '환자단체 청원',
  KOL_OPINION: '전문가(KOL) 의견',
  IR_RELEASE: 'IR·공시',
  RESULT_REPORT: '실적·결과 보도',
  PRE_AGENDA_LEAK: '사전 안건 유출',
};

interface MilestoneDef {
  key: keyof JourneyMilestones;
  label: string;
  color: string;
  icon: string;
}

const MILESTONE_DEFS: MilestoneDef[] = [
  { key: 'mfds_permit_date', label: '식약처 허가', color: '#6366F1', icon: 'ri-file-shield-2-line' },
  { key: 'amjilsim_pass_date', label: '암질심 통과', color: '#EA580C', icon: 'ri-hospital-line' },
  { key: 'yakpyungwi_pass_date', label: '약평위 통과', color: '#0EA5E9', icon: 'ri-checkbox-circle-line' },
  { key: 'first_reimbursement_date', label: '최초 급여 등재', color: '#EAB308', icon: 'ri-flag-2-line' },
  { key: 'reimbursement_effective_date', label: '급여 발효', color: '#A855F7', icon: 'ri-calendar-check-line' },
];

const WINDOW_OPTIONS = [
  { label: '30일', days: 30 },
  { label: '90일', days: 90 },
  { label: '180일', days: 180 },
] as const;

// ── 시간축 유틸 ────────────────────────────────────────────────────────────────

const DAY_MS = 86400000;

function parseTs(dateStr: string | null | undefined): number | null {
  if (!dateStr) return null;
  const t = Date.parse(dateStr.slice(0, 10));
  return Number.isNaN(t) ? null : t;
}

function monthFloor(ts: number): number {
  const d = new Date(ts);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
}

function addMonths(ts: number, n: number): number {
  const d = new Date(ts);
  return Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + n, 1);
}

function formatMonthTick(ts: number): string {
  const d = new Date(ts);
  return `${d.getUTCFullYear()}.${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

function formatDate(dateStr: string | null | undefined): string {
  return dateStr ? dateStr.slice(0, 10) : '—';
}

type ChartBucket = { monthStart: number; label: string } & Record<SignalType, number>;

interface ChartSession {
  session_id: number;
  ts: number;
  label: string;
  status: string;
}

interface ChartMilestone {
  key: string;
  ts: number;
  label: string;
  color: string;
  icon: string;
  dateStr: string;
}

interface ChartBuild {
  buckets: ChartBucket[];
  domain: [number, number];
  sessionsInRange: ChartSession[];
  sessionsBefore: ChartSession[];
  milestonesInRange: ChartMilestone[];
  milestonesBefore: ChartMilestone[];
  maxStack: number;
}

function buildChart(journey: DrugJourney, momentum: DrugMomentum): ChartBuild {
  const nowTs = Date.now();
  const signalTsList = journey.signals
    .map(s => parseTs(s.published_at))
    .filter((t): t is number => t != null);
  const expectedTs = momentum.expected_session ? parseTs(momentum.expected_session.session_date) : null;
  const latestSignalTs = signalTsList.length ? Math.max(...signalTsList) : null;

  // X축 = 최근 12개월 ~ 예상 세션일 (없으면 최신 신호일/오늘 중 늦은 쪽까지)
  const endAnchor = Math.max(nowTs, expectedTs ?? 0, latestSignalTs ?? 0);
  const endTs = endAnchor + 10 * DAY_MS;
  const startTs = endTs - 365 * DAY_MS;

  const buckets: ChartBucket[] = [];
  const bucketIndex = new Map<number, number>();
  let cursor = monthFloor(startTs);
  const lastMonth = monthFloor(endTs);
  while (cursor <= lastMonth) {
    bucketIndex.set(cursor, buckets.length);
    const empty = { monthStart: cursor, label: formatMonthTick(cursor) } as ChartBucket;
    for (const t of SIGNAL_TYPES) empty[t] = 0;
    buckets.push(empty);
    cursor = addMonths(cursor, 1);
  }

  for (const s of journey.signals) {
    const ts = parseTs(s.published_at);
    if (ts == null || ts < startTs || ts > endTs) continue;
    const idx = bucketIndex.get(monthFloor(ts));
    if (idx == null) continue;
    if ((SIGNAL_TYPES as string[]).includes(s.signal_type)) {
      buckets[idx][s.signal_type as SignalType] += 1;
    }
  }

  const maxStack = buckets.reduce((mx, b) => {
    const sum = SIGNAL_TYPES.reduce((s, t) => s + b[t], 0);
    return Math.max(mx, sum);
  }, 0);

  const allSessions: ChartSession[] = journey.sessions
    .map(s => {
      const ts = parseTs(s.session_date);
      if (ts == null) return null;
      return {
        session_id: s.session_id,
        ts,
        label: `${s.committee_type}${s.ordinal ? ` ${s.ordinal}차` : ''}`,
        status: s.status,
      };
    })
    .filter((s): s is ChartSession => s != null);

  const sessionsInRange = allSessions.filter(s => s.ts >= startTs && s.ts <= endTs);
  const sessionsBefore = allSessions.filter(s => s.ts < startTs);

  const milestonesInRange: ChartMilestone[] = [];
  const milestonesBefore: ChartMilestone[] = [];
  for (const def of MILESTONE_DEFS) {
    const raw = journey.milestones[def.key];
    const ts = parseTs(raw);
    if (ts == null) continue;
    const m: ChartMilestone = { key: def.key, ts, label: def.label, color: def.color, icon: def.icon, dateStr: formatDate(raw) };
    if (ts >= startTs && ts <= endTs) milestonesInRange.push(m);
    else if (ts < startTs) milestonesBefore.push(m);
  }

  return {
    buckets,
    domain: [startTs, endTs],
    sessionsInRange,
    sessionsBefore,
    milestonesInRange,
    milestonesBefore,
    maxStack,
  };
}

// ── 세션 라인 라벨 / 마일스톤 마커 렌더러 (recharts custom render) ────────────────

function renderSessionLabel(text: string, color: string, dashed: boolean, offsetIdx: number) {
  return ({ viewBox }: { viewBox?: { x?: number; y?: number } }) => {
    const x = viewBox?.x ?? 0;
    const y = (viewBox?.y ?? 8) + 10 + (offsetIdx % 2) * 13;
    return (
      <text x={x} y={y} textAnchor="middle" fontSize={9} fontWeight={700} fill={color}>
        {dashed ? '예정 · ' : ''}{text}
      </text>
    );
  };
}

function renderMilestoneDot(color: string, title: string) {
  return ({ cx, cy }: { cx?: number; cy?: number }) => {
    if (cx == null || cy == null) return <g />;
    return (
      <g transform={`translate(${cx}, ${cy}) rotate(45)`}>
        <title>{title}</title>
        <rect x={-4.5} y={-4.5} width={9} height={9} fill={color} stroke="#fff" strokeWidth={1.2} rx={1.5} />
      </g>
    );
  };
}

// ── 추세 화살표 ────────────────────────────────────────────────────────────────

function TrendArrow({ direction, isDark }: { direction: 'up' | 'down' | 'flat'; isDark: boolean }) {
  if (direction === 'up') return <i className="ri-arrow-right-up-line text-emerald-500 text-base flex-shrink-0" title="상승 추세"></i>;
  if (direction === 'down') return <i className="ri-arrow-right-down-line text-red-500 text-base flex-shrink-0" title="하락 추세"></i>;
  return <i className={`ri-subtract-line text-base flex-shrink-0 ${isDark ? 'text-[#4A5568]' : 'text-gray-400'}`} title="변화 없음"></i>;
}

// ── 리더보드 카드 ──────────────────────────────────────────────────────────────

function LeaderboardCard({
  item, rank, active, onClick, isDark,
}: {
  item: DrugMomentum; rank: number; active: boolean; onClick: () => void; isDark: boolean;
}) {
  const cardBg = isDark ? 'bg-[#161B27]' : 'bg-white';
  const cardBorder = active
    ? (isDark ? 'border-[#00E5CC]' : 'border-teal-500')
    : (isDark ? 'border-[#1E2530] hover:border-[#2A3545]' : 'border-gray-200 hover:border-gray-300');
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const textMuted = isDark ? 'text-[#4A5568]' : 'text-gray-400';
  const total = Math.max(item.signal_count, 1);
  const scoreColor = isDark ? 'text-[#00E5CC]' : 'text-teal-600';

  return (
    <button
      onClick={onClick}
      className={`w-full text-left ${cardBg} rounded-xl border ${cardBorder} p-3.5 transition-all cursor-pointer`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`text-[11px] font-bold w-5 text-center flex-shrink-0 ${textMuted}`}>{rank}</span>
          <span className={`text-sm font-bold truncate ${textMain}`}>{item.brand_kr}</span>
        </div>
        <TrendArrow direction={item.trend.direction} isDark={isDark} />
      </div>

      <div className="flex items-end justify-between mt-2 gap-2">
        <div>
          <p className={`text-2xl font-bold leading-none tabular-nums ${scoreColor}`}>{item.momentum_score.toFixed(1)}</p>
          <p className={`text-[10px] mt-1 ${textMuted}`}>momentum · 신호 {item.signal_count}건</p>
        </div>
        {item.session_imminent && item.expected_session && (
          <span className="text-[10px] font-bold px-2 py-1 rounded-full bg-amber-500/15 text-amber-500 whitespace-nowrap flex-shrink-0">
            <i className="ri-alarm-warning-line mr-0.5"></i>
            {item.expected_session.session_date.slice(5)} {item.expected_session.committee_type}
          </span>
        )}
      </div>

      <div className="flex h-1.5 rounded-full overflow-hidden mt-2.5 bg-black/10">
        {SIGNAL_TYPES.map(t => {
          const v = item.by_type[t] ?? 0;
          if (!v) return null;
          return <span key={t} style={{ width: `${(v / total) * 100}%`, backgroundColor: SIGNAL_COLORS[t] }} />;
        })}
      </div>
    </button>
  );
}

// ── 메인 뷰 ────────────────────────────────────────────────────────────────────

export default function AccessInsightView({ isDark }: { isDark: boolean }) {
  const [windowDays, setWindowDays] = useState<number>(90);
  const [leaderboard, setLeaderboard] = useState<DrugMomentum[]>([]);
  const [lbLoading, setLbLoading] = useState(true);
  const [lbError, setLbError] = useState<string | null>(null);

  const [drugList, setDrugList] = useState<DrugListItem[]>([]);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [momentum, setMomentum] = useState<DrugMomentum | null>(null);
  const [journey, setJourney] = useState<DrugJourney | null>(null);
  const [jLoading, setJLoading] = useState(false);
  const [jError, setJError] = useState<string | null>(null);

  const cardBg = isDark ? 'bg-[#161B27]' : 'bg-white';
  const cardBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
  const textMuted = isDark ? 'text-[#4A5568]' : 'text-gray-400';
  const accentColor = isDark ? 'text-[#00E5CC]' : 'text-teal-600';
  const statBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const filterActive = isDark ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'bg-teal-600 text-white';
  const gridStroke = isDark ? '#1E2530' : '#E5E7EB';
  const axisColor = isDark ? '#4A5568' : '#9CA3AF';
  const tooltipBg = isDark ? '#161B27' : '#FFFFFF';
  const tooltipBorder = isDark ? '#2A3545' : '#E5E7EB';

  useEffect(() => {
    let alive = true;
    setLbLoading(true);
    fetchAccessLeaderboard(windowDays, 30)
      .then(items => {
        if (!alive) return;
        setLeaderboard(items);
        setLbError(null);
        setSelectedId(prev => (prev != null ? prev : (items[0]?.drug_id ?? null)));
      })
      .catch(e => { if (alive) setLbError(e instanceof Error ? e.message : '조회 실패'); })
      .finally(() => { if (alive) setLbLoading(false); });
    return () => { alive = false; };
  }, [windowDays]);

  useEffect(() => {
    let alive = true;
    fetchAccessDrugs()
      .then(items => { if (alive) setDrugList(items); })
      .catch(() => { /* 피커는 보조 UI — 실패해도 리더보드는 정상 동작 */ });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (selectedId == null) { setMomentum(null); setJourney(null); return; }
    let alive = true;
    setJLoading(true);
    fetchAccessDrugJourney(selectedId, windowDays)
      .then(r => {
        if (!alive) return;
        setMomentum(r.momentum);
        setJourney(r.journey);
        setJError(null);
      })
      .catch(e => { if (alive) setJError(e instanceof Error ? e.message : '조회 실패'); })
      .finally(() => { if (alive) setJLoading(false); });
    return () => { alive = false; };
  }, [selectedId, windowDays]);

  const chart = useMemo(() => {
    if (!momentum || !journey) return null;
    return buildChart(journey, momentum);
  }, [momentum, journey]);

  const recentSignals = useMemo(() => {
    if (!journey) return [];
    return [...journey.signals].sort((a, b) => (b.published_at || '').localeCompare(a.published_at || ''));
  }, [journey]);

  return (
    <div className="space-y-5">
      {/* 인트로 — momentum 은 참고 신호일 뿐 확정 예측이 아님을 항상 명시 */}
      <div className={`${statBg} rounded-xl p-4 border flex items-start gap-3`}>
        <span className={`w-5 h-5 flex items-center justify-center flex-shrink-0 mt-0.5 ${accentColor}`}>
          <i className="ri-radar-line"></i>
        </span>
        <p className={`text-sm leading-relaxed ${textSub}`}>
          약제별 미디어·engage 활동을 위원회 일정과 겹쳐 등재 환경 조성 신호를 관찰합니다.
          <span className={`font-semibold ${textMain}`}> momentum은 확정 예측이 아닌 참고 신호</span>입니다.
        </p>
      </div>

      {/* 관측 윈도우 선택 */}
      <div className="flex items-center gap-3">
        <span className={`text-xs font-semibold ${textMuted}`}>관측 윈도우</span>
        <div className={`inline-flex p-1 rounded-lg border ${cardBorder} ${statBg}`}>
          {WINDOW_OPTIONS.map(o => (
            <button
              key={o.days}
              onClick={() => setWindowDays(o.days)}
              className={`px-3 py-1 rounded-md text-xs font-semibold cursor-pointer whitespace-nowrap transition-all ${
                windowDays === o.days ? filterActive : `${textSub} hover:${textMain}`
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-5">
        {/* ── 좌: momentum 리더보드 ── */}
        <div className="xl:col-span-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className={`text-sm font-bold ${textMain}`}>Momentum 리더보드</h3>
            {drugList.length > 0 && (
              <select
                value={selectedId ?? ''}
                onChange={e => setSelectedId(e.target.value ? Number(e.target.value) : null)}
                className={`text-xs border rounded-lg px-2 py-1.5 ${isDark ? 'bg-[#161B27] border-[#1E2530] text-white' : 'bg-white border-gray-200 text-gray-700'}`}
                title="전체 약제 중 선택 (리더보드 상위 30건 외)"
              >
                <option value="">약제 선택…</option>
                {drugList.map(d => (
                  <option key={d.drug_id} value={d.drug_id}>{d.brand_kr} ({d.signal_count})</option>
                ))}
              </select>
            )}
          </div>

          {lbLoading && (
            <div className={`text-center py-12 text-sm ${textSub}`}>
              <i className="ri-loader-4-line animate-spin mr-2"></i>리더보드 로딩 중…
            </div>
          )}
          {!lbLoading && lbError && (
            <div className="text-center py-8 text-sm text-[#EF4444]">
              <i className="ri-error-warning-line mr-1"></i>{lbError}
            </div>
          )}
          {!lbLoading && !lbError && leaderboard.length === 0 && (
            <div className={`text-center py-12 ${textMuted}`}>
              <span className="w-10 h-10 flex items-center justify-center mx-auto mb-2"><i className="ri-radar-line text-3xl"></i></span>
              <p className="text-sm">수집된 미디어 신호가 없습니다</p>
            </div>
          )}
          {!lbLoading && !lbError && leaderboard.length > 0 && (
            <div className="space-y-2 max-h-[720px] overflow-y-auto pr-1">
              {leaderboard.map((item, i) => (
                <LeaderboardCard
                  key={item.drug_id}
                  item={item}
                  rank={i + 1}
                  active={selectedId === item.drug_id}
                  onClick={() => setSelectedId(item.drug_id)}
                  isDark={isDark}
                />
              ))}
            </div>
          )}
        </div>

        {/* ── 우: journey 오버레이 패널 ── */}
        <div className="xl:col-span-8 space-y-4">
          {jLoading && (
            <div className={`${cardBg} rounded-2xl border ${cardBorder} text-center py-20 text-sm ${textSub}`}>
              <i className="ri-loader-4-line animate-spin mr-2"></i>journey 로딩 중…
            </div>
          )}
          {!jLoading && jError && (
            <div className={`${cardBg} rounded-2xl border ${cardBorder} text-center py-16 text-sm text-[#EF4444]`}>
              <i className="ri-error-warning-line mr-1"></i>{jError}
            </div>
          )}
          {!jLoading && !jError && !momentum && (
            <div className={`${cardBg} rounded-2xl border ${cardBorder} text-center py-20 ${textMuted}`}>
              <span className="w-10 h-10 flex items-center justify-center mx-auto mb-2"><i className="ri-cursor-line text-3xl"></i></span>
              <p className="text-sm">왼쪽 리더보드에서 약제를 선택하세요</p>
            </div>
          )}

          {!jLoading && !jError && momentum && journey && chart && (
            <>
              {/* 요약 헤더 */}
              <div className={`${cardBg} rounded-2xl border ${cardBorder} p-5`}>
                <div className="flex items-start justify-between flex-wrap gap-3">
                  <div>
                    <h3 className={`text-lg font-bold ${textMain}`}>{momentum.brand_kr}</h3>
                    <p className={`text-xs mt-0.5 ${textMuted}`}>
                      기준일 {momentum.reference_date ?? '—'} · 관측 {momentum.window_days}일
                    </p>
                  </div>
                  <div className="flex items-center gap-5">
                    <div className="text-right">
                      <p className={`text-2xl font-bold leading-none tabular-nums ${accentColor}`}>{momentum.momentum_score.toFixed(2)}</p>
                      <p className={`text-[10px] mt-1 ${textMuted}`}>momentum score</p>
                    </div>
                    <div className="text-right">
                      <p className={`text-2xl font-bold leading-none tabular-nums ${textMain}`}>{momentum.signal_count}</p>
                      <p className={`text-[10px] mt-1 ${textMuted}`}>신호 건수</p>
                    </div>
                    <div className="flex flex-col items-center">
                      <TrendArrow direction={momentum.trend.direction} isDark={isDark} />
                      <p className={`text-[10px] mt-1 ${textMuted}`}>
                        {momentum.trend.recent_30d}/{momentum.trend.prior_30d}
                      </p>
                    </div>
                  </div>
                </div>
                {momentum.expected_session && (
                  <div className={`mt-3 pt-3 border-t ${cardBorder} flex items-center gap-2 text-xs ${textSub}`}>
                    <i className="ri-calendar-event-line"></i>
                    예상/최근 세션: <span className={`font-semibold ${textMain}`}>{momentum.expected_session.session_date} {momentum.expected_session.committee_type}</span>
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                      momentum.expected_session.status === 'COMPLETED'
                        ? (isDark ? 'bg-[#1E2530] text-[#8B9BB4]' : 'bg-gray-100 text-gray-500')
                        : 'bg-amber-500/15 text-amber-500'
                    }`}>
                      {momentum.expected_session.status === 'COMPLETED' ? '완료' : '예정'}
                    </span>
                  </div>
                )}
              </div>

              {/* 오버레이 차트: 신호밀도(막대) + 위원회 세션(수직선) + 급여 마일스톤(마름모) */}
              <div className={`${cardBg} rounded-2xl border ${cardBorder} p-5`}>
                <div className="flex items-center justify-between mb-1">
                  <h4 className={`text-xs font-bold uppercase tracking-wider ${textMuted}`}>급여 Journey 오버레이</h4>
                  <span className={`text-[10px] ${textMuted}`}>월별 신호 건수 · 최근 12개월</span>
                </div>
                <ResponsiveContainer width="100%" height={300}>
                  <ComposedChart data={chart.buckets} margin={{ top: 30, right: 16, left: -12, bottom: 4 }}>
                    <CartesianGrid stroke={gridStroke} strokeDasharray="3 3" vertical={false} />
                    <XAxis
                      dataKey="monthStart"
                      type="number"
                      domain={chart.domain}
                      ticks={chart.buckets.map(b => b.monthStart)}
                      tickFormatter={formatMonthTick}
                      tick={{ fontSize: 10, fill: axisColor }}
                      axisLine={{ stroke: gridStroke }}
                      tickLine={false}
                    />
                    <YAxis
                      allowDecimals={false}
                      domain={[-1.6, Math.max(chart.maxStack, 2) + 1]}
                      ticks={Array.from({ length: Math.max(chart.maxStack, 2) + 2 }, (_, i) => i)}
                      tick={{ fontSize: 10, fill: axisColor }}
                      width={26}
                      axisLine={false}
                      tickLine={false}
                    />
                    <Tooltip
                      labelFormatter={label => formatMonthTick(Number(label))}
                      formatter={(value, name) => [`${value}건`, SIGNAL_LABELS[name as SignalType] ?? String(name)]}
                      contentStyle={{ background: tooltipBg, border: `1px solid ${tooltipBorder}`, borderRadius: 8, fontSize: 12 }}
                      labelStyle={{ color: textMuted, fontSize: 11, marginBottom: 4 }}
                    />
                    {SIGNAL_TYPES.map(t => (
                      <Bar key={t} dataKey={t} stackId="signals" fill={SIGNAL_COLORS[t]} maxBarSize={28} />
                    ))}
                    {chart.sessionsInRange.map((s, i) => (
                      <ReferenceLine
                        key={s.session_id}
                        x={s.ts}
                        stroke={s.status === 'COMPLETED' ? (isDark ? '#94A3B8' : '#64748B') : '#F59E0B'}
                        strokeDasharray={s.status === 'COMPLETED' ? undefined : '5 4'}
                        strokeWidth={1.5}
                        label={renderSessionLabel(s.label, s.status === 'COMPLETED' ? (isDark ? '#94A3B8' : '#64748B') : '#F59E0B', s.status !== 'COMPLETED', i)}
                      />
                    ))}
                    {chart.milestonesInRange.map(m => (
                      <ReferenceDot
                        key={m.key}
                        x={m.ts}
                        y={-0.7}
                        r={4.5}
                        shape={renderMilestoneDot(m.color, `${m.label} · ${m.dateStr}`)}
                      />
                    ))}
                  </ComposedChart>
                </ResponsiveContainer>

                {/* 범례 */}
                <div className={`flex flex-wrap items-center gap-x-4 gap-y-1.5 mt-3 pt-3 border-t ${cardBorder} text-[11px] ${textSub}`}>
                  {SIGNAL_TYPES.map(t => (
                    <span key={t} className="flex items-center gap-1.5">
                      <span className="w-2.5 h-2.5 rounded-sm flex-shrink-0" style={{ backgroundColor: SIGNAL_COLORS[t] }}></span>
                      {SIGNAL_LABELS[t]}
                    </span>
                  ))}
                  <span className={`mx-1 ${textMuted}`}>|</span>
                  {MILESTONE_DEFS.map(m => (
                    <span key={m.key} className="flex items-center gap-1.5">
                      <span className="w-2 h-2 flex-shrink-0" style={{ backgroundColor: m.color, transform: 'rotate(45deg)' }}></span>
                      {m.label}
                    </span>
                  ))}
                  <span className={`mx-1 ${textMuted}`}>|</span>
                  <span className="flex items-center gap-1.5"><span className="w-3 h-0.5 bg-amber-500 flex-shrink-0"></span>세션 예정</span>
                  <span className="flex items-center gap-1.5"><span className={`w-3 h-0.5 flex-shrink-0 ${isDark ? 'bg-[#94A3B8]' : 'bg-[#64748B]'}`}></span>세션 완료</span>
                </div>

                {/* 차트 윈도우 이전 마일스톤/세션 — 정보 누락 방지 */}
                {(chart.milestonesBefore.length > 0 || chart.sessionsBefore.length > 0) && (
                  <p className={`text-[11px] mt-2 ${textMuted}`}>
                    차트 윈도우 이전:{' '}
                    {chart.milestonesBefore.map(m => `${m.label} ${m.dateStr}`).join(' · ')}
                    {chart.milestonesBefore.length > 0 && chart.sessionsBefore.length > 0 && ' · '}
                    {chart.sessionsBefore.map(s => `${s.label} ${formatDate(new Date(s.ts).toISOString())}`).join(' · ')}
                  </p>
                )}
              </div>

              {/* 최근 신호 리스트 */}
              <div className={`${cardBg} rounded-2xl border ${cardBorder} p-5`}>
                <h4 className={`text-xs font-bold uppercase tracking-wider mb-3 ${textMuted}`}>최근 신호 ({recentSignals.length}건)</h4>
                {recentSignals.length === 0 ? (
                  <p className={`text-sm ${textMuted}`}>수집된 신호가 없습니다</p>
                ) : (
                  <div className="space-y-1.5 max-h-80 overflow-y-auto pr-1">
                    {recentSignals.map((s, i) => {
                      const type = (SIGNAL_TYPES as string[]).includes(s.signal_type) ? s.signal_type as SignalType : null;
                      const color = type ? SIGNAL_COLORS[type] : '#9CA3AF';
                      const label = type ? SIGNAL_LABELS[type] : s.signal_type;
                      return (
                        <div key={i} className={`flex items-start gap-2.5 py-1.5 ${i > 0 ? `border-t ${cardBorder}` : ''}`}>
                          <span className={`text-[11px] tabular-nums flex-shrink-0 w-20 pt-0.5 ${textMuted}`}>{formatDate(s.published_at)}</span>
                          <span
                            className="text-[10px] font-bold px-1.5 py-0.5 rounded flex-shrink-0 whitespace-nowrap mt-0.5"
                            style={{ backgroundColor: color + '20', color }}
                          >
                            {label}
                          </span>
                          <div className="min-w-0 flex-1">
                            {s.url ? (
                              <a href={s.url} target="_blank" rel="noopener noreferrer"
                                className={`text-xs font-medium leading-snug hover:underline ${textMain}`}>
                                {s.title}
                              </a>
                            ) : (
                              <span className={`text-xs font-medium leading-snug ${textMain}`}>{s.title}</span>
                            )}
                            {s.outlet && <p className={`text-[10px] mt-0.5 ${textMuted}`}>{s.outlet}</p>}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
