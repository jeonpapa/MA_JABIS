import { useEffect, useState } from 'react';
import { fetchMsdSummary, fetchReimbursedProducts, type MsdSummary, type ReimbursedProduct } from '@/api/msd';
import { listPipeline, type PipelineItem } from '@/api/msdPipeline';
import { ApiError } from '@/api/client';

const CURRENT_YEAR = new Date().getFullYear();

export default function MsdSummaryCards() {
  const [summary, setSummary] = useState<MsdSummary | null>(null);
  const [pipeline, setPipeline] = useState<PipelineItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showKeytrudaDetail, setShowKeytrudaDetail] = useState(false);
  const [showPipelineDetail, setShowPipelineDetail] = useState(false);
  const [pipelineYear, setPipelineYear] = useState<number | null>(null);

  // 급여 품목 모달
  const [showReimbursedModal, setShowReimbursedModal] = useState(false);
  const [reimbursedItems, setReimbursedItems] = useState<ReimbursedProduct[] | null>(null);
  const [reimbursedLoading, setReimbursedLoading] = useState(false);
  const [reimbursedError, setReimbursedError] = useState<string | null>(null);

  const openReimbursedModal = async () => {
    setShowReimbursedModal(true);
    if (reimbursedItems !== null) return;
    setReimbursedLoading(true);
    setReimbursedError(null);
    try {
      const r = await fetchReimbursedProducts();
      setReimbursedItems(r.items);
    } catch (err) {
      setReimbursedError(err instanceof ApiError ? err.message : '목록 조회 실패');
    } finally {
      setReimbursedLoading(false);
    }
  };

  useEffect(() => {
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const [s, p] = await Promise.all([fetchMsdSummary(), listPipeline()]);
        setSummary(s);
        setPipeline(p);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : '요약 조회 실패');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const currentPipeline = pipeline.filter(p => p.status === 'current');
  const year0 = pipeline.filter(p => p.expected_year === CURRENT_YEAR);
  const year1 = pipeline.filter(p => p.expected_year === CURRENT_YEAR + 1);
  const year2 = pipeline.filter(p => p.expected_year === CURRENT_YEAR + 2);

  const yearGroups = [
    { year: CURRENT_YEAR, label: `${CURRENT_YEAR}년`, sublabel: '올해', items: year0, color: '#00E5CC' },
    { year: CURRENT_YEAR + 1, label: `${CURRENT_YEAR + 1}년`, sublabel: '+1년', items: year1, color: '#F59E0B' },
    { year: CURRENT_YEAR + 2, label: `${CURRENT_YEAR + 2}년`, sublabel: '+2년', items: year2, color: '#7C3AED' },
  ];

  const selectedYearGroup = yearGroups.find(g => g.year === pipelineYear);

  return (
    <div className="grid grid-cols-3 gap-4">
      {/* 한국MSD 급여 약제 */}
      <button
        onClick={openReimbursedModal}
        disabled={loading || !!error || !summary}
        className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5 text-left hover:border-[#00E5CC]/40 hover:bg-[#161B27]/80 transition-all cursor-pointer disabled:cursor-not-allowed disabled:opacity-70"
      >
        <div className="flex items-center gap-2 mb-3">
          <span className="w-8 h-8 rounded-lg bg-[#00E5CC]/10 flex items-center justify-center">
            <i className="ri-medicine-bottle-line text-[#00E5CC] text-base"></i>
          </span>
          <div className="flex-1">
            <p className="text-[#8B9BB4] text-xs">한국MSD 급여 등재 품목</p>
          </div>
          <i className="ri-external-link-line text-[#4A5568] text-xs"></i>
        </div>
        <div className="flex items-end gap-2">
          {loading ? (
            <span className="text-4xl font-bold text-[#4A5568]">—</span>
          ) : error ? (
            <span className="text-red-400 text-sm">{error}</span>
          ) : (
            <>
              <span className="text-4xl font-bold text-[#00E5CC]">
                {summary?.reimbursedProductCount.toLocaleString() ?? 0}
              </span>
              <span className="text-[#8B9BB4] text-sm mb-1">개 품목</span>
            </>
          )}
        </div>
        <p className="text-[#4A5568] text-xs mt-2">
          약제급여상한금액 · 고시 기준일 {summary?.latestApplyDate ?? '—'}
        </p>
      </button>

      {/* Keytruda 적응증 현황 — 허가 / 급여 2컬럼 */}
      <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
        <div className="flex items-center gap-2 mb-3">
          <span className="w-8 h-8 rounded-lg bg-[#F59E0B]/10 flex items-center justify-center">
            <i className="ri-capsule-line text-[#F59E0B] text-base"></i>
          </span>
          <p className="text-[#8B9BB4] text-xs">Keytruda 적응증 현황</p>
        </div>
        {loading ? (
          <div className="flex items-end gap-2"><span className="text-3xl font-bold text-[#4A5568]">—</span></div>
        ) : error ? (
          <p className="text-red-400 text-sm">{error}</p>
        ) : summary ? (
          <>
            <div className="grid grid-cols-2 gap-3 mb-3">
              <div className="rounded-xl border border-[#1E2530] bg-[#0D1117] p-3">
                <p className="text-[#8B9BB4] text-[10px] uppercase tracking-wider mb-2">허가 (MFDS)</p>
                <div className="flex items-baseline gap-2">
                  <span className="text-2xl font-bold text-[#00E5CC]">{summary.keytruda.mfdsApproved}</span>
                  <span className="text-[#4A5568] text-xs">/ {summary.keytruda.totalIndications}</span>
                </div>
                <p className="text-[#4A5568] text-[10px] mt-1">
                  국외만 허가 <span className="text-[#F59E0B]">{summary.keytruda.pendingMfds}</span>건
                </p>
              </div>
              <div className="rounded-xl border border-[#1E2530] bg-[#0D1117] p-3">
                <p className="text-[#8B9BB4] text-[10px] uppercase tracking-wider mb-2">급여 (HIRA)</p>
                <div className="flex items-baseline gap-2">
                  <span className="text-2xl font-bold text-[#3B82F6]">{summary.keytruda.reimbursedIndications}</span>
                  <span className="text-[#4A5568] text-xs">/ {summary.keytruda.totalIndications}</span>
                </div>
                <p className="text-[#4A5568] text-[10px] mt-1">
                  비급여 <span className="text-[#FF6B6B]">{summary.keytruda.pendingReimbursement}</span>건
                </p>
              </div>
            </div>
            <button
              onClick={() => setShowKeytrudaDetail(!showKeytrudaDetail)}
              className="text-[#00E5CC] text-xs flex items-center gap-1 cursor-pointer hover:text-[#00C9B1] transition-colors whitespace-nowrap"
            >
              {showKeytrudaDetail ? '접기' : `적응증 목록 보기 (${summary.keytruda.totalIndications}건)`}
              <span className="w-3 h-3 flex items-center justify-center">
                <i className={showKeytrudaDetail ? 'ri-arrow-up-s-line text-xs' : 'ri-arrow-down-s-line text-xs'}></i>
              </span>
            </button>
            {showKeytrudaDetail && (
              <div className="mt-3 max-h-60 overflow-y-auto space-y-1.5 pr-1">
                {summary.keytruda.items.map(ind => (
                  <div key={ind.id} className="flex items-start gap-2">
                    <div className="flex flex-col gap-0.5 flex-shrink-0 mt-0.5">
                      <span className={`text-[10px] px-1.5 py-0.5 rounded text-center ${ind.mfds_approved ? 'bg-[#00E5CC]/10 text-[#00E5CC]' : 'bg-[#F59E0B]/10 text-[#F59E0B]'}`}>
                        {ind.mfds_approved ? '허가' : '해외'}
                      </span>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded text-center ${ind.is_reimbursed ? 'bg-[#3B82F6]/10 text-[#3B82F6]' : 'bg-[#4A5568]/10 text-[#4A5568]'}`}>
                        {ind.is_reimbursed ? '급여' : '비급여'}
                      </span>
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-[#8B9BB4] text-xs leading-relaxed">{ind.title}</p>
                      {ind.pivotal_trial && (
                        <p className="text-[#00E5CC] text-[10px] font-mono mt-0.5">
                          <i className="ri-microscope-line text-[10px] mr-1"></i>
                          {ind.pivotal_trial}
                        </p>
                      )}
                      <div className="flex flex-wrap gap-x-3 text-[10px] mt-0.5">
                        {ind.mfds_date && (
                          <span className="text-[#4A5568]">허가일 {ind.mfds_date}</span>
                        )}
                        {ind.is_reimbursed && ind.reimbursement_effective_date && (
                          <span className="text-[#4A5568]">급여개시 {ind.reimbursement_effective_date}</span>
                        )}
                      </div>
                      {ind.is_reimbursed && ind.reimbursement_criteria && (
                        <p className="text-[#4A5568] text-[10px] mt-0.5 italic">
                          {ind.reimbursement_criteria}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        ) : null}
      </div>

      {/* New Pipeline — 올해/+1/+2년 카드 (Phase 3b 에서 API 연결 예정) */}
      <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
        <div className="flex items-center gap-2 mb-3">
          <span className="w-8 h-8 rounded-lg bg-[#7C3AED]/10 flex items-center justify-center">
            <i className="ri-flask-line text-[#7C3AED] text-base"></i>
          </span>
          <p className="text-[#8B9BB4] text-xs">New Pipeline 현황</p>
        </div>

        <div className="grid grid-cols-4 gap-2 mb-3">
          <button
            onClick={() => {
              setPipelineYear(null);
              setShowPipelineDetail(prev => pipelineYear !== null ? true : !prev);
            }}
            className={`rounded-xl p-2.5 border text-center cursor-pointer transition-all ${
              showPipelineDetail && pipelineYear === null
                ? 'border-[#00E5CC]/50 bg-[#00E5CC]/10'
                : 'border-[#1E2530] hover:border-[#2A3545]'
            }`}
          >
            <p className="text-xl font-bold text-[#00E5CC]">{currentPipeline.length}</p>
            <p className="text-[#4A5568] text-[10px] mt-0.5 leading-tight">현재<br />진행</p>
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
                  : 'border-[#1E2530] hover:border-[#2A3545]'
              }`}
              style={
                showPipelineDetail && pipelineYear === g.year
                  ? { borderColor: `${g.color}80`, backgroundColor: `${g.color}15` }
                  : {}
              }
            >
              <p className="text-xl font-bold" style={{ color: g.color }}>{g.items.length}</p>
              <p className="text-[#4A5568] text-[10px] mt-0.5 leading-tight">
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
          className="text-[#00E5CC] text-xs flex items-center gap-1 cursor-pointer hover:text-[#00C9B1] transition-colors whitespace-nowrap"
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
                <p className="text-[#4A5568] text-[10px] font-semibold uppercase tracking-wider mb-1">현재 진행 중</p>
                {currentPipeline.map(p => (
                  <div key={p.id} className="flex items-start gap-2 py-1">
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#00E5CC]/10 text-[#00E5CC] flex-shrink-0 mt-0.5 whitespace-nowrap">{p.phase ?? '—'}</span>
                    <div>
                      <p className="text-white text-xs font-medium leading-tight">{p.name}</p>
                      <p className="text-[#4A5568] text-[10px]">{p.indication ?? '—'}</p>
                    </div>
                  </div>
                ))}
              </>
            )}

            {pipelineYear !== null && selectedYearGroup && (
              <>
                <p className="text-[10px] font-semibold uppercase tracking-wider mb-1" style={{ color: selectedYearGroup.color }}>
                  {selectedYearGroup.label} 예정 ({selectedYearGroup.items.length}개)
                </p>
                {selectedYearGroup.items.length === 0 && (
                  <p className="text-[#4A5568] text-xs py-2">해당 연도 예정 파이프라인 없음</p>
                )}
                {selectedYearGroup.items.map(p => (
                  <div key={p.id} className="flex items-start gap-2 py-1">
                    <span
                      className="text-[10px] px-1.5 py-0.5 rounded flex-shrink-0 mt-0.5 whitespace-nowrap"
                      style={{ backgroundColor: `${selectedYearGroup.color}20`, color: selectedYearGroup.color }}
                    >
                      {p.phase ?? '—'}
                    </span>
                    <div>
                      <p className="text-white text-xs font-medium leading-tight">{p.name}</p>
                      <p className="text-[#4A5568] text-[10px]">{p.indication ?? '—'}</p>
                    </div>
                  </div>
                ))}
              </>
            )}
          </div>
        )}
      </div>

      {/* 급여 품목 상세 모달 */}
      {showReimbursedModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          onClick={() => setShowReimbursedModal(false)}
        >
          <div
            className="bg-[#161B27] rounded-2xl border border-[#1E2530] w-full max-w-4xl max-h-[85vh] flex flex-col"
            onClick={e => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-6 py-4 border-b border-[#1E2530]">
              <div>
                <h3 className="text-white font-bold text-base">한국MSD 급여 등재 품목 상세</h3>
                <p className="text-[#8B9BB4] text-xs mt-0.5">
                  약제급여상한금액 · 고시 {summary?.latestApplyDate ?? '—'} 기준
                  {reimbursedItems && <span className="ml-2 text-[#00E5CC]">{reimbursedItems.length}개</span>}
                </p>
              </div>
              <button
                onClick={() => setShowReimbursedModal(false)}
                className="w-8 h-8 flex items-center justify-center text-[#8B9BB4] hover:text-white rounded-lg hover:bg-[#1E2530] cursor-pointer"
              >
                <i className="ri-close-line text-lg"></i>
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-6">
              {reimbursedLoading && (
                <div className="text-center py-12 text-[#8B9BB4]">
                  <i className="ri-loader-4-line animate-spin text-2xl text-[#00E5CC]"></i>
                  <p className="text-sm mt-2">불러오는 중...</p>
                </div>
              )}
              {reimbursedError && (
                <div className="text-center py-8 text-red-400 text-sm">{reimbursedError}</div>
              )}
              {reimbursedItems && reimbursedItems.length > 0 && (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-[#1E2530] text-[#8B9BB4] text-xs">
                      <th className="text-left px-3 py-2">제품명</th>
                      <th className="text-left px-3 py-2">성분</th>
                      <th className="text-left px-3 py-2">제형</th>
                      <th className="text-right px-3 py-2">상한금액</th>
                      <th className="text-left px-3 py-2 font-mono">보험코드</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reimbursedItems.map((p, i) => (
                      <tr key={p.insurance_code} className={`border-t border-[#1E2530] ${i % 2 ? 'bg-[#1A2035]/20' : ''}`}>
                        <td className="px-3 py-2 text-white">{p.brand_name || p.product_name}</td>
                        <td className="px-3 py-2 text-[#8B9BB4] text-xs">{p.ingredient || '—'}</td>
                        <td className="px-3 py-2 text-[#8B9BB4] text-xs">{p.dosage_form || '—'}</td>
                        <td className="px-3 py-2 text-[#00E5CC] text-right font-semibold">
                          ₩{p.max_price.toLocaleString()}
                        </td>
                        <td className="px-3 py-2 text-[#4A5568] text-xs font-mono">{p.insurance_code}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {reimbursedItems && reimbursedItems.length === 0 && (
                <div className="text-center py-8 text-[#4A5568] text-sm">등재 품목 없음</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
