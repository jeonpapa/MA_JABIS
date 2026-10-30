import { useEffect, useState } from 'react';
import { compareDrugs, type CellStatus, type CompareResult } from '@/api/criteria';
import { Badge, CopyButton, DiffText, RichText, Section } from './shared';
import { copyRow, stepLabel } from './labels';

const CELL: Record<CellStatus, { cls: string; tag?: string; tone?: 'amber' | 'green' | 'gray' }> = {
  base: { cls: 'bg-white border-teal-300' },
  same: { cls: 'bg-white border-gray-200', tag: '같음', tone: 'gray' },
  changed: { cls: 'bg-amber-50 border-amber-300', tag: '조건 다름', tone: 'amber' },
  only_this: { cls: 'bg-emerald-50 border-emerald-300', tag: '이 약만 급여', tone: 'green' },
  only_base: { cls: 'bg-gray-50 border-gray-200', tag: '이 약은 해당 없음', tone: 'gray' },
  none: { cls: 'bg-gray-50 border-gray-100' },
};

export interface ComparePreset { drugs: string[]; focus?: string; line?: number | null; token: number }

export default function CompareTab({ list, setList, preset }: { list: string[]; setList: (l: string[]) => void; preset?: ComparePreset | null }) {
  const [slots, setSlots] = useState<string[]>(() => [...list, '', '', ''].slice(0, 3));
  const [base, setBase] = useState(0);
  const [focus, setFocus] = useState<string>('');
  const [res, setRes] = useState<CompareResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [diffOnly, setDiffOnly] = useState(true);
  const [line, setLine] = useState<number | null>(null);

  useEffect(() => { setSlots([...list, '', '', ''].slice(0, 3)); }, [list]);
  const filled = slots.map(s => s.trim()).filter(Boolean);

  const run = async (f: string = focus, ln: number | null = line, drugsIn: string[] = filled) => {
    if (drugsIn.length < 2) return;
    setBusy(true); setError('');
    setList(drugsIn);
    try { setRes(await compareDrugs(drugsIn, Math.min(base, drugsIn.length - 1), f || null, ln)); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  // 질환 탭에서 '차이 비교하기' 로 넘어온 경우 바로 실행
  useEffect(() => {
    if (!preset) return;
    setSlots([...preset.drugs, '', '', ''].slice(0, 3));
    setFocus(preset.focus ?? '');
    setLine(preset.line ?? null);
    run(preset.focus ?? '', preset.line ?? null, preset.drugs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset?.token]);

  const n = res?.drugs.length ?? 0;
  const grid = { gridTemplateColumns: `180px repeat(${n}, minmax(0, 1fr))` };
  const onco = (res?.onco ?? []).filter(o => !diffOnly || o.differs);
  const coverage = (res?.coverage ?? []).filter(c => !diffOnly || c.status.some(s => s === 'only_this' || s === 'only_base'));

  return (
    <div>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
        {slots.map((s, i) => (
          <div key={i} className={`rounded-lg border p-2 ${base === i ? 'border-teal-500 bg-teal-50/40' : 'border-gray-200 bg-white'}`}>
            <label className="flex items-center gap-1 cursor-pointer text-xs text-gray-600 mb-1">
              <input type="radio" name="base" checked={base === i} onChange={() => setBase(i)} className="accent-teal-600" />
              {base === i ? <span className="font-semibold text-teal-700">기준 약 (이 약과 비교)</span> : '기준으로 하기'}
            </label>
            <input value={s} onChange={e => setSlots(p => p.map((x, j) => j === i ? e.target.value : x))}
              onKeyDown={e => { if (e.key === 'Enter') run(); }} maxLength={100}
              placeholder={['예: 키트루다 / 휴미라', '예: 옵디보 / 레미케이드', '(선택) 세 번째 약'][i]}
              className="w-full px-2 py-1.5 rounded border border-gray-300 text-sm focus:border-teal-500" />
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3 mt-2">
        <button type="button" onClick={() => run()} disabled={busy || filled.length < 2}
          className="px-5 py-2 rounded-lg bg-teal-600 text-white text-sm font-semibold hover:bg-teal-700 disabled:opacity-50">
          {busy ? <><i className="ri-loader-4-line animate-spin mr-1" />비교 중</> : '비교하기'}
        </button>
        {res && res.focus_options.length > 1 && (
          <select value={focus} onChange={e => { setFocus(e.target.value); run(e.target.value); }}
            className="text-xs border border-gray-300 rounded px-2 py-1.5">
            <option value="">모든 질환</option>
            {res.focus_options.map(o => <option key={o.id} value={o.id}>{o.name}만 보기</option>)}
          </select>
        )}
        {res && res.onco.length > 0 && (
          <select value={line ?? ''} onChange={e => { const v = e.target.value ? Number(e.target.value) : null; setLine(v); run(focus, v); }}
            className="text-xs border border-gray-300 rounded px-2 py-1.5">
            <option value="">모든 차수</option>
            {[1, 2, 3].map(n => <option key={n} value={n}>{n}차 치료 환자</option>)}
          </select>
        )}
        <label className="flex items-center gap-1 text-xs text-gray-600">
          <input type="checkbox" checked={diffOnly} onChange={e => setDiffOnly(e.target.checked)} className="accent-teal-600" />차이 있는 것만
        </label>
      </div>
      <p className="text-[11px] text-gray-500 mt-2">
        읽는 법: <mark className="bg-emerald-100 px-1 rounded">초록 글자</mark>는 기준 약에 없는 조건,
        <del className="text-red-400 mx-1">작은 빨간 취소선</del>은 기준 약에만 있는 조건입니다. 번호·띄어쓰기 차이는 무시합니다.
      </p>
      {error && <div className="mt-3 text-sm text-red-600 bg-red-50 rounded p-2">{error}</div>}

      {res && (
        <div className="mt-4 space-y-2">
          <div className="grid gap-2 sticky top-0 z-10 bg-gray-50 py-1" style={grid}>
            <div className="text-[11px] text-gray-500 self-end">
              {res.summary.cells > 0 && <>항암 칸 {res.summary.cells}개 중 차이 {res.summary.differ}개<br /></>}
              급여 질환 차이 {res.summary.diseases_differ}개
            </div>
            {res.drugs.map((d, i) => (
              <div key={i} className={`rounded-lg px-3 py-2 text-sm ${i === res.base ? 'bg-teal-600 text-white' : 'bg-white border border-gray-200'}`}>
                <div className="font-bold">{d.label}{i === res.base && <span className="ml-1 text-[11px] font-normal">· 기준</span>}</div>
                <div className={`text-[11px] ${i === res.base ? 'text-teal-50' : 'text-gray-500'}`}>
                  {d.bundle_only ? '묶음 기준에 포함' : [d.onco_count && `항암 ${d.onco_count}`, d.general_count && `일반 기준 ${d.general_count}`].filter(Boolean).join(' · ') || '급여 기준 없음'}
                </div>
              </div>
            ))}
          </div>

          {res.highlights.length > 0 && (
            <div className="rounded-xl border border-teal-200 bg-teal-50/50 p-3">
              <div className="flex items-center justify-between mb-1">
                <div className="font-bold text-sm text-teal-900">핵심 차이</div>
                <CopyButton text={`[${res.drugs.map(d => d.label).join(' vs ')}] 급여기준 핵심 차이\n` + res.highlights.map(h => '- ' + h).join('\n') + (res.sources?.length ? '\n\n근거: ' + res.sources.join(' / ') : '') + '\n출처: 건강보험심사평가원 공고·고시(공공누리 4유형)'} label="요약 복사" />
              </div>
              <ul className="space-y-1 text-xs text-gray-800 list-disc pl-4">
                {res.highlights.map((h, i) => <li key={i}>{h}</li>)}
              </ul>
            </div>
          )}
          {res.highlights.length === 0 && res.summary.differ === 0 && (
            <div className="rounded-xl border border-gray-200 bg-white p-3 text-sm text-gray-600">선택한 범위에서 급여 기준 문구 차이가 없습니다.</div>
          )}

          {res.onco.length > 0 && (
            <Section title="항암 급여 요법 비교 (암종 × 치료 단계)" count={onco.length}>
              <div className="space-y-2">
                {onco.map((o, k) => (
                  <div key={k} className="grid gap-2 text-xs" style={grid}>
                    <div className="py-1">
                      <div className="font-semibold text-gray-800">{o.cancer}</div>
                      <div className="text-gray-500">{stepLabel(o)}</div>
                      {o.combo && <div className="mt-0.5"><Badge tone="violet">병용 신약 추가</Badge></div>}
                    </div>
                    {o.entries.map((e, i) => (
                      <div key={i} className={`rounded-lg border p-2 ${CELL[e.status].cls}`}>
                        {CELL[e.status].tag && e.status !== 'none' && (
                          <div className="mb-1"><Badge tone={CELL[e.status].tone}>
                            {o.combo && e.status === 'only_this' ? '이 약만 병용 등재 (추가 약은 비급여)' : CELL[e.status].tag}
                          </Badge></div>
                        )}
                        {e.status === 'none' && <div className="text-gray-300">—</div>}
                        {e.rows.map(r => (
                          <div key={r.key} className="mb-2 last:mb-0">
                            <div className="flex justify-between gap-2">
                              <span className="font-semibold text-teal-800">{r.regimen}</span>
                              <CopyButton text={copyRow(r)} />
                            </div>
                            <div className="text-gray-700 mt-0.5">
                              {r.diff ? <DiffText segs={r.diff} /> : <RichText text={r.is_combo_table ? `추가 약제 조건: ${r.added_condition}\n급여되는 기존 요법: ${r.base_regimen ?? ''}\n${r.target}` : r.target} clampLines={5} />}
                            </div>
                            {r.notice && <div className="text-[10px] text-gray-400 mt-0.5">{r.notice} · {r.effective}</div>}
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>
                ))}
                {!onco.length && <div className="text-sm text-gray-400">차이 나는 칸이 없습니다.</div>}
              </div>
            </Section>
          )}

          {res.general.some(g => g.length) && (
            <Section title="일반약제 급여기준 비교 (질환 문단끼리)" count={res.general.reduce((a, g) => a + g.length, 0)}>
              <div className="grid gap-2" style={grid}>
                <div />
                {res.general.map((items, i) => (
                  <div key={i} className="space-y-2">
                    {!items.length && <div className="rounded-lg border border-gray-100 bg-gray-50 p-3 text-xs text-gray-400">일반약제 급여기준 없음</div>}
                    {items.map(g => (
                      <div key={g.key} className={`rounded-lg border p-2 text-xs ${g.status === 'changed' ? 'border-amber-300 bg-amber-50/40' : 'border-gray-200 bg-white'}`}>
                        <div className="flex justify-between gap-2">
                          <span className="font-semibold text-gray-900"><span className="text-teal-700">{res.drugs[i]?.label.split(' (')[0]}</span> · {g.title}</span>
                          <CopyButton text={`${g.title}\n${g.excerpt ?? g.criteria}\n(${g.notice ?? ''}, ${g.effective ?? ''} 시행)`} />
                        </div>
                        <div className="text-[11px] text-gray-500 mb-1">{g.notice} · {g.effective} 시행</div>
                        {g.status === 'shared' && <div className="text-gray-600">기준 약과 <b>같은 묶음 기준</b>을 씁니다 — 기준 문구 차이 없음.</div>}
                        {(g.status === 'base' || g.status === 'only_this') && <RichText text={g.excerpt ?? g.criteria} clampLines={10} />}
                        {g.blocks && (
                          <div className="space-y-2">
                            {g.blocks.filter(b => !diffOnly || b.status !== 'same').map((b, j) => (
                              <div key={j}>
                                {b.heading && <div className="font-semibold text-gray-800 flex items-center gap-1">{b.heading}
                                  {b.status !== 'changed' && <Badge tone={b.status === 'only_this' ? 'green' : 'gray'}>{b.status === 'same' ? '같음' : b.status === 'only_this' ? '이 약만 있음' : '이 약은 없음'}</Badge>}</div>}
                                {b.diff ? <DiffText segs={b.diff} /> : b.text ? <RichText text={b.text} clampLines={4} /> : null}
                              </div>
                            ))}
                            {diffOnly && g.blocks.some(b => b.status === 'same') && (
                              <div className="text-[11px] text-gray-400">같은 문단 {g.blocks.filter(b => b.status === 'same').length}개는 숨김</div>
                            )}
                            {g.status === 'same' && <div className="text-gray-500">기준 약과 문구가 같습니다.</div>}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            </Section>
          )}

          <Section title="급여 질환 비교" count={coverage.length} defaultOpen={!res.onco.length && !res.general.some(g => g.length)}>
            <div className="space-y-1">
              {coverage.map(c => (
                <div key={c.id} className="grid gap-2 text-xs" style={grid}>
                  <div className="py-1.5 text-gray-700">{c.name}</div>
                  {c.has.map((h, i) => {
                    const st = c.status[i];
                    const cls = st === 'only_this' ? 'bg-emerald-50 border-emerald-300' : (!h ? 'bg-gray-50 border-gray-200' : 'bg-white border-gray-200');
                    return (
                      <div key={i} className={`rounded border px-2 py-1.5 ${cls}`}>
                        {h ? <span className="text-emerald-700"><i className="ri-check-line" /> 급여{st === 'only_this' && ' · 이 약만'}</span> : <span className="text-gray-400">해당 없음</span>}
                      </div>
                    );
                  })}
                </div>
              ))}
              {!coverage.length && <div className="text-sm text-gray-400">급여 질환이 모두 같습니다.</div>}
            </div>
          </Section>
        </div>
      )}
    </div>
  );
}
