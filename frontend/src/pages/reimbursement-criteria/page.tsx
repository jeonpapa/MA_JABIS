import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { criteriaMeta, type SourceMeta } from '@/api/criteria';
import DiseaseTab from './components/DiseaseTab';
import DrugTab from './components/DrugTab';
import CompareTab, { type ComparePreset } from './components/CompareTab';

// 급여 기준 — 심평원 항암제 공고 · 일반약제 세부사항 고시 · KCD-9 (agents/rules/hira_criteria_rules.md)

type Tab = 'disease' | 'drug' | 'compare';
const TABS: { id: Tab; label: string; icon: string; desc: string }[] = [
  { id: 'disease', label: '질환으로 찾기', icon: 'ri-stethoscope-line', desc: '환자 상황을 문장으로 입력하면 AI가 질환·차수·바이오마커·약을 알아듣고 맞는 급여 기준을 보여줍니다.' },
  { id: 'drug', label: '약제로 찾기', icon: 'ri-capsule-line', desc: '약 이름(제품명·성분명)으로 그 약의 모든 급여 기준을 봅니다.' },
  { id: 'compare', label: '약제 비교', icon: 'ri-git-compare-line', desc: '최대 3개 약을 기준 약과 비교해, 다른 조건만 색으로 보여줍니다.' },
];
const LIST_KEY = 'criteria_compare_list';

export default function ReimbursementCriteriaPage() {
  const [params, setParams] = useSearchParams();
  const tab = (params.get('tab') as Tab) || 'disease';
  const [meta, setMeta] = useState<SourceMeta | null>(null);
  const [metaErr, setMetaErr] = useState('');
  const [compareList, setCompareList] = useState<string[]>(() => {
    try { return JSON.parse(sessionStorage.getItem(LIST_KEY) || '[]'); } catch { return []; }
  });

  useEffect(() => {
    criteriaMeta().then(r => setMeta(r.source)).catch(e => setMetaErr((e as Error).message));
  }, []);
  useEffect(() => { sessionStorage.setItem(LIST_KEY, JSON.stringify(compareList)); }, [compareList]);

  const [preset, setPreset] = useState<ComparePreset | null>(null);
  const go = (t: Tab) => setParams(p => { p.set('tab', t); return p; });
  const compareFromDisease = (drugs: string[], focus?: string, line?: number | null) => {
    setCompareList(drugs.slice(0, 3));
    setPreset({ drugs: drugs.slice(0, 3), focus, line, token: Date.now() });
    go('compare');
  };
  const addCompare = (q: string) => setCompareList(l => (l.includes(q) || l.length >= 3 ? l : [...l, q]));

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2"><i className="ri-scales-3-line text-teal-600" />급여 기준</h1>
          <p className="text-sm text-gray-500 mt-0.5">어떤 질환에 어떤 약이, 어떤 조건으로 급여되는지 바로 찾습니다.</p>
        </div>
        <div className="text-[11px] text-gray-500 text-right">
          {meta ? (
            <div className="space-y-0.5">
              <div title="각 요법 카드의 날짜는 그 요법이 마지막으로 바뀐 공고의 시행일입니다.">
                <i className="ri-shield-check-line text-teal-600 mr-1" />
                항암 {meta.onco_notice?.notice_no ?? '공고'} — 오늘 기준 최신
                {meta.onco_notice?.effective_date && meta.onco_notice.effective_date > new Date().toISOString().slice(0, 10)
                  ? <b className="text-amber-700"> ({meta.onco_notice.effective_date} 시행 예정분 포함)</b>
                  : <> ({meta.onco_notice?.effective_date ?? '—'} 시행)</>}
                {meta.onco_consolidated?.url && <a href={meta.onco_consolidated.url} className="text-teal-600 hover:underline ml-1">원문</a>}
              </div>
              <div>
                일반약제 {meta.drug_booklet?.title?.match(/\d{4}년 \d+월판/)?.[0] ?? '세부사항'} 책자 + 그 뒤 개정 고시 (최신 시행 {meta.drug_latest_effective ?? '—'})
                {meta.drug_booklet?.url && <a href={meta.drug_booklet.url} target="_blank" rel="noreferrer" className="text-teal-600 hover:underline ml-1">원문</a>}
              </div>
              {meta.checked_at && <div className="text-gray-400">심평원 게시판 마지막 확인 {meta.checked_at}</div>}
            </div>
          ) : metaErr ? <span className="text-red-500">{metaErr}</span> : '기준일 불러오는 중…'}
        </div>
      </div>

      <div className="flex gap-1 mt-4 border-b border-gray-200">
        {TABS.map(t => (
          <button key={t.id} onClick={() => go(t.id)}
            className={`px-4 py-2 text-sm font-semibold border-b-2 -mb-px ${tab === t.id ? 'border-teal-600 text-teal-700' : 'border-transparent text-gray-500 hover:text-gray-800'}`}>
            <i className={`${t.icon} mr-1`} />{t.label}
            {t.id === 'compare' && compareList.length > 0 && <span className="ml-1 text-[11px] bg-teal-600 text-white rounded-full px-1.5">{compareList.length}</span>}
          </button>
        ))}
      </div>
      <p className="text-xs text-gray-500 mt-2 mb-3">{TABS.find(t => t.id === tab)?.desc}</p>

      <div className={tab === 'disease' ? '' : 'hidden'}><DiseaseTab onCompare={compareFromDisease} /></div>
      <div className={tab === 'drug' ? '' : 'hidden'}>
        <DrugTab compareList={compareList} onAddCompare={q => { addCompare(q); }} />
      </div>
      <div className={tab === 'compare' ? '' : 'hidden'}><CompareTab list={compareList} setList={setCompareList} preset={preset} /></div>

      <p className="mt-10 text-[11px] text-gray-400">
        출처: 건강보험심사평가원 공고·고시(공공누리 4유형). 검색용으로 정리한 정보이므로 최종 판단은 원문을 확인하세요.
      </p>
    </div>
  );
}
