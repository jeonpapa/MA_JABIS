import { useEffect, useMemo, useRef, useState } from 'react';
import {
  PieChart, Pie, Cell, ResponsiveContainer, Sector,
  XAxis, YAxis, CartesianGrid, Legend, Tooltip,
  LineChart, Line, BarChart, Bar,
} from 'recharts';
import {
  searchMarketShare, fetchAtc4, fetchAtc4Trend, fetchBrand,
  downloadMarketShareXlsx,
  quarterLabel, formatLcKrw,
  type MsSearchHit, type MsAtc4Response, type MsTrendResponse, type MsBrandResponse,
} from '@/api/marketShare';
import { ApiError } from '@/api/client';

const CHART_COLORS = ['#00E5CC', '#7C3AED', '#F59E0B', '#EF4444', '#10B981', '#06B6D4', '#EC4899'];
const DEFAULT_ATC4 = 'L01G5';

type ViewKey = 'donut' | 'unit' | 'revenue';
type ChartShape = 'line' | 'bar';
type ChartTheme = 'dark' | 'light';

const renderActiveShape = (props: any) => {
  const { cx, cy, innerRadius, outerRadius, startAngle, endAngle, fill, payload, percent } = props;
  return (
    <g>
      <text x={cx} y={cy - 12} textAnchor="middle" fill="#fff" fontSize={13} fontWeight="bold">{payload.name}</text>
      <text x={cx} y={cy + 12} textAnchor="middle" fill="#00E5CC" fontSize={20} fontWeight="bold">{`${payload.value.toFixed(1)}%`}</text>
      <text x={cx} y={cy + 32} textAnchor="middle" fill="#8B9BB4" fontSize={11}>{`(${(percent * 100).toFixed(1)}%)`}</text>
      <Sector cx={cx} cy={cy} innerRadius={innerRadius} outerRadius={outerRadius + 8} startAngle={startAngle} endAngle={endAngle} fill={fill} />
      <Sector cx={cx} cy={cy} innerRadius={outerRadius + 12} outerRadius={outerRadius + 16} startAngle={startAngle} endAngle={endAngle} fill={fill} />
    </g>
  );
};

const UnitTooltip = ({ active, payload, label }: any) => {
  if (active && payload && payload.length) {
    return (
      <div className="bg-[#1E2530] border border-[#2A3545] rounded-xl p-3 min-w-[160px]">
        <p className="text-[#8B9BB4] text-xs mb-2">{label}</p>
        {payload.map((p: any) => (
          <div key={p.dataKey} className="flex items-center justify-between gap-4">
            <span className="text-xs" style={{ color: p.color }}>{p.dataKey}</span>
            <span className="text-white text-xs font-bold">{Number(p.value).toFixed(1)}%</span>
          </div>
        ))}
      </div>
    );
  }
  return null;
};

const RevenueTooltip = ({ active, payload, label }: any) => {
  if (active && payload && payload.length) {
    return (
      <div className="bg-[#1E2530] border border-[#2A3545] rounded-xl p-3 min-w-[200px]">
        <p className="text-[#8B9BB4] text-xs mb-2">{label}</p>
        {payload.map((p: any) => (
          <div key={p.dataKey} className="flex items-center justify-between gap-4">
            <span className="text-xs" style={{ color: p.color }}>{p.dataKey}</span>
            <span className="text-white text-xs font-bold">₩{formatLcKrw(p.value)}M</span>
          </div>
        ))}
      </div>
    );
  }
  return null;
};

export default function KoreanMarketPage() {
  const [activeIndex, setActiveIndex] = useState(0);
  const [view, setView] = useState<ViewKey>('donut');

  // 그래프 컨트롤
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [rangeStart, setRangeStart] = useState<string | null>(null);
  const [rangeEnd, setRangeEnd] = useState<string | null>(null);
  const [chartShape, setChartShape] = useState<ChartShape>('line');
  const [chartTheme, setChartTheme] = useState<ChartTheme>('dark');
  const [downloading, setDownloading] = useState(false);

  // 검색
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<MsSearchHit[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [searchLoading, setSearchLoading] = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 현재 시장 / 선택 브랜드
  const [atc4Code, setAtc4Code] = useState<string>(DEFAULT_ATC4);
  const [atc4Data, setAtc4Data] = useState<MsAtc4Response | null>(null);
  const [trend, setTrend] = useState<MsTrendResponse | null>(null);
  const [selectedBrand, setSelectedBrand] = useState<MsBrandResponse | null>(null);
  const [quarterSel, setQuarterSel] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ATC4 + 트렌드 로드
  useEffect(() => {
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const d = await fetchAtc4(atc4Code, quarterSel ?? undefined);
        setAtc4Data(d);
        if (!quarterSel) setQuarterSel(d.quarter);
        const t = await fetchAtc4Trend(atc4Code, 6);
        setTrend(t);
        setExcluded(new Set());  // 시장 바뀌면 제외 목록 초기화
      } catch (err) {
        setError(err instanceof ApiError ? err.message : '시장 데이터 조회 실패');
        setAtc4Data(null);
        setTrend(null);
      } finally {
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [atc4Code]);

  // 분기 전환 시 ATC4 다시 조회 (트렌드는 전체 분기 미리 받음)
  useEffect(() => {
    if (!quarterSel) return;
    (async () => {
      try {
        const d = await fetchAtc4(atc4Code, quarterSel);
        setAtc4Data(d);
      } catch {/* 무시 */}
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [quarterSel]);

  // 검색 debounce
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
      } finally {
        setSearchLoading(false);
      }
    }, 250);
  };

  const handleSelectHit = async (hit: MsSearchHit) => {
    setSearchQuery(hit.product_name);
    setShowDropdown(false);
    setAtc4Code(hit.atc4_code);
    setQuarterSel(null);
    try {
      const b = await fetchBrand(hit.product_name, hit.atc4_code);
      setSelectedBrand(b);
    } catch {
      setSelectedBrand(null);
    }
  };

  const handleClearSelection = () => {
    setSearchQuery('');
    setSearchResults([]);
    setShowDropdown(false);
    setSelectedBrand(null);
  };

  const toggleExclude = (name: string) => {
    setExcluded(prev => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name); else next.add(name);
      return next;
    });
  };

  // 도넛: atc4 products (상위 6 중 제외 제품 제거 + 기타 합계로 재정규화)
  const donutData = useMemo(() => {
    if (!atc4Data) return [];
    const included = atc4Data.products.filter(p => !excluded.has(p.product_name));
    if (included.length === 0) return [];
    const subtotalIncluded = included.reduce((a, p) => a + p.values_share_pct, 0);
    const top = included.slice(0, 6);
    const rest = included.slice(6);
    const normalize = (v: number) => (subtotalIncluded > 0 ? v / subtotalIncluded * 100.0 : 0);
    const items = top.map((p, i) => ({
      name: p.product_name,
      value: Number(normalize(p.values_share_pct).toFixed(2)),
      color: CHART_COLORS[i % CHART_COLORS.length],
    }));
    if (rest.length > 0) {
      const otherPct = rest.reduce((a, p) => a + p.values_share_pct, 0);
      if (otherPct > 0.1) {
        items.push({
          name: `기타 (${rest.length})`,
          value: Number(normalize(otherPct).toFixed(2)),
          color: '#4A5568',
        });
      }
    }
    return items;
  }, [atc4Data, excluded]);

  // trend 분기 리스트 로드 시 기본 범위를 최근 6분기로 초기화
  useEffect(() => {
    if (!trend || trend.quarters.length === 0) return;
    if (rangeStart && rangeEnd) return;
    const qs = trend.quarters;
    const defaultStart = qs[Math.max(0, qs.length - 6)];
    const defaultEnd = qs[qs.length - 1];
    setRangeStart(defaultStart);
    setRangeEnd(defaultEnd);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trend]);

  // 트렌드 대상 분기 (범위 필터)
  const visibleQuarters = useMemo(() => {
    if (!trend) return [] as string[];
    if (!rangeStart || !rangeEnd) return trend.quarters.slice(-6);
    const [s, e] = rangeStart <= rangeEnd ? [rangeStart, rangeEnd] : [rangeEnd, rangeStart];
    return trend.quarters.filter(q => q >= s && q <= e);
  }, [trend, rangeStart, rangeEnd]);

  // 표시 대상 브랜드 (excluded 제외)
  const visibleBrands = useMemo(() => {
    if (!trend) return [] as string[];
    return trend.top_brands.filter(b => !excluded.has(b));
  }, [trend, excluded]);

  // 트렌드 차트 데이터
  const trendChartData = useMemo(() => {
    if (!trend) return [];
    return visibleQuarters.map(q => {
      const row: Record<string, any> = { quarter: quarterLabel(q) };
      visibleBrands.forEach(b => {
        if (view === 'unit') {
          row[b] = Number((trend.series[b]?.units_share?.[q] ?? 0).toFixed(2));
        } else if (view === 'revenue') {
          row[b] = Math.round((trend.series[b]?.values?.[q] ?? 0) / 1_000_000);
        }
      });
      return row;
    });
  }, [trend, view, visibleQuarters, visibleBrands]);

  const downloadXlsx = async () => {
    if (!atc4Data) return;
    setDownloading(true);
    try {
      const q = quarterSel || atc4Data.quarter;
      await downloadMarketShareXlsx(atc4Data.atc4_code, q, 8);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : (err instanceof Error ? err.message : '다운로드 실패'));
    } finally {
      setDownloading(false);
    }
  };

  // 테마별 색상 — light 선택 시 차트 카드 전체(배경/경계/제목/부제)까지 전환
  const themeVars = chartTheme === 'light'
    ? {
        bg: '#FFFFFF', panel: '#F8FAFC', grid: '#E2E8F0',
        axisTick: '#475569', legend: '#1E293B',
        cardBg: 'bg-white', cardBorder: 'border-[#E2E8F0]',
        titleText: 'text-[#0F172A]', subText: 'text-[#475569]',
      }
    : {
        bg: '#161B27', panel: '#161B27', grid: '#1E2530',
        axisTick: '#8B9BB4', legend: '#8B9BB4',
        cardBg: 'bg-[#161B27]', cardBorder: 'border-[#1E2530]',
        titleText: 'text-white', subText: 'text-[#8B9BB4]',
      };

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      {/* Header */}
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-5 h-5 flex items-center justify-center"><i className="ri-pie-chart-2-line text-[#00E5CC]"></i></span>
              <h1 className="text-2xl font-bold text-white">Korean Market</h1>
            </div>
            <p className="text-[#8B9BB4] text-sm">
              {atc4Data ? `${atc4Data.atc4_desc} · ATC4 ${atc4Data.atc4_code}` : '국내 시장 점유율 (IQVIA NSA-E)'}
            </p>
          </div>

          <div className="flex items-center gap-3">
            {/* Quarter selector */}
            {atc4Data && (
              <select
                value={quarterSel ?? atc4Data.quarter}
                onChange={e => setQuarterSel(e.target.value)}
                className="bg-[#161B27] border border-[#1E2530] text-[#8B9BB4] hover:text-white hover:border-[#2A3545] text-sm px-3 py-2 rounded-lg cursor-pointer"
              >
                {atc4Data.quarters.map(q => (
                  <option key={q} value={q}>{quarterLabel(q)}</option>
                ))}
              </select>
            )}

            <button
              onClick={downloadXlsx}
              disabled={downloading || !atc4Data}
              className="flex items-center gap-2 bg-[#161B27] border border-[#1E2530] text-[#8B9BB4] hover:text-white hover:border-[#2A3545] text-sm font-medium px-4 py-2 rounded-lg cursor-pointer whitespace-nowrap transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              title="Market Share + Unit Trend + Revenue Trend 3개 시트로 다운로드"
            >
              <span className="w-4 h-4 flex items-center justify-center">
                {downloading
                  ? <i className="ri-loader-4-line text-sm animate-spin"></i>
                  : <i className="ri-file-excel-2-line text-sm"></i>}
              </span>
              {downloading ? '다운로드 중…' : '엑셀 다운로드'}
            </button>

            <div className="flex items-center gap-1 bg-[#161B27] border border-[#1E2530] rounded-lg p-1">
              {[
                { key: 'donut', label: 'Market Share', icon: 'ri-pie-chart-2-line' },
                { key: 'unit', label: 'Unit Trend', icon: 'ri-bar-chart-grouped-line' },
                { key: 'revenue', label: 'Revenue Trend', icon: 'ri-line-chart-line' },
              ].map(tab => (
                <button
                  key={tab.key}
                  onClick={() => setView(tab.key as ViewKey)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium cursor-pointer whitespace-nowrap transition-all ${
                    view === tab.key ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'text-[#8B9BB4] hover:text-white'
                  }`}
                >
                  <span className="w-3.5 h-3.5 flex items-center justify-center"><i className={`${tab.icon} text-xs`}></i></span>
                  {tab.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Search Bar */}
        <div className="mt-5 relative">
          <div className="flex items-center gap-3 bg-[#161B27] border border-[#1E2530] rounded-xl px-4 py-3 focus-within:border-[#00E5CC]/50 transition-colors">
            <span className="w-5 h-5 flex items-center justify-center text-[#8B9BB4] flex-shrink-0">
              <i className="ri-search-line text-base"></i>
            </span>
            <input
              type="text"
              placeholder="제품명 또는 성분명으로 검색... (예: KEYTRUDA, PEMBROLIZUMAB)"
              value={searchQuery}
              onChange={e => handleSearch(e.target.value)}
              onFocus={() => searchQuery && setShowDropdown(true)}
              className="flex-1 bg-transparent text-white text-sm placeholder-[#4A5568] focus:outline-none"
            />
            {searchLoading && <span className="text-[#4A5568] text-xs">…</span>}
            {searchQuery && (
              <button onClick={handleClearSelection} className="w-5 h-5 flex items-center justify-center text-[#4A5568] hover:text-white cursor-pointer transition-colors">
                <i className="ri-close-line text-sm"></i>
              </button>
            )}
          </div>

          {showDropdown && searchResults.length > 0 && (
            <div className="absolute top-full left-0 right-0 mt-1 bg-[#161B27] border border-[#1E2530] rounded-xl overflow-hidden z-50 max-h-80 overflow-y-auto">
              {searchResults.map(hit => (
                <button
                  key={`${hit.product_name}|${hit.atc4_code}|${hit.mfr_name}`}
                  onClick={() => handleSelectHit(hit)}
                  className="w-full flex items-center gap-3 px-4 py-3 hover:bg-[#1E2530] transition-colors cursor-pointer text-left border-b border-[#1E2530] last:border-b-0"
                >
                  <span className="w-2.5 h-2.5 rounded-full flex-shrink-0 bg-[#00E5CC]"></span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline gap-2">
                      <span className="text-white text-sm font-semibold">{hit.product_name}</span>
                      <span className="text-[#8B9BB4] text-xs">{hit.molecule_desc}</span>
                    </div>
                    <p className="text-[#4A5568] text-[10px] mt-0.5">{hit.mfr_name} · {hit.atc4_desc}</p>
                  </div>
                  <span className="text-[#00E5CC] text-xs font-bold whitespace-nowrap">
                    ₩{formatLcKrw(hit.values_lc)}M
                  </span>
                </button>
              ))}
            </div>
          )}

          {showDropdown && !searchLoading && searchResults.length === 0 && searchQuery && (
            <div className="absolute top-full left-0 right-0 mt-1 bg-[#161B27] border border-[#1E2530] rounded-xl px-4 py-4 z-50">
              <p className="text-[#4A5568] text-sm text-center">검색 결과가 없습니다</p>
            </div>
          )}
        </div>
      </div>

      <div className="px-8 py-6 space-y-5">
        {loading && !atc4Data && <p className="text-[#8B9BB4] text-sm">시장 데이터 로드 중…</p>}
        {error && <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-sm rounded-xl p-4">{error}</div>}

        {/* 선택 브랜드 상세 카드 */}
        {selectedBrand && atc4Data && (
          <div className="bg-[#161B27] rounded-2xl border border-[#00E5CC]/30 p-5">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <span className="w-3 h-3 rounded-full bg-[#00E5CC]"></span>
                <h3 className="text-white font-bold text-lg">{selectedBrand.product_name}</h3>
                <span className="text-[#8B9BB4] text-sm">({selectedBrand.molecule_desc})</span>
                <span className="bg-[#00E5CC]/10 text-[#00E5CC] text-xs px-2 py-0.5 rounded-full">{selectedBrand.atc4_desc}</span>
              </div>
              <button onClick={handleClearSelection} className="text-[#4A5568] hover:text-white cursor-pointer transition-colors">
                <i className="ri-close-line text-lg"></i>
              </button>
            </div>
            <div className="grid grid-cols-4 gap-4">
              {[
                { label: '제조사', value: selectedBrand.mfr_name, icon: 'ri-building-2-line' },
                { label: `시장 점유율 (${quarterLabel(selectedBrand.quarter)})`, value: `${selectedBrand.market_share_pct.toFixed(1)}%`, icon: 'ri-pie-chart-2-line', highlight: true },
                { label: '매출 (LC)', value: `₩${formatLcKrw(selectedBrand.quarterly.at(-1)?.values_lc ?? 0)}M`, icon: 'ri-line-chart-line' },
                { label: 'ATC4 순위', value: selectedBrand.market_rank ? `#${selectedBrand.market_rank}` : '—', icon: 'ri-trophy-line' },
              ].map(item => (
                <div key={item.label} className="bg-[#0D1117] rounded-xl p-4 border border-[#1E2530]">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="w-4 h-4 flex items-center justify-center text-[#8B9BB4]">
                      <i className={`${item.icon} text-sm`}></i>
                    </span>
                    <span className="text-[#8B9BB4] text-xs">{item.label}</span>
                  </div>
                  <p className={`text-base font-bold ${item.highlight ? 'text-[#00E5CC]' : 'text-white'}`}>{item.value}</p>
                </div>
              ))}
            </div>
            {selectedBrand.packs.length > 1 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {selectedBrand.packs.map(pk => (
                  <span key={pk.product_id} className="text-xs px-2 py-1 rounded bg-[#0D1117] border border-[#1E2530] text-[#8B9BB4]">
                    {pk.pack_desc} {pk.pack_launch_date && <span className="text-[#4A5568]">· {pk.pack_launch_date}</span>}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Summary Row: top-N in ATC4 */}
        {atc4Data && (
          <div className="grid grid-cols-6 gap-3">
            {atc4Data.products.slice(0, 6).map((p, idx) => (
              <div key={p.product_name} className="bg-[#161B27] rounded-xl p-4 border border-[#1E2530]">
                <div className="flex items-center gap-2 mb-2">
                  <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: CHART_COLORS[idx % CHART_COLORS.length] }}></span>
                  <span className="text-[#8B9BB4] text-xs truncate">{p.product_name}</span>
                </div>
                <p className="text-xl font-bold" style={{ color: CHART_COLORS[idx % CHART_COLORS.length] }}>
                  {p.values_share_pct.toFixed(1)}%
                </p>
              </div>
            ))}
          </div>
        )}

        {/* Chart Controls — donut 뷰에서는 shape 토글/분기 범위 숨김 (donut 은 헤더 분기만 사용) */}
        {atc4Data && view !== 'donut' && (
          <div className="flex flex-wrap items-center gap-3 bg-[#161B27] border border-[#1E2530] rounded-2xl px-4 py-3">
            {trend && trend.quarters.length > 0 && (
              <div className="flex items-center gap-2">
                <span className="text-[#8B9BB4] text-xs">반영기간</span>
                <select
                  value={rangeStart ?? ''}
                  onChange={e => setRangeStart(e.target.value)}
                  className="bg-[#0D1117] border border-[#1E2530] text-white hover:border-[#2A3545] text-xs px-2 py-1 rounded-md cursor-pointer"
                >
                  {trend.quarters.map(q => (
                    <option key={q} value={q}>{quarterLabel(q)}</option>
                  ))}
                </select>
                <span className="text-[#4A5568] text-xs">~</span>
                <select
                  value={rangeEnd ?? ''}
                  onChange={e => setRangeEnd(e.target.value)}
                  className="bg-[#0D1117] border border-[#1E2530] text-white hover:border-[#2A3545] text-xs px-2 py-1 rounded-md cursor-pointer"
                >
                  {trend.quarters.map(q => (
                    <option key={q} value={q}>{quarterLabel(q)}</option>
                  ))}
                </select>
              </div>
            )}

            <div className="flex items-center gap-1 bg-[#0D1117] border border-[#1E2530] rounded-md p-1">
              {([
                { key: 'line', label: '선형', icon: 'ri-line-chart-line' },
                { key: 'bar', label: '바형', icon: 'ri-bar-chart-2-line' },
              ] as const).map(opt => (
                <button
                  key={opt.key}
                  onClick={() => setChartShape(opt.key)}
                  className={`flex items-center gap-1 px-2 py-1 rounded text-xs font-medium cursor-pointer transition-all ${
                    chartShape === opt.key ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'text-[#8B9BB4] hover:text-white'
                  }`}
                >
                  <i className={`${opt.icon} text-xs`}></i>
                  {opt.label}
                </button>
              ))}
            </div>

            <div className="flex items-center gap-1 bg-[#0D1117] border border-[#1E2530] rounded-md p-1">
              {([
                { key: 'dark', label: '다크', icon: 'ri-moon-line' },
                { key: 'light', label: '화이트', icon: 'ri-sun-line' },
              ] as const).map(opt => (
                <button
                  key={opt.key}
                  onClick={() => setChartTheme(opt.key)}
                  className={`flex items-center gap-1 px-2 py-1 rounded text-xs font-medium cursor-pointer transition-all ${
                    chartTheme === opt.key ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'text-[#8B9BB4] hover:text-white'
                  }`}
                >
                  <i className={`${opt.icon} text-xs`}></i>
                  {opt.label}
                </button>
              ))}
            </div>

            {excluded.size > 0 && (
              <div className="flex items-center gap-2 flex-1 min-w-0">
                <span className="text-[#8B9BB4] text-xs whitespace-nowrap">제외됨:</span>
                <div className="flex items-center gap-1 flex-wrap">
                  {Array.from(excluded).map(name => (
                    <button
                      key={name}
                      onClick={() => toggleExclude(name)}
                      className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-red-500/10 border border-red-500/30 text-red-400 text-[11px] hover:bg-red-500/20 transition-colors cursor-pointer"
                      title="클릭하여 다시 포함"
                    >
                      <span>{name}</span>
                      <i className="ri-close-line text-xs"></i>
                    </button>
                  ))}
                </div>
                <button
                  onClick={() => setExcluded(new Set())}
                  className="text-[#8B9BB4] hover:text-white text-xs whitespace-nowrap ml-auto cursor-pointer"
                >
                  모두 포함
                </button>
              </div>
            )}
          </div>
        )}

        {/* Main Chart */}
        {atc4Data && (
          <div className={`${themeVars.cardBg} ${themeVars.cardBorder} rounded-2xl p-6 border transition-colors`}>
            {view === 'donut' && (
              <div className="grid grid-cols-2 gap-8 items-center">
                <div>
                  <h3 className="text-white font-bold text-base mb-1">Market Share Distribution</h3>
                  <p className="text-[#8B9BB4] text-xs mb-4">{atc4Data.atc4_desc} · {quarterLabel(atc4Data.quarter)} · Values LC 기준</p>
                  <ResponsiveContainer width="100%" height={320}>
                    <PieChart>
                      <Pie
                        {...({ activeIndex, activeShape: renderActiveShape } as any)}
                        data={donutData}
                        cx="50%"
                        cy="50%"
                        innerRadius={80}
                        outerRadius={130}
                        dataKey="value"
                        onMouseEnter={(_, index) => setActiveIndex(index)}
                      >
                        {donutData.map((entry, index) => (
                          <Cell key={`cell-${index}`} fill={entry.color} stroke="transparent" />
                        ))}
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div className="space-y-2 max-h-[340px] overflow-y-auto pr-1">
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="text-white font-bold text-base">제품별 점유율 상세</h3>
                    <span className="text-[#4A5568] text-[10px]">클릭: 그래프에서 제외</span>
                  </div>
                  {atc4Data.products.slice(0, 10).map((p, idx) => {
                    const isExcluded = excluded.has(p.product_name);
                    return (
                      <div
                        key={p.product_name}
                        className={`p-3 rounded-xl border cursor-pointer transition-all ${
                          isExcluded
                            ? 'border-red-500/30 bg-red-500/5 opacity-50'
                            : activeIndex === idx
                              ? 'border-[#00E5CC]/50 bg-[#00E5CC]/5'
                              : 'border-[#1E2530] hover:border-[#2A3545]'
                        }`}
                        onMouseEnter={() => setActiveIndex(Math.min(idx, donutData.length - 1))}
                        onClick={() => toggleExclude(p.product_name)}
                        title={isExcluded ? '다시 포함' : '그래프에서 제외'}
                      >
                        <div className="flex items-center justify-between mb-2">
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: CHART_COLORS[idx % CHART_COLORS.length] }}></span>
                            <span className={`text-sm font-semibold truncate ${isExcluded ? 'line-through text-[#8B9BB4]' : 'text-white'}`}>{p.product_name}</span>
                            <span className="text-[#4A5568] text-[10px] truncate">{p.mfr_name}</span>
                          </div>
                          <span className={`text-sm font-bold whitespace-nowrap ${isExcluded ? 'line-through text-[#8B9BB4]' : 'text-white'}`}>{p.values_share_pct.toFixed(1)}%</span>
                        </div>
                        <div className="w-full h-1.5 bg-[#1E2530] rounded-full overflow-hidden">
                          <div className="h-full rounded-full transition-all duration-500" style={{ width: `${Math.min(p.values_share_pct, 100)}%`, backgroundColor: CHART_COLORS[idx % CHART_COLORS.length] }}></div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {view === 'unit' && trend && (
              <div>
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h3 className={`font-bold text-base mb-1 ${themeVars.titleText}`}>Unit Trend — Market Share (%)</h3>
                    <p className={`text-xs ${themeVars.subText}`}>
                      분기별 Dosage Units 기준 점유율 · {quarterLabel(visibleQuarters[0] ?? trend.quarters[0])} ~ {quarterLabel(visibleQuarters.at(-1) ?? trend.quarters.at(-1)!)}
                    </p>
                  </div>
                </div>
                <ResponsiveContainer width="100%" height={320}>
                  {chartShape === 'line' ? (
                    <LineChart data={trendChartData} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={themeVars.grid} />
                      <XAxis dataKey="quarter" tick={{ fill: themeVars.axisTick, fontSize: 11 }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fill: themeVars.axisTick, fontSize: 11 }} axisLine={false} tickLine={false} unit="%" />
                      <Tooltip content={<UnitTooltip />} />
                      <Legend wrapperStyle={{ color: themeVars.legend, fontSize: 12 }} />
                      {visibleBrands.map((b, idx) => (
                        <Line
                          key={b}
                          type="monotone"
                          dataKey={b}
                          stroke={CHART_COLORS[idx % CHART_COLORS.length]}
                          strokeWidth={2}
                          dot={{ fill: CHART_COLORS[idx % CHART_COLORS.length], r: 3, strokeWidth: 0 }}
                          activeDot={{ r: 6, strokeWidth: 0 }}
                        />
                      ))}
                    </LineChart>
                  ) : (
                    <BarChart data={trendChartData} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={themeVars.grid} />
                      <XAxis dataKey="quarter" tick={{ fill: themeVars.axisTick, fontSize: 11 }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fill: themeVars.axisTick, fontSize: 11 }} axisLine={false} tickLine={false} unit="%" />
                      <Tooltip content={<UnitTooltip />} cursor={{ fill: chartTheme === 'light' ? 'rgba(0,0,0,0.04)' : 'rgba(255,255,255,0.04)' }} />
                      <Legend wrapperStyle={{ color: themeVars.legend, fontSize: 12 }} />
                      {visibleBrands.map((b, idx) => (
                        <Bar key={b} dataKey={b} fill={CHART_COLORS[idx % CHART_COLORS.length]} radius={[4, 4, 0, 0]} />
                      ))}
                    </BarChart>
                  )}
                </ResponsiveContainer>
              </div>
            )}

            {view === 'revenue' && trend && (
              <div>
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h3 className={`font-bold text-base mb-1 ${themeVars.titleText}`}>Revenue Trend — Sales (M KRW)</h3>
                    <p className={`text-xs ${themeVars.subText}`}>
                      분기별 매출액 (Values LC, 단위: 백만원) · {quarterLabel(visibleQuarters[0] ?? trend.quarters[0])} ~ {quarterLabel(visibleQuarters.at(-1) ?? trend.quarters.at(-1)!)}
                    </p>
                  </div>
                </div>
                <ResponsiveContainer width="100%" height={320}>
                  {chartShape === 'line' ? (
                    <LineChart data={trendChartData} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={themeVars.grid} />
                      <XAxis dataKey="quarter" tick={{ fill: themeVars.axisTick, fontSize: 11 }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fill: themeVars.axisTick, fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={v => `${v.toLocaleString()}`} />
                      <Tooltip content={<RevenueTooltip />} />
                      <Legend wrapperStyle={{ color: themeVars.legend, fontSize: 12 }} />
                      {visibleBrands.map((b, idx) => (
                        <Line
                          key={b}
                          type="monotone"
                          dataKey={b}
                          stroke={CHART_COLORS[idx % CHART_COLORS.length]}
                          strokeWidth={2}
                          dot={{ fill: CHART_COLORS[idx % CHART_COLORS.length], r: 3, strokeWidth: 0 }}
                          activeDot={{ r: 6, strokeWidth: 0 }}
                        />
                      ))}
                    </LineChart>
                  ) : (
                    <BarChart data={trendChartData} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={themeVars.grid} />
                      <XAxis dataKey="quarter" tick={{ fill: themeVars.axisTick, fontSize: 11 }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fill: themeVars.axisTick, fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={v => `${v.toLocaleString()}`} />
                      <Tooltip content={<RevenueTooltip />} cursor={{ fill: chartTheme === 'light' ? 'rgba(0,0,0,0.04)' : 'rgba(255,255,255,0.04)' }} />
                      <Legend wrapperStyle={{ color: themeVars.legend, fontSize: 12 }} />
                      {visibleBrands.map((b, idx) => (
                        <Bar key={b} dataKey={b} fill={CHART_COLORS[idx % CHART_COLORS.length]} radius={[4, 4, 0, 0]} />
                      ))}
                    </BarChart>
                  )}
                </ResponsiveContainer>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
