import { useEffect, useMemo, useRef, useState } from 'react';
import {
  PieChart, Pie, Cell, ResponsiveContainer,
  XAxis, YAxis, CartesianGrid, Tooltip,
  LineChart, Line,
} from 'recharts';
import type { ReactElement } from 'react';
import {
  searchMarketShare, fetchAtc4, fetchAtc4Trend, fetchBrand,
  downloadMarketShareXlsx, resolveDefaultAtc4,
  buildPieData, buildTrendRows, buildBrandColorMap, quarterLabel, formatLcKrw,
  OTHERS_COLOR,
  type MsSearchHit, type MsAtc4Response, type MsTrendResponse, type MsBrandResponse,
  type PieSlice, type TrendRow,
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

const METRIC_HELP = 'Units = IQVIA 처방 단위(정·바이알 등)\n매출 = IQVIA Values LC(원)';

/** 작은 정보 툴팁 (hover / focus) */
const InfoTip = ({ text }: { text: string }) => (
  <span className="relative inline-flex items-center group align-middle" tabIndex={0} aria-label={text}>
    <i className="ri-information-line text-xs cursor-help" style={{ color: 'var(--label-3)' }}></i>
    <span role="tooltip"
      className="pointer-events-none absolute left-1/2 -translate-x-1/2 top-full mt-1 z-50 hidden group-hover:block group-focus:block w-max max-w-[260px] whitespace-pre-line rounded-lg px-2.5 py-1.5 text-[11px] font-normal leading-snug shadow-lg"
      style={{ background: 'rgba(29,29,31,0.92)', color: '#fff' }}>{text}</span>
  </span>
);

/** 라인의 마지막 유효 포인트 인덱스 — 직접 라벨 위치 */
function lastPointIndex(rows: TrendRow[], brand: string): number {
  for (let i = rows.length - 1; i >= 0; i--) if (rows[i][brand] != null) return i;
  return -1;
}

/** 라인 끝 직접 라벨 (recharts Line label renderer) */
function endLabel(brand: string, rows: TrendRow[], color: string) {
  const last = lastPointIndex(rows, brand);
  const Label = (props: { index?: number; x?: number | string; y?: number | string }): ReactElement => {
    if (props.index !== last || props.x == null || props.y == null) return <g />;
    return (
      <text x={Number(props.x) + 8} y={Number(props.y)} dy={4} fill={color} fontSize={11} fontWeight={600}>
        {brand.length > 14 ? `${brand.slice(0, 13)}…` : brand}
      </text>
    );
  };
  return Label;
}

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
  const [defaultAtc4, setDefaultAtc4] = useState<string | null>(null);
  const [selectedKey, setSelectedKey] = useState<{ name: string; atc4: string } | null>(null);
  const [downloading, setDownloading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // 기본 시장: 키트루다의 ATC4 (PD-1/PD-L1) 를 검색으로 해석
  useEffect(() => {
    let on = true;
    resolveDefaultAtc4().then(code => {
      if (!on) return;
      setDefaultAtc4(code);
      setAtc4Code(c => c ?? code);
    });
    return () => { on = false; };
  }, []);

  // 기간 (시작/끝 분기) — 추이 그래프 범위. 현황(카드·도넛·상세)의 '기준 분기' = 기간 종료 분기(toQ).
  // 분기 목록은 전체 시장 공통(_ms_all_quarters) 이므로 ATC4 가 바뀌어도 기간은 유지된다.
  const [fromQ, setFromQ] = useState<string>('');
  const [toQ, setToQ] = useState<string>('');
  const [latestQ, setLatestQ] = useState<string>('');
  // 최신 분기면 파라미터 생략 (초기 로드 후 중복 요청 방지)
  const snapParam = toQ && latestQ && toQ !== latestQ ? toQ : undefined;

  const market = useApi<MsAtc4Response | null>(
    () => (atc4Code ? fetchAtc4(atc4Code, snapParam) : Promise.resolve(null)),
    [atc4Code, snapParam],
  );
  const trend = useApi<MsTrendResponse | null>(
    () => (atc4Code && toQ ? fetchAtc4Trend(atc4Code, 5, fromQ || undefined, toQ || undefined) : Promise.resolve(null)),
    [atc4Code, fromQ, toQ],
  );
  const brandApi = useApi<MsBrandResponse | null>(
    () => (selectedKey
      ? fetchBrand(selectedKey.name, selectedKey.atc4, snapParam).catch(() => null)
      : Promise.resolve(null)),
    [selectedKey?.name, selectedKey?.atc4, snapParam],
  );
  const selectedBrand = selectedKey && brandApi.data && brandApi.data.product_name === selectedKey.name
    && brandApi.data.atc4_code === selectedKey.atc4 ? brandApi.data : null;

  useEffect(() => { setActiveIndex(0); }, [atc4Code, snapParam]);

  // 첫 로드 시 기본 기간 = 사용 가능한 마지막 6개 분기, 기준 분기 = 최신
  const availableQuarters = market.data?.quarters ?? trend.data?.available_quarters ?? [];
  useEffect(() => {
    const d = market.data;
    if (!d || latestQ) return;
    const qs = d.quarters ?? [];
    if (!qs.length) return;
    const latest = d.meta?.latest_quarter ?? qs[qs.length - 1];
    setLatestQ(latest);
    setToQ(latest);
    setFromQ(qs[Math.max(0, qs.length - 6)]);
  }, [market.data, latestQ]);
  const snapshotQ = market.data?.quarter ?? (toQ || latestQ);

  // ── 어댑터: 실데이터 → readdy 차트 형태 ──────────────────────────────────
  const pieData = useMemo(() => (market.data ? buildPieData(market.data, 5) : []), [market.data]);
  // 브랜드 색상 단일 매핑 — 카드·도넛·추이·상세 공통
  const colorMap = useMemo(
    () => buildBrandColorMap(pieData, trend.data?.top_brands ?? []),
    [pieData, trend.data],
  );
  const colorOf = (brand: string) => colorMap[brand] ?? OTHERS_COLOR;
  const shareTrendRows = useMemo(() => (trend.data ? buildTrendRows(trend.data, 'share') : []), [trend.data]);
  const unitTrendRows = useMemo(() => (trend.data ? buildTrendRows(trend.data, 'units') : []), [trend.data]);
  const revenueTrendRows = useMemo(() => (trend.data ? buildTrendRows(trend.data, 'revenue') : []), [trend.data]);
  const trendBrands = trend.data?.top_brands ?? [];

  const totalUnits = market.data ? Math.round(market.data.totals.dosage_units) : 0;
  const totalValuesM = market.data ? formatLcKrw(market.data.totals.values_lc) : '';
  const unitLine = (item: { units: number; unitShare: number | null }) =>
    `물량 ${item.unitShare != null ? item.unitShare + '% · ' : ''}${item.units.toLocaleString()} Units`;
  // 데이터가 이미 있으면 재요청 중에도 화면 유지 (상단 '갱신 중' 표시)
  const isLoading = !atc4Code || (market.loading && !market.data) || (trend.loading && !trend.data && !!toQ);
  const refreshing = !isLoading && (market.loading || trend.loading || brandApi.loading);
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

  const handleSelect = (hit: MsSearchHit) => {
    setSearchQuery(hit.product_name);
    setShowDropdown(false);
    if (hit.atc4_code !== atc4Code) setAtc4Code(hit.atc4_code);
    setSelectedKey({ name: hit.product_name, atc4: hit.atc4_code });
  };

  const handleClear = () => {
    setSearchQuery('');
    setSearchResults([]);
    setShowDropdown(false);
    setSelectedKey(null);
  };

  const handleResetMarket = () => {
    handleClear();
    if (defaultAtc4) setAtc4Code(defaultAtc4);
  };

  const exportRange = fromQ && toQ ? `${quarterLabel(fromQ)}~${quarterLabel(toQ)}` : '';
  const exportTitle = market.data
    ? `엑셀 내보내기 — 현황(점유율 시트): ${quarterLabel(market.data.quarter)} 기준 · 추이(Unit/Revenue Trend 시트): ${exportRange || '전체 분기'}`
    : '엑셀 다운로드';

  const handleDownload = async () => {
    if (!market.data) return;
    setDownloading(true);
    setActionError(null);
    try {
      await downloadMarketShareXlsx(market.data.atc4_code, market.data.quarter, 8, fromQ || undefined, toQ || undefined);
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

  const selectedColor = selectedBrand ? colorOf(selectedBrand.product_name) : undefined;

  const metaInfo = market.data?.meta;
  const sourceLine = [
    metaInfo?.source ?? 'IQVIA NSA-E',
    (metaInfo?.latest_quarter || latestQ) ? `최신 ${quarterLabel(metaInfo?.latest_quarter || latestQ)}` : '',
    metaInfo?.last_uploaded_at ? `업로드 ${metaInfo.last_uploaded_at.slice(0, 10)}` : '',
  ].filter(Boolean).join(' · ');

  /** 탭별 카드 지표 — 점유율 탭: 매출 점유율 / 물량 탭: 물량 점유율 / 매출 탭: 매출액 */
  const cardMetric = (item: PieSlice) => {
    if (view === 'unit') {
      return {
        label: '물량 점유율',
        main: item.unitShare != null ? `${item.unitShare}%` : '정보 없음',
        sub: `${item.units.toLocaleString()} Units · 매출 점유율 ${item.value}%`,
      };
    }
    if (view === 'revenue') {
      return { label: '분기 매출', main: `₩${formatLcKrw(item.valuesLc)}M`, sub: `매출 점유율 ${item.value}%` };
    }
    return { label: '매출 점유율', main: `${item.value}%`, sub: unitLine(item) };
  };

  const trendLegend = (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 mb-3">
      {trendBrands.map(b => (
        <span key={b} className={`inline-flex items-center gap-1.5 text-xs ${textSub}`}>
          <span className="w-3 h-[3px] rounded-full" style={{ backgroundColor: colorOf(b) }}></span>{b}
        </span>
      ))}
    </div>
  );

  const renderTrendChart = (
    rows: TrendRow[], height: number, yFormat: (v: number) => string,
    tooltip: ReactElement, yDomain?: [number, 'auto'],
  ) => (
    <>
      {trendLegend}
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={rows} margin={{ top: 10, right: 110, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
          <XAxis dataKey="quarter" tick={{ fill: tickFill, fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis tick={{ fill: tickFill, fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={yFormat} domain={yDomain} />
          <Tooltip content={tooltip} />
          {trendBrands.map(brand => {
            const c = colorOf(brand);
            return (
              <Line key={brand} type="monotone" dataKey={brand} name={brand} stroke={c} strokeWidth={2}
                dot={{ fill: c, r: 4, strokeWidth: 0 }} activeDot={{ r: 6, strokeWidth: 0 }}
                isAnimationActive={false} label={endLabel(brand, rows, c)} />
            );
          })}
        </LineChart>
      </ResponsiveContainer>
    </>
  );

  return (
    <div className={`min-h-screen ${pageBg} ${isDark ? 'text-white' : 'text-gray-900'}`}>
      <div className={`px-8 pt-8 pb-6 border-b ${headerBorder}`}>
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
          <div className="min-w-0 flex-1 basis-[320px]">
            <div className="flex items-center gap-2 mb-1">
              <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-pie-chart-2-line"></i></span>
              <h1 className={`text-2xl font-bold ${textMain}`}>시장점유율</h1>
            </div>
            <p className={`${textSub} text-sm`}>국내 시장 점유율 현황 및 매출 추이 분석</p>
            {market.data && (
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 mt-1">
                <span className={`text-sm break-words ${textMain}`}>{market.data.atc4_desc}</span>
                <span className="ui-pill">ATC4 {market.data.atc4_code}</span>
                {defaultAtc4 && market.data.atc4_code !== defaultAtc4 && (
                  <button onClick={handleResetMarket}
                    className="inline-flex items-center gap-1 text-xs font-medium cursor-pointer hover:underline"
                    style={{ color: 'var(--accent)' }} title={`기본 시장(ATC4 ${defaultAtc4}, 키트루다 시장)으로 돌아가기`}>
                    <i className="ri-arrow-go-back-line text-xs"></i>기본 시장으로
                  </button>
                )}
              </div>
            )}
            <p className={`text-xs mt-1 flex flex-wrap items-center gap-1.5 ${textMuted}`}>
              <span>데이터: {sourceLine}</span>
              <InfoTip text={METRIC_HELP} />
              {refreshing && <span className="animate-pulse">· 갱신 중…</span>}
            </p>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <button onClick={handleDownload} disabled={downloading || !market.data} title={exportTitle}
              className={`flex items-center gap-2 border text-sm font-medium px-4 py-2 rounded-lg cursor-pointer whitespace-nowrap transition-all disabled:opacity-50 disabled:cursor-not-allowed ${isDark ? 'bg-[#161B27] border-[#1E2530] text-[#8B9BB4] hover:text-white hover:border-[#2A3545]' : 'bg-white border-gray-200 text-gray-500 hover:text-gray-900 hover:border-gray-300'}`}>
              <span className="w-4 h-4 flex items-center justify-center">
                <i className={downloading ? 'ri-loader-4-line text-sm animate-spin' : 'ri-file-excel-2-line text-sm'}></i>
              </span>
              <span className="flex flex-col items-start leading-tight">
                <span>{downloading ? '다운로드 중…' : '엑셀 다운로드'}</span>
                {market.data && (
                  <span className={`text-[10px] font-normal ${textMuted}`}>
                    현황 {quarterLabel(market.data.quarter)} · 추이 {exportRange || '전체'}
                  </span>
                )}
              </span>
            </button>
            {availableQuarters.length > 0 && (
              <div className={`flex items-center gap-1.5 rounded-lg px-2 py-1 ${tabBg}`}
                title="기간: 추이 그래프 범위 · 현황(카드·도넛·상세)은 종료 분기 기준">
                <i className={`ri-calendar-line text-xs ${textSub}`}></i>
                <span className={`text-[11px] ${textSub}`}>기간</span>
                <select value={fromQ} onChange={e => setFromQ(e.target.value)} aria-label="기간 시작 분기"
                  className={`bg-transparent text-xs font-medium outline-none cursor-pointer ${textMain}`}>
                  {availableQuarters.filter(q => !toQ || q <= toQ).map(q => (
                    <option key={q} value={q} className={isDark ? 'bg-[#1E2530]' : 'bg-white'}>{quarterLabel(q)}</option>
                  ))}
                </select>
                <span className={`text-xs ${textSub}`}>~</span>
                <select value={toQ} onChange={e => setToQ(e.target.value)} aria-label="기간 종료 분기 (현황 기준 분기)"
                  className={`bg-transparent text-xs font-medium outline-none cursor-pointer ${textMain}`}>
                  {availableQuarters.filter(q => !fromQ || q >= fromQ).map(q => (
                    <option key={q} value={q} className={isDark ? 'bg-[#1E2530]' : 'bg-white'}>{quarterLabel(q)}</option>
                  ))}
                </select>
                <span className={`text-[10px] whitespace-nowrap ${textMuted}`}>· 현황 기준 {toQ ? quarterLabel(toQ) : ''}</span>
              </div>
            )}
            <div className={`flex items-center gap-1 rounded-lg p-1 ${tabBg}`}>
              {[
                { key: 'donut', label: '점유율', icon: 'ri-pie-chart-2-line' },
                { key: 'unit', label: '물량 추이', icon: 'ri-bar-chart-grouped-line' },
                { key: 'revenue', label: '매출 추이', icon: 'ri-line-chart-line' },
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
            <div className="flex items-start justify-between gap-3 mb-4">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0">
                <span className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: selectedColor }}></span>
                <h3 className={`font-bold text-lg ${textMain}`}>{selectedBrand.product_name}</h3>
                <span className={`text-sm ${textSub}`}>({selectedBrand.molecule_desc})</span>
                <span className="ui-pill">{selectedBrand.atc4_desc}</span>
                <span className={`text-xs ${textMuted}`}>{quarterLabel(selectedBrand.quarter)} 기준</span>
              </div>
              <button onClick={handleClear} className={`cursor-pointer transition-colors flex-shrink-0 ${textMuted} hover:${textMain}`} aria-label="상세 닫기"><i className="ri-close-line text-lg"></i></button>
            </div>
            {(() => {
              const qPoint = selectedBrand.quarterly.find(x => x.quarter === selectedBrand.quarter);
              const qValues = selectedBrand.quarter_values_lc ?? qPoint?.values_lc ?? 0;
              const qUnits = selectedBrand.quarter_dosage_units ?? qPoint?.dosage_units ?? 0;
              const q = quarterLabel(selectedBrand.quarter);
              const stats: { label: string; value: string; icon: string; highlight?: boolean }[] = [
                { label: '제조사', value: selectedBrand.mfr_name || '정보 없음', icon: 'ri-building-2-line' },
                { label: `매출 점유율 (${q})`, value: `${selectedBrand.market_share_pct.toFixed(1)}%`, icon: 'ri-pie-chart-2-line', highlight: true },
                { label: `분기 매출 (${q})`, value: `₩${formatLcKrw(qValues)}M`, icon: 'ri-line-chart-line' },
                { label: `분기 물량 (${q})`, value: `${Math.round(qUnits).toLocaleString()} Units`, icon: 'ri-bar-chart-grouped-line' },
                { label: `ATC4 시장 순위 (${q}, 매출)`, value: selectedBrand.market_rank ? `#${selectedBrand.market_rank}` : '정보 없음', icon: 'ri-trophy-line' },
              ];
              return (
                <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                  {stats.map(item => (
                    <div key={item.label} className={`rounded-xl p-4 border ${statBg}`}>
                      <div className="flex items-center gap-2 mb-2"><span className={`w-4 h-4 flex items-center justify-center ${textSub}`}><i className={`${item.icon} text-sm`}></i></span><span className={`text-xs ${textSub}`}>{item.label}</span></div>
                      <p className={`text-base font-bold ${item.highlight ? '' : textMain}`}
                        style={item.highlight ? { color: selectedColor } : undefined}>{item.value}</p>
                    </div>
                  ))}
                </div>
              );
            })()}
          </div>
        )}

        {!isLoading && pieData.length > 0 && (
          <div>
            <p className={`text-xs mb-2 flex items-center gap-1.5 ${textSub}`}>
              <span className={`font-semibold ${textMain}`}>현황</span>
              <span>· {quarterLabel(snapshotQ)} 기준 · {view === 'unit' ? '물량(Units) 점유율' : view === 'revenue' ? '매출(Values LC)' : '매출(Values LC) 점유율'}</span>
              <InfoTip text={METRIC_HELP} />
            </p>
            <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
              {pieData.map(item => {
                const m = cardMetric(item);
                return (
                  <div key={item.name} className={`rounded-xl p-4 border ${sumBg}`}>
                    <div className="flex items-center gap-2 mb-2"><span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: item.color }}></span><span className={`text-xs truncate ${textSub}`} title={item.name}>{item.name}</span></div>
                    <p className={`text-[11px] ${textMuted}`}>{m.label}</p>
                    <p className="text-xl font-bold" style={{ color: item.color }}>{m.main}</p>
                    <p className={`text-xs mt-1 ${textMuted}`}>{m.sub}</p>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {!isLoading && pieData.length > 0 && (
          <div className={`rounded-2xl p-6 border ${chartBg}`}>
            {view === 'donut' && (
              <div>
                <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
                  <div className="min-w-0">
                    <h3 className={`font-bold text-base mb-1 ${textMain}`}>
                      {shareSubView === 'snapshot' ? '점유율 분포' : '점유율 추이'}
                    </h3>
                    <p className={`text-xs ${textSub}`}>
                      {shareSubView === 'snapshot'
                        ? `${market.data ? quarterLabel(market.data.quarter) : ''} · 매출(Values LC) 기준 점유율 · 시장 전체 매출 ₩${totalValuesM}M · 물량 ${totalUnits.toLocaleString()} Units`
                        : `분기별 매출(Values LC) 점유율 추이 · ${trendRangeLabel(shareTrendRows)} · 상위 ${trendBrands.length}개 (${quarterLabel(toQ || snapshotQ)} 매출 기준)`}
                    </p>
                  </div>
                  <div className={`flex items-center gap-1 rounded-lg p-1 ${subtabBg}`}>
                    {[
                      { key: 'snapshot', label: '현황', icon: 'ri-pie-chart-2-line' },
                      { key: 'trend', label: '추이', icon: 'ri-line-chart-line' },
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
                            <p className="text-xl font-bold" style={{ color: activeSlice.color }}>{activeSlice.value}%</p>
                            <p className={`text-[11px] ${textSub}`}>매출 점유율</p>
                            <p className={`text-[11px] ${textSub}`}>{unitLine(activeSlice)}</p>
                          </div>
                        </div>
                      )}
                    </div>
                    <div className="space-y-3">
                      <h3 className={`font-bold text-base mb-4 ${textMain}`}>제품별 점유율 상세 <span className={`text-xs font-normal ${textMuted}`}>매출 기준 · 물량 병기</span></h3>
                      {pieData.map((item, idx) => (
                        <div key={item.name} className={`p-3 rounded-xl border cursor-pointer transition-all ${safeActive === idx ? listActive : listDefault}`}
                          onMouseEnter={() => setActiveIndex(idx)}>
                          <div className="flex items-center justify-between mb-2">
                            <div className="flex items-center gap-2 min-w-0"><span className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: item.color }}></span><span className={`text-sm font-semibold truncate ${textMain}`}>{item.name}</span>{item.mfrName && <span className={`text-[10px] truncate ${textMuted}`}>{item.mfrName}</span>}</div>
                            <div className="flex items-center gap-3 flex-shrink-0">
                              <span className={`text-xs ${textMuted}`}>{unitLine(item)}</span>
                              <span className={`text-sm font-bold ${textMain}`} title="매출(Values LC) 기준 점유율">매출 {item.value}%</span>
                            </div>
                          </div>
                          <div className={`w-full h-1.5 rounded-full overflow-hidden ${barTrack}`}><div className="h-full rounded-full transition-all duration-500" style={{ width: `${Math.min(item.value, 100)}%`, backgroundColor: item.color }}></div></div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {shareSubView === 'trend' && renderTrendChart(
                  shareTrendRows, 360, v => `${v}%`, <ShareTrendTooltip isDark={isDark} />, [0, 'auto'],
                )}
              </div>
            )}

            {view === 'unit' && (
              <div>
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h3 className={`font-bold text-base mb-1 flex items-center gap-1.5 ${textMain}`}>물량 추이 — 처방 단위(Units) <InfoTip text={METRIC_HELP} /></h3>
                    <p className={`text-xs ${textSub}`}>분기별 IQVIA 처방 단위 수량 추이 · {trendRangeLabel(unitTrendRows)} · 상위 {trendBrands.length}개 ({quarterLabel(toQ || snapshotQ)} 매출 기준)</p>
                  </div>
                </div>
                {renderTrendChart(unitTrendRows, 320, v => `${(v / 1000).toFixed(1)}k`, <UnitTooltip isDark={isDark} />, [0, 'auto'])}
              </div>
            )}

            {view === 'revenue' && (
              <div>
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h3 className={`font-bold text-base mb-1 flex items-center gap-1.5 ${textMain}`}>매출 추이 — 매출액(백만원) <InfoTip text={METRIC_HELP} /></h3>
                    <p className={`text-xs ${textSub}`}>분기별 매출액 추이 (IQVIA Values LC, 단위: 백만원) · {trendRangeLabel(revenueTrendRows)} · 상위 {trendBrands.length}개 ({quarterLabel(toQ || snapshotQ)} 매출 기준)</p>
                  </div>
                </div>
                {renderTrendChart(revenueTrendRows, 320, v => v.toLocaleString(), <RevenueTooltip isDark={isDark} />)}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
