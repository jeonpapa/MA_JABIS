import { useState, useEffect, useMemo } from 'react';
import {
  searchDomesticPriceChanges,
  downloadDomesticExport,
  fetchChangeReason,
  enrichBulk,
  DomesticProduct,
  DomesticPriceHistoryEntry,
  DomesticAnalogue,
  ChangeReasonResult,
  EnrichmentResult,
} from '@/api/domestic';
import PriceWaterfall from './components/PriceWaterfall';
import AnalogueCompareModal from './components/AnalogueCompareModal';
import RsaRegistryModal from './components/RsaRegistryModal';
import EffectSummary from './components/EffectSummary';

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
  // 변동사유 캐시 — key: `${insurance_code}|${date}` · v1 UX 복원: 전체 payload 보관
  const [reasons, setReasons] = useState<Record<string, { data?: ChangeReasonResult; loading: boolean; error?: string }>>({});
  // On-demand enrichment — key: normalized_name · 허가일·용법·정확한 daily_cost
  const [liveEnrich, setLiveEnrich] = useState<Record<string, EnrichmentResult>>({});
  const [enrichingSet, setEnrichingSet] = useState<Set<string>>(new Set());
  const [expandedRefs, setExpandedRefs] = useState<Set<string>>(new Set());
  const [rsaModalOpen, setRsaModalOpen] = useState(false);
  const [rsaModalDrug, setRsaModalDrug] = useState<{ brand: string; isRsa: 0 | 1 | null; type: string | null; note: string | null } | null>(null);
  const toggleRefs = (key: string) =>
    setExpandedRefs(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });

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
  const analyzeReason = (h: DomesticPriceHistoryEntry, refresh = false) => {
    if (!selectedDrug) return;
    const key = `${selectedDrug.insuranceCode}|${h.date}`;
    const existing = reasons[key];
    if (existing && existing.loading) return;
    setReasons(prev => ({ ...prev, [key]: { loading: true } }));
    fetchChangeReason({
      drug: selectedDrug.productName,
      date: h.date,
      ingredient: selectedDrug.ingredient,
      deltaPct: h.changeRate,
      refresh,
    })
      .then(r => {
        setReasons(prev => ({ ...prev, [key]: { data: r, loading: false } }));
      })
      .catch(e => {
        setReasons(prev => ({
          ...prev,
          [key]: { loading: false, error: e?.message || 'error' },
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

  // On-demand LLM enrichment — 선택된 기준약제 + 선택된 비교약제들에 대해 허가일·용법·정확한 daily_cost 조회
  // drug_enrichment 캐시 히트는 즉시, 미스는 Perplexity 10~30초 (WARP 필요)
  useEffect(() => {
    if (!selectedDrug) return;
    const targets: Array<{ normalized_name: string; product_name: string; ingredient: string; current_price: number; code: string; codes: string[] }> = [];
    const pushTarget = (norm: string | undefined, product_name: string, ingredient: string, price: number, code: string, codes: string[] = []) => {
      if (!norm) return;
      if (liveEnrich[norm] || enrichingSet.has(norm)) return;
      targets.push({ normalized_name: norm, product_name, ingredient, current_price: price, code, codes });
    };
    pushTarget(
      selectedDrug.normalizedName,
      selectedDrug.productName,
      selectedDrug.ingredient,
      selectedDrug.currentPrice,
      selectedDrug.insuranceCode,
      selectedDrug.mergedCodes,
    );
    for (const name of selectedAnalogues) {
      const a = analoguePool.find(x => x.name === name);
      if (a && a.normalizedName) {
        pushTarget(a.normalizedName, a.name, a.ingredient || '', a.price, a.insuranceCode || '');
      }
    }
    if (targets.length === 0) return;
    const norms = targets.map(t => t.normalized_name);
    setEnrichingSet(prev => { const s = new Set(prev); norms.forEach(n => s.add(n)); return s; });
    enrichBulk(targets)
      .then(results => {
        setLiveEnrich(prev => ({ ...prev, ...results }));
      })
      .catch(e => {
        console.warn('[enrichBulk] 실패:', e);
      })
      .finally(() => {
        setEnrichingSet(prev => {
          const s = new Set(prev);
          norms.forEach(n => s.delete(n));
          return s;
        });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDrug, selectedAnalogues, analoguePool]);

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
    // liveEnrich 가 있으면 해당 값으로 override (LLM 결과가 DB JOIN 보다 정확)
    const mergeLive = (norm: string | undefined, fallback: { dailyCost: number | null; approvalDate: string | null; usageText: string | null; enrichmentSource: string | null }) => {
      if (!norm) return { ...fallback, enriching: enrichingSet.has(norm || '') };
      const live = liveEnrich[norm];
      if (!live) return { ...fallback, enriching: enrichingSet.has(norm) };
      // LLM 이 실패 레코드 반환 → fallback(상속/추정) 보존 + 실패 배지 표시.
      // 실제로 호출했으나 Perplexity 차단/timeout 으로 빈 결과 → "조사 실패" 배지로 투명화.
      if (live.is_failure) {
        return {
          ...fallback,
          enrichmentSource: 'enrichment_failed',
          enriching: false,
        };
      }
      // dailyCost: 백엔드 bsa_calc 가 있으면 fallback (= selectedDrug.dailyCost) 우선 — 상단 카드와 일치.
      // bsa_calc 없을 때만 liveEnrich 의 treatment_cost.daily 사용.
      const useBsaPriority = fallback.dailyCost != null;
      return {
        dailyCost: useBsaPriority ? fallback.dailyCost : (live.treatment_cost?.daily ?? fallback.dailyCost),
        approvalDate: live.approval_date || fallback.approvalDate,
        usageText: live.usage_text || fallback.usageText,
        enrichmentSource: live.cache_source === 'fresh' ? 'llm_fresh' : 'llm_cache',
        enriching: false,
      };
    };
    const base = mergeLive(selectedDrug.normalizedName, {
      dailyCost: selectedDrug.dailyCost,
      approvalDate: selectedDrug.firstApprovalDate,
      usageText: selectedDrug.dosage,
      enrichmentSource: selectedDrug.enrichmentSource ?? null,
    });
    return [
      {
        name: selectedDrug.productName,
        price: selectedDrug.currentPrice,
        dailyCost: base.dailyCost,
        approvalDate: base.approvalDate,
        coverageStart: selectedDrug.coverageStart,
        usageText: base.usageText,
        dosageForm: selectedDrug.dosageForm || null,
        enrichmentSource: base.enrichmentSource,
        enriching: base.enriching,
        bsaCalc: selectedDrug.bsaCalc ?? null,
        usageUnverified: selectedDrug.usageUnverified ?? false,
        isRsa: selectedDrug.isRsa ?? null,
        rsaType: selectedDrug.rsaType ?? null,
        rsaNote: selectedDrug.rsaNote ?? null,
        rsaSource: selectedDrug.rsaSource ?? null,
        isBase: true,
      },
      ...analoguePool
        .filter(a => selectedAnalogues.includes(a.name))
        .map(a => {
          const m = mergeLive(a.normalizedName, {
            dailyCost: a.dailyCost,
            approvalDate: a.approvalDate,
            usageText: a.usageText,
            enrichmentSource: a.enrichmentSource ?? null,
          });
          return {
            name: a.name,
            price: a.price,
            dailyCost: m.dailyCost,
            approvalDate: m.approvalDate,
            coverageStart: a.coverageStart,
            usageText: m.usageText,
            dosageForm: a.dosageForm,
            enrichmentSource: m.enrichmentSource,
            enriching: m.enriching,
            bsaCalc: a.bsaCalc ?? null,
            usageUnverified: a.usageUnverified ?? false,
            isRsa: a.isRsa ?? null,
            rsaType: a.rsaType ?? null,
            rsaNote: a.rsaNote ?? null,
            rsaSource: a.rsaSource ?? null,
            isBase: false,
          };
        }),
    ];
  }, [selectedDrug, selectedAnalogues, analoguePool, liveEnrich, enrichingSet]);

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
                        <span className="text-[#8B9BB4] text-xs flex items-center gap-1">
                          식약처 허가일자
                          {selectedDrug.mfdsPermit?.permitDate && (
                            <span
                              className="px-1 py-0.5 text-[9px] rounded bg-[#1E3A5F] text-[#7FCEFF] cursor-help"
                              title="식약처 의약품 제품 허가정보 API 실측 (DrugPrdtPrmsnInfoService07)"
                            >
                              MFDS 실측
                            </span>
                          )}
                        </span>
                        <span className="text-white text-xs text-right">{selectedDrug.firstApprovalDate || '—'}</span>
                      </div>
                      {selectedDrug.mfdsPermit?.atcCode && (
                        <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                          <span className="text-[#8B9BB4] text-xs">ATC 코드</span>
                          <span className="text-white text-xs text-right font-mono">{selectedDrug.mfdsPermit.atcCode}</span>
                        </div>
                      )}
                      {selectedDrug.mfdsPermit?.etcOtc && (
                        <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                          <span className="text-[#8B9BB4] text-xs">분류</span>
                          <span className="text-white text-xs text-right">
                            {selectedDrug.mfdsPermit.etcOtc}
                            {selectedDrug.mfdsPermit.rareDrugYn === 'Y' && (
                              <span className="ml-2 px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-300 text-[10px]">희귀의약품</span>
                            )}
                            {selectedDrug.mfdsPermit.newdrugClass === '신약' && (
                              <span className="ml-2 px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 text-[10px]">신약</span>
                            )}
                          </span>
                        </div>
                      )}
                      {selectedDrug.mfdsPermit?.permitHolder && (
                        <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                          <span className="text-[#8B9BB4] text-xs">허가권자</span>
                          <span className="text-white text-xs text-right">{selectedDrug.mfdsPermit.permitHolder}</span>
                        </div>
                      )}
                      {selectedDrug.mfdsPermit?.packUnit && (
                        <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                          <span className="text-[#8B9BB4] text-xs">포장단위</span>
                          <span className="text-white text-xs text-right">{selectedDrug.mfdsPermit.packUnit}</span>
                        </div>
                      )}
                      {selectedDrug.mfdsPermit?.storageMethod && (
                        <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                          <span className="text-[#8B9BB4] text-xs">보관</span>
                          <span className="text-white text-xs text-right">{selectedDrug.mfdsPermit.storageMethod}</span>
                        </div>
                      )}
                      <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                        <span className="text-[#8B9BB4] text-xs">급여 등재일자</span>
                        <span className="text-white text-xs text-right">{selectedDrug.coverageStart || '—'}</span>
                      </div>
                      <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                        <span className="text-[#8B9BB4] text-xs flex items-center gap-1">
                          특허 상태
                          {selectedDrug.patentSource === 'mfds_api' ? (
                            <span
                              className="px-1 py-0.5 text-[9px] rounded bg-[#1E3A5F] text-[#7FCEFF] cursor-help"
                              title={selectedDrug.patentSourceNote || 'MFDS 공공데이터 API 실측'}
                            >
                              MFDS 실측
                            </span>
                          ) : selectedDrug.patentSource === 'price_history' ? (
                            <span
                              className="px-1 py-0.5 text-[9px] rounded bg-[#3A2D1E] text-[#E0B055] cursor-help"
                              title={selectedDrug.patentSourceNote || '가격 history 추정 (KR-RULE-009)'}
                            >
                              가격 history 추정
                            </span>
                          ) : null}
                        </span>
                        <span className="text-xs text-right flex items-center gap-2 flex-wrap justify-end">
                          {selectedDrug.patentStatus === '만료' ? (
                            <>
                              <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 font-semibold">만료</span>
                              {selectedDrug.patentLoeDateInferred && (
                                <span className="text-[#8B9BB4]">≈ {selectedDrug.patentLoeDateInferred} 추정</span>
                              )}
                            </>
                          ) : selectedDrug.patentStatus === '유효' ? (
                            <>
                              <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 font-semibold">유효</span>
                              {selectedDrug.patentExpiryDate && (
                                <span className="text-white">만료 예정: {selectedDrug.patentExpiryDate}</span>
                              )}
                            </>
                          ) : (
                            <span className="text-[#4A5568]">—</span>
                          )}
                          <a
                            href={`https://nedrug.mfds.go.kr/searchPatent?searchYn=true&itemName=${encodeURIComponent(selectedDrug.brandName || selectedDrug.productName)}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-[#00E5CC] hover:underline"
                            title="MFDS 의약품특허목록집에서 직접 조회"
                          >
                            MFDS 조회 ↗
                          </a>
                        </span>
                      </div>
                      {selectedDrug.patentSubstancePatents && selectedDrug.patentSubstancePatents.length > 0 && (
                        <div className="py-2 border-b border-[#1E2530]">
                          <div className="text-[#8B9BB4] text-xs mb-2">핵심 물질특허 (LOE 결정용)</div>
                          <div className="space-y-1">
                            {selectedDrug.patentSubstancePatents.slice(0, 5).map((sp, idx) => (
                              <div key={`${sp.patent_no}-${idx}`} className="flex flex-col gap-0.5 text-[10px] py-1 border-b border-[#1E2530]/50">
                                <div className="flex items-center gap-2">
                                  <span className={`px-1 py-0.5 rounded ${
                                    sp.patent_status === '등록'
                                      ? 'bg-emerald-500/15 text-emerald-300'
                                      : 'bg-[#2A3545] text-[#8B9BB4]'
                                  }`}>
                                    {sp.patent_status}
                                  </span>
                                  <span className="text-white font-mono">{sp.patent_no}</span>
                                  <span className="text-[#8B9BB4]">{sp.patent_gb_code}</span>
                                  {sp.patent_end_date && (
                                    <span className="text-[#8B9BB4] ml-auto">{sp.patent_end_date}</span>
                                  )}
                                </div>
                                {sp.invn_name && (
                                  <div className="text-[9px] text-[#8B9BB4] pl-1">{sp.invn_name}</div>
                                )}
                              </div>
                            ))}
                            {selectedDrug.patentSubstancePatents.length > 5 && (
                              <div className="text-[9px] text-[#4A5568]">
                                + {selectedDrug.patentSubstancePatents.length - 5}건 추가
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                      {selectedDrug.patentSecondaryPatents && selectedDrug.patentSecondaryPatents.length > 0 && (
                        <details className="py-2 border-b border-[#1E2530]">
                          <summary className="text-[#8B9BB4] text-xs cursor-pointer hover:text-white">
                            후속 특허 {selectedDrug.patentSecondaryPatents.length}건 (ADC/조성/용도/제법 — LOE 결정 무관)
                          </summary>
                          <div className="space-y-1 mt-2">
                            {selectedDrug.patentSecondaryPatents.slice(0, 8).map((sp, idx) => (
                              <div key={`${sp.patent_no}-${idx}`} className="flex flex-col gap-0.5 text-[10px] py-1">
                                <div className="flex items-center gap-2">
                                  <span className={`px-1 py-0.5 rounded ${
                                    sp.patent_status === '등록'
                                      ? 'bg-amber-500/15 text-amber-300'
                                      : 'bg-[#2A3545] text-[#8B9BB4]'
                                  }`}>
                                    {sp.patent_status}
                                  </span>
                                  <span className="text-white font-mono">{sp.patent_no}</span>
                                  <span className="text-[#8B9BB4]">{sp.patent_gb_code}</span>
                                  {sp.reclassified_reason && (
                                    <span className="text-[#7FCEFF] text-[9px]" title="reclassified from core">
                                      [{sp.reclassified_reason === 'academic_patentee' ? '학술기관' :
                                        sp.reclassified_reason === 'follow_on_modality' ? '후속 modality' :
                                        sp.reclassified_reason}]
                                    </span>
                                  )}
                                  {sp.patent_end_date && (
                                    <span className="text-[#8B9BB4] ml-auto">{sp.patent_end_date}</span>
                                  )}
                                </div>
                                {sp.invn_name && (
                                  <div className="text-[9px] text-[#8B9BB4] pl-1">{sp.invn_name}</div>
                                )}
                              </div>
                            ))}
                            {selectedDrug.patentSecondaryPatents.length > 8 && (
                              <div className="text-[9px] text-[#4A5568]">
                                + {selectedDrug.patentSecondaryPatents.length - 8}건 추가
                              </div>
                            )}
                          </div>
                        </details>
                      )}
                      <div className="flex items-center justify-between py-2 border-b border-[#1E2530]">
                        <span className="text-[#8B9BB4] text-xs flex items-center gap-1">
                          병합 보험코드
                          <span
                            className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border border-[#3A4555] text-[9px] text-[#8B9BB4] cursor-help"
                            title="동일 제품(성분·제형·함량 동일) 이 약가 변동·기재 변경으로 인해 HIRA 약제 상한금액표에 여러 보험코드로 등록된 경우, 이를 normalized_name 기준으로 묶어 단일 제품으로 표시합니다. 가격 history 는 모든 코드의 합산이며, 급여 등재일은 가장 이른 등재일을 사용합니다."
                          >
                            i
                          </span>
                        </span>
                        <span className="text-white text-xs text-right font-mono" title={selectedDrug.mergedCodes.join(', ')}>
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

                {/* 효능효과 · 투약비용 */}
                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
                    <div className="flex items-center gap-2 mb-4">
                      <span className="w-5 h-5 flex items-center justify-center">
                        <i className="ri-shield-check-line text-[#00E5CC]"></i>
                      </span>
                      <h3 className="text-white font-bold text-sm">효능 · 효과</h3>
                    </div>
                    <EffectSummary
                      effectText={selectedDrug.mfdsPermit?.effectText ?? null}
                      usageText={selectedDrug.dosage}
                    />
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
                          <div className="flex flex-wrap items-center gap-1.5 mb-2">
                            {item.isBase ? (
                              <span className="text-xs px-2 py-0.5 rounded-full bg-[#00E5CC]/20 text-[#00E5CC] font-semibold">기준</span>
                            ) : (
                              <span className="text-xs px-2 py-0.5 rounded-full bg-[#7C3AED]/20 text-[#C4B5FD] font-semibold">비교</span>
                            )}
                            {/* RSA 배지 — 위험분담제 자산은 표시가 ≠ 실제가. 카드 헤더 노출. */}
                            {item.isRsa === 1 && (() => {
                              const typeLabel: Record<string, string> = {
                                refund: '환급형',
                                expenditure_cap: '총액제한형',
                                utilization: '사용량-약가 연동',
                                conditional: '조건부 급여',
                                combined: '복합 유형',
                              };
                              const label = (item.rsaType && typeLabel[item.rsaType]) || 'RSA 대상';
                              return (
                                <span className="inline-flex items-center gap-0.5">
                                  <span
                                    className="text-[10px] px-1.5 py-0.5 rounded-md bg-red-400/10 text-red-300 border border-red-400/30 cursor-help"
                                    title={`위험분담제 ${label}\n\n표시가는 정부와의 RSA 계약 하 부분 조정만 노출되며, 실제 환급 차액 후 net 가격은 비공개.\n\n${item.rsaNote || ''}\n\n출처: ${item.rsaSource || '-'}`}
                                  >
                                    ⚠ RSA · {label}
                                  </span>
                                  <button
                                    onClick={() => {
                                      setRsaModalDrug({ brand: item.name, isRsa: item.isRsa as 0|1|null, type: item.rsaType || null, note: item.rsaNote || null });
                                      setRsaModalOpen(true);
                                    }}
                                    title="RSA 정보 수정"
                                    className="text-[10px] text-[#8B9BB4] hover:text-white px-1"
                                  >
                                    <i className="ri-edit-line"></i>
                                  </button>
                                </span>
                              );
                            })()}
                            {item.isRsa === null && !item.isBase && (
                              <span className="inline-flex items-center gap-0.5">
                                <span
                                  className="text-[10px] px-1.5 py-0.5 rounded-md bg-[#4A5568]/20 text-[#8B9BB4] border border-[#4A5568]/30 cursor-help"
                                  title="RSA 적용 여부 미확인 — 정확한 net 가격 산출 불가"
                                >
                                  RSA 미확인
                                </span>
                                <button
                                  onClick={() => {
                                    setRsaModalDrug({ brand: item.name, isRsa: null, type: null, note: null });
                                    setRsaModalOpen(true);
                                  }}
                                  title="RSA 등록"
                                  className="text-[10px] text-[#00E5CC] hover:text-white px-1"
                                >
                                  <i className="ri-add-line"></i>
                                </button>
                              </span>
                            )}
                            {item.enriching && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-[#00E5CC]/10 text-[#00E5CC] border border-[#00E5CC]/20 inline-flex items-center gap-1">
                                <i className="ri-loader-4-line animate-spin text-[9px]"></i>조사 중
                              </span>
                            )}
                            {(item.enrichmentSource === 'llm_cache' || item.enrichmentSource === 'llm_fresh') && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-emerald-400/10 text-emerald-300 border border-emerald-400/20" title={`LLM ${item.enrichmentSource === 'llm_fresh' ? '실시간' : '캐시'} 조사`}>
                                조사 완료
                              </span>
                            )}
                            {item.enrichmentSource === 'enrichment_failed' && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-amber-400/10 text-amber-300 border border-amber-400/20" title="LLM 조사 실패 (WARP/네트워크 확인 필요) — 기존 추정/상속 값 표시">
                                조사 실패
                              </span>
                            )}
                            {item.enrichmentSource === 'direct' && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-sky-400/10 text-sky-300 border border-sky-400/20" title="drug_enrichment 직접 매칭">직접</span>
                            )}
                            {item.enrichmentSource && item.enrichmentSource.startsWith('inherited_generic') && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-amber-400/10 text-amber-300 border border-amber-400/20" title={item.enrichmentSource}>
                                상속 ({item.enrichmentSource.split(':')[1] || '동일 generic'})
                              </span>
                            )}
                            {item.enrichmentSource === 'default_heuristic' && (
                              <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-[#4A5568]/20 text-[#8B9BB4] border border-[#4A5568]/30" title="1정/일 가정 (추정) — 정확한 용법 조사 필요">
                                추정
                              </span>
                            )}
                          </div>
                          <p className="text-white text-sm font-bold mb-3 leading-snug line-clamp-2" title={item.name}>{item.name}</p>
                          <div className="space-y-2">
                            <div>
                              <p className="text-[#4A5568] text-xs mb-0.5">
                                {item.isRsa === 1 ? '표시가 (실제가 비공개)' : '현재 약가'}
                              </p>
                              <p className={`text-base font-bold ${item.isRsa === 1 ? 'text-amber-200' : 'text-white'}`}>
                                ₩{item.price.toLocaleString()}
                              </p>
                            </div>
                            <div>
                              <div className="flex items-center gap-1.5 mb-0.5">
                                <p className="text-[#4A5568] text-xs">일일 투약비용</p>
                                {item.bsaCalc && (
                                  <span
                                    className="text-[9px] px-1.5 py-[1px] rounded bg-amber-400/10 text-amber-300 border border-amber-400/20 cursor-help"
                                    title={item.bsaCalc.rationale + (item.bsaCalc.method === 'bsa'
                                      ? '\n\n표준 환자: 성인 60kg / 체표면적 1.7m² (DuBois 공식 BSA)'
                                      : '\n\n표준 환자: 성인 60kg')}
                                  >
                                    {item.bsaCalc.method === 'bsa' ? 'BSA 1.7m²' : '60kg'}
                                  </span>
                                )}
                                {item.usageUnverified && (
                                  <span
                                    className="text-[9px] px-1.5 py-[1px] rounded bg-red-400/10 text-red-300 border border-red-400/20 cursor-help"
                                    title="용법 텍스트가 적응증·효능 서술로 보임 (mg/kg, q3w 같은 dosing 패턴 미포함). 일일투약비용은 단가 기준 임시 표시 — 정확한 용법 조사 필요."
                                  >
                                    용법 미확정
                                  </span>
                                )}
                              </div>
                              <p className={`text-sm font-semibold ${item.usageUnverified ? 'text-red-300/70' : 'text-white'}`}>
                                {item.dailyCost != null ? `₩${item.dailyCost.toLocaleString()}` : '—'}
                              </p>
                              {item.bsaCalc && (
                                <p className="text-[#4A5568] text-[10px] leading-tight mt-0.5">
                                  {item.bsaCalc.perDoseMg.toFixed(0)}mg × q{item.bsaCalc.intervalDays/7}w
                                </p>
                              )}
                              {item.usageUnverified && !item.bsaCalc && (
                                <p className="text-red-300/60 text-[10px] leading-tight mt-0.5">
                                  단가 기준 임시값 (정확한 용법 조사 필요)
                                </p>
                              )}
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
                                      <i className="ri-loader-4-line animate-spin mr-1"></i>
                                      PubMed · HIRA · MA 전문지 검색 중 (20~40초)…
                                    </span>
                                  );
                                }
                                if (r.error) {
                                  return (
                                    <div className="flex items-center gap-2">
                                      <span className="text-red-400">분석 실패</span>
                                      <button
                                        onClick={() => analyzeReason(h, true)}
                                        className="text-[10px] text-[#00E5CC] hover:underline cursor-pointer"
                                      >재시도</button>
                                    </div>
                                  );
                                }
                                const d = r.data!;
                                const mech = d.mechanism_label || '미분류';
                                const conf = (d.confidence || 'low').toLowerCase();
                                const confColor = conf === 'high'
                                  ? 'bg-emerald-400/10 text-emerald-400 border-emerald-400/20'
                                  : conf === 'medium'
                                  ? 'bg-amber-400/10 text-amber-300 border-amber-400/20'
                                  : 'bg-red-400/10 text-red-400 border-red-400/20';
                                const confLabel = conf === 'high' ? '높음' : conf === 'medium' ? '보통' : '낮음';
                                const refs = d.references || [];
                                return (
                                  <div className="space-y-2 min-w-[280px] max-w-[520px]">
                                    {/* 기전 + 신뢰도 + 캐시/검토 배지 */}
                                    <div className="flex flex-wrap items-center gap-1.5">
                                      <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-md bg-[#00E5CC]/10 text-[#00E5CC] border border-[#00E5CC]/20 font-semibold">
                                        <i className="ri-settings-2-line text-[10px]"></i>{mech}
                                      </span>
                                      <span className={`text-[10px] px-1.5 py-0.5 rounded-md border font-semibold ${confColor}`}>
                                        신뢰도 {confLabel}
                                      </span>
                                      {d.cached && (
                                        <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-[#4A5568]/20 text-[#8B9BB4] border border-[#4A5568]/30">캐시</span>
                                      )}
                                      {d.review && d.review.approved === false && (
                                        <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-amber-400/10 text-amber-300 border border-amber-400/20" title={d.review.final_verdict || ''}>
                                          ReviewAgent 거부
                                        </span>
                                      )}
                                      <button
                                        onClick={() => analyzeReason(h, true)}
                                        className="ml-auto text-[10px] text-[#4A5568] hover:text-[#00E5CC] underline-offset-2 hover:underline"
                                        title="캐시 무시 재분석"
                                      >
                                        재분석
                                      </button>
                                    </div>
                                    {/* 본문 사유 */}
                                    {d.reason && (
                                      <p className="text-[#B0BCC9] leading-relaxed text-[11px] whitespace-pre-wrap break-words">
                                        {d.reason}
                                      </p>
                                    )}
                                    {/* 핵심 근거 — evidence_summary */}
                                    {d.evidence_summary && d.evidence_summary !== '수동 검토 필요' && (
                                      <div className="text-[11px] px-2 py-1.5 rounded-md bg-[#0D1117] border border-[#1E2530] text-[#8B9BB4] leading-relaxed">
                                        <span className="text-[#00E5CC]">📄</span> {d.evidence_summary}
                                      </div>
                                    )}
                                    {/* 레퍼런스 — 펼치기 토글 (기본 접힘). 수집/윈도우 메타는 노출 안 함. */}
                                    {refs.length > 0 && (() => {
                                      const refKey = `${selectedDrug.insuranceCode}|${h.date}`;
                                      const isOpen = expandedRefs.has(refKey);
                                      return (
                                        <div className="space-y-1">
                                          <button
                                            onClick={() => toggleRefs(refKey)}
                                            className="text-[10px] text-[#4A5568] hover:text-[#00E5CC] inline-flex items-center gap-1"
                                          >
                                            <i className={`text-[10px] ${isOpen ? 'ri-arrow-down-s-line' : 'ri-arrow-right-s-line'}`}></i>
                                            참고문헌 {refs.length}건
                                          </button>
                                          {isOpen && refs.slice(0, 6).map((ref, i) => {
                                            const w = typeof ref.weight === 'number' ? ref.weight.toFixed(1) : '?';
                                            const media = ref.media || ref.journal || '기타';
                                            const title = (ref.title || ref.url).slice(0, 60);
                                            const pub = ref.published_at || (ref.date_unknown ? '일자 불명' : '');
                                            return (
                                              <div key={i} className="flex items-start gap-1.5 text-[11px] pl-3">
                                                <span
                                                  className="shrink-0 px-1 py-0.5 rounded bg-[#00E5CC]/10 text-[#00E5CC] font-mono text-[10px]"
                                                  title={media}
                                                >
                                                  W{w}
                                                </span>
                                                <span className="shrink-0 text-[10px] text-[#4A5568]">{media}</span>
                                                {pub && <span className="shrink-0 text-[10px] text-[#4A5568]">· {pub}</span>}
                                                <a
                                                  href={ref.url}
                                                  target="_blank"
                                                  rel="noopener noreferrer"
                                                  className="text-[#8B9BB4] hover:text-[#00E5CC] hover:underline truncate"
                                                  title={ref.title || ref.url}
                                                >
                                                  {title}{(ref.title || '').length > 60 ? '…' : ''}
                                                </a>
                                              </div>
                                            );
                                          })}
                                        </div>
                                      );
                                    })()}
                                    {/* notes — LLM 분석 메모만 (enforce/Naver/reviewer 로그는 잘라냄) */}
                                    {(() => {
                                      const rawNotes = (d.notes || '').trim();
                                      const cutMarker = rawNotes.search(/(?:· )?(?:\[enforce\]|\[Naver|\[openai-|\[gemini|\[LOE override\]|ReviewAgent 거부)/);
                                      const cleanNotes = cutMarker > 0 ? rawNotes.slice(0, cutMarker).trim().replace(/[·\s]+$/, '') : (cutMarker === 0 ? '' : rawNotes);
                                      return cleanNotes ? (
                                        <div className="text-[10px] text-[#4A5568] leading-relaxed">
                                          ℹ {cleanNotes}
                                        </div>
                                      ) : null;
                                    })()}
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
      <RsaRegistryModal
        open={rsaModalOpen}
        onClose={() => setRsaModalOpen(false)}
        initialBrandKey={rsaModalDrug?.brand || ''}
        initialIsRsa={rsaModalDrug?.isRsa ?? 1}
        initialRsaType={rsaModalDrug?.type || 'refund'}
        initialRsaNote={rsaModalDrug?.note || ''}
        onSuccess={() => {
          // 등록 후 검색 결과 재가져오기 — selectedDrug 재선택으로 트리거
          if (selectedDrug && search.trim()) {
            const q = search.trim();
            searchDomesticPriceChanges(q).then(setProducts).catch(() => {});
          }
        }}
      />
    </div>
  );
}
