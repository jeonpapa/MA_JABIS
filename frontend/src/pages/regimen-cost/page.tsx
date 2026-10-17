import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import {
  Bar, BarChart, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  listRegimens, createRegimen, updateRegimen, deleteRegimen,
  wapSearch, oncoSearch, oncoGet, customRegimenGet, saveCustomRegimen, oncoCost, drugDosing, saveDrugDosing, exportRegimenXlsx,
  priceSources, regimenDrugSearch,
  WapResult, OncoDrug, OncoRegimenHit, Patient, PATIENT_DEFAULT, RegimenDrugHit, OncoTotals,
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
// 비용 셀: 산출 불가는 ₩0 이 아니라 '미산출'
const fmtCost = (n: number | null | undefined) => n == null ? '미산출' : '₩' + Math.round(n).toLocaleString();
const WAP_NOTE = '주성분 가중평균 데이터 연결 전 — 국내 상한금액 기준으로 계산합니다';
// 용어 툴팁 (비전문가용 쉬운 설명)
const TIPS = {
  regimen: '레지멘: 한 번의 치료에 함께 쓰는 약제 조합과 투여 일정(용량·간격·횟수)입니다. 예) 펨브롤리주맙 200mg을 3주마다 1회.',
  bsa: 'BSA(체표면적): 키와 체중으로 계산한 몸의 겉넓이(m²)입니다. 많은 항암제는 mg/m² 단위라 BSA 를 곱해 1회 용량을 구합니다 (Mosteller 공식).',
  gfr: 'GFR(사구체 여과율): 콩팥이 혈액을 거르는 능력입니다. 나이·체중·성별·혈청 크레아티닌(SCr)으로 추정(Cockcroft-Gault, 최대 125)하며, 카보플라틴(AUC) 용량 계산에 씁니다.',
  wap: 'WAP(주성분 가중평균가): 같은 성분·규격 제품들의 가격을 사용량으로 가중평균한 단가입니다. 특정 브랜드 대신 시장 평균 가격으로 비교할 때 씁니다.',
  domestic: '국내 상한금액: 건강보험 약제급여목록의 보험 상한금액(표시가)입니다. 실거래가·RSA 환급 후 실제 가격은 반영되지 않습니다.',
} as const;
function Tip({ text }: { text: string }) {
  return <i className="ri-question-line text-[12px] align-middle cursor-help" style={{ color: 'var(--label-3)' }} title={text} aria-label={text} role="img"></i>;
}
const unitPerLabel = (u?: string | null) => u === 'mg/m2' || u === 'mg/m2/day' ? 'BSA' : u === 'mg/kg' || u === 'mg/kg/day' ? '체중' : u === 'AUC' ? 'GFR' : '';
const shortProduct = (s: string) => (s || '').split('_')[0];

/** 약제별 미산출 사유 요약 (툴팁용). */
function missingSummary(r: Regimen): string {
  const lines = (r.oncoDrugs || [])
    .filter(d => d.cost && d.cost.cycle == null)
    .map(d => `${d.ingredient}: ${d.missing_reason || '산출 불가'}`);
  return lines.length ? `미산출 약제 (합계에서 제외)\n${lines.join('\n')}` : '';
}

/** 레지멘 합계 셀 — null 은 '미산출' + 사유, 부분합은 * 표시. */
function TotalCell({ t, k, r }: { t?: OncoTotals; k: 'cycle' | 'course' | 'daily' | 'monthly' | 'yearly'; r: Regimen }) {
  if (!t) return <span style={{ color: 'var(--label-3)' }}>—</span>;
  const v = t[k];
  if (v == null) {
    const why = k === 'course' && t.computedCount
      ? ((r.oncoDrugs || []).find(d => d.course_reason)?.course_reason || '총사이클 미입력')
      : missingSummary(r) || '가격·용량 정보 부족';
    return <span className="text-xs font-normal" style={{ color: 'var(--label-3)' }} title={why}>미산출{k === 'course' && t.computedCount ? ` · ${why}` : ''}</span>;
  }
  const partial = t.partial?.[k];
  return <span title={partial ? missingSummary(r) : undefined}>{fmtCost(v)}{partial && <sup className="text-amber-500">*</sup>}</span>;
}
// 기준일은 한국 날짜 (toISOString 은 UTC 라 KST 오전 9시 전에는 전날이 된다)
const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul' }).format(new Date());
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
  // WAP 연결 여부 확인 전에는 국내 상한금액이 안전한 기본값
  const [source, setSource] = useState<PriceSource>('domestic');
  const [wapAvailable, setWapAvailable] = useState<boolean | null>(null);
  // 이미 약제가 있는 카드에 레지멘을 고른 경우 — 교체/새 비교 카드 선택 대기
  const [pendingPick, setPendingPick] = useState<{ hit: OncoRegimenHit; ri: number } | null>(null);
  const [patient, setPatient] = useState<Patient>(PATIENT_DEFAULT);
  const [patientOpen, setPatientOpen] = useState(false);

  const [addTarget, setAddTarget] = useState<number | null>(null);
  const [addMode, setAddMode] = useState<'onco' | 'drug'>('onco');
  const [query, setQuery] = useState('');
  const [oncoHits, setOncoHits] = useState<OncoRegimenHit[]>([]);
  const [results, setResults] = useState<RegimenDrugHit[]>([]);
  const [wapResults, setWapResults] = useState<WapResult[]>([]);
  const [searching, setSearching] = useState(false);
  // 마지막으로 끝난 검색어/오류 — '검색 중'·'결과 없음'·'오류'를 구분해 보여준다
  const [searchedFor, setSearchedFor] = useState('');
  const [searchError, setSearchError] = useState('');
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
  useEffect(() => {
    priceSources().then(ps => {
      setWapAvailable(ps.weighted_avg.available);
      // WAP 연결됨 + 아직 아무것도 추가 전이면 서버 기본 소스 사용
      if (ps.default === 'weighted_avg' && !ref.current.regimens.some(r => (r.oncoDrugs || []).length)) setSource('weighted_avg');
    }).catch(() => setWapAvailable(false));
  }, []);

  // 검색
  const useWapSearch = source === 'weighted_avg' && wapAvailable === true;
  useEffect(() => {
    if (addTarget == null || !query.trim()) { setOncoHits([]); setResults([]); setWapResults([]); return; }
    const t = setTimeout(async () => {
      setSearching(true); setSearchError('');
      setOncoHits([]); setResults([]); setWapResults([]);
      try {
        if (addMode === 'onco') setOncoHits(await oncoSearch(query.trim()));
        else {
          let wapHits: WapResult[] = [];
          if (useWapSearch) {
            const r = await wapSearch(query.trim(), asOfDate);
            wapHits = r.available ? (r.results || []).slice(0, 25) : [];
            setWapResults(wapHits);
          }
          // 국내 등재품 검색(한글 제품명·영문 INN) — WAP 미연결이거나 WAP 결과가 없을 때
          if (!wapHits.length) setResults(await regimenDrugSearch(query.trim()));
        }
      } catch (e) { setOncoHits([]); setResults([]); setWapResults([]); setSearchError(e instanceof Error ? e.message : '검색 실패'); }
      finally { setSearching(false); setSearchedFor(query.trim()); }
    }, 350);
    return () => clearTimeout(t);
  }, [query, addTarget, addMode, useWapSearch, asOfDate]);

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
        priceNote: res.priceNote ?? null,
      }));
      if (res.wapAvailable != null) setWapAvailable(res.wapAvailable);
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

  // 레지멘 선택: 빈 카드면 바로 채우고, 이미 약제가 있으면 교체/새 비교 카드 중 선택을 물어본다
  // (예전에는 조용히 약제를 합쳐 서로 다른 레지멘이 한 카드에 섞였음)
  const loadRegimenRows = async (hit: OncoRegimenHit): Promise<OncoDrug[]> => {
    const isCustom = hit.source_kind === 'custom';
    const full = isCustom ? await customRegimenGet(hit.ref) : await oncoGet(hit.ref);
    const src = ref.current.source;
    return full.drugs.map(d => ({
      ...d,
      uid: uidRef.current++,
      dose_source: isCustom ? (d.dose_source || 'saved') : 'onco_db',
      // 커스텀은 저장된 가격조회 정보 보존(한글 브랜드 등), 정본은 현재 가격 소스 + INN=성분
      price_source: isCustom && d.price_source && (d.price_source !== 'weighted_avg' || wapAvailable) ? d.price_source : src,
      price_ref: d.price_ref || '',
      price_inn: d.price_inn || d.ingredient,
    }));
  };
  const closeSearch = () => { setAddTarget(null); setQuery(''); setOncoHits([]); setResults([]); setWapResults([]); };
  const addRegimen = async (hit: OncoRegimenHit, ri: number) => {
    if ((ref.current.regimens[ri]?.oncoDrugs || []).length) { setPendingPick({ hit, ri }); closeSearch(); return; }
    await applyRegimen(hit, ri, 'replace');
  };
  const applyRegimen = async (hit: OncoRegimenHit, ri: number, mode: 'replace' | 'new') => {
    setPendingPick(null);
    setBusyCalc(true);
    try {
      const rows = await loadRegimenRows(hit);
      if (mode === 'new' && ref.current.regimens.length < MAX_REGIMENS) {
        const idx = ref.current.regimens.length;
        setRegimens(p => [...p, { ...emptyRegimen(hit.regimen_name), oncoDrugs: rows }]);
        closeSearch();
        setTimeout(() => recompute(idx, rows), 0);
        setMsg(`'${hit.regimen_name}'을(를) 새 비교 카드로 추가했습니다`);
      } else {
        setRegimens(p => p.map((r, i) => i === ri ? { ...r, name: hit.regimen_name, oncoDrugs: rows, oncoTotals: undefined, saved: false } : r));
        closeSearch();
        setTimeout(() => recompute(ri, rows), 0);
      }
    } catch { setMsg('레지멘을 불러오지 못했습니다 — 다시 시도해 주세요'); }
    finally { setBusyCalc(false); }
  };

  // 가격 소스 전환 = 모든 행에 적용 (예전에는 행별 price_source 가 추가 시점 값으로 고정돼 전환이 무시됨)
  const changeSource = (s: PriceSource) => {
    setSource(s);
    setRegimens(p => p.map(r => ({
      ...r,
      oncoDrugs: (r.oncoDrugs || []).map(d => {
        const prev = d.price_source || 'domestic';
        const refs = { ...(d.price_refs || {}), ...(d.price_ref ? { [prev]: d.price_ref } : {}) };
        return { ...d, price_source: s, price_ref: refs[s] || '', price_refs: refs };
      }),
    })));
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
    name: r.name || `레지멘 ${i + 1}`, value: r.oncoTotals?.[metric] ?? null, idx: i,
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
    // WAP 연결 전이면 저장본의 가중평균 행도 국내 상한금액으로 계산
    const loadedSrc: PriceSource = c.payload.source === 'weighted_avg' && wapAvailable === false ? 'domestic' : (c.payload.source || source);
    setRegimens([c.payload.base, ...(c.payload.comparators || [])].filter(Boolean).map(r => ({
      ...r,
      oncoDrugs: (r.oncoDrugs || []).map(d => d.price_source === 'weighted_avg' && loadedSrc === 'domestic'
        ? { ...d, price_source: 'domestic' as PriceSource, price_ref: '' } : d),
    })));
    setAsOfDate(c.payload.asOfDate || c.payload.snapshotDate || today());
    setSource(loadedSrc);
    if (c.payload.patient) setPatient(c.payload.patient);
    setMsg(`'${c.name}' 불러옴`);
    setTimeout(() => recomputeAll(), 50);   // 저장 스냅샷(구 ₩0 합계 등) 대신 현재 기준으로 재계산
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
            <h1 className="text-2xl font-bold flex items-center gap-2">요법 비용 비교</h1>
            <p className="text-sm text-gray-500 mt-1">레지멘 <Tip text={TIPS.regimen} />(정본 DB)으로 기본 구성 후 자유 편집 · 환자 파라미터 기반 용량 + 기준일 약가로 치료비 비교.</p>
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
          {chartData.every(d => !d.value) ? (
            <div className="h-[260px] flex flex-col items-center justify-center text-center gap-1.5" role="status">
              <i className="ri-bar-chart-box-line text-3xl text-gray-300"></i>
              <p className="text-sm font-medium text-gray-600">아직 비교할 비용이 없습니다</p>
              <p className="text-xs text-gray-400">아래 레지멘 카드에서 레지멘이나 약제를 추가하면 치료비가 계산됩니다.</p>
            </div>
          ) : (
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={chartData} margin={{ top: 24, right: 16, left: 8, bottom: 5 }}>
              <XAxis dataKey="name" tick={{ fontSize: 12 }} /><YAxis tickFormatter={v => `${Math.round(v / 10000)}만`} tick={{ fontSize: 11 }} />
              <Tooltip formatter={(v: number) => fmt(v)} />
              <Bar dataKey="value" radius={[8, 8, 0, 0]} maxBarSize={70}>
                {chartData.map(d => <Cell key={d.idx} fill={COLORS[d.idx % COLORS.length]} />)}
                <LabelList dataKey="value" position="top" formatter={(v: number | null) => fmtCost(v)} style={{ fontSize: 11, fontWeight: 700 }} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          )}
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
                        {t?.hasMissing && <span className="ml-1 text-amber-500" title={missingSummary(r)}>⚠</span>}
                        {t?.hasMissing && <span className="ml-1 text-[11px]" style={{ color: 'var(--label-3)' }}>
                          {t.computedCount ? `약제 ${t.missingCount}개 제외된 부분합` : '미산출'}</span>}
                      </td>
                      {!(r.oncoDrugs || []).length ? <td colSpan={5} className="text-right px-3 text-xs" style={{ color: 'var(--label-3)' }}>약제 없음</td> : <>
                      <td className="text-right px-3 whitespace-nowrap">{hasCycle ? <TotalCell t={t} k="cycle" r={r} /> : '—'}</td>
                      <td className="text-right px-3 whitespace-nowrap font-bold text-teal-700">{hasCycle ? <TotalCell t={t} k="course" r={r} /> : '—'}</td>
                      <td className="text-right px-3 whitespace-nowrap"><TotalCell t={t} k="daily" r={r} /></td>
                      <td className="text-right px-3 whitespace-nowrap font-semibold"><TotalCell t={t} k="monthly" r={r} /></td>
                      <td className="text-right px-3 whitespace-nowrap font-semibold"><TotalCell t={t} k="yearly" r={r} /></td>
                      </>}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-gray-400 mt-2">1사이클·전체코스는 항암 레지멘(주기 투여)에만 표시 · 일반약제는 일/월/연만 · 전체코스는 총사이클을 입력해야 계산 · ‘0’ 대신 ‘미산출’은 가격·용량이 부족한 경우입니다 (⚠ 에 마우스를 올리면 사유)</p>
        </div>

        {/* 컨트롤 (테이블 인접) */}
        <div className="bg-white rounded-2xl border px-5 py-3 mb-3 flex items-center gap-5 flex-wrap">
          <label className="flex items-center gap-2 text-sm"><i className="ri-calendar-event-line text-teal-600"></i><span className="text-gray-600">기준 시점</span>
            <input type="date" value={asOfDate} max={today()} onChange={e => setAsOfDate(e.target.value)} className="border rounded-lg px-2.5 py-1 text-sm" />
          </label>
          <div className="flex items-center gap-2 text-sm"><span className="text-gray-600">가격 소스</span>
            <div className="flex gap-1 bg-gray-100 rounded-lg p-1">
              {([['domestic', '국내 상한금액', TIPS.domestic], ['weighted_avg', '주성분 가중평균(WAP)', TIPS.wap]] as [PriceSource, string, string][]).map(([s, lbl, tip]) => {
                const off = s === 'weighted_avg' && wapAvailable === false;
                return (
                  <button key={s} onClick={() => !off && changeSource(s)} disabled={off} aria-disabled={off}
                    title={off ? `${WAP_NOTE}\n\n${tip}` : tip}
                    className={`text-xs px-3 py-1 rounded-md ${source === s ? 'bg-white shadow font-semibold text-teal-700' : 'text-gray-500'} ${off ? 'opacity-50 cursor-not-allowed' : ''}`}>
                    {lbl}{off && <span className="ml-1 text-[10px]">· 연결 전</span>}</button>
                );
              })}
            </div>
            <Tip text={`${TIPS.domestic}\n\n${TIPS.wap}`} />
          </div>
          <button onClick={() => setPatientOpen(o => !o)} className="flex items-center gap-1.5 text-sm text-gray-600 hover:text-teal-700">
            <i className="ri-user-heart-line text-teal-600"></i>환자값 <span className="text-xs text-gray-400">키 {patient.height}cm · {patient.weight}kg · BSA {pm.bsa} m² · GFR {pm.gfr}</span><i className={`ri-arrow-${patientOpen ? 'up' : 'down'}-s-line`}></i>
          </button>
          <span className="inline-flex gap-1.5"><Tip text={TIPS.bsa} /><Tip text={TIPS.gfr} /></span>
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
              <div className="text-gray-500 pb-1">→ BSA <Tip text={TIPS.bsa} /> <b className="text-gray-700">{pm.bsa}</b> m² · CrCl {pm.crcl} · GFR(cap125) <Tip text={TIPS.gfr} /> <b className="text-gray-700">{pm.gfr}</b></div>
              <button onClick={() => setPatient(PATIENT_DEFAULT)} className="text-gray-400 hover:text-gray-600 pb-1">기본값</button>
            </div>
          )}
        </div>

        {(wapAvailable === false || regimens.some(r => r.priceNote)) && (
          <div className="ui-notice ui-notice-info mb-3" role="status">
            <i className="ri-information-line"></i>
            <span>{WAP_NOTE}. 가중평균이 연결되면 ‘주성분 가중평균(WAP)’을 선택할 수 있습니다.</span>
          </div>
        )}

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
                              {d.cost && d.cost.cycle == null && (
                                <span className="block text-[10px] font-normal text-amber-600 whitespace-normal" title={d.missing_reason || ''}>
                                  미산출 · {d.missing_reason || '산출 불가'}</span>
                              )}
                            </td>
                            <td className="px-1"><input value={d.dose_value ?? ''} onChange={e => editRow(ri, di, { dose_value: num(e.target.value) })} onBlur={() => commitRow(ri, di)} className="w-14 border rounded px-1 py-0.5 text-right" /></td>
                            <td className="px-1"><select value={d.unit ?? ''} onChange={e => { editRow(ri, di, { unit: e.target.value }); setTimeout(() => commitRow(ri, di), 0); }} className="border rounded px-1 py-0.5">{UNITS.map(u => <option key={u}>{u}</option>)}{d.unit && !UNITS.includes(d.unit) && <option>{d.unit}</option>}</select></td>
                            <td className="px-1.5 text-gray-500 whitespace-nowrap">{d.dose_days || '—'}</td>
                            <td className="px-1"><input value={d.per_cycle ?? ''} onChange={e => editRow(ri, di, { per_cycle: num(e.target.value) })} onBlur={() => commitRow(ri, di)} className="w-12 border rounded px-1 py-0.5 text-right" /></td>
                            <td className="px-1"><input value={d.cycle_days ?? ''} onChange={e => editRow(ri, di, { cycle_days: num(e.target.value) })} onBlur={() => commitRow(ri, di)} className="w-12 border rounded px-1 py-0.5 text-right" /></td>
                            <td className="px-1"><input value={d.total_cycles ?? ''} onChange={e => editRow(ri, di, { total_cycles: num(e.target.value) })} onBlur={() => commitRow(ri, di)} className="w-12 border rounded px-1 py-0.5 text-right" /></td>
                            <td className="px-1.5 text-right bg-teal-50/60 font-medium" title={d.dose_basis || ''}>{d.one_dose_mg ?? '—'}</td>
                            <td className="px-1.5 text-right bg-teal-50/60 font-medium">{d.cycle_total_mg ?? '—'}</td>
                            <td className="px-1.5 text-right whitespace-nowrap" title={d.price?.available ? `${d.price.label || ''} · ${fmt(d.price.unit_price)}/단위(${d.price.content_mg ?? '?'}mg)${d.price.priceDate ? ` · ${d.price.priceDate} 적용` : ''}` : d.missing_reason || ''}>
                              {d.cost ? <span className={d.cost.cycle == null ? 'text-[11px] text-amber-600' : ''}>{fmtCost(d.cost.cycle)}</span> : '—'}</td>
                            <td className="px-1.5 text-right whitespace-nowrap text-gray-500" title={d.course_reason || ''}>
                              {d.cost ? (d.cost.course == null ? <span className="text-[11px]" style={{ color: 'var(--label-3)' }}>{d.cost.cycle == null ? '미산출' : (d.course_reason || '미산출')}</span> : fmtCost(d.cost.course)) : '—'}</td>
                            <td className="px-1.5 text-right whitespace-nowrap text-gray-500">{d.cost ? fmtCost(d.cost.monthly) : '—'}</td>
                            <td className="px-1.5 whitespace-nowrap">
                              <span className={`text-[10px] px-1 py-0.5 rounded ${d.dose_source === 'saved' ? 'bg-teal-500/15 text-teal-600' : d.dose_source === 'onco_db' ? 'bg-indigo-500/15 text-indigo-600' : d.dose_source === 'mfds_label' ? 'bg-blue-500/15 text-blue-600' : d.dose_source === 'manual' ? 'bg-amber-500/15 text-amber-600' : 'bg-gray-200 text-gray-400'}`}>{DOSE_SRC_LABEL[d.dose_source || 'none']}</span>
                              <span className="ml-1 text-[10px] text-gray-400" title={d.price?.fallback ? (d.price.note || WAP_NOTE) : d.price?.label || ''}>
                                {(d.price?.source || d.price_source) === 'weighted_avg' ? 'WAP' : d.price?.fallback ? '국내가(대체)' : '국내가'}</span>
                            </td>
                            <td className="px-1"><button onClick={() => removeRow(ri, di)} className="text-gray-300 hover:text-red-500"><i className="ri-close-line"></i></button></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {rows.length > 0 && (
                  <div className="mt-2 text-[11px] leading-relaxed" style={{ color: 'var(--label-2)' }}>
                    <span className="font-semibold">계산 가정</span>
                    <span className="ml-1.5">환자 {patient.height}cm·{patient.weight}kg·{patient.age}세·{patient.sex === 'F' ? '여' : '남'}·SCr {patient.scr}</span>
                    <span className="ml-1.5">→ BSA <Tip text={TIPS.bsa} /> {r.metrics?.bsa ?? pm.bsa} m² · GFR <Tip text={TIPS.gfr} /> {r.metrics?.gfr ?? pm.gfr} mL/min</span>
                    <span className="ml-1.5">· 기준일 {asOfDate} · {source === 'weighted_avg' && wapAvailable ? '주성분 가중평균' : '국내 상한금액'} 기준</span>
                    <ul className="mt-0.5 space-y-0.5">
                      {rows.map((d, di) => {
                        const per = unitPerLabel(d.unit);
                        return (
                          <li key={d.uid ?? di}>
                            <b className="font-medium" style={{ color: 'var(--label)' }}>{d.ingredient}</b>{' '}
                            {d.dose_value ?? '?'}{d.unit || ''}{per ? ` × ${per}` : ''}{d.one_dose_mg != null ? ` = ${d.one_dose_mg}mg/회` : ''}
                            {' · '}{d.per_cycle ?? 1}회/{d.cycle_days ?? '?'}일{d.cycle_label ? ` (${d.cycle_label})` : ''}
                            {' · '}{d.total_cycles ? `총 ${d.total_cycles}사이클` : '총사이클 미입력'}
                            {d.price?.available && d.price.unit_price != null && <> · 단가 {fmt(d.price.unit_price)}/{d.price.content_mg}mg ({shortProduct(d.price.label || '')})</>}
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                )}

                {pendingPick?.ri === ri && (
                  <div className="ui-notice ui-notice-warn mt-2 flex-wrap items-center" role="alertdialog" aria-label="레지멘 추가 방식 선택">
                    <span className="flex-1 min-w-[16rem]">
                      이 카드에는 이미 ‘{r.name}’ 약제가 있습니다. ‘{pendingPick.hit.regimen_name}’을(를) 어떻게 추가할까요?
                      <span className="block text-[11px] opacity-80">레지멘끼리 약제를 섞지 않도록, 보통은 새 비교 카드로 추가하는 것을 권장합니다. 약제 하나만 더하려면 ‘약제’ 탭을 쓰세요.</span>
                    </span>
                    <button className="ui-button" style={{ height: 30, fontSize: 13 }} autoFocus disabled={regimens.length >= MAX_REGIMENS}
                      title={regimens.length >= MAX_REGIMENS ? `비교 카드는 최대 ${MAX_REGIMENS}개입니다` : ''}
                      onClick={() => applyRegimen(pendingPick.hit, ri, 'new')}>새 비교 카드로 추가</button>
                    <button className="ui-button-secondary" style={{ height: 30, fontSize: 13 }}
                      onClick={() => applyRegimen(pendingPick.hit, ri, 'replace')}>이 카드 교체</button>
                    <button className="text-xs underline" onClick={() => setPendingPick(null)}>취소</button>
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
                          placeholder={addMode === 'onco' ? '암종·레지멘·약제 (예: 비소세포폐암, pembrolizumab)' : useWapSearch ? '영문 성분명 (예: pembrolizumab)' : '제품명 또는 영문 성분명 (예: 키트루다, pembrolizumab)'}
                          className="text-xs border rounded-lg px-2 py-1.5 w-72 outline-none focus:border-teal-400" />
                        <button onClick={closeSearch} className="text-xs text-gray-400 pt-1.5">닫기</button>
                        {query.trim() && !searching && searchedFor === query.trim() && oncoHits.length === 0 && results.length === 0 && wapResults.length === 0 && (
                          <div className="absolute z-10 top-9 left-0 w-[28rem] bg-white border rounded-lg shadow-lg px-3 py-3 text-xs" role="status">
                            {searchError
                              ? <p className="text-red-500">검색 실패: {searchError}</p>
                              : <p className="text-gray-500">‘{query.trim()}’ 검색 결과가 없습니다. {addMode === 'onco'
                                  ? '암종(예: 비소세포폐암)이나 영문 성분명(예: pembrolizumab), 제품명(예: 키트루다)으로 검색해 보세요.'
                                  : '제품명(예: 키트루다)이나 영문 성분명(예: pembrolizumab)으로 검색해 보세요. 국내 급여 등재품만 검색됩니다.'}</p>}
                          </div>
                        )}
                        {query.trim() && (searching || oncoHits.length > 0 || results.length > 0 || wapResults.length > 0) && (
                          <div className="absolute z-10 top-9 left-0 w-[28rem] bg-white border rounded-lg shadow-lg max-h-72 overflow-auto">
                            {searching && <p className="text-xs text-gray-400 px-3 py-2">검색 중…</p>}
                            {addMode === 'onco' && !searching && oncoHits.length > 0 && rows.length > 0 && (
                              <p className="text-[11px] px-3 py-1.5 border-b" style={{ color: 'var(--label-2)' }}>
                                이 카드에 이미 약제가 있어요. 레지멘을 고르면 ‘새 비교 카드로 추가’할지 ‘이 카드 교체’할지 물어봅니다.</p>
                            )}
                            {addMode === 'onco' && oncoHits.map(h => (
                              <button key={h.ref} onClick={() => addRegimen(h, ri)} className="block w-full text-left text-xs px-3 py-1.5 hover:bg-teal-50">
                                <span className="font-medium">{h.regimen_name}</span><span className="text-gray-400"> · {h.cancer}{h.line ? ` · ${h.line}` : ''}</span>
                                <span className="block text-gray-400 truncate">{h.drug_names.join(' + ')}</span>
                              </button>
                            ))}
                            {addMode === 'drug' && wapResults.map(w => (
                              <button key={w.main_ingredient_code} onClick={() => addDrug(ri, { inn: extractInn(w.ingredient_name), display: extractInn(w.ingredient_name), price_source: 'weighted_avg', price_ref: w.main_ingredient_code })} className="block w-full text-left text-xs px-3 py-1.5 hover:bg-indigo-50">
                                <span className="font-medium">{fmt(w.weighted_avg_price)}</span><span className="text-gray-400"> · {w.main_ingredient_code}</span><span className="block text-gray-400 truncate">{w.ingredient_name}</span>
                              </button>
                            ))}
                            {addMode === 'drug' && !wapResults.length && results.length > 0 && useWapSearch && (
                              <p className="text-[11px] px-3 py-1.5 border-b" style={{ color: 'var(--label-2)' }}>가중평균 결과가 없어 국내 등재품(상한금액)을 보여드립니다.</p>
                            )}
                            {addMode === 'drug' && results.map(p => (
                              <button key={p.insurance_code} onClick={() => addDrug(ri, { inn: p.inn || extractInn(p.ingredient || ''), display: shortProduct(p.product_name_kr), price_source: 'domestic', price_ref: p.insurance_code })} className="block w-full text-left text-xs px-3 py-1.5 hover:bg-teal-50">
                                <span className="font-medium">{shortProduct(p.product_name_kr)}</span><span className="text-gray-400"> · {p.max_price != null ? fmt(p.max_price) : '상한금액 없음'}</span>
                                <span className="block text-gray-400 truncate">{p.ingredient}{p.company ? ` · ${p.company}` : ''}</span>
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
