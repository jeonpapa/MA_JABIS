import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import {
  Bar, BarChart, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { searchDomesticPriceChanges, DomesticProduct } from '@/api/domestic';
import {
  listRegimens, createRegimen, updateRegimen, deleteRegimen, regimenTotals,
  wapSearch, priceAsOf, oncoSearch, oncoGet, oncoCost,
  WapResult, PriceAsOfItem, OncoDrug, OncoRegimenHit, Patient, PATIENT_DEFAULT,
  Regimen, RegimenDrug, RegimenComparison, RegimenPayload, PriceSource,
} from '@/api/regimenCost';

const MAX_REGIMENS = 6;
const MAX_DRUGS = 5;
const COLORS = ['#00857c', '#1f6fb2', '#c2780c', '#6a4ea3', '#0f9d58', '#d23f57'];
const UNITS = ['mg/m2', 'mg/m2/day', 'mg/kg', 'mg/kg/day', 'AUC', 'mg', 'g/m2', 'unit', 'mcg'];

type Metric = 'daily' | 'monthly' | 'yearly';
const METRIC_LABEL: Record<Metric, string> = { daily: '일', monthly: '월', yearly: '연' };
const COST_KEY: Record<Metric, 'daily' | 'monthly' | 'yearly'> = { daily: 'daily', monthly: 'monthly', yearly: 'yearly' };

const fmt = (n: number | null | undefined) => n == null ? '—' : '₩' + Math.round(n).toLocaleString();
const today = () => new Date().toISOString().slice(0, 10);
const emptyRegimen = (name: string): Regimen => ({ name, kind: 'manual', drugs: [] });
const drugKey = (d: RegimenDrug) => d.source === 'weighted_avg' ? 'wap:' + (d.mainIngredientCode || '') : 'dom:' + d.insuranceCode;
const drugToItem = (d: RegimenDrug): PriceAsOfItem => ({
  ...(d.source === 'weighted_avg'
    ? { source: 'weighted_avg' as const, mainIngredientCode: d.mainIngredientCode, ingredientName: d.ingredient }
    : { source: 'domestic' as const, insuranceCode: d.insuranceCode, normalizedName: d.normalizedName, productName: d.name, ingredient: d.ingredient }),
  doseOverride: d.doseOverride,
});

// onco 레지멘 합계(선택 지표)
function oncoTotal(drugs: OncoDrug[] | undefined, key: 'cycle' | 'course' | 'daily' | 'monthly' | 'yearly') {
  let s = 0, missing = false;
  for (const d of drugs || []) {
    const v = d.cost?.[key]; if (v == null) missing = true; else s += v;
  }
  return { sum: s, missing };
}

export default function RegimenCostPage() {
  const [regimens, setRegimens] = useState<Regimen[]>([emptyRegimen('기준 레지멘'), emptyRegimen('비교 레지멘 1')]);
  const [metric, setMetric] = useState<Metric>('monthly');
  const [asOfDate, setAsOfDate] = useState(today());
  const [source, setSource] = useState<PriceSource>('weighted_avg');
  const [patient, setPatient] = useState<Patient>(PATIENT_DEFAULT);
  const [patientOpen, setPatientOpen] = useState(false);

  // 추가 검색
  const [addTarget, setAddTarget] = useState<number | null>(null);
  const [addMode, setAddMode] = useState<'onco' | 'manual'>('onco');
  const [query, setQuery] = useState('');
  const [oncoHits, setOncoHits] = useState<OncoRegimenHit[]>([]);
  const [results, setResults] = useState<DomesticProduct[]>([]);
  const [wapResults, setWapResults] = useState<WapResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [busyCalc, setBusyCalc] = useState(false);

  // 저장
  const [saved, setSaved] = useState<RegimenComparison[]>([]);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [compareName, setCompareName] = useState('새 비교');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const stateRef = useRef({ regimens, asOfDate, source, patient });
  stateRef.current = { regimens, asOfDate, source, patient };

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

  // onco 레지멘 1건 재계산
  const recomputeOnco = useCallback(async (ri: number, drugs: OncoDrug[]) => {
    const { asOfDate, source, patient } = stateRef.current;
    const raw = drugs.map(d => ({
      ingredient: d.ingredient, dose_value: d.dose_value, unit: d.unit, per_cycle: d.per_cycle,
      cycle_days: d.cycle_days, total_cycles: d.total_cycles, route: d.route, note: d.note, verify: d.verify,
    }));
    const res = await oncoCost(asOfDate, source, patient, raw as OncoDrug[]);
    setRegimens(prev => prev.map((r, i) => i !== ri ? r : {
      ...r, oncoDrugs: res.drugs, metrics: res.metrics, oncoTotals: res.totals,
    }));
  }, []);

  // 전체 재계산 (날짜·소스·환자 변경 시)
  const recomputeAll = useCallback(async () => {
    const { regimens } = stateRef.current;
    setBusyCalc(true);
    try {
      for (let i = 0; i < regimens.length; i++) {
        const r = regimens[i];
        if (r.kind === 'onco' && r.oncoDrugs?.length) await recomputeOnco(i, r.oncoDrugs);
        else if (r.drugs.length) await repriceManual(i);
      }
    } finally { setBusyCalc(false); }
  }, [recomputeOnco]);

  const firstMount = useRef(true);
  useEffect(() => {
    if (firstMount.current) { firstMount.current = false; return; }
    const t = setTimeout(() => { recomputeAll(); }, 400);
    return () => clearTimeout(t);
  }, [asOfDate, source, patient, recomputeAll]);

  // manual 재가격
  const repriceManual = async (ri: number) => {
    const r = stateRef.current.regimens[ri]; if (!r?.drugs.length) return;
    const res = await priceAsOf(stateRef.current.asOfDate, r.drugs.map(drugToItem));
    setRegimens(prev => prev.map((rg, i) => i !== ri ? rg : {
      ...rg, drugs: rg.drugs.map((d, di) => {
        const x = res[di]; if (!x) return d;
        return { ...d, currentPrice: x.price, dailyCost: x.dailyCost, monthlyCost: x.monthlyCost,
                 yearlyCost: x.yearlyCost, priceDate: x.priceDate, available: x.available, doseInfo: x.doseInfo ?? d.doseInfo };
      }),
    }));
  };

  // onco 레지멘 추가
  const addOncoRegimen = async (hit: OncoRegimenHit, ri: number) => {
    setBusyCalc(true);
    try {
      const full = await oncoGet(hit.ref);
      setRegimens(prev => prev.map((r, i) => i !== ri ? r : {
        ...r, kind: 'onco', name: hit.regimen_name, oncoRef: hit.ref, oncoDrugs: full.drugs, drugs: [],
      }));
      setAddTarget(null); setQuery('');
      // 계산
      setRegimens(prev => prev);
      await recomputeOnco(ri, full.drugs);
    } finally { setBusyCalc(false); }
  };

  // onco 약제 셀 편집
  const editOncoDrug = (ri: number, di: number, patch: Partial<OncoDrug>) => {
    setRegimens(prev => prev.map((r, i) => i !== ri ? r : {
      ...r, oncoDrugs: (r.oncoDrugs || []).map((d, j) => j === di ? { ...d, ...patch } : d),
    }));
  };
  const commitOncoEdit = (ri: number) => {
    const r = stateRef.current.regimens[ri];
    if (r?.oncoDrugs) recomputeOnco(ri, r.oncoDrugs);
  };
  const removeOncoDrug = (ri: number, di: number) => {
    setRegimens(prev => prev.map((r, i) => i !== ri ? r : { ...r, oncoDrugs: (r.oncoDrugs || []).filter((_, j) => j !== di) }));
    setTimeout(() => commitOncoEdit(ri), 0);
  };

  // manual 약제 추가
  const addManualDrug = async (ri: number, item: PriceAsOfItem, display: { name: string; ingredient: string; key: Partial<RegimenDrug> }) => {
    const [r] = await priceAsOf(asOfDate, [item]);
    setRegimens(prev => prev.map((rg, i) => i !== ri ? rg : {
      ...rg, kind: 'manual', drugs: [...rg.drugs, {
        insuranceCode: display.key.insuranceCode || '', name: display.name, ingredient: display.ingredient,
        currentPrice: r?.price ?? null, dailyCost: r?.dailyCost ?? null, monthlyCost: r?.monthlyCost ?? null,
        yearlyCost: r?.yearlyCost ?? null, priceDate: r?.priceDate, available: r?.available ?? true,
        doseInfo: r?.doseInfo, ...display.key,
      }],
    }));
    setAddTarget(null); setQuery(''); setResults([]); setWapResults([]);
  };

  const renameRegimen = (ri: number, name: string) => setRegimens(prev => prev.map((r, i) => i === ri ? { ...r, name } : r));
  const addRegimen = () => regimens.length < MAX_REGIMENS && setRegimens(prev => [...prev, emptyRegimen(`비교 레지멘 ${prev.length}`)]);
  const removeRegimen = (ri: number) => ri > 0 && setRegimens(prev => prev.filter((_, i) => i !== ri));

  // 차트 (선택 지표; onco=oncoTotals, manual=regimenTotals)
  const chartData = useMemo(() => regimens.map((r, i) => {
    const v = r.kind === 'onco' ? (r.oncoTotals?.[metric] ?? 0) : regimenTotals(r)[metric];
    return { name: r.name || `레지멘 ${i + 1}`, value: v, idx: i };
  }), [regimens, metric]);

  const payload = (): RegimenPayload => ({ base: regimens[0], comparators: regimens.slice(1), asOfDate, source, patient });
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
    setRegimens([c.payload.base, ...(c.payload.comparators || [])].filter(Boolean));
    setAsOfDate(c.payload.asOfDate || c.payload.snapshotDate || today());
    if (c.payload.source) setSource(c.payload.source);
    if (c.payload.patient) setPatient(c.payload.patient);
    setMsg(`'${c.name}' 불러옴`);
  };
  const onDelete = async (id: number) => { await deleteRegimen(id); setSaved(await listRegimens()); if (currentId === id) { setCurrentId(null); setMsg('삭제됨'); } };
  const onNew = () => { setCurrentId(null); setCompareName('새 비교'); setRegimens([emptyRegimen('기준 레지멘'), emptyRegimen('비교 레지멘 1')]); };

  const m = patient;
  const setP = (k: keyof Patient, v: string) => setPatient(p => ({ ...p, [k]: k === 'sex' ? (v as 'M' | 'F') : Number(v) }));

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 p-6">
      <div className="max-w-[1500px] mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-1">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2"><i className="ri-bar-chart-box-line text-teal-600"></i>투약 비용 비교</h1>
            <p className="text-sm text-gray-500 mt-1">항암 레지멘(정본 DB)·환자 파라미터 기반 용량 계산 + 기준일 약가로 사이클·월·연 치료비 비교.</p>
          </div>
          <div className="flex items-center gap-2">
            <input value={compareName} onChange={e => setCompareName(e.target.value)} className="border rounded-lg px-3 py-1.5 text-sm w-40" placeholder="비교 이름" />
            <button onClick={onSave} disabled={busy} className="bg-teal-600 text-white text-sm px-4 py-1.5 rounded-lg hover:bg-teal-700 disabled:opacity-50">{currentId ? '업데이트' : '저장'}</button>
            <button onClick={onNew} className="border text-sm px-3 py-1.5 rounded-lg hover:bg-gray-100">새로</button>
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

        {/* 컨트롤 바 (레지멘 테이블 인접) — 기준일·소스·환자 */}
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
            <i className="ri-user-heart-line text-teal-600"></i>환자값 <span className="text-xs text-gray-400">BSA {patientMetricsLocal(patient).bsa} · GFR {patientMetricsLocal(patient).gfr}</span>
            <i className={`ri-arrow-${patientOpen ? 'up' : 'down'}-s-line`}></i>
          </button>
          {busyCalc && <span className="text-xs text-gray-400">계산 중…</span>}
          <span className="text-[11px] text-gray-400 ml-auto">표시가 기준 (실거래·RSA net 비공개)</span>
          {patientOpen && (
            <div className="w-full border-t pt-3 mt-1 flex items-end gap-4 flex-wrap text-xs">
              {([['height', '키(cm)'], ['weight', '체중(kg)'], ['age', '나이'], ['scr', 'SCr(mg/dL)']] as [keyof Patient, string][]).map(([k, lbl]) => (
                <label key={k} className="flex flex-col gap-1"><span className="text-gray-500">{lbl}</span>
                  <input value={String(m[k])} onChange={e => setP(k, e.target.value)} inputMode="decimal" className="border rounded-md px-2 py-1 w-24" /></label>
              ))}
              <label className="flex flex-col gap-1"><span className="text-gray-500">성별</span>
                <select value={m.sex} onChange={e => setP('sex', e.target.value)} className="border rounded-md px-2 py-1 w-24"><option>M</option><option>F</option></select></label>
              <div className="text-gray-500 pb-1">→ BSA <b className="text-gray-700">{patientMetricsLocal(patient).bsa}</b> m² · CrCl {patientMetricsLocal(patient).crcl} · GFR(cap125) <b className="text-gray-700">{patientMetricsLocal(patient).gfr}</b></div>
              <button onClick={() => setPatient(PATIENT_DEFAULT)} className="text-gray-400 hover:text-gray-600 pb-1">기본값</button>
            </div>
          )}
        </div>

        {/* 레지멘 목록 */}
        <div className="space-y-3">
          {regimens.map((r, ri) => (
            <div key={ri} className="bg-white rounded-2xl border p-4" style={{ borderLeftColor: COLORS[ri % COLORS.length], borderLeftWidth: 4 }}>
              <div className="flex items-center gap-2 mb-2">
                {ri === 0 && <span className="text-[10px] px-1.5 py-0.5 rounded bg-teal-100 text-teal-700 font-semibold">기준</span>}
                <input value={r.name} onChange={e => renameRegimen(ri, e.target.value)} className="font-semibold text-sm flex-1 min-w-0 bg-transparent outline-none border-b border-transparent focus:border-gray-300" />
                {r.kind === 'onco' && r.metrics && <span className="text-[11px] text-gray-400">BSA {r.metrics.bsa} · GFR {r.metrics.gfr}</span>}
                {ri > 0 && <button onClick={() => removeRegimen(ri)} className="text-gray-300 hover:text-red-500"><i className="ri-delete-bin-line"></i></button>}
              </div>

              {/* onco 테이블 */}
              {r.kind === 'onco' && r.oncoDrugs && r.oncoDrugs.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs border-collapse">
                    <thead>
                      <tr className="text-gray-400 border-b">
                        {['약제성분', '용량값', '단위', '투여일', '회수/주기', '주기(일)', 'q표기', '총사이클', '1회(mg)', '주기총량(mg)', '사이클₩', '코스₩', ''].map(h => (
                          <th key={h} className="text-left font-medium px-1.5 py-1 whitespace-nowrap">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {r.oncoDrugs.map((d, di) => (
                        <tr key={di} className="border-b last:border-0">
                          <td className="px-1.5 py-1 font-medium whitespace-nowrap">{d.ingredient}
                            {d.verify === '검증필요' && <span title="검증필요" className="ml-1 text-amber-500">⚠</span>}
                            {d.price && !d.price.available && <span title={d.price.reason} className="ml-1 text-red-400">●</span>}
                          </td>
                          <td className="px-1"><input value={d.dose_value ?? ''} onChange={e => editOncoDrug(ri, di, { dose_value: e.target.value === '' ? null : Number(e.target.value) })} onBlur={() => commitOncoEdit(ri)} className="w-14 border rounded px-1 py-0.5 text-right" /></td>
                          <td className="px-1"><select value={d.unit ?? ''} onChange={e => { editOncoDrug(ri, di, { unit: e.target.value }); setTimeout(() => commitOncoEdit(ri), 0); }} className="border rounded px-1 py-0.5">{UNITS.map(u => <option key={u}>{u}</option>)}{d.unit && !UNITS.includes(d.unit) && <option>{d.unit}</option>}</select></td>
                          <td className="px-1.5 text-gray-500 whitespace-nowrap">{d.dose_days || '—'}</td>
                          <td className="px-1"><input value={d.per_cycle ?? ''} onChange={e => editOncoDrug(ri, di, { per_cycle: e.target.value === '' ? null : Number(e.target.value) })} onBlur={() => commitOncoEdit(ri)} className="w-12 border rounded px-1 py-0.5 text-right" /></td>
                          <td className="px-1"><input value={d.cycle_days ?? ''} onChange={e => editOncoDrug(ri, di, { cycle_days: e.target.value === '' ? null : Number(e.target.value) })} onBlur={() => commitOncoEdit(ri)} className="w-12 border rounded px-1 py-0.5 text-right" /></td>
                          <td className="px-1.5 text-gray-500">{d.cycle_label || '—'}</td>
                          <td className="px-1"><input value={d.total_cycles ?? ''} onChange={e => editOncoDrug(ri, di, { total_cycles: e.target.value === '' ? null : Number(e.target.value) })} onBlur={() => commitOncoEdit(ri)} className="w-12 border rounded px-1 py-0.5 text-right" /></td>
                          <td className="px-1.5 text-right bg-teal-50/60 font-medium">{d.one_dose_mg ?? '—'}</td>
                          <td className="px-1.5 text-right bg-teal-50/60 font-medium">{d.cycle_total_mg ?? '—'}</td>
                          <td className="px-1.5 text-right whitespace-nowrap" title={d.price?.label ? `${d.price.label} · ${fmt(d.price.unit_price)}/단위` : d.price?.reason || ''}>{fmt(d.cost?.cycle)}</td>
                          <td className="px-1.5 text-right whitespace-nowrap text-gray-500">{fmt(d.cost?.course)}</td>
                          <td className="px-1"><button onClick={() => removeOncoDrug(ri, di)} className="text-gray-300 hover:text-red-500"><i className="ri-close-line"></i></button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* manual 칩 */}
              {(r.kind !== 'onco') && r.drugs.length > 0 && (
                <div className="flex flex-wrap gap-2 items-center mb-1">
                  {r.drugs.map(d => {
                    const k = drugKey(d); const isWap = d.source === 'weighted_avg';
                    return (
                      <div key={k} title={`${d.ingredient} · ${d.priceDate || ''}`} className={`inline-flex items-center gap-2 rounded-lg border pl-2.5 pr-1.5 py-1.5 text-xs ${isWap ? 'border-indigo-200 bg-indigo-50' : 'border-gray-200 bg-gray-50'}`}>
                        <span className="font-medium max-w-[180px] truncate">{d.name}</span>
                        {isWap && <span className="text-[9px] px-1 py-0.5 rounded bg-indigo-500/15 text-indigo-600 font-semibold">가중</span>}
                        <span className="text-gray-500">{fmt(d[metric === 'daily' ? 'dailyCost' : metric === 'monthly' ? 'monthlyCost' : 'yearlyCost'])}</span>
                        <button onClick={() => setRegimens(prev => prev.map((rg, i) => i === ri ? { ...rg, drugs: rg.drugs.filter(x => drugKey(x) !== k) } : rg))} className="text-gray-300 hover:text-red-500"><i className="ri-close-line"></i></button>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* 추가 영역 + 합계 */}
              <div className="flex items-end justify-between gap-4 mt-2">
                <div className="relative">
                  {addTarget === ri ? (
                    <div className="flex items-start gap-2">
                      <div className="flex gap-1 bg-gray-100 rounded-lg p-0.5 text-xs">
                        <button onClick={() => setAddMode('onco')} className={`px-2 py-1 rounded ${addMode === 'onco' ? 'bg-white shadow font-semibold text-teal-700' : 'text-gray-500'}`}>항암 레지멘</button>
                        <button onClick={() => setAddMode('manual')} className={`px-2 py-1 rounded ${addMode === 'manual' ? 'bg-white shadow font-semibold text-teal-700' : 'text-gray-500'}`}>단일 약제</button>
                      </div>
                      <input autoFocus value={query} onChange={e => setQuery(e.target.value)}
                        placeholder={addMode === 'onco' ? '암종·레지멘·약제 (예: 소세포, EP, Pembrolizumab)' : source === 'weighted_avg' ? '영문 성분명' : '제품·성분'}
                        className="text-xs border rounded-lg px-2 py-1.5 w-72 outline-none focus:border-teal-400" />
                      <button onClick={() => { setAddTarget(null); setQuery(''); }} className="text-xs text-gray-400 pt-1.5">닫기</button>
                      {query.trim() && (searching || oncoHits.length > 0 || results.length > 0 || wapResults.length > 0) && (
                        <div className="absolute z-10 top-9 left-0 w-[28rem] bg-white border rounded-lg shadow-lg max-h-72 overflow-auto">
                          {searching && <p className="text-xs text-gray-400 px-3 py-2">검색 중…</p>}
                          {addMode === 'onco' && oncoHits.map(h => (
                            <button key={h.ref} onClick={() => addOncoRegimen(h, ri)} className="block w-full text-left text-xs px-3 py-1.5 hover:bg-teal-50">
                              <span className="font-medium">{h.regimen_name}</span><span className="text-gray-400"> · {h.cancer}{h.line ? ` · ${h.line}` : ''}</span>
                              <span className="block text-gray-400 truncate">{h.drug_names.join(' + ')}</span>
                            </button>
                          ))}
                          {addMode === 'manual' && source === 'weighted_avg' && wapResults.map(w => (
                            <button key={w.main_ingredient_code} onClick={() => addManualDrug(ri, { source: 'weighted_avg', mainIngredientCode: w.main_ingredient_code, ingredientName: w.ingredient_name }, { name: w.ingredient_name, ingredient: w.ingredient_name, key: { source: 'weighted_avg', mainIngredientCode: w.main_ingredient_code } })} className="block w-full text-left text-xs px-3 py-1.5 hover:bg-indigo-50">
                              <span className="font-medium">{fmt(w.weighted_avg_price)}</span><span className="text-gray-400"> · {w.main_ingredient_code}</span><span className="block text-gray-400 truncate">{w.ingredient_name}</span>
                            </button>
                          ))}
                          {addMode === 'manual' && source === 'domestic' && results.map(p => (
                            <button key={p.insuranceCode} onClick={() => addManualDrug(ri, { source: 'domestic', insuranceCode: p.insuranceCode, normalizedName: p.normalizedName, productName: p.fullProductName, ingredient: p.ingredient, codes: p.mergedCodes }, { name: p.productName, ingredient: p.ingredient, key: { source: 'domestic', insuranceCode: p.insuranceCode, normalizedName: p.normalizedName } })} className="block w-full text-left text-xs px-3 py-1.5 hover:bg-teal-50">
                              <span className="font-medium">{p.productName}</span><span className="text-gray-400"> · {fmt(p.currentPrice)}</span><span className="block text-gray-400 truncate">{p.ingredient}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : (
                    <button onClick={() => { setAddTarget(ri); setAddMode(r.kind === 'onco' ? 'onco' : 'onco'); setQuery(''); }} className="inline-flex items-center gap-1 border border-dashed rounded-lg px-3 py-1.5 text-xs text-gray-500 hover:border-teal-400 hover:text-teal-600"><i className="ri-add-line"></i> 레지멘/약제 추가</button>
                  )}
                </div>

                {/* 합계 */}
                {(() => {
                  const isOnco = r.kind === 'onco';
                  const daily = isOnco ? (r.oncoTotals?.daily ?? null) : regimenTotals(r).daily;
                  const monthly = isOnco ? (r.oncoTotals?.monthly ?? null) : regimenTotals(r).monthly;
                  const yearly = isOnco ? (r.oncoTotals?.yearly ?? null) : regimenTotals(r).yearly;
                  const cycle = isOnco ? oncoTotal(r.oncoDrugs, 'cycle').sum : null;
                  const course = isOnco ? oncoTotal(r.oncoDrugs, 'course').sum : null;
                  const missing = isOnco ? r.oncoTotals?.hasMissing : regimenTotals(r).hasMissing;
                  return (
                    <div className="text-right text-xs space-y-0.5 min-w-[230px]">
                      {isOnco && <div className="flex justify-end gap-4"><span className="text-gray-400">1사이클</span><span className="font-semibold">{fmt(cycle)}</span><span className="text-gray-400">전체코스</span><span className="font-bold text-teal-700">{fmt(course)}</span></div>}
                      <div className="flex justify-end gap-4">
                        <span className="text-gray-400">일</span><span>{fmt(daily)}</span>
                        <span className="text-gray-400">월</span><span className="font-semibold">{fmt(monthly)}</span>
                        <span className="text-gray-400">연</span><span className="font-semibold">{fmt(yearly)}</span>
                      </div>
                      {missing && <p className="text-amber-600 text-[10px]">일부 약가 미상 (합산 제외)</p>}
                    </div>
                  );
                })()}
              </div>
            </div>
          ))}

          {regimens.length < MAX_REGIMENS && (
            <button onClick={addRegimen} className="w-full bg-white rounded-2xl border border-dashed py-3 text-gray-400 hover:border-teal-400 hover:text-teal-600 text-sm"><i className="ri-add-line"></i> 비교 레지멘 추가</button>
          )}
        </div>
      </div>
    </div>
  );
}

// 환자 metrics 로컬 미러(서버와 동일 산식 — 표시용)
function patientMetricsLocal(p: Patient) {
  const bsa = Math.sqrt(p.height * p.weight / 3600);
  const k = p.sex === 'F' ? 0.85 : 1.0;
  const crcl = ((140 - p.age) * p.weight * k) / (72 * p.scr);
  return { bsa: bsa.toFixed(3), crcl: crcl.toFixed(1), gfr: Math.min(crcl, 125).toFixed(1) };
}
