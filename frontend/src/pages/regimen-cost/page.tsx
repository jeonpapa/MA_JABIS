import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import {
  Bar, BarChart, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { searchDomesticPriceChanges, DomesticProduct } from '@/api/domestic';
import {
  listRegimens, createRegimen, updateRegimen, deleteRegimen, regimenTotals,
  wapSearch, priceAsOf, WapResult, PriceAsOfItem,
  Regimen, RegimenDrug, RegimenComparison, RegimenPayload, PriceSource,
} from '@/api/regimenCost';

const MAX_REGIMENS = 6;        // 기준 1 + 비교 5
const MAX_DRUGS = 5;           // 레지멘당 최대 약제
const MIN_DRUGS = 2;
const COLORS = ['#00857c', '#1f6fb2', '#c2780c', '#6a4ea3', '#0f9d58', '#d23f57'];

type Metric = 'daily' | 'monthly' | 'yearly';
const METRIC_LABEL: Record<Metric, string> = { daily: '일 치료비', monthly: '월 치료비', yearly: '연 치료비' };
const COST_KEY: Record<Metric, keyof Pick<RegimenDrug, 'dailyCost' | 'monthlyCost' | 'yearlyCost'>> =
  { daily: 'dailyCost', monthly: 'monthlyCost', yearly: 'yearlyCost' };

const fmt = (n: number | null | undefined) => n == null ? '—' : '₩' + Math.round(n).toLocaleString();
const today = () => new Date().toISOString().slice(0, 10);

function emptyRegimen(name: string): Regimen { return { name, drugs: [] }; }
function drugKey(d: RegimenDrug): string {
  return d.source === 'weighted_avg' ? 'wap:' + (d.mainIngredientCode || '') : 'dom:' + d.insuranceCode;
}
function drugToItem(d: RegimenDrug): PriceAsOfItem {
  return d.source === 'weighted_avg'
    ? { source: 'weighted_avg', mainIngredientCode: d.mainIngredientCode, ingredientName: d.ingredient }
    : { source: 'domestic', insuranceCode: d.insuranceCode, normalizedName: d.normalizedName,
        productName: d.name, ingredient: d.ingredient };
}

export default function RegimenCostPage() {
  const [regimens, setRegimens] = useState<Regimen[]>([
    emptyRegimen('기준 레지멘'), emptyRegimen('비교 레지멘 1'),
  ]);
  const [metric, setMetric] = useState<Metric>('monthly');
  const [asOfDate, setAsOfDate] = useState<string>(today());
  const [source, setSource] = useState<PriceSource>('domestic');

  // 약제 검색
  const [addTarget, setAddTarget] = useState<number | null>(null);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<DomesticProduct[]>([]);
  const [wapResults, setWapResults] = useState<WapResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [repricing, setRepricing] = useState(false);

  // 저장
  const [saved, setSaved] = useState<RegimenComparison[]>([]);
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [compareName, setCompareName] = useState('새 비교');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const regimensRef = useRef(regimens);
  regimensRef.current = regimens;

  useEffect(() => { listRegimens().then(setSaved).catch(() => {}); }, []);

  // 검색 (소스 토글 분기)
  useEffect(() => {
    if (addTarget == null || !query.trim()) { setResults([]); setWapResults([]); return; }
    const t = setTimeout(async () => {
      setSearching(true);
      try {
        if (source === 'weighted_avg') {
          const r = await wapSearch(query.trim(), asOfDate);
          setWapResults(r.available ? (r.results || []).slice(0, 25) : []);
          if (!r.available) setMsg(r.reason || '가중평균 조회 불가');
        } else {
          setResults((await searchDomesticPriceChanges(query)).slice(0, 12));
        }
      } catch { setResults([]); setWapResults([]); }
      finally { setSearching(false); }
    }, 350);
    return () => clearTimeout(t);
  }, [query, addTarget, source, asOfDate]);

  // 기준일 변경 → 전체 약제 배치 재가격
  const repriceAll = useCallback(async (dateStr: string) => {
    const flat: { ri: number; di: number }[] = [];
    const items: PriceAsOfItem[] = [];
    regimensRef.current.forEach((r, ri) => r.drugs.forEach((d, di) => { flat.push({ ri, di }); items.push(drugToItem(d)); }));
    if (!items.length) return;
    setRepricing(true);
    try {
      const res = await priceAsOf(dateStr, items);
      setRegimens(prev => {
        const next = prev.map(r => ({ ...r, drugs: [...r.drugs] }));
        flat.forEach((f, idx) => {
          const r = res[idx]; if (!r) return;
          const d = next[f.ri]?.drugs[f.di]; if (!d) return;
          next[f.ri].drugs[f.di] = {
            ...d, currentPrice: r.price, dailyCost: r.dailyCost, monthlyCost: r.monthlyCost,
            yearlyCost: r.yearlyCost, priceDate: r.priceDate, available: r.available,
          };
        });
        return next;
      });
    } catch { setMsg('재가격 실패'); } finally { setRepricing(false); }
  }, []);

  useEffect(() => {
    const t = setTimeout(() => { repriceAll(asOfDate); }, 400);
    return () => clearTimeout(t);
  }, [asOfDate, repriceAll]);

  const pushDrug = (ri: number, drug: RegimenDrug) => {
    setRegimens(prev => prev.map((r, i) => i === ri ? { ...r, drugs: [...r.drugs, drug] } : r));
    setQuery(''); setResults([]); setWapResults([]);
  };

  const addDomestic = async (p: DomesticProduct) => {
    const ri = addTarget; if (ri == null) return;
    if (regimens[ri].drugs.length >= MAX_DRUGS) { setMsg(`레지멘당 최대 ${MAX_DRUGS}개`); return; }
    if (regimens[ri].drugs.some(d => drugKey(d) === 'dom:' + p.insuranceCode)) { setMsg('이미 추가된 약제'); return; }
    const [r] = await priceAsOf(asOfDate, [{
      source: 'domestic', insuranceCode: p.insuranceCode, normalizedName: p.normalizedName,
      productName: p.fullProductName, ingredient: p.ingredient, codes: p.mergedCodes,
    }]);
    pushDrug(ri, {
      insuranceCode: p.insuranceCode, name: p.productName, ingredient: p.ingredient,
      currentPrice: r?.price ?? p.currentPrice, dailyCost: r?.dailyCost ?? null,
      monthlyCost: r?.monthlyCost ?? null, yearlyCost: r?.yearlyCost ?? null,
      source: 'domestic', normalizedName: p.normalizedName, priceDate: r?.priceDate,
      available: r?.available ?? true,
    });
  };

  const addWap = async (w: WapResult) => {
    const ri = addTarget; if (ri == null) return;
    if (regimens[ri].drugs.length >= MAX_DRUGS) { setMsg(`레지멘당 최대 ${MAX_DRUGS}개`); return; }
    if (regimens[ri].drugs.some(d => drugKey(d) === 'wap:' + w.main_ingredient_code)) { setMsg('이미 추가된 규격'); return; }
    const [r] = await priceAsOf(asOfDate, [{
      source: 'weighted_avg', mainIngredientCode: w.main_ingredient_code, ingredientName: w.ingredient_name,
    }]);
    pushDrug(ri, {
      insuranceCode: '', name: r?.name || w.ingredient_name, ingredient: w.ingredient_name,
      currentPrice: r?.price ?? w.weighted_avg_price, dailyCost: r?.dailyCost ?? null,
      monthlyCost: r?.monthlyCost ?? null, yearlyCost: r?.yearlyCost ?? null,
      source: 'weighted_avg', mainIngredientCode: w.main_ingredient_code, priceDate: r?.priceDate,
      available: r?.available ?? true,
    });
  };

  const removeDrug = (ri: number, key: string) =>
    setRegimens(prev => prev.map((r, i) => i === ri ? { ...r, drugs: r.drugs.filter(d => drugKey(d) !== key) } : r));
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

  const payload = (): RegimenPayload => ({
    base: regimens[0], comparators: regimens.slice(1), asOfDate,
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
    setAsOfDate(c.payload.asOfDate || c.payload.snapshotDate || today());
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

  const cost = (d: RegimenDrug) => d[COST_KEY[metric]];

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900 p-6">
      <div className="max-w-[1400px] mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-1">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <i className="ri-bar-chart-box-line text-teal-600"></i>투약 비용 비교
            </h1>
            <p className="text-sm text-gray-500 mt-1">
              기준 시점의 약가(국내약가 표시가 또는 주성분 가중평균)로 레지멘(약제 2~5개) 치료비를 비교합니다.
            </p>
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

        {/* 컨트롤 바: 기준일 + 소스 토글 */}
        <div className="bg-white rounded-2xl border px-5 py-3 mb-4 flex items-center gap-5 flex-wrap">
          <label className="flex items-center gap-2 text-sm">
            <i className="ri-calendar-event-line text-teal-600"></i>
            <span className="text-gray-600">기준 시점</span>
            <input type="date" value={asOfDate} max={today()} onChange={e => setAsOfDate(e.target.value)}
              className="border rounded-lg px-2.5 py-1 text-sm" />
            {repricing && <span className="text-xs text-gray-400">재계산 중…</span>}
          </label>
          <div className="flex items-center gap-2 text-sm">
            <span className="text-gray-600">가격 소스</span>
            <div className="flex gap-1 bg-gray-100 rounded-lg p-1">
              {([['domestic', '국내약가 (브랜드)'], ['weighted_avg', '주성분 가중평균']] as [PriceSource, string][]).map(([s, lbl]) => (
                <button key={s} onClick={() => { setSource(s); setQuery(''); setResults([]); setWapResults([]); }}
                  className={`text-xs px-3 py-1 rounded-md ${source === s ? 'bg-white shadow font-semibold text-teal-700' : 'text-gray-500'}`}>
                  {lbl}</button>
              ))}
            </div>
          </div>
          <span className="text-[11px] text-gray-400 ml-auto">표시가 기준 치료비 (실거래·RSA net가 비공개)</span>
        </div>

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
          <ResponsiveContainer width="100%" height={280}>
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

        {/* 레지멘 가로 라인 */}
        <div className="space-y-3">
          {regimens.map((r, ri) => {
            const t = regimenTotals(r);
            return (
              <div key={ri} className="bg-white rounded-2xl border p-4"
                style={{ borderLeftColor: COLORS[ri % COLORS.length], borderLeftWidth: 4 }}>
                <div className="flex items-start gap-4">
                  {/* 이름 */}
                  <div className="w-44 flex-shrink-0 flex items-center gap-1.5 pt-1">
                    {ri === 0 && <span className="text-[10px] px-1.5 py-0.5 rounded bg-teal-100 text-teal-700 font-semibold">기준</span>}
                    <input value={r.name} onChange={e => renameRegimen(ri, e.target.value)}
                      className="font-semibold text-sm flex-1 min-w-0 bg-transparent outline-none border-b border-transparent focus:border-gray-300" />
                    {ri > 0 && <button onClick={() => removeRegimen(ri)} className="text-gray-300 hover:text-red-500"><i className="ri-delete-bin-line"></i></button>}
                  </div>

                  {/* 약제 칩 */}
                  <div className="flex-1 min-w-0 flex flex-wrap gap-2 items-center">
                    {r.drugs.map(d => {
                      const k = drugKey(d);
                      const isWap = d.source === 'weighted_avg';
                      return (
                        <div key={k}
                          title={`${d.ingredient}\n${isWap ? '주성분 가중평균' : '국내약가 표시가'} · ${d.priceDate || ''}${d.available === false ? ' · 해당 시점 가격 없음' : ''}`}
                          className={`group inline-flex items-center gap-2 rounded-lg border pl-2.5 pr-1.5 py-1.5 text-xs ${d.available === false ? 'border-amber-300 bg-amber-50' : isWap ? 'border-indigo-200 bg-indigo-50' : 'border-gray-200 bg-gray-50'}`}>
                          <span className="font-medium max-w-[180px] truncate">{d.name}</span>
                          {isWap && <span className="text-[9px] px-1 py-0.5 rounded bg-indigo-500/15 text-indigo-600 font-semibold">가중</span>}
                          <span className="text-gray-500">{fmt(cost(d))}</span>
                          <button onClick={() => removeDrug(ri, k)} className="text-gray-300 hover:text-red-500"><i className="ri-close-line"></i></button>
                        </div>
                      );
                    })}

                    {/* 추가 */}
                    {r.drugs.length < MAX_DRUGS && (addTarget === ri ? (
                      <div className="relative">
                        <input autoFocus value={query} onChange={e => setQuery(e.target.value)}
                          placeholder={source === 'weighted_avg' ? '영문 성분명 (예: pembrolizumab)' : '제품·성분 검색'}
                          className="text-xs border rounded-lg px-2 py-1.5 w-56 outline-none focus:border-teal-400" />
                        <button onClick={() => { setAddTarget(null); setQuery(''); }} className="ml-1 text-xs text-gray-400">닫기</button>
                        {(query.trim() && (searching || results.length > 0 || wapResults.length > 0)) && (
                          <div className="absolute z-10 mt-1 w-80 bg-white border rounded-lg shadow-lg max-h-64 overflow-auto">
                            {searching && <p className="text-xs text-gray-400 px-3 py-2">검색 중…</p>}
                            {source === 'domestic' && results.map(p => (
                              <button key={p.insuranceCode} onClick={() => addDomestic(p)}
                                className="block w-full text-left text-xs px-3 py-1.5 hover:bg-teal-50">
                                <span className="font-medium">{p.productName}</span>
                                <span className="text-gray-400"> · {fmt(p.currentPrice)}</span>
                                <span className="block text-gray-400 truncate">{p.ingredient}</span>
                              </button>
                            ))}
                            {source === 'weighted_avg' && wapResults.map(w => (
                              <button key={w.main_ingredient_code} onClick={() => addWap(w)}
                                className="block w-full text-left text-xs px-3 py-1.5 hover:bg-indigo-50">
                                <span className="font-medium">{fmt(w.weighted_avg_price)}</span>
                                <span className="text-gray-400"> · {w.main_ingredient_code}</span>
                                <span className="block text-gray-400 truncate">{w.ingredient_name}</span>
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    ) : (
                      <button onClick={() => { setAddTarget(ri); setQuery(''); setResults([]); setWapResults([]); }}
                        className="inline-flex items-center gap-1 border border-dashed rounded-lg px-3 py-1.5 text-xs text-gray-500 hover:border-teal-400 hover:text-teal-600">
                        <i className="ri-add-line"></i> 약제
                      </button>
                    ))}
                    {r.drugs.length < MIN_DRUGS && <span className="text-[11px] text-amber-600">약제 {MIN_DRUGS}개 이상</span>}
                  </div>

                  {/* 합계 */}
                  <div className="w-48 flex-shrink-0 text-right text-xs space-y-0.5 border-l pl-4">
                    <div className="flex justify-between"><span className="text-gray-400">일</span><span className="font-semibold">{fmt(t.daily)}</span></div>
                    <div className="flex justify-between"><span className="text-gray-400">월</span><span className="font-semibold">{fmt(t.monthly)}</span></div>
                    <div className="flex justify-between"><span className="text-gray-400">연</span><span className="font-bold text-teal-700">{fmt(t.yearly)}</span></div>
                    {t.hasMissing && <p className="text-amber-600 text-[10px]">일부 치료비 미상</p>}
                  </div>
                </div>
              </div>
            );
          })}

          {regimens.length < MAX_REGIMENS && (
            <button onClick={addRegimen}
              className="w-full bg-white rounded-2xl border border-dashed py-3 text-gray-400 hover:border-teal-400 hover:text-teal-600 text-sm">
              <i className="ri-add-line"></i> 비교 레지멘 추가
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
