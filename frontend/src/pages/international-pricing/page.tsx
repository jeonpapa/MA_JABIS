import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Bar, BarChart, Cell, LabelList, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { useApi } from '@/hooks/useApi';
import {
  deleteForeignDrug,
  fetchForeignDrugList,
  fetchPricingTab,
  resolveIdentity,
  downloadForeignReport,
  PRICING_COUNTRY_CODE,
  a8ExcludeReasonLabel,
} from '@/api/foreign';
import PriceRequestPanel from './PriceRequestPanel';
import type { PriceRequestPanelHandle } from './PriceRequestPanel';
import OecdPrices from './OecdPrices';
import { downloadPriceHistory } from '@/api/priceRequests';
import type { PriceScope } from '@/api/priceRequests';
import type {
  A8Pricing,
  Candidate,
  ForeignDrugListItem,
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

const countryMeta = (key: string) => A8_COUNTRIES.find(c => c.key === key);
const countryLabel = (key: string) => countryMeta(key)?.label ?? key;
const countryFlag = (key: string) => countryMeta(key)?.flag ?? '';

const getCurrencySymbol = (currency: string) => {
  const map: Record<string, string> = { USD: '$', GBP: '£', JPY: '¥', CHF: 'Fr.', EUR: '€', CAD: 'CA$' };
  return map[currency] || currency;
};

const formatKrw = (v?: number | null) => (v == null ? null : `₩${Math.round(v).toLocaleString()}`);

/** YYYYMMDD → YYYY.MM */
const fxMonth = (s: string) => (s && s.length >= 6 ? `${s.slice(0, 4)}.${s.slice(4, 6)}` : s);

const displayName = (q: string) => (q ? q.charAt(0).toUpperCase() + q.slice(1) : q);

interface Selection {
  query: string;       // 백엔드 query_name / product slug (keytruda, welireg …)
  canonical: string;   // INN — 이력에 없는 ad-hoc 검색이면 ''
  countryCount?: number;
  /** 타입어헤드로 확정된 약제 정체성 — 검색 시 백엔드로 전달 (스크레이퍼 term 라우팅) */
  identity?: { slug: string; brand: string; inn: string };
}

function readPricingDeepLink(): { query: string; scope: PriceScope; requestId: string } {
  if (typeof window === 'undefined') return { query: '', scope: 'a8', requestId: '' };
  const params = new URLSearchParams(window.location.search);
  const query = (params.get('q') || '').replace(/[\u0000-\u001f\u007f]/g, '').trim().slice(0, 160);
  const scope: PriceScope = params.get('scope') === 'oecd' ? 'oecd' : 'a8';
  const rawId = params.get('request') || '';
  const requestId = /^[A-Za-z0-9_-]{1,128}$/.test(rawId) ? rawId : '';
  return { query, scope, requestId };
}

export default function InternationalPricingPage() {
  const [deepLink] = useState(readPricingDeepLink);
  const [isDark, setIsDark] = useState(false);
  const [newSearchQuery, setNewSearchQuery] = useState('');
  const [identityCandidates, setIdentityCandidates] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState<Selection | null>(() => deepLink.query ? { query: deepLink.query, canonical: '' } : null);
  const [searchDropdown, setSearchDropdown] = useState(false);
  const [scope, setScope] = useState<PriceScope>(deepLink.scope);
  const [resultRevision, setResultRevision] = useState(0);
  const requestPanelRef = useRef<PriceRequestPanelHandle>(null);
  const [deleting, setDeleting] = useState<string | null>(null); // 삭제 진행 중 queryName
  const [historyDownloadBusy, setHistoryDownloadBusy] = useState<Record<string, boolean>>({});
  const [historyDownloadErrors, setHistoryDownloadErrors] = useState<Record<string, string>>({});
  const historyDownloadingRef = useRef(new Set<string>());
  const [showCalcLogic, setShowCalcLogic] = useState(false);     // 조정가 산출 로직 패널
  const [showReport, setShowReport] = useState(false);           // 리포트 다운로드 모달
  const [reportFmt, setReportFmt] = useState<'xlsx' | 'hwpx'>('xlsx');
  const [appliedDisplay, setAppliedDisplay] = useState('');      // 국내 신청가 표시가
  const [appliedActual, setAppliedActual] = useState('');        // 국내 신청가 실제가
  const [reportBusy, setReportBusy] = useState(false);
  const [reportErr, setReportErr] = useState('');

  // ── 데이터 로딩 (캐시/DB 조회 전용 — 라이브 스크레이프는 명시적 버튼 뒤에만) ──
  const history = useApi<ForeignDrugListItem[]>(fetchForeignDrugList, []);

  const pricing = useApi<PricingTabData | null>(
    () => (selected ? fetchPricingTab(selected.query) : Promise.resolve(null)),
    [selected?.query],
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

  // 검색어 → 약제 정체성 해석 (300ms debounce, GET /api/foreign/resolve)
  useEffect(() => {
    const q = newSearchQuery.trim();
    if (!q) { setIdentityCandidates([]); return; }
    let cancelled = false;
    const t = setTimeout(async () => {
      try {
        const cands = await resolveIdentity(q);
        if (!cancelled) setIdentityCandidates(cands);
      } catch { if (!cancelled) setIdentityCandidates([]); }
    }, 300);
    return () => { cancelled = true; clearTimeout(t); };
  }, [newSearchQuery]);

  const handleSelect = (sel: Selection) => {
    setSelected(sel);
    setNewSearchQuery('');
    setSearchDropdown(false);
    setShowCalcLogic(false);
  };
  const handleSelectHistory = (d: ForeignDrugListItem) =>
    handleSelect({ query: d.queryName, canonical: d.canonical, countryCount: d.countryCount });
  // 타입어헤드 후보 확정 — identity 를 selection 에 실어 검색 시 백엔드로 전달
  const handleSelectIdentity = (c: Candidate) => {
    handleSelect({
      query: c.slug,
      canonical: c.inn || '',
      identity: { slug: c.slug, brand: c.brand, inn: c.inn },
    });
  };
  const handleNewSearch = () => {
    const q = newSearchQuery.trim();
    if (!q) return;
    if (identityCandidates.length > 0) handleSelectIdentity(identityCandidates[0]);
    else if (searchResults.length > 0) handleSelectHistory(searchResults[0]);
    else handleSelect({ query: q.toLowerCase(), canonical: '' }); // 캐시 조회만 — 비어있으면 빈 상태 + 라이브 버튼
  };

  // 검색 이력 삭제 — 가격·HTA·허가 캐시 전부 제거 (복구 불가, confirm 필수)
  const handleDeleteHistory = async (e: React.MouseEvent, d: ForeignDrugListItem) => {
    e.stopPropagation(); // 카드 선택 클릭과 분리
    if (deleting) return;
    const ok = window.confirm(
      `'${displayName(d.queryName)}' 검색 이력을 삭제할까요?\n${d.countryCount}개국 가격 캐시와 HTA·허가 캐시가 모두 삭제되며 복구할 수 없습니다.`,
    );
    if (!ok) return;
    setDeleting(d.queryName);
    try {
      await deleteForeignDrug(d.queryName);
      if (selected?.query === d.queryName) setSelected(null); // 보던 제품이면 상세 닫기
      history.reload();
    } catch (err) {
      window.alert(`삭제 실패: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setDeleting(null);
    }
  };

  const handleDownloadHistory = async (queryName: string) => {
    if (historyDownloadingRef.current.has(queryName)) return;
    historyDownloadingRef.current.add(queryName);
    setHistoryDownloadBusy(prev => ({ ...prev, [queryName]: true }));
    setHistoryDownloadErrors(prev => ({ ...prev, [queryName]: '' }));
    try { await downloadPriceHistory('a8', queryName); }
    catch (error) { setHistoryDownloadErrors(prev => ({ ...prev, [queryName]: error instanceof Error ? error.message : String(error) })); }
    finally {
      historyDownloadingRef.current.delete(queryName);
      setHistoryDownloadBusy(prev => ({ ...prev, [queryName]: false }));
    }
  };

  // 수집은 Aside 큐로만 접수. 캐시 표시·조정가 계산 경로는 그대로 유지한다.
  const runLiveSearch = () => {
    if (selected) requestPanelRef.current?.submit(undefined, selected);
  };
  const researchCountry = (countryKey: string) => {
    if (selected) requestPanelRef.current?.submit([PRICING_COUNTRY_CODE[countryKey] ?? countryKey], selected);
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
  const dropdownBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200 shadow-lg';
  const dropdownHover = isDark ? 'hover:bg-[#00E5CC]/8' : 'hover:bg-teal-50';
  const emptyCenter = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const divider = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const pillBg = isDark ? 'bg-[#1E2530]' : 'bg-gray-100';
  const pillText = isDark ? 'text-[#8B9BB4]' : 'text-gray-600';
  const tableHeadBg = isDark ? 'bg-[#0D1117]' : 'bg-gray-50';

  const chartAxis = isDark ? '#4A5568' : '#9CA3AF';
  const chartGrid = isDark ? '#1E2530' : 'rgba(60,60,67,0.1)';
  const chartBar = isDark ? '#0A84FF' : '#0071E3';
  const chartMin = '#34C759';
  const chartMax = '#FF3B30';
  const chartAvgLine = isDark ? '#FF9F0A' : '#FF9F0A';

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

  // ── 제형(formulation) 탭 — 강도×투여경로. 선택 제형의 a8Pricing/summary 로 렌더 ──
  const formulations = pricing.data?.formulations ?? [];
  const [pickedForm, setPickedForm] = useState<string | null>(null);
  const activeFormKey = (pickedForm && formulations.some(f => f.key === pickedForm))
    ? pickedForm : (formulations[0]?.key ?? null);
  const activeGroup = formulations.find(f => f.key === activeFormKey) ?? null;
  const activeA8: Record<string, A8Pricing | undefined> =
    activeGroup?.a8Pricing ?? pricing.data?.a8Pricing ?? {};
  const summary = activeGroup?.summary ?? pricing.data?.summary;

  // 그래프 데이터 — 조정가 보유 국가만, 조정가 오름차순
  const chartData = useMemo(() => {
    return A8_COUNTRIES
      .map(c => { const cell = activeA8[c.key]; return { key: c.key, name: c.label, flag: c.flag, adj: (cell?.adjustedPriceKrwNormalized ?? cell?.adjustedPriceKrw) ?? null }; })
      .filter((d): d is { key: string; name: string; flag: string; adj: number } => d.adj != null && d.adj > 0)
      .sort((a, b) => a.adj - b.adj);
  }, [activeA8]);

  // 산출 로직 테이블 행 — calc breakdown 보유 국가만
  const calcRows = useMemo(() => {
    return A8_COUNTRIES
      .map(c => ({ meta: c, cell: activeA8[c.key] }))
      .filter((r): r is { meta: typeof A8_COUNTRIES[number]; cell: A8Pricing } => !!r.cell?.calc);
  }, [activeA8]);

  const fxWindow = calcRows.length > 0 && calcRows[0].cell.calc
    ? `${fxMonth(calcRows[0].cell.calc.fxFrom)} ~ ${fxMonth(calcRows[0].cell.calc.fxTo)}`
    : '';

  return (
    <div className={`min-h-screen ${pageBg} ${isDark ? 'text-white' : 'text-gray-900'}`}>
      <div className={`px-8 pt-8 pb-6 border-b ${headerBorder}`}>
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-global-line"></i></span>
              <h1 className={`text-2xl font-bold ${textMain}`}>해외 약가</h1>
            </div>
            <p className={`${textSub} text-sm`}>A8 조정가 비교 / OECD·공단 추가국가 공개 가격 및 조사 요청</p>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={() => { if (selected) { setReportErr(''); setShowReport(true); } }}
              disabled={!selected || scope !== 'a8'}
              className="flex items-center gap-2 bg-teal-600 text-white text-sm font-semibold px-4 py-2 rounded-lg cursor-pointer whitespace-nowrap hover:bg-teal-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
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
        <div role="tablist" aria-label="해외약가 국가 범위" className="flex gap-2">
          {(['a8', 'oecd'] as const).map(value => <button key={value} role="tab" aria-selected={scope === value}
            onClick={() => setScope(value)} className={"rounded-lg px-4 py-2 text-sm font-semibold " + (scope === value ? 'bg-teal-600 text-white' : pillBg + ' ' + pillText)}>
            {value === 'a8' ? 'A8' : 'OECD·공단 추가국가'}
          </button>)}
        </div>
        <PriceRequestPanel ref={requestPanelRef} targetRequestId={deepLink.requestId} scope={scope} query={selected?.query ?? ''} identity={selected?.identity} isDark={isDark}
          onResultsChanged={request => {
            history.reload();
            if (request.scope === 'a8' && selected) pricing.reload();
            setResultRevision(v => v + 1);
          }} />
        {scope === 'oecd' ? <OecdPrices query={selected?.query ?? ''} revision={resultRevision} isDark={isDark} /> : <>

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
                <div key={drug.id} onClick={() => handleSelectHistory(drug)} role="button" tabIndex={0}
                  onKeyDown={e => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); handleSelectHistory(drug); } }}
                  className={`relative text-left p-4 rounded-2xl border transition-all cursor-pointer group ${selected?.query === drug.queryName ? accentBg + ' ' + accentBorder : historyCard + ' ' + historyCardHover}`}>
                  <button onClick={e => handleDeleteHistory(e, drug)} disabled={deleting === drug.queryName}
                    title="검색 이력 삭제 (가격·HTA·허가 캐시 전부)"
                    className={`absolute top-2.5 right-2.5 w-6 h-6 flex items-center justify-center rounded-lg transition-all cursor-pointer opacity-0 group-hover:opacity-100 focus:opacity-100 ${isDark ? 'text-[#4A5568] hover:text-red-400 hover:bg-red-400/10' : 'text-gray-300 hover:text-red-500 hover:bg-red-50'}`}>
                    <i className={`text-sm ${deleting === drug.queryName ? 'ri-loader-4-line animate-spin' : 'ri-delete-bin-line'}`}></i>
                  </button>
                  <div className="flex items-start justify-between mb-3">
                    <div className="flex-1 min-w-0">
                      <p className={`text-sm font-bold truncate ${selected?.query === drug.queryName ? accentColor : textMain} group-hover:${accentColor} transition-colors`}>{displayName(drug.queryName)}</p>
                      <p className={`${textSub} text-xs mt-0.5 truncate`}>{drug.canonical}</p>
                    </div>
                    {selected?.query === drug.queryName && <span className={`w-5 h-5 flex items-center justify-center flex-shrink-0 ml-2 mr-6 ${accentColor}`}><i className="ri-checkbox-circle-fill text-sm"></i></span>}
                  </div>
                  <div className="space-y-1.5">
                    <div className="flex items-center gap-1.5"><span className={`w-3.5 h-3.5 flex items-center justify-center ${textMuted}`}><i className="ri-calendar-line text-xs"></i></span><span className={`text-xs ${textMuted}`}>{drug.lastSearchedAt}</span></div>
                    <div className="flex items-center gap-1.5"><span className={`w-3.5 h-3.5 flex items-center justify-center ${textMuted}`}><i className="ri-global-line text-xs"></i></span><span className={`text-xs truncate ${textMuted}`}>{drug.countryCount}개국 가격 캐시</span></div>
                    <button type="button" disabled={historyDownloadBusy[drug.queryName]} aria-busy={!!historyDownloadBusy[drug.queryName]}
                      aria-label={displayName(drug.queryName) + ' 현재 저장 캐시 Excel 다운로드'}
                      onClick={e => { e.stopPropagation(); void handleDownloadHistory(drug.queryName); }}
                      className={'rounded-lg border px-3 py-1.5 text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed ' + accentColor}>
                      {historyDownloadBusy[drug.queryName] ? '다운로드 중…' : 'Excel 다운로드'}
                    </button>
                    <p className={'text-xs ' + textSub}>현재 저장 캐시 기준 · 요청 당시 스냅샷 아님</p>
                    {historyDownloadErrors[drug.queryName] && <p role="alert" className="text-xs text-red-500 break-words">다운로드 실패: {historyDownloadErrors[drug.queryName]}</p>}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* New Search */}
        <div className={`rounded-2xl border p-5 ${searchBg} ${cardBorder}`}>
          <div className="flex items-center gap-2 mb-3">
            <span className={`w-4 h-4 flex items-center justify-center ${accentColor}`}><i className="ri-search-2-line text-sm"></i></span>
            <p className={`text-sm font-semibold ${textMain}`}>신규 검색</p>
            <span className={`text-xs ${textMuted}`}>이력에 없는 제품은 캐시 조회 후 조사 요청을 접수할 수 있습니다</span>
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
                  {/* 해석된 약제 정체성 후보 — 클릭 시 {brand, INN} 확정 후 검색에 전달 */}
                  {identityCandidates.length > 0 && (
                    <div className={`border-b ${divider}`}>
                      {identityCandidates.map(c => (
                        <button key={c.slug} onClick={() => handleSelectIdentity(c)}
                          className={`w-full flex items-center gap-2 px-4 py-2.5 text-left hover:${isDark ? 'bg-[#1E2530]' : 'bg-gray-50'} transition-colors`}>
                          <i className="ri-checkbox-circle-line text-teal-500"></i>
                          <div className="flex-1 min-w-0">
                            <p className={`text-sm font-semibold ${textMain}`}>{c.display || c.brand}</p>
                            <p className={`text-xs ${textSub}`}>
                              {c.inn ? `성분 ${c.inn} · ` : ''}{c.matched_as === 'ingredient' ? '성분명 입력' : '브랜드명 입력'}
                              {c.source === 'mfds' ? ' · MFDS' : ''}
                            </p>
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
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
                      <div className="flex-1 min-w-0"><p className={`text-sm font-semibold ${textMain}`}>'{newSearchQuery.trim()}' 캐시 조회</p><p className={`text-xs ${textSub}`}>이력에 없는 제품 · 캐시 확인 후 조사 요청 가능</p></div>
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
              </div>
            </div>

            {pricing.loading && !pricing.data ? <TabLoading label="A8 국가 캐시 가격 로딩 중…" />
              : pricing.error ? <TabError message={pricing.error} onRetry={pricing.reload} />
                : (
                  <div className="space-y-4">
                    {pricing.data && !pricing.data.hasAnyPrice && (
                      <div className={`rounded-2xl border px-5 py-4 flex items-center justify-between gap-4 ${cardBg} ${cardBorder}`}>
                        <div>
                          <p className={`text-sm font-semibold ${textMain}`}>캐시된 가격 데이터가 없습니다</p>
                          <p className={`text-xs mt-0.5 ${textMuted}`}>위 패널에서 수신 이메일을 확인하고 A8 조사 요청을 접수하세요</p>
                        </div>
                        <button onClick={runLiveSearch}
                          className="flex items-center gap-2 text-xs font-bold px-4 py-2.5 rounded-xl whitespace-nowrap transition-colors bg-teal-600 text-white hover:bg-teal-700 cursor-pointer">
                          <i className="ri-radar-line"></i>
                          A8 전체 조사 요청
                        </button>
                      </div>
                    )}

                    {/* ── 제형(강도×투여경로) 탭 — US canonical 먼저, foreign_only 뒤 ── */}
                    {formulations.length > 0 && (
                      <div className="flex gap-2 flex-wrap items-center">
                        <span className={`text-xs font-semibold mr-1 ${textSub}`}>제형</span>
                        {formulations.map(f => (
                          <button key={f.key} onClick={() => setPickedForm(f.key)}
                            className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg border cursor-pointer whitespace-nowrap transition-colors ${activeFormKey === f.key ? 'bg-teal-600 text-white border-teal-600' : `${cardBg} ${cardBorder} ${textSub} hover:${textMain}`}`}>
                            {f.label}
                            {!f.isUsListed && <span className="text-[10px] px-1 py-0.5 rounded bg-red-500/15 text-red-400 font-semibold">US 미등재</span>}
                          </button>
                        ))}
                      </div>
                    )}

                    {/* ── A8 조정가 요약 카드 (최저 / 평균 / 최고) ── */}
                    {summary && (
                      <div>
                        <div className="grid grid-cols-3 gap-3">
                          <div className={`rounded-2xl border p-5 ${cardBg} ${cardBorder}`}>
                            <div className="flex items-center gap-2 mb-2">
                              <span className="w-5 h-5 flex items-center justify-center text-emerald-500"><i className="ri-arrow-down-circle-line"></i></span>
                              <p className={`text-xs font-semibold uppercase tracking-wider ${textSub}`}>A8 조정 최저가</p>
                            </div>
                            <p className="text-2xl font-black text-emerald-500 tabular-nums">{formatKrw(summary.minKrw)}</p>
                            <p className={`text-xs mt-1.5 ${textSub}`}>{countryFlag(summary.minCountryKey)} {countryLabel(summary.minCountryKey)} · 최소단위(정/바이알)당</p>
                          </div>
                          <div className={`rounded-2xl border p-5 ${cardBg} ${cardBorder}`}>
                            <div className="flex items-center gap-2 mb-2">
                              <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-equalizer-2-line"></i></span>
                              <p className={`text-xs font-semibold uppercase tracking-wider ${textSub}`}>A8 조정 평균가</p>
                            </div>
                            <p className={`text-2xl font-black tabular-nums ${accentColor}`}>{formatKrw(summary.avgKrw)}</p>
                            <p className={`text-xs mt-1.5 ${textSub}`}>{summary.includedKeys.length}개국 단순평균 · 최소단위당</p>
                          </div>
                          <div className={`rounded-2xl border p-5 ${cardBg} ${cardBorder}`}>
                            <div className="flex items-center gap-2 mb-2">
                              <span className="w-5 h-5 flex items-center justify-center text-red-400"><i className="ri-arrow-up-circle-line"></i></span>
                              <p className={`text-xs font-semibold uppercase tracking-wider ${textSub}`}>A8 조정 최고가</p>
                            </div>
                            <p className="text-2xl font-black text-red-400 tabular-nums">{formatKrw(summary.maxKrw)}</p>
                            <p className={`text-xs mt-1.5 ${textSub}`}>{countryFlag(summary.maxCountryKey)} {countryLabel(summary.maxCountryKey)} · 최소단위당</p>
                          </div>
                        </div>
                        {(summary.excludedKeys.length > 0 || summary.unverifiedKeys.length > 0) && (
                          <p className={`text-xs mt-2 px-1 ${textMuted}`}>
                            <i className="ri-information-line mr-1"></i>
                            평균 산출 제외국:
                            {summary.excludedKeys.length > 0 && (
                              <> {summary.excludedKeys.map(k => `${countryFlag(k)} ${countryLabel(k)}`).join(' · ')} — 조정가 미산출 (가격 미공개/미등재/환율 미적용)</>
                            )}
                            {summary.excludedKeys.length > 0 && summary.unverifiedKeys.length > 0 && <> ·</>}
                            {summary.unverifiedKeys.length > 0 && (
                              // 조정가는 있지만 팩 가격이 단위가로 저장된 미확인 행 — 숫자는 카드에 남기되 평균/최소/최고에서 뺀다
                              <> {summary.unverifiedKeys.map(k => `${countryFlag(k)} ${countryLabel(k)}`).join(' · ')} — {a8ExcludeReasonLabel(summary.excludedReasons[summary.unverifiedKeys[0]])} (팩 가격이 단위가로 저장)</>
                            )}
                            . 아래 '조정가 산출 로직'에서 사유 확인.
                          </p>
                        )}
                      </div>
                    )}

                    {/* ── A8 국가별 조정가 그래프 ── */}
                    {chartData.length > 0 && (
                      <div className={`rounded-2xl border overflow-hidden ${cardBg} ${cardBorder}`}>
                        <div className={`px-5 py-4 border-b ${divider} flex items-center justify-between`}>
                          <div>
                            <h3 className={`font-bold text-sm ${textMain}`}>국가별 조정가 비교</h3>
                            <p className={`text-xs mt-0.5 ${textMuted}`}>최소단위(정/바이알)당 국내 조정가 KRW — 낮은 순 정렬, 점선 = {chartData.length}개국 평균</p>
                          </div>
                          <div className="flex items-center gap-3">
                            <span className="flex items-center gap-1 text-xs"><span className="w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: chartMin }}></span><span className={textMuted}>최저</span></span>
                            <span className="flex items-center gap-1 text-xs"><span className="w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: chartMax }}></span><span className={textMuted}>최고</span></span>
                          </div>
                        </div>
                        <div className="px-3 pt-4 pb-2" style={{ height: 280 }}>
                          <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={chartData} margin={{ top: 24, right: 24, left: 8, bottom: 0 }}>
                              <XAxis dataKey="name" tick={{ fill: chartAxis, fontSize: 12 }} axisLine={{ stroke: chartGrid }} tickLine={false}
                                tickFormatter={(name: string) => { const d = chartData.find(x => x.name === name); return d ? `${d.flag} ${name}` : name; }} />
                              <YAxis tick={{ fill: chartAxis, fontSize: 11 }} axisLine={false} tickLine={false}
                                tickFormatter={(v: number) => `${Math.round(v / 10000).toLocaleString()}만`} width={56} />
                              <Tooltip cursor={{ fill: isDark ? '#1E253060' : '#F3F4F6' }}
                                contentStyle={{ backgroundColor: isDark ? '#161B27' : '#FFFFFF', border: `1px solid ${chartGrid}`, borderRadius: 12, fontSize: 12 }}
                                labelStyle={{ color: isDark ? '#FFFFFF' : '#111827', fontWeight: 700 }}
                                formatter={(value) => [formatKrw(Number(value)) ?? '-', '조정가 (최소단위당)']} />
                              {summary && <ReferenceLine y={summary.avgKrw} stroke={chartAvgLine} strokeDasharray="6 4"
                                label={{ value: `평균 ${formatKrw(summary.avgKrw)}`, position: 'insideTopRight', fill: chartAvgLine, fontSize: 11, fontWeight: 700 }} />}
                              <Bar dataKey="adj" radius={[8, 8, 0, 0]} maxBarSize={56}>
                                {chartData.map(d => (
                                  <Cell key={d.key}
                                    fill={summary && d.key === summary.minCountryKey ? chartMin
                                      : summary && d.key === summary.maxCountryKey ? chartMax : chartBar} />
                                ))}
                                <LabelList dataKey="adj" position="top"
                                  formatter={(v) => formatKrw(Number(v)) ?? ''}
                                  style={{ fill: isDark ? '#C9D1D9' : '#374151', fontSize: 11, fontWeight: 700 }} />
                              </Bar>
                            </BarChart>
                          </ResponsiveContainer>
                        </div>
                      </div>
                    )}

                    {/* ── A8 국가 카드 그리드 ── */}
                    <div className={`rounded-2xl border overflow-hidden ${cardBg} ${cardBorder}`}>
                      <div className={`px-5 py-4 border-b ${divider}`}>
                        <h3 className={`font-bold text-sm ${textMain}`}>A8 국가 급여 약가</h3>
                        <p className={`text-xs mt-0.5 ${textMuted}`}>미국 · 영국 · 독일 · 프랑스 · 캐나다 · 일본 · 이탈리아 · 스위스 — 급여 배지는 공개 DB에서 확인된 경우에만 표시</p>
                      </div>
                      <div className="grid grid-cols-4 gap-0">
                        {A8_COUNTRIES.map((country, idx) => {
                          const cell = activeA8[country.key];
                          const cov = pricing.data?.coverageNotes[country.key];
                          return (
                            <div key={country.key} className={`relative p-5 ${idx % 4 !== 3 ? 'border-r' : ''} ${idx >= 4 ? 'border-t' : ''} ${divider}`}>
                              <div className="flex items-center gap-2 mb-3"><span className="text-xl">{country.flag}</span><div className="flex-1 min-w-0"><p className={`text-xs font-semibold ${textMain}`}>{country.label}</p><p className={`text-xs ${textMuted}`}>{country.currency}</p></div>
                                {cell?.reimbursed && <span className="text-xs px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-600 border border-emerald-200 font-semibold whitespace-nowrap" title={`급여 ${cell.reimbursedLabel}`}>급여{cell.reimbursedLabel === '조건부' ? '(조건부)' : ''}</span>}
                                <button
                                  onClick={() => researchCountry(country.key)}
                                  title={`${country.label} 조사 요청 접수 (위 패널의 이메일 사용)`}
                                  className={`w-6 h-6 flex items-center justify-center rounded-lg shrink-0 transition-colors ${textMuted} ${isDark ? 'hover:bg-[#1E2530] hover:text-teal-400' : 'hover:bg-gray-100 hover:text-teal-600'} cursor-pointer`}>
                                  <i className="text-sm ri-refresh-line"></i>
                                </button>
                              </div>
                              {cell ? (
                                <>
                                  {cell.price != null && cell.price > 0 ? (
                                    <p className={`text-lg font-bold mb-1 ${accentColor}`} title={cell.productName}>{getCurrencySymbol(cell.currency)}{cell.price.toLocaleString()}</p>
                                  ) : (
                                    <p className={`text-sm font-semibold mb-1 ${textSub}`}>가격 미공개</p>
                                  )}
                                  {cell.dosageStrength && <p className={`text-xs truncate ${textSub}`} title={cell.dosageStrength}>{cell.dosageStrength}{cell.packCount && cell.packCount > 1 ? ` × ${cell.packCount}` : ''}</p>}
                                  {cell.doseNormFactor != null && cell.doseNormFactor !== 1 && cell.adjustedPriceKrwNormalized != null ? (
                                    <>
                                      <p className={`text-xs mt-1 font-semibold ${textSub}`}>조정가{cell.referenceStrengthMg ? ` (${cell.referenceStrengthMg}mg 기준)` : ''} {formatKrw(cell.adjustedPriceKrwNormalized)}</p>
                                      <p className={`text-xs ${textMuted}`} title={cell.doseNormNote || ''}>↳ 용량보정 {cell.doseNormNote || `×${cell.doseNormFactor}`} · 원본 {cell.adjustedPriceKrw != null ? formatKrw(cell.adjustedPriceKrw) : '-'}</p>
                                    </>
                                  ) : (
                                    cell.adjustedPriceKrw != null && <p className={`text-xs mt-1 ${textSub}`}>조정가 {formatKrw(cell.adjustedPriceKrw)}</p>
                                  )}
                                  {cell.dailyCostKrw != null && <p className={`text-xs ${textSub}`}>일일 {formatKrw(cell.dailyCostKrw)}{cell.dosingScheduleLabel ? ` (${cell.dosingScheduleLabel})` : ''}</p>}
                                  {cell.reimbursed && cell.reimbursedDate && <p className={`text-xs mt-1 ${textMuted}`}>급여 결정: {cell.reimbursedDate}</p>}
                                  {!cell.reimbursedKnown && <p className={`text-xs mt-1 ${textMuted}`}>급여정보 없음</p>}
                                  {(cell.price == null || cell.price <= 0) && (cov?.policy || cell.note) && <p className={`text-xs mt-1 leading-relaxed ${textMuted}`}>{cov?.policy || cell.note}</p>}
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

                    {/* ── 조정가 산출 로직 (사후관리) ── */}
                    <div className={`rounded-2xl border overflow-hidden ${cardBg} ${cardBorder}`}>
                      <button onClick={() => setShowCalcLogic(!showCalcLogic)}
                        className={`w-full flex items-center gap-3 px-5 py-4 text-left cursor-pointer transition-colors ${isDark ? 'hover:bg-[#1E2530]/50' : 'hover:bg-gray-50'}`}>
                        <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-function-line"></i></span>
                        <div className="flex-1 min-w-0">
                          <h3 className={`font-bold text-sm ${textMain}`}>조정가 산출 로직 <span className={`font-medium ${textMuted}`}>(사후관리)</span></h3>
                          <p className={`text-xs mt-0.5 ${textMuted}`}>국가별 환율·공장도율·가산 단계 상세 — 재정영향분석 표준 공식 검증용</p>
                        </div>
                        {fxWindow && <span className={`text-xs px-2 py-1 rounded-full whitespace-nowrap ${pillBg} ${pillText}`}>환율 기간 {fxWindow}</span>}
                        <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className={`text-sm transition-transform duration-200 ${showCalcLogic ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'}`}></i></span>
                      </button>
                      {showCalcLogic && (
                        <div className={`border-t ${divider}`}>
                          {/* 공식 설명 */}
                          <div className={`px-5 py-4 ${tableHeadBg}`}>
                            <p className={`text-xs font-semibold mb-1.5 ${textMain}`}>표준 공식 (재정영향분석 기준, 최소단위당)</p>
                            <p className={`text-xs font-mono leading-relaxed ${textSub}`}>
                              조정가 = 표시가 ÷ 포장수량 × 환율(KEB 36개월 평균) × 공장도율(국가별) × 1.10<span className={textMuted}>(KR VAT)</span> × 1.0869<span className={textMuted}>(KR 유통거래폭)</span>
                            </p>
                            <p className={`text-xs mt-1.5 leading-relaxed ${textMuted}`}>
                              VAT 10%·유통거래폭 8.69%는 해외 공장도가를 <b>한국 약가 등가</b>로 환산하기 위한 한국 기준 상수 (국가별 VAT 아님).
                              공장도율은 해당국 소매가→출하가 환산 비율이며 자료원에 따라 오버라이드됩니다 (IT Class H=1.0 등).
                            </p>
                          </div>
                          {calcRows.length === 0 ? (
                            <p className={`px-5 py-4 text-xs ${textMuted}`}>조정가 산출 내역이 있는 국가가 없습니다.</p>
                          ) : (
                            <div className="overflow-x-auto">
                              <table className="w-full text-xs">
                                <thead>
                                  <tr className={`${tableHeadBg} border-b ${divider}`}>
                                    <th className={`text-left px-4 py-2.5 font-semibold whitespace-nowrap ${textSub}`}>국가</th>
                                    <th className={`text-right px-3 py-2.5 font-semibold whitespace-nowrap ${textSub}`}>표시가 (현지)</th>
                                    <th className={`text-right px-3 py-2.5 font-semibold whitespace-nowrap ${textSub}`}>포장</th>
                                    <th className={`text-right px-3 py-2.5 font-semibold whitespace-nowrap ${textSub}`}>단위가 (현지)</th>
                                    <th className={`text-right px-3 py-2.5 font-semibold whitespace-nowrap ${textSub}`}>환율</th>
                                    <th className={`text-right px-3 py-2.5 font-semibold whitespace-nowrap ${textSub}`}>KRW 환산</th>
                                    <th className={`text-right px-3 py-2.5 font-semibold whitespace-nowrap ${textSub}`}>공장도율</th>
                                    <th className={`text-right px-3 py-2.5 font-semibold whitespace-nowrap ${textSub}`}>공장도가</th>
                                    <th className={`text-right px-3 py-2.5 font-semibold whitespace-nowrap ${textSub}`}>+VAT 10%</th>
                                    <th className={`text-right px-4 py-2.5 font-semibold whitespace-nowrap ${textSub}`}>조정가 (+유통 8.69%)</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {calcRows.map(({ meta, cell }) => {
                                    const c = cell.calc!;
                                    return (
                                      <tr key={meta.key} className={`border-b last:border-0 ${divider}`}>
                                        <td className={`px-4 py-2.5 whitespace-nowrap font-semibold ${textMain}`}>{meta.flag} {meta.label}</td>
                                        <td className={`px-3 py-2.5 text-right tabular-nums whitespace-nowrap ${textSub}`}>{getCurrencySymbol(cell.currency)}{c.listedPrice.toLocaleString()}</td>
                                        <td className={`px-3 py-2.5 text-right tabular-nums ${textSub}`}>{c.packCount > 1 ? `${c.packCount}개입` : '단위'}</td>
                                        <td className={`px-3 py-2.5 text-right tabular-nums whitespace-nowrap ${textSub}`}>{getCurrencySymbol(cell.currency)}{Number(c.perUnitLocal.toFixed(2)).toLocaleString()}</td>
                                        <td className={`px-3 py-2.5 text-right tabular-nums whitespace-nowrap ${textSub}`} title={`KEB 36개월 평균 ${fxMonth(c.fxFrom)}~${fxMonth(c.fxTo)}`}>{c.exchangeRate.toLocaleString()}</td>
                                        <td className={`px-3 py-2.5 text-right tabular-nums whitespace-nowrap ${textSub}`}>{formatKrw(c.krwConverted)}</td>
                                        <td className={`px-3 py-2.5 text-right tabular-nums ${textSub}`} title={c.factoryRatioLabel || ''}>{c.factoryRatio != null ? `× ${c.factoryRatio}` : '-'}</td>
                                        <td className={`px-3 py-2.5 text-right tabular-nums whitespace-nowrap ${textSub}`}>{formatKrw(c.factoryPriceKrw)}</td>
                                        <td className={`px-3 py-2.5 text-right tabular-nums whitespace-nowrap ${textSub}`}>{formatKrw(c.vatAppliedKrw)}</td>
                                        <td className={`px-4 py-2.5 text-right tabular-nums whitespace-nowrap font-bold ${accentColor}`}>{formatKrw(c.adjustedPriceKrw)}</td>
                                      </tr>
                                    );
                                  })}
                                </tbody>
                              </table>
                            </div>
                          )}
                          {/* 산출 제외국 사유 */}
                          {summary && (summary.excludedKeys.length > 0 || summary.unverifiedKeys.length > 0) && (
                            <div className={`px-5 py-3.5 border-t ${divider}`}>
                              <p className={`text-xs font-semibold mb-1.5 ${textSub}`}>산출 제외국 사유</p>
                              <div className="space-y-1">
                                {summary.unverifiedKeys.map(k => (
                                  <p key={`unv-${k}`} className={`text-xs leading-relaxed ${textMuted}`}>
                                    {countryFlag(k)} <span className="font-semibold">{countryLabel(k)}</span> — {a8ExcludeReasonLabel(summary.excludedReasons[k])} (팩 가격이 단위가로 저장돼 평균 산출 제외 — 조정가는 카드에 표시)
                                  </p>
                                ))}
                                {summary.excludedKeys.map(k => {
                                  const cov = pricing.data?.coverageNotes[k];
                                  const cell = activeA8[k];
                                  const reason = cell == null ? '캐시 데이터 없음 (미수집 또는 미등재)'
                                    : cell.price == null ? (cov?.policy || '가격 미공개')
                                      : '환율/조정가 미산출';
                                  return (
                                    <p key={k} className={`text-xs leading-relaxed ${textMuted}`}>
                                      {countryFlag(k)} <span className="font-semibold">{countryLabel(k)}</span> — {reason}
                                    </p>
                                  );
                                })}
                              </div>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                )}
          </div>
        ) : (
          <div className={`rounded-2xl border py-16 text-center ${emptyCenter} ${cardBorder}`}>
            <span className={`w-12 h-12 flex items-center justify-center mx-auto mb-3 ${textMuted}`}><i className="ri-global-line text-4xl"></i></span>
            <p className={`text-sm mb-1 ${textSub}`}>검색 이력에서 제품을 선택하거나</p>
            <p className={`text-xs ${textMuted}`}>신규 검색으로 성분명 또는 제품명을 입력하세요</p>
          </div>
        )}
        </>}
      </div>

      {/* 리포트 다운로드 모달 — 형식 선택 + 국내 신청가 입력 */}
      {showReport && selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowReport(false)}>
          <div className={`w-[420px] rounded-2xl border p-6 ${cardBg} ${cardBorder}`} onClick={e => e.stopPropagation()}>
            <h3 className={`text-lg font-bold mb-1 ${textMain}`}>리포트 다운로드</h3>
            <p className={`text-xs mb-4 ${textSub}`}>{headerName} · A8 약가 비교표</p>

            <label className={`text-xs font-semibold ${textSub}`}>파일 형식</label>
            <div className="flex gap-2 mt-1 mb-4">
              {(['xlsx', 'hwpx'] as const).map(f => (
                <button key={f} onClick={() => setReportFmt(f)}
                  className={`flex-1 text-sm py-2 rounded-lg border ${reportFmt === f ? 'bg-teal-600 text-white border-teal-600' : `${textSub} ${cardBorder}`}`}>
                  {f === 'xlsx' ? '엑셀 (.xlsx)' : '한글 (.hwpx)'}</button>
              ))}
            </div>

            <label className={`text-xs font-semibold ${textSub}`}>국내 신청가격 (선택 — 비율 계산용)</label>
            <div className="grid grid-cols-2 gap-2 mt-1 mb-1">
              <input value={appliedDisplay} onChange={e => setAppliedDisplay(e.target.value.replace(/[^0-9]/g, ''))}
                placeholder="표시가 /정" className={`border rounded-lg px-3 py-2 text-sm ${cardBorder} bg-transparent ${textMain}`} />
              <input value={appliedActual} onChange={e => setAppliedActual(e.target.value.replace(/[^0-9]/g, ''))}
                placeholder="실제가 /정" className={`border rounded-lg px-3 py-2 text-sm ${cardBorder} bg-transparent ${textMain}`} />
            </div>
            <p className={`text-[11px] mb-4 ${textMuted}`}>※ 실제가(net)는 RSA 등으로 비공개일 수 있으며, 비율 = 조정가 ÷ 실제가.</p>

            {reportErr && <p className="text-xs text-red-500 mb-2">{reportErr}</p>}
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowReport(false)} className={`text-sm px-4 py-2 rounded-lg border ${cardBorder} ${textSub}`}>취소</button>
              <button disabled={reportBusy}
                onClick={async () => {
                  setReportBusy(true); setReportErr('');
                  try {
                    await downloadForeignReport(selected.query, reportFmt,
                      appliedDisplay ? Number(appliedDisplay) : null,
                      appliedActual ? Number(appliedActual) : null);
                    setShowReport(false);
                  } catch (e) { setReportErr(e instanceof Error ? e.message : '다운로드 실패'); }
                  finally { setReportBusy(false); }
                }}
                className="text-sm px-4 py-2 rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50">
                {reportBusy ? '생성 중…' : '다운로드'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
