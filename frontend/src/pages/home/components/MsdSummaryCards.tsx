import { useState } from 'react';
import {
  fetchMsdSummary,
  fetchPipeline,
  createPipeline,
  fetchReimbursedProducts,
  type PipelineItemView,
  type ReimbursedProductView,
} from '@/api/home';
import { useApi } from '@/hooks/useApi';
import { ApiError } from '@/api/client';
import PipelineModal, { PipelineFormData } from './PipelineModal';

const CURRENT_YEAR = new Date().getFullYear();

export type PipelineItem = PipelineItemView;

interface Props {
  isDark: boolean;
}

export default function MsdSummaryCards({ isDark }: Props) {
  const [showKeytrudaDetail, setShowKeytrudaDetail] = useState(false);
  const [showProductDetail, setShowProductDetail] = useState(false);
  const [showPipelineDetail, setShowPipelineDetail] = useState(false);
  const [pipelineYear, setPipelineYear] = useState<number | null>(null);
  const [showPipelineModal, setShowPipelineModal] = useState(false);
  const [pipelineSubmitError, setPipelineSubmitError] = useState<string | null>(null);

  const { data: summary, loading: summaryLoading, error: summaryError } = useApi(fetchMsdSummary, []);
  const { data: pipelineData, loading: pipelineLoading, error: pipelineError, reload: reloadPipeline } = useApi(fetchPipeline, []);

  // 급여 품목 상세 — 첫 펼침 시 lazy 조회
  const [products, setProducts] = useState<ReimbursedProductView[] | null>(null);
  const [productsLoading, setProductsLoading] = useState(false);
  const [productsError, setProductsError] = useState<string | null>(null);

  const toggleProductDetail = async () => {
    const next = !showProductDetail;
    setShowProductDetail(next);
    if (!next || products !== null || productsLoading) return;
    setProductsLoading(true);
    setProductsError(null);
    try {
      const r = await fetchReimbursedProducts();
      setProducts(r.items);
    } catch (err) {
      setProductsError(err instanceof ApiError ? err.message : '품목 조회 실패');
    } finally {
      setProductsLoading(false);
    }
  };

  const allPipelineData: PipelineItem[] = pipelineData ?? [];
  const customCount = allPipelineData.filter(p => p.isCustom).length;

  const currentPipeline = allPipelineData.filter(p => p.status === 'current');
  const year0 = allPipelineData.filter(p => p.expectedYear === CURRENT_YEAR);
  const year1 = allPipelineData.filter(p => p.expectedYear === CURRENT_YEAR + 1);
  const year2 = allPipelineData.filter(p => p.expectedYear === CURRENT_YEAR + 2);

  const yearGroups = [
    { year: CURRENT_YEAR, label: `${CURRENT_YEAR}년`, sublabel: '올해', items: year0, color: '#0D9488' },
    { year: CURRENT_YEAR + 1, label: `${CURRENT_YEAR + 1}년`, sublabel: '+1년', items: year1, color: '#D97706' },
    { year: CURRENT_YEAR + 2, label: `${CURRENT_YEAR + 2}년`, sublabel: '+2년', items: year2, color: '#7C3AED' },
  ];

  const selectedYearGroup = yearGroups.find(g => g.year === pipelineYear);

  const handleAddPipeline = async (data: PipelineFormData) => {
    setPipelineSubmitError(null);
    try {
      await createPipeline({
        name: data.clinicalCode,
        phase: data.phase,
        indication: data.indication,
        expectedYear: CURRENT_YEAR,
        status: 'current',
        drugClass: data.drugClass,
        targetDisease: data.targetDisease,
        domesticApprovalDate: data.domesticApprovalDate,
        domesticReimbursementDate: data.domesticReimbursementDate,
      });
      reloadPipeline();
      setShowPipelineDetail(true);
    } catch (err) {
      setPipelineSubmitError(err instanceof ApiError ? err.message : '파이프라인 등록 실패');
    }
  };

  const cardBg = isDark ? 'bg-[#161B27]' : 'bg-white';
  const cardBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
  const textMuted = isDark ? 'text-[#4A5568]' : 'text-gray-400';
  const accentTeal = isDark ? 'text-[#00E5CC]' : 'text-teal-600';
  const accentTealBg = isDark ? 'bg-[#00E5CC]/10' : 'bg-teal-50';
  const accentAmber = isDark ? 'text-[#F59E0B]' : 'text-amber-600';
  const accentAmberBg = isDark ? 'bg-[#F59E0B]/10' : 'bg-amber-50';
  const accentPurple = isDark ? 'text-[#7C3AED]' : 'text-purple-600';
  const accentPurpleBg = isDark ? 'bg-[#7C3AED]/10' : 'bg-purple-50';
  const divider = isDark ? 'bg-[#1E2530]' : 'bg-gray-200';
  const detailBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const detailHover = isDark ? 'hover:border-[#2A3545]' : 'hover:border-gray-300';
  const phaseBgTeal = isDark ? 'bg-[#00E5CC]/10 text-[#00E5CC]' : 'bg-teal-100 text-teal-700';
  const indentBg = isDark ? 'bg-[#00E5CC]/10' : 'bg-teal-100';
  const indentText = isDark ? 'text-[#00E5CC]' : 'text-teal-700';
  const indentBgAmber = isDark ? 'bg-[#F59E0B]/10' : 'bg-amber-100';
  const indentTextAmber = isDark ? 'text-[#F59E0B]' : 'text-amber-700';
  const customBorder = isDark ? 'border-[#7C3AED]/40' : 'border-purple-300';
  const customBg = isDark ? 'bg-[#7C3AED]/5' : 'bg-purple-50/50';

  return (
    <div className="grid grid-cols-3 gap-4">
      {/* 한국MSD 급여 약제 */}
      <div className={`${cardBg} rounded-2xl border ${cardBorder} p-5`}>
        <div className="flex items-center gap-2 mb-3">
          <span className={`w-8 h-8 rounded-lg flex items-center justify-center ${accentTealBg}`}>
            <i className={`ri-medicine-bottle-line ${accentTeal} text-base`}></i>
          </span>
          <div>
            <p className={`${textSub} text-xs`}>한국MSD 현재 급여 약제</p>
          </div>
        </div>
        <div className="flex items-end gap-2">
          {summaryLoading ? (
            <span className={`text-4xl font-bold ${textMuted}`}>—</span>
          ) : summaryError ? (
            <span className="text-red-500 text-sm">조회 실패 — {summaryError}</span>
          ) : (
            <>
              <span className={`text-4xl font-bold ${accentTeal}`}>{summary?.total ?? 0}</span>
              <span className={`${textSub} text-sm mb-1`}>개 품목</span>
            </>
          )}
        </div>
        <p className={`${textMuted} text-xs mt-2`}>
          건강보험 급여 등재 기준 (고시 {summary?.latestApplyDate ?? '—'})
        </p>
        {!summaryLoading && !summaryError && (
          <button
            onClick={toggleProductDetail}
            className={`${accentTeal} text-xs flex items-center gap-1 cursor-pointer hover:opacity-80 transition-colors whitespace-nowrap mt-2`}
          >
            {showProductDetail ? '접기' : '품목 목록 보기'}
            <span className="w-3 h-3 flex items-center justify-center">
              <i className={showProductDetail ? 'ri-arrow-up-s-line text-xs' : 'ri-arrow-down-s-line text-xs'}></i>
            </span>
          </button>
        )}
        {showProductDetail && (
          <div className="mt-3 max-h-48 overflow-y-auto space-y-1.5 pr-1">
            {productsLoading && <p className={`${textMuted} text-xs py-2`}>불러오는 중...</p>}
            {productsError && <p className="text-red-500 text-xs py-2">{productsError}</p>}
            {!productsLoading && !productsError && products && products.length === 0 && (
              <p className={`${textMuted} text-xs py-2`}>등재 품목 정보 없음</p>
            )}
            {!productsLoading && !productsError && products?.map((p) => (
              <div key={p.insuranceCode} className="flex items-start gap-2">
                <span className={`${textSub} text-xs leading-relaxed flex-1 min-w-0 truncate`} title={p.name}>{p.name}</span>
                <span className={`${accentTeal} text-xs font-semibold flex-shrink-0 tabular-nums`}>
                  {p.maxPrice > 0 ? `₩${p.maxPrice.toLocaleString()}` : '—'}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Keytruda 급여/비급여 */}
      <div className={`${cardBg} rounded-2xl border ${cardBorder} p-5`}>
        <div className="flex items-center gap-2 mb-3">
          <span className={`w-8 h-8 rounded-lg flex items-center justify-center ${accentAmberBg}`}>
            <i className={`ri-capsule-line ${accentAmber} text-base`}></i>
          </span>
          <p className={`${textSub} text-xs`}>Keytruda 적응증 현황</p>
        </div>
        {summaryLoading ? (
          <div className="flex items-center gap-4 mb-3">
            <span className={`text-3xl font-bold ${textMuted}`}>—</span>
          </div>
        ) : summaryError ? (
          <p className="text-red-500 text-sm mb-3">조회 실패 — {summaryError}</p>
        ) : (
          <>
            <div className="flex items-center gap-4 mb-3">
              <div>
                <p className={`text-3xl font-bold ${accentTeal}`}>{summary?.keytruda.reimbursed ?? 0}</p>
                <p className={`${textSub} text-xs mt-0.5`}>급여 적응증</p>
              </div>
              <div className={`w-px h-10 ${divider}`}></div>
              <div>
                <p className={`text-3xl font-bold ${accentAmber}`}>{summary?.keytruda.nonReimbursedApproved ?? 0}</p>
                <p className={`${textSub} text-xs mt-0.5`}>비급여 허가</p>
              </div>
            </div>
            <button
              onClick={() => setShowKeytrudaDetail(!showKeytrudaDetail)}
              className={`${accentTeal} text-xs flex items-center gap-1 cursor-pointer hover:opacity-80 transition-colors whitespace-nowrap`}
            >
              {showKeytrudaDetail ? '접기' : '적응증 목록 보기'}
              <span className="w-3 h-3 flex items-center justify-center">
                <i className={showKeytrudaDetail ? 'ri-arrow-up-s-line text-xs' : 'ri-arrow-down-s-line text-xs'}></i>
              </span>
            </button>
            {showKeytrudaDetail && (
              <div className="mt-3 max-h-48 overflow-y-auto space-y-1.5 pr-1">
                {(summary?.keytruda.indications ?? []).length === 0 && (
                  <p className={`${textMuted} text-xs py-2`}>적응증 정보 없음</p>
                )}
                {(summary?.keytruda.indications ?? []).map((ind, i) => (
                  <div key={i} className="flex items-start gap-2">
                    <span className={`text-xs px-1.5 py-0.5 rounded flex-shrink-0 mt-0.5 ${ind.type === '급여' ? indentBg + ' ' + indentText : indentBgAmber + ' ' + indentTextAmber}`}>
                      {ind.type}
                    </span>
                    <span className={`${textSub} text-xs leading-relaxed`}>{ind.name}</span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {/* New Pipeline */}
      <div className={`${cardBg} rounded-2xl border ${cardBorder} p-5 relative`}>
        {/* 설정 아이콘 */}
        <button
          onClick={() => setShowPipelineModal(true)}
          className={`absolute top-4 right-4 w-8 h-8 rounded-lg flex items-center justify-center cursor-pointer transition-all ${
            isDark
              ? 'bg-[#1E2530] text-[#8B9BB4] hover:bg-[#2A3545] hover:text-white'
              : 'bg-gray-100 text-gray-400 hover:bg-gray-200 hover:text-gray-700'
          }`}
          title="파이프라인 설정"
        >
          <i className="ri-settings-3-line text-base"></i>
        </button>

        <div className="flex items-center gap-2 mb-3">
          <span className={`w-8 h-8 rounded-lg flex items-center justify-center ${accentPurpleBg}`}>
            <i className={`ri-flask-line ${accentPurple} text-base`}></i>
          </span>
          <p className={`${textSub} text-xs`}>New Pipeline 현황</p>
          {customCount > 0 && (
            <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${isDark ? 'bg-[#7C3AED]/20 text-[#7C3AED]' : 'bg-purple-100 text-purple-600'}`}>
              +{customCount}
            </span>
          )}
        </div>

        {pipelineError && (
          <p className="text-red-500 text-xs mb-2">파이프라인 조회 실패 — {pipelineError}</p>
        )}
        {pipelineSubmitError && (
          <p className="text-red-500 text-xs mb-2">{pipelineSubmitError}</p>
        )}

        <div className="grid grid-cols-4 gap-2 mb-3">
          <button
            onClick={() => {
              setPipelineYear(null);
              setShowPipelineDetail(prev => pipelineYear !== null ? true : !prev);
            }}
            className={`rounded-xl p-2.5 border text-center cursor-pointer transition-all ${
              showPipelineDetail && pipelineYear === null
                ? 'border-teal-400/50 bg-teal-50'
                : `${detailBorder} ${detailHover}`
            }`}
          >
            <p className={`text-xl font-bold ${pipelineLoading ? textMuted : accentTeal}`}>
              {pipelineLoading ? '—' : currentPipeline.length}
            </p>
            <p className={`${textMuted} text-[10px] mt-0.5 leading-tight`}>현재<br />진행</p>
          </button>

          {yearGroups.map(g => (
            <button
              key={g.year}
              onClick={() => {
                if (pipelineYear === g.year && showPipelineDetail) {
                  setShowPipelineDetail(false);
                  setPipelineYear(null);
                } else {
                  setPipelineYear(g.year);
                  setShowPipelineDetail(true);
                }
              }}
              className={`rounded-xl p-2.5 border text-center cursor-pointer transition-all ${
                showPipelineDetail && pipelineYear === g.year
                  ? 'border-opacity-50 bg-opacity-10'
                  : `${detailBorder} ${detailHover}`
              }`}
              style={
                showPipelineDetail && pipelineYear === g.year
                  ? { borderColor: `${g.color}80`, backgroundColor: `${g.color}15` }
                  : {}
              }
            >
              <p className="text-xl font-bold" style={{ color: pipelineLoading ? undefined : g.color }}>
                {pipelineLoading ? '—' : g.items.length}
              </p>
              <p className={`${textMuted} text-[10px] mt-0.5 leading-tight`}>
                {g.sublabel}<br />{g.label.replace('년', '')}
              </p>
            </button>
          ))}
        </div>

        <button
          onClick={() => {
            if (!showPipelineDetail) {
              setShowPipelineDetail(true);
              setPipelineYear(null);
            } else {
              setShowPipelineDetail(false);
              setPipelineYear(null);
            }
          }}
          className={`${accentTeal} text-xs flex items-center gap-1 cursor-pointer hover:opacity-80 transition-colors whitespace-nowrap`}
        >
          {showPipelineDetail ? '접기' : '파이프라인 목록 보기'}
          <span className="w-3 h-3 flex items-center justify-center">
            <i className={showPipelineDetail ? 'ri-arrow-up-s-line text-xs' : 'ri-arrow-down-s-line text-xs'}></i>
          </span>
        </button>

        {showPipelineDetail && (
          <div className="mt-3 max-h-52 overflow-y-auto space-y-1.5 pr-1">
            {pipelineYear === null && (
              <>
                <p className={`${textMuted} text-[10px] font-semibold uppercase tracking-wider mb-1`}>현재 진행 중</p>
                {currentPipeline.map((p, i) => (
                  <div key={p.id ?? i} className={`flex items-start gap-2 py-1.5 px-2 rounded-lg ${p.isCustom ? customBg + ' ' + customBorder + ' border' : ''}`}>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded flex-shrink-0 mt-0.5 whitespace-nowrap ${p.isCustom ? (isDark ? 'bg-[#7C3AED]/20 text-[#7C3AED]' : 'bg-purple-100 text-purple-600') : phaseBgTeal}`}>{p.phase}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <p className={`${textMain} text-xs font-medium leading-tight`}>{p.name}</p>
                        {p.isCustom && (
                          <span className={`text-[9px] px-1 py-0.5 rounded font-medium ${isDark ? 'bg-[#7C3AED]/20 text-[#7C3AED]' : 'bg-purple-100 text-purple-600'}`}>custom</span>
                        )}
                      </div>
                      <p className={`${textMuted} text-[10px]`}>{p.indication}</p>
                      {p.isCustom && (p.drugClass || p.targetDisease) && (
                        <p className={`${textMuted} text-[10px] mt-0.5`}>
                          {[p.drugClass, p.targetDisease].filter(Boolean).join(' · ')}
                        </p>
                      )}
                      {p.isCustom && (p.domesticApprovalDate || p.domesticReimbursementDate) && (
                        <p className={`${textMuted} text-[10px]`}>
                          {p.domesticApprovalDate && <span>허가: {p.domesticApprovalDate}</span>}
                          {p.domesticApprovalDate && p.domesticReimbursementDate && <span className="mx-1">|</span>}
                          {p.domesticReimbursementDate && <span>급여: {p.domesticReimbursementDate}</span>}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
                {currentPipeline.length === 0 && (
                  <p className={`${textMuted} text-xs py-2`}>현재 진행 중인 파이프라인이 없습니다</p>
                )}
              </>
            )}

            {pipelineYear !== null && selectedYearGroup && (
              <>
                <p className="text-[10px] font-semibold uppercase tracking-wider mb-1" style={{ color: selectedYearGroup.color }}>
                  {selectedYearGroup.label} 예정 ({selectedYearGroup.items.length}개)
                </p>
                {selectedYearGroup.items.length === 0 && (
                  <p className={`${textMuted} text-xs py-2`}>해당 연도 예정 파이프라인 없음</p>
                )}
                {selectedYearGroup.items.map((p, i) => (
                  <div key={p.id ?? i} className={`flex items-start gap-2 py-1.5 px-2 rounded-lg ${p.isCustom ? customBg + ' ' + customBorder + ' border' : ''}`}>
                    <span
                      className="text-[10px] px-1.5 py-0.5 rounded flex-shrink-0 mt-0.5 whitespace-nowrap"
                      style={p.isCustom ? {} : { backgroundColor: `${selectedYearGroup.color}20`, color: selectedYearGroup.color }}
                    >
                      {p.phase}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <p className={`${textMain} text-xs font-medium leading-tight`}>{p.name}</p>
                        {p.isCustom && (
                          <span className={`text-[9px] px-1 py-0.5 rounded font-medium ${isDark ? 'bg-[#7C3AED]/20 text-[#7C3AED]' : 'bg-purple-100 text-purple-600'}`}>custom</span>
                        )}
                      </div>
                      <p className={`${textMuted} text-[10px]`}>{p.indication}</p>
                      {p.isCustom && (p.drugClass || p.targetDisease) && (
                        <p className={`${textMuted} text-[10px] mt-0.5`}>
                          {[p.drugClass, p.targetDisease].filter(Boolean).join(' · ')}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </>
            )}
          </div>
        )}
      </div>

      {/* Pipeline Modal */}
      {showPipelineModal && (
        <PipelineModal
          isDark={isDark}
          pipelines={allPipelineData}
          onClose={() => setShowPipelineModal(false)}
          onAdd={handleAddPipeline}
        />
      )}
    </div>
  );
}
