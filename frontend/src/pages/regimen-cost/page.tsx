import { useState, useEffect, useMemo, useCallback } from 'react';
import {
  Bar, BarChart, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import {
  searchDomesticPriceChanges, enrichBulk, DomesticProduct, EnrichmentRequestItem,
} from '@/api/domestic';
import {
  listRegimens, createRegimen, updateRegimen, deleteRegimen, regimenTotals,
  Regimen, RegimenDrug, RegimenComparison,
} from '@/api/regimenCost';

const MAX_REGIMENS = 6;        // 기준 1 + 비교 5
const MAX_DRUGS = 5;           // 레지멘당 최대 약제
const MIN_DRUGS = 2;
const COLORS = ['#00857c', '#1f6fb2', '#c2780c', '#6a4ea3', '#0f9d58', '#d23f57'];

type Metric = 'daily' | 'monthly' | 'yearly';
const METRIC_LABEL: Record<Metric, string> = { daily: '일 치료비', monthly: '월 치료비', yearly: '연 치료비' };

const fmt = (n: number) => '₩' + Math.round(n).toLocaleString();

function emptyRegimen(name: string): Regimen { return { name, drugs: [] }; }

export default function RegimenCostPage() {
  const [regimens, setRegimens] = useState<Regimen[]>([
    emptyRegimen('기준 레지멘'), emptyRegimen('비교 레지멘 1'),
  ]);
  const [metric, setMetric] = useState<Metric>('monthly');

  // 약제 검색
  const [addTarget, setAddTarget] = useState<number | null>(null);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<DomesticProduct[]>([]);
  const [searching, setSearching] = useState(false);

  // 저장
  const [saved, setSaved] = useState<RegimenComparison[]>([]);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [compareName, setCompareName] = useState('새 비교');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  useEffect(() => { listRegimens().then(setSaved).catch(() => {}); }, []);

  useEffect(() => {
    if (!query.trim() || addTarget == null) { setResults([]); return; }
    const t = setTimeout(async () => {
      setSearching(true);
      try { setResults((await searchDomesticPriceChanges(query)).slice(0, 12)); }
      catch { setResults([]); }
      finally { setSearching(false); }
    }, 350);
    return () => clearTimeout(t);
  }, [query, addTarget]);

  const enrichDrug = useCallback(async (p: DomesticProduct): Promise<RegimenDrug> => {
    const base: RegimenDrug = {
      insuranceCode: p.insuranceCode, name: p.productName, ingredient: p.ingredient,
      currentPrice: p.currentPrice,
      dailyCost: p.dailyCost ?? null, monthlyCost: p.monthlyCost ?? null, yearlyCost: p.yearlyCost ?? null,
    };
    if (base.dailyCost != null || base.monthlyCost != null || base.yearlyCost != null) return base;
    try {
      const item: EnrichmentRequestItem = {
        normalized_name: p.normalizedName, product_name: p.fullProductName,
        ingredient: p.ingredient, current_price: p.currentPrice, code: p.insuranceCode, codes: p.mergedCodes,
      };
      const res = await enrichBulk([item]);
      const e = res[p.normalizedName];
      if (e && !e.is_failure && e.treatment_cost) {
        base.dailyCost = e.treatment_cost.daily ?? null;
        base.monthlyCost = e.treatment_cost.monthly ?? null;
        base.yearlyCost = e.treatment_cost.annual ?? null;
      }
    } catch { /* 비용 미상 유지 */ }
    return base;
  }, []);

  const addDrug = async (p: DomesticProduct) => {
    if (addTarget == null) return;
    const ri = addTarget;
    if (regimens[ri].drugs.length >= MAX_DRUGS) { setMsg(`레지멘당 최대 ${MAX_DRUGS}개`); return; }
    if (regimens[ri].drugs.some(d => d.insuranceCode === p.insuranceCode)) { setMsg('이미 추가된 약제'); return; }
    const drug = await enrichDrug(p);
    setRegimens(prev => prev.map((r, i) => i === ri ? { ...r, drugs: [...r.drugs, drug] } : r));
    setQuery(''); setResults([]);
  };

  const removeDrug = (ri: number, code: string) =>
    setRegimens(prev => prev.map((r, i) => i === ri ? { ...r, drugs: r.drugs.filter(d => d.insuranceCode !== code) } : r));
  const renameRegimen = (ri: number, name: string) =>
    setRegimens(prev => prev.map((r, i) => i === ri ? { ...r, name } : r));
  const addRegimen = () =>
    regimens.length < MAX_REGIMENS && setRegimens(prev => [...prev, emptyRegimen(`비교 레지멘 ${prev.length}`)]);
  const removeRegimen = (ri: number) =>
    ri > 0 && setRegimens(prev => prev.filter((_, i) => i !== ri));

  const chartData = useMemo(() => regimens.map((r, i) => {
    const t = regimenTotals(r);
    return { name: r.name || `레지멘 ${i + 1}`, value: t[metric], hasMissing: t.hasMissing, idx: i };
  }), [regimens, metric]);

  const payload = () => ({
    base: regimens[0], comparators: regimens.slice(1),
    snapshotDate: new Date().toISOString().slice(0, 10),
  });

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
    setMsg(`'${c.name}' 불러옴`);
  };

  const onDelete = async (id: number) => {
    await deleteRegimen(id); setSaved(await listRegimens());
    if (currentId === id) { setCurrentId(null); setMsg('삭제됨'); }
  };

  const onNew = () => {
    setCurrentId(null); setCompareName('새 비교');
    setRegimens([emptyRegimen('기준 레지멘'), emptyRegimen('비교 레지멘 1')]);
  };

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 p-6">
      <div className="max-w-[1400px] mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-1">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <i className="ri-bar-chart-box-line text-teal-600"></i>투약 비용 비교
            </h1>
            <p className="text-sm text-gray-500 mt-1">국내약가 기반 레지멘(약제 2~5개)을 구성해 일·월·연 치료비를 비교합니다.</p>
          </div>
          <div className="flex items-center gap-2">
            <input value={compareName} onChange={e => setCompareName(e.target.value)}
              className="border rounded-lg px-3 py-1.5 text-sm w-40" placeholder="비교 이름" />
            <button onClick={onSave} disabled={busy}
              className="bg-teal-600 text-white text-sm px-4 py-1.5 rounded-lg hover:bg-teal-700 disabled:opacity-50">
              {currentId ? '업데이트' : '저장'}</button>
            <button onClick={onNew} className="border text-sm px-3 py-1.5 rounded-lg hover:bg-gray-100">새로</button>
          </div>
        </div>
        {msg && <p className="text-xs text-teal-700 mb-2">{msg}</p>}

        {/* 저장된 비교 */}
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
        <div className="bg-white rounded-2xl border p-5 mb-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-bold text-sm">레지멘 치료비 비교</h2>
            <div className="flex gap-1 bg-gray-100 rounded-lg p-1">
              {(['daily', 'monthly', 'yearly'] as Metric[]).map(m => (
                <button key={m} onClick={() => setMetric(m)}
                  className={`text-xs px-3 py-1 rounded-md ${metric === m ? 'bg-white shadow font-semibold text-teal-700' : 'text-gray-500'}`}>
                  {METRIC_LABEL[m]}</button>
              ))}
            </div>
          </div>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={chartData} margin={{ top: 24, right: 16, left: 8, bottom: 5 }}>
              <XAxis dataKey="name" tick={{ fontSize: 12 }} />
              <YAxis tickFormatter={(v) => `${Math.round(v / 10000)}만`} tick={{ fontSize: 11 }} />
              <Tooltip formatter={(v: number) => fmt(v)} />
              <Bar dataKey="value" radius={[8, 8, 0, 0]} maxBarSize={70}>
                {chartData.map((d) => <Cell key={d.idx} fill={COLORS[d.idx % COLORS.length]} />)}
                <LabelList dataKey="value" position="top" formatter={(v: number) => fmt(v)} style={{ fontSize: 11, fontWeight: 700 }} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* 레지멘 컬럼 */}
        <div className="grid gap-4" style={{ gridTemplateColumns: `repeat(${Math.min(regimens.length, MAX_REGIMENS)}, minmax(220px, 1fr))` }}>
          {regimens.map((r, ri) => {
            const t = regimenTotals(r);
            return (
              <div key={ri} className="bg-white rounded-2xl border p-4" style={{ borderTopColor: COLORS[ri % COLORS.length], borderTopWidth: 3 }}>
                <div className="flex items-center gap-1 mb-2">
                  {ri === 0 && <span className="text-xs px-1.5 py-0.5 rounded bg-teal-100 text-teal-700 font-semibold">기준</span>}
                  <input value={r.name} onChange={e => renameRegimen(ri, e.target.value)}
                    className="font-semibold text-sm flex-1 min-w-0 bg-transparent outline-none border-b border-transparent focus:border-gray-300" />
                  {ri > 0 && <button onClick={() => removeRegimen(ri)} className="text-gray-300 hover:text-red-500"><i className="ri-delete-bin-line"></i></button>}
                </div>

                {/* 약제 목록 */}
                <div className="space-y-2 mb-2">
                  {r.drugs.map(d => (
                    <div key={d.insuranceCode} className="border rounded-lg p-2 text-xs">
                      <div className="flex items-start justify-between gap-1">
                        <span className="font-medium leading-tight">{d.name}</span>
                        <button onClick={() => removeDrug(ri, d.insuranceCode)} className="text-gray-300 hover:text-red-500"><i className="ri-close-line"></i></button>
                      </div>
                      <p className="text-gray-400 truncate">{d.ingredient}</p>
                      <p className="text-gray-600 mt-0.5">
                        일 {d.dailyCost != null ? fmt(d.dailyCost) : '—'}
                      </p>
                    </div>
                  ))}
                  {r.drugs.length < MIN_DRUGS && <p className="text-xs text-amber-600">약제를 {MIN_DRUGS}개 이상 추가하세요</p>}
                </div>

                {/* 약제 추가 */}
                {r.drugs.length < MAX_DRUGS && (
                  addTarget === ri ? (
                    <div className="border rounded-lg p-2">
                      <input autoFocus value={query} onChange={e => setQuery(e.target.value)}
                        placeholder="제품·성분 검색" className="w-full text-xs border-b pb-1 outline-none mb-1" />
                      {searching && <p className="text-xs text-gray-400">검색 중…</p>}
                      <div className="max-h-48 overflow-auto">
                        {results.map(p => (
                          <button key={p.insuranceCode} onClick={() => addDrug(p)}
                            className="block w-full text-left text-xs py-1 hover:bg-teal-50 rounded px-1">
                            <span className="font-medium">{p.productName}</span>
                            <span className="text-gray-400"> · {fmt(p.currentPrice)}</span>
                          </button>
                        ))}
                      </div>
                      <button onClick={() => { setAddTarget(null); setQuery(''); }} className="text-xs text-gray-400 mt-1">닫기</button>
                    </div>
                  ) : (
                    <button onClick={() => { setAddTarget(ri); setQuery(''); setResults([]); }}
                      className="w-full border border-dashed rounded-lg py-1.5 text-xs text-gray-500 hover:border-teal-400 hover:text-teal-600">
                      <i className="ri-add-line"></i> 약제 추가
                    </button>
                  )
                )}

                {/* 합산 */}
                <div className="border-t mt-3 pt-2 text-xs space-y-0.5">
                  <div className="flex justify-between"><span className="text-gray-500">일</span><span className="font-semibold">{fmt(t.daily)}</span></div>
                  <div className="flex justify-between"><span className="text-gray-500">월</span><span className="font-semibold">{fmt(t.monthly)}</span></div>
                  <div className="flex justify-between"><span className="text-gray-500">연</span><span className="font-bold text-teal-700">{fmt(t.yearly)}</span></div>
                  {t.hasMissing && <p className="text-amber-600 text-[11px]">일부 약제 치료비 미상 (합산 제외)</p>}
                </div>
              </div>
            );
          })}

          {regimens.length < MAX_REGIMENS && (
            <button onClick={addRegimen}
              className="bg-white rounded-2xl border border-dashed flex flex-col items-center justify-center min-h-[140px] text-gray-400 hover:border-teal-400 hover:text-teal-600">
              <i className="ri-add-line text-2xl"></i><span className="text-xs mt-1">비교 레지멘 추가</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
