import { useMemo, useState } from 'react';
import { useApi } from '@/hooks/useApi';
import { fetchPipeline, PipelineDrug, PipelineStage } from '@/api/reimbStatus';
import DrugDetailModal from './DrugDetailModal';
import NhisCompletedModal from './NhisCompletedModal';

type ListFilter = '' | '신규' | '확대';

// 단계 라벨/설명은 UI 상수 (백엔드는 stage id 만 제공)
const STAGE_META: Record<PipelineStage['id'], { label: string; description: string }> = {
  cancer: { label: '암질환심의위원회', description: '중증(암)질환심의위원회 심의 진행' },
  evaluation: { label: '약제급여평가위원회', description: '약제급여평가위원회 심의 및 평가' },
  nhis: { label: '건강보험공단', description: '건강보험공단 등재 및 급여 적용' },
};

const STATUS_BADGE: Record<PipelineDrug['status'], { label: string; dark: string; light: string }> = {
  completed:   { label: '협상 완료', dark: 'text-emerald-300 bg-emerald-400/10 border-emerald-400/30', light: 'text-emerald-600 bg-emerald-50 border-emerald-200' },
  negotiating: { label: '협상 중',   dark: 'text-sky-300 bg-sky-400/10 border-sky-400/30',           light: 'text-sky-600 bg-sky-50 border-sky-200' },
  scheduled:   { label: '심의 상정예정', dark: 'text-teal-300 bg-teal-400/10 border-teal-400/30',      light: 'text-teal-600 bg-teal-50 border-teal-200' },
  waiting:     { label: '심의 대기', dark: 'text-amber-300 bg-amber-400/10 border-amber-400/30',      light: 'text-amber-600 bg-amber-50 border-amber-200' },
};

function StatusBadge({ drug, isDark }: { drug: PipelineDrug; isDark: boolean }) {
  const cfg = STATUS_BADGE[drug.status] ?? STATUS_BADGE.waiting;
  // 상정예정이면 차수 정보 부기 ("심의 상정예정 · 7차")
  const suffix = drug.status === 'scheduled' && drug.expectedSessionCycle
    ? ` · ${drug.expectedSessionCycle}차` : '';
  return (
    <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border whitespace-nowrap ${
      isDark ? cfg.dark : cfg.light
    }`}>
      {cfg.label}{suffix}
    </span>
  );
}

function DrugCard({ drug, index, isDark, onClick }: {
  drug: PipelineDrug;
  index: number;
  isDark: boolean;
  onClick: () => void;
}) {
  const accentBorder = drug.status === 'completed'
    ? 'border-l-emerald-400'
    : drug.status === 'negotiating'
    ? 'border-l-sky-400'
    : drug.status === 'scheduled'
    ? 'border-l-teal-400'
    : 'border-l-amber-400';

  return (
    <div
      onClick={onClick}
      className={`rounded-lg p-3.5 border-l-[3px] transition-all cursor-pointer ${
        isDark
          ? `bg-[#0D1117] border-[#1E2530] ${accentBorder} hover:bg-[#121820] hover:border-[#2A3545]`
          : `bg-white border border-gray-100 ${accentBorder} hover:shadow-sm hover:border-gray-250`
      }`}
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className={`w-4 h-4 flex items-center justify-center rounded text-[10px] font-bold flex-shrink-0 ${
            isDark ? 'bg-[#1E2530] text-[#8B9BB4]' : 'bg-gray-100 text-gray-500'
          }`}>
            {index + 1}
          </span>
          <span className={`text-[10px] font-medium truncate ${isDark ? 'text-[#6B7A90]' : 'text-gray-400'}`}>
            {drug.type ?? (drug.msdFlag ? 'MSD' : '—')}
          </span>
        </div>
        <StatusBadge drug={drug} isDark={isDark} />
      </div>

      <h4 className={`text-sm font-bold mb-1 leading-snug ${isDark ? 'text-white' : 'text-gray-900'}`}>
        {drug.name}
        {drug.msdFlag && (
          <span className={`ml-1.5 text-[9px] font-bold px-1.5 py-0.5 rounded align-middle ${
            isDark ? 'bg-[#00E5CC]/15 text-[#00E5CC]' : 'bg-teal-50 text-teal-600'
          }`}>MSD</span>
        )}
      </h4>

      <p className={`text-xs mb-2 truncate ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
        {drug.ingredient ?? '성분 정보 없음'}
      </p>

      <div className={`flex items-center gap-1.5 text-[10px] mb-1 ${isDark ? 'text-[#5A6A80]' : 'text-gray-400'}`}>
        <span className="w-3.5 h-3.5 flex items-center justify-center flex-shrink-0">
          <i className="ri-building-line text-[11px]"></i>
        </span>
        <span className="truncate font-medium">{drug.company ?? '정보 없음'}</span>
      </div>

      {/* 신규/확대 + 공단 공식 출처 표지 (NHIS 매칭 시) */}
      {(drug.listType || drug.negotiationDateSource === 'nhis_official') && (
        <div className="flex items-center gap-1 flex-wrap mt-1.5">
          {drug.listType && (
            <span className={`text-[9px] font-semibold px-1.5 py-0.5 rounded-full ${
              drug.listType === '신규'
                ? (isDark ? 'bg-sky-400/10 text-sky-300' : 'bg-sky-50 text-sky-600')
                : (isDark ? 'bg-violet-400/10 text-violet-300' : 'bg-violet-50 text-violet-600')
            }`}>
              {drug.listType === '신규' ? '신약' : '사용범위 확대'}
            </span>
          )}
          {drug.negotiationDateSource === 'nhis_official' && (
            <span className={`text-[9px] font-semibold px-1.5 py-0.5 rounded-full flex items-center gap-0.5 ${
              isDark ? 'bg-emerald-400/10 text-emerald-300' : 'bg-emerald-50 text-emerald-600'
            }`}>
              <i className="ri-verified-badge-line text-[10px]"></i>공단 공식
            </span>
          )}
        </div>
      )}

      {/* 세부 히스토리/notes 는 카드에서 제거 — 클릭 시 모달에서 확인 (이슈 ②) */}
      <div className={`flex items-center justify-between gap-2 mt-2 pt-2 border-t ${
        isDark ? 'border-[#1E2530]' : 'border-gray-100'
      }`}>
        {drug.status === 'scheduled' && drug.expectedSessionDate ? (
          <span className={`text-[10px] font-medium ${isDark ? 'text-teal-400' : 'text-teal-600'}`}>
            <i className="ri-calendar-event-line text-[11px] mr-1"></i>예정 {drug.expectedSessionDate}
          </span>
        ) : (
          <span className={`text-[10px] ${isDark ? 'text-[#5A6A80]' : 'text-gray-400'}`}>
            <i className="ri-calendar-line text-[11px] mr-1"></i>{drug.updatedDate ?? '—'}
          </span>
        )}
        {drug.keyIssues.length > 0 && (
          <span className={`text-[10px] font-medium flex items-center gap-0.5 ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
            <i className="ri-lightbulb-flash-line text-[11px]"></i>쟁점 {drug.keyIssues.length}
          </span>
        )}
      </div>
    </div>
  );
}

export default function PipelineBoard({ isDark }: { isDark: boolean }) {
  const [selectedDrug, setSelectedDrug] = useState<{ drug: PipelineDrug; stageLabel: string } | null>(null);
  const [showCompleted, setShowCompleted] = useState(false);
  const [listFilter, setListFilter] = useState<ListFilter>('');

  const { data, loading, error, reload } = useApi(fetchPipeline);
  const pipelineStages = data ?? [];

  // 신규/확대 필터칩은 건강보험공단(nhis) 단계에만 적용 (해당 단계 약제만 신규/확대 구분 보유).
  const filteredStages = useMemo(() => pipelineStages.map(stage => ({
    ...stage,
    drugs: (stage.id === 'nhis' && listFilter)
      ? stage.drugs.filter(d => d.listType === listFilter)
      : stage.drugs,
  })), [pipelineStages, listFilter]);

  const totalDrugs = filteredStages.reduce((acc, s) => acc + s.drugs.length, 0);

  const sortedStages = filteredStages.map(stage => ({
    ...stage,
    count: stage.drugs.length,
    drugs: [...stage.drugs].sort((a, b) => {
      const order: Record<string, number> = { scheduled: 0, waiting: 1, negotiating: 2, completed: 3 };
      return (order[a.status] ?? 9) - (order[b.status] ?? 9);
    }),
  }));

  const chips: { key: ListFilter; label: string }[] = [
    { key: '', label: '전체' },
    { key: '신규', label: '신약' },
    { key: '확대', label: '사용범위 확대' },
  ];

  return (
    <>
      <div className={`rounded-2xl border p-6 ${isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200'}`}>
        <div className="flex items-start justify-between gap-3 mb-1">
          <div className="flex items-center gap-2">
            <span className={`w-5 h-5 flex items-center justify-center ${isDark ? 'text-teal-400' : 'text-teal-600'}`}>
              <i className="ri-dashboard-line text-base"></i>
            </span>
            <h2 className={`font-bold text-base ${isDark ? 'text-white' : 'text-gray-900'}`}>
              Reimbursement Dashboard
            </h2>
          </div>
          <button
            onClick={() => setShowCompleted(true)}
            className={`text-[11px] font-semibold px-3 py-1.5 rounded-lg border flex items-center gap-1.5 cursor-pointer transition-colors flex-shrink-0 ${
              isDark
                ? 'bg-emerald-400/10 text-emerald-300 border-emerald-400/30 hover:bg-emerald-400/15'
                : 'bg-emerald-50 text-emerald-600 border-emerald-200 hover:bg-emerald-100'
            }`}
          >
            <i className="ri-checkbox-circle-line text-sm"></i>협상 완료 내역
          </button>
        </div>
        <p className={`text-xs mb-5 ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
          급여 등재 진행 중 약제 (협상 완료 건은 별도) · 현재 {totalDrugs}개 약제
        </p>

        {loading && (
          <div className={`flex items-center justify-center gap-2 py-14 text-sm ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
            <i className="ri-loader-4-line animate-spin text-lg"></i>
            파이프라인 불러오는 중...
          </div>
        )}

        {!loading && error && (
          <div className={`text-center py-14 ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
            <span className="w-12 h-12 flex items-center justify-center mx-auto mb-3 text-red-400">
              <i className="ri-error-warning-line text-3xl"></i>
            </span>
            <p className="text-sm mb-3">파이프라인을 불러오지 못했습니다: {error}</p>
            <button
              onClick={reload}
              className="px-4 py-2 rounded-lg text-xs font-semibold bg-teal-500 text-white hover:bg-teal-600 cursor-pointer transition-colors"
            >
              다시 시도
            </button>
          </div>
        )}

        {!loading && !error && (
          <>
            <div className="grid grid-cols-3 gap-4">
              {sortedStages.map((stage, idx) => (
                <div key={stage.id} className={`rounded-xl border p-4 ${
                  isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-50/80 border-gray-100'
                }`}>
                  <div className="flex items-center gap-2 mb-3">
                    <span className={`w-5 h-5 flex items-center justify-center rounded-md text-[11px] font-bold flex-shrink-0 ${
                      isDark ? 'bg-teal-400/15 text-teal-400' : 'bg-teal-50 text-teal-600'
                    }`}>
                      {idx + 1}
                    </span>
                    <div className="flex-1 min-w-0">
                      <h3 className={`text-xs font-bold truncate ${isDark ? 'text-white' : 'text-gray-900'}`}>
                        {STAGE_META[stage.id]?.label ?? stage.id}
                      </h3>
                      <p className={`text-[10px] truncate ${isDark ? 'text-[#5A6A80]' : 'text-gray-400'}`}>
                        {STAGE_META[stage.id]?.description ?? ''}
                      </p>
                    </div>
                    <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full flex-shrink-0 ${
                      isDark ? 'bg-[#1E2530] text-[#8B9BB4]' : 'bg-gray-100 text-gray-500'
                    }`}>
                      {stage.count}건
                    </span>
                  </div>

                  {/* 신규/확대 필터칩 — 건강보험공단 단계 전용 */}
                  {stage.id === 'nhis' && (
                    <div className="flex items-center gap-1 mb-3 flex-wrap">
                      {chips.map(c => (
                        <button
                          key={c.key}
                          onClick={() => setListFilter(c.key)}
                          className={`text-[10px] font-semibold px-2.5 py-1 rounded-full border transition-colors cursor-pointer ${
                            listFilter === c.key
                              ? (isDark ? 'bg-teal-400/15 text-teal-300 border-teal-400/40' : 'bg-teal-50 text-teal-700 border-teal-300')
                              : (isDark ? 'bg-[#161B27] text-[#8B9BB4] border-[#1E2530] hover:border-[#2A3545]' : 'bg-white text-gray-500 border-gray-200 hover:border-gray-300')
                          }`}
                        >
                          {c.label}
                        </button>
                      ))}
                    </div>
                  )}

                  <div className="space-y-2">
                    {stage.drugs.map((drug, dIdx) => (
                      <DrugCard
                        key={drug.id}
                        drug={drug}
                        index={dIdx}
                        isDark={isDark}
                        onClick={() => setSelectedDrug({ drug, stageLabel: STAGE_META[stage.id]?.label ?? stage.id })}
                      />
                    ))}
                    {stage.drugs.length === 0 && (
                      <p className={`text-[11px] text-center py-6 ${isDark ? 'text-[#4A5568]' : 'text-gray-300'}`}>
                        해당 단계 약제 없음
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>

            <div className={`mt-4 pt-3 border-t flex items-center gap-3 flex-wrap ${
              isDark ? 'border-[#1E2530]' : 'border-gray-100'
            }`}>
              <span className={`text-[10px] font-semibold ${isDark ? 'text-[#5A6A80]' : 'text-gray-400'}`}>상태:</span>
              <div className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-teal-500"></span>
                <span className={`text-[10px] ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>심의 상정예정</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-amber-500"></span>
                <span className={`text-[10px] ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>심의 대기</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-sky-500"></span>
                <span className={`text-[10px] ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>협상 중</span>
              </div>
              <div className="flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-emerald-500"></span>
                <span className={`text-[10px] ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>협상 완료</span>
              </div>
            </div>
          </>
        )}
      </div>

      {selectedDrug && (
        <DrugDetailModal
          drug={selectedDrug.drug}
          stageLabel={selectedDrug.stageLabel}
          isDark={isDark}
          onClose={() => setSelectedDrug(null)}
        />
      )}

      {showCompleted && (
        <NhisCompletedModal isDark={isDark} onClose={() => setShowCompleted(false)} />
      )}
    </>
  );
}
