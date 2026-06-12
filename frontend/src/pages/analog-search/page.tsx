import { useEffect, useMemo, useState } from 'react';
import { useApi } from '@/hooks/useApi';
import {
  fetchAnalogFacets, searchAnalog, fetchAnalogDetail, generateAnalogBrief,
  FACET_LABELS, type AnalogReport, type AnalogSearchResult, type AnalogBrief,
} from '@/api/analog';

const FACET_KEYS = ['disease_category', 'cancer_type', 'line_of_therapy', 'committee',
  'review_result', 'reimbursement_track', 'coverage_gap_type'];

const RESULT_KR: Record<string, string> = {
  APPROVED: '통과', CONDITIONAL_APPROVED: '조건부', REJECTED: '미설정', None: '-',
};
const GAP_STYLE: Record<string, string> = {
  '축소': 'bg-orange-50 text-orange-600 border-orange-200',
  '확대': 'bg-emerald-50 text-emerald-600 border-emerald-200',
  '구체화': 'bg-sky-50 text-sky-600 border-sky-200',
  '동일': 'bg-gray-100 text-gray-500 border-gray-200',
  '비교불가': 'bg-gray-50 text-gray-400 border-gray-200',
};

export default function AnalogSearchPage() {
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [semantic, setSemantic] = useState('');
  const [fts, setFts] = useState('');
  const [data, setData] = useState<AnalogSearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<AnalogReport | null>(null);
  const [brief, setBrief] = useState<AnalogBrief | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);

  const facets = useApi(fetchAnalogFacets, []);

  const runSearch = async () => {
    setLoading(true); setBrief(null);
    try {
      const res = await searchAnalog({ filters, fts: fts.trim() || undefined, semantic: semantic.trim() || undefined, limit: 60 });
      setData(res);
    } catch (e) { console.error(e); setData(null); }
    finally { setLoading(false); }
  };

  // 초기 1회 (최신 패싯 없이 = 최신 60건)
  useEffect(() => { runSearch(); /* eslint-disable-next-line */ }, []);

  const setFacet = (k: string, v: string) =>
    setFilters(prev => { const n = { ...prev }; if (v) n[k] = v; else delete n[k]; return n; });

  const results = data?.results ?? [];
  const briefIds = useMemo(() => results.slice(0, 10).map(r => r.id), [results]);

  const makeBrief = async () => {
    if (!results.length) return;
    setBriefLoading(true);
    try {
      const ctx = [semantic, ...Object.values(filters)].filter(Boolean).join(' · ');
      setBrief(await generateAnalogBrief(briefIds, ctx));
    } catch (e) { setBrief({ brief: '', cited_ids: [], error: String(e) }); }
    finally { setBriefLoading(false); }
  };

  const openDetail = async (id: number) => {
    try { setSelected(await fetchAnalogDetail(id)); } catch (e) { console.error(e); }
  };

  return (
    <div className="min-h-screen bg-gray-50 text-gray-900">
      <div className="px-8 pt-8 pb-5 border-b border-gray-200 bg-white">
        <div className="flex items-center gap-2 mb-1">
          <i className="ri-search-eye-line text-teal-600 text-xl"></i>
          <h1 className="text-2xl font-bold">등재 아날로그 검색</h1>
        </div>
        <p className="text-gray-500 text-sm">약평위·암질심 537개 평가 사례 — 유사 신약이 어떻게 평가·등재됐나 (허가↔급여 갭·재심의 trajectory 포함)</p>
      </div>

      <div className="px-8 py-6 space-y-5">
        {/* 검색 컨트롤 */}
        <div className="rounded-2xl border border-gray-200 bg-white p-5 space-y-4">
          {/* 패싯 드롭다운 */}
          <div className="grid grid-cols-7 gap-2">
            {FACET_KEYS.map(k => (
              <div key={k}>
                <label className="text-[10px] font-semibold text-gray-400 uppercase">{FACET_LABELS[k]}</label>
                <select value={filters[k] ?? ''} onChange={e => setFacet(k, e.target.value)}
                  className="w-full mt-0.5 text-xs border border-gray-200 rounded-lg px-2 py-2 bg-gray-50 focus:outline-none focus:border-teal-300">
                  <option value="">전체</option>
                  {(facets.data?.[k] ?? []).map(o => (
                    <option key={o.value} value={o.value}>
                      {(RESULT_KR[o.value] ?? o.value)} ({o.count})
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>
          {/* 텍스트 검색 */}
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="text-[10px] font-semibold text-gray-400 uppercase">시맨틱 (신약 프로파일 서술)</label>
              <input value={semantic} onChange={e => setSemantic(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && runSearch()}
                placeholder="예: EGFR 변이 비소세포폐암 표적치료제, PD-1 면역항암제 1차"
                className="w-full mt-0.5 text-sm border border-gray-200 rounded-lg px-3 py-2 bg-gray-50 focus:outline-none focus:border-teal-300" />
            </div>
            <div className="w-64">
              <label className="text-[10px] font-semibold text-gray-400 uppercase">키워드 (FTS)</label>
              <input value={fts} onChange={e => setFts(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && runSearch()}
                placeholder="본문 키워드"
                className="w-full mt-0.5 text-sm border border-gray-200 rounded-lg px-3 py-2 bg-gray-50 focus:outline-none focus:border-teal-300" />
            </div>
            <div className="flex items-end gap-2">
              <button onClick={runSearch}
                className="bg-teal-600 text-white text-sm font-bold px-5 py-2 rounded-lg hover:bg-teal-700 transition-colors whitespace-nowrap">
                <i className="ri-search-line mr-1"></i>검색
              </button>
              <button onClick={() => { setFilters({}); setSemantic(''); setFts(''); }}
                className="text-gray-500 text-xs px-3 py-2 rounded-lg hover:bg-gray-100">초기화</button>
            </div>
          </div>
        </div>

        {/* 결과 헤더 + 브리프 버튼 */}
        <div className="flex items-center justify-between">
          <p className="text-sm text-gray-500">
            {loading ? '검색 중…' : `${results.length}건`}
            {data?.mode === 'semantic' && <span className="ml-2 text-teal-600 text-xs">시맨틱 유사도 정렬</span>}
          </p>
          {results.length > 0 && (
            <button onClick={makeBrief} disabled={briefLoading}
              className="flex items-center gap-1.5 bg-white border border-teal-300 text-teal-700 text-xs font-bold px-4 py-2 rounded-lg hover:bg-teal-50 transition-colors disabled:opacity-60">
              <i className={briefLoading ? 'ri-loader-4-line animate-spin' : 'ri-lightbulb-flash-line'}></i>
              {briefLoading ? '브리프 생성 중…' : '아날로그 브리프 생성 (상위 10건)'}
            </button>
          )}
        </div>

        {/* 브리프 */}
        {brief && (
          <div className="rounded-2xl border border-teal-200 bg-teal-50/60 p-5">
            {brief.error ? <p className="text-sm text-red-500">브리프 실패: {brief.error}</p> : (
              <>
                <div className="flex items-center gap-2 mb-2">
                  <i className="ri-lightbulb-flash-line text-teal-600"></i>
                  <h3 className="font-bold text-sm text-teal-700">아날로그 전략 브리프</h3>
                  {brief.cached && <span className="text-[10px] text-gray-400">캐시</span>}
                </div>
                <div className="text-sm text-gray-700 whitespace-pre-line leading-relaxed">{brief.brief}</div>
                <p className="text-[10px] text-gray-400 mt-2">근거 사례 {brief.cited_ids.length}건 — 본문 [사례 N] 인용은 위 표 순서</p>
              </>
            )}
          </div>
        )}

        {/* 결과 표 */}
        <div className="rounded-2xl border border-gray-200 bg-white overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200 text-left text-gray-500">
                  <th className="px-3 py-2.5 font-semibold">#</th>
                  <th className="px-3 py-2.5 font-semibold">약제</th>
                  <th className="px-3 py-2.5 font-semibold">급여 적응증</th>
                  <th className="px-3 py-2.5 font-semibold">결과</th>
                  <th className="px-3 py-2.5 font-semibold">트랙·RSA</th>
                  <th className="px-3 py-2.5 font-semibold">허가↔급여</th>
                  <th className="px-3 py-2.5 font-semibold">재심의</th>
                  <th className="px-3 py-2.5 font-semibold">차수일</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => (
                  <tr key={r.id} onClick={() => openDetail(r.id)}
                    className="border-b border-gray-100 last:border-0 hover:bg-teal-50/40 cursor-pointer">
                    <td className="px-3 py-2.5 text-gray-400 tabular-nums">{i + 1}</td>
                    <td className="px-3 py-2.5">
                      <div className="font-bold text-gray-900">{r.brand_name ?? '(약제명 미상)'}</div>
                      <div className="text-gray-400">{r.generic_name ?? ''}{r.similarity != null ? ` · 유사도 ${r.similarity}` : ''}</div>
                    </td>
                    <td className="px-3 py-2.5 text-gray-600 max-w-[220px] truncate" title={r.disease_name ?? ''}>
                      {r.disease_name ?? '—'}
                      {r.line_of_therapy && <span className="ml-1 text-gray-400">· {r.line_of_therapy}</span>}
                    </td>
                    <td className="px-3 py-2.5">
                      <span className={`px-1.5 py-0.5 rounded font-semibold ${
                        r.review_result === 'APPROVED' ? 'text-emerald-600 bg-emerald-50'
                        : r.review_result === 'CONDITIONAL_APPROVED' ? 'text-amber-600 bg-amber-50'
                        : r.review_result === 'REJECTED' ? 'text-red-500 bg-red-50' : 'text-gray-400'}`}>
                        {RESULT_KR[r.review_result ?? 'None'] ?? r.review_result}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-gray-600">
                      {r.reimbursement_track && r.reimbursement_track !== 'null' ? r.reimbursement_track : '—'}
                      {r.rsa_types.length > 0 && <span className="ml-1 text-violet-500">· {r.rsa_types.join(',')}</span>}
                    </td>
                    <td className="px-3 py-2.5">
                      {r.coverage_gap_type ? (
                        <span className={`px-1.5 py-0.5 rounded-full border font-semibold ${GAP_STYLE[r.coverage_gap_type] ?? 'bg-gray-50 text-gray-400 border-gray-200'}`}>
                          {r.coverage_gap_type}
                        </span>
                      ) : <span className="text-gray-300">—</span>}
                    </td>
                    <td className="px-3 py-2.5 text-gray-600">
                      {r.requeue_count != null && r.requeue_count > 0
                        ? <span className="text-orange-500">{r.requeue_count}회 재심의</span>
                        : <span className="text-gray-400">—</span>}
                    </td>
                    <td className="px-3 py-2.5 text-gray-400 tabular-nums whitespace-nowrap">
                      {r.session_date}{r.ordinal ? ` ·${r.ordinal}차` : ''}
                    </td>
                  </tr>
                ))}
                {!loading && results.length === 0 && (
                  <tr><td colSpan={8} className="px-3 py-10 text-center text-gray-400">조건에 맞는 사례 없음</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* 상세 모달 — 허가↔급여 나란히 */}
      {selected && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
          onClick={e => { if (e.target === e.currentTarget) setSelected(null); }}>
          <div className="bg-white rounded-2xl border border-gray-200 w-full max-w-3xl max-h-[88vh] overflow-y-auto shadow-xl">
            <div className="sticky top-0 bg-white border-b border-gray-100 px-6 py-4 flex items-start justify-between z-10">
              <div>
                <h2 className="text-xl font-bold">{selected.brand_name ?? '(약제명 미상)'}</h2>
                <p className="text-sm text-gray-500">{selected.generic_name} · {selected.manufacturer} · {selected.session_date} {selected.committee} {selected.ordinal}차</p>
              </div>
              <button onClick={() => setSelected(null)} className="w-9 h-9 flex items-center justify-center rounded-lg hover:bg-gray-100 text-gray-400">
                <i className="ri-close-line text-lg"></i>
              </button>
            </div>
            <div className="px-6 py-5 space-y-4">
              {/* 핵심 메타 */}
              <div className="grid grid-cols-4 gap-2 text-xs">
                {[['심의결과', RESULT_KR[selected.review_result ?? 'None'] ?? selected.review_result],
                  ['등재트랙', selected.reimbursement_track], ['RSA', selected.rsa_types.join(', ') || '—'],
                  ['재심의', selected.requeue_count != null ? `${selected.requeue_count}회` : '—']].map(([l, v]) => (
                  <div key={l} className="bg-gray-50 border border-gray-100 rounded-lg p-2.5">
                    <p className="text-[10px] text-gray-400">{l}</p>
                    <p className="font-bold text-gray-900">{v || '—'}</p>
                  </div>
                ))}
              </div>
              {/* 허가 ↔ 급여 적응증 */}
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg border border-gray-200 p-3">
                  <p className="text-[11px] font-bold text-gray-500 mb-1">식약처 허가 적응증 {selected.mfds_permit_date && <span className="text-gray-400">({selected.mfds_permit_date})</span>}</p>
                  <p className="text-xs text-gray-700 whitespace-pre-line max-h-48 overflow-y-auto">{selected.mfds_effect_text || '허가 정보 미수집'}</p>
                </div>
                <div className="rounded-lg border border-gray-200 p-3">
                  <p className="text-[11px] font-bold text-gray-500 mb-1">급여 승인 적응증</p>
                  <p className="text-xs text-gray-700">{selected.disease_name || '—'}</p>
                </div>
              </div>
              {/* 갭 분류 */}
              {selected.coverage_gap_type && (
                <div className={`rounded-lg border p-3 ${GAP_STYLE[selected.coverage_gap_type] ?? 'bg-gray-50 border-gray-200'}`}>
                  <p className="text-xs font-bold mb-1">허가 ↔ 급여 갭: {selected.coverage_gap_type}</p>
                  <p className="text-xs leading-relaxed">{selected.coverage_gap_evidence}</p>
                </div>
              )}
              {/* 본문 */}
              {selected.body_text && (
                <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                  <p className="text-[11px] font-bold text-gray-500 mb-1">평가 보고서 본문</p>
                  <p className="text-xs text-gray-600 whitespace-pre-line max-h-64 overflow-y-auto">{selected.body_text}</p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
