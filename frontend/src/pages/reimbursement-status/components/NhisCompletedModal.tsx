import { useEffect, useMemo, useState } from 'react';
import { fetchNhisNegotiations, NhisNegotiation } from '@/api/reimbStatus';

type ListFilter = '' | '신규' | '확대';

// 건강보험공단 '협상 완료 내역' 검색 테이블 — NHIS 공개자료(신약/사용범위 확대)
// 와 동일한 6컬럼(제품명·제약사명·효능군·등록연월·협상결과·협상완료연월).
export default function NhisCompletedModal({ isDark, onClose }: {
  isDark: boolean;
  onClose: () => void;
}) {
  const [listType, setListType] = useState<ListFilter>('');
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [items, setItems] = useState<NhisNegotiation[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 검색어 디바운스 (300ms)
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fetchNhisNegotiations({ status: 'completed', listType, q: debouncedQ || undefined })
      .then(res => {
        if (!alive) return;
        setItems(res.items);
        setTotal(res.counts.completed);
      })
      .catch(e => { if (alive) setError(e?.message ?? '조회 실패'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [listType, debouncedQ]);

  const chips: { key: ListFilter; label: string }[] = useMemo(() => [
    { key: '', label: '전체' },
    { key: '신규', label: '신약' },
    { key: '확대', label: '사용범위 확대' },
  ], []);

  const headerCls = isDark ? 'text-[#8B9BB4] bg-[#0D1117]' : 'text-gray-500 bg-gray-50';
  const cellBorder = isDark ? 'border-[#1E2530]' : 'border-gray-100';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className={`w-full max-w-4xl max-h-[85vh] flex flex-col rounded-2xl border shadow-2xl ${
          isDark ? 'bg-[#161B27] border-[#1E2530]' : 'bg-white border-gray-200'
        }`}
        onClick={e => e.stopPropagation()}
      >
        {/* 헤더 */}
        <div className={`flex items-start justify-between gap-3 p-5 border-b ${cellBorder}`}>
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className={`w-5 h-5 flex items-center justify-center ${isDark ? 'text-emerald-400' : 'text-emerald-600'}`}>
                <i className="ri-checkbox-circle-line text-base"></i>
              </span>
              <h2 className={`font-bold text-base ${isDark ? 'text-white' : 'text-gray-900'}`}>
                협상 완료 내역
              </h2>
              <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                isDark ? 'bg-emerald-400/10 text-emerald-300 border border-emerald-400/30' : 'bg-emerald-50 text-emerald-600 border border-emerald-200'
              }`}>
                공단 공식 {total}건
              </span>
            </div>
            <p className={`text-xs ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
              국민건강보험공단 공개자료 — 협상이 타결된 약제 (신약 · 사용범위 확대)
            </p>
          </div>
          <button
            onClick={onClose}
            className={`w-8 h-8 flex items-center justify-center rounded-lg flex-shrink-0 cursor-pointer transition-colors ${
              isDark ? 'text-[#8B9BB4] hover:bg-[#1E2530]' : 'text-gray-400 hover:bg-gray-100'
            }`}
          >
            <i className="ri-close-line text-xl"></i>
          </button>
        </div>

        {/* 필터 + 검색 */}
        <div className={`flex items-center gap-2 flex-wrap p-4 border-b ${cellBorder}`}>
          <div className="flex items-center gap-1.5">
            {chips.map(c => (
              <button
                key={c.key}
                onClick={() => setListType(c.key)}
                className={`text-[11px] font-semibold px-3 py-1.5 rounded-full border transition-colors cursor-pointer ${
                  listType === c.key
                    ? (isDark ? 'bg-teal-400/15 text-teal-300 border-teal-400/40' : 'bg-teal-50 text-teal-700 border-teal-300')
                    : (isDark ? 'bg-[#0D1117] text-[#8B9BB4] border-[#1E2530] hover:border-[#2A3545]' : 'bg-white text-gray-500 border-gray-200 hover:border-gray-300')
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>
          <div className="relative flex-1 min-w-[180px]">
            <i className={`ri-search-line absolute left-3 top-1/2 -translate-y-1/2 text-sm ${isDark ? 'text-[#5A6A80]' : 'text-gray-400'}`}></i>
            <input
              value={q}
              onChange={e => setQ(e.target.value)}
              placeholder="제품명 · 제약사명 검색"
              className={`w-full pl-9 pr-3 py-1.5 rounded-lg text-xs border outline-none transition-colors ${
                isDark
                  ? 'bg-[#0D1117] border-[#1E2530] text-white placeholder-[#5A6A80] focus:border-teal-400/50'
                  : 'bg-white border-gray-200 text-gray-900 placeholder-gray-400 focus:border-teal-400'
              }`}
            />
          </div>
        </div>

        {/* 테이블 */}
        <div className="flex-1 overflow-auto">
          {loading && (
            <div className={`flex items-center justify-center gap-2 py-16 text-sm ${isDark ? 'text-[#8B9BB4]' : 'text-gray-500'}`}>
              <i className="ri-loader-4-line animate-spin text-lg"></i>불러오는 중...
            </div>
          )}
          {!loading && error && (
            <div className="text-center py-16 text-sm text-red-400">{error}</div>
          )}
          {!loading && !error && items.length === 0 && (
            <div className={`text-center py-16 text-sm ${isDark ? 'text-[#5A6A80]' : 'text-gray-400'}`}>
              조건에 맞는 협상 완료 내역이 없습니다.
            </div>
          )}
          {!loading && !error && items.length > 0 && (
            <table className="w-full text-left border-collapse">
              <thead className="sticky top-0 z-10">
                <tr className={`text-[10px] font-bold uppercase tracking-wide ${headerCls}`}>
                  <th className="px-4 py-2.5">제품명</th>
                  <th className="px-3 py-2.5">제약사명</th>
                  <th className="px-3 py-2.5">효능군</th>
                  <th className="px-3 py-2.5 whitespace-nowrap">등록연월</th>
                  <th className="px-3 py-2.5">협상결과</th>
                  <th className="px-3 py-2.5 whitespace-nowrap">협상완료연월</th>
                  <th className="px-3 py-2.5">구분</th>
                </tr>
              </thead>
              <tbody>
                {items.map(it => (
                  <tr key={it.id} className={`border-t ${cellBorder} ${
                    isDark ? 'hover:bg-[#0D1117]' : 'hover:bg-gray-50'
                  }`}>
                    <td className={`px-4 py-2.5 text-xs font-semibold ${isDark ? 'text-white' : 'text-gray-900'}`}>
                      {it.productName}
                      {it.matched && (
                        <span className={`ml-1.5 text-[9px] font-bold px-1 py-0.5 rounded align-middle ${
                          isDark ? 'bg-[#00E5CC]/15 text-[#00E5CC]' : 'bg-teal-50 text-teal-600'
                        }`}>추적</span>
                      )}
                    </td>
                    <td className={`px-3 py-2.5 text-xs ${isDark ? 'text-[#8B9BB4]' : 'text-gray-600'}`}>{it.manufacturer ?? '—'}</td>
                    <td className={`px-3 py-2.5 text-xs ${isDark ? 'text-[#8B9BB4]' : 'text-gray-600'}`}>{it.efficacyGroup ?? '—'}</td>
                    <td className={`px-3 py-2.5 text-xs whitespace-nowrap ${isDark ? 'text-[#8B9BB4]' : 'text-gray-600'}`}>{it.registeredYm ?? '—'}</td>
                    <td className={`px-3 py-2.5 text-xs ${isDark ? 'text-[#8B9BB4]' : 'text-gray-600'}`}>{it.result ?? '—'}</td>
                    <td className={`px-3 py-2.5 text-xs font-medium whitespace-nowrap ${isDark ? 'text-emerald-300' : 'text-emerald-600'}`}>{it.completedYm ?? '—'}</td>
                    <td className="px-3 py-2.5">
                      <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap ${
                        it.listType === '신규'
                          ? (isDark ? 'bg-sky-400/10 text-sky-300 border border-sky-400/30' : 'bg-sky-50 text-sky-600 border border-sky-200')
                          : (isDark ? 'bg-violet-400/10 text-violet-300 border border-violet-400/30' : 'bg-violet-50 text-violet-600 border border-violet-200')
                      }`}>
                        {it.listType === '신규' ? '신약' : '사용범위 확대'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* 푸터 */}
        <div className={`px-5 py-3 border-t text-[10px] ${cellBorder} ${isDark ? 'text-[#5A6A80]' : 'text-gray-400'}`}>
          출처: 국민건강보험공단 약가협상 공개자료 (nhis.or.kr) · 주 1회 자동 동기화
        </div>
      </div>
    </div>
  );
}
