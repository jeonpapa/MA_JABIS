// 해외약가 모니터링 (2026-11-10 사용자 요청)
// 프로젝트(약제 N개·국가·주기·수신자) → 회차마다 수집 → 직전 회차 대비 변경 하이라이트 메일. 하루 전·완료 후 텔레그램.
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  fetchMonitorConfig, fetchMonitorProjects, fetchMonitorProject, createMonitorProject, updateMonitorProject,
  deleteMonitorProject, runMonitorNow, fetchMonitorRun, fetchMonitorMailPreview, downloadMonitorRun,
  COUNTRY_KO, KIND_KO, VERDICT_KO, RUN_STATUS_KO,
  type MonitorConfig, type MonitorProject, type MonitorRun, type MonitorSchedule, type MonitorItem, type MonitorKind, type ProjectInput,
} from '@/api/priceMonitor';
import { resolveIdentity, type Candidate } from '@/api/foreign';

const A8 = ['JP', 'IT', 'FR', 'CH', 'UK', 'DE', 'CA', 'US'];
const WEEK = ['월', '화', '수', '목', '금', '토', '일'];
const KIND_STYLE: Record<MonitorKind, string> = {
  price: 'bg-rose-50 text-rose-700 border-rose-200', basis: 'bg-amber-50 text-amber-800 border-amber-200',
  packs: 'bg-indigo-50 text-indigo-700 border-indigo-200', now_priced: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  lost: 'bg-gray-100 text-gray-700 border-gray-300', unpriced: 'bg-gray-50 text-gray-500 border-gray-200',
  same: 'bg-white text-gray-500 border-gray-200', first: 'bg-white text-gray-600 border-gray-200',
};

const kst = (iso?: string | null) => iso ? new Date(iso).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-';
const money = (v: number[] | number | null | undefined, cur = '') => v == null ? '-' : (Array.isArray(v) ? v : [v]).map(x => x.toLocaleString('en-US', { maximumFractionDigits: 2 })).join(' / ') + (cur ? ' ' + cur : '');
const pct = (p: number | null | undefined) => p == null ? '' : `${p > 0 ? '+' : ''}${(p * 100).toFixed(1)}%`;
const errMsg = (e: unknown) => e instanceof Error ? e.message : String(e);

function readDeepLink() {
  const p = new URLSearchParams(window.location.search);
  return { project: p.get('project') || '', run: p.get('run') || '' };
}

// ── 설정 폼 ────────────────────────────────────────────────────────────────
function ProjectForm({ initial, config, onCancel, onSaved }: {
  initial?: MonitorProject | null; config: MonitorConfig | null; onCancel: () => void; onSaved: (p: MonitorProject) => void;
}) {
  const [name, setName] = useState(initial?.name ?? '');
  const [items, setItems] = useState<MonitorItem[]>(initial?.items.map(i => ({ query: i.query, identity: i.identity, label: i.label })) ?? []);
  const [bulk, setBulk] = useState('');
  const [search, setSearch] = useState('');
  const [cands, setCands] = useState<Candidate[]>([]);
  const [countries, setCountries] = useState<string[]>(initial?.countries ?? A8);
  const [schedType, setSchedType] = useState<MonitorSchedule['type']>(initial?.schedule.type ?? 'weekly');
  const [weekdays, setWeekdays] = useState<number[]>(initial?.schedule.type === 'weekly' ? initial.schedule.weekdays : [1]);
  const [monthDays, setMonthDays] = useState<string>(initial?.schedule.type === 'monthly' ? initial.schedule.days.join(', ') : '1');
  const [dates, setDates] = useState<string[]>(initial?.schedule.type === 'dates' ? initial.schedule.dates : []);
  const [dateInput, setDateInput] = useState('');
  const [hour, setHour] = useState<number>(initial?.schedule.hour ?? 9);
  const [recipients, setRecipients] = useState(initial?.recipients.join(', ') ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const t = search.trim();
    if (t.length < 2) { setCands([]); return; }
    const h = window.setTimeout(() => { resolveIdentity(t).then(setCands).catch(() => setCands([])); }, 250);
    return () => window.clearTimeout(h);
  }, [search]);

  const addItems = (list: MonitorItem[]) => setItems(prev => {
    const seen = new Set(prev.map(x => x.query.toLowerCase()));
    return [...prev, ...list.filter(x => { const k = x.query.trim().toLowerCase(); if (!k || seen.has(k)) return false; seen.add(k); return true; })];
  });
  const addBulk = () => { addItems(bulk.split(/[\n,;]+/).map(s => ({ query: s.trim() })).filter(x => x.query)); setBulk(''); };
  const leadHours = useMemo(() => {
    const per = config?.hours_per_item_a8 ?? 3;
    return Math.min(Math.max(per * Math.max(items.length, 1) * (Math.max(countries.length, 1) / 8) + 12, 30), 336);
  }, [items.length, countries.length, config]);

  const schedule = (): MonitorSchedule => {
    if (schedType === 'weekly') return { type: 'weekly', weekdays, hour };
    if (schedType === 'monthly') return { type: 'monthly', days: monthDays.split(/[\s,]+/).map(Number).filter(n => n >= 1 && n <= 31), hour };
    return { type: 'dates', dates, hour };
  };
  const save = async () => {
    setError(''); setSaving(true);
    const input: ProjectInput = {
      name: name.trim(), items, countries, schedule: schedule(),
      recipients: recipients.split(/[\s,;]+/).map(s => s.trim()).filter(Boolean),
    };
    try {
      const res = initial ? await updateMonitorProject(initial.id, input) : await createMonitorProject(input);
      onSaved(res.project);
    } catch (e) { setError(errMsg(e)); } finally { setSaving(false); }
  };
  const domains = (config?.domains ?? ['msd.com']).map(d => '@' + d).join(', ');

  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5 space-y-5">
      <div className="flex items-center justify-between">
        <h3 className="font-bold text-gray-800">{initial ? '모니터링 설정 수정' : '새 모니터링 프로젝트'}</h3>
        <button onClick={onCancel} className="text-sm text-gray-500 hover:text-gray-700">닫기 ×</button>
      </div>
      <label className="block">
        <span className="text-xs font-semibold text-gray-500">프로젝트 이름</span>
        <input value={name} onChange={e => setName(e.target.value)} maxLength={80} placeholder="예: 항암 면역항암제 A8 주간 모니터링"
          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:border-teal-300" />
      </label>

      <div>
        <span className="text-xs font-semibold text-gray-500">대상 약제 ({items.length}개 · 개수 제한 없음)</span>
        <div className="mt-1 flex flex-wrap gap-1.5 min-h-[36px] rounded-lg border border-gray-200 p-2">
          {items.length === 0 && <span className="text-xs text-gray-400">아래에서 약제를 추가하세요</span>}
          {items.map(it => (
            <span key={it.query} className="inline-flex items-center gap-1 rounded-full bg-teal-50 border border-teal-200 px-2.5 py-0.5 text-xs text-teal-800">
              {it.label || it.query}{it.identity?.inn ? <span className="text-teal-500">({it.identity.inn})</span> : null}
              <button aria-label={`${it.query} 빼기`} onClick={() => setItems(items.filter(x => x.query !== it.query))} className="text-teal-500 hover:text-rose-600">×</button>
            </span>
          ))}
        </div>
        <div className="mt-2 grid md:grid-cols-2 gap-2">
          <div className="relative">
            <input value={search} onChange={e => setSearch(e.target.value)} placeholder="약제 검색(상표·성분)"
              onKeyDown={e => { if (e.key === 'Enter' && search.trim()) { addItems([{ query: search.trim() }]); setSearch(''); } }}
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:border-teal-300" />
            {cands.length > 0 && (
              <div className="absolute z-20 mt-1 w-full rounded-lg border border-gray-200 bg-white shadow-lg">
                {cands.map(c => (
                  <button key={c.slug} onClick={() => { addItems([{ query: c.slug, identity: { slug: c.slug, brand: c.brand, inn: c.inn }, label: c.display || c.brand }]); setSearch(''); setCands([]); }}
                    className="block w-full text-left px-3 py-2 text-sm hover:bg-teal-50">
                    <b>{c.display || c.brand}</b> <span className="text-xs text-gray-500">{c.inn}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="flex gap-2">
            <textarea value={bulk} onChange={e => setBulk(e.target.value)} rows={1} placeholder="여러 개 붙여넣기(줄바꿈·쉼표)"
              className="flex-1 rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:border-teal-300" />
            <button onClick={addBulk} disabled={!bulk.trim()} className="rounded-lg border border-teal-300 px-3 text-sm text-teal-700 disabled:opacity-50">추가</button>
          </div>
        </div>
      </div>

      <div>
        <span className="text-xs font-semibold text-gray-500">국가</span>
        <div className="mt-1 flex flex-wrap gap-2">
          {A8.map(cc => (
            <label key={cc} className={`cursor-pointer rounded-full border px-3 py-1 text-xs ${countries.includes(cc) ? 'bg-teal-600 border-teal-600 text-white' : 'bg-white border-gray-200 text-gray-600'}`}>
              <input type="checkbox" className="sr-only" checked={countries.includes(cc)}
                onChange={() => setCountries(countries.includes(cc) ? countries.filter(x => x !== cc) : A8.filter(x => x === cc || countries.includes(x)))} />
              {COUNTRY_KO[cc]}
            </label>
          ))}
        </div>
      </div>

      <div>
        <span className="text-xs font-semibold text-gray-500">주기 (한국 시간)</span>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          {(['weekly', 'monthly', 'dates'] as const).map(t => (
            <button key={t} onClick={() => setSchedType(t)}
              className={`rounded-lg px-3 py-1.5 text-sm border ${schedType === t ? 'bg-teal-600 text-white border-teal-600' : 'bg-white text-gray-600 border-gray-200'}`}>
              {t === 'weekly' ? '주 단위' : t === 'monthly' ? '월 단위' : '특정 일자'}
            </button>
          ))}
          <select value={hour} onChange={e => setHour(Number(e.target.value))} aria-label="발송 시각"
            className="rounded-lg border border-gray-200 px-2 py-1.5 text-sm">
            {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00 발송</option>)}
          </select>
        </div>
        {schedType === 'weekly' && (
          <div className="mt-2 flex gap-1.5">
            {WEEK.map((w, i) => (
              <button key={w} onClick={() => setWeekdays(weekdays.includes(i + 1) ? weekdays.filter(d => d !== i + 1) : [...weekdays, i + 1].sort())}
                className={`h-8 w-8 rounded-full text-sm border ${weekdays.includes(i + 1) ? 'bg-teal-600 text-white border-teal-600' : 'bg-white text-gray-600 border-gray-200'}`}>{w}</button>
            ))}
          </div>
        )}
        {schedType === 'monthly' && (
          <input value={monthDays} onChange={e => setMonthDays(e.target.value)} placeholder="예: 1, 15 (없는 날은 말일)"
            className="mt-2 w-60 rounded-lg border border-gray-200 px-3 py-1.5 text-sm" />
        )}
        {schedType === 'dates' && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input type="date" value={dateInput} onChange={e => setDateInput(e.target.value)} className="rounded-lg border border-gray-200 px-2 py-1 text-sm" />
            <button onClick={() => { if (dateInput && !dates.includes(dateInput)) setDates([...dates, dateInput].sort()); setDateInput(''); }}
              className="rounded-lg border border-teal-300 px-3 py-1 text-sm text-teal-700">날짜 추가</button>
            {dates.map(d => (
              <span key={d} className="rounded-full bg-gray-100 px-2.5 py-0.5 text-xs text-gray-700">{d}
                <button className="ml-1 text-gray-400 hover:text-rose-600" onClick={() => setDates(dates.filter(x => x !== d))}>×</button></span>
            ))}
          </div>
        )}
        <p className="mt-2 text-xs text-gray-500">
          약제 {items.length}개 · {countries.length}개국 기준, 발송 약 <b>{Math.round(leadHours)}시간 전</b>에 수집을 시작합니다.
          수집이 덜 끝나면 끝날 때까지 발송을 미루고, 하루 전과 완료 후 텔레그램으로 알립니다.
        </p>
      </div>

      <label className="block">
        <span className="text-xs font-semibold text-gray-500">수신자 (회사 도메인 {domains} · 여러 명은 쉼표·줄바꿈)</span>
        <textarea value={recipients} onChange={e => setRecipients(e.target.value)} rows={2} placeholder="name@msd.com, team@msd.com"
          className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:outline-none focus:border-teal-300" />
      </label>

      {error && <p role="alert" className="text-sm text-rose-600">{error}</p>}
      <div className="flex justify-end gap-2">
        <button onClick={onCancel} className="rounded-lg border border-gray-200 px-4 py-2 text-sm text-gray-600">취소</button>
        <button onClick={save} disabled={saving || !name.trim() || !items.length || !countries.length || !recipients.trim()}
          className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">{saving ? '저장 중…' : '저장'}</button>
      </div>
    </div>
  );
}

// ── 회차 상세 ──────────────────────────────────────────────────────────────
function RunDetail({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [run, setRun] = useState<MonitorRun | null>(null);
  const [error, setError] = useState('');
  const [onlyChanged, setOnlyChanged] = useState(true);
  const [preview, setPreview] = useState<{ subject: string; html: string } | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => fetchMonitorRun(runId).then(r => { if (alive) setRun(r.run); }).catch(e => alive && setError(errMsg(e)));
    load();
    const t = window.setInterval(() => { if (run && ['sent', 'failed', 'cancelled'].includes(run.status)) return; load(); }, 60000);
    return () => { alive = false; window.clearInterval(t); };
  }, [runId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (error) return <p className="text-sm text-rose-600">{error}</p>;
  if (!run) return <p className="text-sm text-gray-400">불러오는 중…</p>;
  const diff = run.diff, snap = run.snapshot;
  const countries = run.countries ?? [];
  const items = (diff?.items ?? []).filter(it => !onlyChanged || it.highlight || !diff?.has_previous);
  const snapBy = Object.fromEntries((snap?.items ?? []).map(i => [i.query, i]));

  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5 space-y-4">
      <div className="flex flex-wrap items-center gap-2 justify-between">
        <div>
          <h3 className="font-bold text-gray-800">회차 {kst(run.due_at)} · {RUN_STATUS_KO[run.status]}</h3>
          <p className="text-xs text-gray-500">
            수집 시작 {kst(run.started_at || run.start_at)}{run.ready_at ? ` · 수집 완료 ${kst(run.ready_at)}` : ''}{run.sent_at ? ` · 발송 ${kst(run.sent_at)}` : ''}
            {diff?.previous_due_at ? ` · 비교 대상 ${kst(diff.previous_due_at)} 회차` : ''}
          </p>
        </div>
        <div className="flex gap-2">
          {snap && <button onClick={() => fetchMonitorMailPreview(run.id).then(setPreview).catch(e => setError(errMsg(e)))}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-bold text-gray-700">메일 미리보기</button>}
          {snap && <button onClick={() => downloadMonitorRun(run.id).catch(e => setError(errMsg(e)))}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs font-bold text-gray-700"><i className="ri-file-excel-2-line"></i> 엑셀</button>}
          <button onClick={onClose} className="text-sm text-gray-500">닫기 ×</button>
        </div>
      </div>

      {run.progress && (
        <div className="rounded-xl bg-gray-50 p-3 text-xs text-gray-600">
          <p className="font-semibold mb-1">수집 진행</p>
          <div className="grid md:grid-cols-2 gap-1">
            {run.progress.map(p => {
              const last = p.requests[p.requests.length - 1];
              const done = p.requests[0]?.countries.filter(c => c.status !== 'pending').length ?? 0;
              return <div key={p.query}>{p.query}: {last?.status ?? '-'} · {done}/{p.requests[0]?.countries.length ?? 0}개국{p.requests.length > 1 ? ` · 재요청 ${p.requests.length - 1}회` : ''}</div>;
            })}
          </div>
        </div>
      )}

      {diff && (
        <>
          {!diff.has_previous ? (
            <p className="rounded-lg bg-gray-50 p-3 text-sm text-gray-600">첫 회차입니다. 이번 가격을 기준으로 저장했고 다음 회차부터 변경을 표시합니다.</p>
          ) : (
            <div className="flex flex-wrap gap-2 text-xs">
              {(['price', 'basis', 'packs', 'now_priced', 'lost'] as MonitorKind[]).map(k => (
                <span key={k} className={`rounded-full border px-3 py-1 ${KIND_STYLE[k]}`}>{KIND_KO[k]} <b>{diff.totals[k] ?? 0}</b></span>
              ))}
              <span className="rounded-full border border-gray-200 px-3 py-1 text-gray-500">변동 없음 {diff.totals.same ?? 0}</span>
              <span className="rounded-full border border-gray-200 px-3 py-1 text-gray-500" title="현지 원가격은 같고 원화 조정가만 0.5% 이상 바뀜">환율 영향만 {diff.totals.fx ?? 0}</span>
              <label className="ml-auto flex items-center gap-1 text-gray-600"><input type="checkbox" checked={onlyChanged} onChange={e => setOnlyChanged(e.target.checked)} /> 바뀐 약제만</label>
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs">
              <thead><tr className="bg-gray-50 text-gray-600">
                <th className="px-2 py-2 text-left sticky left-0 bg-gray-50">약제</th>
                {countries.map(cc => <th key={cc} className="px-2 py-2">{COUNTRY_KO[cc] ?? cc}</th>)}
              </tr></thead>
              <tbody>
                {items.length === 0 && <tr><td colSpan={countries.length + 1} className="p-4 text-center text-gray-400">직전 회차 대비 바뀐 약제가 없습니다.</td></tr>}
                {items.map(it => (
                  <tr key={it.query} className="border-t border-gray-100 align-top">
                    <td className="px-2 py-2 font-semibold text-gray-800 sticky left-0 bg-white">{it.label}{it.is_new_item && <span className="ml-1 text-[10px] text-teal-600">신규</span>}</td>
                    {countries.map(cc => {
                      const d = it.countries[cc];
                      const sc = snapBy[it.query]?.countries[cc];
                      if (!d) return <td key={cc} className="px-2 py-2 text-gray-300">-</td>;
                      const first = sc?.rows?.[0];
                      return (
                        <td key={cc} className="px-1.5 py-1.5">
                          <div className={`rounded-lg border px-2 py-1.5 min-w-[120px] ${KIND_STYLE[d.kind]}`}
                            title={(sc?.verdict_sources ?? []).map(s => `${s.url} — ${s.finding}`).join('\n') || d.reason || ''}>
                            <div className="font-semibold">{KIND_KO[d.kind]}</div>
                            {d.kind === 'price' && d.changes.slice(0, 2).map(c => (
                              <div key={c.key}>{money(c.old)} → <b>{money(c.new, c.currency)}</b> <span className={(c.pct ?? 0) < 0 ? 'text-blue-700' : 'text-rose-700'}>{pct(c.pct)}</span></div>
                            ))}
                            {(d.kind === 'basis' || d.kind === 'packs') && d.changes.slice(0, 2).map(c => <div key={c.key} className="truncate max-w-[180px]">{c.label}: {money(c.old)} → {money(c.new, c.currency)}</div>)}
                            {['same', 'first', 'now_priced'].includes(d.kind) && first && <div>{money(first.local_price ?? null, first.currency)}</div>}
                            {d.fx && <div className="text-gray-500">원화 {pct(d.fx.pct)} (환율)</div>}
                            {['lost', 'unpriced'].includes(d.kind) && <div>{VERDICT_KO[d.verdict ?? ''] ?? (d.status === 'failed' ? '수집 실패' : '미확정')}</div>}
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-gray-500">비교는 같은 출처·가격 단계의 현지 통화 원가격 기준입니다. '기준·출처 변경'은 인하·인상으로 읽지 않습니다. 칸에 마우스를 올리면 미확인 판정의 확인 출처가 보입니다.</p>
        </>
      )}
      {!diff && run.status !== 'cancelled' && <p className="text-sm text-gray-500">수집이 끝나면 직전 회차와 비교한 결과가 여기에 표시됩니다.</p>}

      {preview && (
        <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={() => setPreview(null)}>
          <div className="max-h-[85vh] w-full max-w-4xl overflow-auto rounded-2xl bg-white p-5" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between mb-3"><b className="text-sm">{preview.subject}</b><button onClick={() => setPreview(null)} className="text-sm text-gray-500">닫기 ×</button></div>
            <iframe title="메일 미리보기" sandbox="" srcDoc={preview.html} className="w-full h-[65vh] border border-gray-200 rounded-lg" />
          </div>
        </div>
      )}
    </div>
  );
}

// ── 메인 ───────────────────────────────────────────────────────────────────
export default function MonitoringPanel() {
  const deep = useMemo(readDeepLink, []);
  const [config, setConfig] = useState<MonitorConfig | null>(null);
  const [projects, setProjects] = useState<MonitorProject[]>([]);
  const [selected, setSelected] = useState<string>(deep.project);
  const [detail, setDetail] = useState<{ project: MonitorProject; runs: MonitorRun[] } | null>(null);
  const [runId, setRunId] = useState<string>(deep.run);
  const [form, setForm] = useState<'new' | 'edit' | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const loadList = useCallback(() => fetchMonitorProjects().then(r => {
    setProjects(r.projects);
    setSelected(cur => (cur && r.projects.some(x => x.id === cur)) ? cur : (r.projects[0]?.id ?? ''));
  }).catch(e => setError(errMsg(e))), []);
  const loadDetail = useCallback((id: string) => fetchMonitorProject(id).then(setDetail).catch(e => setError(errMsg(e))), []);

  useEffect(() => { fetchMonitorConfig().then(setConfig).catch(() => setConfig(null)); loadList(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (selected) loadDetail(selected); else setDetail(null); }, [selected, loadDetail]);

  const act = async (fn: () => Promise<unknown>, reloadDetail = true) => {
    setBusy(true); setError('');
    try { await fn(); await loadList(); if (reloadDetail && selected) await loadDetail(selected); } catch (e) { setError(errMsg(e)); } finally { setBusy(false); }
  };
  const p = detail?.project;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-bold text-gray-800">해외약가 모니터링</h2>
          <p className="text-xs text-gray-500">프로젝트별로 약제·국가·주기를 정하면 회차마다 새로 수집하고, 직전 회차 대비 바뀐 가격을 하이라이트해 메일로 보냅니다.
            {config && !config.telegram && ' (텔레그램 알림 설정 대기)'}
            {config && config.worker.state !== 'active' && <span className="text-amber-700"> · 수집 워커가 최근 응답하지 않습니다</span>}</p>
        </div>
        <button onClick={() => { setForm('new'); setRunId(''); }} className="rounded-lg bg-teal-600 px-4 py-2 text-sm font-bold text-white">+ 새 프로젝트</button>
      </div>
      {error && <p role="alert" className="text-sm text-rose-600">{error}</p>}

      <div className="grid lg:grid-cols-[280px_1fr] gap-4">
        <div className="space-y-2">
          {projects.length === 0 && <p className="rounded-xl border border-dashed border-gray-300 p-4 text-sm text-gray-500">아직 프로젝트가 없습니다.</p>}
          {projects.map(pr => (
            <button key={pr.id} onClick={() => { setSelected(pr.id); setRunId(''); setForm(null); }}
              className={`w-full text-left rounded-xl border p-3 ${selected === pr.id ? 'border-teal-400 bg-teal-50/50' : 'border-gray-200 bg-white hover:border-teal-200'}`}>
              <div className="flex items-center gap-2"><b className="text-sm text-gray-800 truncate flex-1">{pr.name}</b>
                {pr.status === 'paused' && <span className="text-[10px] rounded bg-gray-200 px-1.5 text-gray-600">일시정지</span>}</div>
              <p className="text-xs text-gray-500 mt-0.5">{pr.schedule_text} · 약제 {pr.items.length}개 · {pr.countries.length}개국</p>
              {pr.next_run && <p className="text-xs text-teal-700 mt-0.5">다음 {kst(pr.next_run.due_at)} · {RUN_STATUS_KO[pr.next_run.status]}</p>}
            </button>
          ))}
        </div>

        <div className="space-y-4 min-w-0">
          {form && <ProjectForm initial={form === 'edit' ? p : null} config={config} onCancel={() => setForm(null)}
            onSaved={pr => { setForm(null); setSelected(pr.id); loadList(); loadDetail(pr.id); }} />}

          {!form && p && (
            <div className="rounded-2xl border border-gray-200 bg-white p-5 space-y-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <h3 className="font-bold text-gray-800">{p.name}</h3>
                  <p className="text-xs text-gray-500">{p.schedule_text} · {p.countries.map(c => COUNTRY_KO[c]).join(', ')} · 수신 {p.recipients.join(', ')}</p>
                  <p className="text-xs text-gray-500">발송 약 {Math.round(p.lead_hours)}시간 전 수집 시작{p.next_run ? ` · 다음 회차 ${kst(p.next_run.due_at)} (${RUN_STATUS_KO[p.next_run.status]})` : ''}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button disabled={busy || p.status !== 'active'} onClick={() => act(async () => { const r = await runMonitorNow(p.id); setRunId(r.run.id); })}
                    className="rounded-lg border border-teal-300 px-3 py-1.5 text-xs font-bold text-teal-700 disabled:opacity-50" title="지금 수집을 시작하고 끝나는 대로 발송합니다">지금 실행</button>
                  <button disabled={busy} onClick={() => act(() => updateMonitorProject(p.id, { status: p.status === 'active' ? 'paused' : 'active' }))}
                    className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-700">{p.status === 'active' ? '일시정지' : '다시 시작'}</button>
                  <button onClick={() => setForm('edit')} className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-700">설정 수정</button>
                  <button disabled={busy} onClick={() => { if (window.confirm(`'${p.name}' 프로젝트를 삭제할까요? 이전 회차 기록은 보관됩니다.`)) act(async () => { await deleteMonitorProject(p.id); setSelected(''); setDetail(null); setRunId(''); }, false); }}
                    className="rounded-lg border border-rose-200 px-3 py-1.5 text-xs text-rose-600">삭제</button>
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {p.items.map(it => <span key={it.query} className="rounded-full bg-gray-100 px-2.5 py-0.5 text-xs text-gray-700">{it.label || it.query}</span>)}
              </div>

              <div>
                <h4 className="text-sm font-bold text-gray-700 mb-1">이전 모니터링</h4>
                {(detail?.runs ?? []).length === 0 ? <p className="text-xs text-gray-400">아직 회차가 없습니다.</p> : (
                  <table className="w-full text-xs">
                    <thead><tr className="text-gray-500 border-b border-gray-100"><th className="text-left py-1.5">회차</th><th>상태</th><th>변경</th><th>가격 변동</th><th>기준 변경</th><th>미확인 전환</th><th>메일</th></tr></thead>
                    <tbody>
                      {detail!.runs.map(r => (
                        <tr key={r.id} onClick={() => setRunId(r.id)} className={`cursor-pointer border-b border-gray-50 hover:bg-teal-50/40 ${runId === r.id ? 'bg-teal-50/60' : ''}`}>
                          <td className="py-1.5">{kst(r.due_at)}</td>
                          <td className="text-center">{RUN_STATUS_KO[r.status]}</td>
                          <td className="text-center">{r.highlight_count ? <span className="rounded-full bg-rose-100 px-2 text-rose-700 font-bold">{r.highlight_count}</span> : (r.totals ? '0' : '-')}</td>
                          <td className="text-center">{r.totals?.price ?? '-'}</td>
                          <td className="text-center">{r.totals?.basis ?? '-'}</td>
                          <td className="text-center">{r.totals?.lost ?? '-'}</td>
                          <td className="text-center">{r.mail_status === 'sent' ? '발송' : r.mail_status === 'uncertain' ? '확인 필요' : r.status === 'ready' || r.status === 'mailing' ? '대기' : '-'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}

          {!form && runId && <RunDetail key={runId} runId={runId} onClose={() => setRunId('')} />}
        </div>
      </div>
    </div>
  );
}
