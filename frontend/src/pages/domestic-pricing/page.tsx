import { useState, useEffect, useMemo } from 'react';
import {
  searchDomesticPriceChanges,
  downloadDomesticExport,
  fetchChangeReason,
  enrichBulk,
  DomesticProduct,
  DomesticPriceHistoryEntry,
  ChangeReasonResult,
  EnrichmentResult,
  EnrichmentRequestItem,
} from '@/api/domestic';
import PriceWaterfall from './components/PriceWaterfall';
import AnalogueCompareModal from './components/AnalogueCompareModal';

const DOSAGE_CLAMP = 220;

export default function DomesticPricingPage() {
  const [isDark, setIsDark] = useState(false);
  const [search, setSearch] = useState('');
  const [products, setProducts] = useState<DomesticProduct[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedDrug, setSelectedDrug] = useState<DomesticProduct | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [selectedAnalogues, setSelectedAnalogues] = useState<string[]>([]);
  const [downloading, setDownloading] = useState(false);
  const [dosageExpanded, setDosageExpanded] = useState(false);
  // 변동사유 캐시 — key: `${insurance_code}|${date}` (lazy: 행의 "사유 분석" 클릭 시에만 조회)
  const [reasons, setReasons] = useState<Record<string, { data?: ChangeReasonResult; loading: boolean; error?: string }>>({});
  const [expandedRefs, setExpandedRefs] = useState<Set<string>>(new Set());
  // on-demand enrichment — key: normalized_name. DB JOIN 값 우선, 없을 때만 live 로 채움
  const [liveEnrich, setLiveEnrich] = useState<Record<string, EnrichmentResult>>({});
  const [enrichingSet, setEnrichingSet] = useState<Set<string>>(new Set());

  // 검색 디바운스 300ms — 응답 역전 방지 seq 가드
  useEffect(() => {
    const q = search.trim();
    if (q.length < 2) {
      setProducts([]);
      setError(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    let cancelled = false;
    const handle = setTimeout(async () => {
      try {
        const result = await searchDomesticPriceChanges(q);
        if (cancelled) return;
        setProducts(result);
      } catch (e: any) {
        if (cancelled) return;
        setError(e?.message || '검색 실패');
        setProducts([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 300);
    return () => { cancelled = true; clearTimeout(handle); };
  }, [search]);

  // 검색 결과가 바뀌면 사라진 선택 해제
  useEffect(() => {
    if (selectedDrug && !products.some(p => p.id === selectedDrug.id)) {
      setSelectedDrug(null);
      setSelectedAnalogues([]);
    }
  }, [products, selectedDrug]);

  // 선택된 기준약제 + 비교약제 enrichment (캐시 우선 — 허가일·일일투약비 보강)
  useEffect(() => {
    if (!selectedDrug) return;
    const targets: EnrichmentRequestItem[] = [];
    const push = (norm: string | undefined, product_name: string, ingredient: string, price: number, code: string, codes: string[] = []) => {
      if (!norm || liveEnrich[norm] || enrichingSet.has(norm)) return;
      targets.push({ normalized_name: norm, product_name, ingredient, current_price: price, code, codes });
    };
    push(selectedDrug.normalizedName, selectedDrug.fullProductName, selectedDrug.ingredient,
      selectedDrug.currentPrice, selectedDrug.insuranceCode, selectedDrug.mergedCodes);
    for (const name of selectedAnalogues) {
      const a = (selectedDrug.analogues || []).find(x => x.name === name);
      if (a?.normalizedName) push(a.normalizedName, a.name, a.ingredient || '', a.price, a.insuranceCode || '', a.mergedCodes || []);
    }
    if (targets.length === 0) return;
    const norms = targets.map(t => t.normalized_name);
    setEnrichingSet(prev => { const s = new Set(prev); norms.forEach(n => s.add(n)); return s; });
    enrichBulk(targets)
      .then(results => setLiveEnrich(prev => ({ ...prev, ...results })))
      .catch(e => console.warn('[enrichBulk] 실패:', e))
      .finally(() => setEnrichingSet(prev => { const s = new Set(prev); norms.forEach(n => s.delete(n)); return s; }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDrug, selectedAnalogues]);

  const handleSelect = (drug: DomesticProduct) => {
    setSelectedDrug(drug);
    setSelectedAnalogues([]);
    setDosageExpanded(false);
  };
  const handleToggleAnalogue = (name: string) => {
    setSelectedAnalogues(prev => prev.includes(name) ? prev.filter(n => n !== name) : [...prev, name]);
  };
  const toggleRefs = (key: string) => {
    setExpandedRefs(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  // 변동사유 lazy 분석 (PubMed·HIRA·MA 전문지 — 캐시 우선, 미스 시 20~40초)
  const analyzeReason = (h: DomesticPriceHistoryEntry, refresh = false) => {
    if (!selectedDrug) return;
    const key = `${selectedDrug.insuranceCode}|${h.date}`;
    if (reasons[key]?.loading) return;
    setReasons(prev => ({ ...prev, [key]: { loading: true } }));
    fetchChangeReason({
      drug: selectedDrug.productName,
      date: h.date,
      ingredient: selectedDrug.ingredient,
      insuranceCode: selectedDrug.insuranceCode,
      deltaPct: h.changeRate,
      refresh,
    })
      .then(r => setReasons(prev => ({ ...prev, [key]: { data: r, loading: false } })))
      .catch(e => setReasons(prev => ({ ...prev, [key]: { loading: false, error: e?.message || 'error' } })));
  };

  const handleDownload = async () => {
    const q = search.trim();
    if (!q) return;
    setDownloading(true);
    try {
      await downloadDomesticExport(q, 'xlsx');
    } catch (e: any) {
      setError(e?.message || '다운로드 실패');
    } finally {
      setDownloading(false);
    }
  };

  // 관련도 정렬 — 제품명 > 성분명
  const sortedProducts = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return products;
    const score = (item: DomesticProduct) => {
      const name = (item.productName || '').toLowerCase();
      const ing = (item.ingredient || '').toLowerCase();
      const nameScore = name === q ? 0 : name.startsWith(q) ? 1 : name.includes(q) ? 50 + name.indexOf(q) : Infinity;
      const ingScore = ing === q ? 200 : ing.startsWith(q) ? 201 : ing.includes(q) ? 400 + ing.indexOf(q) : Infinity;
      return Math.min(nameScore, ingScore);
    };
    return [...products].sort((a, b) => {
      const sa = score(a); const sb = score(b);
      if (sa !== sb) return sa - sb;
      return a.productName.localeCompare(b.productName);
    });
  }, [products, search]);

  // DB JOIN 값 우선, 없을 때만 live enrichment 로 보강 (날조 금지 — 실패 시 null 유지)
  const liveFill = (norm: string | undefined, dbDaily: number | null) => {
    if (dbDaily != null || !norm) return dbDaily;
    const live = liveEnrich[norm];
    if (!live || live.is_failure) return null;
    return live.treatment_cost?.daily ?? null;
  };

  const baseLive = selectedDrug ? liveEnrich[selectedDrug.normalizedName] : undefined;
  const detailDaily = selectedDrug ? (selectedDrug.dailyCost ?? (baseLive && !baseLive.is_failure ? baseLive.treatment_cost?.daily ?? null : null)) : null;
  const detailMonthly = selectedDrug ? (selectedDrug.monthlyCost ?? (baseLive && !baseLive.is_failure ? baseLive.treatment_cost?.monthly ?? null : null)) : null;
  const detailYearly = selectedDrug ? (selectedDrug.yearlyCost ?? (baseLive && !baseLive.is_failure ? baseLive.treatment_cost?.annual ?? null : null)) : null;
  const detailApproval = selectedDrug ? (selectedDrug.firstApprovalDate ?? (baseLive && !baseLive.is_failure ? baseLive.approval_date ?? null : null)) : null;

  const compareList = selectedDrug ? [
    { name: selectedDrug.productName, price: selectedDrug.currentPrice, dailyCost: detailDaily, isBase: true },
    ...(selectedDrug.analogues || []).filter(a => selectedAnalogues.includes(a.name)).map(a => ({
      name: a.name, price: a.price, dailyCost: liveFill(a.normalizedName, a.dailyCost), isBase: false,
    })),
  ] : [];

  const showEmptyPrompt = search.trim().length < 2;
  const dosageText = selectedDrug?.dosage ?? null;
  const dosageDisplay = dosageText
    ? (dosageExpanded || dosageText.length <= DOSAGE_CLAMP ? dosageText : dosageText.slice(0, DOSAGE_CLAMP) + '…')
    : '정보 없음';

  const pageBg = isDark ? 'bg-[#0D1117]' : 'bg-gray-50';
  const headerBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const cardBg = isDark ? 'bg-[#161B27]' : 'bg-white';
  const cardBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const tableHeaderBg = isDark ? 'bg-[#1E2530]' : 'bg-gray-100';
  const tableBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const tableStripe = isDark ? 'bg-[#1A2035]/20' : 'bg-gray-50/50';
  const tableHover = isDark ? 'hover:bg-[#00E5CC]/5' : 'hover:bg-teal-50/50';
  const tableSelected = isDark ? 'bg-[#00E5CC]/10' : 'bg-teal-50';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
  const textMuted = isDark ? 'text-[#4A5568]' : 'text-gray-400';
  const accentColor = isDark ? 'text-[#00E5CC]' : 'text-teal-600';
  const miniStatBg = isDark ? 'bg-[#1E2530]' : 'bg-gray-100';
  const compareBaseBg = isDark ? 'border-[#00E5CC]/40 bg-[#00E5CC]/5' : 'border-teal-300 bg-teal-50';
  const compareItemBg = isDark ? 'bg-[#1E2530] border-[#1E2530]' : 'bg-gray-100 border-gray-200';
  const searchBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const searchFocus = isDark ? 'focus-within:border-[#00E5CC]/50' : 'focus-within:border-teal-300';
  const searchText = isDark ? 'text-white placeholder-[#4A5568]' : 'text-gray-900 placeholder-gray-400';
  const emptyBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const errorBanner = isDark ? 'bg-red-400/10 border-red-400/30 text-red-400' : 'bg-red-50 border-red-200 text-red-600';
  const mechBadge = isDark ? 'bg-[#00E5CC]/10 text-[#00E5CC] border-[#00E5CC]/20' : 'bg-teal-50 text-teal-700 border-teal-200';
  const reasonBtn = isDark
    ? 'border-[#2A3545] bg-[#1E2530] text-[#8B9BB4] hover:text-white hover:border-[#00E5CC]'
    : 'border-gray-300 bg-gray-100 text-gray-600 hover:text-gray-900 hover:border-teal-400';
  const reasonBody = isDark ? 'text-[#B0BCC9]' : 'text-gray-600';
  const evidenceBox = isDark ? 'bg-[#0D1117] border-[#1E2530] text-[#8B9BB4]' : 'bg-gray-50 border-gray-200 text-gray-500';

  const confBadge = (conf: string) => {
    const c = (conf || 'low').toLowerCase();
    if (c === 'high') return { cls: isDark ? 'bg-emerald-400/10 text-emerald-400 border-emerald-400/20' : 'bg-emerald-50 text-emerald-600 border-emerald-200', label: '높음' };
    if (c === 'medium') return { cls: isDark ? 'bg-amber-400/10 text-amber-300 border-amber-400/20' : 'bg-amber-50 text-amber-600 border-amber-200', label: '보통' };
    return { cls: isDark ? 'bg-red-400/10 text-red-400 border-red-400/20' : 'bg-red-50 text-red-600 border-red-200', label: '낮음' };
  };

  // 출처 칩 — 식약처 공공데이터 실측 vs LLM 보강 추정 (date_source 원칙)
  const sourceChip = (src: 'mfds_official' | 'estimate' | null) =>
    src === 'mfds_official' ? (
      <span className={`ml-1.5 text-[10px] px-1.5 py-0.5 rounded border font-semibold ${isDark ? 'bg-emerald-400/10 text-emerald-400 border-emerald-400/20' : 'bg-emerald-50 text-emerald-600 border-emerald-200'}`} title="식약처 의약품 허가정보 API 실측값">식약처 공식</span>
    ) : src === 'estimate' ? (
      <span className={`ml-1.5 text-[10px] px-1.5 py-0.5 rounded border font-semibold ${isDark ? 'bg-amber-400/10 text-amber-300 border-amber-400/20' : 'bg-amber-50 text-amber-600 border-amber-200'}`} title="LLM 보강 추정값 — 식약처 공식 데이터 미확인">추정</span>
    ) : null;

  return (
    <div className={`min-h-screen ${pageBg} ${isDark ? 'text-white' : 'text-gray-900'}`}>
      {/* Header */}
      <div className={`px-8 pt-8 pb-6 border-b ${headerBorder}`}>
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-price-tag-3-line"></i></span>
              <h1 className={`text-2xl font-bold ${textMain}`}>국내약가</h1>
            </div>
            <p className={`${textSub} text-sm`}>건강보험 등재 약가 상세 정보 및 변동 이력</p>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={handleDownload} disabled={!search.trim() || products.length === 0 || downloading}
              className="flex items-center gap-2 bg-teal-600 text-white text-sm font-semibold px-4 py-2 rounded-lg cursor-pointer whitespace-nowrap hover:bg-teal-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
              <span className="w-4 h-4 flex items-center justify-center">
                <i className={downloading ? 'ri-loader-4-line animate-spin text-sm' : 'ri-download-2-line text-sm'}></i>
              </span>{downloading ? '내려받는 중...' : '엑셀 다운로드'}
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
        {/* Search */}
        <div className={`rounded-2xl border px-5 py-4 flex items-center gap-3 ${searchBg} ${searchFocus} transition-colors`}>
          <span className={`w-5 h-5 flex items-center justify-center ${loading ? accentColor : textSub}`}>
            <i className={loading ? 'ri-loader-4-line animate-spin text-base' : 'ri-search-line text-base'}></i>
          </span>
          <input type="text" placeholder="제품명, 성분명, 보험코드로 검색 (2자 이상)..." value={search} onChange={e => setSearch(e.target.value)}
            className={`bg-transparent text-sm focus:outline-none flex-1 ${searchText}`} />
          {search && <button onClick={() => setSearch('')} className={`w-5 h-5 flex items-center justify-center cursor-pointer transition-colors ${textMuted} hover:${textMain}`}><i className="ri-close-line text-sm"></i></button>}
        </div>

        {error && (
          <div className={`rounded-xl border px-4 py-3 flex items-center gap-2 ${errorBanner}`}>
            <i className="ri-error-warning-line text-sm"></i>
            <p className="text-xs">{error}</p>
          </div>
        )}

        {showEmptyPrompt ? (
          <div className={`rounded-2xl border py-16 text-center ${emptyBg} ${cardBorder}`}>
            <span className={`w-12 h-12 flex items-center justify-center mx-auto mb-3 ${textMuted}`}><i className="ri-search-line text-4xl"></i></span>
            <p className={`text-sm ${textSub}`}>제품명, 성분명 또는 보험코드를 2자 이상 입력하세요</p>
            <p className={`text-xs mt-1 ${textMuted}`}>예: 키트루다, 자누비아, 펨브롤리주맙</p>
          </div>
        ) : (
          <>
            {/* Product List */}
            <div className={`rounded-2xl border overflow-hidden ${cardBg} ${cardBorder}`}>
              <div className={`px-5 py-3 border-b ${tableBorder} flex items-center justify-between`}>
                <p className={`text-xs ${textSub}`}>총 <span className={`font-semibold ${textMain}`}>{products.length}</span>개 품목</p>
                <p className={`text-xs ${textMuted}`}>클릭하여 상세 정보 확인</p>
              </div>
              {loading && products.length === 0 ? (
                <div className={`text-center py-12 ${textMuted}`}>
                  <span className="w-8 h-8 flex items-center justify-center mx-auto mb-2"><i className={`ri-loader-4-line animate-spin text-2xl ${accentColor}`}></i></span>
                  <p className={`text-sm ${textSub}`}>HIRA 약가 이력 검색 중...</p>
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className={tableHeaderBg}>
                        <th className={`text-left text-xs font-semibold px-5 py-3 whitespace-nowrap ${textSub}`}>제품명</th>
                        <th className={`text-left text-xs font-semibold px-4 py-3 whitespace-nowrap ${textSub}`}>성분명</th>
                        <th className={`text-left text-xs font-semibold px-4 py-3 whitespace-nowrap ${textSub}`}>보험코드</th>
                        <th className={`text-left text-xs font-semibold px-4 py-3 whitespace-nowrap ${textSub}`}>제형/함량</th>
                        <th className={`text-right text-xs font-semibold px-4 py-3 whitespace-nowrap ${textSub}`}>현재 상한금액</th>
                        <th className={`text-center text-xs font-semibold px-4 py-3 whitespace-nowrap ${textSub}`}>변동률</th>
                        <th className={`text-center text-xs font-semibold px-4 py-3 whitespace-nowrap ${textSub}`}>RSA</th>
                        <th className={`text-left text-xs font-semibold px-4 py-3 whitespace-nowrap ${textSub}`}>최종 변경일</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sortedProducts.map((item, idx) => (
                        <tr key={item.id} onClick={() => handleSelect(item)}
                          className={`border-t ${tableBorder} transition-colors cursor-pointer ${tableHover} ${selectedDrug?.id === item.id ? tableSelected : idx % 2 === 1 ? tableStripe : ''}`}>
                          <td className={`px-5 py-3 text-sm font-medium whitespace-nowrap ${textMain}`}>{item.productName}</td>
                          <td className={`px-4 py-3 text-sm whitespace-nowrap ${textSub}`}>{item.ingredient || '-'}</td>
                          <td className={`px-4 py-3 text-xs font-mono whitespace-nowrap ${textSub}`}>{item.insuranceCode}</td>
                          <td className="px-4 py-3 whitespace-nowrap"><span className={`text-xs px-2 py-1 rounded-full ${isDark ? 'bg-[#1E2530] text-[#8B9BB4]' : 'bg-gray-100 text-gray-600'}`}>{item.category || '-'}</span></td>
                          <td className={`px-4 py-3 text-sm font-semibold text-right whitespace-nowrap ${textMain}`}>₩{item.currentPrice.toLocaleString()}</td>
                          <td className="px-4 py-3 text-center whitespace-nowrap">
                            {item.change !== null ? (
                              <span className={`text-xs font-semibold px-2 py-1 rounded-full ${item.change < 0 ? 'text-red-500 bg-red-50 border border-red-200' : item.change > 0 ? 'text-emerald-500 bg-emerald-50 border border-emerald-200' : `${textSub} bg-gray-50`}`}>
                                {item.change > 0 ? '+' : ''}{item.change}%
                              </span>
                            ) : <span className={`text-xs ${textMuted}`}>-</span>}
                          </td>
                          <td className="px-4 py-3 text-center whitespace-nowrap">
                            {item.hasRSA ? <span className="text-xs px-2 py-1 rounded-full bg-amber-50 text-amber-600 font-semibold border border-amber-200" title="위험분담제 — 표시가 ≠ 실제가 (환급·총액제한 차액 비공개)">RSA</span> : <span className={`text-xs ${textMuted}`}>-</span>}
                          </td>
                          <td className={`px-4 py-3 text-xs whitespace-nowrap ${textSub}`}>{item.lastUpdated}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!loading && products.length === 0 && (
                    <div className={`text-center py-12 ${textMuted}`}>
                      <span className="w-8 h-8 flex items-center justify-center mx-auto mb-2"><i className="ri-search-line text-2xl"></i></span>
                      <p className="text-sm">검색 결과가 없습니다</p>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Detail Panel */}
            {selectedDrug && (
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div className={`rounded-2xl border p-5 ${cardBg} ${cardBorder}`}>
                    <div className="flex items-center gap-2 mb-4">
                      <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-information-line"></i></span>
                      <h3 className={`font-bold text-sm ${textMain}`}>기본 정보</h3>
                      <span className={`ml-auto text-xs font-semibold ${accentColor}`}>{selectedDrug.productName}</span>
                    </div>
                    <p className={`text-[10px] -mt-3 mb-3 ${textMuted}`}>출처: 심평원(HIRA) 약제급여목록 고시</p>
                    <div className="space-y-3">
                      {[
                        { label: '최초 약가 등재일', value: selectedDrug.firstRegistDate },
                        { label: '현재 상한금액', value: `₩${selectedDrug.currentPrice.toLocaleString()}`, highlight: true },
                        { label: '약가 변동 이력 횟수', value: `${selectedDrug.priceChangeCount}회` },
                        { label: '최초 약가 대비 변동률', value: `${selectedDrug.changeRateFromFirst > 0 ? '+' : ''}${selectedDrug.changeRateFromFirst}%`, change: selectedDrug.changeRateFromFirst },
                      ].map((row, i) => (
                        <div key={i} className={`flex items-center justify-between py-2 ${i < 3 ? `border-b ${tableBorder}` : ''}`}>
                          <span className={`text-xs ${textSub}`}>{row.label}</span>
                          <span className={`text-sm font-medium ${row.highlight ? accentColor + ' font-bold' : row.change !== undefined ? (row.change < 0 ? 'text-red-500' : 'text-emerald-500') + ' font-bold' : textMain}`}>{row.value}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className={`rounded-2xl border p-5 ${cardBg} ${cardBorder}`}>
                    <div className="flex items-center gap-2 mb-4">
                      <span className={`w-5 h-5 flex items-center justify-center text-purple-500`}><i className="ri-file-list-3-line"></i></span>
                      <h3 className={`font-bold text-sm ${textMain}`}>상세 정보</h3>
                      {selectedDrug.enrichmentConfidence && (
                        <span className={`ml-auto text-[10px] px-1.5 py-0.5 rounded ${miniStatBg} ${textMuted}`} title="enrichment 신뢰도">
                          enrich: {selectedDrug.enrichmentConfidence}
                        </span>
                      )}
                    </div>
                    <p className={`text-[10px] -mt-3 mb-3 ${textMuted}`}>출처: 식약처 의약품 허가정보 API 우선 · 부족분 보조 추정(칩 표시)</p>
                    <div className="space-y-3">
                      <div className={`flex items-center justify-between py-2 border-b ${tableBorder}`}>
                        <span className={`text-xs ${textSub}`}>RSA 여부</span>
                        <div className="flex items-center gap-2">
                          {selectedDrug.hasRSA ? (
                            <>
                              <span className="text-xs px-2 py-0.5 rounded-full bg-amber-50 text-amber-600 border border-amber-200 font-semibold" title="위험분담제 — 표시가 ≠ 실제가 (환급·총액제한 차액 비공개)">적용</span>
                              <span className={`text-xs ${textSub}`} title={selectedDrug.rsaNote || undefined}>{selectedDrug.rsaType || 'RSA'}</span>
                            </>
                          ) : selectedDrug.isRsa === 0 ? (
                            <span className={`text-xs ${textMuted}`}>해당없음</span>
                          ) : (
                            <span className={`text-xs ${textMuted}`}>정보 없음</span>
                          )}
                        </div>
                      </div>
                      <div className={`flex items-center justify-between py-2 border-b ${tableBorder}`}>
                        <span className={`text-xs flex-shrink-0 ${textSub}`}>약제평가위원회 문서</span>
                        <span className={`text-xs ${textMuted}`}>정보 없음</span>
                      </div>
                      <div className={`flex items-center justify-between py-2 border-b ${tableBorder}`}>
                        <span className={`text-xs flex-shrink-0 ${textSub}`}>최초 허가일{sourceChip(selectedDrug.approvalDateSource)}</span>
                        <span className={`text-xs ${detailApproval ? textMain : textMuted}`}>{detailApproval || '정보 없음'}</span>
                      </div>
                      <div className={`flex items-start justify-between py-2`}>
                        <span className={`text-xs flex-shrink-0 ${textSub}`}>용법용량{sourceChip(selectedDrug.usageSource)}</span>
                        <span className={`text-xs text-right ml-4 break-words ${dosageText ? textMain : textMuted}`}>
                          {dosageDisplay}
                          {dosageText && dosageText.length > DOSAGE_CLAMP && (
                            <button onClick={() => setDosageExpanded(!dosageExpanded)} className={`ml-1.5 cursor-pointer underline underline-offset-2 ${accentColor}`}>
                              {dosageExpanded ? '접기' : '더보기'}
                            </button>
                          )}
                        </span>
                      </div>
                      <div className="grid grid-cols-3 gap-2 pt-1">
                        {[
                          { label: '일치료비', value: detailDaily ? `₩${detailDaily.toLocaleString()}` : '-' },
                          { label: '월치료비', value: detailMonthly ? `₩${detailMonthly.toLocaleString()}` : '-' },
                          { label: '연치료비', value: detailYearly ? `₩${detailYearly.toLocaleString()}` : '-' },
                        ].map((s, i) => (
                          <div key={i} className={`rounded-lg p-2 text-center ${miniStatBg}`}>
                            <p className={`text-xs mb-1 ${textMuted}`}>{s.label}</p>
                            <p className={`text-xs font-bold ${textMain}`}>{s.value}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Compare */}
                <div className={`rounded-2xl border p-5 ${cardBg} ${cardBorder}`}>
                  <div className="flex items-center justify-between mb-4">
                    <div className="flex items-center gap-2">
                      <span className="w-5 h-5 flex items-center justify-center text-amber-500"><i className="ri-scales-3-line"></i></span>
                      <h3 className={`font-bold text-sm ${textMain}`}>약제 비교</h3>
                      <span className={`text-xs ${textMuted}`}>(최대 3개)</span>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className={`text-xs ${textSub}`}>동일성분 등재 품목: <span className={`font-semibold ${textMain}`}>{selectedDrug.sameIngredientCount}개</span></span>
                      <button onClick={() => setShowModal(true)} disabled={(selectedDrug.analogues || []).length === 0}
                        className={`flex items-center gap-1.5 border text-xs px-3 py-1.5 rounded-lg cursor-pointer transition-colors whitespace-nowrap disabled:opacity-40 disabled:cursor-not-allowed ${isDark ? 'bg-[#1E2530] border-[#2A3545] text-[#8B9BB4] hover:text-white' : 'bg-gray-100 border-gray-300 text-gray-600 hover:text-gray-900'}`}>
                        <span className="w-4 h-4 flex items-center justify-center"><i className="ri-add-line text-sm"></i></span>아날로그 선택
                      </button>
                    </div>
                  </div>
                  {compareList.length > 0 ? (
                    <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${compareList.length}, 1fr)` }}>
                      {compareList.map((item) => (
                        <div key={item.name} className={`rounded-xl p-4 border ${item.isBase ? compareBaseBg : compareItemBg}`}>
                          {item.isBase && <span className={`text-xs px-2 py-0.5 rounded-full font-semibold mb-2 inline-block ${isDark ? 'bg-[#00E5CC]/20 text-[#00E5CC]' : 'bg-teal-100 text-teal-700'}`}>기준</span>}
                          <p className={`text-sm font-bold mb-3 leading-snug ${textMain}`}>{item.name}</p>
                          <div className="space-y-2">
                            <div><p className={`text-xs mb-0.5 ${textMuted}`}>약가</p><p className={`text-base font-bold ${textMain}`}>₩{item.price.toLocaleString()}</p></div>
                            <div><p className={`text-xs mb-0.5 ${textMuted}`}>일치료비</p>
                              {item.dailyCost ? (
                                <>
                                  <p className={`text-sm font-semibold ${item.isBase ? accentColor : textMain}`}>₩{item.dailyCost.toLocaleString()}</p>
                                  {!item.isBase && compareList[0].dailyCost && (
                                    <p className={`text-xs mt-0.5 ${item.dailyCost > compareList[0].dailyCost ? 'text-red-500' : 'text-emerald-500'}`}>
                                      기준 대비 {item.dailyCost > compareList[0].dailyCost ? '+' : ''}{(((item.dailyCost - compareList[0].dailyCost) / compareList[0].dailyCost) * 100).toFixed(1)}%
                                    </p>
                                  )}
                                </>
                              ) : (
                                <p className={`text-xs ${textMuted}`}>{enrichingSet.size > 0 ? '조회 중...' : '정보 없음'}</p>
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className={`text-center py-8 ${textMuted}`}>
                      <span className="w-8 h-8 flex items-center justify-center mx-auto mb-2"><i className="ri-add-circle-line text-2xl"></i></span>
                      <p className="text-sm">{(selectedDrug.analogues || []).length === 0 ? '검색 결과 내 비교 가능한 약제가 없습니다 (성분명으로 검색하면 후보가 늘어납니다)' : '아날로그 약제를 선택하여 비교하세요'}</p>
                    </div>
                  )}
                </div>

                <PriceWaterfall history={selectedDrug.priceHistory} productName={selectedDrug.productName} isDark={isDark} />

                {/* Price History Table */}
                <div className={`rounded-2xl border overflow-hidden ${cardBg} ${cardBorder}`}>
                  <div className={`px-5 py-4 border-b ${tableBorder} flex items-center gap-2`}>
                    <span className={`w-5 h-5 flex items-center justify-center ${accentColor}`}><i className="ri-history-line"></i></span>
                    <h3 className={`font-bold text-sm ${textMain}`}>가격 변동 이력 테이블</h3>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full">
                      <thead>
                        <tr className={tableHeaderBg}>
                          <th className={`text-left text-xs font-semibold px-5 py-3 whitespace-nowrap ${textSub}`}>등재시점</th>
                          <th className={`text-left text-xs font-semibold px-4 py-3 whitespace-nowrap ${textSub}`}>구분</th>
                          <th className={`text-left text-xs font-semibold px-4 py-3 whitespace-nowrap ${textSub}`}>주성분</th>
                          <th className={`text-left text-xs font-semibold px-4 py-3 whitespace-nowrap ${textSub}`}>업체명</th>
                          <th className={`text-right text-xs font-semibold px-4 py-3 whitespace-nowrap ${textSub}`}>상한금액 (원)</th>
                          <th className={`text-center text-xs font-semibold px-4 py-3 whitespace-nowrap ${textSub}`}>변동률</th>
                          <th className={`text-left text-xs font-semibold px-4 py-3 whitespace-nowrap ${textSub}`}>변동사유</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selectedDrug.priceHistory.map((h, idx) => {
                          const reasonKey = `${selectedDrug.insuranceCode}|${h.date}`;
                          const r = reasons[reasonKey];
                          return (
                            <tr key={idx} className={`border-t ${tableBorder} ${tableHover} transition-colors ${idx % 2 === 1 ? tableStripe : ''}`}>
                              <td className={`px-5 py-3 text-sm whitespace-nowrap align-top ${textMain}`}>{h.date}</td>
                              <td className="px-4 py-3 whitespace-nowrap align-top">
                                <span className={`text-xs px-2 py-1 rounded-full font-semibold ${h.type === '최초등재' ? (isDark ? 'bg-[#00E5CC]/10 text-[#00E5CC]' : 'bg-teal-50 text-teal-700') : h.type === '약가인하' ? 'bg-red-50 text-red-600' : h.type === '약가인상' ? 'bg-emerald-50 text-emerald-600' : `${textMuted} bg-gray-50`}`}>{h.type}</span>
                              </td>
                              <td className={`px-4 py-3 text-sm whitespace-nowrap align-top ${textSub}`}>{selectedDrug.ingredient || '-'}</td>
                              <td className={`px-4 py-3 text-sm whitespace-nowrap align-top ${textSub}`}>{selectedDrug.company || '-'}</td>
                              <td className={`px-4 py-3 text-sm font-semibold text-right whitespace-nowrap align-top ${textMain}`}>₩{h.price.toLocaleString()}</td>
                              <td className="px-4 py-3 text-center whitespace-nowrap align-top">
                                {h.changeRate !== null ? (
                                  <span className={`text-xs font-semibold ${h.changeRate < 0 ? 'text-red-500' : h.changeRate > 0 ? 'text-emerald-500' : textSub}`}>
                                    {h.changeRate > 0 ? '+' : ''}{h.changeRate}%
                                  </span>
                                ) : <span className={`text-xs ${textMuted}`}>-</span>}
                              </td>
                              <td className="px-4 py-3 text-xs align-top whitespace-normal break-words">
                                {h.type === '최초등재' ? (
                                  <span className={textSub}>{h.reason}</span>
                                ) : !r ? (
                                  <button onClick={() => analyzeReason(h)}
                                    className={`inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-md border transition-colors cursor-pointer whitespace-nowrap ${reasonBtn}`}>
                                    <i className="ri-search-2-line text-[11px]"></i>사유 분석
                                  </button>
                                ) : r.loading ? (
                                  <span className={`italic inline-flex items-center ${textMuted}`}>
                                    <i className="ri-loader-4-line animate-spin mr-1"></i>PubMed · HIRA · MA 전문지 검색 중 (20~40초)…
                                  </span>
                                ) : r.error ? (
                                  <span className="inline-flex items-center gap-2">
                                    <span className="text-red-500">분석 실패</span>
                                    <button onClick={() => analyzeReason(h, true)} className={`text-[10px] cursor-pointer hover:underline ${accentColor}`}>재시도</button>
                                  </span>
                                ) : (() => {
                                  const d = r.data!;
                                  const conf = confBadge(d.confidence);
                                  const refs = d.references || [];
                                  const refsOpen = expandedRefs.has(reasonKey);
                                  return (
                                    <div className="space-y-2 min-w-[280px] max-w-[520px]">
                                      <div className="flex flex-wrap items-center gap-1.5">
                                        <span className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-md border font-semibold ${mechBadge}`}>
                                          <i className="ri-settings-2-line text-[10px]"></i>{d.mechanism_label || '미분류'}
                                        </span>
                                        <span className={`text-[10px] px-1.5 py-0.5 rounded-md border font-semibold ${conf.cls}`}>신뢰도 {conf.label}</span>
                                        {d.cached && <span className={`text-[10px] px-1.5 py-0.5 rounded-md ${miniStatBg} ${textMuted}`}>캐시</span>}
                                        <button onClick={() => analyzeReason(h, true)} className={`ml-auto text-[10px] ${textMuted} hover:${accentColor} underline-offset-2 hover:underline cursor-pointer`} title="캐시 무시 재분석">재분석</button>
                                      </div>
                                      {d.reason && <p className={`leading-relaxed text-[11px] whitespace-pre-wrap break-words ${reasonBody}`}>{d.reason}</p>}
                                      {d.evidence_summary && d.evidence_summary !== '수동 검토 필요' && (
                                        <div className={`text-[11px] px-2 py-1.5 rounded-md border leading-relaxed ${evidenceBox}`}>근거: {d.evidence_summary}</div>
                                      )}
                                      {refs.length > 0 && (
                                        <div className="space-y-1">
                                          <button onClick={() => toggleRefs(reasonKey)} className={`text-[10px] inline-flex items-center gap-1 cursor-pointer ${textMuted} hover:${accentColor}`}>
                                            <i className={`text-[10px] ${refsOpen ? 'ri-arrow-down-s-line' : 'ri-arrow-right-s-line'}`}></i>참고문헌 {refs.length}건
                                          </button>
                                          {refsOpen && refs.slice(0, 6).map((ref, i) => (
                                            <div key={i} className="flex items-start gap-1.5 text-[11px] pl-3">
                                              <span className={`shrink-0 px-1 py-0.5 rounded font-mono text-[10px] ${mechBadge}`}>{ref.media || ref.journal || '기타'}</span>
                                              <a href={ref.url} target="_blank" rel="noreferrer" className={`${textSub} hover:underline break-all`}>
                                                {(ref.title || ref.url).slice(0, 70)}{ref.published_at ? ` (${ref.published_at})` : ref.date_unknown ? ' (일자 불명)' : ''}
                                              </a>
                                            </div>
                                          ))}
                                        </div>
                                      )}
                                    </div>
                                  );
                                })()}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            )}

            {!selectedDrug && !loading && products.length > 0 && (
              <div className={`rounded-2xl border py-16 text-center ${emptyBg} ${cardBorder}`}>
                <span className={`w-12 h-12 flex items-center justify-center mx-auto mb-3 ${textMuted}`}><i className="ri-price-tag-3-line text-4xl"></i></span>
                <p className={`text-sm ${textSub}`}>위 목록에서 약제를 클릭하면 상세 정보가 표시됩니다</p>
              </div>
            )}
          </>
        )}
      </div>

      {selectedDrug && (
        <AnalogueCompareModal open={showModal} onClose={() => setShowModal(false)}
          baseProduct={{ name: selectedDrug.productName, price: selectedDrug.currentPrice, dailyCost: detailDaily }}
          analogues={selectedDrug.analogues || []} selected={selectedAnalogues} onToggle={handleToggleAnalogue} isDark={isDark} />
      )}
    </div>
  );
}
