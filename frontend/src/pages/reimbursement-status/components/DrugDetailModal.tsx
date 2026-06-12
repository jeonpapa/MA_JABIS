import { useEffect, useRef } from 'react';
import { PipelineDrug, DrugHistoryItem, stateKind } from '@/api/reimbStatus';

interface DrugDetailModalProps {
  drug: PipelineDrug;
  stageLabel: string;
  isDark: boolean;
  onClose: () => void;
}

function historyDetail(h: DrugHistoryItem): string {
  const parts: string[] = [];
  if (h.cycle != null) parts.push(`${h.cycle}차 회차`);
  if (h.sessionDate) parts.push(`회의일 ${h.sessionDate}`);
  if (h.attempt != null) parts.push(`${h.attempt}차 시도`);
  return parts.length > 0 ? parts.join(' · ') : '—';
}

export default function DrugDetailModal({ drug, stageLabel, isDark, onClose }: DrugDetailModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleEsc);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', handleEsc);
      document.body.style.overflow = '';
    };
  }, [onClose]);

  const statusLabel = drug.status === 'scheduled'
    ? `심의 상정예정${drug.expectedSessionCycle ? ` · ${drug.expectedSessionCycle}차` : ''}`
    : drug.status === 'completed' ? '협상완료'
    : drug.status === 'negotiating' ? '협상중' : '심의 대기';
  const statusColor = drug.status === 'scheduled'
    ? 'text-teal-600 bg-teal-50 border-teal-200'
    : drug.status === 'completed'
    ? 'text-emerald-600 bg-emerald-50 border-emerald-200'
    : drug.status === 'negotiating'
    ? 'text-sky-600 bg-sky-50 border-sky-200'
    : 'text-amber-600 bg-amber-50 border-amber-200';
  const statusColorDark = drug.status === 'scheduled'
    ? 'text-teal-300 bg-teal-400/10 border-teal-400/30'
    : drug.status === 'completed'
    ? 'text-emerald-300 bg-emerald-400/10 border-emerald-400/30'
    : drug.status === 'negotiating'
    ? 'text-sky-300 bg-sky-400/10 border-sky-400/30'
    : 'text-amber-300 bg-amber-400/10 border-amber-400/30';

  const modalBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200';
  const headerBg = isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-100';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
  const textMuted = isDark ? 'text-[#5A6A80]' : 'text-gray-400';
  const infoBg = isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-50 border-gray-100';
  const infoLabel = isDark ? 'text-[#5A6A80]' : 'text-gray-400';
  const infoValue = isDark ? 'text-white' : 'text-gray-900';
  const sectionTitle = isDark ? 'text-[#6B7A90]' : 'text-gray-400';
  const contentBg = isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-50 border-gray-100';
  const contentText = isDark ? 'text-[#8B9BB4]' : 'text-gray-700';
  const dividerColor = isDark ? 'bg-[#2A3545]' : 'bg-gray-200';
  const closeHover = isDark ? 'hover:bg-[#1E2530] text-[#6B7A90] hover:text-white' : 'hover:bg-gray-100 text-gray-400 hover:text-gray-600';

  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      onClick={(e) => { if (e.target === overlayRef.current) onClose(); }}
    >
      <div className={`rounded-2xl border w-full max-w-2xl max-h-[85vh] overflow-y-auto shadow-xl ${modalBg}`}>
        {/* Header */}
        <div className={`sticky top-0 border-b px-6 py-4 rounded-t-2xl flex items-start justify-between gap-4 z-10 ${headerBg}`}>
          <div className="min-w-0">
            <div className="flex items-center gap-2 mb-1.5">
              <span className={`text-xs font-medium ${textMuted}`}>{drug.type ?? (drug.msdFlag ? 'MSD 자산' : '—')}</span>
              <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${isDark ? statusColorDark : statusColor}`}>
                {statusLabel}
              </span>
            </div>
            <h2 className={`text-xl font-bold ${textMain}`}>{drug.name}</h2>
            <p className={`text-sm mt-0.5 ${textSub}`}>
              {drug.ingredient ?? '성분 정보 없음'}{drug.nameEn ? ` · ${drug.nameEn}` : ''}
            </p>
          </div>
          <button
            onClick={onClose}
            className={`w-9 h-9 flex items-center justify-center rounded-lg cursor-pointer flex-shrink-0 transition-colors ${closeHover}`}
          >
            <i className="ri-close-line text-lg"></i>
          </button>
        </div>

        <div className="px-6 py-5 space-y-5">
          {/* Basic Info */}
          <div className="grid grid-cols-2 gap-3">
            <div className={`rounded-lg p-3 border ${infoBg}`}>
              <p className={`text-[10px] font-medium mb-0.5 ${infoLabel}`}>현재 단계</p>
              <p className={`text-sm font-bold ${infoValue}`}>{stageLabel}</p>
            </div>
            <div className={`rounded-lg p-3 border ${infoBg}`}>
              <p className={`text-[10px] font-medium mb-0.5 ${infoLabel}`}>회사</p>
              <p className={`text-sm font-bold ${infoValue}`}>{drug.company ?? '정보 없음'}</p>
            </div>
            <div className={`rounded-lg p-3 border ${infoBg}`}>
              <p className={`text-[10px] font-medium mb-0.5 ${infoLabel}`}>최초 신청일</p>
              <p className={`text-sm font-bold ${infoValue}`}>{drug.submittedDate ?? '정보 없음'}</p>
            </div>
            <div className={`rounded-lg p-3 border ${infoBg}`}>
              <p className={`text-[10px] font-medium mb-0.5 ${infoLabel}`}>암질심 통과일</p>
              <p className={`text-sm font-bold ${infoValue}`}>{drug.amjilsimPassDate ?? '정보 없음'}</p>
            </div>
            <div className={`rounded-lg p-3 border ${infoBg}`}>
              <p className={`text-[10px] font-medium mb-0.5 ${infoLabel}`}>약평위 통과일</p>
              <p className={`text-sm font-bold ${infoValue}`}>{drug.yakpyungwiPassDate ?? '정보 없음'}</p>
            </div>
            {drug.status === 'scheduled' && drug.expectedSessionDate && (
              <div className={`rounded-lg p-3 border ${isDark ? 'bg-teal-400/5 border-teal-400/20' : 'bg-teal-50/60 border-teal-100'}`}>
                <p className={`text-[10px] font-medium mb-0.5 ${isDark ? 'text-teal-400/70' : 'text-teal-500'}`}>상정 예정</p>
                <p className={`text-sm font-bold ${isDark ? 'text-teal-300' : 'text-teal-700'}`}>
                  {drug.expectedSessionDate}{drug.expectedSessionCycle ? ` (${drug.expectedSessionCycle}차)` : ''}
                </p>
              </div>
            )}
          </div>

          {/* 핵심 쟁점 — D±1 보고서 전사 인사이트 (이슈 ②) */}
          {drug.keyIssues.length > 0 && (
            <div className={`rounded-xl p-4 border ${
              isDark ? 'bg-[#00E5CC]/5 border-[#00E5CC]/20' : 'bg-teal-50/70 border-teal-200'
            }`}>
              <h3 className={`text-xs font-bold uppercase tracking-wider mb-2.5 flex items-center gap-1.5 ${
                isDark ? 'text-[#00E5CC]' : 'text-teal-600'
              }`}>
                <i className="ri-lightbulb-flash-line text-sm"></i>핵심 쟁점
              </h3>
              <ul className="space-y-2">
                {drug.keyIssues.map((issue, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <span className={`mt-1.5 w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                      isDark ? 'bg-[#00E5CC]' : 'bg-teal-500'
                    }`} />
                    <span className={`text-sm leading-relaxed ${isDark ? 'text-[#C9D1D9]' : 'text-gray-700'}`}>
                      {issue}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Indication */}
          <div>
            <h3 className={`text-xs font-bold uppercase tracking-wider mb-2 ${sectionTitle}`}>적응증</h3>
            <p className={`text-sm leading-relaxed rounded-lg p-3 border ${contentBg} ${contentText}`}>
              {drug.indication ?? '정보 없음'}
            </p>
          </div>

          {/* Notes */}
          {drug.notes && (
            <div>
              <h3 className={`text-xs font-bold uppercase tracking-wider mb-2 ${sectionTitle}`}>현황 노트</h3>
              <p className={`text-sm leading-relaxed ${contentText}`}>{drug.notes}</p>
            </div>
          )}

          {/* Review History */}
          <div>
            <h3 className={`text-xs font-bold uppercase tracking-wider mb-2 ${sectionTitle}`}>과거 심의 이력</h3>
            {drug.history.length === 0 ? (
              <p className={`text-sm rounded-lg p-3 border text-center ${contentBg} ${textMuted}`}>
                등록된 심의 이력 없음
              </p>
            ) : (
              <div className="space-y-2">
                {drug.history.map((h, i) => {
                  const kind = stateKind(h.state, h.stateLabel);
                  return (
                    <div key={h.id ?? i} className={`flex items-start gap-3 rounded-lg p-3 border ${infoBg}`}>
                      <div className="flex flex-col items-center flex-shrink-0">
                        <span className={`w-2.5 h-2.5 rounded-full ${
                          kind === 'approved' ? 'bg-emerald-500' : kind === 'rejected' ? 'bg-red-400' : 'bg-amber-400'
                        }`} />
                        {i < drug.history.length - 1 && <div className={`w-px h-full mt-1 ${dividerColor}`} />}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                          <span className={`text-xs ${textMuted}`}>{h.date ?? '—'}</span>
                          <span className={`text-[10px] font-semibold ${textSub}`}>{h.committee}</span>
                          <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                            kind === 'approved'
                              ? 'text-emerald-700 bg-emerald-50'
                              : kind === 'rejected'
                              ? 'text-red-600 bg-red-50'
                              : 'text-amber-600 bg-amber-50'
                          }`}>
                            {h.stateLabel || h.state}
                          </span>
                        </div>
                        <p className={`text-xs ${contentText}`}>
                          {historyDetail(h)}
                          {h.synthetic && (
                            <span className={`ml-1.5 text-[10px] px-1.5 py-0.5 rounded ${
                              isDark ? 'bg-[#1E2530] text-[#6B7A90]' : 'bg-gray-100 text-gray-400'
                            }`} title="통과일 컬럼 기반 — 개별 회차 큐 기록 미수집">통과일 기준</span>
                          )}
                          {h.evidenceUrl && (
                            <>
                              {' · '}
                              <a
                                href={h.evidenceUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className={`underline ${isDark ? 'text-teal-400' : 'text-teal-600'}`}
                              >
                                근거 자료
                              </a>
                            </>
                          )}
                        </p>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Expected Timeline */}
          <div>
            <h3 className={`text-xs font-bold uppercase tracking-wider mb-2 ${sectionTitle}`}>예상 타임라인</h3>
            <div className="relative">
              {drug.timeline.map((t, i) => (
                <div key={i} className="flex items-start gap-3 pb-3 last:pb-0">
                  <div className="flex flex-col items-center flex-shrink-0 pt-0.5">
                    <span className={`w-3.5 h-3.5 rounded-full border-2 ${
                      t.status === 'done'
                        ? 'bg-emerald-500 border-emerald-500'
                        : t.status === 'in_progress'
                        ? 'bg-white border-teal-500 ring-2 ring-teal-100'
                        : t.status === 'expected'
                        ? (isDark ? 'bg-teal-400/20 border-teal-400' : 'bg-teal-50 border-teal-400')
                        : t.status === 'rejected'
                        ? 'bg-red-400 border-red-400'
                        : isDark ? 'bg-[#161B27] border-[#2A3545]' : 'bg-white border-gray-300'
                    }`} />
                    {i < drug.timeline.length - 1 && (
                      <div className={`w-0.5 h-7 mt-0.5 ${
                        drug.timeline[i + 1].status === 'done'
                          ? 'bg-emerald-300'
                          : isDark ? 'bg-[#2A3545]' : 'bg-gray-200'
                      }`} />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className={`text-sm font-bold ${
                      t.status === 'done'
                        ? textSub
                        : t.status === 'in_progress'
                        ? (isDark ? 'text-teal-400' : 'text-teal-700')
                        : t.status === 'expected'
                        ? (isDark ? 'text-teal-400/80' : 'text-teal-600')
                        : t.status === 'rejected'
                        ? (isDark ? 'text-red-400' : 'text-red-500')
                        : textMuted
                    }`}>
                      {t.phase}
                      {t.negotiationStatus ? ` · ${t.negotiationStatus}` : ''}
                    </p>
                    <p className={`text-xs ${
                      t.status === 'in_progress' ? (isDark ? 'text-teal-300' : 'text-teal-600')
                      : t.status === 'expected' ? (isDark ? 'text-teal-400/70' : 'text-teal-500')
                      : textMuted
                    }`}>
                      {t.date ? t.date
                        : t.expectedDate ? `예상 ${t.expectedDate}`
                        : t.status === 'expected' ? '예정일 미정' : '미정'}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
