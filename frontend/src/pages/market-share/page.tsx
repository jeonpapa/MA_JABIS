import { useEffect, useMemo, useRef, useState } from 'react';
import {
  PieChart, Pie, Cell, ResponsiveContainer,
  XAxis, YAxis, CartesianGrid, Tooltip,
  LineChart, Line,
} from 'recharts';
import {
  searchMarketShare, fetchAtc4, fetchAtc4Trend, fetchBrand,
  downloadMarketShareXlsx, resolveDefaultAtc4,
  buildPieData, buildTrendRows, quarterLabel, formatLcKrw,
  LINE_COLORS,
  type MsSearchHit, type MsAtc4Response, type MsTrendResponse, type MsBrandResponse,
} from '@/api/marketShare';
import { useApi } from '@/hooks/useApi';

type ViewKey = 'donut' | 'unit' | 'revenue';
type ShareSubView = 'snapshot' | 'trend';

const UnitTooltip = ({ active, payload, label, isDark }: any) => {
  if (active && payload && payload.length) {
    const bg = isDark ? 'bg-[#1E2530] border-[#2A3545]' : 'bg-white border-gray-200 shadow-lg';
    const txt = isDark ? 'text-white' : 'text-gray-900';
    const sub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
    return (
      <div className={`${bg} border rounded-xl p-3 min-w-[160px]`}>
        <p className={`text-xs mb-2 ${sub}`}>{label}</p>
        {payload.map((p: any) => (
          <div key={p.dataKey} className="flex items-center justify-between gap-4"><span className="text-xs" style={{ color: p.color }}>{p.dataKey}</span><span className={`text-xs font-bold ${txt}`}>{Number(p.value).toLocaleString()} Units</span></div>
        ))}
      </div>
    );
  }
  return null;
};

const RevenueTooltip = ({ active, payload, label, isDark }: any) => {
  if (active && payload && payload.length) {
    const bg = isDark ? 'bg-[#1E2530] border-[#2A3545]' : 'bg-white border-gray-200 shadow-lg';
    const txt = isDark ? 'text-white' : 'text-gray-900';
    const sub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
    return (
      <div className={`${bg} border rounded-xl p-3 min-w-[160px]`}>
        <p className={`text-xs mb-2 ${sub}`}>{label}</p>
        {payload.map((p: any) => (
          <div key={p.dataKey} className="flex items-center justify-between gap-4"><span className="text-xs" style={{ color: p.color }}>{p.dataKey}</span><span className={`text-xs font-bold ${txt}`}>₩{Number(p.value).toLocaleString()}M</span></div>
        ))}
      </div>
    );
  }
  return null;
};

const ShareTrendTooltip = ({ active, payload, label, isDark }: any) => {
  if (active && payload && payload.length) {
    const bg = isDark ? 'bg-[#1E2530] border-[#2A3545]' : 'bg-white border-gray-200 shadow-lg';
    const txt = isDark ? 'text-white' : 'text-gray-900';
    const sub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
    return (
      <div className={`${bg} border rounded-xl p-3 min-w-[160px]`}>
        <p className={`text-xs mb-2 ${sub}`}>{label}</p>
        {payload.map((p: any) => (
          <div key={p.dataKey} className="flex items-center justify-between gap-4"><span className="text-xs" style={{ color: p.color }}>{p.dataKey}</span><span className={`text-xs font-bold ${txt}`}>{p.value}%</span></div>
        ))}
      </div>
    );
  }
  return null;
};

export default function KoreanMarketPage() {
  const [isDark, setIsDark] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [view, setView] = useState<ViewKey>('donut');
  const [shareSubView, setShareSubView] = useState<ShareSubView>('snapshot');

  // 검색 (서버 검색, debounce)
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<MsSearchHit[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 현재 시장 / 선택 브랜드
  const [atc4Code, setAtc4Code] = useState<string | null>(null);
  const [selectedBrand, setSelectedBrand] = useState<MsBrandResponse | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // 기본 시장: 키트루다의 ATC4 (PD-1/PD-L1) 를 검색으로 해석
  useEffect(() => {
    let on = true;
    resolveDefaultAtc4().then(code => { if (on) setAtc4Code(code); });
    return () => { on = false; };
  }, []);

  const market = useApi<MsAtc4Response | null>(
    () => (atc4Code ? fetchAtc4(atc4Code) : Promise.resolve(null)),
    [atc4Code],
  );
  const trend = useApi<MsTrendResponse | null>(
    () => (atc4Code ? fetchAtc4Trend(atc4Code, 5) : Promise.resolve(null)),
    [atc4Code],
  );

  useEffect(() => { setActiveIndex(0); }, [atc4Code]);

  // ── 어댑터: 실데이터 → readdy 차트 형태 ──────────────────────────────────
  const pieData = useMemo(() => (market.data ? buildPieData(market.data, 5) : []), [market.data]);
  const shareTrendRows = useMemo(() => (trend.data ? buildTrendRows(trend.data, 'share', 6) : []), [trend.data]);
  const unitTrendRows = useMemo(() => (trend.data ? buildTrendRows(trend.data, 'units', 6) : []), [trend.data]);
  const revenueTrendRows = useMemo(() => (trend.data ? buildTrendRows(trend.data, 'revenue', 6) : []), [trend.data]);
  const trendBrands = trend.data?.top_brands ?? [];

  const totalUnits = market.data ? Math.round(market.data.totals.dosage_units) : 0;
  const isLoading = !atc4Code || market.loading || trend.loading;
  const errorMsg = market.error || trend.error || actionError;
  const isEmpty = !isLoading && !market.error && market.data != null && pieData.length === 0;

  // ── Pie active sector (recharts 3.x: activeIndex prop 제거 → state + overlay) ──
  const safeActive = pieData.length > 0 ? Math.min(activeIndex, pieData.length - 1) : -1;
  const activeSlice = safeActive >= 0 ? pieData[safeActive] : null;
  const activeAngles = useMemo(() => {
    if (safeActive < 0) return null;
    const total = pieData.reduce((a, d) => a + d.value, 0);
    if (total <= 0) return null;
    let cum = 0;
    for (let i = 0; i < safeActive; i++) cum += pieData[i].value;
    return {
      start: (cum / total) * 360,
      end: ((cum + pieData[safeActive].value) / total) * 360,
    };
  }, [pieData, safeActive]);

  const handleSearch = (value: string) => {
    setSearchQuery(value);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    if (!value.trim()) {
      setSearchResults([]);
      setShowDropdown(false);
      return;
    }
    searchTimer.current = setTimeout(async () => {
      try {
        setSearchLoading(true);
        const r = await searchMarketShare(value.trim(), 20);
        setSearchResults(r.items);
        setShowDropdown(true);
      } catch {
        setSearchResults([]);
        setShowDropdown(true);
      } finally {
        setSearchLoading(false);
      }
    }, 250);
  };

  const handleSelect = async (hit: MsSearchHit) => {
    setSearchQuery(hit.product_name);
    setShowDropdown(false);
    if (hit.atc4_code !== atc4Code) setAtc4Code(hit.atc4_code);
    try {
      const b = await fetchBrand(hit.product_name, hit.atc4_code);
      setSelectedBrand(b);
    } catch {
      setSelectedBrand(null);
    }
  };

  const handleClear = () => {
    setSearchQuery('');
    setSearchResults([]);
    setShowDropdown(false);
    setSelectedBrand(null);
  };

  const handleDownload = async () => {
    if (!market.data) return;
    setDownloading(true);
    setActionError(null);
    try {
      await downloadMarketShareXlsx(market.data.atc4_code, market.data.quarter, 8);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : '엑셀 다운로드 실패');
    } finally {
      setDownloading(false);
    }
  };

  const pageBg = isDark ? 'bg-[#0D1117]' : 'bg-gray-50';
  const headerBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const cardBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
  const textMuted = isDark ? 'text-[#4A5568]' : 'text-gray-400';
  const accentColor = isDark ? 'text-[#00E5CC]' : 'text-teal-600';
  const accentDot = isDark ? 'bg-[#00E5CC]' : 'bg-teal-600';
  const searchBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const searchFocus = isDark ? 'focus-within:border-[#00E5CC]/50' : 'focus-within:border-teal-300';
  const searchText = isDark ? 'text-white placeholder-[#4A5568]' : 'text-gray-900 placeholder-gray-400';
  const tabBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-gray-100 border-gray-200';
  const tabActive = isDark ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'bg-teal-600 text-white';
  const tabInactive = isDark ? 'text-[#8B9BB4] hover:text-white' : 'text-gray-500 hover:text-gray-900';
  const subtabBg = isDark ? 'bg-[#0D1117]' : 'bg-gray-100';
  const subtabActive = isDark ? 'bg-[#1E2530] text-[#00E5CC]' : 'bg-white text-teal-600 shadow-sm';
  const subtabInactive = isDark ? 'text-[#8B9BB4] hover:text-white' : 'text-gray-500 hover:text-gray-700';
  const dropdownBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200 shadow-lg';
  const dropdownHover = isDark ? 'hover:bg-[#1E2530]' : 'hover:bg-gray-100';
  const detailBg = isDark ? 'bg-[#161B27] border-[#00E5CC]/30' : 'bg-white border-teal-300';
  const statBg = isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-50 border-gray-200';
  const sumBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const barTrack = isDark ? 'bg-[#1E2530]' : 'bg-gray-200';
  const gridStroke = isDark ? '#1E2530' : '#E5E7EB';
  const tickFill = isDark ? '#8B9BB4' : '#6B7280';
  const chartBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const listActive = isDark ? 'border-[#00E5CC]/50 bg-[#00E5CC]/5' : 'border-teal-300 bg-teal-50';
  const listDefault = isDark ? 'border-[#1E2530] hover:border-[#2A3545]' : 'border-gray-200 hover:border-gray-300';

  const trendRangeLabel = (rows: { quarter: string }[]) =>
    rows.length > 0 ? `${rows[0].quarter} ~ ${rows[rows.length - 1].quarter}` : '';

  const selectedColor = selectedBrand
    ? (pieData.find(d => d.name === selectedBrand.product_name)?.color ?? (isDark ? '#00E5CC' : '#0D9488'))
    : undefined;

  return (
    <div className={`min-h-screen ${pageBg} ${isDark ? 'text-white' : 'text-gray-900'}`}>
      <div className={`px-8 pt-8 pb-6 border-b ${headerBorder}`}>
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-pie-chart-2-line"></i></span>
              <h1 className={`text-2xl font-bold ${textMain}`}>Korean Market</h1>
            </div>
            <p className={`${textSub} text-sm`}>
              국내 시장 점유율 현황 및 매출 추이 분석
              {market.data && <span className={textMuted}> · {market.data.atc4_desc} (ATC4 {market.data.atc4_code})</span>}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={handleDownload} disabled={downloading || !market.data}
              className={`flex items-center gap-2 border text-sm font-medium px-4 py-2 rounded-lg cursor-pointer whitespace-nowrap transition-all disabled:opacity-50 disabled:cursor-not-allowed ${isDark ? 'bg-[#161B27] border-[#1E2530] text-[#8B9BB4] hover:text-white hover:border-[#2A3545]' : 'bg-white border-gray-200 text-gray-500 hover:text-gray-900 hover:border-gray-300'}`}>
              <span className="w-4 h-4 flex items-center justify-center">
                <i className={downloading ? 'ri-loader-4-line text-sm animate-spin' : 'ri-file-excel-2-line text-sm'}></i>
              </span>{downloading ? '다운로드 중…' : '엑셀 다운로드'}
            </button>
            <div className={`flex items-center gap-1 rounded-lg p-1 ${tabBg}`}>
              {[
                { key: 'donut', label: 'Market Share', icon: 'ri-pie-chart-2-line' },
                { key: 'unit', label: 'Unit Trend', icon: 'ri-bar-chart-grouped-line' },
                { key: 'revenue', label: 'Revenue Trend', icon: 'ri-line-chart-line' },
              ].map(tab => (
                <button key={tab.key} onClick={() => setView(tab.key as ViewKey)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${view === tab.key ? tabActive : tabInactive}`}>
                  <span className="w-3.5 h-3.5 flex items-center justify-center"><i className={`${tab.icon} text-xs`}></i></span>{tab.label}
                </button>
              ))}
            </div>
            <button onClick={() => setIsDark(!isDark)}
              className={`w-9 h-9 flex items-center justify-center rounded-lg cursor-pointer transition-all ${isDark ? 'bg-[#1E2530] text-amber-400 hover:bg-[#2A3545]' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'}`}
              title={isDark ? '라이트 모드' : '다크 모드'}>
              <i className={isDark ? 'ri-sun-line text-lg' : 'ri-moon-line text-lg'}></i>
            </button>
          </div>
        </div>

        <div className="mt-5 relative">
          <div className={`flex items-center gap-3 rounded-xl px-4 py-3 transition-colors border ${searchBg} ${searchFocus}`}>
            <span className={`w-5 h-5 flex items-center justify-center flex-shrink-0 ${textSub}`}><i className="ri-search-line text-base"></i></span>
            <input type="text" placeholder="제품명 또는 성분명으로 검색... (예: Keytruda, Pembrolizumab)"
              value={searchQuery} onChange={e => handleSearch(e.target.value)} onFocus={() => searchQuery && setShowDropdown(true)}
              className={`flex-1 bg-transparent text-sm focus:outline-none ${searchText}`} />
            {searchLoading && <span className={`text-xs ${textMuted}`}>…</span>}
            {searchQuery && <button onClick={handleClear} className={`w-5 h-5 flex items-center justify-center cursor-pointer transition-colors ${textMuted} hover:${textMain}`}><i className="ri-close-line text-sm"></i></button>}
          </div>
          {showDropdown && searchResults.length > 0 && (
            <div className={`absolute top-full left-0 right-0 mt-1 rounded-xl overflow-hidden z-50 border max-h-80 overflow-y-auto ${dropdownBg}`}>
              {searchResults.map(hit => (
                <button key={`${hit.product_name}|${hit.atc4_code}|${hit.mfr_name}`} onClick={() => handleSelect(hit)}
                  className={`w-full flex items-center gap-3 px-4 py-3 transition-colors cursor-pointer text-left ${dropdownHover}`}>
                  <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${accentDot}`}></span>
                  <div className="flex-1 min-w-0">
                    <span className={`text-sm font-semibold ${textMain}`}>{hit.product_name}</span>
                    <span className={`text-xs ml-2 ${textSub}`}>{hit.molecule_desc}</span>
                    <p className={`text-[10px] mt-0.5 ${textMuted}`}>{hit.mfr_name} · {hit.atc4_desc}</p>
                  </div>
                  <span className={`text-xs font-bold whitespace-nowrap ${accentColor}`}>₩{formatLcKrw(hit.values_lc)}M</span>
                </button>
              ))}
            </div>
          )}
          {showDropdown && !searchLoading && searchResults.length === 0 && searchQuery && (
            <div className={`absolute top-full left-0 right-0 mt-1 rounded-xl px-4 py-4 z-50 border ${dropdownBg}`}><p className={`text-sm text-center ${textMuted}`}>검색 결과가 없습니다</p></div>
          )}
        </div>
      </div>

      <div className="px-8 py-6 space-y-5">
        {errorMsg && (
          <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-sm rounded-xl p-4 flex items-center justify-between">
            <span>{errorMsg}</span>
            <button onClick={() => { setActionError(null); market.reload(); trend.reload(); }} className="text-xs underline cursor-pointer">다시 시도</button>
          </div>
        )}

        {isLoading && (
          <div className={`rounded-2xl border p-10 text-center ${chartBg}`}>
            <p className={`text-sm animate-pulse ${textSub}`}>시장 데이터 로드 중…</p>
          </div>
        )}

        {isEmpty && (
          <div className={`rounded-2xl border p-10 text-center ${chartBg}`}>
            <p className={`text-sm ${textMain}`}>해당 시장의 분기 데이터가 없습니다</p>
            <p className={`text-xs mt-1 ${textSub}`}>ATC4 {market.data?.atc4_code} · {market.data?.atc4_desc} — 다른 제품을 검색해 보세요</p>
          </div>
        )}

        {selectedBrand && (
          <div className={`rounded-2xl border p-5 ${detailBg}`}>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <span className="w-3 h-3 rounded-full" style={{ backgroundColor: selectedColor }}></span>
                <h3 className={`font-bold text-lg ${textMain}`}>{selectedBrand.product_name}</h3>
                <span className={`text-sm ${textSub}`}>({selectedBrand.molecule_desc})</span>
                <span className={`text-xs px-2 py-0.5 rounded-full ${isDark ? 'bg-[#00E5CC]/10 text-[#00E5CC]' : 'bg-teal-100 text-teal-700'}`}>{selectedBrand.atc4_desc}</span>
              </div>
              <button onClick={handleClear} className={`cursor-pointer transition-colors ${textMuted} hover:${textMain}`}><i className="ri-close-line text-lg"></i></button>
            </div>
            <div className="grid grid-cols-4 gap-4">
              {[
                { label: '제조사', value: selectedBrand.mfr_name || '정보 없음', icon: 'ri-building-2-line' },
                { label: `시장 점유율 (${quarterLabel(selectedBrand.quarter)})`, value: `${selectedBrand.market_share_pct.toFixed(1)}%`, icon: 'ri-pie-chart-2-line', highlight: true },
                { label: `분기 매출 (${quarterLabel(selectedBrand.quarter)})`, value: `₩${formatLcKrw(selectedBrand.quarterly.length > 0 ? selectedBrand.quarterly[selectedBrand.quarterly.length - 1].values_lc : 0)}M`, icon: 'ri-line-chart-line' },
                { label: 'ATC4 시장 순위', value: selectedBrand.market_rank ? `#${selectedBrand.market_rank}` : '정보 없음', icon: 'ri-trophy-line' },
              ].map(item => (
                <div key={item.label} className={`rounded-xl p-4 border ${statBg}`}>
                  <div className="flex items-center gap-2 mb-2"><span className={`w-4 h-4 flex items-center justify-center ${textSub}`}><i className={`${item.icon} text-sm`}></i></span><span className={`text-xs ${textSub}`}>{item.label}</span></div>
                  <p className={`text-base font-bold ${item.highlight ? accentColor : textMain}`}>{item.value}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        {!isLoading && pieData.length > 0 && (
          <div className="grid grid-cols-6 gap-3">
            {pieData.map(item => (
              <div key={item.name} className={`rounded-xl p-4 border ${sumBg}`}>
                <div className="flex items-center gap-2 mb-2"><span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: item.color }}></span><span className={`text-xs truncate ${textSub}`}>{item.name}</span></div>
                <p className="text-xl font-bold" style={{ color: item.color }}>{item.value}%</p>
                <p className={`text-xs mt-1 ${textMuted}`}>{item.units.toLocaleString()} Units</p>
              </div>
            ))}
          </div>
        )}

        {!isLoading && pieData.length > 0 && (
          <div className={`rounded-2xl p-6 border ${chartBg}`}>
            {view === 'donut' && (
              <div>
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h3 className={`font-bold text-base mb-1 ${textMain}`}>
                      {shareSubView === 'snapshot' ? 'Market Share Distribution' : 'Market Share Trend'}
                    </h3>
                    <p className={`text-xs ${textSub}`}>
                      {shareSubView === 'snapshot'
                        ? `${market.data ? quarterLabel(market.data.quarter) : ''} · 전체 시장 ${totalUnits.toLocaleString()} Units 기준 (Values LC)`
                        : `분기별 시장 점유율 변화 추이 · ${trendRangeLabel(shareTrendRows)}`}
                    </p>
                  </div>
                  <div className={`flex items-center gap-1 rounded-lg p-1 ${subtabBg}`}>
                    {[
                      { key: 'snapshot', label: 'Snapshot', icon: 'ri-pie-chart-2-line' },
                      { key: 'trend', label: 'Trend', icon: 'ri-line-chart-line' },
                    ].map(stab => (
                      <button key={stab.key} onClick={() => { setShareSubView(stab.key as ShareSubView); setActiveIndex(0); }}
                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${shareSubView === stab.key ? subtabActive : subtabInactive}`}>
                        <span className="w-3.5 h-3.5 flex items-center justify-center"><i className={`${stab.icon} text-xs`}></i></span>{stab.label}
                      </button>
                    ))}
                  </div>
                </div>

                {shareSubView === 'snapshot' && (
                  <div className="grid grid-cols-2 gap-8 items-center">
                    <div className="relative">
                      <ResponsiveContainer width="100%" height={320}>
                        <PieChart>
                          <Pie
                            data={pieData} cx="50%" cy="50%" startAngle={0} endAngle={360}
                            innerRadius={80} outerRadius={130} dataKey="value"
                            isAnimationActive={false}
                            onMouseEnter={(_, index) => setActiveIndex(index)}>
                            {pieData.map((entry, index) => (<Cell key={`cell-${index}`} fill={entry.color} stroke="transparent" />))}
                          </Pie>
                          {/* recharts 3.x 는 Pie activeIndex prop 미지원 — 활성 섹터는 state 기반 overlay Pie 로 렌더 */}
                          {activeSlice && activeAngles && (
                            <Pie
                              data={[{ name: activeSlice.name, value: 1 }]} dataKey="value"
                              cx="50%" cy="50%" startAngle={activeAngles.start} endAngle={activeAngles.end}
                              innerRadius={80} outerRadius={138}
                              fill={activeSlice.color} stroke="transparent"
                              isAnimationActive={false} pointerEvents="none" />
                          )}
                          {activeSlice && activeAngles && (
                            <Pie
                              data={[{ name: activeSlice.name, value: 1 }]} dataKey="value"
                              cx="50%" cy="50%" startAngle={activeAngles.start} endAngle={activeAngles.end}
                              innerRadius={142} outerRadius={146}
                              fill={activeSlice.color} stroke="transparent"
                              isAnimationActive={false} pointerEvents="none" />
                          )}
                        </PieChart>
                      </ResponsiveContainer>
                      {activeSlice && (
                        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                          <div className="text-center max-w-[150px]">
                            <p className={`text-[13px] font-bold truncate ${textMain}`}>{activeSlice.name}</p>
                            <p className={`text-xl font-bold ${accentColor}`}>{activeSlice.value}%</p>
                            <p className={`text-[11px] ${textSub}`}>{activeSlice.units.toLocaleString()} Units</p>
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="space-y-3">
                      <h3 className={`font-bold text-base mb-4 ${textMain}`}>제품별 점유율 상세</h3>
                      {pieData.map((item, idx) => (
                        <div key={item.name} className={`p-3 rounded-xl border cursor-pointer transition-all ${safeActive === idx ? listActive : listDefault}`}
                          onMouseEnter={() => setActiveIndex(idx)}>
                          <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center gap-2 min-w-0"><span className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: item.color }}></span><span className={`text-sm font-semibold truncate ${textMain}`}>{item.name}</span>{item.mfrName && <span className={`text-[10px] truncate ${textMuted}`}>{item.mfrName}</span>}</div>
                            <div className="flex items-center gap-3 flex-shrink-0">
                              <span className={`text-xs ${textMuted}`}>{item.units.toLocaleString()} Units</span>
                              <span className={`text-sm font-bold ${textMain}`}>{item.value}%</span>
                            </div>
                          </div>
                          <div className={`w-full h-1.5 rounded-full overflow-hidden ${barTrack}`}><div className="h-full rounded-full transition-all duration-500" style={{ width: `${Math.min(item.value, 100)}%`, backgroundColor: item.color }}></div></div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {shareSubView === 'trend' && (
                  <ResponsiveContainer width="100%" height={360}>
                    <LineChart data={shareTrendRows} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
                      <XAxis dataKey="quarter" tick={{ fill: tickFill, fontSize: 11 }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fill: tickFill, fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={v => `${v}%`} domain={[0, 'auto']} />
                      <Tooltip content={<ShareTrendTooltip isDark={isDark} />} />
                      {trendBrands.map((brand, idx) => (
                        <Line key={brand} type="monotone" dataKey={brand} stroke={LINE_COLORS[idx % LINE_COLORS.length]} strokeWidth={2} dot={{ fill: LINE_COLORS[idx % LINE_COLORS.length], r: 4, strokeWidth: 0 }} activeDot={{ r: 6, strokeWidth: 0 }} />
                      ))}
                    </LineChart>
                  </ResponsiveContainer>
                )}
              </div>
            )}

            {view === 'unit' && (
              <div>
                <div className="flex items-center justify-between mb-4">
                  <div><h3 className={`font-bold text-base mb-1 ${textMain}`}>Unit Trend — Prescription Volume (Units)</h3><p className={`text-xs ${textSub}`}>분기별 실제 처방 유닛 수량 추이 · {trendRangeLabel(unitTrendRows)}</p></div>
                </div>
                <ResponsiveContainer width="100%" height={320}>
                  <LineChart data={unitTrendRows} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
                    <XAxis dataKey="quarter" tick={{ fill: tickFill, fontSize: 11 }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fill: tickFill, fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={v => `${(v / 1000).toFixed(1)}k`} domain={[0, 'auto']} />
                    <Tooltip content={<UnitTooltip isDark={isDark} />} />
                    {trendBrands.map((brand, idx) => (
                      <Line key={brand} type="monotone" dataKey={brand} stroke={LINE_COLORS[idx % LINE_COLORS.length]} strokeWidth={2} dot={{ fill: LINE_COLORS[idx % LINE_COLORS.length], r: 4, strokeWidth: 0 }} activeDot={{ r: 6, strokeWidth: 0 }} />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}

            {view === 'revenue' && (
              <div>
                <div className="flex items-center justify-between mb-4">
                  <div><h3 className={`font-bold text-base mb-1 ${textMain}`}>Revenue Trend — Sales (M KRW)</h3><p className={`text-xs ${textSub}`}>분기별 매출액 추이 (Values LC, 단위: 백만원) · {trendRangeLabel(revenueTrendRows)}</p></div>
                </div>
                <ResponsiveContainer width="100%" height={320}>
                  <LineChart data={revenueTrendRows} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
                    <XAxis dataKey="quarter" tick={{ fill: tickFill, fontSize: 11 }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fill: tickFill, fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={v => `${v.toLocaleString()}`} />
                    <Tooltip content={<RevenueTooltip isDark={isDark} />} />
                    {trendBrands.map((brand, idx) => (
                      <Line key={brand} type="monotone" dataKey={brand} stroke={LINE_COLORS[idx % LINE_COLORS.length]} strokeWidth={2} dot={{ fill: LINE_COLORS[idx % LINE_COLORS.length], r: 4, strokeWidth: 0 }} activeDot={{ r: 6, strokeWidth: 0 }} />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
