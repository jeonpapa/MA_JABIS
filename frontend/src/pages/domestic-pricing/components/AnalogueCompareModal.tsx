import { useEffect, useState } from 'react';
import { searchDomesticPriceChanges } from '@/api/domestic';
import type { DomesticAnalogue } from '@/api/domestic';

/**
 * 아날로그 약제 선택 (2026-11-03 개선 요청 5).
 * 동일 성분 후보가 없어도(단독 등재 성분 — 전체 성분의 약 57%) 다른 약제를 직접 검색해 비교할 수 있다.
 * 아날로그는 성분이 아니라 같은 질환·같은 차수 기준으로 고르는 경우가 많으므로 성분 제한을 두지 않는다.
 */
interface Props {
  open: boolean;
  onClose: () => void;
  baseProduct: { name: string; price: number; dailyCost: number | null; insuranceCode?: string };
  /** 동일 성분 후보 */
  analogues: DomesticAnalogue[];
  /** 검색으로 추가한 다른 약제 */
  extras: DomesticAnalogue[];
  selected: string[];
  onToggle: (name: string) => void;
  /** 검색 결과에서 고른 약제 — 처음이면 추가하고 선택, 이미 있으면 선택 토글 */
  onPick: (a: DomesticAnalogue) => void;
  isDark?: boolean;
}

export default function AnalogueCompareModal({ open, onClose, baseProduct, analogues, extras, selected, onToggle, onPick, isDark = false }: Props) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState<DomesticAnalogue[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) { setQ(''); setResults([]); setError(''); }
  }, [open]);

  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) { setResults([]); setError(''); return; }
    let cancelled = false;
    const h = setTimeout(async () => {
      setLoading(true); setError('');
      try {
        const products = await searchDomesticPriceChanges(term);
        if (cancelled) return;
        setResults(products
          .filter(p => p.insuranceCode !== baseProduct.insuranceCode && p.productName !== baseProduct.name)
          .slice(0, 20)
          .map(p => ({
            name: p.productName, ingredient: p.ingredient, price: p.currentPrice, dailyCost: p.dailyCost,
            company: p.company, normalizedName: p.normalizedName, insuranceCode: p.insuranceCode,
            mergedCodes: p.mergedCodes, dosingUnconfirmed: p.dosingUnconfirmed,
          })));
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 350);
    return () => { cancelled = true; clearTimeout(h); };
  }, [q, baseProduct.insuranceCode, baseProduct.name]);

  if (!open) return null;

  const modalBg = isDark ? 'bg-[#161B27] border-[#2A3545]' : 'bg-white border-gray-200 shadow-2xl';
  const textMain = isDark ? 'text-white' : 'text-gray-900';
  const textSub = isDark ? 'text-[#8B9BB4]' : 'text-gray-500';
  const baseBg = isDark ? 'bg-[#00E5CC]/10 border-[#00E5CC]/30' : 'bg-teal-50 border-teal-300';
  const baseText = isDark ? 'text-[#00E5CC]' : 'text-teal-700';
  const itemBg = isDark ? 'bg-[#1E2530] border-[#1E2530]' : 'bg-gray-100 border-gray-200';
  const itemHover = isDark ? 'hover:border-[#2A3545]' : 'hover:border-gray-300';
  const itemSelectedBg = isDark ? 'bg-[#7C3AED]/10 border-[#7C3AED]/40' : 'bg-purple-50 border-purple-300';
  const itemDisabled = isDark ? 'bg-[#1E2530] border-[#1E2530] opacity-40 cursor-not-allowed' : 'bg-gray-100 border-gray-200 opacity-40 cursor-not-allowed';
  const checkSelected = isDark ? 'bg-[#7C3AED]' : 'bg-purple-600';
  const checkDefault = isDark ? 'border-[#4A5568]' : 'border-gray-400';
  const closeBtn = isDark ? 'text-[#8B9BB4] hover:text-white' : 'text-gray-500 hover:text-gray-900';
  const field = isDark ? 'bg-[#0D1117] border-[#2A3545] text-white placeholder-[#4A5568]' : 'bg-gray-50 border-gray-200 text-gray-900 placeholder-gray-400';

  const daily = (a: DomesticAnalogue) => a.dailyCost
    ? <p className={`text-xs ${textSub}`}>일치료비 ₩{a.dailyCost.toLocaleString()}</p>
    : a.dosingUnconfirmed ? <p className={`text-xs ${textSub}`}>용법 미확정</p> : null;

  const row = (a: DomesticAnalogue, fromSearch: boolean) => {
    const isSelected = selected.includes(a.name);
    const isDisabled = !isSelected && selected.length >= 2;
    return (
      <button key={(fromSearch ? 's:' : 'a:') + (a.insuranceCode || a.name)} type="button"
        onClick={() => { if (isDisabled) return; if (fromSearch) onPick(a); else onToggle(a.name); }}
        className={`w-full text-left rounded-xl p-3 border transition-all cursor-pointer ${isSelected ? itemSelectedBg : isDisabled ? itemDisabled : itemBg + ' ' + itemHover}`}>
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <div className={`w-4 h-4 rounded flex items-center justify-center flex-shrink-0 ${isSelected ? checkSelected : 'border ' + checkDefault}`}>
              {isSelected && <i className="ri-check-line text-white text-xs"></i>}
            </div>
            <div className="min-w-0">
              <p className={`text-sm font-medium truncate ${textMain}`}>{a.name}</p>
              <p className={`text-xs truncate ${textSub}`}>{a.ingredient || '성분 정보 없음'} · {a.company}</p>
            </div>
          </div>
          <div className="text-right flex-shrink-0">
            <p className={`text-sm font-semibold ${textMain}`}>₩{(a.price || 0).toLocaleString()}</p>
            {daily(a)}
          </div>
        </div>
      </button>
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={onClose}></div>
      <div role="dialog" aria-modal="true" aria-label="아날로그 약제 선택" className={`relative rounded-2xl w-full max-w-lg mx-4 p-6 border max-h-[85vh] flex flex-col ${modalBg}`}>
        <div className="flex items-center justify-between mb-4">
          <h3 className={`font-bold text-base ${textMain}`}>아날로그 약제 선택</h3>
          <button type="button" onClick={onClose} aria-label="닫기" className={`w-7 h-7 flex items-center justify-center cursor-pointer transition-colors ${closeBtn}`}>
            <i className="ri-close-line text-lg"></i>
          </button>
        </div>
        <p className={`${textSub} text-xs mb-4`}>최대 2개까지 선택하여 비교할 수 있습니다 (기준 약제 포함 최대 3개). 성분이 달라도 검색해서 비교할 수 있습니다.</p>

        <div className={`rounded-xl p-3 mb-3 ${baseBg}`}>
          <div className="flex items-center justify-between">
            <div>
              <p className={`text-xs font-semibold mb-0.5 ${baseText}`}>기준 약제</p>
              <p className={`text-sm font-bold ${textMain}`}>{baseProduct.name}</p>
            </div>
            <div className="text-right">
              <p className={`text-sm font-bold ${textMain}`}>₩{baseProduct.price.toLocaleString()}</p>
              {baseProduct.dailyCost && <p className={`text-xs ${textSub}`}>일치료비 ₩{baseProduct.dailyCost.toLocaleString()}</p>}
            </div>
          </div>
        </div>

        <div className="overflow-y-auto -mx-1 px-1 space-y-4">
          <div>
            <p className={`text-xs font-semibold mb-2 ${textSub}`}>다른 약제 검색</p>
            <input value={q} onChange={e => setQ(e.target.value)} placeholder="제품명 또는 성분명 (예: 키트루다, 니볼루맙)" aria-label="비교할 약제 검색"
              className={`w-full text-sm rounded-xl border px-3 py-2 mb-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-teal-400 ${field}`} />
            {loading && <p className={`text-xs ${textSub}`}>검색 중…</p>}
            {error && <p role="alert" className="text-xs text-red-500">검색 실패: {error}</p>}
            {!loading && !error && q.trim().length >= 2 && results.length === 0 && <p className={`text-xs ${textSub}`}>검색 결과가 없습니다.</p>}
            <div className="space-y-2">{results.map(a => row(a, true))}</div>
          </div>
          {extras.length > 0 && (
            <div>
              <p className={`text-xs font-semibold mb-2 ${textSub}`}>추가한 다른 약제 ({extras.length})</p>
              <div className="space-y-2">{extras.map(a => row(a, false))}</div>
            </div>
          )}
          <div>
            <p className={`text-xs font-semibold mb-2 ${textSub}`}>동일 성분 후보{analogues.length ? ` (${analogues.length})` : ''}</p>
            {analogues.length === 0
              ? <p className={`text-xs ${textSub}`}>같은 성분의 다른 등재 품목이 없습니다. 위에서 다른 약제를 검색하세요.</p>
              : <div className="space-y-2">{analogues.map(a => row(a, false))}</div>}
          </div>
        </div>

        <button type="button" onClick={onClose}
          className="mt-4 w-full bg-teal-600 text-white font-semibold text-sm py-2.5 rounded-xl cursor-pointer hover:bg-teal-700 transition-colors whitespace-nowrap">
          선택 완료
        </button>
      </div>
    </div>
  );
}
