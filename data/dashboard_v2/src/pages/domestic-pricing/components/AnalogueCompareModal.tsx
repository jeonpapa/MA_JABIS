import { useEffect, useMemo, useRef, useState } from 'react';
import { searchAnalogues, DomesticAnalogue } from '@/api/domestic';

interface Props {
  open: boolean;
  onClose: () => void;
  baseProduct: { name: string; price: number; dailyCost: number | null };
  baseInsuranceCode: string;
  analogues: DomesticAnalogue[];
  selected: string[];
  onToggle: (name: string) => void;
  onAddExternal: (a: DomesticAnalogue) => void;
}

export default function AnalogueCompareModal({
  open, onClose, baseProduct, baseInsuranceCode,
  analogues, selected, onToggle, onAddExternal,
}: Props) {
  const [query, setQuery] = useState('');
  const [remoteHits, setRemoteHits] = useState<DomesticAnalogue[]>([]);
  const [remoteLoading, setRemoteLoading] = useState(false);
  const [remoteError, setRemoteError] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return analogues;
    return analogues.filter(a =>
      a.name.toLowerCase().includes(q) ||
      (a.ingredient || '').toLowerCase().includes(q) ||
      (a.company || '').toLowerCase().includes(q),
    );
  }, [analogues, query]);

  // 원격 검색 — 로컬에 매칭되는 게 부족하거나 성분이 달라도 찾을 수 있도록
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const q = query.trim();
    if (q.length < 2) {
      setRemoteHits([]);
      setRemoteLoading(false);
      setRemoteError(null);
      return;
    }
    setRemoteLoading(true);
    setRemoteError(null);
    debounceRef.current = setTimeout(async () => {
      try {
        const hits = await searchAnalogues(q, baseInsuranceCode);
        const localNames = new Set(analogues.map(a => a.name));
        setRemoteHits(hits.filter(h => !localNames.has(h.name)));
      } catch (e: any) {
        setRemoteError(e?.message || '검색 실패');
        setRemoteHits([]);
      } finally {
        setRemoteLoading(false);
      }
    }, 350);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, analogues, baseInsuranceCode]);

  if (!open) return null;

  const maxReached = selected.length >= 2;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60" onClick={onClose}></div>
      <div className="relative bg-[#161B27] border border-[#2A3545] rounded-2xl w-full max-w-lg mx-4 p-6 shadow-2xl max-h-[85vh] flex flex-col">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-white font-bold text-base">아날로그 약제 선택</h3>
          <button onClick={onClose} className="w-7 h-7 flex items-center justify-center text-[#8B9BB4] hover:text-white cursor-pointer transition-colors">
            <i className="ri-close-line text-lg"></i>
          </button>
        </div>
        <p className="text-[#8B9BB4] text-xs mb-3">
          최대 2개까지 선택 (기준 약제 포함 3개). <span className="text-[#4A5568]">성분이 달라도 검색으로 추가 가능.</span>
        </p>

        {/* Search */}
        <div className="flex items-center gap-2 bg-[#0D1117] border border-[#1E2530] rounded-xl px-3 py-2 mb-3">
          <i className={remoteLoading ? 'ri-loader-4-line animate-spin text-[#00E5CC] text-sm' : 'ri-search-line text-[#4A5568] text-sm'}></i>
          <input
            type="text"
            autoFocus
            placeholder="제품명 · 성분명 · 제조사 (2자 이상 입력 시 전체 DB 검색)"
            value={query}
            onChange={e => setQuery(e.target.value)}
            className="bg-transparent text-white text-sm placeholder-[#4A5568] focus:outline-none flex-1"
          />
          {query && (
            <button onClick={() => setQuery('')} className="w-5 h-5 flex items-center justify-center text-[#4A5568] hover:text-white cursor-pointer">
              <i className="ri-close-line text-sm"></i>
            </button>
          )}
        </div>

        {/* Base product */}
        <div className="bg-[#00E5CC]/10 border border-[#00E5CC]/30 rounded-xl p-3 mb-3 flex-shrink-0">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-[#00E5CC] text-xs font-semibold mb-0.5">기준 약제</p>
              <p className="text-white text-sm font-bold">{baseProduct.name}</p>
            </div>
            <div className="text-right">
              <p className="text-white text-sm font-bold">₩{baseProduct.price.toLocaleString()}</p>
              {baseProduct.dailyCost && (
                <p className="text-[#8B9BB4] text-xs">일치료비 ₩{baseProduct.dailyCost.toLocaleString()}</p>
              )}
            </div>
          </div>
        </div>

        {/* Analogues (local + selected externals) */}
        <div className="space-y-2 flex-1 overflow-y-auto">
          {filtered.length > 0 && (
            <div className="text-[10px] text-[#4A5568] uppercase tracking-wider px-1">검색 결과 내 · {filtered.length}건</div>
          )}
          {filtered.length === 0 && !remoteLoading && remoteHits.length === 0 && (
            <div className="text-center py-6 text-[#4A5568] text-xs">
              {analogues.length === 0 ? '동일 성분 비교 약제가 없습니다' : `"${query}" 에 해당하는 약제가 없습니다`}
            </div>
          )}
          {filtered.map((a) => {
            const isSelected = selected.includes(a.name);
            const isDisabled = !isSelected && maxReached;
            return (
              <button
                key={a.name}
                onClick={() => !isDisabled && onToggle(a.name)}
                disabled={isDisabled}
                className={`w-full text-left rounded-xl p-3 border transition-all cursor-pointer ${
                  isSelected
                    ? 'bg-[#7C3AED]/10 border-[#7C3AED]/40'
                    : isDisabled
                    ? 'bg-[#1E2530] border-[#1E2530] opacity-40 cursor-not-allowed'
                    : 'bg-[#1E2530] border-[#1E2530] hover:border-[#2A3545]'
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className={`w-4 h-4 rounded flex items-center justify-center flex-shrink-0 ${isSelected ? 'bg-[#7C3AED]' : 'border border-[#4A5568]'}`}>
                      {isSelected && <i className="ri-check-line text-white text-xs"></i>}
                    </div>
                    <div>
                      <p className="text-white text-sm font-medium">{a.name}</p>
                      <p className="text-[#8B9BB4] text-xs">{a.ingredient} · {a.company}</p>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="text-white text-sm font-semibold">₩{a.price.toLocaleString()}</p>
                    {a.dailyCost && (
                      <p className="text-[#8B9BB4] text-xs">일치료비 ₩{a.dailyCost.toLocaleString()}</p>
                    )}
                  </div>
                </div>
              </button>
            );
          })}

          {remoteHits.length > 0 && (
            <>
              <div className="text-[10px] text-[#00E5CC] uppercase tracking-wider px-1 pt-3 border-t border-[#1E2530] mt-3">
                DB 검색 결과 · {remoteHits.length}건 (성분 무관)
              </div>
              {remoteHits.map(a => {
                const isDisabled = maxReached;
                return (
                  <button
                    key={`ext-${a.name}`}
                    onClick={() => !isDisabled && onAddExternal(a)}
                    disabled={isDisabled}
                    className={`w-full text-left rounded-xl p-3 border transition-all ${
                      isDisabled
                        ? 'bg-[#1E2530] border-[#1E2530] opacity-40 cursor-not-allowed'
                        : 'bg-[#0D1117] border-dashed border-[#00E5CC]/40 hover:border-[#00E5CC] hover:bg-[#00E5CC]/5 cursor-pointer'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="w-4 h-4 flex items-center justify-center text-[#00E5CC] flex-shrink-0">
                          <i className="ri-add-circle-line text-sm"></i>
                        </span>
                        <div>
                          <p className="text-white text-sm font-medium">{a.name}</p>
                          <p className="text-[#8B9BB4] text-xs">{a.ingredient || '성분 미상'} · {a.company || '—'}</p>
                        </div>
                      </div>
                      <div className="text-right">
                        <p className="text-white text-sm font-semibold">₩{a.price.toLocaleString()}</p>
                        {a.dailyCost && (
                          <p className="text-[#8B9BB4] text-xs">일치료비 ₩{a.dailyCost.toLocaleString()}</p>
                        )}
                      </div>
                    </div>
                  </button>
                );
              })}
            </>
          )}

          {remoteError && (
            <p className="text-red-400 text-xs text-center py-2">{remoteError}</p>
          )}
        </div>

        <button
          onClick={onClose}
          className="mt-4 w-full bg-[#00E5CC] text-[#0A0E1A] font-semibold text-sm py-2.5 rounded-xl cursor-pointer hover:bg-[#00C9B1] transition-colors whitespace-nowrap flex-shrink-0"
        >
          선택 완료
        </button>
      </div>
    </div>
  );
}
