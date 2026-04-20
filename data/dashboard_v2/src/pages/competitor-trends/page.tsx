import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer, CartesianGrid,
} from 'recharts';
import {
  listCompetitorTrends,
  COMPETITOR_BADGES,
  type CompetitorTrend,
} from '@/api/competitorTrends';

const BADGE_TYPES = ['전체', ...COMPETITOR_BADGES];

const BADGE_CHART_COLORS: Record<string, string> = {
  '신규 출시': '#F59E0B',
  '가격 변동': '#EF4444',
  '임상 진행': '#7C3AED',
  '급여 등재': '#10B981',
  '파이프라인': '#06B6D4',
  '전략 변화': '#EC4899',
};

function monthKey(dateStr: string): string | null {
  // "YYYY-MM-DD…" → "YYYY-MM"
  if (!dateStr || dateStr.length < 7) return null;
  return dateStr.slice(0, 7);
}

function lastNMonths(n: number): string[] {
  const now = new Date();
  const out: string[] = [];
  for (let i = n - 1; i >= 0; i -= 1) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    out.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`);
  }
  return out;
}

export default function CompetitorTrendsPage() {
  const [filter, setFilter] = useState('전체');
  const [companyFilter, setCompanyFilter] = useState<string>('전체');
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState<number | null>(null);
  const [items, setItems] = useState<CompetitorTrend[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    listCompetitorTrends()
      .then(list => { if (alive) { setItems(list); setError(null); } })
      .catch(e => { if (alive) setError(String(e)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const companies = useMemo(() => {
    const set = new Set<string>();
    for (const i of items) if (i.company) set.add(i.company);
    return Array.from(set).sort((a, b) => a.localeCompare(b, 'ko'));
  }, [items]);

  const filtered = items.filter(d => {
    const matchBadge = filter === '전체' || d.badge === filter;
    const matchCompany = companyFilter === '전체' || d.company === companyFilter;
    const matchSearch = !search || d.company.toLowerCase().includes(search.toLowerCase()) || d.headline.toLowerCase().includes(search.toLowerCase());
    return matchBadge && matchCompany && matchSearch;
  });

  const stats = {
    companies: new Set(items.map(i => i.company)).size,
    monthly: items.filter(i => i.date.startsWith(new Date().toISOString().slice(0, 7))).length,
    launch: items.filter(i => i.badge === '신규 출시').length,
    price: items.filter(i => i.badge === '가격 변동').length,
  };

  // 월별 동향 추이 (최근 6개월 × badge 스택) — 내부 자료 범위에서 KPI 4종을 한 화면으로
  const monthlyTrendData = useMemo(() => {
    const months = lastNMonths(6);
    const base: Record<string, Record<string, number>> = {};
    for (const m of months) {
      base[m] = {};
      for (const b of COMPETITOR_BADGES) base[m][b] = 0;
    }
    for (const it of items) {
      const mk = monthKey(it.date);
      if (!mk || !(mk in base)) continue;
      if (it.badge in base[mk]) base[mk][it.badge] += 1;
    }
    return months.map(m => ({
      month: m.slice(2),  // "YY-MM"
      ...base[m],
    }));
  }, [items]);

  const hasTrendData = monthlyTrendData.some(row =>
    COMPETITOR_BADGES.some(b => (row as any)[b] > 0)
  );

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      {/* Header */}
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-5 h-5 flex items-center justify-center"><i className="ri-bar-chart-grouped-line text-[#00E5CC]"></i></span>
              <h1 className="text-2xl font-bold text-white">Competitor Trends</h1>
            </div>
            <p className="text-[#8B9BB4] text-sm">경쟁사 동향 모니터링 및 전략 분석</p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-2 bg-[#161B27] border border-[#1E2530] rounded-lg px-3 py-2">
              <span className="w-4 h-4 flex items-center justify-center text-[#8B9BB4]"><i className="ri-search-line text-sm"></i></span>
              <input
                type="text"
                placeholder="회사명 또는 키워드 검색..."
                value={search}
                onChange={e => setSearch(e.target.value)}
                className="bg-transparent text-white text-sm placeholder-[#4A5568] focus:outline-none w-48"
              />
            </div>
            <Link
              to="/admin/competitor-trends"
              className="flex items-center gap-2 bg-[#00E5CC] text-[#0A0E1A] text-sm font-semibold px-4 py-2 rounded-lg cursor-pointer whitespace-nowrap hover:bg-[#00C9B1] transition-colors"
            >
              <span className="w-4 h-4 flex items-center justify-center"><i className="ri-add-line text-sm"></i></span>
              동향 관리
            </Link>
          </div>
        </div>
      </div>

      <div className="px-8 py-6 space-y-5">
        {/* Stats */}
        <div className="grid grid-cols-4 gap-4">
          {[
            { label: '모니터링 경쟁사', value: String(stats.companies), icon: 'ri-building-2-line', color: '#00E5CC' },
            { label: '이번 달 동향', value: String(stats.monthly), icon: 'ri-notification-3-line', color: '#7C3AED' },
            { label: '신규 출시 예정', value: String(stats.launch), icon: 'ri-rocket-line', color: '#F59E0B' },
            { label: '가격 변동 건수', value: String(stats.price), icon: 'ri-exchange-dollar-line', color: '#EF4444' },
          ].map(stat => (
            <div key={stat.label} className="bg-[#161B27] rounded-xl p-4 border border-[#1E2530] flex items-center gap-4">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0" style={{ backgroundColor: stat.color + '20' }}>
                <span className="w-5 h-5 flex items-center justify-center" style={{ color: stat.color }}>
                  <i className={`${stat.icon} text-lg`}></i>
                </span>
              </div>
              <div>
                <p className="text-[#8B9BB4] text-xs">{stat.label}</p>
                <p className="text-xl font-bold" style={{ color: stat.color }}>{stat.value}</p>
              </div>
            </div>
          ))}
        </div>

        {/* 월별 동향 추이 */}
        {!loading && items.length > 0 && (
          <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-white font-bold text-base mb-1">월별 동향 추이</h3>
                <p className="text-[#8B9BB4] text-xs">최근 6개월 · 유형별 건수 (내부 모니터링 기록 기반)</p>
              </div>
            </div>
            {hasTrendData ? (
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={monthlyTrendData} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1E2530" />
                  <XAxis dataKey="month" tick={{ fill: '#8B9BB4', fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: '#8B9BB4', fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} />
                  <Tooltip
                    contentStyle={{ backgroundColor: '#0D1117', border: '1px solid #2A3545', borderRadius: '0.5rem', fontSize: 12 }}
                    cursor={{ fill: 'rgba(0, 229, 204, 0.05)' }}
                  />
                  <Legend wrapperStyle={{ color: '#8B9BB4', fontSize: 11 }} />
                  {COMPETITOR_BADGES.map(badge => (
                    <Bar
                      key={badge}
                      dataKey={badge}
                      stackId="a"
                      fill={BADGE_CHART_COLORS[badge] || '#4A5568'}
                      radius={[0, 0, 0, 0]}
                    />
                  ))}
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="text-center py-8 text-[#4A5568] text-xs">
                최근 6개월 데이터가 부족합니다 — 기록이 쌓이면 자동 표시됩니다
              </div>
            )}
          </div>
        )}

        {/* Company Filter Chips */}
        {companies.length > 0 && (
          <div>
            <p className="text-[#8B9BB4] text-xs mb-2">경쟁사</p>
            <div className="flex gap-2 flex-wrap">
              <button
                onClick={() => setCompanyFilter('전체')}
                className={`px-3 py-1.5 rounded-full text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${
                  companyFilter === '전체' ? 'bg-[#7C3AED] text-white' : 'bg-[#161B27] border border-[#1E2530] text-[#8B9BB4] hover:text-white'
                }`}
              >
                전체
              </button>
              {companies.map(c => {
                const count = items.filter(i => i.company === c).length;
                return (
                  <button
                    key={c}
                    onClick={() => setCompanyFilter(c)}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${
                      companyFilter === c ? 'bg-[#7C3AED] text-white' : 'bg-[#161B27] border border-[#1E2530] text-[#8B9BB4] hover:text-white'
                    }`}
                  >
                    {c}
                    <span className={`${companyFilter === c ? 'text-white/70' : 'text-[#4A5568]'} text-[10px]`}>{count}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Filter Tabs */}
        <div>
          <p className="text-[#8B9BB4] text-xs mb-2">유형</p>
          <div className="flex gap-2 flex-wrap">
            {BADGE_TYPES.map(type => (
              <button
                key={type}
                onClick={() => setFilter(type)}
                className={`px-3 py-1.5 rounded-full text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${
                  filter === type ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'bg-[#161B27] border border-[#1E2530] text-[#8B9BB4] hover:text-white'
                }`}
              >
                {type}
              </button>
            ))}
          </div>
        </div>

        {loading && <div className="text-center py-16 text-[#8B9BB4] text-sm">로딩 중…</div>}
        {error && <div className="text-center py-6 text-[#EF4444] text-sm">{error}</div>}

        {/* Cards Grid */}
        {!loading && (
          <div className="grid grid-cols-3 gap-4">
            {filtered.map(item => (
              <div
                key={item.id}
                className="bg-[#161B27] rounded-2xl border border-[#1E2530] hover:border-[#2A3545] transition-all duration-200 overflow-hidden"
              >
                <div className="p-5">
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-2.5">
                      <div
                        className="w-9 h-9 rounded-xl flex items-center justify-center text-xs font-bold text-white flex-shrink-0"
                        style={{ backgroundColor: (item.color || '#1E2530') + '25', border: `1px solid ${(item.color || '#1E2530')}40` }}
                      >
                        {item.logo || item.company.slice(0, 2).toUpperCase()}
                      </div>
                      <div>
                        <p className="text-white text-xs font-semibold leading-tight">{item.company}</p>
                        <p className="text-[#4A5568] text-xs">{item.date}</p>
                      </div>
                    </div>
                    <span className={`text-xs font-semibold px-2 py-1 rounded-full whitespace-nowrap ${item.badgeColor || 'bg-[#1E2530] text-[#8B9BB4]'}`}>
                      {item.badge}
                    </span>
                  </div>

                  <h4 className="text-white text-sm font-bold mb-2 leading-snug">{item.headline}</h4>
                  <p className={`text-[#8B9BB4] text-xs leading-relaxed ${expanded === item.id ? '' : 'line-clamp-2'}`}>
                    {item.detail}
                  </p>

                  <div className="flex items-center justify-between mt-4">
                    <div className="flex items-center gap-1.5">
                      <span className="w-3.5 h-3.5 flex items-center justify-center text-[#4A5568]"><i className="ri-file-text-line text-xs"></i></span>
                      <span className="text-[#4A5568] text-xs">{item.source || '—'}</span>
                    </div>
                    <button
                      onClick={() => setExpanded(expanded === item.id ? null : item.id)}
                      className="flex items-center gap-1 text-[#00E5CC] text-xs font-medium cursor-pointer whitespace-nowrap hover:text-[#00C9B1] transition-colors"
                    >
                      {expanded === item.id ? '접기' : '더 보기'}
                      <span className="w-3.5 h-3.5 flex items-center justify-center">
                        <i className={`text-xs ${expanded === item.id ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'}`}></i>
                      </span>
                    </button>
                  </div>
                </div>

                {/* Bottom accent bar */}
                <div className="h-0.5 w-full" style={{ backgroundColor: (item.color || '#1E2530') + '60' }}></div>
              </div>
            ))}
          </div>
        )}

        {!loading && filtered.length === 0 && (
          <div className="text-center py-16 text-[#4A5568]">
            <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3"><i className="ri-search-line text-4xl"></i></span>
            <p className="text-sm">{items.length === 0 ? '등록된 동향이 없습니다 — 관리 페이지에서 추가하세요' : '검색 결과가 없습니다'}</p>
          </div>
        )}
      </div>
    </div>
  );
}
