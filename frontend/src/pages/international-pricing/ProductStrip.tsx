import { useMemo, useRef, useState } from 'react';
import type { ForeignDrugListItem } from '@/api/foreign';

/**
 * 해외 약가 '제품 파일' — 검색 이력을 가로 카드 스트립으로 보여준다 (2026-11-03 사용자 승인 개선안).
 * 높이는 한 줄로 고정되고 제품이 늘면 옆으로 스크롤한다. 선택 카드는 위로 들려 아래 상세 패널과 이어진다.
 * 카드: 이름·성분, A8 8칸(가격 있음 / 캐시만·미확정 / 없음), 가격 있는 국가 수·최근 확인일, 기준 변경·가격 변동·오래됨·조사 중 배지.
 */
export const A8_CODES = ['JP', 'IT', 'FR', 'CH', 'UK', 'DE', 'CA', 'US'] as const;

type SortKey = 'recent' | 'name' | 'changed';

interface Props {
  items: ForeignDrugListItem[];
  selectedQuery: string | null;
  onSelect: (item: ForeignDrugListItem) => void;
  /** 진행 중 요청이 있는 제품 queryName(소문자) */
  activeQueries: Set<string>;
  onNew: () => void;
  newOpen: boolean;
  activeRequestCount: number;
  isDark: boolean;
  loading?: boolean;
}

const displayName = (q: string) => (q ? q.charAt(0).toUpperCase() + q.slice(1) : q);
const mmdd = (iso: string) => (iso && iso.length >= 10 ? `${iso.slice(5, 7)}.${iso.slice(8, 10)}` : iso || '—');

export default function ProductStrip({ items, selectedQuery, onSelect, activeQueries, onNew, newOpen, activeRequestCount, isDark, loading }: Props) {
  const [filter, setFilter] = useState('');
  const [sort, setSort] = useState<SortKey>('recent');
  const stripRef = useRef<HTMLDivElement>(null);

  const list = useMemo(() => {
    const q = filter.trim().toLowerCase();
    let out = q
      ? items.filter(d => d.queryName.toLowerCase().includes(q) || d.canonical.toLowerCase().includes(q) || d.aliases.some(a => a.toLowerCase().includes(q)))
      : [...items];
    if (sort === 'name') out = [...out].sort((a, b) => a.queryName.localeCompare(b.queryName));
    else if (sort === 'changed') out = [...out].sort((a, b) => (b.changeCounts.price * 10 + b.changeCounts.basis) - (a.changeCounts.price * 10 + a.changeCounts.basis));
    else out = [...out].sort((a, b) => (b.lastSearchedAt || '').localeCompare(a.lastSearchedAt || ''));
    return out;
  }, [items, filter, sort]);

  const t = isDark
    ? {
      main: 'text-white', sub: 'text-[#8B9BB4]', muted: 'text-[#4A5568]',
      card: 'bg-[#161B27] border-[#1E2530] hover:border-[#2A3545]', sel: 'bg-[#0F2A2A] border-[#00E5CC]/60',
      field: 'bg-[#0D1117] border-[#1E2530] text-white placeholder-[#4A5568]', seg: 'bg-[#1E2530]', segOn: 'bg-[#0D1117] text-white', segOff: 'text-[#8B9BB4]',
      priced: 'bg-[#00E5CC]/20 text-[#00E5CC] border-transparent', unpriced: 'border-dashed border-[#8B9BB4] text-[#C9D4E3]', none: 'border-[#1E2530] text-[#4A5568]',
      basis: 'bg-amber-400/15 text-amber-300', price: 'bg-rose-400/15 text-rose-300', stale: 'border border-red-400/50 text-red-300', active: 'bg-sky-400/15 text-sky-300',
      nav: 'bg-[#161B27] border-[#1E2530] text-white', addCard: 'border-[#1E2530] text-[#8B9BB4] hover:text-white', notch: 'bg-[#0F2A2A] border-[#00E5CC]/60',
    }
    : {
      main: 'text-gray-900', sub: 'text-gray-500', muted: 'text-gray-400',
      card: 'bg-white border-gray-200 hover:border-gray-300', sel: 'bg-teal-50 border-teal-400',
      field: 'bg-white border-gray-200 text-gray-900 placeholder-gray-400', seg: 'bg-gray-100', segOn: 'bg-white text-gray-900 shadow-sm', segOff: 'text-gray-500',
      priced: 'bg-teal-100 text-teal-800 border-transparent', unpriced: 'border-dashed border-gray-400 text-gray-700', none: 'border-gray-200 text-gray-400',
      basis: 'bg-amber-100 text-amber-800', price: 'bg-rose-100 text-rose-700', stale: 'border border-red-300 text-red-600', active: 'bg-sky-100 text-sky-700',
      nav: 'bg-white border-gray-200 text-gray-700', addCard: 'border-gray-300 text-gray-500 hover:text-gray-800', notch: 'bg-teal-50 border-teal-400',
    };

  const scrollBy = (dx: number) => stripRef.current?.scrollBy({ left: dx, behavior: 'smooth' });
  const onKey = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === 'ArrowRight') { e.preventDefault(); (e.currentTarget.parentElement?.nextElementSibling?.querySelector('button[data-card]') as HTMLButtonElement | null)?.focus(); }
    if (e.key === 'ArrowLeft') { e.preventDefault(); (e.currentTarget.parentElement?.previousElementSibling?.querySelector('button[data-card]') as HTMLButtonElement | null)?.focus(); }
  };

  return (
    <section aria-label="해외약가 제품 파일 (검색 이력)" className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className={`w-4 h-4 flex items-center justify-center ${t.sub}`}><i className="ri-folders-line text-sm"></i></span>
        <h2 className={`text-sm font-semibold ${t.main}`}>제품 파일</h2>
        <span className={`text-xs ${t.sub}`}>{loading ? '불러오는 중…' : `${list.length}개 제품`}</span>
        <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="제품·성분 필터" aria-label="제품·성분 필터"
          className={`ml-2 w-40 text-xs rounded-lg border px-2.5 py-1.5 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 ${t.field}`} />
        <div role="group" aria-label="정렬" className={`flex gap-0.5 rounded-lg p-0.5 ${t.seg}`}>
          {([['recent', '최근 확인'], ['name', '이름'], ['changed', '변경 있음']] as const).map(([k, label]) => (
            <button key={k} type="button" aria-pressed={sort === k} onClick={() => setSort(k)}
              className={`text-xs px-2.5 py-1 rounded-md cursor-pointer ${sort === k ? t.segOn : t.segOff}`}>{label}</button>
          ))}
        </div>
        <button type="button" onClick={onNew} aria-expanded={newOpen}
          className="ml-auto flex items-center gap-1.5 bg-teal-600 text-white text-xs font-semibold px-3 py-1.5 rounded-lg cursor-pointer hover:bg-teal-700 transition-colors">
          <i className={newOpen ? 'ri-close-line' : 'ri-add-line'}></i>{newOpen ? '새 조사 닫기' : '새 조사'}
          {activeRequestCount > 0 && <span className="ml-1 rounded-full bg-white/25 px-1.5 text-[10px]">진행 {activeRequestCount}</span>}
        </button>
      </div>

      <div className="relative">
        <button type="button" aria-label="왼쪽으로" onClick={() => scrollBy(-440)}
          className={`absolute -left-3 top-[58px] z-10 w-7 h-7 rounded-full border flex items-center justify-center cursor-pointer ${t.nav}`}><i className="ri-arrow-left-s-line"></i></button>
        <div ref={stripRef} className="flex gap-2.5 overflow-x-auto pt-3 pb-4 px-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {list.map(d => {
            const sel = selectedQuery?.toLowerCase() === d.queryName.toLowerCase();
            const pricedCount = A8_CODES.filter(c => d.countryStatus[c]?.status === 'priced').length;
            const busy = activeQueries.has(d.queryName.toLowerCase());
            return (
              <div key={d.id} className="relative flex-none">
                <button type="button" data-card onClick={() => onSelect(d)} onKeyDown={onKey}
                  aria-current={sel ? 'true' : undefined} aria-label={`${displayName(d.queryName)} 열기`}
                  className={`w-[204px] text-left rounded-xl border px-3 pt-2.5 pb-2 flex flex-col gap-1.5 cursor-pointer transition-transform duration-200 motion-reduce:transition-none focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 ${sel ? `${t.sel} -translate-y-1.5` : `${t.card} hover:-translate-y-1`}`}>
                  <span className={`text-sm font-semibold truncate ${t.main}`}>{displayName(d.queryName)}</span>
                  <span className={`text-[11px] -mt-1 truncate ${t.sub}`}>{d.canonical !== d.queryName ? d.canonical : '\u00a0'}</span>
                  <span className="grid grid-cols-8 gap-0.5" aria-label="A8 국가 상태">
                    {A8_CODES.map(c => {
                      const st = d.countryStatus[c]?.status;
                      const cls = st === 'priced' ? t.priced : st === 'unpriced' ? t.unpriced : t.none;
                      const title = st === 'priced' ? `${c} 가격 있음 · 확인 ${d.countryStatus[c]?.checkedAt ?? ''}` : st === 'unpriced' ? `${c} 캐시만·가격 미확정 · 확인 ${d.countryStatus[c]?.checkedAt ?? ''}` : `${c} 미수집`;
                      return <span key={c} title={title} className={`h-4 rounded-[3px] border text-[8.5px] font-mono flex items-center justify-center ${cls}`}>{c}</span>;
                    })}
                  </span>
                  <span className="flex items-center gap-1 flex-wrap text-[10.5px]">
                    <span className={t.sub}>{pricedCount}/8 · {mmdd(d.lastSearchedAt)}</span>
                    {d.changeCounts.price > 0 && <span className={`rounded px-1.5 leading-4 ${t.price}`}>가격 변동 {d.changeCounts.price}</span>}
                    {d.changeCounts.basis > 0 && <span className={`rounded px-1.5 leading-4 ${t.basis}`} title="출처·가격 단계가 바뀐 국가 수 — 가격 인하가 아님">기준 변경 {d.changeCounts.basis}</span>}
                    {d.staleCountries.length > 0 && <span className={`rounded px-1.5 leading-4 ${t.stale}`} title={`30일 넘게 확인 안 함: ${d.staleCountries.join(', ')}`}>오래됨 {d.staleCountries.length}</span>}
                    {busy && <span className={`rounded px-1.5 leading-4 ${t.active}`}><i className="ri-loader-4-line animate-spin motion-reduce:animate-none"></i> 조사 중</span>}
                  </span>
                </button>
                {sel && <span aria-hidden="true" className={`absolute left-1/2 -bottom-[3px] w-3 h-3 -ml-1.5 rotate-45 border-r border-b ${t.notch}`} />}
              </div>
            );
          })}
          <div className="flex-none">
            <button type="button" onClick={onNew}
              className={`w-[120px] h-full min-h-[112px] rounded-xl border border-dashed text-xs flex flex-col items-center justify-center gap-1 cursor-pointer ${t.addCard}`}>
              <i className="ri-add-line text-base"></i>새 조사
            </button>
          </div>
        </div>
        <button type="button" aria-label="오른쪽으로" onClick={() => scrollBy(440)}
          className={`absolute -right-3 top-[58px] z-10 w-7 h-7 rounded-full border flex items-center justify-center cursor-pointer ${t.nav}`}><i className="ri-arrow-right-s-line"></i></button>
      </div>
      {!loading && items.length > 0 && list.length === 0 && <p className={`text-xs ${t.sub}`}>필터와 일치하는 제품이 없습니다.</p>}
    </section>
  );
}
