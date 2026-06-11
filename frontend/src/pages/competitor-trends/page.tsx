import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { listCompetitorTrends, COMPETITOR_BADGES, type CompetitorTrend } from '@/api/competitorTrends';

const BADGE_TYPES = ['전체', ...COMPETITOR_BADGES];

export default function CompetitorTrendsPage() {
  const [isDark, setIsDark] = useState(false);
  const [filter, setFilter] = useState('전체');
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
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : '조회 실패'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const filtered = items.filter(d => {
    const matchBadge = filter === '전체' || d.badge === filter;
    const matchSearch = !search || d.company.toLowerCase().includes(search.toLowerCase()) || d.headline.toLowerCase().includes(search.toLowerCase());
    return matchBadge && matchSearch;
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
            <p className={`${textSub} text-sm`}>경쟁사 동향 모니터링 및 전략 분석</p>
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
        {/* Stats — 실데이터 기반 */}
        <div className="grid grid-cols-4 gap-4">
          {[
            { label: '모니터링 경쟁사', value: String(stats.companies), icon: 'ri-building-2-line', color: '#0D9488' },
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
      </div>
    </div>
  );
}
