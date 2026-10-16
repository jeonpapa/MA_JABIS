import { useEffect, useRef, useState } from 'react';
import { downloadPriceHistory, fetchOecdPrices, fetchPriceCatalog } from '@/api/priceRequests';
import type { OecdPrice } from '@/api/priceRequests';

const tiers: Record<string, string> = { ex_factory: '공장도가 (EXF)', pharmacy_purchase: '약국구입가 (PPP)', pharmacy_retail: '약국판매가', reimbursement: '상환가', other: '기타' };
const vat: Record<string, string> = { included: '포함', excluded: '제외', unknown: '미확인' };
const amount = (n?: number | null) => n != null && Number.isFinite(n) && n > 0 ? n.toLocaleString('ko-KR', { maximumFractionDigits: 4 }) : '미확정';
const safeLink = (value: string) => /^https?:\/\//i.test(value || '') ? value : undefined;

export default function OecdPrices({ query = '', revision = 0, isDark = false }: { query?: string; revision?: number; isDark?: boolean }) {
  const [draft, setDraft] = useState(query);
  const [search, setSearch] = useState(query);
  const [retry, setRetry] = useState(0);
  const [rows, setRows] = useState<OecdPrice[]>([]);
  const [labels, setLabels] = useState<Record<string, string>>({});
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [resultQuery, setResultQuery] = useState('');
  const [downloadBusy, setDownloadBusy] = useState(false);
  const [downloadError, setDownloadError] = useState<{ query: string; message: string } | null>(null);
  const downloadingRef = useRef(false);
  useEffect(() => { setDraft(query); setSearch(query); }, [query]);
  useEffect(() => {
    let active = true;
    fetchPriceCatalog().then(c => { if (active) setLabels(Object.fromEntries(c.oecd.map(x => [x.code, x.label]))); }).catch(() => { /* country codes remain visible */ });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    let active = true;
    setRows([]); setError(''); setResultQuery(''); setDownloadError(null);
    if (!search.trim()) { setLoading(false); return; }
    setLoading(true);
    fetchOecdPrices(search.trim()).then(result => { if (active) { setRows(result); setResultQuery(search.trim()); } })
      .catch(e => { if (active) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [search, revision, retry]);
  const downloadResults = async () => {
    if (downloadingRef.current || loading || rows.length === 0 || resultQuery !== search.trim()) return;
    const exportQuery = resultQuery;
    downloadingRef.current = true; setDownloadBusy(true); setDownloadError(null);
    try { await downloadPriceHistory('oecd', exportQuery); }
    catch (error) { setDownloadError({ query: exportQuery, message: error instanceof Error ? error.message : String(error) }); }
    finally { downloadingRef.current = false; setDownloadBusy(false); }
  };
  const shell = isDark ? 'bg-[#161B27] border-[#1E2530] text-gray-100' : 'bg-white border-gray-200 text-gray-900';
  return <section className={'rounded-2xl border p-5 space-y-4 ' + shell} aria-label="OECD·공단 추가국가 가격">
    <div><h2 className="font-bold">OECD·공단 추가국가 공개 가격</h2>
      <p className="text-xs opacity-70 mt-1">공단 A8 외 29개국 기준이며 대만·싱가포르 등 OECD 비회원국을 포함합니다. 원문 가격단계·VAT·포장을 보존합니다. A8 조정가나 임의 환율 환산은 적용하지 않습니다.</p></div>
    <form onSubmit={e => { e.preventDefault(); setSearch(draft.trim()); setRetry(v => v + 1); }} className="flex gap-2">
      <input aria-label="추가국가 캐시 검색어" value={draft} onChange={e => setDraft(e.target.value)} placeholder="제품명 또는 성분명으로 저장된 가격 조회" className="flex-1 min-w-0 rounded-lg border px-3 py-2 text-sm bg-transparent" />
      <button type="submit" className="rounded-lg bg-teal-600 text-white px-4 py-2 text-sm">캐시 조회</button>
    </form>
    {!loading && !error && rows.length > 0 && resultQuery === search.trim() && <div className="space-y-1">
      <button type="button" onClick={() => void downloadResults()} disabled={downloadBusy} aria-busy={downloadBusy}
        className="rounded-lg border border-teal-600 text-teal-600 px-3 py-2 text-sm disabled:opacity-50 disabled:cursor-not-allowed">
        {downloadBusy ? '다운로드 중…' : 'Excel 다운로드'}
      </button>
      <p className="text-xs opacity-70">현재 검색어 “{resultQuery}”의 최신 저장 캐시 기준입니다. 요청 당시 결과 스냅샷이 아닙니다.</p>
    </div>}
    {downloadError?.query === search.trim() && <p role="alert" className="text-sm text-red-500">다운로드 실패: {downloadError.message}</p>}
    {loading ? <p className="text-sm opacity-70">가격 로딩 중…</p>
      : error ? <p role="alert" className="text-sm text-red-500">가격 조회 실패: {error} <button onClick={() => setRetry(v => v + 1)} className="underline">재시도</button></p>
      : !search ? <p className="text-sm opacity-70">제품을 선택하거나 검색어를 입력하세요. 조사 요청은 위 패널에서 별도로 접수합니다.</p>
      : rows.length === 0 ? <p className="text-sm opacity-70">저장된 공개 가격이 없습니다. 미허가를 뜻하지 않습니다. 위 패널에서 국가를 선택해 조사를 요청하세요.</p>
      : <div className="space-y-3">{rows.map(row => <article key={row.id} className="rounded-lg border p-4 text-xs space-y-2">
        <div className="flex justify-between gap-2 flex-wrap"><h3 className="font-semibold text-sm">{labels[row.country] || row.country} · {row.product_name}</h3><span>근거 등급 {row.grade}</span></div>
        <p>{row.ingredient} · {row.dosage_strength} · {row.dosage_form}</p>
        <p>포장: {row.package_unit || '미확인'} · 포장 수량 {row.pack_count > 0 ? row.pack_count : '미확인'} · 포장 총함량 {row.total_pkg_mg > 0 ? row.total_pkg_mg + ' mg' : '미확인'}</p>
        <p className="font-semibold text-teal-600">원문 가격: {amount(row.local_price)} {row.local_price > 0 ? row.currency : ''} · {tiers[row.price_tier] || row.price_tier}</p>
        <p>원문 가격단계: {row.price_tier_original || '미확인'} · VAT: {vat[row.vat_status] || '미확인'}</p>
        {(row.ex_factory_price != null || row.pharmacy_purchase_price != null) && <p>공장도가 (EXF): {amount(row.ex_factory_price)} {row.ex_factory_price > 0 ? row.currency : ''} · 약국구입가 (PPP): {amount(row.pharmacy_purchase_price)} {row.pharmacy_purchase_price > 0 ? row.currency : ''}</p>}
        {row.derivation_note && <p className="opacity-80">단계 변환 근거: {row.derivation_note}</p>}
        {row.normalized_price > 0 && <p>서버 제공 함량 정규화가: {amount(row.normalized_price)} {row.currency} · 기준 {row.reference_mg > 0 ? row.reference_mg + ' mg' : '미확인'}</p>}
        <p>적용일: {row.effective_date || '미확인'} · 관찰일: {row.observed_at || '미확인'}</p>
        <p className="break-all">출처: {safeLink(row.source_url) ? <a href={safeLink(row.source_url)} target="_blank" rel="noopener noreferrer" className="text-teal-600 underline">{row.source_label || row.source_url}</a> : row.source_label || '미확인'} · 증빙: {row.evidence_ref || '미확인'}</p>
      </article>)}</div>}
  </section>;
}
