import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { fetchMeetings, fetchPipeline, type MeetingSchedule, type PipelineDrug } from '@/api/reimbStatus';
import { listCompetitorTrends, primaryTrends, type CompetitorTrend } from '@/api/competitorTrends';
import { fetchSignals, type SVPolicySignal } from '@/api/stakeholder';
import { fetchForeignDrugList } from '@/api/foreign';
import { fetchPolicyOverview } from '@/api/policyIntelligence';
import { fetchMsdSummary } from '@/api/home';

// 대시보드 최상단 '오늘의 요약' — 리더가 60초 안에 '오늘 알아야 할 것'을 보도록:
// 다음 심의 일정과 MSD 안건, 이번 주 경쟁사 움직임, 최신 정책 시그널, 데이터 기준일(신선도 신호등).
// 모든 블록은 독립 로딩 — 하나가 실패해도 나머지는 보인다. 기존 조회 API만 사용(쓰기 없음).

interface Freshness { label: string; date: string | null; staleAfterDays: number; to: string }

const DAY = 86_400_000;
const toDate = (s?: string | null) => {
  if (!s) return null;
  const d = new Date(s.replace(/\./g, '-').slice(0, 10) + 'T00:00:00+09:00');
  return Number.isNaN(d.getTime()) ? null : d;
};
const ageDays = (s?: string | null) => {
  const d = toDate(s);
  return d ? Math.floor((Date.now() - d.getTime()) / DAY) : null;
};
const short = (s?: string | null) => (s ? s.replace(/-/g, '.').slice(0, 10) : '—');

function settle<T>(p: Promise<T>, set: (v: T | null) => void) {
  p.then(set).catch(() => set(null));
}

export default function TodaySummary() {
  const [meetings, setMeetings] = useState<MeetingSchedule[] | null>(null);
  const [msdDrugs, setMsdDrugs] = useState<PipelineDrug[] | null>(null);
  const [trends, setTrends] = useState<CompetitorTrend[] | null>(null);
  const [signal, setSignal] = useState<SVPolicySignal | null>(null);
  const [fresh, setFresh] = useState<Freshness[]>([]);

  useEffect(() => {
    settle(fetchMeetings(), setMeetings);
    settle(fetchPipeline().then(st => st.flatMap(s => s.drugs).filter(d => d.msdFlag)), setMsdDrugs);
    settle(listCompetitorTrends().then(primaryTrends), setTrends); // 같은 사건 중복 기사 제외
    settle(fetchSignals().then(r => r.signals?.[0] ?? null), setSignal);
    Promise.allSettled([fetchMsdSummary(), fetchForeignDrugList(), fetchPolicyOverview()]).then(([msd, foreign, policy]) => {
      const foreignLatest = foreign.status === 'fulfilled'
        ? foreign.value.map(f => f.lastSearchedAt).filter(Boolean).sort().slice(-1)[0] ?? null : null;
      setFresh([
        { label: '국내 약가 고시', date: msd.status === 'fulfilled' ? msd.value.latestApplyDate : null, staleAfterDays: 45, to: '/domestic-pricing' },
        { label: '해외 약가 수집', date: foreignLatest, staleAfterDays: 30, to: '/international-pricing' },
        { label: '정책(KRPIA)', date: policy.status === 'fulfilled' ? policy.value.overview?.latest_event_date ?? null : null, staleAfterDays: 30, to: '/policy-intelligence' },
      ]);
    });
  }, []);

  const next = (meetings ?? []).filter(m => m.daysUntil >= 0).sort((a, b) => a.daysUntil - b.daysUntil)[0];
  const weekAgo = Date.now() - 7 * DAY;
  const weekTrends = (trends ?? []).filter(t => (toDate(t.date)?.getTime() ?? 0) >= weekAgo)
    .sort((a, b) => b.date.localeCompare(a.date));
  const msdActive = (msdDrugs ?? []).filter(d => d.status !== 'completed');

  return (
    <section className="ui-card p-5" aria-label="오늘의 요약">
      <div className="flex items-baseline justify-between mb-4">
        <div>
          <p className="ui-eyebrow">오늘의 요약</p>
          <h2 className="text-[19px] font-semibold tracking-tight" style={{ color: 'var(--label)' }}>
            {new Date().toLocaleDateString('ko-KR', { timeZone: 'Asia/Seoul', month: 'long', day: 'numeric', weekday: 'short' })} 기준 꼭 볼 것
          </h2>
        </div>
      </div>
      <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' }}>
        {/* 1. 다음 심의 */}
        <Link to="/reimbursement-status" className="rounded-2xl p-4 block hover:opacity-90" style={{ background: 'var(--surface-inset)' }}>
          <p className="text-xs font-medium" style={{ color: 'var(--label-2)' }}>다음 심의</p>
          {meetings === null ? <p className="text-sm mt-2" style={{ color: 'var(--label-3)' }}>불러오는 중…</p>
            : next ? (
              <>
                <p className="text-[22px] font-semibold mt-1" style={{ color: 'var(--label)' }}>D-{next.daysUntil}</p>
                <p className="text-sm" style={{ color: 'var(--label)' }}>{next.date.slice(5)} ({next.dayOfWeek}) {next.typeLabel} {next.cycle}</p>
              </>
            ) : <p className="text-sm mt-2" style={{ color: 'var(--label-3)' }}>예정된 회차 정보 없음</p>}
          <p className="text-xs mt-2" style={{ color: 'var(--label-2)' }}>
            MSD 진행 중 {msdDrugs === null ? '—' : `${msdActive.length}건`}
            {msdActive.length > 0 && ` · ${msdActive.slice(0, 2).map(d => d.name).join(', ')}`}
          </p>
        </Link>

        {/* 2. 경쟁사 */}
        <Link to="/competitor-trends" className="rounded-2xl p-4 block hover:opacity-90" style={{ background: 'var(--surface-inset)' }}>
          <p className="text-xs font-medium" style={{ color: 'var(--label-2)' }}>최근 7일 경쟁사 동향</p>
          {trends === null ? <p className="text-sm mt-2" style={{ color: 'var(--label-3)' }}>불러오는 중…</p> : (
            <>
              <p className="text-[22px] font-semibold mt-1" style={{ color: 'var(--label)' }}>{weekTrends.length}건</p>
              {weekTrends.slice(0, 2).map(t => (
                <p key={t.id} className="text-xs mt-1 truncate" style={{ color: 'var(--label)' }} title={t.headline}>
                  <span style={{ color: 'var(--label-3)' }}>{t.company} · </span>{t.headline}
                </p>
              ))}
            </>
          )}
        </Link>

        {/* 3. 정책 시그널 */}
        <Link to="/stakeholder-signals" className="rounded-2xl p-4 block hover:opacity-90" style={{ background: 'var(--surface-inset)' }}>
          <p className="text-xs font-medium" style={{ color: 'var(--label-2)' }}>최신 정책 시그널{signal?.as_of ? ` · ${short(signal.as_of)}` : ''}</p>
          {signal ? (
            <>
              <p className="text-sm font-semibold mt-1.5 leading-snug" style={{ color: 'var(--label)' }}>{signal.title}</p>
              <p className="text-[11px] mt-1.5" style={{ color: 'var(--label-3)' }}>분석 가설 · 공식 선언 아님</p>
            </>
          ) : <p className="text-sm mt-2" style={{ color: 'var(--label-3)' }}>표시할 시그널 없음</p>}
        </Link>

        {/* 4. 데이터 기준일 */}
        <div className="rounded-2xl p-4" style={{ background: 'var(--surface-inset)' }}>
          <p className="text-xs font-medium" style={{ color: 'var(--label-2)' }}>데이터 기준일</p>
          <ul className="mt-2 space-y-1.5">
            {fresh.length === 0 && <li className="text-sm" style={{ color: 'var(--label-3)' }}>불러오는 중…</li>}
            {fresh.map(f => {
              const age = ageDays(f.date);
              const stale = age == null || age > f.staleAfterDays;
              return (
                <li key={f.label}>
                  <Link to={f.to} className="flex items-center gap-2 text-sm hover:underline" style={{ color: 'var(--label)' }}
                    title={age == null ? '기준일 정보 없음' : `${age}일 전`}>
                    <span className="ui-dot" style={{ background: stale ? 'var(--orange-solid)' : 'var(--green-solid)' }} aria-hidden="true" />
                    <span className="flex-1 whitespace-nowrap">{f.label}</span>
                    <span className="tabular-nums whitespace-nowrap text-right" style={{ color: stale ? 'var(--orange)' : 'var(--label-2)' }}>
                      {short(f.date)}
                      {stale && age != null && <span className="block text-[11px]">{age}일째 갱신 없음</span>}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </section>
  );
}
