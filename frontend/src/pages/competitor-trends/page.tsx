import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { listCompetitorTrends, COMPETITOR_BADGES, type CompetitorTrend } from '@/api/competitorTrends';
import {
  fetchCompetitorNews,
  fetchNewsBrands,
  type CompetitorNewsItem,
  type NewsBrand,
  type NewsBrandsResponse,
} from '@/api/competitorNews';

const BADGE_TYPES = ['전체', ...COMPETITOR_BADGES];

const PERIOD_OPTIONS = [
  { label: '1개월', days: 31 },
  { label: '3개월', days: 93 },
  { label: '6개월', days: 183 },
] as const;

// anchor 문자열을 읽기 좋은 그룹 라벨로 정규화
function anchorGroup(anchor: string, kind: string): string {
  if (kind === 'msd_asset') return 'MSD 자산';
  if (anchor.includes('PD-(L)1')) return 'PD-(L)1';
  if (anchor.includes('EGFR')) return 'EGFR 폐암';
  if (anchor.includes('ADC')) return 'ADC';
  return 'Others';
}
const GROUP_ORDER = ['PD-(L)1', 'EGFR 폐암', 'ADC', 'Others', 'MSD 자산'];

// 동향 카드 펼침 시 관련 뉴스 (company 기준 lazy fetch)
function RelatedNews({
  company,
  isDark,
}: {
  company: string;
  isDark: boolean;
}) {
  const [items, setItems] = useState<CompetitorNewsItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetchCompetitorNews({ company, limit: 6 })
      .then(list => { if (alive) { setItems(list); setError(null); } })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : '조회 실패'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [company]);

  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
  const textMuted = isDark ? 'text-[#4A5568]' : 'text-gray-400';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const divider = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const rowHover = isDark ? 'hover:bg-[#1E2530]/40' : 'hover:bg-gray-50';

  return (
    <div className={`mt-4 pt-3 border-t ${divider}`}>
      <div className="flex items-center gap-1.5 mb-2">
        <span className={`text-[11px] font-bold ${textSub}`}>관련 뉴스</span>
        <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-[#00857C]/20 text-[#00E5CC]">Tier 1</span>
      </div>
      {loading && (
        <div className={`flex items-center gap-1.5 py-2 text-[11px] ${textMuted}`}>
          <i className="ri-loader-4-line animate-spin"></i>불러오는 중…
        </div>
      )}
      {!loading && error && (
        <p className="text-[11px] text-[#EF4444] py-1"><i className="ri-error-warning-line mr-1"></i>{error}</p>
      )}
      {!loading && !error && items && items.length === 0 && (
        <p className={`text-[11px] py-1 ${textMuted}`}>관련 T1 뉴스 없음</p>
      )}
      {!loading && !error && items && items.length > 0 && (
        <ul className="space-y-1">
          {items.map(n => (
            <li key={n.id}>
              <a
                href={n.url}
                target="_blank"
                rel="noopener noreferrer"
                className={`flex items-start gap-2 px-1.5 py-1 rounded-md transition-colors ${rowHover}`}
              >
                <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-[#00857C]/20 text-[#00E5CC] flex-shrink-0 mt-0.5">T1</span>
                <span className="min-w-0 flex-1">
                  <span className={`text-[11px] font-medium leading-snug line-clamp-1 ${textMain}`}>{n.title}</span>
                  <span className={`flex items-center gap-1.5 text-[10px] mt-0.5 ${textMuted}`}>
                    <span>{n.source_name || n.source_domain}</span>
                    <span>·</span>
                    <span>{n.pub_date}</span>
                  </span>
                </span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function CompetitorTrendsPage() {
  const [isDark, setIsDark] = useState(false);
  const [view, setView] = useState<'trends' | 'archive'>('trends');
  const [filter, setFilter] = useState('전체');
  const [companyFilter, setCompanyFilter] = useState('전체');
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState<number | null>(null);
  const [items, setItems] = useState<CompetitorTrend[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 뉴스 아카이브 (B)
  const [brands, setBrands] = useState<NewsBrandsResponse | null>(null);
  const [brandsErr, setBrandsErr] = useState<string | null>(null);
  const [selBrand, setSelBrand] = useState<string>('전체');
  const [period, setPeriod] = useState<number>(183);
  const [news, setNews] = useState<CompetitorNewsItem[]>([]);
  const [newsLoading, setNewsLoading] = useState(false);
  const [newsErr, setNewsErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    listCompetitorTrends()
      .then(list => { if (alive) { setItems(list); setError(null); } })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : '조회 실패'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  // 아카이브 메타(브랜드/통계)는 1회 로드
  useEffect(() => {
    let alive = true;
    fetchNewsBrands()
      .then(r => { if (alive) { setBrands(r); setBrandsErr(null); } })
      .catch(e => { if (alive) setBrandsErr(e instanceof Error ? e.message : '조회 실패'); });
    return () => { alive = false; };
  }, []);

  // 뉴스 리스트는 선택 브랜드/기간 변경 시 재조회
  useEffect(() => {
    let alive = true;
    setNewsLoading(true);
    fetchCompetitorNews({
      brand: selBrand === '전체' ? undefined : selBrand,
      days: period,
      limit: 200,
    })
      .then(list => { if (alive) { setNews(list); setNewsErr(null); } })
      .catch(e => { if (alive) setNewsErr(e instanceof Error ? e.message : '조회 실패'); })
      .finally(() => { if (alive) setNewsLoading(false); });
    return () => { alive = false; };
  }, [selBrand, period]);

  // anchor 그룹별 브랜드 칩 묶음
  const brandGroups = useMemo(() => {
    const map = new Map<string, NewsBrand[]>();
    for (const b of brands?.brands ?? []) {
      const g = anchorGroup(b.anchor, b.kind);
      if (!map.has(g)) map.set(g, []);
      map.get(g)!.push(b);
    }
    return GROUP_ORDER.filter(g => map.has(g)).map(g => ({ group: g, brands: map.get(g)! }));
  }, [brands]);

  const selBrandColor = useMemo(
    () => brands?.brands.find(b => b.query === selBrand)?.color ?? '#00E5CC',
    [brands, selBrand],
  );

  // 경쟁사(회사) 목록 — 실데이터에서 distinct company 추출 + 카드 수 집계 (하드코딩 금지)
  const companyList = useMemo(() => {
    const map = new Map<string, number>();
    for (const i of items) {
      map.set(i.company, (map.get(i.company) ?? 0) + 1);
    }
    return Array.from(map.entries())
      .map(([company, count]) => ({ company, count }))
      .sort((a, b) => a.company.localeCompare(b.company, 'ko'));
  }, [items]);

  const filtered = items.filter(d => {
    const matchBadge = filter === '전체' || d.badge === filter;
    const matchCompany = companyFilter === '전체' || d.company === companyFilter;
    const matchSearch = !search || d.company.toLowerCase().includes(search.toLowerCase()) || d.headline.toLowerCase().includes(search.toLowerCase());
    return matchBadge && matchCompany && matchSearch;
  });

  // 통계 — 실데이터에서 직접 산출 (mock 상수 금지)
  const stats = useMemo(() => {
    const thisMonth = new Date().toISOString().slice(0, 7);
    return {
      companies: new Set(items.map(i => i.company)).size,
      monthly: items.filter(i => (i.date || '').startsWith(thisMonth)).length,
      launch: items.filter(i => i.badge === '신규 출시').length,
      price: items.filter(i => i.badge === '가격 변동').length,
    };
  }, [items]);

  const pageBg = isDark ? 'bg-[#0D1117]' : 'bg-gray-50';
  const headerBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const cardBg = isDark ? 'bg-[#161B27]' : 'bg-white';
  const cardBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const cardHover = isDark ? 'hover:border-[#2A3545]' : 'hover:border-gray-300';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
  const textMuted = isDark ? 'text-[#4A5568]' : 'text-gray-400';
  const accentColor = isDark ? 'text-[#00E5CC]' : 'text-teal-600';
  const inputBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const inputFocus = isDark ? 'focus-within:border-[#00E5CC]/50' : 'focus-within:border-teal-300';
  const inputText = isDark ? 'text-white placeholder-[#4A5568]' : 'text-gray-900 placeholder-gray-400';
  const statBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const filterBtn = isDark ? 'bg-[#161B27] border-[#1E2530] text-[#8B9BB4] hover:text-white' : 'bg-white border-gray-200 text-gray-500 hover:text-gray-900';
  const filterActive = isDark ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'bg-teal-600 text-white';
  const sourceText = isDark ? 'text-[#4A5568]' : 'text-gray-400';

  return (
    <div className={`min-h-screen ${pageBg} ${isDark ? 'text-white' : 'text-gray-900'}`}>
      {/* Header */}
      <div className={`px-8 pt-8 pb-6 border-b ${headerBorder}`}>
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-bar-chart-grouped-line"></i></span>
              <h1 className={`text-2xl font-bold ${textMain}`}>Competitor Trends</h1>
            </div>
            <p className={`${textSub} text-sm`}>MNC 동향 모니터링 및 전략 분석</p>
          </div>
          <div className="flex items-center gap-2">
            <div className={`flex items-center gap-2 ${inputBg} rounded-lg px-3 py-2 ${inputFocus} transition-colors`}>
              <span className={`w-4 h-4 flex items-center justify-center ${textSub}`}><i className="ri-search-line text-sm"></i></span>
              <input
                type="text"
                placeholder="회사명 또는 키워드 검색..."
                value={search}
                onChange={e => setSearch(e.target.value)}
                className={`bg-transparent text-sm focus:outline-none w-48 ${inputText}`}
              />
            </div>
            <Link
              to="/admin/competitor-trends"
              className="flex items-center gap-2 bg-teal-600 text-white text-sm font-semibold px-4 py-2 rounded-lg cursor-pointer whitespace-nowrap hover:bg-teal-700 transition-colors"
            >
              <span className="w-4 h-4 flex items-center justify-center"><i className="ri-add-line text-sm"></i></span>
              동향 관리
            </Link>
            <button
              onClick={() => setIsDark(!isDark)}
              className={`w-9 h-9 flex items-center justify-center rounded-lg cursor-pointer transition-all ${
                isDark ? 'bg-[#1E2530] text-amber-400 hover:bg-[#2A3545]' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'
              }`}
              title={isDark ? '라이트 모드' : '다크 모드'}
            >
              <i className={isDark ? 'ri-sun-line text-lg' : 'ri-moon-line text-lg'}></i>
            </button>
          </div>
        </div>
      </div>

      <div className="px-8 py-6 space-y-5">
        {/* 최상위 토글: 동향 카드 | 뉴스 아카이브 */}
        <div className={`inline-flex p-1 rounded-xl border ${cardBorder} ${statBg}`}>
          {([
            { key: 'trends' as const, label: '동향 카드', icon: 'ri-layout-grid-line' },
            { key: 'archive' as const, label: '뉴스 아카이브', icon: 'ri-newspaper-line' },
          ]).map(t => (
            <button
              key={t.key}
              onClick={() => setView(t.key)}
              className={`flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-sm font-semibold cursor-pointer whitespace-nowrap transition-all ${
                view === t.key ? filterActive : `${textSub} hover:${textMain}`
              }`}
            >
              <i className={`${t.icon} text-sm`}></i>
              {t.label}
              {t.key === 'archive' && brands && (
                <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-black/10">
                  {brands.stats.total}
                </span>
              )}
            </button>
          ))}
        </div>

        {view === 'trends' && (<>
        {/* Stats — 실데이터 기반 */}
        <div className="grid grid-cols-4 gap-4">
          {[
            { label: '모니터링 MNC', value: String(stats.companies), icon: 'ri-building-2-line', color: '#0D9488' },
            { label: '이번 달 동향', value: String(stats.monthly), icon: 'ri-notification-3-line', color: '#7C3AED' },
            { label: '신규 출시 동향', value: String(stats.launch), icon: 'ri-rocket-line', color: '#D97706' },
            { label: '가격 변동 건수', value: String(stats.price), icon: 'ri-exchange-dollar-line', color: '#EF4444' },
          ].map(stat => (
            <div key={stat.label} className={`${statBg} rounded-xl p-4 border flex items-center gap-4`}>
              <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0" style={{ backgroundColor: stat.color + '15' }}>
                <span className="w-5 h-5 flex items-center justify-center" style={{ color: stat.color }}>
                  <i className={`${stat.icon} text-lg`}></i>
                </span>
              </div>
              <div>
                <p className={`${textSub} text-xs`}>{stat.label}</p>
                <p className="text-xl font-bold" style={{ color: stat.color }}>{loading ? '—' : stat.value}</p>
              </div>
            </div>
          ))}
        </div>

        {/* Filter Tabs */}
        <div className="flex gap-2 flex-wrap">
          {BADGE_TYPES.map(type => (
            <button
              key={type}
              onClick={() => setFilter(type)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${
                filter === type ? filterActive : filterBtn
              }`}
            >
              {type}
            </button>
          ))}
        </div>

        {/* 경쟁사(회사) 필터 — 실데이터 distinct company, badge/검색과 AND 조합 */}
        {!loading && !error && companyList.length > 0 && (
          <div className="flex gap-2 flex-wrap items-center">
            <span className={`text-[11px] font-semibold flex-shrink-0 ${textMuted}`}>MNC</span>
            <button
              onClick={() => setCompanyFilter('전체')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${
                companyFilter === '전체' ? filterActive : filterBtn
              }`}
            >
              전체
              <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${companyFilter === '전체' ? 'bg-black/10' : 'bg-black/10'}`}>
                {items.length}
              </span>
            </button>
            {companyList.map(({ company, count }) => (
              <button
                key={company}
                onClick={() => setCompanyFilter(company)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${
                  companyFilter === company ? filterActive : filterBtn
                }`}
              >
                {company}
                <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-black/10">
                  {count}
                </span>
              </button>
            ))}
          </div>
        )}

        {/* 필터 결과 건수 — badge + 경쟁사 + 검색 조합 반영 */}
        {!loading && !error && (
          <div className={`text-xs ${textMuted}`}>
            {filtered.length}건 표시
          </div>
        )}

        {/* Loading / Error */}
        {loading && (
          <div className={`text-center py-16 ${textSub} text-sm`}>
            <i className="ri-loader-4-line animate-spin mr-2"></i>동향 로딩 중…
          </div>
        )}
        {!loading && error && (
          <div className="text-center py-10 text-[#EF4444] text-sm">
            <i className="ri-error-warning-line mr-1"></i>{error}
          </div>
        )}

        {/* Cards Grid */}
        {!loading && !error && (
          <div className="grid grid-cols-3 gap-4">
            {filtered.map(item => {
              const color = item.color || '#1E2530';
              return (
                <div
                  key={item.id}
                  className={`${cardBg} rounded-2xl border ${cardBorder} ${cardHover} transition-all duration-200 overflow-hidden`}
                >
                  <div className="p-5">
                    <div className="flex items-center justify-between mb-4">
                      <div className="flex items-center gap-2.5">
                        <div
                          className={`w-9 h-9 rounded-xl flex items-center justify-center text-xs font-bold flex-shrink-0 ${textMain}`}
                          style={{ backgroundColor: color + '25', border: `1px solid ${color}40` }}
                        >
                          {item.logo || item.company.slice(0, 2).toUpperCase()}
                        </div>
                        <div>
                          <p className={`text-xs font-semibold leading-tight ${textMain}`}>{item.company}</p>
                          <p className={`text-xs ${textMuted}`}>{item.date}</p>
                        </div>
                      </div>
                      <span className={`text-xs font-semibold px-2 py-1 rounded-full whitespace-nowrap ${item.badgeColor || 'bg-gray-500/20 text-gray-400'}`}>
                        {item.badge}
                      </span>
                    </div>

                    <h4 className={`text-sm font-bold mb-2 leading-snug ${textMain}`}>{item.headline}</h4>
                    <p className={`text-xs leading-relaxed ${expanded === item.id ? '' : 'line-clamp-2'} ${textSub}`}>
                      {item.detail}
                    </p>

                    <div className="flex items-center justify-between mt-4">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <span className={`w-3.5 h-3.5 flex items-center justify-center flex-shrink-0 ${textMuted}`}><i className="ri-file-text-line text-xs"></i></span>
                        {item.url ? (
                          <a
                            href={item.url}
                            target="_blank"
                            rel="noreferrer"
                            className={`text-xs truncate hover:underline ${sourceText}`}
                          >
                            {item.source || '출처 링크'}
                          </a>
                        ) : (
                          <span className={`text-xs truncate ${sourceText}`}>{item.source || '정보 없음'}</span>
                        )}
                      </div>
                      <button
                        onClick={() => setExpanded(expanded === item.id ? null : item.id)}
                        className={`flex items-center gap-1 text-xs font-medium cursor-pointer whitespace-nowrap transition-colors ${accentColor} hover:opacity-80`}
                      >
                        {expanded === item.id ? '접기' : '더 보기'}
                        <span className="w-3.5 h-3.5 flex items-center justify-center">
                          <i className={`text-xs ${expanded === item.id ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'}`}></i>
                        </span>
                      </button>
                    </div>

                    {expanded === item.id && (
                      <RelatedNews company={item.company} isDark={isDark} />
                    )}
                  </div>

                  <div className="h-0.5 w-full" style={{ backgroundColor: color + '60' }}></div>
                </div>
              );
            })}
          </div>
        )}

        {!loading && !error && filtered.length === 0 && (
          <div className={`text-center py-16 ${textMuted}`}>
            <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3"><i className="ri-search-line text-4xl"></i></span>
            <p className="text-sm">{items.length === 0 ? '등록된 동향이 없습니다 — 동향 관리에서 추가하세요' : '검색 결과가 없습니다'}</p>
          </div>
        )}
        </>)}

        {/* ───────────── 뉴스 아카이브 (B) ───────────── */}
        {view === 'archive' && (
          <>
            {/* 헤더 메타 */}
            <div className={`${statBg} rounded-xl p-4 border ${cardBorder}`}>
              {brandsErr && (
                <p className="text-sm text-[#EF4444]"><i className="ri-error-warning-line mr-1"></i>{brandsErr}</p>
              )}
              {!brandsErr && brands && (
                <div className="flex items-center flex-wrap gap-x-3 gap-y-1 text-sm">
                  <span className={`font-bold ${textMain}`}>총 {brands.stats.total.toLocaleString()}건</span>
                  <span className={textMuted}>·</span>
                  <span className={textSub}>Tier 1 전문지</span>
                  <span className={textMuted}>·</span>
                  <span className={textSub}>{brands.stats.earliest ?? '—'} ~ {brands.stats.latest ?? '—'}</span>
                  <span className={textMuted}>·</span>
                  <span className={textSub}>1년 보존</span>
                </div>
              )}
              {!brandsErr && !brands && (
                <div className={`flex items-center gap-1.5 text-sm ${textMuted}`}>
                  <i className="ri-loader-4-line animate-spin"></i>아카이브 메타 로딩 중…
                </div>
              )}
            </div>

            {/* 브랜드 필터 칩 (anchor 그룹별) */}
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={() => setSelBrand('전체')}
                  className={`px-3 py-1.5 rounded-full text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${
                    selBrand === '전체' ? filterActive : filterBtn
                  }`}
                >
                  전체
                  {brands && (
                    <span className="ml-1.5 opacity-70">{brands.stats.total}</span>
                  )}
                </button>
              </div>
              {brandGroups.map(({ group, brands: gb }) => (
                <div key={group} className="flex flex-wrap items-center gap-2">
                  <span className={`text-[11px] font-semibold w-16 flex-shrink-0 ${textMuted}`}>{group}</span>
                  {gb.map(b => {
                    const active = selBrand === b.query;
                    const c = b.color ?? '#00E5CC';
                    return (
                      <button
                        key={b.query}
                        onClick={() => setSelBrand(b.query)}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium cursor-pointer whitespace-nowrap transition-all border ${
                          active ? '' : filterBtn
                        }`}
                        style={active ? { backgroundColor: c + '20', borderColor: c, color: c } : undefined}
                      >
                        <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ backgroundColor: c }}></span>
                        {b.query}
                        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${active ? '' : 'bg-black/10'}`}
                          style={active ? { backgroundColor: c + '30' } : undefined}>
                          {b.news_count}
                        </span>
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>

            {/* 기간 segmented control */}
            <div className="flex items-center gap-3">
              <div className={`inline-flex p-1 rounded-lg border ${cardBorder} ${statBg}`}>
                {PERIOD_OPTIONS.map(p => (
                  <button
                    key={p.days}
                    onClick={() => setPeriod(p.days)}
                    className={`px-3 py-1 rounded-md text-xs font-semibold cursor-pointer whitespace-nowrap transition-all ${
                      period === p.days ? filterActive : `${textSub} hover:${textMain}`
                    }`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
              {!newsLoading && !newsErr && (
                <span className={`text-xs ${textMuted}`}>{news.length}건 표시</span>
              )}
            </div>

            {/* 뉴스 리스트 */}
            {newsLoading && (
              <div className={`text-center py-16 ${textSub} text-sm`}>
                <i className="ri-loader-4-line animate-spin mr-2"></i>뉴스 로딩 중…
              </div>
            )}
            {!newsLoading && newsErr && (
              <div className="text-center py-10 text-[#EF4444] text-sm">
                <i className="ri-error-warning-line mr-1"></i>{newsErr}
              </div>
            )}
            {!newsLoading && !newsErr && news.length === 0 && (
              <div className={`text-center py-16 ${textMuted}`}>
                <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3"><i className="ri-newspaper-line text-4xl"></i></span>
                <p className="text-sm">선택한 조건에 해당하는 뉴스가 없습니다</p>
              </div>
            )}
            {!newsLoading && !newsErr && news.length > 0 && (
              <div className="space-y-2">
                {news.map(n => {
                  const c = brands?.brands.find(b => b.query === n.brand)?.color ?? selBrandColor;
                  return (
                    <a
                      key={n.id}
                      href={n.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={`block ${cardBg} rounded-xl border ${cardBorder} ${cardHover} transition-all p-4`}
                    >
                      <div className="flex items-start gap-3">
                        <span
                          className="text-[11px] font-bold px-2 py-1 rounded-lg flex-shrink-0 whitespace-nowrap"
                          style={{ backgroundColor: c + '22', color: c, border: `1px solid ${c}40` }}
                        >
                          {n.brand}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1.5 mb-1 flex-wrap">
                            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-[#00857C]/20 text-[#00E5CC]">T1</span>
                            <span className={`text-xs font-medium ${textSub}`}>{n.source_name || n.source_domain}</span>
                            <span className={textMuted}>·</span>
                            <span className={`text-xs ${textMuted}`}>{n.pub_date}</span>
                          </div>
                          <h4 className={`text-sm font-bold leading-snug mb-1 ${textMain}`}>{n.title}</h4>
                          {n.description && (
                            <p className={`text-xs leading-relaxed line-clamp-2 ${textSub}`}>{n.description}</p>
                          )}
                          <p className={`text-[10px] mt-1.5 ${textMuted}`}>{n.anchor}</p>
                        </div>
                        <span className={`w-4 h-4 flex items-center justify-center flex-shrink-0 ${textMuted}`}>
                          <i className="ri-external-link-line text-xs"></i>
                        </span>
                      </div>
                    </a>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
