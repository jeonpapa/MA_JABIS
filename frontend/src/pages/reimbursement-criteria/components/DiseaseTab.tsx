import { useEffect, useMemo, useState } from 'react';
import {
  clarifyDisease, diseaseResults,
  type Age, type ClarifyResult, type DiseaseResults, type Filters, type Question, type Target,
} from '@/api/criteria';
import { AtAGlance, GeneralItemCard, Section, StepGroups, T, UpcomingBadge } from './shared';
import { stepLabel } from './labels';

const EXAMPLES = ['두경부암 키트루다 1차', 'HER2 음성 위암 1차', 'EGFR 변이 비소세포폐암 2차', '다발골수종 이식 불가', '아토피', '류마티스관절염'];
const EMPTY: Filters = { settings: [], line: null, markers: [] };
const VERDICT = {
  covered: { cls: 'bg-emerald-50 border-emerald-300 text-emerald-900', icon: 'ri-checkbox-circle-fill text-emerald-600' },
  combo_only: { cls: 'bg-amber-50 border-amber-300 text-amber-900', icon: 'ri-error-warning-fill text-amber-600' },
  none: { cls: 'bg-gray-50 border-gray-300 text-gray-700', icon: 'ri-close-circle-fill text-gray-400' },
  age_outside: { cls: 'bg-amber-50 border-amber-300 text-amber-900', icon: 'ri-user-unfollow-fill text-amber-600' },
};

export default function DiseaseTab({ onCompare }: { onCompare: (drugs: string[], focus?: string, line?: number | null) => void }) {
  const [query, setQuery] = useState('');
  const [clar, setClar] = useState<ClarifyResult | null>(null);
  const [targets, setTargets] = useState<Target[]>([]);
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [drugs, setDrugs] = useState<{ name: string; inns: string[] }[]>([]);
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const [res, setRes] = useState<DiseaseResults | null>(null);
  const [busy, setBusy] = useState<'' | 'ai' | 'db'>('');
  const [error, setError] = useState('');
  const [edit, setEdit] = useState(false);
  const [onlyDrug, setOnlyDrug] = useState(true);
  const [age, setAge] = useState<Age | null>(null);

  const run = async (q: string, ans: Record<string, unknown> = {}, fixed?: string[]) => {
    if (!q.trim() && !fixed?.length) return;
    setBusy('ai'); setError(''); setEdit(false); setRes(null); setOnlyDrug(true);
    try {
      const r = await clarifyDisease(q, ans, fixed);
      setClar(r); setAnswers(ans);
      setTargets(r.targets);
      if (!fixed) setAge(r.age ?? null);
      setFilters(r.filters ?? EMPTY);
      // 질환을 되물은 뒤(fixed)에도 처음 질문의 약은 유지한다
      setDrugs(d => {
        const next = r.drugs ?? [];
        if (!fixed) return next;
        const names = new Set(next.map(x => x.name));
        return [...next, ...d.filter(x => !names.has(x.name))];
      });
      if (!r.targets.length) setRes(null);
    } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };

  useEffect(() => {
    if (!targets.length) return;
    let alive = true;
    setBusy('db');
    diseaseResults(targets.map(t => t.id), { ...filters, drugs: drugs.flatMap(d => d.inns), keywords: clar?.keywords ?? [], age })
      .then(r => { if (alive) setRes(r); })
      .catch(e => { if (alive) setError((e as Error).message); })
      .finally(() => { if (alive) setBusy(''); });
    return () => { alive = false; };
  }, [targets, filters, drugs, clar?.keywords, age]);

  const answer = (q: Question, v: string | number | null) => {
    if (q.field === 'targets') {
      const ids = typeof v === 'string' && v.startsWith('__all__:') ? v.slice(8).split(',') : v ? [String(v)] : [];
      run(query, {}, ids);
      return;
    }
    const next = { ...answers, [q.field]: q.field === 'line' ? v : (v === null ? [] : [v]) };
    setAnswers(next);
    setFilters(f => ({ ...f, [q.field]: q.field === 'line' ? (v as number | null) : (v === null ? [] : [String(v)]) }));
    setClar(c => (c ? { ...c, questions: c.questions.filter(x => x.field !== q.field) } : c));
  };
  const toggle = (field: 'settings' | 'markers', v: string) =>
    setFilters(f => ({ ...f, [field]: f[field].includes(v) ? f[field].filter(x => x !== v) : [...f[field], v] }));

  const facets = clar?.facets;
  const label = (field: string, v: string) =>
    field === 'settings' ? facets?.settings.find(s => s.code === v)?.label ?? v : facets?.markers.find(m => m.value === v)?.label ?? v;
  const kws = clar?.keywords ?? [];
  const view = useMemo(() => {
    if (!res) return null;
    if (!onlyDrug || !drugs.length) return res;
    return { ...res, onco: res.onco.filter(r => r.focus), combo: res.combo.filter(r => r.focus),
      general: res.general.filter(g => g.focus) };
  }, [res, onlyDrug, drugs.length]);

  return (
    <div>
      <form onSubmit={e => { e.preventDefault(); run(query); }} className="flex gap-2">
        <div className="relative flex-1">
          <i className="ri-search-line absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input value={query} onChange={e => setQuery(e.target.value)} maxLength={200}
            placeholder="질환과 상황을 그대로 입력하세요 — 예: 두경부암 키트루다 1차, HER2 음성 위암, 만 8세 아토피"
            className="w-full pl-9 pr-3 py-2.5 rounded-lg border border-gray-300 focus:border-teal-500 focus:ring-1 focus:ring-teal-500 text-sm" />
        </div>
        <button type="submit" disabled={busy === 'ai' || !query.trim()}
          className="px-5 py-2.5 rounded-lg bg-teal-600 text-white text-sm font-semibold hover:bg-teal-700 disabled:opacity-50">
          {busy === 'ai' ? <><i className="ri-loader-4-line animate-spin mr-1" />확인 중</> : '찾기'}
        </button>
      </form>
      {!clar && (
        <div className="flex flex-wrap gap-1.5 mt-2 text-xs">
          <span className="text-gray-400">예시</span>
          {EXAMPLES.map(x => (
            <button key={x} type="button" onClick={() => { setQuery(x); run(x); }}
              className="px-2 py-0.5 rounded-full border border-gray-200 text-gray-600 hover:border-teal-400 hover:text-teal-700">{x}</button>
          ))}
        </div>
      )}
      {error && <div className="mt-3 text-sm text-red-600 bg-red-50 rounded p-2">{error}</div>}

      {/* AI 가 이해한 조건 — 칩으로 바로 보이고, 지울 수 있다 */}
      {clar && (
        <div className="mt-3">
          {clar.interpretation && <div className="text-xs text-gray-500 mb-1.5"><i className="ri-sparkling-line text-teal-600 mr-1" />{clar.interpretation}</div>}
          {targets.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              {targets.map(t => (
                <Chip key={t.id} tone="teal" onRemove={targets.length > 1 ? () => setTargets(ts => ts.filter(x => x.id !== t.id)) : undefined}
                  onClick={targets.length > 1 ? () => setTargets([t]) : undefined} title={targets.length > 1 ? '이 질환만 보기' : undefined}>{t.name}</Chip>
              ))}
              {age && <Chip tone="amber" onRemove={() => setAge(null)}>나이: {age.label}</Chip>}
              {filters.line && <Chip onRemove={() => setFilters(f => ({ ...f, line: null }))}>{filters.line}차 치료</Chip>}
              {filters.settings.map(s => <Chip key={s} onRemove={() => toggle('settings', s)}>{label('settings', s)}</Chip>)}
              {filters.markers.map(m => <Chip key={m} onRemove={() => toggle('markers', m)}>{label('markers', m)}</Chip>)}
              {drugs.map(d => <Chip key={d.name} tone="violet" onRemove={() => setDrugs(ds => ds.filter(x => x.name !== d.name))}>약: {d.name}</Chip>)}
              {facets && facets.onco_rows > 0 && (
                <button type="button" onClick={() => setEdit(x => !x)} className="text-xs text-teal-700 hover:underline ml-1">
                  <i className="ri-equalizer-line" /> 조건 바꾸기
                </button>
              )}
              {clar.alternatives.length > 0 && (
                <span className="text-xs text-gray-400 ml-2">혹시 {clar.alternatives.map((a, i) => (
                  <button key={a.id} type="button" onClick={() => run(query, {}, [a.id])} className="text-teal-700 hover:underline">{i ? ', ' : ''}{a.name}</button>
                ))}?</span>
              )}
            </div>
          )}

          {!targets.length && clar.drugs.length >= 2 && (
            <button type="button" onClick={() => onCompare(clar.drugs.map(d => d.name))}
              className="mt-3 px-3 py-1.5 rounded-lg bg-white border border-teal-500 text-teal-700 text-xs font-semibold hover:bg-teal-50">
              <i className="ri-git-compare-line mr-1" />{clar.drugs.map(d => d.name).join(' vs ')} 차이 비교하기 →
            </button>
          )}
          {clar.questions.map(q => (
            <div key={q.field + q.text} className="mt-3 rounded-lg border border-amber-200 bg-amber-50/60 p-3">
              <div className="text-sm font-semibold text-gray-900">{q.text}</div>
              <div className="flex flex-wrap gap-1.5 mt-2">
                {q.options.map(o => (
                  <button key={String(o.value)} type="button" onClick={() => answer(q, o.value)}
                    className="px-3 py-1 rounded-full border border-gray-300 bg-white text-sm hover:border-teal-500 hover:text-teal-700">{o.label}</button>
                ))}
              </div>
            </div>
          ))}

          {edit && facets && (
            <div className="mt-2 rounded-lg border border-gray-200 bg-white p-3 space-y-2 text-xs">
              <Row label="치료 차수">
                {[null, ...facets.lines.map(l => l.value)].map(v => (
                  <Pill key={String(v)} on={filters.line === v} onClick={() => setFilters(f => ({ ...f, line: v }))}>
                    {v === null ? '상관없음' : facets.lines.find(l => l.value === v)?.label}
                  </Pill>
                ))}
              </Row>
              <Row label="치료 단계">
                {facets.settings.map(s => <Pill key={s.code} on={filters.settings.includes(s.code)} onClick={() => toggle('settings', s.code)}>{s.label}</Pill>)}
              </Row>
              {facets.markers.length > 0 && (
                <Row label="환자 바이오마커">
                  {facets.markers.map(m => <Pill key={m.value} on={filters.markers.includes(m.value)} onClick={() => toggle('markers', m.value)}>{m.label}</Pill>)}
                </Row>
              )}
              <p className="text-gray-400">바이오마커는 환자 상태입니다. 반대 조건(예: HER2 음성 환자에게 HER2 양성 요법)만 빠지고, 조건이 없는 요법은 남습니다.</p>
            </div>
          )}
        </div>
      )}

      {busy === 'db' && <div className="mt-4 text-sm text-gray-500"><i className="ri-loader-4-line animate-spin mr-1" />급여 기준 찾는 중…</div>}

      {res && targets.length > 0 && (
        <div>
          {/* 약 없이 상황만 입력: 환자 조건(바이오마커·이식 여부)에 맞는 요법 */}
          {res.answers.length === 0 && res.matches.length > 0 && (
            <div className="mt-4 rounded-xl border border-teal-300 bg-teal-50 p-4">
              <div className="flex items-center gap-2 font-bold text-sm text-teal-900">
                <i className="ri-focus-3-line text-lg text-teal-600" />환자 조건에 맞는 요법 {res.matches.length}개
              </div>
              <ul className="mt-2 space-y-1.5 text-xs">
                {res.matches.map(m => (
                  <li key={m.key}>
                    <a href={`#d-${m.key}`} onClick={e => { e.preventDefault(); document.getElementById(`d-${m.key}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }}
                      className="font-semibold hover:underline">{m.regimen}</a>
                    <span className="text-gray-500"> · {stepLabel(m)} · 일치: {m.matched.join(', ')} · {m.notice} ({m.effective})</span><UpcomingBadge date={m.effective} />
                    {m.combo && <span className="ml-1 px-1 rounded bg-violet-100 text-violet-700 text-[10px]">병용 신약 추가(추가 약은 비급여)</span>}
                    <div className="text-gray-700 mt-0.5">{m.condition}</div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 약별 판정 + 핵심 조건 */}
          {res.answers.length > 0 && (
            <div className="mt-4 space-y-2">
              {res.answers.map((a, i) => (
                <div key={a.drug} className={`rounded-xl border p-4 ${VERDICT[a.status].cls}`}>
                  <div className="flex items-center gap-2 font-bold text-sm">
                    <i className={`${VERDICT[a.status].icon} text-lg`} />
                    {drugs[i]?.name ?? a.drug} — {a.message}
                  </div>
                  {a.status === 'none' && a.elsewhere && a.elsewhere.length > 0 && (
                    <div className="mt-1.5 text-xs">
                      <span className="text-gray-700">다른 조건에서는 급여: <b>{a.elsewhere.join(' · ')}</b></span>
                      <button type="button" onClick={() => { setFilters(f => ({ ...f, settings: [], line: null, markers: [] })); setEdit(false); }}
                        className="ml-2 px-2 py-0.5 rounded border border-gray-300 bg-white text-teal-700 hover:border-teal-500">조건 풀고 보기</button>
                    </div>
                  )}
                  {a.rows.length > 0 && (
                    <ul className="mt-2 space-y-1.5 text-xs">
                      {a.rows.map(r => (
                        <li key={r.key}>
                          <a href={`#d-${r.key}`} onClick={e => { e.preventDefault(); setOnlyDrug(true); setTimeout(() => document.getElementById(`d-${r.key}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50); }}
                            className="font-semibold hover:underline">{r.regimen}</a>
                          <span className="text-gray-500"> · {r.cancer} · {stepLabel(r)} · {r.notice} ({r.effective})</span><UpcomingBadge date={r.effective} />
                          <div className="text-gray-700 mt-0.5">{r.condition}</div>
                        </li>
                      ))}
                    </ul>
                  )}
                  {a.items.length > 0 && (
                    <ul className="mt-2 space-y-1.5 text-xs">
                      {a.items.map(x => (
                        <li key={x.key}><b>{x.title}</b><span className="text-gray-500"> · {x.notice} ({x.effective})</span>
                          {x.age?.status === 'match' && <span className="ml-1 px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 text-[11px]">{age?.label} 해당 조항: {x.age.label}</span>}
                          {x.age?.status === 'outside' && <div className="text-amber-800 mt-0.5">{x.age.message}</div>}
                          <div className="text-gray-700 mt-0.5">{x.condition}</div></li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
              {drugs.length >= 2 && (
                <button type="button" onClick={() => onCompare(drugs.map(d => d.name), targets[0]?.id, filters.line)}
                  className="px-3 py-1.5 rounded-lg bg-white border border-teal-500 text-teal-700 text-xs font-semibold hover:bg-teal-50">
                  <i className="ri-git-compare-line mr-1" />{drugs.map(d => d.name).join(' vs ')} 차이 비교하기 →
                </button>
              )}
              {drugs.length > 0 && (res.onco.some(r => r.focus) || res.combo.some(r => r.focus) || res.general.some(g => g.focus)) && (
                <label className="flex items-center gap-1 text-xs text-gray-600">
                  <input type="checkbox" checked={onlyDrug} onChange={e => setOnlyDrug(e.target.checked)} className="accent-teal-600" />
                  아래 목록에서 {drugs.map(d => d.name).join(', ')}만 보기
                </label>
              )}
            </div>
          )}

          {/* 한눈에 보기 */}
          {view && (view.onco.length > 0 || view.combo.length > 0 || view.general.length > 0) && (
            <div className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
              <div className="flex items-center justify-between mb-2">
                <div className="font-bold text-sm">한눈에 보기</div>
                <div className="text-[11px] text-gray-400">약 이름을 누르면 조건으로 이동{filters.markers.length > 0 && <> · <i className="ri-focus-3-line text-teal-600" /> 환자 바이오마커와 맞는 요법</>}</div>
              </div>
              {view.onco.length > 0 && <AtAGlance rows={view.onco} idPrefix="d" />}
              {view.combo.length > 0 && (
                <div className={view.onco.length > 0 ? 'border-t border-gray-100 mt-1 pt-1' : ''}>
                  <AtAGlance rows={view.combo} idPrefix="d" prefix={() => '병용 신약 추가'} />
                  <div className="text-[10px] text-gray-400 mt-0.5">병용 신약 추가 요법: 추가 약은 비급여, 함께 쓰는 기존 요법만 급여 · ●는 시행 예정</div>
                </div>
              )}
              {view.general.length > 0 && (
                <div className="flex gap-3 py-1.5 text-xs border-t border-gray-100">
                  <div className="w-48 shrink-0 font-semibold text-gray-700">일반약제 급여기준</div>
                  <div className="flex flex-wrap gap-1">
                    {view.general.slice(0, 20).map(g => (
                      <a key={g.key} href={`#g-${g.key}`} onClick={e => { e.preventDefault(); document.getElementById(`g-${g.key}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }}
                        className={`px-1.5 py-0.5 rounded border ${g.focus ? 'bg-teal-600 text-white border-teal-600' : 'border-gray-200 text-gray-700 hover:border-teal-400'}`}>
                        {g.title.replace(/\s*\(품명.*$/, '').replace(/\s*(경구제|주사제|제제)$/, '')}
                      </a>
                    ))}
                  </div>
                </div>
              )}
              <div className="flex flex-wrap gap-3 mt-2 pt-2 border-t border-gray-100 text-[11px] text-gray-500">
                {view.combo.length > 0 && <span><T k="병용신약">병용 신약 추가 요법</T> {view.combo.length}개</span>}
              </div>
            </div>
          )}

          {view && view.onco.length > 0 && (
            <Section title={<>항암 급여 요법 — 치료 순서별</>} count={view.onco.length}
              note={<>진행·전이(<T k="고식적">고식적</T>) 요법의 <T k="차수">차수</T>: '1차 이상'은 2차 환자에게도 쓸 수 있습니다.</>}>
              <StepGroups rows={view.onco} keywords={kws} idPrefix="d" />
            </Section>
          )}
          {view && view.general.length > 0 && (
            <Section title="일반약제 급여기준" count={view.general.length} note="이 질환과 관련이 큰 기준부터 보여줍니다.">
              <div className="grid gap-2">{view.general.map(g => <GeneralItemCard key={g.key} item={g} keywords={kws} id={`g-${g.key}`} ageLabel={age?.label} />)}</div>
            </Section>
          )}
          {res.mentioned && res.mentioned.length > 0 && (
            <Section title="본문에서 언급만 된 기준" count={res.mentioned.length} defaultOpen={false}
              note="이 질환이 급여 대상이 아니라 위험요인·부작용 등으로만 언급된 기준입니다(예: '당뇨병이 병발한 경우').">
              <ul className="text-xs text-gray-600 space-y-0.5">{res.mentioned.map(m => <li key={m.key}>· {m.title}</li>)}</ul>
            </Section>
          )}
          {view && view.combo.length > 0 && (
            <Section title={<T k="병용신약">병용 신약 추가 요법 (기존 요법만 급여)</T>} count={view.combo.length} defaultOpen={res.answers.some(a => a.status === 'combo_only')}
              note="추가 약제는 이 표로 급여되지 않습니다. 함께 쓰는 기존 항암요법 부분만 급여로 인정됩니다.">
              <StepGroups rows={view.combo} keywords={kws} idPrefix="d" />
            </Section>
          )}
          {!res.onco.length && !res.general.length && !res.combo.length && (
            <div className="mt-8 text-center text-sm text-gray-400">조건에 맞는 급여 기준이 없습니다. 조건 칩을 지워 범위를 넓혀 보세요.</div>
          )}
        </div>
      )}
    </div>
  );
}

function Chip({ children, onRemove, onClick, title, tone = 'gray' }: {
  children: React.ReactNode; onRemove?: () => void; onClick?: () => void; title?: string; tone?: 'gray' | 'teal' | 'violet' | 'amber';
}) {
  const cls = { gray: 'bg-white border-gray-300 text-gray-700', teal: 'bg-teal-600 border-teal-600 text-white',
    violet: 'bg-violet-50 border-violet-300 text-violet-800', amber: 'bg-amber-50 border-amber-300 text-amber-800' }[tone];
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs ${cls}`} title={title}>
      {onClick ? <button type="button" onClick={onClick} className="hover:underline">{children}</button> : children}
      {onRemove && <button type="button" onClick={onRemove} aria-label="조건 지우기" className="opacity-60 hover:opacity-100"><i className="ri-close-line" /></button>}
    </span>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="flex flex-wrap items-center gap-1"><span className="w-24 text-gray-500">{label}</span>{children}</div>;
}

function Pill({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick}
      className={`px-2 py-0.5 rounded-full border ${on ? 'bg-teal-600 text-white border-teal-600' : 'border-gray-300 bg-white text-gray-600 hover:border-teal-400'}`}>
      {children}
    </button>
  );
}

