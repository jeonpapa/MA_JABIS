import { useEffect, useState, useRef } from 'react';
import { Link } from 'react-router-dom';
import { isAdmin, getCurrentUser } from '@/utils/authUsers';
import {
  ALL_COUNTRIES,
  countCached,
  parseReferenceMg,
  resolveDrug,
  searchDomestic,
  fetchForeignCached,
  runLiveForeign,
  fetchAssumptions,
  computeScenarios,
  fetchHTA,
  exportWorkbook,
  type Assumptions,
  type ComputedScenario,
  type ForeignRow,
  type HTASummary,
  type MatchingRow,
  type ProductInfo,
  type ScenarioSpec,
} from '@/api/workbench';

type Toast = { id: number; type: 'error' | 'success' | 'info'; message: string };

const STEPS = [
  { n: 1, name: '제품 검색', desc: '국내 + 해외' },
  { n: 2, name: '매칭 확인', desc: '국가별 SKU' },
  { n: 3, name: '시나리오', desc: 'A·B·C안' },
  { n: 4, name: '조정가 산출', desc: '상한가 비교' },
  { n: 5, name: 'HTA & Export', desc: 'xlsx 다운로드' },
];

function fmtNum(n: number | null | undefined, digits = 0): string {
  if (n == null || Number.isNaN(n)) return '—';
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

function gradeBadgeStyle(grade: string): string {
  if (grade.includes('🟢')) return 'bg-[#22C55E]/20 text-[#22C55E]';
  if (grade.includes('🔴')) return 'bg-[#EF4444]/20 text-[#EF4444]';
  return 'bg-[#F59E0B]/20 text-[#F59E0B]';
}

function doseConfLabel(conf?: string | null): { text: string; cls: string } | null {
  if (!conf) return null;
  if (conf === 'parsed') return { text: '파싱', cls: 'bg-[#22C55E]/20 text-[#22C55E]' };
  if (conf === 'reference') return { text: '참조', cls: 'bg-[#F59E0B]/20 text-[#F59E0B]' };
  if (conf === 'combo') return { text: '복합제', cls: 'bg-[#EF4444]/20 text-[#EF4444]' };
  return { text: '미상', cls: 'bg-[#EF4444]/20 text-[#EF4444]' };
}

export default function WorkbenchPage() {
  const [query, setQuery] = useState('');
  const [product, setProduct] = useState<ProductInfo | null>(null);
  const [prices, setPrices] = useState<Record<string, number>>({});
  const [matching, setMatching] = useState<MatchingRow[]>([]);
  const [assumptions, setAssumptions] = useState<Assumptions | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioSpec[]>([]);
  const [computed, setComputed] = useState<ComputedScenario[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [htaSummary, setHtaSummary] = useState<HTASummary | null>(null);
  const [searching, setSearching] = useState(false);
  const [liveScraping, setLiveScraping] = useState(false);
  const [computing, setComputing] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [searchStatus, setSearchStatus] = useState<string>('검색어를 입력하세요');
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [editIdx, setEditIdx] = useState<number>(-1);
  const [modalOpen, setModalOpen] = useState(false);
  const toastIdRef = useRef(0);

  useEffect(() => {
    fetchAssumptions().then(setAssumptions).catch(() => {
      showToast('가정치 로드 실패 (기본값 사용)', 'error');
    });
  }, []);

  const showToast = (message: string, type: Toast['type'] = 'error') => {
    const id = ++toastIdRef.current;
    setToasts(t => [...t, { id, type, message }]);
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 3500);
  };

  const resetDownstream = () => {
    setMatching([]);
    setPrices({});
    setScenarios([]);
    setComputed([]);
    setSelected(null);
    setHtaSummary(null);
  };

  const runSearch = async () => {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setSearchStatus(`검색 중… ${q}`);
    resetDownstream();
    try {
      const [resolved, dom] = await Promise.all([
        resolveDrug(q),
        searchDomestic(q, 5).catch(() => ({ results: [] })),
      ]);
      let foreign: { results: Record<string, ForeignRow[]> } | null = null;
      try { foreign = await fetchForeignCached(q); } catch { /* ignore */ }
      if (!countCached(foreign) && resolved.ingredient && resolved.ingredient.toLowerCase() !== q.toLowerCase()) {
        try { foreign = await fetchForeignCached(resolved.ingredient); } catch { /* ignore */ }
      }

      const firstDom = (dom.results || [])[0] as any;
      const firstProduct = (resolved.products || [])[0] || firstDom?.product_name_kr || resolved.query;
      const info: ProductInfo = {
        drug_name_en: resolved.ingredient || '',
        drug_name_kr: firstProduct || '',
        ingredient: resolved.ingredient || '',
        sku: firstDom?.dosage_strength || '100mg / 1 vial',
        atc: firstDom?.atc_code || '',
        manufacturer: firstDom?.manufacturer || '',
      };
      setProduct(info);
      setSearchStatus(`✓ 해석됨 — 성분명: ${resolved.ingredient || '—'} · 국내 제품: ${firstProduct}`);
      applyForeign(foreign, info);
    } catch (e: any) {
      setSearchStatus(`검색 실패: ${e?.message || e}`);
      showToast(`검색 실패: ${e?.message || e}`, 'error');
    } finally {
      setSearching(false);
    }
  };

  const applyForeign = (foreign: { results: Record<string, ForeignRow[]> } | null, info: ProductInfo) => {
    const newMatching: MatchingRow[] = [];
    const newPrices: Record<string, number> = {};
    const ing = (info.ingredient || query).toLowerCase();
    const results = foreign?.results || {};

    for (const [country, rows] of Object.entries(results)) {
      if (!rows?.length) continue;
      const row = rows[0];
      const nameLc = (row.product_name || '').toLowerCase();
      const grade = row.local_price && ing && nameLc.includes(ing)
        ? '🟢' : row.local_price ? '🟡' : '🔴';
      newMatching.push({
        country,
        source: row.source_site || row.source_url || 'scraper',
        product_name: row.product_name || '',
        form: row.form || '',
        strength: row.strength || '',
        pack: row.pack || '',
        raw_price: row.local_price ?? null,
        currency: row.currency || '',
        grade,
        searched_at: row.searched_at,
      });
      if (row.local_price) newPrices[country] = row.local_price;
    }

    setMatching(newMatching);
    setPrices(newPrices);

    if (Object.keys(newPrices).length === 0) return;

    const scList: ScenarioSpec[] = seedScenarios(Object.keys(newPrices));
    setScenarios(scList);
    setSelected(scList[0]?.name || null);
    runCompute(scList, newPrices, info);
  };

  const seedScenarios = (countries: string[]): ScenarioSpec[] => [
    { name: 'A안 (전체)',        include_countries: countries,                          formula: 'min_n', percent: 0.90, notes: '전체 국가 포함, 최저 × 90%' },
    { name: 'B안 (IT 제외)',     include_countries: countries.filter(c => c !== 'IT'),  formula: 'min_n', percent: 0.90, notes: 'IT 환율 변동성 고려 제외' },
    { name: 'C안 (평균 × 85%)',  include_countries: countries,                          formula: 'avg_n', percent: 0.85, notes: '평균 기준, 보수적 85%' },
  ];

  const runCompute = async (
    scList: ScenarioSpec[] = scenarios,
    priceMap: Record<string, number> = prices,
    info: ProductInfo | null = product,
  ) => {
    if (!scList.length || !Object.keys(priceMap).length) return;
    setComputing(true);
    try {
      const rows_meta: Record<string, { product_name?: string; strength?: string; pack?: string; form?: string }> = {};
      for (const m of matching.length ? matching : []) {
        rows_meta[m.country] = { product_name: m.product_name, strength: m.strength, pack: m.pack, form: m.form };
      }
      const product_slug = (info?.drug_name_en || query || '').toLowerCase();
      const reference_mg = parseReferenceMg(info?.sku);
      const body = {
        prices: priceMap,
        scenarios: scList,
        assumptions: assumptions || undefined,
        rows_meta,
        product_slug,
        reference_mg,
      };
      const r = await computeScenarios(body);
      setComputed(r.scenarios);
      setHtaSummary(r.hta_summary);
      if (!selected) setSelected(scList[0]?.name || null);
    } catch (e: any) {
      showToast(`계산 실패: ${e?.message || e}`);
    } finally {
      setComputing(false);
    }
  };

  const onLiveScrape = async () => {
    const q = product?.drug_name_en || query;
    if (!q) return;
    setLiveScraping(true);
    setSearchStatus(`Live 스크래핑 중 (7개국 동시)… ${q}`);
    try {
      const r = await runLiveForeign(q);
      if (r.error) { showToast(`스크래핑 실패: ${r.error}`); return; }
      const cached = await fetchForeignCached(q);
      applyForeign(cached, product || {
        drug_name_en: q, drug_name_kr: '', ingredient: q, sku: '100mg / 1 vial', atc: '', manufacturer: '',
      });
      setSearchStatus(`✓ Live 스크래핑 완료 — ${q}`);
    } catch (e: any) {
      showToast(`스크래핑 실패: ${e?.message || e}`);
    } finally {
      setLiveScraping(false);
    }
  };

  const selectedSc = computed.find(s => s.name === selected) || computed[0];
  const selectedSpec = scenarios.find(s => s.name === selected) || scenarios[0];

  const onLoadHTA = async () => {
    const p = product?.drug_name_en || product?.drug_name_kr || query;
    if (!p) { showToast('먼저 제품을 검색하세요'); return; }
    try {
      const r = await fetchHTA(p);
      setHtaSummary(r.summary);
      showToast('HTA 교차검증 로드 완료', 'success');
    } catch (e: any) {
      showToast(`HTA 로드 실패: ${e?.message || e}`);
      setHtaSummary(null);
    }
  };

  const onExport = async () => {
    if (!selected || !computed.length) { showToast('시나리오를 먼저 선정하세요'); return; }
    setExporting(true);
    try {
      const session = {
        project: {
          project_name: `${product?.drug_name_kr || query} 협상 시나리오`,
          drug_name_en: product?.drug_name_en || '',
          drug_name_kr: product?.drug_name_kr || '',
          manufacturer: product?.manufacturer || '',
          atc: product?.atc || '',
          sku: product?.sku || '',
          neg_type: '약가 조정',
          author: 'Workbench User',
          date: new Date().toISOString().slice(0, 10),
          version: 'v1.0',
        },
        prices,
        scenarios: computed,
        selected,
        source_raw: matching.map(m => ({
          country: m.country, site: m.source, url: '',
          fetched_at: new Date().toISOString(), query, product_id: '',
          raw_price: m.raw_price, currency: m.currency, note: m.grade,
        })),
        matching: matching.map(m => ({
          country: m.country, source: m.source,
          product_name: m.product_name, form: m.form,
          strength: m.strength, pack: m.pack,
          kr_reference: product?.drug_name_kr || '',
          grade: m.grade,
        })),
        hta: null,
        audit_log: [{
          timestamp: new Date().toISOString(),
          user: 'dashboard', sheet: 'Export',
          field: 'selected_scenario', old: '', new: selected,
          reason: 'xlsx export 실행',
        }],
        assumptions: assumptions || undefined,
      } as any;
      const { blob, filename } = await exportWorkbook(session);
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click();
      setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 100);
      showToast(`xlsx 다운로드 완료 — ${filename}`, 'success');
    } catch (e: any) {
      showToast(`Export 실패: ${e?.message || e}`);
    } finally {
      setExporting(false);
    }
  };

  const onRemoveScenario = (idx: number) => {
    if (!confirm('삭제하시겠어요?')) return;
    const next = scenarios.filter((_, i) => i !== idx);
    setScenarios(next);
    if (selected && !next.find(s => s.name === selected)) setSelected(next[0]?.name || null);
    runCompute(next);
  };

  const completed = computed.length ? 4 : Object.keys(prices).length ? 2 : matching.length ? 1 : 0;
  const active = computed.length ? 5 : Object.keys(prices).length ? 3 : matching.length ? 2 : 1;

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      {/* Header */}
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-5 h-5 flex items-center justify-center"><i className="ri-scales-3-line text-[#00E5CC]"></i></span>
              <h1 className="text-2xl font-bold text-white">Negotiation Workbench</h1>
            </div>
            <p className="text-[#8B9BB4] text-sm">제품 검색 → 매칭 확인 → 시나리오 빌더 → 조정가 산출 → HTA & xlsx Export</p>
          </div>
          {isAdmin(getCurrentUser()) && (
            <Link
              to="/admin/workbench-settings"
              className="flex items-center gap-2 bg-[#161B27] border border-[#1E2530] hover:border-[#00E5CC]/50 text-[#8B9BB4] hover:text-[#00E5CC] text-sm font-semibold px-4 py-2 rounded-lg cursor-pointer transition-colors"
              title="HIRA 가정치 편집 (admin only)"
            >
              <i className="ri-settings-3-line"></i>
              가정치 설정
            </Link>
          )}
        </div>
      </div>

      <div className="px-8 py-6 space-y-5">
        {/* Stepper */}
        <div className="bg-[#161B27] border border-[#1E2530] rounded-2xl px-5 py-4 flex items-center gap-0 overflow-x-auto">
          {STEPS.map((s, i) => {
            const isComplete = s.n <= completed;
            const isActive = s.n === active;
            return (
              <div key={s.n} className="flex items-center gap-2 flex-1 min-w-0">
                <div
                  className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold flex-shrink-0 ${
                    isComplete ? 'bg-[#22C55E] text-white' :
                    isActive   ? 'bg-[#00E5CC] text-[#0A0E1A] ring-4 ring-[#00E5CC]/20' :
                                 'bg-[#1E2530] text-[#4A5568]'
                  }`}
                >
                  {isComplete ? '✓' : s.n}
                </div>
                <div className="flex flex-col min-w-0">
                  <span className={`text-xs font-semibold whitespace-nowrap ${isActive ? 'text-[#00E5CC]' : isComplete ? 'text-white' : 'text-[#4A5568]'}`}>{s.name}</span>
                  <span className="text-[10px] text-[#4A5568] whitespace-nowrap truncate">{s.desc}</span>
                </div>
                {i < STEPS.length - 1 && (
                  <div className={`flex-1 h-0.5 mx-2 ${isComplete ? 'bg-[#22C55E]' : 'bg-[#1E2530]'}`} />
                )}
              </div>
            );
          })}
        </div>

        {/* Step 1. 검색 */}
        <section className="bg-[#161B27] border border-[#1E2530] rounded-2xl p-5">
          <div className="flex items-center gap-2 mb-3">
            <div className="w-7 h-7 rounded-full bg-[#00E5CC] text-[#0A0E1A] flex items-center justify-center text-xs font-bold">1</div>
            <h2 className="text-base font-bold text-white">제품 검색</h2>
            <span className="text-[#8B9BB4] text-xs">국내 약가 DB + 해외 캐시</span>
          </div>
          <div className="flex gap-2 mb-3">
            <input
              type="text"
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) runSearch(); }}
              placeholder="제품명 또는 성분명 (예: 키트루다 / pembrolizumab)"
              className="flex-1 bg-[#0D1117] border border-[#1E2530] rounded-lg px-4 py-2.5 text-white text-sm placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50"
            />
            <button
              onClick={runSearch}
              disabled={searching || !query.trim()}
              className="bg-[#00E5CC] text-[#0A0E1A] text-sm font-bold px-5 py-2.5 rounded-lg cursor-pointer hover:bg-[#00C9B1] transition-colors disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
            >
              {searching ? '검색 중…' : '검색'}
            </button>
          </div>
          <div className={`text-xs px-3 py-2 rounded-lg ${Object.keys(prices).length ? 'bg-[#00E5CC]/10 text-[#00E5CC]' : 'bg-[#1E2530] text-[#8B9BB4]'}`}>
            {searchStatus}
          </div>
        </section>

        {/* Step 2. 매칭 */}
        {(matching.length > 0 || (product && !searching)) && (
          <section className="bg-[#161B27] border border-[#1E2530] rounded-2xl p-5">
            <div className="flex items-center gap-2 mb-3">
              <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold ${completed >= 2 ? 'bg-[#22C55E] text-white' : 'bg-[#00E5CC] text-[#0A0E1A]'}`}>
                {completed >= 2 ? '✓' : '2'}
              </div>
              <h2 className="text-base font-bold text-white">데이터 매칭 확인</h2>
              <span className="text-[#8B9BB4] text-xs">국가별 SKU + 현지가격</span>
            </div>
            {matching.length === 0 ? (
              <div className="text-center py-8 bg-[#0D1117] rounded-lg border border-dashed border-[#1E2530]">
                <p className="text-[#8B9BB4] text-sm mb-3">해외 약가 캐시 없음 — Live 스크래핑으로 7개국 동시 조회</p>
                <button
                  onClick={onLiveScrape}
                  disabled={liveScraping}
                  className="bg-[#7C3AED] text-white text-sm font-bold px-5 py-2 rounded-lg cursor-pointer hover:bg-[#6D28D9] transition-colors disabled:opacity-50"
                >
                  {liveScraping ? '스크래핑 중… (30초~2분)' : 'Live 스크래핑 실행'}
                </button>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-[#8B9BB4] border-b border-[#1E2530]">
                      <th className="text-left py-2 px-3 font-semibold">국가</th>
                      <th className="text-left py-2 px-3 font-semibold">소스</th>
                      <th className="text-left py-2 px-3 font-semibold">제품명</th>
                      <th className="text-left py-2 px-3 font-semibold">용량</th>
                      <th className="text-right py-2 px-3 font-semibold">현지가격</th>
                      <th className="text-left py-2 px-3 font-semibold">통화</th>
                      <th className="text-left py-2 px-3 font-semibold">일관성</th>
                    </tr>
                  </thead>
                  <tbody>
                    {matching.map(m => {
                      const doseRow = selectedSc?.rows?.[m.country];
                      const conf = doseConfLabel(doseRow?.dose_confidence);
                      const mgLbl = doseRow?.mg_pack_total
                        ? `${doseRow.mg_pack_total}mg`
                        : doseRow?.dose_confidence === 'combo' ? '복합제' : '—';
                      return (
                        <tr key={m.country} className="border-b border-[#1E2530]/50 hover:bg-[#0D1117]/50">
                          <td className="py-2.5 px-3 font-bold text-[#00E5CC]">{m.country}</td>
                          <td className="py-2.5 px-3 text-[#8B9BB4]">{m.source}</td>
                          <td className="py-2.5 px-3 text-white">{m.product_name || '—'}</td>
                          <td className="py-2.5 px-3 text-white">
                            {mgLbl}
                            {conf && <span className={`ml-1.5 px-1.5 py-0.5 rounded text-[10px] ${conf.cls}`}>{conf.text}</span>}
                          </td>
                          <td className="py-2.5 px-3 text-right font-mono text-white">{fmtNum(m.raw_price)}</td>
                          <td className="py-2.5 px-3 text-[#8B9BB4]">{m.currency}</td>
                          <td className="py-2.5 px-3"><span className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${gradeBadgeStyle(m.grade)}`}>{m.grade}</span></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <p className="text-[10px] text-[#4A5568] mt-2">🟢 Exact (국내와 동일 SKU) · 🟡 Strength-equivalent · 🔴 Mismatch</p>
              </div>
            )}
          </section>
        )}

        {/* Step 3. 시나리오 */}
        {scenarios.length > 0 && (
          <section className="bg-[#161B27] border border-[#1E2530] rounded-2xl p-5">
            <div className="flex items-center gap-2 mb-3">
              <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold ${computed.length ? 'bg-[#22C55E] text-white' : 'bg-[#00E5CC] text-[#0A0E1A]'}`}>
                {computed.length ? '✓' : '3'}
              </div>
              <h2 className="text-base font-bold text-white">협상 시나리오</h2>
              <span className="text-[#8B9BB4] text-xs">A·B·C안 병렬 비교</span>
              <div className="ml-auto flex gap-2">
                <button
                  onClick={() => { setEditIdx(-1); setModalOpen(true); }}
                  className="bg-[#00E5CC] text-[#0A0E1A] text-xs font-bold px-3 py-1.5 rounded-lg cursor-pointer hover:bg-[#00C9B1] transition-colors"
                >+ 시나리오 추가</button>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-4">
              {scenarios.map((s, idx) => {
                const c = computed[idx];
                const isSel = s.name === selected;
                return (
                  <div
                    key={s.name}
                    className={`rounded-xl p-4 border-2 transition-all cursor-pointer ${isSel ? 'border-[#00E5CC] bg-[#00E5CC]/5' : 'border-[#1E2530] bg-[#0D1117] hover:border-[#2A3545]'}`}
                    onClick={() => setSelected(s.name)}
                  >
                    <h3 className="text-sm font-bold text-[#00E5CC] mb-1">{s.name}</h3>
                    <p className="text-[10px] text-[#4A5568] mb-3">{c?.basis || `${s.formula} × ${Math.round(s.percent * 100)}%`}</p>
                    <div className="flex flex-wrap gap-1 mb-3">
                      {s.include_countries.map(cc => (
                        <span key={cc} className="text-[10px] px-1.5 py-0.5 bg-[#161B27] border border-[#1E2530] rounded font-semibold text-[#8B9BB4]">{cc}</span>
                      ))}
                    </div>
                    <div className="text-2xl font-bold text-white">
                      {c?.proposed_ceiling ? `₩ ${fmtNum(c.proposed_ceiling)}` : '—'}
                    </div>
                    <p className="text-[10px] text-[#4A5568] mb-3">제안 상한가</p>
                    {s.notes && <p className="text-[11px] text-[#8B9BB4] mb-3 line-clamp-2">{s.notes}</p>}
                    <div className="flex gap-1.5">
                      <button
                        onClick={e => { e.stopPropagation(); setSelected(s.name); }}
                        className={`flex-1 text-[10px] font-semibold py-1.5 rounded cursor-pointer transition-colors ${isSel ? 'bg-[#00E5CC] text-[#0A0E1A]' : 'bg-[#1E2530] text-[#8B9BB4] hover:text-white'}`}
                      >{isSel ? '✓ 선정' : '선정'}</button>
                      <button
                        onClick={e => { e.stopPropagation(); setEditIdx(idx); setModalOpen(true); }}
                        className="px-2 text-[10px] font-semibold py-1.5 rounded bg-[#1E2530] text-[#8B9BB4] hover:text-white cursor-pointer transition-colors"
                      >수정</button>
                      <button
                        onClick={e => { e.stopPropagation(); onRemoveScenario(idx); }}
                        className="px-2 text-[10px] font-semibold py-1.5 rounded bg-[#EF4444]/20 text-[#EF4444] hover:bg-[#EF4444]/30 cursor-pointer transition-colors"
                      >삭제</button>
                    </div>
                  </div>
                );
              })}
            </div>
            {computing && <p className="text-xs text-[#8B9BB4] mt-2">계산 중…</p>}
          </section>
        )}

        {/* Step 4. 조정가 상세 */}
        {selectedSc && Object.keys(selectedSc.rows || {}).length > 0 && (
          <section className="bg-[#161B27] border border-[#1E2530] rounded-2xl p-5">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-7 h-7 rounded-full bg-[#22C55E] text-white flex items-center justify-center text-xs font-bold">✓</div>
              <h2 className="text-base font-bold text-white">조정가 상세 — {selectedSc.name}</h2>
              <span className="text-[#8B9BB4] text-xs">{selectedSc.basis}</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="text-[#0A0E1A] bg-[#00E5CC]">
                    <th className="py-2 px-2 font-bold">국가</th>
                    <th className="py-2 px-2 font-bold text-right">현지가격</th>
                    <th className="py-2 px-2 font-bold text-right">환율</th>
                    <th className="py-2 px-2 font-bold text-right">KRW환산</th>
                    <th className="py-2 px-2 font-bold text-right">공장도%</th>
                    <th className="py-2 px-2 font-bold text-right">공장도(KRW)</th>
                    <th className="py-2 px-2 font-bold text-right">VAT%</th>
                    <th className="py-2 px-2 font-bold text-right">VAT적용</th>
                    <th className="py-2 px-2 font-bold text-right">마진%</th>
                    <th className="py-2 px-2 font-bold text-right">조정가(KRW)</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(selectedSc.rows).map(([country, r]) => {
                    const isMin = country === selectedSc.stats?.min_country;
                    return (
                      <tr key={country} className="border-b border-[#1E2530]/50">
                        <td className="py-2 px-2 font-bold text-[#00E5CC] text-center">{country}{isMin ? ' ⭐' : ''}</td>
                        <td className="py-2 px-2 text-right font-mono text-white">{fmtNum(r.local_price)}</td>
                        <td className="py-2 px-2 text-right font-mono text-[#8B9BB4]">{r.fx_rate?.toFixed(2)}</td>
                        <td className="py-2 px-2 text-right font-mono text-white">{fmtNum(r.krw_converted)}</td>
                        <td className="py-2 px-2 text-right text-[#8B9BB4]">{(r.factory_ratio * 100).toFixed(1)}%</td>
                        <td className="py-2 px-2 text-right font-mono text-white">{fmtNum(r.factory_krw)}</td>
                        <td className="py-2 px-2 text-right text-[#8B9BB4]">{(r.vat_rate * 100).toFixed(1)}%</td>
                        <td className="py-2 px-2 text-right font-mono text-white">{fmtNum(r.vat_applied)}</td>
                        <td className="py-2 px-2 text-right text-[#8B9BB4]">{(r.margin_rate * 100).toFixed(1)}%</td>
                        <td className="py-2 px-2 text-right font-mono text-[#22C55E] font-bold bg-[#22C55E]/10">{fmtNum(r.adjusted)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {Object.keys(selectedSc.excluded || {}).length > 0 && (
              <p className="mt-3 text-[11px] text-[#F59E0B]">
                ⊘ 제외: {Object.entries(selectedSc.excluded).map(([c, r], i) => (
                  <span key={c}>{i > 0 && ' · '}<strong>{c}</strong> ({r})</span>
                ))}
              </p>
            )}
            <div className="mt-4 flex flex-wrap gap-4 text-xs">
              <div>A8 최저: <strong className="text-white">{fmtNum(selectedSc.stats?.min)}</strong> <span className="text-[#8B9BB4]">({selectedSc.stats?.min_country || '-'})</span></div>
              <div>A8 평균: <strong className="text-white">{fmtNum(selectedSc.stats?.avg)}</strong></div>
              <div>최저 × {Math.round((selectedSc.stats?.percent || 0) * 100)}%: <strong className="text-white">{fmtNum(selectedSc.stats?.min_percent)}</strong></div>
              <div>평균 × {Math.round((selectedSc.stats?.percent || 0) * 100)}%: <strong className="text-white">{fmtNum(selectedSc.stats?.avg_percent)}</strong></div>
              <div className="ml-auto text-[#00E5CC] font-bold">제안 상한가: ₩ {fmtNum(selectedSc.proposed_ceiling)} ({selectedSc.basis})</div>
            </div>
          </section>
        )}

        {/* Step 5. HTA + Export */}
        {computed.length > 0 && (
          <section className="bg-[#161B27] border border-[#1E2530] rounded-2xl p-5">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-7 h-7 rounded-full bg-[#00E5CC] text-[#0A0E1A] flex items-center justify-center text-xs font-bold">5</div>
              <h2 className="text-base font-bold text-white">HTA Matrix</h2>
              <span className="text-[#8B9BB4] text-xs">Tier-3 다중-LLM 교차검증</span>
              <button
                onClick={onLoadHTA}
                className="ml-auto bg-[#7C3AED]/20 border border-[#7C3AED]/50 text-[#A78BFA] text-xs font-semibold px-3 py-1.5 rounded-lg cursor-pointer hover:bg-[#7C3AED]/30 transition-colors"
              >HTA 데이터 로드</button>
            </div>
            {htaSummary?.agencies?.length ? (
              <>
                <p className="text-xs text-[#8B9BB4] mb-3">
                  총 <strong className="text-white">{htaSummary.total_fields}</strong> 필드 · 합의 <strong className="text-[#22C55E]">{htaSummary.agree}</strong> · 충돌 <strong className="text-[#EF4444]">{htaSummary.conflict}</strong>
                </p>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  {htaSummary.agencies.map(a => (
                    <div key={a.code} className="bg-[#0D1117] border border-[#1E2530] rounded-lg p-3">
                      <p className="text-sm font-bold text-[#00E5CC]">{a.name}</p>
                      <p className="text-[10px] text-[#4A5568] mb-2">{a.country || ''} · <code>{a.code}</code></p>
                      <div className="flex flex-wrap gap-1">
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-[#22C55E]/20 text-[#22C55E]">✅ {a.agree}</span>
                        {a.conflict ? <span className="text-[10px] px-2 py-0.5 rounded-full bg-[#EF4444]/20 text-[#EF4444]">❌ {a.conflict}</span> : null}
                        {a.single ? <span className="text-[10px] px-2 py-0.5 rounded-full bg-[#F59E0B]/20 text-[#F59E0B]">⚠ {a.single}</span> : null}
                        {a.narrative ? <span className="text-[10px] px-2 py-0.5 rounded-full bg-[#7C3AED]/20 text-[#A78BFA]">📝 {a.narrative}</span> : null}
                        {a.missing ? <span className="text-[10px] px-2 py-0.5 rounded-full bg-[#1E2530] text-[#8B9BB4]">∅ {a.missing}</span> : null}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <div className="text-center py-6 text-[#4A5568] text-xs">
                HTA 캐시 없음 — 신규 제품은 Tier-3 LLM 교차검증 캐시 생성 후 자동 attach 됩니다.
              </div>
            )}
          </section>
        )}

        {/* Export Bar */}
        {computed.length > 0 && (
          <div className="sticky bottom-4 bg-[#161B27] border border-[#00E5CC]/30 rounded-2xl px-5 py-3 flex items-center gap-3 shadow-2xl">
            <div className="flex-1 text-sm">
              선정: <strong className="text-[#00E5CC]">{selected || '—'}</strong>
              {selectedSc?.proposed_ceiling && (
                <span className="ml-3 text-[#00E5CC] font-bold">₩ {fmtNum(selectedSc.proposed_ceiling)}</span>
              )}
              <span className="ml-2 text-[#4A5568] text-xs">{selectedSc?.basis}</span>
            </div>
            <button
              onClick={onExport}
              disabled={exporting}
              className="bg-[#00E5CC] text-[#0A0E1A] text-sm font-bold px-5 py-2 rounded-lg cursor-pointer hover:bg-[#00C9B1] transition-colors disabled:opacity-50 whitespace-nowrap"
            >
              {exporting ? '생성 중…' : 'xlsx Export'}
            </button>
          </div>
        )}
      </div>

      {/* Scenario Modal */}
      {modalOpen && (
        <ScenarioModal
          existing={editIdx >= 0 ? scenarios[editIdx] : null}
          assumptions={assumptions}
          availableCountries={Object.keys(prices)}
          onClose={() => setModalOpen(false)}
          onSave={spec => {
            let next: ScenarioSpec[];
            if (editIdx >= 0) {
              next = scenarios.map((s, i) => i === editIdx ? spec : s);
            } else {
              next = [...scenarios, spec];
            }
            setScenarios(next);
            if (!selected) setSelected(spec.name);
            setModalOpen(false);
            runCompute(next);
          }}
        />
      )}

      {/* Toasts */}
      <div className="fixed bottom-24 right-6 space-y-2 z-50">
        {toasts.map(t => (
          <div
            key={t.id}
            className={`px-4 py-3 rounded-lg shadow-2xl text-xs max-w-sm border-l-4 bg-[#161B27] text-white ${
              t.type === 'success' ? 'border-[#22C55E]' : t.type === 'info' ? 'border-[#00E5CC]' : 'border-[#EF4444]'
            }`}
          >
            {t.message}
          </div>
        ))}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Scenario Modal (add/edit)
// ═══════════════════════════════════════════════════════════════════
function ScenarioModal({
  existing, assumptions, availableCountries, onClose, onSave,
}: {
  existing: ScenarioSpec | null;
  assumptions: Assumptions | null;
  availableCountries: string[];
  onClose: () => void;
  onSave: (spec: ScenarioSpec) => void;
}) {
  const [name, setName] = useState(existing?.name || `시나리오 ${Date.now() % 1000}`);
  const [formula, setFormula] = useState<'min_n' | 'avg_n'>(existing?.formula || 'min_n');
  const [percent, setPercent] = useState(Math.round((existing?.percent || 0.9) * 100));
  const [countries, setCountries] = useState<string[]>(existing?.include_countries || availableCountries);
  const [notes, setNotes] = useState(existing?.notes || '');

  const toggleCountry = (c: string) => {
    setCountries(prev => prev.includes(c) ? prev.filter(x => x !== c) : [...prev, c]);
  };

  const submit = () => {
    if (!name.trim()) { alert('이름 필수'); return; }
    if (!countries.length) { alert('최소 1개 국가 포함'); return; }
    onSave({
      name: name.trim(),
      include_countries: countries,
      formula,
      percent: percent / 100,
      notes: notes.trim() || undefined,
    });
  };

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-[#161B27] border border-[#1E2530] rounded-2xl p-6 w-[520px] max-w-[90vw]" onClick={e => e.stopPropagation()}>
        <h3 className="text-lg font-bold text-[#00E5CC] mb-4">{existing ? '시나리오 수정' : '시나리오 추가'}</h3>

        <div className="space-y-3">
          <div>
            <label className="text-xs text-[#8B9BB4] font-semibold block mb-1">이름 *</label>
            <input
              value={name} onChange={e => setName(e.target.value)}
              className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-[#00E5CC]/50"
            />
          </div>

          <div>
            <label className="text-xs text-[#8B9BB4] font-semibold block mb-1">포함 국가</label>
            <div className="flex flex-wrap gap-1.5">
              {ALL_COUNTRIES.map(c => {
                const disabled = assumptions?.countries?.[c]?.phase === 2;
                const checked = countries.includes(c) && !disabled;
                return (
                  <label key={c} className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border text-xs cursor-pointer transition-colors ${
                    disabled ? 'bg-[#0D1117] border-[#1E2530] text-[#4A5568] cursor-not-allowed' :
                    checked  ? 'bg-[#00E5CC]/20 border-[#00E5CC] text-[#00E5CC]' :
                               'bg-[#0D1117] border-[#1E2530] text-[#8B9BB4] hover:border-[#2A3545]'
                  }`}>
                    <input
                      type="checkbox" checked={checked} disabled={disabled}
                      onChange={() => !disabled && toggleCountry(c)}
                      className="hidden"
                    />
                    {c}{disabled ? ' (P2)' : ''}
                  </label>
                );
              })}
            </div>
          </div>

          <div>
            <label className="text-xs text-[#8B9BB4] font-semibold block mb-1">제안가 공식</label>
            <div className="flex gap-4 items-center text-xs">
              <label className="inline-flex items-center gap-1.5 cursor-pointer text-white">
                <input type="radio" name="formula" checked={formula === 'min_n'} onChange={() => setFormula('min_n')} />
                최저 × N%
              </label>
              <label className="inline-flex items-center gap-1.5 cursor-pointer text-white">
                <input type="radio" name="formula" checked={formula === 'avg_n'} onChange={() => setFormula('avg_n')} />
                평균 × N%
              </label>
              <label className="inline-flex items-center gap-1.5 ml-auto text-white">
                N% =
                <input
                  type="number" min={1} max={100} value={percent}
                  onChange={e => setPercent(Number(e.target.value))}
                  className="w-16 bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-white text-xs text-right"
                />
              </label>
            </div>
          </div>

          <div>
            <label className="text-xs text-[#8B9BB4] font-semibold block mb-1">메모</label>
            <textarea
              value={notes} onChange={e => setNotes(e.target.value)} rows={2}
              className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-xs focus:outline-none focus:border-[#00E5CC]/50"
              placeholder="예: IT 환율 변동성 커서 제외"
            />
          </div>
        </div>

        <div className="flex justify-end gap-2 mt-5">
          <button onClick={onClose} className="px-4 py-2 text-xs text-[#8B9BB4] hover:text-white cursor-pointer">취소</button>
          <button onClick={submit} className="bg-[#00E5CC] text-[#0A0E1A] text-xs font-bold px-5 py-2 rounded-lg cursor-pointer hover:bg-[#00C9B1]">저장</button>
        </div>
      </div>
    </div>
  );
}
