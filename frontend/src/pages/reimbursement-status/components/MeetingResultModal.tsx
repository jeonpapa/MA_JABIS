import { useEffect } from 'react';
import { useApi } from '@/hooks/useApi';
import { fetchMeetingResults, stateKind, MeetingSchedule, ResultKind } from '@/api/reimbStatus';

interface Props {
  meeting: MeetingSchedule;
  isDark: boolean;
  onClose: () => void;
}

export default function MeetingResultModal({ meeting, isDark, onClose }: Props) {
  const { data, loading, error, reload } = useApi(() => fetchMeetingResults(meeting.id), [meeting.id]);

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

  const isCancer = meeting.type === 'cancer';

  const resultColor = (r: ResultKind) => {
    if (r === 'approved') return isDark ? 'text-emerald-400' : 'text-emerald-600';
    if (r === 'rejected') return isDark ? 'text-red-400' : 'text-red-500';
    return isDark ? 'text-amber-400' : 'text-amber-600';
  };

  const resultBg = (r: ResultKind) => {
    if (r === 'approved') return isDark ? 'bg-emerald-400/10 border-emerald-400/25' : 'bg-emerald-50 border-emerald-200';
    if (r === 'rejected') return isDark ? 'bg-red-400/10 border-red-400/25' : 'bg-red-50 border-red-200';
    return isDark ? 'bg-amber-400/10 border-amber-400/25' : 'bg-amber-50 border-amber-200';
  };

  const resultDot = (r: ResultKind) => {
    if (r === 'approved') return isDark ? 'bg-emerald-400' : 'bg-emerald-500';
    if (r === 'rejected') return isDark ? 'bg-red-400' : 'bg-red-400';
    return isDark ? 'bg-amber-400' : 'bg-amber-400';
  };

  const typeBadge = isCancer
    ? (isDark ? 'bg-teal-400/10 text-teal-300 border-teal-400/25' : 'bg-teal-50 text-teal-700 border-teal-200')
    : (isDark ? 'bg-violet-400/10 text-violet-300 border-violet-400/25' : 'bg-violet-50 text-violet-700 border-violet-200');

  const typeLabel = isCancer ? '암질환심의위원회' : '약제급여평가위원회';
  const typeIcon = isCancer ? 'ri-microscope-line' : 'ri-scales-line';

  const title = data?.report?.title ?? `${meeting.year}년 ${meeting.cycle} ${typeLabel} 결과`;
  const totals = data?.totals;
  const drugs = data?.drugs ?? [];
  const report = data?.report ?? null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className={`absolute inset-0 ${isDark ? 'bg-black/70' : 'bg-black/40'} backdrop-blur-sm`} />
      <div
        onClick={e => e.stopPropagation()}
        className={`relative w-full max-w-2xl max-h-[85vh] overflow-y-auto rounded-2xl border shadow-2xl ${
          isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200'
        }`}
      >
        <button
          onClick={onClose}
          className={`absolute top-4 right-4 w-9 h-9 flex items-center justify-center rounded-full cursor-pointer transition-colors z-10 ${
            isDark ? 'bg-[#0D1117] text-[#8B9BB4] hover:text-white hover:bg-[#1E2530]' : 'bg-gray-100 text-gray-400 hover:text-gray-700 hover:bg-gray-200'
          }`}
        >
          <i className="ri-close-line text-lg"></i>
        </button>

        <div className="p-6">
          <div className="flex items-center gap-3 mb-1 flex-wrap">
            <span className={`text-[11px] font-bold px-2.5 py-1 rounded-full border whitespace-nowrap ${typeBadge}`}>
              <span className={`w-3.5 h-3.5 flex items-center justify-center mr-1.5 inline-flex`}>
                <i className={`${typeIcon} text-xs`}></i>
              </span>
              {typeLabel} {meeting.cycle}
            </span>
            <span className={`text-xs ${isDark ? 'text-[#5A6A80]' : 'text-gray-400'}`}>
              {meeting.date}
            </span>
          </div>

          <h2 className={`text-xl font-bold mb-5 mt-2 ${isDark ? 'text-white' : 'text-gray-900'}`}>
            {title}
          </h2>

          {loading && (
            <div className={`flex items-center justify-center gap-2 py-14 text-sm ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
              <i className="ri-loader-4-line animate-spin text-lg"></i>
              심의 결과 불러오는 중...
            </div>
          )}

          {!loading && error && (
            <div className={`text-center py-14 ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
              <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3 text-red-400">
                <i className="ri-error-warning-line text-3xl"></i>
              </span>
              <p className="text-sm mb-3">심의 결과를 불러오지 못했습니다: {error}</p>
              <button
                onClick={reload}
                className="px-4 py-2 rounded-lg text-xs font-semibold bg-teal-500 text-white hover:bg-teal-600 cursor-pointer transition-colors"
              >
                다시 시도
              </button>
            </div>
          )}

          {!loading && !error && data && (
            <>
              <div className={`grid grid-cols-3 gap-3 mb-6`}>
                <div className={`rounded-xl border p-3.5 text-center ${
                  isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-50 border-gray-100'
                }`}>
                  <p className={`text-2xl font-black ${isDark ? 'text-white' : 'text-gray-900'}`}>{totals?.reviewed ?? 0}</p>
                  <p className={`text-[11px] font-semibold ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>총 심의</p>
                </div>
                <div className={`rounded-xl border p-3.5 text-center ${
                  isDark ? 'bg-emerald-400/5 border-emerald-400/15' : 'bg-emerald-50 border-emerald-100'
                }`}>
                  <p className="text-2xl font-black text-emerald-500">{totals?.approved ?? 0}</p>
                  <p className={`text-[11px] font-semibold ${isDark ? 'text-emerald-400/70' : 'text-emerald-600'}`}>설정·통과</p>
                </div>
                <div className={`rounded-xl border p-3.5 text-center ${
                  isDark ? 'bg-red-400/5 border-red-400/15' : 'bg-red-50 border-red-100'
                }`}>
                  <p className="text-2xl font-black text-red-400">{totals?.rejected ?? 0}</p>
                  <p className={`text-[11px] font-semibold ${isDark ? 'text-red-400/70' : 'text-red-500'}`}>미설정·거절</p>
                </div>
              </div>

              <div className="mb-6">
                <h3 className={`text-sm font-bold mb-3 flex items-center gap-2 ${isDark ? 'text-white' : 'text-gray-900'}`}>
                  <span className={`w-5 h-5 flex items-center justify-center ${isCancer ? (isDark ? 'text-teal-400' : 'text-teal-600') : (isDark ? 'text-violet-400' : 'text-violet-600')}`}>
                    <i className="ri-capsule-line text-sm"></i>
                  </span>
                  심의 약제 목록
                </h3>
                {drugs.length === 0 ? (
                  <div className={`rounded-xl border p-5 text-center text-sm ${
                    isDark ? 'bg-[#0D1117] border-[#1E2530] text-[#5A6A80]' : 'bg-gray-50 border-gray-100 text-gray-400'
                  }`}>
                    등록된 심의 결과 없음
                  </div>
                ) : (
                  <div className="space-y-2">
                    {drugs.map((drug, idx) => {
                      const kind = stateKind(drug.state, drug.stateLabel);
                      return (
                        <div
                          key={idx}
                          className={`flex items-center gap-3 rounded-xl border p-3.5 transition-all ${
                            isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-50 border-gray-100'
                          }`}
                        >
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 mb-0.5">
                              <span className={`text-sm font-bold whitespace-nowrap ${isDark ? 'text-white' : 'text-gray-900'}`}>
                                {drug.name}
                              </span>
                              <span className={`text-[11px] font-medium whitespace-nowrap ${isDark ? 'text-[#5A6A80]' : 'text-gray-400'}`}>
                                {drug.ingredient ?? ''}
                              </span>
                            </div>
                            <p className={`text-xs truncate ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
                              {drug.company ?? '정보 없음'} &middot; {drug.indication ?? '정보 없음'}
                            </p>
                          </div>
                          <span className={`flex items-center gap-1.5 text-[11px] font-bold px-2.5 py-1.5 rounded-full border whitespace-nowrap ${resultBg(kind)} ${resultColor(kind)}`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${resultDot(kind)}`}></span>
                            {drug.stateLabel || drug.state}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              <div className={`rounded-xl border p-4 mb-5 ${
                isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-50 border-gray-100'
              }`}>
                <h3 className={`text-sm font-bold mb-2 flex items-center gap-2 ${isDark ? 'text-white' : 'text-gray-900'}`}>
                  <span className={`w-5 h-5 flex items-center justify-center ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
                    <i className="ri-file-text-line text-sm"></i>
                  </span>
                  종합 분석
                </h3>
                <p className={`text-sm leading-relaxed ${isDark ? 'text-[#8B9BB4]' : 'text-gray-600'}`}>
                  {report?.summary ?? '리포트 없음'}
                </p>
              </div>

              {report && report.highlights.length > 0 && (
                <div className="mb-5">
                  <h3 className={`text-sm font-bold mb-3 flex items-center gap-2 ${isDark ? 'text-white' : 'text-gray-900'}`}>
                    <span className={`w-5 h-5 flex items-center justify-center ${isCancer ? (isDark ? 'text-amber-400' : 'text-amber-500') : (isDark ? 'text-violet-400' : 'text-violet-500')}`}>
                      <i className="ri-lightbulb-line text-sm"></i>
                    </span>
                    핵심 Takeaways
                  </h3>
                  <div className="space-y-2">
                    {report.highlights.map((t, idx) => (
                      <div key={idx} className="flex items-start gap-2.5">
                        <span className={`w-5 h-5 flex items-center justify-center flex-shrink-0 mt-0.5 text-[11px] font-bold rounded-full ${
                          isDark ? 'bg-[#1E2530] text-[#8B9BB4]' : 'bg-gray-100 text-gray-500'
                        }`}>
                          {idx + 1}
                        </span>
                        <p className={`text-sm leading-relaxed ${isDark ? 'text-[#8B9BB4]' : 'text-gray-600'}`}>{t}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {meeting.note && (
                <div className={`rounded-xl border p-4 ${
                  isCancer
                    ? (isDark ? 'border-teal-400/20 bg-teal-400/5' : 'border-teal-200 bg-teal-50')
                    : (isDark ? 'border-violet-400/20 bg-violet-400/5' : 'border-violet-200 bg-violet-50')
                }`}>
                  <h3 className={`text-sm font-bold mb-2 flex items-center gap-2 ${
                    isCancer ? (isDark ? 'text-teal-400' : 'text-teal-700') : (isDark ? 'text-violet-400' : 'text-violet-700')
                  }`}>
                    <span className={`w-5 h-5 flex items-center justify-center`}>
                      <i className="ri-arrow-right-circle-line text-sm"></i>
                    </span>
                    회차 노트
                  </h3>
                  <p className={`text-sm leading-relaxed ${isDark ? 'text-[#8B9BB4]' : 'text-gray-600'}`}>
                    {meeting.note}
                  </p>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
