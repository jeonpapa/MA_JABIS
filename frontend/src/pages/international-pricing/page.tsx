import { useMemo, useState } from 'react';
import { useApi } from '@/hooks/useApi';
import {
  APPROVAL_AGENCIES,
  fetchApprovalTab,
  fetchForeignDrugList,
  fetchHtaTab,
  fetchPricingTab,
  searchForeignLive,
} from '@/api/foreign';
import type {
  ApprovalTabData,
  ForeignDrugListItem,
  HtaTabData,
  PricingTabData,
} from '@/api/foreign';

const A8_COUNTRIES = [
  { key: 'usa', label: '미국', flag: '🇺🇸', currency: 'USD' },
  { key: 'uk', label: '영국', flag: '🇬🇧', currency: 'GBP' },
  { key: 'germany', label: '독일', flag: '🇩🇪', currency: 'EUR' },
  { key: 'france', label: '프랑스', flag: '🇫🇷', currency: 'EUR' },
  { key: 'canada', label: '캐나다', flag: '🇨🇦', currency: 'CAD' },
  { key: 'japan', label: '일본', flag: '🇯🇵', currency: 'JPY' },
  { key: 'italy', label: '이탈리아', flag: '🇮🇹', currency: 'EUR' },
  { key: 'switzerland', label: '스위스', flag: '🇨🇭', currency: 'CHF' },
];

const HTA_COUNTRIES = [
  { key: 'uk', label: '영국', flag: '🇬🇧', body: 'NICE' },
  { key: 'canada', label: '캐나다', flag: '🇨🇦', body: 'CADTH' },
  { key: 'australia', label: '호주', flag: '🇦🇺', body: 'PBAC' },
  { key: 'scotland', label: '스코틀랜드', flag: '🏴󠁧󠁢󠁳󠁣󠁴󠁿', body: 'SMC' },
];

const getCurrencySymbol = (currency: string) => {
  const map: Record<string, string> = { USD: '$', GBP: '£', JPY: '¥', CHF: 'Fr.', EUR: '€', CAD: 'CA$' };
  return map[currency] || currency;
};

const formatKrw = (v?: number) => (v == null ? null : `₩${Math.round(v).toLocaleString()}`);

const displayName = (q: string) => (q ? q.charAt(0).toUpperCase() + q.slice(1) : q);

interface Selection {
  query: string;       // 백엔드 query_name / product slug (keytruda, welireg …)
  canonical: string;   // INN — 이력에 없는 ad-hoc 검색이면 ''
  countryCount?: number;
}

export default function InternationalPricingPage() {
  const [isDark, setIsDark] = useState(false);
  const [newSearchQuery, setNewSearchQuery] = useState('');
  const [selected, setSelected] = useState<Selection | null>(null);
  const [activeTab, setActiveTab] = useState<'pricing' | 'hta' | 'approval'>('pricing');
  const [visitedHta, setVisitedHta] = useState(false);
  const [visitedApproval, setVisitedApproval] = useState(false);
  const [expandedHta, setExpandedHta] = useState<string | null>(null);
  const [expandedApproval, setExpandedApproval] = useState<string | null>(null);
  const [searchDropdown, setSearchDropdown] = useState(false);
  const [liveSearching, setLiveSearching] = useState(false);
  const [liveError, setLiveError] = useState<string | null>(null);

  // ── 데이터 로딩 (캐시/DB 조회 전용 — 라이브 스크레이프는 명시적 버튼 뒤에만) ──
  const history = useApi<ForeignDrugListItem[]>(fetchForeignDrugList, []);

  const pricing = useApi<PricingTabData | null>(
    () => (selected ? fetchPricingTab(selected.query) : Promise.resolve(null)),
    [selected?.query],
  );
  // HTA / 허가 탭은 첫 진입 시점에 lazy 로딩
  const hta = useApi<HtaTabData | null>(
    () => (selected && visitedHta ? fetchHtaTab(selected.query) : Promise.resolve(null)),
    [selected?.query, visitedHta],
  );
  const approval = useApi<ApprovalTabData | null>(
    () => (selected && visitedApproval ? fetchApprovalTab(selected.query) : Promise.resolve(null)),
    [selected?.query, visitedApproval],
  );

  const historyItems = history.data ?? [];
  const searchResults = useMemo(() => {
    const q = newSearchQuery.trim().toLowerCase();
    if (!q) return [];
    return historyItems.filter(d =>
      d.queryName.toLowerCase().includes(q)
      || d.canonical.toLowerCase().includes(q)
      || d.aliases.some(a => a.toLowerCase().includes(q)));
  }, [newSearchQuery, historyItems]);

  const handleSelect = (sel: Selection) => {
    setSelected(sel);
    setNewSearchQuery('');
    setSearchDropdown(false);
    setActiveTab('pricing');
    setVisitedHta(false);
    setVisitedApproval(false);
    setExpandedHta(null);
    setExpandedApproval(null);
    setLiveError(null);
  };
  const handleSelectHistory = (d: ForeignDrugListItem) =>
    handleSelect({ query: d.queryName, canonical: d.canonical, countryCount: d.countryCount });
  const handleNewSearch = () => {
    const q = newSearchQuery.trim();
    if (!q) return;
    if (searchResults.length > 0) handleSelectHistory(searchResults[0]);
    else handleSelect({ query: q.toLowerCase(), canonical: '' }); // 캐시 조회만 — 비어있으면 빈 상태 + 라이브 버튼
  };
  const handleTab = (key: 'pricing' | 'hta' | 'approval') => {
    setActiveTab(key);
    if (key === 'hta') setVisitedHta(true);
    if (key === 'approval') setVisitedApproval(true);
  };

  // 라이브 스크레이프 — 비용이 크므로 (수 분) 명시적 confirm 뒤에만 실행
  const runLiveSearch = async () => {
    if (!selected || liveSearching) return;
    const ok = window.confirm(
      `'${displayName(selected.query)}' 해외약가 실시간 스크레이핑을 실행할까요?\n8개국 수집에 수 분이 소요됩니다.`,
    );
    if (!ok) return;
    setLiveSearching(true);
    setLiveError(null);
    try {
      await searchForeignLive(selected.query);
      pricing.reload();
      history.reload();
    } catch (e) {
      setLiveError(e instanceof Error ? e.message : String(e));
    } finally {
      setLiveSearching(false);
    }
  };

  // ── 스타일 토큰 (readdy 디자인 유지) ──
  const pageBg = isDark ? 'bg-[#0D1117]' : 'bg-gray-50';
  const headerBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const cardBg = isDark ? 'bg-[#161B27]' : 'bg-white';
  const cardBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
  const textMuted = isDark ? 'text-[#4A5568]' : 'text-gray-400';
  const accentColor = isDark ? 'text-[#00E5CC]' : 'text-teal-600';
  const accentBg = isDark ? 'bg-[#00E5CC]/8' : 'bg-teal-50';
  const accentBorder = isDark ? 'border-[#00E5CC]/40' : 'border-teal-300';
  const historyCard = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const historyCardHover = isDark ? 'hover:border-[#2A3545]' : 'hover:border-gray-300';
  const searchBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const searchInputBg = isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-50 border-gray-200';
  const searchFocus = isDark ? 'focus-within:border-[#00E5CC]/50' : 'focus-within:border-teal-300';
  const searchText = isDark ? 'text-white placeholder-[#4A5568]' : 'text-gray-900 placeholder-gray-400';
  const tabBg = isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-100 border-gray-200';
  const tabActive = isDark ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'bg-teal-600 text-white';
  const tabInactive = isDark ? 'text-[#8B9BB4] hover:text-white' : 'text-gray-500 hover:text-gray-900';
  const dropdownBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200 shadow-lg';
  const dropdownHover = isDark ? 'hover:bg-[#00E5CC]/8' : 'hover:bg-teal-50';
  const htaCardBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const htaCardHover = isDark ? 'hover:bg-[#1E2530]/50' : 'hover:bg-gray-100';
  const fullTextBg = isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-50 border-gray-200';
  const emptyCenter = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const divider = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const pillBg = isDark ? 'bg-[#1E2530]' : 'bg-gray-100';
  const pillText = isDark ? 'text-[#8B9BB4]' : 'text-gray-600';

  const statusBadgeClass = (status?: string) =>
    status === '권고' ? 'bg-emerald-50 text-emerald-600 border border-emerald-200'
      : status === '조건부 권고' ? 'bg-amber-50 text-amber-600 border border-amber-200'
        : status === '비권고' ? 'bg-red-50 text-red-500 border border-red-200'
          : (isDark ? 'bg-[#4A5568]/20 text-[#8B9BB4]' : 'bg-gray-100 text-gray-600');

  // ── 탭 공통 상태 컴포넌트 ──
  const TabLoading = ({ label }: { label: string }) => (
    <div className={`rounded-2xl border py-12 text-center ${cardBg} ${cardBorder}`}>
      <span className={`w-8 h-8 inline-flex items-center justify-center mb-2 ${accentColor}`}>
        <i className="ri-loader-4-line text-2xl animate-spin"></i>
      </span>
      <p className={`text-sm ${textSub}`}>{label}</p>
    </div>
  );
  const TabError = ({ message, onRetry }: { message: string; onRetry: () => void }) => (
    <div className={`rounded-2xl border py-10 text-center ${cardBg} ${cardBorder}`}>
      <span className="w-8 h-8 inline-flex items-center justify-center mb-2 text-red-400">
        <i className="ri-error-warning-line text-2xl"></i>
      </span>
      <p className={`text-sm mb-3 ${textSub}`}>데이터 로딩 실패: {message}</p>
      <button onClick={onRetry}
        className="text-xs font-semibold px-4 py-2 rounded-lg bg-teal-600 text-white cursor-pointer hover:bg-teal-700 transition-colors">
        다시 시도
      </button>
    </div>
  );

  const headerName = selected ? displayName(selected.query) : '';
  const headerIngredient = selected ? (selected.canonical || pricing.data?.ingredient || '') : '';

  return (
    <div className={`min-h-screen ${pageBg} ${isDark ? 'text-white' : 'text-gray-900'}`}>
      <div className={`px-8 pt-8 pb-6 border-b ${headerBorder}`}>
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-global-line"></i></span>
              <h1 className={`text-2xl font-bold ${textMain}`}>해외약가</h1>
            </div>
            <p className={`${textSub} text-sm`}>A8 국가 급여 약가 및 HTA · 허가 현황</p>
          </div>
          <div className="flex items-center gap-3">
            <button className="flex items-center gap-2 bg-teal-600 text-white text-sm font-semibold px-4 py-2 rounded-lg cursor-pointer whitespace-nowrap hover:bg-teal-700 transition-colors">
              <span className="w-4 h-4 flex items-center justify-center"><i className="ri-download-2-line text-sm"></i></span>리포트 다운로드
            </button>
            <button onClick={() => setIsDark(!isDark)}
              className={`w-9 h-9 flex items-center justify-center rounded-lg cursor-pointer transition-all ${isDark ? 'bg-[#1E2530] text-amber-400 hover:bg-[#2A3545]' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'}`}
              title={isDark ? '라이트 모드' : '다크 모드'}>
              <i className={isDark ? 'ri-sun-line text-lg' : 'ri-moon-line text-lg'}></i>
            </button>
          </div>
        </div>
      </div>

      <div className="px-8 py-6 space-y-5">
        {/* History */}
        <div>
          <div className="flex items-center gap-2 mb-3">
            <span className={`w-4 h-4 flex items-center justify-center ${textSub}`}><i className="ri-history-line text-sm"></i></span>
            <p className={`text-xs font-semibold uppercase tracking-wider ${textSub}`}>기존 검색 이력</p>
            <span className={`text-xs px-2 py-0.5 rounded-full ${pillBg} ${pillText}`}>{historyItems.length}건</span>
            {history.loading && <span className={`w-3.5 h-3.5 flex items-center justify-center ${textMuted}`}><i className="ri-loader-4-line text-xs animate-spin"></i></span>}
          </div>
          {history.error ? (
            <div className={`rounded-2xl border px-5 py-4 ${cardBg} ${cardBorder}`}>
              <p className={`text-sm ${textSub}`}>검색 이력 로딩 실패: {history.error}</p>
              <button onClick={history.reload} className={`text-xs mt-2 font-medium cursor-pointer ${accentColor}`}>다시 시도</button>
            </div>
          ) : !history.loading && historyItems.length === 0 ? (
            <div className={`rounded-2xl border px-5 py-4 ${cardBg} ${cardBorder}`}>
              <p className={`text-sm ${textMuted}`}>검색 이력이 없습니다 — 신규 검색으로 시작하세요</p>
            </div>
          ) : (
            <div className="grid grid-cols-4 gap-3">
              {historyItems.map(drug => (
                <button key={drug.id} onClick={() => handleSelectHistory(drug)}
                  className={`text-left p-4 rounded-2xl border transition-all cursor-pointer group ${selected?.query === drug.queryName ? accentBg + ' ' + accentBorder : historyCard + ' ' + historyCardHover}`}>
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex-1 min-w-0">
                      <p className={`text-sm font-bold truncate ${selected?.query === drug.queryName ? accentColor : textMain} group-hover:${accentColor} transition-colors`}>{displayName(drug.queryName)}</p>
                      <p className={`${textSub} text-xs mt-0.5 truncate`}>{drug.canonical}</p>
                    </div>
                    {selected?.query === drug.queryName && <span className={`w-5 h-5 flex items-center justify-center flex-shrink-0 ml-2 ${accentColor}`}><i className="ri-checkbox-circle-fill text-sm"></i></span>}
                  </div>
                  <div className="space-y-1.5">
                    <div className="flex items-center gap-1.5"><span className={`w-3.5 h-3.5 flex items-center justify-center ${textMuted}`}><i className="ri-calendar-line text-xs"></i></span><span className={`text-xs ${textMuted}`}>{drug.lastSearchedAt}</span></div>
                    <div className="flex items-center gap-1.5"><span className={`w-3.5 h-3.5 flex items-center justify-center ${textMuted}`}><i className="ri-global-line text-xs"></i></span><span className={`text-xs truncate ${textMuted}`}>{drug.countryCount}개국 가격 캐시</span></div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* New Search */}
        <div className={`rounded-2xl border p-5 ${searchBg} ${cardBorder}`}>
          <div className="flex items-center gap-2 mb-3">
            <span className={`w-4 h-4 flex items-center justify-center ${accentColor}`}><i className="ri-search-2-line text-sm"></i></span>
            <p className={`text-sm font-semibold ${textMain}`}>신규 검색</p>
            <span className={`text-xs ${textMuted}`}>이력에 없는 제품은 캐시 조회 후 실시간 검색을 선택할 수 있습니다</span>
          </div>
          <div className="flex gap-3 relative">
            <div className="flex-1 relative">
              <div className={`flex items-center gap-3 rounded-xl px-4 py-3 transition-colors ${searchInputBg} ${searchFocus}`}>
                <span className={`w-4 h-4 flex items-center justify-center flex-shrink-0 ${textSub}`}><i className="ri-search-line text-sm"></i></span>
                <input type="text" placeholder="영문 성분명 또는 제품명 입력 (예: Pembrolizumab, Keytruda)"
                  value={newSearchQuery} onChange={e => { setNewSearchQuery(e.target.value); setSearchDropdown(true); }}
                  onFocus={() => newSearchQuery && setSearchDropdown(true)} onKeyDown={e => e.key === 'Enter' && handleNewSearch()}
                  className={`flex-1 bg-transparent text-sm focus:outline-none ${searchText}`} />
                {newSearchQuery && <button onClick={() => { setNewSearchQuery(''); setSearchDropdown(false); }} className={`w-4 h-4 flex items-center justify-center cursor-pointer transition-colors ${textMuted} hover:${textMain}`}><i className="ri-close-line text-sm"></i></button>}
              </div>
              {searchDropdown && newSearchQuery && (
                <div className={`absolute top-full left-0 right-0 mt-1 rounded-xl overflow-hidden z-50 ${dropdownBg}`}>
                  {searchResults.map(d => (
                    <button key={d.id} onClick={() => handleSelectHistory(d)}
                      className={`w-full flex items-center gap-3 px-4 py-3 transition-colors cursor-pointer text-left border-b last:border-0 ${divider} ${dropdownHover}`}>
                      <span className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 ${isDark ? 'bg-[#00E5CC]/10' : 'bg-teal-50'}`}><i className={`ri-capsule-line text-xs ${accentColor}`}></i></span>
                      <div className="flex-1 min-w-0"><p className={`text-sm font-semibold ${textMain}`}>{displayName(d.queryName)}</p><p className={`text-xs ${textSub}`}>{d.canonical}</p></div>
                      <span className={`text-xs whitespace-nowrap ${textMuted}`}>검색 이력 있음</span>
                    </button>
                  ))}
                  {searchResults.length === 0 && (
                    <button onClick={handleNewSearch}
                      className={`w-full flex items-center gap-3 px-4 py-3 transition-colors cursor-pointer text-left ${dropdownHover}`}>
                      <span className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 ${isDark ? 'bg-[#00E5CC]/10' : 'bg-teal-50'}`}><i className={`ri-search-line text-xs ${accentColor}`}></i></span>
                      <div className="flex-1 min-w-0"><p className={`text-sm font-semibold ${textMain}`}>'{newSearchQuery.trim()}' 캐시 조회</p><p className={`text-xs ${textSub}`}>이력에 없는 제품 — 캐시 확인 후 실시간 검색 가능</p></div>
                    </button>
                  )}
                </div>
              )}
            </div>
            <button onClick={handleNewSearch} className="flex items-center gap-2 bg-teal-600 text-white text-sm font-bold px-5 py-3 rounded-xl cursor-pointer whitespace-nowrap hover:bg-teal-700 transition-colors">
              <span className="w-4 h-4 flex items-center justify-center"><i className="ri-search-line text-sm"></i></span>검색
            </button>
          </div>
        </div>

        {/* Detail Panel */}
        {selected ? (
          <div className="space-y-4">
            <div className={`rounded-2xl border px-6 py-4 ${cardBg} ${cardBorder}`}>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4 min-w-0">
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0 ${isDark ? 'bg-[#00E5CC]/10' : 'bg-teal-50'}`}><i className={`ri-capsule-line text-lg ${accentColor}`}></i></div>
                  <div className="min-w-0"><h2 className={`text-lg font-bold truncate ${textMain}`}>{headerName}</h2><p className={`text-sm truncate ${textSub}`}>{headerIngredient || '성분 정보 없음'}</p></div>
                  <div className={`flex items-center gap-3 ml-4 pl-4 border-l flex-shrink-0 ${divider}`}>
                    <div className="flex items-center gap-1.5"><span className={`w-3.5 h-3.5 flex items-center justify-center ${textMuted}`}><i className="ri-calendar-line text-xs"></i></span><span className={`text-xs ${textMuted}`}>{pricing.data?.lastSearchedAt ? `최근 수집 ${pricing.data.lastSearchedAt}` : '수집일 정보 없음'}</span></div>
                    {selected.countryCount != null && (
                      <div className="flex items-center gap-1.5"><span className={`w-3.5 h-3.5 flex items-center justify-center ${textMuted}`}><i className="ri-global-line text-xs"></i></span><span className={`text-xs ${textMuted}`}>{selected.countryCount}개국 캐시</span></div>
                    )}
                  </div>
                </div>
                <div className={`flex items-center gap-1 rounded-xl p-1 flex-shrink-0 ${tabBg}`}>
                  {[
                    { key: 'pricing', label: 'A8 급여약가', icon: 'ri-money-dollar-circle-line' },
                    { key: 'hta', label: 'HTA 현황', icon: 'ri-shield-check-line' },
                    { key: 'approval', label: '허가 현황', icon: 'ri-file-check-line' },
                  ].map(tab => (
                    <button key={tab.key} onClick={() => handleTab(tab.key as 'pricing' | 'hta' | 'approval')}
                      className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium cursor-pointer transition-all whitespace-nowrap ${activeTab === tab.key ? tabActive : tabInactive}`}>
                      <span className="w-4 h-4 flex items-center justify-center"><i className={`${tab.icon} text-xs`}></i></span>{tab.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Pricing Tab */}
            {activeTab === 'pricing' && (
              pricing.loading ? <TabLoading label="A8 국가 캐시 가격 로딩 중…" />
                : pricing.error ? <TabError message={pricing.error} onRetry={pricing.reload} />
                  : (
                    <div className="space-y-3">
                      {pricing.data && !pricing.data.hasAnyPrice && (
                        <div className={`rounded-2xl border px-5 py-4 flex items-center justify-between gap-4 ${cardBg} ${cardBorder}`}>
                          <div>
                            <p className={`text-sm font-semibold ${textMain}`}>캐시된 가격 데이터가 없습니다</p>
                            <p className={`text-xs mt-0.5 ${textMuted}`}>실시간 스크레이핑은 8개국 수집에 수 분이 소요됩니다 — 필요한 경우에만 실행하세요</p>
                            {liveError && <p className="text-xs mt-1 text-red-400">실시간 검색 실패: {liveError}</p>}
                          </div>
                          <button onClick={runLiveSearch} disabled={liveSearching}
                            className={`flex items-center gap-2 text-xs font-bold px-4 py-2.5 rounded-xl whitespace-nowrap transition-colors ${liveSearching ? 'bg-gray-300 text-gray-500 cursor-not-allowed' : 'bg-teal-600 text-white hover:bg-teal-700 cursor-pointer'}`}>
                            <i className={liveSearching ? 'ri-loader-4-line animate-spin' : 'ri-radar-line'}></i>
                            {liveSearching ? '실시간 수집 중… (수 분 소요)' : '실시간 검색 실행'}
                          </button>
                        </div>
                      )}
                      <div className={`rounded-2xl border overflow-hidden ${cardBg} ${cardBorder}`}>
                        <div className={`px-5 py-4 border-b ${divider}`}>
                          <h3 className={`font-bold text-sm ${textMain}`}>A8 국가 급여 약가</h3>
                          <p className={`text-xs mt-0.5 ${textMuted}`}>미국 · 영국 · 독일 · 프랑스 · 캐나다 · 일본 · 이탈리아 · 스위스 — 급여 배지는 공개 DB에서 확인된 경우에만 표시</p>
                        </div>
                        <div className="grid grid-cols-4 gap-0">
                          {A8_COUNTRIES.map((country, idx) => {
                            const cell = pricing.data?.a8Pricing[country.key];
                            const cov = pricing.data?.coverageNotes[country.key];
                            return (
                              <div key={country.key} className={`p-5 ${idx % 4 !== 3 ? 'border-r' : ''} ${idx >= 4 ? 'border-t' : ''} ${divider}`}>
                                <div className="flex items-center gap-2 mb-3"><span className="text-xl">{country.flag}</span><div className="flex-1 min-w-0"><p className={`text-xs font-semibold ${textMain}`}>{country.label}</p><p className={`text-xs ${textMuted}`}>{country.currency}</p></div>
                                  {cell?.reimbursed && <span className="text-xs px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-600 border border-emerald-200 font-semibold whitespace-nowrap" title={`급여 ${cell.reimbursedLabel}`}>급여{cell.reimbursedLabel === '조건부' ? '(조건부)' : ''}</span>}
                                </div>
                                {cell ? (
                                  <>
                                    {cell.price != null ? (
                                      <p className={`text-lg font-bold mb-1 ${accentColor}`} title={cell.productName}>{getCurrencySymbol(cell.currency)}{cell.price.toLocaleString()}</p>
                                    ) : (
                                      <p className={`text-sm font-semibold mb-1 ${textSub}`}>가격 미공개</p>
                                    )}
                                    {cell.dosageStrength && <p className={`text-xs truncate ${textSub}`} title={cell.dosageStrength}>{cell.dosageStrength}{cell.packCount && cell.packCount > 1 ? ` × ${cell.packCount}` : ''}</p>}
                                    {cell.adjustedPriceKrw != null && <p className={`text-xs mt-1 ${textSub}`}>조정가 {formatKrw(cell.adjustedPriceKrw)}</p>}
                                    {cell.dailyCostKrw != null && <p className={`text-xs ${textSub}`}>일일 {formatKrw(cell.dailyCostKrw)}{cell.dosingScheduleLabel ? ` (${cell.dosingScheduleLabel})` : ''}</p>}
                                    {cell.reimbursed && cell.reimbursedDate && <p className={`text-xs mt-1 ${textMuted}`}>급여 결정: {cell.reimbursedDate}</p>}
                                    {!cell.reimbursedKnown && <p className={`text-xs mt-1 ${textMuted}`}>급여정보 없음</p>}
                                    {cell.price == null && (cov?.policy || cell.note) && <p className={`text-xs mt-1 leading-relaxed ${textMuted}`}>{cov?.policy || cell.note}</p>}
                                    {cell.sourceLabel && <p className={`text-xs mt-1 truncate ${textMuted}`} title={cell.sourceLabel}>{cell.sourceLabel}{cell.variantCount > 1 ? ` · 외 ${cell.variantCount - 1}건` : ''}</p>}
                                  </>
                                ) : (
                                  <>
                                    <p className={`text-sm ${textMuted}`}>정보 없음</p>
                                    {cov?.policy && <p className={`text-xs mt-1 leading-relaxed ${textMuted}`}>{cov.policy}</p>}
                                  </>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    </div>
                  )
            )}

            {/* HTA Tab */}
            {activeTab === 'hta' && (
              hta.loading ? <TabLoading label="HTA 평가 현황 로딩 중…" />
                : hta.error ? <TabError message={hta.error} onRetry={hta.reload} />
                  : (
                    <div className="space-y-3">
                      <div className={`rounded-2xl border px-5 py-3 ${cardBg} ${cardBorder}`}>
                        <h3 className={`font-bold text-sm ${textMain}`}>HTA 중심 국가 평가 현황</h3>
                        <p className={`text-xs mt-0.5 ${textMuted}`}>영국(NICE) · 캐나다(CADTH) · 호주(PBAC) · 스코틀랜드(SMC) — 항목을 클릭하면 평가 이력 전체를 확인할 수 있습니다</p>
                      </div>
                      {HTA_COUNTRIES.map(country => {
                        const rec = hta.data?.[country.key];
                        const isExpanded = expandedHta === country.key;
                        return (
                          <div key={country.key} className={`rounded-2xl border overflow-hidden ${htaCardBg} ${cardBorder}`}>
                            <button onClick={() => rec && setExpandedHta(isExpanded ? null : country.key)}
                              className={`w-full flex items-center gap-4 px-5 py-4 transition-colors text-left ${rec ? 'cursor-pointer ' + htaCardHover : 'cursor-default'}`}>
                              <span className="text-2xl flex-shrink-0">{country.flag}</span>
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2 mb-1">
                                  <p className={`text-sm font-bold ${textMain}`}>{country.label}</p><span className={`text-xs ${textMuted}`}>({country.body})</span>
                                  {rec && <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${statusBadgeClass(rec.status)}`}>{rec.recommendation}</span>}
                                  {rec && rec.totalCount > 1 && <span className={`text-xs px-1.5 py-0.5 rounded-full ${pillBg} ${pillText}`}>총 {rec.totalCount}건</span>}
                                </div>
                                {rec ? <div className="flex items-center gap-3 min-w-0"><span className={`text-xs whitespace-nowrap ${textSub}`}>평가일: {rec.date || '-'}</span><span className={`text-xs ${textMuted}`}>|</span><span className={`text-xs truncate ${textSub}`}>{rec.note || '제목 없음'}</span></div>
                                  : <p className={`text-xs ${textMuted}`}>평가 정보 없음</p>}
                              </div>
                              {rec && (
                                <div className="flex items-center gap-2 flex-shrink-0">
                                  <span className={`text-xs font-medium whitespace-nowrap ${accentColor}`}>{isExpanded ? '접기' : '평가 이력 보기'}</span>
                                  <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className={`text-sm transition-transform duration-200 ${isExpanded ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'}`}></i></span>
                                </div>
                              )}
                            </button>
                            {isExpanded && rec && (
                              <div className={`px-5 pb-5 border-t ${divider}`}>
                                <div className={`mt-4 rounded-xl p-4 border ${fullTextBg}`}>
                                  <div className="flex items-center gap-2 mb-3"><span className={`w-4 h-4 flex items-center justify-center ${accentColor}`}><i className="ri-file-text-line text-xs"></i></span><p className={`text-xs font-semibold uppercase tracking-wider ${accentColor}`}>{country.body} Appraisals ({rec.totalCount})</p></div>
                                  <div className="space-y-3 max-h-96 overflow-y-auto pr-1">
                                    {rec.allDecisions.map((d, i) => (
                                      <div key={i} className={`pb-3 ${i < rec.allDecisions.length - 1 ? 'border-b ' + divider : ''}`}>
                                        <div className="flex items-center gap-2 mb-1 flex-wrap">
                                          <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${statusBadgeClass(d.status)}`}>{d.recommendation}</span>
                                          <span className={`text-xs ${textMuted}`}>{d.date || '일자 미상'}</span>
                                          {d.detailUrl && <a href={d.detailUrl} target="_blank" rel="noreferrer" className={`text-xs font-medium ${accentColor}`} onClick={e => e.stopPropagation()}>원문 ↗</a>}
                                        </div>
                                        <p className={`text-xs leading-relaxed ${textSub}`}>{d.title || '(제목 없음)'}</p>
                                        {d.indication && <p className={`text-xs leading-relaxed mt-0.5 ${textMuted}`}>{d.indication}</p>}
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )
            )}

            {/* Approval Tab — 실데이터 보유 6개 규제기관 (FDA/EMA/MHRA/PMDA/MFDS/TGA) */}
            {activeTab === 'approval' && (
              approval.loading ? <TabLoading label="허가 현황 로딩 중…" />
                : approval.error ? <TabError message={approval.error} onRetry={approval.reload} />
                  : (
                    <div className="space-y-3">
                      <div className={`rounded-2xl border px-5 py-3 ${cardBg} ${cardBorder}`}>
                        <h3 className={`font-bold text-sm ${textMain}`}>주요국 허가 현황</h3>
                        <p className={`text-xs mt-0.5 ${textMuted}`}>FDA(미국) · EMA(유럽연합) · MHRA(영국) · PMDA(일본) · MFDS(한국) · TGA(호주) — 항목을 클릭하면 적응증별 허가 원문을 확인할 수 있습니다</p>
                      </div>
                      {APPROVAL_AGENCIES.map(agency => {
                        const rec = approval.data?.[agency.key];
                        const isExpanded = expandedApproval === agency.key;
                        const hasData = !!rec?.hasData;
                        return (
                          <div key={agency.key} className={`rounded-2xl border overflow-hidden ${htaCardBg} ${cardBorder}`}>
                            <button onClick={() => hasData && setExpandedApproval(isExpanded ? null : agency.key)}
                              className={`w-full flex items-center gap-4 px-5 py-4 transition-colors text-left ${hasData ? 'cursor-pointer ' + htaCardHover : 'cursor-default'}`}>
                              <span className="text-2xl flex-shrink-0">{agency.flag}</span>
                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2 mb-1 flex-wrap">
                                  <p className={`text-sm font-bold ${textMain}`}>{agency.label}</p>
                                  <span className={`text-xs ${textMuted}`}>({agency.agencyName})</span>
                                  {hasData ? <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600 border border-emerald-200 font-semibold">허가</span>
                                    : <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${isDark ? 'bg-[#4A5568]/20 text-[#8B9BB4]' : 'bg-gray-100 text-gray-600'}`}>허가 정보 없음</span>}
                                  {hasData && rec?.hasUnverifiedDates && <span className="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-600 border border-amber-200 font-semibold" title="일부 허가일자는 공식 확인 전 추정값입니다">일부 일자 추정</span>}
                                </div>
                                {hasData && rec ? (
                                  <div className="flex items-center gap-3 min-w-0">
                                    <span className={`text-xs whitespace-nowrap ${textSub}`}>최초 허가일: {rec.firstApprovalDate || '미상'}</span>
                                    <span className={`text-xs ${textMuted}`}>|</span>
                                    <span className={`text-xs truncate ${textSub}`}>{rec.indicationSummary}</span>
                                  </div>
                                ) : <p className={`text-xs ${textMuted}`}>수집된 허가 데이터 없음 — 미허가 단정 아님</p>}
                              </div>
                              {hasData && (
                                <div className="flex items-center gap-2 flex-shrink-0">
                                  <span className={`text-xs font-medium whitespace-nowrap ${accentColor}`}>{isExpanded ? '접기' : '적응증별 원문'}</span>
                                  <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className={`text-sm transition-transform duration-200 ${isExpanded ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'}`}></i></span>
                                </div>
                              )}
                            </button>
                            {isExpanded && rec && (
                              <div className={`px-5 pb-5 border-t ${divider}`}>
                                <div className={`mt-4 rounded-xl p-4 border ${fullTextBg}`}>
                                  <div className="flex items-center gap-2 mb-3"><span className={`w-4 h-4 flex items-center justify-center ${accentColor}`}><i className="ri-file-check-line text-xs"></i></span><p className={`text-xs font-semibold uppercase tracking-wider ${accentColor}`}>Official Indication / Approval Text — {agency.agencyName} ({rec.indicationBlocks.length})</p></div>
                                  <div className="space-y-4 max-h-[28rem] overflow-y-auto pr-1">
                                    {rec.indicationBlocks.map(block => (
                                      <div key={block.indicationId + (block.approvalDate || '')} className={`pb-4 border-b last:border-0 ${divider}`}>
                                        <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                                          <p className={`text-xs font-bold ${textMain}`}>{block.title}</p>
                                          {block.lineOfTherapy && <span className={`text-xs px-1.5 py-0.5 rounded-full ${pillBg} ${pillText}`}>{block.lineOfTherapy}</span>}
                                          {block.biomarkerLabel && <span className={`text-xs px-1.5 py-0.5 rounded-full ${pillBg} ${pillText}`}>{block.biomarkerLabel}</span>}
                                        </div>
                                        <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                                          <span className={`text-xs ${textMuted}`}>승인: {block.approvalDate || '일자 미상'}</span>
                                          {block.dateSource === 'unverified' && <span className="text-xs px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-600 border border-amber-200">추정</span>}
                                          {block.labelUrl && <a href={block.labelUrl} target="_blank" rel="noreferrer" className={`text-xs font-medium ${accentColor}`}>라벨 원문 ↗</a>}
                                        </div>
                                        <p className={`text-xs leading-relaxed whitespace-pre-line ${textSub}`}>{block.body}</p>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )
            )}
          </div>
        ) : (
          <div className={`rounded-2xl border py-16 text-center ${emptyCenter} ${cardBorder}`}>
            <span className={`w-12 h-12 flex items-center justify-center mx-auto mb-3 ${textMuted}`}><i className="ri-global-line text-4xl"></i></span>
            <p className={`text-sm mb-1 ${textSub}`}>검색 이력에서 제품을 선택하거나</p>
            <p className={`text-xs ${textMuted}`}>신규 검색으로 성분명 또는 제품명을 입력하세요</p>
          </div>
        )}
      </div>
    </div>
  );
}
