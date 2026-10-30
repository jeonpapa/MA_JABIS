import { useMemo, useState } from 'react';
import { searchDrugCriteria, type DrugSearchResult } from '@/api/criteria';
import { AtAGlance, GeneralItemCard, Section, StepGroups, T } from './shared';

export default function DrugTab({ onAddCompare, compareList }: { onAddCompare: (q: string) => void; compareList: string[] }) {
  const [q, setQ] = useState('');
  const [res, setRes] = useState<DrugSearchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [cancer, setCancer] = useState<string>('');

  const search = async (term: string) => {
    if (term.trim().length < 2) return;
    setBusy(true); setError(''); setCancer('');
    try { setRes(await searchDrugCriteria(term.trim())); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  const cancers = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of res?.onco ?? []) m.set(r.cancer, (m.get(r.cancer) ?? 0) + 1);
    return [...m.entries()];
  }, [res]);
  const pick = (r: { cancer: string }) => !cancer || r.cancer === cancer;
  const main = (res?.onco ?? []).filter(r => !r.is_combo_table && pick(r));
  const combo = (res?.onco ?? []).filter(r => r.is_combo_table && pick(r));
  const own = res?.general.filter(g => g.match !== 'mentioned') ?? [];
  const mentioned = res?.general.filter(g => g.match === 'mentioned') ?? [];
  const inCompare = res ? compareList.includes(res.resolved.query) : false;
  const nothing = res && !res.onco.length && !res.general.length;

  return (
    <div>
      <form onSubmit={e => { e.preventDefault(); search(q); }} className="flex gap-2">
        <div className="relative flex-1">
          <i className="ri-capsule-line absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input value={q} onChange={e => setQ(e.target.value)} maxLength={100}
            placeholder="제품명이나 성분명 — 예: 키트루다, 펨브롤리주맙, 듀피젠트, 자누비아"
            className="w-full pl-9 pr-3 py-2.5 rounded-lg border border-gray-300 focus:border-teal-500 focus:ring-1 focus:ring-teal-500 text-sm" />
        </div>
        <button type="submit" disabled={busy || q.trim().length < 2}
          className="px-5 py-2.5 rounded-lg bg-teal-600 text-white text-sm font-semibold hover:bg-teal-700 disabled:opacity-50">
          {busy ? <i className="ri-loader-4-line animate-spin" /> : '찾기'}
        </button>
      </form>
      {error && <div className="mt-3 text-sm text-red-600 bg-red-50 rounded p-2">{error}</div>}

      {res && (
        <div className="mt-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-lg font-bold text-gray-900">{res.resolved.query}</span>
            {res.resolved.inns[0] && res.resolved.inns[0] !== res.resolved.query.toLowerCase() && <span className="text-sm text-gray-500">{res.resolved.inns[0]}</span>}
            <button type="button" onClick={() => onAddCompare(res.resolved.query)} disabled={inCompare || compareList.length >= 3} title={compareList.length >= 3 && !inCompare ? '비교는 최대 3개까지입니다. 약제 비교 탭에서 하나를 지우세요.' : undefined}
              className="ml-auto px-3 py-1 rounded-lg border border-teal-500 text-teal-700 text-xs font-semibold hover:bg-teal-50 disabled:opacity-40">
              <i className="ri-git-compare-line mr-1" />{inCompare ? '비교 목록에 있음' : `비교에 추가 (${compareList.length}/3)`}
            </button>
            {compareList.length >= 3 && !inCompare && <span className="text-[11px] text-amber-700">비교는 최대 3개 — 약제 비교 탭에서 하나를 지우세요</span>}
          </div>
          <div className="text-xs text-gray-500 mt-1">
            {[res.onco.filter(r => !r.is_combo_table).length && `항암 급여 요법 ${res.onco.filter(r => !r.is_combo_table).length}개`,
              own.length && `일반약제 급여기준 ${own.length}개`,
              res.onco.filter(r => r.is_combo_table).length && `병용 신약 추가 요법 ${res.onco.filter(r => r.is_combo_table).length}개`].filter(Boolean).join(' · ')}
          </div>

          {nothing && <div className="mt-6 text-center text-sm text-gray-400">급여 기준에서 찾지 못했습니다. 성분명(영문)으로도 찾아보세요.</div>}
          {!own.length && mentioned.length > 0 && !res.onco.length && (
            <div className="mt-3 rounded-lg border border-violet-200 bg-violet-50 p-3 text-xs text-violet-900">
              이 약은 단독 급여기준이 없고, 아래 <T k="묶음기준">묶음 기준</T>({mentioned.slice(0, 2).map(m => m.title.replace(/\s*\(.*$/, '')).join(', ')})에 포함되어 있습니다.
            </div>
          )}

          {cancers.length > 1 && (
            <div className="flex flex-wrap gap-1 mt-3 text-xs">
              <span className="text-gray-400 mr-1">암종</span>
              {[['', res.onco.length] as [string, number], ...cancers].map(([c, n]) => (
                <button key={c || 'all'} type="button" onClick={() => setCancer(c)}
                  className={`px-2 py-0.5 rounded-full border ${cancer === c ? 'bg-teal-600 text-white border-teal-600' : 'border-gray-300 bg-white text-gray-600 hover:border-teal-400'}`}>
                  {c || '전체'} {n}
                </button>
              ))}
            </div>
          )}

          {main.length > 0 && (
            <div className="mt-3 rounded-xl border border-gray-200 bg-white p-4">
              <div className="font-bold text-sm mb-2">한눈에 보기</div>
              <AtAGlance rows={main} idPrefix="x" prefix={r => r.cancer} />
            </div>
          )}
          {main.length > 0 && (
            <Section title="항암 급여 요법" count={main.length}>
              {(cancer ? [cancer] : [...new Set(main.map(r => r.cancer))]).map(c => (
                <div key={c} className="mb-5">
                  <div className="text-sm font-bold text-gray-800 mb-2">{c}</div>
                  <StepGroups rows={main.filter(r => r.cancer === c)} idPrefix="x" />
                </div>
              ))}
            </Section>
          )}
          {own.length > 0 && (
            <Section title="일반약제 급여기준" count={own.length}>
              <div className="grid gap-2">{own.map(g => <GeneralItemCard key={g.key} item={g} />)}</div>
            </Section>
          )}
          {mentioned.length > 0 && (
            <Section title={own.length ? '다른 기준에서 이 약이 언급된 부분' : '포함된 묶음 기준'} count={mentioned.length} defaultOpen={!own.length}
              note={own.length ? '교체 투여 조건 등에서 이 약 이름이 나오는 기준입니다.' : undefined}>
              <div className="grid gap-2">{mentioned.map(g => <GeneralItemCard key={g.key} item={g} />)}</div>
            </Section>
          )}
          {combo.length > 0 && (
            <Section title={<T k="병용신약">병용 신약 추가 요법 (기존 요법만 급여)</T>} count={combo.length} defaultOpen={!main.length}
              note="추가 약제는 이 표로 급여되지 않습니다. 함께 쓰는 기존 항암요법 부분만 급여로 인정됩니다.">
              <StepGroups rows={combo} idPrefix="x" />
            </Section>
          )}
        </div>
      )}
    </div>
  );
}
