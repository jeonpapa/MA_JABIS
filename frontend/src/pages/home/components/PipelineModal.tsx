import { useState } from 'react';
import { PipelineItem } from './MsdSummaryCards';

export interface PipelineFormData {
  clinicalCode: string;
  phase: string;
  drugClass: string;
  indication: string;
  targetDisease: string;
  domesticApprovalDate: string;
  domesticReimbursementDate: string;
}

const INITIAL_FORM: PipelineFormData = {
  clinicalCode: '',
  phase: '',
  drugClass: '',
  indication: '',
  targetDisease: '',
  domesticApprovalDate: '',
  domesticReimbursementDate: '',
};

const PHASE_OPTIONS = ['Discovery', 'Phase 1', 'Phase 2', 'Phase 3', 'Submitted', 'Approved'];

const CURRENT_YEAR = new Date().getFullYear();

interface Props {
  isDark: boolean;
  pipelines: PipelineItem[];
  onClose: () => void;
  onAdd: (data: PipelineFormData) => void;
}

export default function PipelineModal({ isDark, pipelines, onClose, onAdd }: Props) {
  const [view, setView] = useState<'list' | 'form'>('list');
  const [form, setForm] = useState<PipelineFormData>(INITIAL_FORM);
  const [errors, setErrors] = useState<Partial<Record<keyof PipelineFormData, string>>>({});

  const overlayBg = 'bg-black/40';
  const cardBg = isDark ? 'bg-[#161B27]' : 'bg-white';
  const cardBorder = isDark ? 'border-[#1E2530]' : 'border-gray-200';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
  const textMuted = isDark ? 'text-[#4A5568]' : 'text-gray-400';
  const inputBg = isDark ? 'bg-[#0D1117] border-[#1E2530]' : 'bg-gray-50 border-gray-200';
  const inputFocus = isDark ? 'focus:border-[#00E5CC]/50 focus:ring-0' : 'focus:border-teal-300 focus:ring-0';
  const inputText = isDark ? 'text-white placeholder-[#4A5568]' : 'text-gray-900 placeholder-gray-400';
  const errorText = 'text-red-500';
  const selectBg = isDark ? 'bg-[#0D1117]' : 'bg-gray-50';
  const btnPrimary = 'bg-teal-600 text-white hover:bg-teal-700';
  const btnSecondary = isDark ? 'bg-[#1E2530] text-[#8B9BB4] hover:bg-[#2A3545]' : 'bg-gray-100 text-gray-500 hover:bg-gray-200';
  const accentTeal = isDark ? 'text-[#00E5CC]' : 'text-teal-600';
  const accentPurple = isDark ? 'text-[#7C3AED]' : 'text-purple-600';
  const accentAmber = isDark ? 'text-[#F59E0B]' : 'text-amber-600';
  const pillTeal = isDark ? 'bg-[#00E5CC]/10 text-[#00E5CC]' : 'bg-teal-100 text-teal-700';
  const pillPurple = isDark ? 'bg-[#7C3AED]/20 text-[#7C3AED]' : 'bg-purple-100 text-purple-600';
  const pillAmber = isDark ? 'bg-[#F59E0B]/10 text-[#F59E0B]' : 'bg-amber-100 text-amber-700';
  const rowHover = isDark ? 'hover:bg-white/5' : 'hover:bg-gray-50';
  const sectionBg = isDark ? 'bg-[#1A2035]/20' : 'bg-gray-50/50';
  const yearColors: Record<number, string> = {
    [CURRENT_YEAR]: '#0D9488',
    [CURRENT_YEAR + 1]: '#D97706',
    [CURRENT_YEAR + 2]: '#7C3AED',
  };
  const yearLabels: Record<number, string> = {
    [CURRENT_YEAR]: `${CURRENT_YEAR}년 예정`,
    [CURRENT_YEAR + 1]: `${CURRENT_YEAR + 1}년 예정`,
    [CURRENT_YEAR + 2]: `${CURRENT_YEAR + 2}년 예정`,
  };

  const currentPipelines = pipelines.filter(p => p.status === 'current');
  const yearPipelines = (year: number) => pipelines.filter(p => p.expectedYear === year && p.status !== 'current');

  const updateField = (field: keyof PipelineFormData, value: string) => {
    setForm(prev => ({ ...prev, [field]: value }));
    if (errors[field]) {
      setErrors(prev => {
        const next = { ...prev };
        delete next[field];
        return next;
      });
    }
  };

  const validate = (): boolean => {
    const newErrors: Partial<Record<keyof PipelineFormData, string>> = {};
    if (!form.clinicalCode.trim()) newErrors.clinicalCode = '임상코드명을 입력해주세요';
    if (!form.phase) newErrors.phase = '임상 상황을 선택해주세요';
    if (!form.drugClass.trim()) newErrors.drugClass = '약제클래스를 입력해주세요';
    if (!form.indication.trim()) newErrors.indication = '적응증을 입력해주세요';
    if (!form.targetDisease.trim()) newErrors.targetDisease = '대상질환을 입력해주세요';
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = () => {
    if (!validate()) return;
    onAdd(form);
    setForm(INITIAL_FORM);
    setView('list');
  };

  const renderPipelineRow = (p: PipelineItem, idx: number, yearColor?: string) => (
    <div
      key={idx}
      className={`flex items-start gap-3 py-2.5 px-2 rounded-lg ${rowHover} transition-colors ${p.isCustom ? (isDark ? 'bg-[#7C3AED]/5 border border-[#7C3AED]/30' : 'bg-purple-50/70 border border-purple-200') : ''}`}
    >
      <span
        className={`text-[10px] px-2 py-1 rounded font-semibold flex-shrink-0 mt-0.5 whitespace-nowrap ${p.isCustom ? pillPurple : yearColor ? '' : pillTeal}`}
        style={!p.isCustom && yearColor ? { backgroundColor: `${yearColor}20`, color: yearColor } : {}}
      >
        {p.phase}
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <p className={`${textMain} text-sm font-semibold leading-tight`}>{p.name}</p>
          {p.isCustom && (
            <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-medium ${pillPurple}`}>custom</span>
          )}
        </div>
        <p className={`${textMuted} text-xs mt-0.5`}>{p.indication}</p>
        {(p.drugClass || p.targetDisease) && (
          <p className={`${textMuted} text-[11px] mt-0.5`}>
            {[p.drugClass, p.targetDisease].filter(Boolean).join(' · ')}
          </p>
        )}
        {(p.domesticApprovalDate || p.domesticReimbursementDate) && (
          <p className={`${textMuted} text-[11px]`}>
            {p.domesticApprovalDate && <span>허가: {p.domesticApprovalDate}</span>}
            {p.domesticApprovalDate && p.domesticReimbursementDate && <span className="mx-1.5">|</span>}
            {p.domesticReimbursementDate && <span>급여: {p.domesticReimbursementDate}</span>}
          </p>
        )}
      </div>
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6" onClick={onClose}>
      <div className={`absolute inset-0 ${overlayBg}`}></div>

      <div
        className={`relative w-full max-w-2xl max-h-[85vh] rounded-2xl border shadow-2xl ${cardBg} ${cardBorder} overflow-hidden flex flex-col`}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className={`flex items-center justify-between px-6 py-4 border-b ${cardBorder} flex-shrink-0`}>
          <div className="flex items-center gap-2">
            <span className={`w-8 h-8 rounded-lg flex items-center justify-center ${isDark ? 'bg-[#7C3AED]/10' : 'bg-purple-50'}`}>
              <i className={`ri-flask-line text-base ${accentPurple}`}></i>
            </span>
            <div>
              <h3 className={`font-bold text-base ${textMain}`}>
                {view === 'list' ? 'Pipeline 전체 명단' : 'New Pipeline 등록'}
              </h3>
              <p className={`${textMuted} text-xs`}>
                {view === 'list' ? `총 ${pipelines.length}개 파이프라인` : '신규 파이프라인 정보를 입력해주세요'}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {view === 'list' && (
              <button
                onClick={() => setView('form')}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold cursor-pointer whitespace-nowrap transition-colors flex items-center gap-1.5 ${btnPrimary}`}
              >
                <span className="w-3.5 h-3.5 flex items-center justify-center"><i className="ri-add-line text-sm"></i></span>
                추가하기
              </button>
            )}
            {view === 'form' && (
              <button
                onClick={() => { setView('list'); setForm(INITIAL_FORM); setErrors({}); }}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium cursor-pointer whitespace-nowrap transition-colors ${btnSecondary}`}
              >
                <span className="w-3.5 h-3.5 flex items-center justify-center"><i className="ri-arrow-left-line text-sm"></i></span>
                목록으로
              </button>
            )}
            <button onClick={onClose} className={`w-8 h-8 flex items-center justify-center rounded-lg cursor-pointer transition-colors ${textSub} hover:${textMain}`}>
              <i className="ri-close-line text-lg"></i>
            </button>
          </div>
        </div>

        {/* Body - list view */}
        {view === 'list' && (
          <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
            {/* 현재 진행 중 */}
            <div>
              <div className="flex items-center gap-2 mb-3">
                <div className={`w-2 h-2 rounded-full ${isDark ? 'bg-[#00E5CC]' : 'bg-teal-500'}`}></div>
                <h4 className={`text-sm font-bold ${textMain}`}>현재 진행 중</h4>
                <span className={`text-xs px-1.5 py-0.5 rounded-full ${isDark ? 'bg-teal-500/20 text-teal-400' : 'bg-teal-100 text-teal-700'}`}>{currentPipelines.length}</span>
              </div>
              <div className={`rounded-xl ${sectionBg} p-3 space-y-0.5`}>
                {currentPipelines.length === 0 ? (
                  <p className={`${textMuted} text-xs py-3 text-center`}>현재 진행 중인 파이프라인이 없습니다</p>
                ) : (
                  currentPipelines.map((p, i) => renderPipelineRow(p, i))
                )}
              </div>
            </div>

            {/* 연도별 예정 */}
            {[CURRENT_YEAR, CURRENT_YEAR + 1, CURRENT_YEAR + 2].map(year => {
              const items = yearPipelines(year);
              const color = yearColors[year];
              return (
                <div key={year}>
                  <div className="flex items-center gap-2 mb-3">
                    <div className="w-2 h-2 rounded-full" style={{ backgroundColor: color }}></div>
                    <h4 className={`text-sm font-bold ${textMain}`}>{yearLabels[year]}</h4>
                    <span className="text-xs px-1.5 py-0.5 rounded-full" style={{ backgroundColor: `${color}20`, color }}>{items.length}</span>
                  </div>
                  <div className={`rounded-xl ${sectionBg} p-3 space-y-0.5`}>
                    {items.length === 0 ? (
                      <p className={`${textMuted} text-xs py-3 text-center`}>해당 연도 예정 파이프라인이 없습니다</p>
                    ) : (
                      items.map((p, i) => renderPipelineRow(p, i, color))
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Body - form view */}
        {view === 'form' && (
          <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
            {/* 임상코드명 + 임상상황 */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={`block text-xs font-semibold mb-1.5 ${textSub}`}>
                  임상코드명 <span className={errorText}>*</span>
                </label>
                <input
                  type="text"
                  placeholder="예: MK-3475"
                  value={form.clinicalCode}
                  onChange={e => updateField('clinicalCode', e.target.value)}
                  className={`w-full rounded-lg px-3 py-2.5 text-sm border outline-none transition-colors ${inputBg} ${inputFocus} ${inputText} ${errors.clinicalCode ? 'border-red-400' : ''}`}
                />
                {errors.clinicalCode && <p className={`text-xs mt-1 ${errorText}`}>{errors.clinicalCode}</p>}
              </div>
              <div>
                <label className={`block text-xs font-semibold mb-1.5 ${textSub}`}>
                  임상 상황 <span className={errorText}>*</span>
                </label>
                <select
                  value={form.phase}
                  onChange={e => updateField('phase', e.target.value)}
                  className={`w-full rounded-lg px-3 py-2.5 text-sm border outline-none transition-colors cursor-pointer ${inputBg} ${inputFocus} ${inputText} ${selectBg} ${errors.phase ? 'border-red-400' : ''}`}
                >
                  <option value="" disabled>선택해주세요</option>
                  {PHASE_OPTIONS.map(opt => (
                    <option key={opt} value={opt}>{opt}</option>
                  ))}
                </select>
                {errors.phase && <p className={`text-xs mt-1 ${errorText}`}>{errors.phase}</p>}
              </div>
            </div>

            {/* 약제클래스 + 대상질환 */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={`block text-xs font-semibold mb-1.5 ${textSub}`}>
                  약제클래스 <span className={errorText}>*</span>
                </label>
                <input
                  type="text"
                  placeholder="예: 면역항암제, ADC"
                  value={form.drugClass}
                  onChange={e => updateField('drugClass', e.target.value)}
                  className={`w-full rounded-lg px-3 py-2.5 text-sm border outline-none transition-colors ${inputBg} ${inputFocus} ${inputText} ${errors.drugClass ? 'border-red-400' : ''}`}
                />
                {errors.drugClass && <p className={`text-xs mt-1 ${errorText}`}>{errors.drugClass}</p>}
              </div>
              <div>
                <label className={`block text-xs font-semibold mb-1.5 ${textSub}`}>
                  대상질환 <span className={errorText}>*</span>
                </label>
                <input
                  type="text"
                  placeholder="예: 비소세포폐암"
                  value={form.targetDisease}
                  onChange={e => updateField('targetDisease', e.target.value)}
                  className={`w-full rounded-lg px-3 py-2.5 text-sm border outline-none transition-colors ${inputBg} ${inputFocus} ${inputText} ${errors.targetDisease ? 'border-red-400' : ''}`}
                />
                {errors.targetDisease && <p className={`text-xs mt-1 ${errorText}`}>{errors.targetDisease}</p>}
              </div>
            </div>

            {/* 적응증 (full width) */}
            <div>
              <label className={`block text-xs font-semibold mb-1.5 ${textSub}`}>
                적응증 <span className={errorText}>*</span>
              </label>
              <input
                type="text"
                placeholder="예: 1차 치료, 보조요법"
                value={form.indication}
                onChange={e => updateField('indication', e.target.value)}
                className={`w-full rounded-lg px-3 py-2.5 text-sm border outline-none transition-colors ${inputBg} ${inputFocus} ${inputText} ${errors.indication ? 'border-red-400' : ''}`}
              />
              {errors.indication && <p className={`text-xs mt-1 ${errorText}`}>{errors.indication}</p>}
            </div>

            {/* 국내 허가일 + 국내 급여일 */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className={`block text-xs font-semibold mb-1.5 ${textSub}`}>국내 허가일</label>
                <input
                  type="date"
                  value={form.domesticApprovalDate}
                  onChange={e => updateField('domesticApprovalDate', e.target.value)}
                  className={`w-full rounded-lg px-3 py-2.5 text-sm border outline-none transition-colors ${inputBg} ${inputFocus} ${inputText} ${selectBg}`}
                />
              </div>
              <div>
                <label className={`block text-xs font-semibold mb-1.5 ${textSub}`}>국내 급여일</label>
                <input
                  type="date"
                  value={form.domesticReimbursementDate}
                  onChange={e => updateField('domesticReimbursementDate', e.target.value)}
                  className={`w-full rounded-lg px-3 py-2.5 text-sm border outline-none transition-colors ${inputBg} ${inputFocus} ${inputText} ${selectBg}`}
                />
              </div>
            </div>

            {/* Footer buttons for form */}
            <div className={`flex items-center justify-end gap-3 pt-2`}>
              <button
                onClick={() => { setView('list'); setForm(INITIAL_FORM); setErrors({}); }}
                className={`px-4 py-2.5 rounded-lg text-sm font-medium cursor-pointer whitespace-nowrap transition-colors ${btnSecondary}`}
              >
                취소
              </button>
              <button
                onClick={handleSubmit}
                className={`px-4 py-2.5 rounded-lg text-sm font-semibold cursor-pointer whitespace-nowrap transition-colors flex items-center gap-2 ${btnPrimary}`}
              >
                <span className="w-4 h-4 flex items-center justify-center"><i className="ri-add-line text-sm"></i></span>
                등록하기
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}