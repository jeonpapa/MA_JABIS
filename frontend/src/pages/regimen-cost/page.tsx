import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import {
  Bar, BarChart, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { searchDomesticPriceChanges, DomesticProduct } from '@/api/domestic';
import {
  listRegimens, createRegimen, updateRegimen, deleteRegimen,
  wapSearch, oncoSearch, oncoGet, customRegimenGet, saveCustomRegimen, oncoCost, drugDosing, saveDrugDosing, exportRegimenXlsx,
  WapResult, OncoDrug, OncoRegimenHit, Patient, PATIENT_DEFAULT,
  Regimen, RegimenComparison, RegimenPayload, PriceSource,
} from '@/api/regimenCost';

const MAX_REGIMENS = 6;
const COLORS = ['#00857c', '#1f6fb2', '#c2780c', '#6a4ea3', '#0f9d58', '#d23f57'];
const UNITS = ['mg/m2', 'mg/m2/day', 'mg/kg', 'mg/kg/day', 'AUC', 'mg', 'g/m2', '정', 'unit', 'mcg'];
const DOSE_SRC_LABEL: Record<string, string> = {
  saved: '저장됨', onco_db: '레지멘DB', mfds_label: '허가사항', manual: '수동', none: '미입력', loading: '조회중',
};

type Metric = 'daily' | 'monthly' | 'yearly';
const METRIC_LABEL: Record<Metric, string> = { daily: '일', monthly: '월', yearly: '연' };

const fmt = (n: number | null | undefined) => n == null ? '—' : '₩' + Math.round(n).toLocaleString();
const today = () => new Date().toISOString().slice(0, 10);
const emptyRegimen = (name: string): Regimen => ({ name, kind: 'onco', drugs: [], oncoDrugs: [] });
const extractInn = (s: string) => (s || '').match(/[A-Za-z][A-Za-z-]+/)?.[0] || s;
const num = (s: string) => s.trim() === '' ? null : Number(s);

function patientMetricsLocal(p: Patient) {
  const bsa = Math.sqrt(p.height * p.weight / 3600);
  const k = p.sex === 'F' ? 0.85 : 1.0;
  const crcl = ((140 - p.age) * p.weight * k) / (72 * p.scr);
  return { bsa: bsa.toFixed(3), crcl: crcl.toFixed(1), gfr: Math.min(crcl, 125).toFixed(1) };
}

export default function RegimenCostPage() {
  const [regimens, setRegimens] = useState<Regimen[]>([emptyRegimen('기준 레지멘'), emptyRegimen('비교 레지멘 1')]);
  const [metric, setMetric] = useState<Metric>('monthly');
  const [asOfDate, setAsOfDate] = useState(today());
  const [source, setSource] = useState<PriceSource>('weighted_avg');
  const [patient, setPatient] = useState<Patient>(PATIENT_DEFAULT);
  const [patientOpen, setPatientOpen] = useState(false);

  const [addTarget, setAddTarget] = useState<number | null>(null);
  const [addMode, setAddMode] = useState<'onco' | 'drug'>('onco');
  const [query, setQuery] = useState('');
  const [oncoHits, setOncoHits] = useState<OncoRegimenHit[]>([]);
  const [results, setResults] = useState<DomesticProduct[]>([]);
  const [wapResults, setWapResults] = useState<WapResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [busyCalc, setBusyCalc] = useState(false);

  const [saved, setSaved] = useState<RegimenComparison[]>([]);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [compareName, setCompareName] = useState('새 비교');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const ref = useRef({ regimens, asOfDate, source, patient });
  ref.current = { regimens, asOfDate, source, patient };
  const uidRef = useRef(1);

  useEffect(() => { listRegimens().then(setSaved).catch(() => {}); }, []);

  // 검색
  useEffect(() => {
    if (addTarget == null || !query.trim()) { setOncoHits([]); setResults([]); setWapResults([]); return; }
    const t = setTimeout(async () => {
      setSearching(true);
      try {
        if (addMode === 'onco') setOncoHits(await oncoSearch(query.trim()));
        else if (source === 'weighted_avg') {
          const r = await wapSearch(query.trim(), asOfDate);
          setWapResults(r.available ? (r.results || []).slice(0, 25) : []);
          if (!r.available) setMsg(r.reason || '가중평균 조회 불가');
        } else setResults((await searchDomesticPriceChanges(query)).slice(0, 12));
      } catch { setOncoHits([]); setResults([]); setWapResults([]); }
      finally { setSearching(false); }
    }, 350);
    return () => clearTimeout(t);
  }, [query, addTarget, addMode, source, asOfDate]);

  // 레지멘 1건 재계산 (행별 price_source/ref/inn 그대로 전달)
  const recompute = useCallback(async (ri: number, rows?: OncoDrug[]) => {
    const cur = ref.current;
    const list = rows ?? cur.regimens[ri]?.oncoDrugs ?? [];
    if (!list.length) {
      setRegimens(p => p.map((r, i) => i === ri ? { ...r, oncoTotals: undefined } : r));
      return;
    }
    try {
      const res = await oncoCost(cur.asOfDate, cur.source, cur.patient, list);
      setRegimens(p => p.map((r, i) => i !== ri ? r : {
        ...r, oncoDrugs: res.drugs.map((d, j) => ({ ...list[j], ...d })), metrics: res.metrics, oncoTotals: res.totals,
      }));
    } catch { setMsg('치료비 계산 실패 — 다시 시도해 주세요'); }
  }, []);

  const recomputeAll = useCallback(async () => {
    setBusyCalc(true);
    try { for (let i = 0; i < ref.current.regimens.length; i++) await recompute(i); }
    finally { setBusyCalc(false); }
  }, [recompute]);

  const firstMount = useRef(true);
  useEffect(() => {
    if (firstMount.current) { firstMount.current = false; return; }
    const t = setTimeout(() => recomputeAll(), 400);
    return () => clearTimeout(t);
  }, [asOfDate, source, patient, recomputeAll]);

  const appendRows = (ri: number, rows: OncoDrug[]) => {
    const withUid = rows.map(r => ({ ...r, uid: r.uid ?? uidRef.current++ }));
    const newList = [...(ref.current.regimens[ri]?.oncoDrugs || []), ...withUid];
    setRegimens(p => p.map((r, i) => i === ri ? { ...r, oncoDrugs: newList, saved: false } : r));
    setAddTarget(null); setQuery(''); setOncoHits([]); setResults([]); setWapResults([]);
    setTimeout(() => recompute(ri, newList), 0);  // 명시적 리스트 전달(ref 타이밍 비의존)
    return withUid;
  };

  // 레지멘 로드 = 행 추가(대체 아님). 커스텀/정본 모두 지원
  const addRegimen = async (hit: OncoRegimenHit, ri: number) => {
    setBusyCalc(true);
    try {
      const isCustom = hit.source_kind === 'custom';
      const full = isCustom ? await customRegimenGet(hit.ref) : await oncoGet(hit.ref);
      const rows: OncoDrug[] = full.drugs.map(d => ({
        ...d,
        dose_source: isCustom ? (d.dose_source || 'saved') : 'onco_db',
        // 커스텀은 저장된 가격조회 정보 보존(한글 브랜드 등), 정본은 INN=성분 기본
        price_source: d.price_source || ref.current.source,
        price_ref: d.price_ref || '',
        price_inn: d.price_inn || d.ingredient,
      }));
      const empty = !(ref.current.regimens[ri]?.oncoDrugs?.length);
      if (empty) setRegimens(p => p.map((r, i) => i === ri ? { ...r, name: hit.regimen_name } : r));
      appendRows(ri, rows);
    } finally { setBusyCalc(false); }
  };

  // 단일 약제 추가 (WAP/브랜드) — 행 즉시 표시(낙관적) 후 용법 비동기 채움(실패해도 행 유지)
  const addDrug = async (ri: number, opts: { inn: string; display: string; price_source: PriceSource; price_ref: string }) => {
    const [row] = appendRows(ri, [{
      ingredient: opts.display, dose_value: null, unit: 'mg', per_cycle: 1, cycle_days: 1, total_cycles: null,
      dose_days: null, cycle_label: null, route: null,
      price_source: opts.price_source, price_ref: opts.price_ref, price_inn: opts.inn, dose_source: 'loading',
    }]);
    setBusyCalc(true);
    try {
      const dose = await drugDosing(opts.inn, opts.display);
      setRegimens(p => p.map((r, i) => i !== ri ? r : {
        ...r, oncoDrugs: (r.oncoDrugs || []).map(d => d.uid === row.uid ? {
          ...d, ...dose, uid: row.uid, ingredient: opts.display,
          price_source: opts.price_source, price_ref: opts.price_ref, price_inn: opts.inn,
        } : d),
      }));
    } catch { /* 용법 미상 — 빈 행 유지(사용자 수동 입력) */ }
    finally { setBusyCalc(false); recompute(ri); }
  };

  const editRow = (ri: number, di: number, patch: Partial<OncoDrug>) =>
    setRegimens(p => p.map((r, i) => i !== ri ? r : {
      ...r, saved: false, oncoDrugs: (r.oncoDrugs || []).map((d, j) => j === di ? { ...d, ...patch, dose_source: 'manual' } : d),
    }));

  // 편집 확정 → 재계산 + 영구 저장(다음 추가 시 자동 표출)
  const commitRow = (ri: number, di: number) => {
    recompute(ri);
    const d = ref.current.regimens[ri]?.oncoDrugs?.[di];
    if (d && (d.price_inn || d.ingredient)) {
      saveDrugDosing(d.price_inn || extractInn(d.ingredient), {
        ingredient: d.ingredient, dose_value: d.dose_value, unit: d.unit, dose_days: d.dose_days,
        per_cycle: d.per_cycle, cycle_days: d.cycle_days, cycle_label: d.cycle_label,
        total_cycles: d.total_cycles, route: d.route,
      }).catch(() => {});
    }
  };
  const removeRow = (ri: number, di: number) => {
    setRegimens(p => p.map((r, i) => i !== ri ? r : { ...r, saved: false, oncoDrugs: (r.oncoDrugs || []).filter((_, j) => j !== di) }));
    setTimeout(() => recompute(ri), 0);
  };

  const renameRegimen = (ri: number, name: string) => setRegimens(p => p.map((r, i) => i === ri ? { ...r, name, saved: false } : r));
  // 현재 레지멘(약제 조합+이름)을 영구 라이브러리에 저장 → 다음 '레지멘' 검색에 노출(공유)
  const saveAsRegimen = async (ri: number) => {
    const r = ref.current.regimens[ri];
    if (!r?.oncoDrugs?.length) { setMsg('저장할 약제가 없습니다'); return; }
    try {
      await saveCustomRegimen(r.name, r.oncoDrugs);
      setRegimens(p => p.map((rg, i) => i === ri ? { ...rg, saved: true } : rg));
      setMsg(`'${r.name}' 레지멘 저장됨 — 다음 레지멘 검색에서 불러올 수 있어요`);
    } catch { setMsg('레지멘 저장 실패 (로그인 필요)'); }
  };
  const addBlank = () => regimens.length < MAX_REGIMENS && setRegimens(p => [...p, emptyRegimen(`비교 레지멘 ${p.length}`)]);
  const removeRegimen = (ri: number) => ri > 0 && setRegimens(p => p.filter((_, i) => i !== ri));

  const chartData = useMemo(() => regimens.map((r, i) => ({
    name: r.name || `레지멘 ${i + 1}`, value: r.oncoTotals?.[metric] ?? 0, idx: i,
  })), [regimens, metric]);

  const stripSaved = ({ saved, ...r }: Regimen): Regimen => r;
  const payload = (): RegimenPayload => ({ base: stripSaved(regimens[0]), comparators: regimens.slice(1).map(stripSaved), asOfDate, source, patient });
  const onSave = async () => {
    setBusy(true); setMsg('');
    try {
      if (currentId) { await updateRegimen(currentId, compareName, payload()); setMsg('저장됨'); }
      else { const c = await createRegimen(compareName, payload()); setCurrentId(c.id); setMsg('새 비교 저장됨'); }
      setSaved(await listRegimens());
    } catch { setMsg('저장 실패 (로그인 필요)'); } finally { setBusy(false); }
  };
  const onLoad = (c: RegimenComparison) => {
    setCurrentId(c.id); setCompareName(c.name);
    setRegimens([c.payload.base, ...(c.payload.comparators || [])].filter(Boolean).map(r => ({ ...r, oncoDrugs: r.oncoDrugs || [] })));
    setAsOfDate(c.payload.asOfDate || c.payload.snapshotDate || today());
    if (c.payload.source) setSource(c.payload.source);
    if (c.payload.patient) setPatient(c.payload.patient);
    setMsg(`'${c.name}' 불러옴`);
  };
  const onDelete = async (id: number) => { await deleteRegimen(id); setSaved(await listRegimens()); if (currentId === id) { setCurrentId(null); setMsg('삭제됨'); } };
  const onNew = () => { setCurrentId(null); setCompareName('새 비교'); setRegimens([emptyRegimen('기준 레지멘'), emptyRegimen('비교 레지멘 1')]); };
  const onExport = async () => {
    if (!regimens.some(r => (r.oncoDrugs || []).length)) { setMsg('내보낼 약제가 없습니다'); return; }
    try { await exportRegimenXlsx(asOfDate, source, patient, regimens); setMsg('엑셀 다운로드 완료 (수식 포함)'); }
    catch (e) { setMsg(String((e as Error).message || '다운로드 실패')); }
  };

  const setP = (k: keyof Patient, v: string) => setPatient(p => ({ ...p, [k]: k === 'sex' ? (v as 'M' | 'F') : Number(v) }));
  const pm = patientMetricsLocal(patient);

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 p-6">
      <div className="max-w-[1500px] mx-auto">
        <div className="flex items-center justify-between mb-1">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2"><i className="ri-bar-chart-box-line text-teal-600"></i>투약 비용 비교</h1>
            <p className="text-sm text-gray-500 mt-1">레지멘(정본 DB)으로 기본 구성 후 자유 편집 · 환자 파라미터 기반 용량 + 기준일 약가로 치료비 비교.</p>
          </div>
          <div className="flex items-center gap-2">
            <input value={compareName} onChange={e => setCompareName(e.target.value)} className="border rounded-lg px-3 py-1.5 text-sm w-40" placeholder="비교 이름" />
            <button onClick={onSave} disabled={busy} className="bg-teal-600 text-white text-sm px-4 py-1.5 rounded-lg hover:bg-teal-700 disabled:opacity-50">{currentId ? '업데이트' : '저장'}</button>
            <button onClick={onNew} className="border text-sm px-3 py-1.5 rounded-lg hover:bg-gray-100">새로</button>
            <button onClick={onExport} title="수식이 살아있는 엑셀로 다운로드"
              className="inline-flex items-center gap-1 border text-sm px-3 py-1.5 rounded-lg hover:bg-gray-100"><i className="ri-file-excel-2-line text-green-600"></i>엑셀</button>
          </div>
        </div>
        {msg && <p className="text-xs text-teal-700 mb-2">{msg}</p>}
        {saved.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap mb-4 text-xs">
            <span className="text-gray-500">저장됨:</span>
            {saved.map(c => (
              <span key={c.id} className={`inline-flex items-center gap-1 border rounded-full px-2.5 py-1 ${currentId === c.id ? 'bg-teal-50 border-teal-300' : 'bg-white'}`}>
                <button onClick={() => onLoad(c)} className="hover:text-teal-700">{c.name}</button>
                <button onClick={() => onDelete(c.id)} className="text-gray-400 hover:text-red-500"><i className="ri-close-line"></i></button>
              </span>
            ))}
          </div>
        )}

        {/* 차트 */}
        <div className="bg-white rounded-2xl border p-5 mb-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-bold text-sm">레지멘 치료비 비교 ({METRIC_LABEL[metric]})</h2>
            <div className="flex gap-1 bg-gray-100 rounded-lg p-1">
              {(['daily', 'monthly', 'yearly'] as Metric[]).map(mm => (
                <button key={mm} onClick={() => setMetric(mm)} className={`text-xs px-3 py-1 rounded-md ${metric === mm ? 'bg-white shadow font-semibold text-teal-700' : 'text-gray-500'}`}>{METRIC_LABEL[mm]} 치료비</button>
              ))}
            </div>
          </div>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={chartData} margin={{ top: 24, right: 16, left: 8, bottom: 5 }}>
              <XAxis dataKey="name" tick={{ fontSize: 12 }} /><YAxis tickFormatter={v => `${Math.round(v / 10000)}만`} tick={{ fontSize: 11 }} />
              <Tooltip formatter={(v: number) => fmt(v)} />
              <Bar dataKey="value" radius={[8, 8, 0, 0]} maxBarSize={70}>
                {chartData.map(d => <Cell key={d.idx} fill={COLORS[d.idx % COLORS.length]} />)}
                <LabelList dataKey="value" position="top" formatter={(v: number) => fmt(v)} style={{ fontSize: 11, fontWeight: 700 }} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* 레지멘별 비용 비교 테이블 */}
        <div className="bg-white rounded-2xl border p-5 mb-4">
          <h2 className="font-bold text-sm mb-3">레지멘별 비용 비교</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="text-gray-400 border-b text-xs">
                <th className="text-left font-medium py-1.5">레지멘</th>
                <th className="text-right font-medium px-3 whitespace-nowrap">1사이클</th>
                <th className="text-right font-medium px-3 whitespace-nowrap">전체코스</th>
                <th className="text-right font-medium px-3">일</th>
                <th className="text-right font-medium px-3">월</th>
                <th className="text-right font-medium px-3">연</th>
              </tr></thead>
              <tbody>
                {regimens.map((r, i) => {
                  const t = r.oncoTotals;
                  const hasCycle = (r.oncoDrugs || []).some(d => (d.cycle_days ?? 1) > 1);
                  return (
                    <tr key={i} className="border-b last:border-0">
                      <td className="py-2 whitespace-nowrap">
                        <span className="inline-block w-2.5 h-2.5 rounded-full mr-2 align-middle" style={{ background: COLORS[i % COLORS.length] }}></span>
                        {r.name || `레지멘 ${i + 1}`}
                        {i === 0 && <span className="ml-1.5 text-[10px] px-1 py-0.5 rounded bg-teal-100 text-teal-700">기준</span>}
                        {t?.hasMissing && <span className="ml-1 text-amber-500" title="일부 약가/용량 미상 (합산 제외)">⚠</span>}
                      </td>
                      <td className="text-right px-3 whitespace-nowrap">{hasCycle ? fmt(t?.cycle) : '—'}</td>
                      <td className="text-right px-3 whitespace-nowrap font-bold text-teal-700">{hasCycle ? fmt(t?.course) : '—'}</td>
                      <td className="text-right px-3 whitespace-nowrap">{fmt(t?.daily)}</td>
                      <td className="text-right px-3 whitespace-nowrap font-semibold">{fmt(t?.monthly)}</td>
                      <td className="text-right px-3 whitespace-nowrap font-semibold">{fmt(t?.yearly)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-gray-400 mt-2">1사이클·전체코스는 항암 레지멘(주기 투여)에만 표시 · 일반약제는 일/월/연만</p>
        </div>

        {/* 컨트롤 (테이블 인접) */}
        <div className="bg-white rounded-2xl border px-5 py-3 mb-3 flex items-center gap-5 flex-wrap">
          <label className="flex items-center gap-2 text-sm"><i className="ri-calendar-event-line text-teal-600"></i><span className="text-gray-600">기준 시점</span>
            <input type="date" value={asOfDate} max={today()} onChange={e => setAsOfDate(e.target.value)} className="border rounded-lg px-2.5 py-1 text-sm" />
          </label>
          <div className="flex items-center gap-2 text-sm"><span className="text-gray-600">가격 소스</span>
            <div className="flex gap-1 bg-gray-100 rounded-lg p-1">
              {([['weighted_avg', '주성분 가중평균'], ['domestic', '국내약가(브랜드)']] as [PriceSource, string][]).map(([s, lbl]) => (
                <button key={s} onClick={() => setSource(s)} className={`text-xs px-3 py-1 rounded-md ${source === s ? 'bg-white shadow font-semibold text-teal-700' : 'text-gray-500'}`}>{lbl}</button>
              ))}
            </div>
          </div>
          <button onClick={() => setPatientOpen(o => !o)} className="flex items-center gap-1.5 text-sm text-gray-600 hover:text-teal-700">
            <i className="ri-user-heart-line text-teal-600"></i>환자값 <span className="text-xs text-gray-400">BSA {pm.bsa} · GFR {pm.gfr}</span><i className={`ri-arrow-${patientOpen ? 'up' : 'down'}-s-line`}></i>
          </button>
          {busyCalc && <span className="text-xs text-gray-400">계산 중…</span>}
          <span className="text-[11px] text-gray-400 ml-auto">표시가 기준 (실거래·RSA net 비공개)</span>
          {patientOpen && (
            <div className="w-full border-t pt-3 mt-1 flex items-end gap-4 flex-wrap text-xs">
              {([['height', '키(cm)'], ['weight', '체중(kg)'], ['age', '나이'], ['scr', 'SCr(mg/dL)']] as [keyof Patient, string][]).map(([k, lbl]) => (
                <label key={k} className="flex flex-col gap-1"><span className="text-gray-500">{lbl}</span>
                  <input value={String(patient[k])} onChange={e => setP(k, e.target.value)} inputMode="decimal" className="border rounded-md px-2 py-1 w-24" /></label>
              ))}
              <label className="flex flex-col gap-1"><span className="text-gray-500">성별</span>
                <select value={patient.sex} onChange={e => setP('sex', e.target.value)} className="border rounded-md px-2 py-1 w-24"><option>M</option><option>F</option></select></label>
              <div className="text-gray-500 pb-1">→ BSA <b className="text-gray-700">{pm.bsa}</b> m² · CrCl {pm.crcl} · GFR(cap125) <b className="text-gray-700">{pm.gfr}</b></div>
              <button onClick={() => setPatient(PATIENT_DEFAULT)} className="text-gray-400 hover:text-gray-600 pb-1">기본값</button>
            </div>
          )}
        </div>

        {/* 레지멘 목록 */}
        <div className="space-y-3">
          {regimens.map((r, ri) => {
            const rows = r.oncoDrugs || [];
            return (
              <div key={ri} className="bg-white rounded-2xl border p-4" style={{ borderLeftColor: COLORS[ri % COLORS.length], borderLeftWidth: 4 }}>
                <div className="flex items-center gap-2 mb-2">
                  {ri === 0 && <span className="text-[10px] px-1.5 py-0.5 rounded bg-teal-100 text-teal-700 font-semibold">기준</span>}
                  <input value={r.name} onChange={e => renameRegimen(ri, e.target.value)} className="font-semibold text-sm flex-1 min-w-0 bg-transparent outline-none border-b border-transparent focus:border-gray-300" />
                  {r.metrics && <span className="text-[11px] text-gray-400">BSA {r.metrics.bsa} · GFR {r.metrics.gfr}</span>}
                  {rows.length > 0 && (
                    <button onClick={() => saveAsRegimen(ri)}
                      title={r.saved ? '저장됨 — 편집 시 다시 저장하세요' : '이 레지멘을 라이브러리에 저장(다음 검색에 노출)'}
                      className={`inline-flex items-center gap-1 text-[11px] border rounded-md px-1.5 py-0.5 ${r.saved ? 'text-teal-600 border-teal-300 bg-teal-50' : 'text-gray-400 hover:text-teal-600'}`}>
                      <i className={r.saved ? 'ri-bookmark-fill' : 'ri-bookmark-line'}></i>{r.saved ? '저장됨' : '레지멘 저장'}</button>
                  )}
                  {ri > 0 && <button onClick={() => removeRegimen(ri)} className="text-gray-300 hover:text-red-500"><i className="ri-delete-bin-line"></i></button>}
                </div>

                {rows.length > 0 && (
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs border-collapse">
                      <thead><tr className="text-gray-400 border-b">
                        {['약제', '용량값', '단위', '투여일', '회수/주기', '주기(일)', '총사이클', '1회(mg)', '주기총량(mg)', '사이클₩', '코스₩', '월₩', '출처', ''].map(h => <th key={h} className="text-left font-medium px-1.5 py-1 whitespace-nowrap">{h}</th>)}
                      </tr></thead>
                      <tbody>
                        {rows.map((d, di) => (
                          <tr key={d.uid ?? di} className="border-b last:border-0">
                            <td className="px-1.5 py-1 font-medium whitespace-nowrap max-w-[180px] truncate" title={`${d.ingredient}${d.price?.label ? ` · ${d.price.label}` : ''}`}>
                              {d.ingredient}
                              {d.verify === '검증필요' && <span title="검증필요" className="ml-1 text-amber-500">⚠</span>}
                              {d.price && !d.price.available && <span title={d.price.reason} className="ml-1 text-red-400">●</span>}
                            </td>
                            <td className="px-1"><input value={d.dose_value ?? ''} onChange={e => editRow(ri, di, { dose_value: num(e.target.value) })} onBlur={() => commitRow(ri, di)} className="w-14 border rounded px-1 py-0.5 text-right" /></td>
                            <td className="px-1"><select value={d.unit ?? ''} onChange={e => { editRow(ri, di, { unit: e.target.value }); setTimeout(() => commitRow(ri, di), 0); }} className="border rounded px-1 py-0.5">{UNITS.map(u => <option key={u}>{u}</option>)}{d.unit && !UNITS.includes(d.unit) && <option>{d.unit}</option>}</select></td>
                            <td className="px-1.5 text-gray-500 whitespace-nowrap">{d.dose_days || '—'}</td>
                            <td className="px-1"><input value={d.per_cycle ?? ''} onChange={e => editRow(ri, di, { per_cycle: num(e.target.value) })} onBlur={() => commitRow(ri, di)} className="w-12 border rounded px-1 py-0.5 text-right" /></td>
                            <td className="px-1"><input value={d.cycle_days ?? ''} onChange={e => editRow(ri, di, { cycle_days: num(e.target.value) })} onBlur={() => commitRow(ri, di)} className="w-12 border rounded px-1 py-0.5 text-right" /></td>
                            <td className="px-1"><input value={d.total_cycles ?? ''} onChange={e => editRow(ri, di, { total_cycles: num(e.target.value) })} onBlur={() => commitRow(ri, di)} className="w-12 border rounded px-1 py-0.5 text-right" /></td>
                            <td className="px-1.5 text-right bg-teal-50/60 font-medium">{d.one_dose_mg ?? '—'}</td>
                            <td className="px-1.5 text-right bg-teal-50/60 font-medium">{d.cycle_total_mg ?? '—'}</td>
                            <td className="px-1.5 text-right whitespace-nowrap" title={d.price?.label ? `${d.price.label} · ${fmt(d.price.unit_price)}/단위` : d.price?.reason || ''}>{fmt(d.cost?.cycle)}</td>
                            <td className="px-1.5 text-right whitespace-nowrap text-gray-500">{fmt(d.cost?.course)}</td>
                            <td className="px-1.5 text-right whitespace-nowrap text-gray-500">{fmt(d.cost?.monthly)}</td>
                            <td className="px-1.5 whitespace-nowrap">
                              <span className={`text-[10px] px-1 py-0.5 rounded ${d.dose_source === 'saved' ? 'bg-teal-500/15 text-teal-600' : d.dose_source === 'onco_db' ? 'bg-indigo-500/15 text-indigo-600' : d.dose_source === 'mfds_label' ? 'bg-blue-500/15 text-blue-600' : d.dose_source === 'manual' ? 'bg-amber-500/15 text-amber-600' : 'bg-gray-200 text-gray-400'}`}>{DOSE_SRC_LABEL[d.dose_source || 'none']}</span>
                              <span className="ml-1 text-[10px] text-gray-400">{d.price_source === 'domestic' ? '브랜드' : 'WAP'}</span>
                            </td>
                            <td className="px-1"><button onClick={() => removeRow(ri, di)} className="text-gray-300 hover:text-red-500"><i className="ri-close-line"></i></button></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                <div className="mt-2">
                  <div className="relative">
                    {addTarget === ri ? (
                      <div className="flex items-start gap-2">
                        <div className="flex gap-1 bg-gray-100 rounded-lg p-0.5 text-xs">
                          <button onClick={() => setAddMode('onco')} className={`px-2 py-1 rounded ${addMode === 'onco' ? 'bg-white shadow font-semibold text-teal-700' : 'text-gray-500'}`}>레지멘</button>
                          <button onClick={() => setAddMode('drug')} className={`px-2 py-1 rounded ${addMode === 'drug' ? 'bg-white shadow font-semibold text-teal-700' : 'text-gray-500'}`}>약제</button>
                        </div>
                        <input autoFocus value={query} onChange={e => setQuery(e.target.value)}
                          placeholder={addMode === 'onco' ? '암종·레지멘·약제 (예: 소세포, EP)' : source === 'weighted_avg' ? '영문 성분명' : '제품·성분'}
                          className="text-xs border rounded-lg px-2 py-1.5 w-72 outline-none focus:border-teal-400" />
                        <button onClick={() => { setAddTarget(null); setQuery(''); }} className="text-xs text-gray-400 pt-1.5">닫기</button>
                        {query.trim() && (searching || oncoHits.length > 0 || results.length > 0 || wapResults.length > 0) && (
                          <div className="absolute z-10 top-9 left-0 w-[28rem] bg-white border rounded-lg shadow-lg max-h-72 overflow-auto">
                            {searching && <p className="text-xs text-gray-400 px-3 py-2">검색 중…</p>}
                            {addMode === 'onco' && oncoHits.map(h => (
                              <button key={h.ref} onClick={() => addRegimen(h, ri)} className="block w-full text-left text-xs px-3 py-1.5 hover:bg-teal-50">
                                <span className="font-medium">{h.regimen_name}</span><span className="text-gray-400"> · {h.cancer}{h.line ? ` · ${h.line}` : ''}</span>
                                <span className="block text-gray-400 truncate">{h.drug_names.join(' + ')}</span>
                              </button>
                            ))}
                            {addMode === 'drug' && source === 'weighted_avg' && wapResults.map(w => (
                              <button key={w.main_ingredient_code} onClick={() => addDrug(ri, { inn: extractInn(w.ingredient_name), display: extractInn(w.ingredient_name), price_source: 'weighted_avg', price_ref: w.main_ingredient_code })} className="block w-full text-left text-xs px-3 py-1.5 hover:bg-indigo-50">
                                <span className="font-medium">{fmt(w.weighted_avg_price)}</span><span className="text-gray-400"> · {w.main_ingredient_code}</span><span className="block text-gray-400 truncate">{w.ingredient_name}</span>
                              </button>
                            ))}
                            {addMode === 'drug' && source === 'domestic' && results.map(p => (
                              <button key={p.insuranceCode} onClick={() => addDrug(ri, { inn: p.hiraIngredient || p.ingredient, display: p.productName, price_source: 'domestic', price_ref: p.insuranceCode })} className="block w-full text-left text-xs px-3 py-1.5 hover:bg-teal-50">
                                <span className="font-medium">{p.productName}</span><span className="text-gray-400"> · {fmt(p.currentPrice)}</span><span className="block text-gray-400 truncate">{p.ingredient}</span>
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    ) : (
                      <button onClick={() => { setAddTarget(ri); setAddMode('onco'); setQuery(''); }} className="inline-flex items-center gap-1 border border-dashed rounded-lg px-3 py-1.5 text-xs text-gray-500 hover:border-teal-400 hover:text-teal-600"><i className="ri-add-line"></i> 레지멘/약제 추가</button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}

          {regimens.length < MAX_REGIMENS && (
            <button onClick={addBlank} className="w-full bg-white rounded-2xl border border-dashed py-3 text-gray-400 hover:border-teal-400 hover:text-teal-600 text-sm"><i className="ri-add-line"></i> 비교 레지멘 추가</button>
          )}
        </div>
      </div>
    </div>
  );
}
