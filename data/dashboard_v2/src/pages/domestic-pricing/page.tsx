import { useState, useEffect, useMemo } from 'react';
import {
  searchDomesticPriceChanges,
  downloadDomesticExport,
  fetchChangeReason,
  DomesticProduct,
  DomesticPriceHistoryEntry,
  DomesticAnalogue,
} from '@/api/domestic';
import PriceWaterfall from './components/PriceWaterfall';
import AnalogueCompareModal from './components/AnalogueCompareModal';

export default function DomesticPricingPage() {
  const [search, setSearch] = useState('');
  const [products, setProducts] = useState<DomesticProduct[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedDrug, setSelectedDrug] = useState<DomesticProduct | null>(null);
  const [showModal, setShowModal] = useState(false);
  const [selectedAnalogues, setSelectedAnalogues] = useState<string[]>([]);
  const [customAnalogues, setCustomAnalogues] = useState<DomesticAnalogue[]>([]);
  const [downloading, setDownloading] = useState(false);
  // 변동사유 캐시 — key: `${insurance_code}|${date}`
  const [reasons, setReasons] = useState<Record<string, { label: string; full: string; loading: boolean; error?: string }>>({});

  // 검색 디바운스 — 300ms 후 실제 API 호출
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
    const handle = setTimeout(async () => {
      try {
        const result = await searchDomesticPriceChanges(q);
        setProducts(result);
      } catch (e: any) {
        setError(e?.message || '검색 실패');
        setProducts([]);
      } finally {
        setLoading(false);
      }
    }, 300);
    return () => clearTimeout(handle);
  }, [search]);

  // 검색 결과가 바뀌면 기존 선택 초기화
  useEffect(() => {
    if (selectedDrug && !products.some(p => p.id === selectedDrug.id)) {
      setSelectedDrug(null);
      setSelectedAnalogues([]);
      setCustomAnalogues([]);
    }
  }, [products, selectedDrug]);

  const handleSelect = (drug: DomesticProduct) => {
    setSelectedDrug(drug);
    setSelectedAnalogues([]);
    setCustomAnalogues([]);
  };

  // 변동사유는 사용자가 "사유 분석" 버튼 클릭 시에만 가져옴 (자동 실행 금지 — v1 UX 복원)
  const analyzeReason = (h: DomesticPriceHistoryEntry) => {
    if (!selectedDrug) return;
    const key = `${selectedDrug.insuranceCode}|${h.date}`;
    const existing = reasons[key];
    if (existing && existing.loading) return;
    setReasons(prev => ({ ...prev, [key]: { label: '분석 중…', full: '', loading: true } }));
    fetchChangeReason({
      drug: selectedDrug.productName,
      date: h.date,
      ingredient: selectedDrug.ingredient,
      deltaPct: h.changeRate,
    })
      .then(r => {
        setReasons(prev => ({
          ...prev,
          [key]: {
            label: r.mechanism_label || '분석 완료',
            full: r.reason || '',
            loading: false,
          },
        }));
      })
      .catch(e => {
        setReasons(prev => ({
          ...prev,
          [key]: { label: '분석 실패', full: '', loading: false, error: e?.message || 'error' },
        }));
      });
  };

  const handleToggleAnalogue = (name: string) => {
    setSelectedAnalogues(prev =>
      prev.includes(name) ? prev.filter(n => n !== name) : [...prev, name]
    );
  };

  const handleAddExternalAnalogue = (a: DomesticAnalogue) => {
    setCustomAnalogues(prev => (prev.some(x => x.name === a.name) ? prev : [...prev, a]));
    setSelectedAnalogues(prev => (prev.includes(a.name) ? prev : [...prev, a.name]));
  };

  const analoguePool = useMemo(() => {
    if (!selectedDrug) return [] as DomesticAnalogue[];
    const seen = new Set<string>();
    const out: DomesticAnalogue[] = [];
    for (const a of [...(selectedDrug.analogues || []), ...customAnalogues]) {
      if (seen.has(a.name)) continue;
      seen.add(a.name);
      out.push(a);
    }
    return out;
  }, [selectedDrug, customAnalogues]);

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

  // 검색어 유사도 점수 — 낮을수록 우선. productName > ingredient > 기타
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

  const compareList = useMemo(() => {
    if (!selectedDrug) return [];
    return [
      {
        name: selectedDrug.productName,
        price: selectedDrug.currentPrice,
        dailyCost: selectedDrug.dailyCost,
        approvalDate: selectedDrug.firstApprovalDate,
        coverageStart: selectedDrug.coverageStart,
        usageText: selectedDrug.dosage,
        dosageForm: selectedDrug.dosageForm || null,
        isBase: true,
      },
      ...analoguePool
        .filter(a => selectedAnalogues.includes(a.name))
        .map(a => ({
          name: a.name,
          price: a.price,
          dailyCost: a.dailyCost,
          approvalDate: a.approvalDate,
          coverageStart: a.coverageStart,
          usageText: a.usageText,
          dosageForm: a.dosageForm,
          isBase: false,
        })),
    ];
  }, [selectedDrug, selectedAnalogues, analoguePool]);

  const showEmptyPrompt = !search.trim() || search.trim().length < 2;
  const showNoResults = !loading && !showEmptyPrompt && products.length === 0 && !error;

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      {/* Header */}
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-5 h-5 flex items-center justify-center"><i className="ri-price-tag-3-line text-[#00E5CC]"></i></span>
              <h1 className="text-2xl font-bold text-white">국내약가</h1>
            </div>
            <p className="text-[#8B9BB4] text-sm">건강보험 등재 약가 상세 정보 및 변동 이력</p>
          </div>
          <button
            onClick={handleDownload}
            disabled={!search.trim() || products.length === 0 || downloading}
            className="flex items-center gap-2 bg-[#00E5CC] text-[#0A0E1A] text-sm font-semibold px-4 py-2 rounded-lg cursor-pointer whitespace-nowrap hover:bg-[#00C9B1] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            <span className="w-4 h-4 flex items-center justify-center">
              <i className={downloading ? 'ri-loader-4-line animate-spin text-sm' : 'ri-download-2-line text-sm'}></i>
            </span>
            {downloading ? '내려받는 중...' : '엑셀 다운로드'}
          </button>
        </div>
      </div>

      <div className="px-8 py-6 space-y-5">
        {/* Search */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] px-5 py-4 flex items-center gap-3">
          <span className="w-5 h-5 flex items-center justify-center text-[#8B9BB4]">
            <i className={loading ? 'ri-loader-4-line animate-spin text-base text-[#00E5CC]' : 'ri-search-line text-base'}></i>
          </span>
          <input
            type="text"
            placeholder="제품명, 성분명으로 검색 (2자 이상)..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="bg-transparent text-white text-sm placeholder-[#4A5568] focus:outline-none flex-1"
          />
          {search && (
            <button onClick={() => setSearch('')} className="w-5 h-5 flex items-center justify-center text-[#4A5568] hover:text-white cursor-pointer transition-colors">
              <i className="ri-close-line text-sm"></i>
            </button>
          )}
        </div>

        {error && (
          <div className="bg-red-400/10 border border-red-400/30 rounded-xl px-4 py-3 flex items-center gap-2">
            <i className="ri-error-warning-line text-red-400 text-sm"></i>
            <p className="text-red-400 text-xs">{error}</p>
          </div>
        )}

        {showEmptyPrompt ? (
          <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] py-16 text-center">
            <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3 text-[#4A5568]">
              <i className="ri-search-line text-4xl"></i>
            </span>
            <p className="text-[#8B9BB4] text-sm">제품명 또는 성분명을 2자 이상 입력하세요</p>
            <p className="text-[#4A5568] text-xs mt-1">예: 키트루다, 자누비아, 펨브롤리주맙</p>
          </div>
        ) : (
          <>
            {/* Product List */}
            <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] overflow-hidden">
              <div className="px-5 py-3 border-b border-[#1E2530] flex items-center justify-between">
                <p className="text-[#8B9BB4] text-xs">총 <span className="text-white font-semibold">{products.length}</span>개 품목</p>
                <p className="text-[#4A5568] text-xs">클릭하여 상세 정보 확인</p>
              </div>
              <div className="overflow-auto max-h-[440px]">
                <table className="w-full">
                  <thead className="sticky top-0 z-10">
                    <tr className="bg-[#1E2530]">
                      <th className="text-left text-[#8B9BB4] text-xs font-semibold px-5 py-3 whitespace-nowrap">제품명</th>
                      <th className="text-left text-[#8B9BB4] text-xs font-semibold px-4 py-3 whitespace-nowrap">성분명</th>
                      <th className="text-left text-[#8B9BB4] text-xs font-semibold px-4 py-3 whitespace-nowrap">보험코드</th>
                      <th className="text-left text-[#8B9BB4] text-xs font-semibold px-4 py-3 whitespace-nowrap">제형</th>
                      <th className="text-right text-[#8B9BB4] text-xs font-semibold px-4 py-3 whitespace-nowrap">현재 상한금액</th>
                      <th className="text-center text-[#8B9BB4] text-xs font-semibold px-4 py-3 whitespace-nowrap">최근 변동률</th>
                      <th className="text-center text-[#8B9BB4] text-xs font-semibold px-4 py-3 whitespace-nowrap">상태</th>
                      <th className="text-left text-[#8B9BB4] text-xs font-semibold px-4 py-3 whitespace-nowrap">최종 변경일</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedProducts.map((item, idx) => (
                      <tr
                        key={item.id}
                        onClick={() => handleSelect(item)}
                        className={`border-t border-[#1E2530] hover:bg-[#00E5CC]/5 transition-colors cursor-pointer ${
                          selectedDrug?.id === item.id ? 'bg-[#00E5CC]/10' : idx % 2 === 1 ? 'bg-[#1A2035]/20' : ''
                        }`}
                      >
                        <td className="px-5 py-3 text-white text-sm font-medium whitespace-nowrap">{item.productName}</td>
                        <td className="px-4 py-3 text-[#8B9BB4] text-sm whitespace-nowrap">{item.ingredient || '-'}</td>
                        <td className="px-4 py-3 text-[#8B9BB4] text-xs font-mono whitespace-nowrap">{item.insuranceCode}</td>
                        <td className="px-4 py-3 whitespace-nowrap">
                          <span className="text-xs px-2 py-1 rounded-full bg-[#1E2530] text-[#8B9BB4]">{item.dosageForm || '-'}</span>
                        </td>
                        <td className="px-4 py-3 text-white text-sm font-semibold text-right whitespace-nowrap">
                          ₩{item.currentPrice.toLocaleString()}
                        </td>
                        <td className="px-4 py-3 text-center whitespace-nowrap">
                          {item.change !== null ? (
                            <span className={`text-xs font-semibold px-2 py-1 rounded-full ${
                              item.change < 0 ? 'text-red-400 bg-red-400/10' :
                              item.change > 0 ? 'text-emerald-400 bg-emerald-400/10' :
                              'text-[#8B9BB4] bg-[#8B9BB4]/10'
                            }`}>
                              {item.change > 0 ? '+' : ''}{item.change}%
                            </span>
                          ) : <span className="text-[#4A5568] text-xs">-</span>}
                        </td>
                        <td className="px-4 py-3 text-center whitespace-nowrap">
                          {item.status === 'active' ? (
                            <span className="text-xs px-2 py-1 rounded-full bg-emerald-400/10 text-emerald-400 font-semibold">등재</span>
                          ) : item.status === 'stale' ? (
                            <span className="text-xs px-2 py-1 rounded-full bg-amber-400/10 text-amber-400 font-semibold" title={item.statusDetail}>지연</span>
                          ) : (
                            <span className="text-xs px-2 py-1 rounded-full bg-red-400/10 text-red-400 font-semibold" title={item.statusDetail}>삭제의심</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-[#8B9BB4] text-xs whitespace-nowrap">{item.lastUpdated}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {showNoResults && (
                  <div className="text-center py-12 text-[#4A5568]">
                    <span className="w-8 h-8 flex items-center justify-center mx-auto mb-2"><i className="ri-search-line text-2xl"></i></span>
                    <p className="text-sm">검색 결과가 없습니다</p>
                  </div>
                )}
                {loading && products.length === 0 && (
                  <div className="text-center py-12 text-[#4A5568]">
                    <i className="ri-loader-4-line animate-spin text-2xl text-[#00E5CC]"></i>
                    <p className="text-sm mt-2">검색 중...</p>
                  </div>
                )}
              </div>
            </div>

            {/* Detail Panel */}
            {selectedDrug && (
              <div className="space-y-4">
                {/* Basic Info + Detail Info */}
                <div className="grid grid-cols-2 gap-4">
                  {/* 기본정보 */}
                  <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
                    <div className="flex items-center gap-2 mb-4">
                      <span className="w-5 h-5 flex items-center justify-center">
                        <i className="ri-information-line text-[#00E5CC]"></i>
                      </span>
                      <h3 className="text-white font-bold text-sm">기본 정보</h3>
                      <span className="ml-auto text-[#00E5CC] text-xs font-semibold">{selectedDrug.productName}</span>
                    </div>
                    <div className="space-y-3">
                      <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                        <span className="text-[#8B9BB4] text-xs">최초 약가 등재일</span>
                        <span className="text-white text-sm font-medium">{selectedDrug.firstRegistDate}</span>
                      </div>
                      <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                        <span className="text-[#8B9BB4] text-xs">현재 상한금액</span>
                        <span className="text-[#00E5CC] text-sm font-bold">₩{selectedDrug.currentPrice.toLocaleString()}</span>
                      </div>
                      <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                        <span className="text-[#8B9BB4] text-xs">약가 변동 이력 횟수</span>
                        <span className="text-white text-sm font-medium">{selectedDrug.priceChangeCount}회</span>
                      </div>
                      <div className="flex items-center justify-between py-2">
                        <span className="text-[#8B9BB4] text-xs">최초 약가 대비 변동률</span>
                        <span className={`text-sm font-bold ${selectedDrug.changeRateFromFirst < 0 ? 'text-red-400' : selectedDrug.changeRateFromFirst > 0 ? 'text-emerald-400' : 'text-[#8B9BB4]'}`}>
                          {selectedDrug.changeRateFromFirst > 0 ? '+' : ''}{selectedDrug.changeRateFromFirst}%
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* 상세정보 */}
                  <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
                    <div className="flex items-center gap-2 mb-4">
                      <span className="w-5 h-5 flex items-center justify-center">
                        <i className="ri-file-list-3-line text-[#7C3AED]"></i>
                      </span>
                      <h3 className="text-white font-bold text-sm">상세 정보</h3>
                      {selectedDrug.enrichmentConfidence && (
                        <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded bg-[#1E2530] text-[#4A5568]">
                          enrich: {selectedDrug.enrichmentConfidence}
                        </span>
                      )}
                    </div>
                    <div className="space-y-3">
                      <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                        <span className="text-[#8B9BB4] text-xs">제조/수입사</span>
                        <span className="text-white text-xs text-right">
                          {selectedDrug.mergedCompanies.slice(0, 2).join(', ')}
                          {selectedDrug.mergedCompanies.length > 2 && ` 외 ${selectedDrug.mergedCompanies.length - 2}사`}
                        </span>
                      </div>
                      <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                        <span className="text-[#8B9BB4] text-xs">주성분</span>
                        <span className="text-white text-xs text-right">{selectedDrug.ingredient || '-'}</span>
                      </div>
                      <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                        <span className="text-[#8B9BB4] text-xs">제형/함량</span>
                        <span className="text-white text-xs text-right">{selectedDrug.dosageForm || '-'}</span>
                      </div>
                      <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                        <span className="text-[#8B9BB4] text-xs">식약처 허가일자</span>
                        <span className="text-white text-xs text-right">{selectedDrug.firstApprovalDate || '—'}</span>
                      </div>
                      <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                        <span className="text-[#8B9BB4] text-xs">급여 등재일자</span>
                        <span className="text-white text-xs text-right">{selectedDrug.coverageStart || '—'}</span>
                      </div>
                      <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                        <span className="text-[#8B9BB4] text-xs">병합 보험코드</span>
                        <span className="text-white text-xs text-right font-mono">
                          {selectedDrug.mergedCodes.length}개
                        </span>
                      </div>
                      <div className="flex items-center justify-between py-2">
                        <span className="text-[#8B9BB4] text-xs">급여 상태</span>
                        <span className="text-white text-xs text-right">
                          {selectedDrug.status === 'active' && '등재 유지'}
                          {selectedDrug.status === 'stale' && `지연 — ${selectedDrug.statusDetail}`}
                          {selectedDrug.status === 'delisted_probable' && `삭제 의심 — ${selectedDrug.statusDetail}`}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>

                {/* 용법용량 · 투약비용 */}
                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
                    <div className="flex items-center gap-2 mb-4">
                      <span className="w-5 h-5 flex items-center justify-center">
                        <i className="ri-capsule-line text-[#00E5CC]"></i>
                      </span>
                      <h3 className="text-white font-bold text-sm">용법 · 용량</h3>
                    </div>
                    {selectedDrug.dosage ? (
                      <p className="text-[#8B9BB4] text-xs leading-relaxed whitespace-pre-wrap break-words">
                        {selectedDrug.dosage}
                      </p>
                    ) : (
                      <p className="text-[#4A5568] text-xs">—</p>
                    )}
                  </div>

                  <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
                    <div className="flex items-center gap-2 mb-4">
                      <span className="w-5 h-5 flex items-center justify-center">
                        <i className="ri-coins-line text-[#F59E0B]"></i>
                      </span>
                      <h3 className="text-white font-bold text-sm">투약 비용 (상한금액 기준)</h3>
                    </div>
                    <div className="space-y-3">
                      <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                        <span className="text-[#8B9BB4] text-xs">일일 투약비용</span>
                        <span className="text-white text-sm font-semibold">
                          {selectedDrug.dailyCost != null ? `₩${selectedDrug.dailyCost.toLocaleString()}` : '—'}
                        </span>
                      </div>
                      <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                        <span className="text-[#8B9BB4] text-xs">월간 투약비용 (30일)</span>
                        <span className="text-white text-sm font-semibold">
                          {selectedDrug.monthlyCost != null ? `₩${selectedDrug.monthlyCost.toLocaleString()}` : '—'}
                        </span>
                      </div>
                      <div className="flex items-center justify-between py-2">
                        <span className="text-[#8B9BB4] text-xs">연간 투약비용 (365일)</span>
                        <span className="text-white text-sm font-semibold">
                          {selectedDrug.yearlyCost != null ? `₩${selectedDrug.yearlyCost.toLocaleString()}` : '—'}
                        </span>
                      </div>
                    </div>
                    {selectedDrug.dailyCost == null && (
                      <p className="text-[10px] text-[#4A5568] mt-2">
                        용법용량 분석 데이터가 없어 계산할 수 없습니다.
                      </p>
                    )}
                  </div>
                </div>

                {/* 약제 비교 */}
                <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
                  <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
                    <div className="flex items-center gap-2">
                      <span className="w-5 h-5 flex items-center justify-center">
                        <i className="ri-scales-3-line text-[#F59E0B]"></i>
                      </span>
                      <h3 className="text-white font-bold text-sm">약제 비교</h3>
                      <span className="text-[11px] px-2 py-0.5 rounded-full bg-[#00E5CC]/10 text-[#00E5CC] font-semibold">
                        총 {compareList.length}개 {compareList.length > 1 && `(기준 1 + 비교 ${compareList.length - 1})`}
                      </span>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-[#8B9BB4] text-xs">
                        검색 결과: <span className="text-white font-semibold">{products.length}개</span>
                      </span>
                      <button
                        onClick={() => setShowModal(true)}
                        className="flex items-center gap-1.5 bg-[#1E2530] border border-[#2A3545] text-[#8B9BB4] hover:text-white text-xs px-3 py-1.5 rounded-lg cursor-pointer transition-colors whitespace-nowrap"
                      >
                        <span className="w-4 h-4 flex items-center justify-center"><i className="ri-add-line text-sm"></i></span>
                        비교 약제 선택
                      </button>
                    </div>
                  </div>

                  {compareList.length > 0 ? (
                    <div className="flex gap-3 overflow-x-auto pb-2 -mx-1 px-1 snap-x">
                      {compareList.map((item) => (
                        <div
                          key={item.name}
                          className={`flex-shrink-0 w-64 snap-start rounded-xl p-4 border ${item.isBase ? 'border-[#00E5CC]/40 bg-[#00E5CC]/5' : 'border-[#1E2530] bg-[#1E2530]'}`}
                        >
                          {item.isBase ? (
                            <span className="text-xs px-2 py-0.5 rounded-full bg-[#00E5CC]/20 text-[#00E5CC] font-semibold mb-2 inline-block">기준</span>
                          ) : (
                            <span className="text-xs px-2 py-0.5 rounded-full bg-[#7C3AED]/20 text-[#C4B5FD] font-semibold mb-2 inline-block">비교</span>
                          )}
                          <p className="text-white text-sm font-bold mb-3 leading-snug line-clamp-2" title={item.name}>{item.name}</p>
                          <div className="space-y-2">
                            <div>
                              <p className="text-[#4A5568] text-xs mb-0.5">현재 약가</p>
                              <p className="text-white text-base font-bold">₩{item.price.toLocaleString()}</p>
                            </div>
                            <div>
                              <p className="text-[#4A5568] text-xs mb-0.5">일일 투약비용</p>
                              <p className="text-white text-sm font-semibold">
                                {item.dailyCost != null ? `₩${item.dailyCost.toLocaleString()}` : '—'}
                              </p>
                            </div>
                            <div>
                              <p className="text-[#4A5568] text-xs mb-0.5">허가일자</p>
                              <p className="text-white text-sm">{item.approvalDate || '—'}</p>
                            </div>
                            <div>
                              <p className="text-[#4A5568] text-xs mb-0.5">급여 등재일</p>
                              <p className="text-white text-sm">{item.coverageStart || '—'}</p>
                            </div>
                            <div>
                              <p className="text-[#4A5568] text-xs mb-0.5">제형</p>
                              <p className="text-white text-sm">{item.dosageForm || '—'}</p>
                            </div>
                            <div>
                              <p className="text-[#4A5568] text-xs mb-0.5">용법 · 용량</p>
                              <p className="text-white text-xs leading-snug line-clamp-3" title={item.usageText || undefined}>
                                {item.usageText || '—'}
                              </p>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-center py-8 text-[#4A5568]">
                      <span className="w-8 h-8 flex items-center justify-center mx-auto mb-2"><i className="ri-add-circle-line text-2xl"></i></span>
                      <p className="text-sm">비교할 약제를 선택하세요</p>
                    </div>
                  )}
                </div>

                {/* Waterfall Chart */}
                <PriceWaterfall history={selectedDrug.priceHistory} productName={selectedDrug.productName} />

                {/* Price History Table */}
                <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] overflow-hidden">
                  <div className="px-5 py-4 border-b border-[#1E2530] flex items-center gap-2">
                    <span className="w-5 h-5 flex items-center justify-center">
                      <i className="ri-history-line text-[#00E5CC]"></i>
                    </span>
                    <h3 className="text-white font-bold text-sm">가격 변동 이력 테이블</h3>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full">
                      <thead>
                        <tr className="bg-[#1E2530]">
                          <th className="text-left text-[#8B9BB4] text-xs font-semibold px-5 py-3 whitespace-nowrap">등재시점</th>
                          <th className="text-left text-[#8B9BB4] text-xs font-semibold px-4 py-3 whitespace-nowrap">구분</th>
                          <th className="text-left text-[#8B9BB4] text-xs font-semibold px-4 py-3 whitespace-nowrap">주성분</th>
                          <th className="text-left text-[#8B9BB4] text-xs font-semibold px-4 py-3 whitespace-nowrap">업체명</th>
                          <th className="text-right text-[#8B9BB4] text-xs font-semibold px-4 py-3 whitespace-nowrap">상한금액 (원)</th>
                          <th className="text-center text-[#8B9BB4] text-xs font-semibold px-4 py-3 whitespace-nowrap">변동률</th>
                          <th className="text-left text-[#8B9BB4] text-xs font-semibold px-4 py-3 whitespace-nowrap">변동사유</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selectedDrug.priceHistory.map((h, idx) => (
                          <tr key={idx} className={`border-t border-[#1E2530] hover:bg-[#00E5CC]/5 transition-colors ${idx % 2 === 1 ? 'bg-[#1A2035]/20' : ''}`}>
                            <td className="px-5 py-3 text-white text-sm whitespace-nowrap">{h.date}</td>
                            <td className="px-4 py-3 whitespace-nowrap">
                              <span className={`text-xs px-2 py-1 rounded-full font-semibold ${
                                h.type === '최초등재' ? 'bg-[#00E5CC]/10 text-[#00E5CC]' :
                                h.type === '약가인하' ? 'bg-red-400/10 text-red-400' :
                                h.type === '약가인상' ? 'bg-emerald-400/10 text-emerald-400' :
                                'bg-[#4A5568]/20 text-[#8B9BB4]'
                              }`}>
                                {h.type}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-[#8B9BB4] text-sm whitespace-nowrap">{selectedDrug.ingredient || '-'}</td>
                            <td className="px-4 py-3 text-[#8B9BB4] text-sm whitespace-nowrap">{selectedDrug.company || '-'}</td>
                            <td className="px-4 py-3 text-white text-sm font-semibold text-right whitespace-nowrap">
                              ₩{h.price.toLocaleString()}
                            </td>
                            <td className="px-4 py-3 text-center whitespace-nowrap">
                              {h.changeRate !== null ? (
                                <span className={`text-xs font-semibold ${h.changeRate < 0 ? 'text-red-400' : h.changeRate > 0 ? 'text-emerald-400' : 'text-[#8B9BB4]'}`}>
                                  {h.changeRate > 0 ? '+' : ''}{h.changeRate}%
                                </span>
                              ) : <span className="text-[#4A5568] text-xs">-</span>}
                            </td>
                            <td className="px-4 py-3 text-xs align-top whitespace-normal break-words">
                              {h.type === '최초등재' ? (
                                <span className="text-[#8B9BB4]">{h.reason}</span>
                              ) : (() => {
                                const r = reasons[`${selectedDrug.insuranceCode}|${h.date}`];
                                if (!r) {
                                  return (
                                    <button
                                      onClick={() => analyzeReason(h)}
                                      className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-md border border-[#2A3545] bg-[#1E2530] text-[#8B9BB4] hover:text-white hover:border-[#00E5CC] transition-colors cursor-pointer whitespace-nowrap"
                                    >
                                      <i className="ri-search-2-line text-[11px]"></i>
                                      사유 분석
                                    </button>
                                  );
                                }
                                if (r.loading) {
                                  return (
                                    <span className="text-[#4A5568] italic inline-flex items-center">
                                      <i className="ri-loader-4-line animate-spin mr-1"></i>분석 중…
                                    </span>
                                  );
                                }
                                if (r.error) {
                                  return (
                                    <div className="flex items-center gap-2">
                                      <span className="text-red-400">분석 실패</span>
                                      <button
                                        onClick={() => analyzeReason(h)}
                                        className="text-[10px] text-[#00E5CC] hover:underline cursor-pointer"
                                      >재시도</button>
                                    </div>
                                  );
                                }
                                return (
                                  <div className="space-y-1 min-w-[220px] max-w-[480px]">
                                    <span className="text-[#00E5CC] font-semibold">{r.label}</span>
                                    {r.full && (
                                      <p className="text-[#8B9BB4] leading-relaxed text-[11px] whitespace-pre-wrap break-words">
                                        {r.full}
                                      </p>
                                    )}
                                  </div>
                                );
                              })()}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            )}

            {!selectedDrug && !loading && products.length > 0 && (
              <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] py-16 text-center">
                <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3 text-[#4A5568]">
                  <i className="ri-price-tag-3-line text-4xl"></i>
                </span>
                <p className="text-[#8B9BB4] text-sm">위 목록에서 약제를 클릭하면 상세 정보가 표시됩니다</p>
              </div>
            )}
          </>
        )}
      </div>

      {/* Analogue Modal */}
      {selectedDrug && (
        <AnalogueCompareModal
          open={showModal}
          onClose={() => setShowModal(false)}
          baseProduct={{ name: selectedDrug.productName, price: selectedDrug.currentPrice, dailyCost: selectedDrug.dailyCost }}
          baseInsuranceCode={selectedDrug.insuranceCode}
          analogues={analoguePool}
          selected={selectedAnalogues}
          onToggle={handleToggleAnalogue}
          onAddExternal={handleAddExternalAnalogue}
        />
      )}
    </div>
  );
}
