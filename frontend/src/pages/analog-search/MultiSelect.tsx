// 등재 아날로그 필터 — 복수 선택 드롭다운 (2026-11-09 사용자 요청)
// 같은 칸에서 여러 값 = OR, 칸끼리 = AND. 바깥 클릭·ESC 로 닫힌다.
import { useEffect, useRef, useState } from 'react';

interface Option { value: string; count: number }
interface Props {
  label: string;
  options: Option[];
  selected: string[];
  onChange: (next: string[]) => void;
  renderLabel?: (v: string) => string;
}

export default function MultiSelect({ label, options, selected, onChange, renderLabel = v => v }: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
  }, [open]);

  // 선택했지만 현재 옵션 목록(상위 단계 변경으로 0건)에 없는 값도 보여 줘서 해제할 수 있게 한다
  const merged: Option[] = [...options, ...selected.filter(v => !options.some(o => o.value === v)).map(v => ({ value: v, count: 0 }))];
  const shown = q.trim() ? merged.filter(o => renderLabel(o.value).toLowerCase().includes(q.trim().toLowerCase())) : merged;
  const toggle = (v: string) => onChange(selected.includes(v) ? selected.filter(x => x !== v) : [...selected, v]);
  const summary = selected.length === 0 ? '전체' : selected.length === 1 ? renderLabel(selected[0]) : `${renderLabel(selected[0])} 외 ${selected.length - 1}`;

  return (
    <div ref={ref} className="relative">
      <label className="text-[10px] font-semibold text-gray-400 uppercase">{label}</label>
      <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open} aria-haspopup="listbox"
        title={selected.map(renderLabel).join(', ') || '전체'}
        className={`w-full mt-0.5 text-xs border rounded-lg px-2 py-2 text-left flex items-center gap-1 focus:outline-none focus:border-teal-300 ${selected.length ? 'bg-teal-50 border-teal-300 text-teal-800' : 'bg-gray-50 border-gray-200 text-gray-700'}`}>
        <span className="truncate flex-1">{summary}</span>
        {selected.length > 1 && <span className="text-[10px] bg-teal-600 text-white rounded-full px-1.5">{selected.length}</span>}
        <i className="ri-arrow-down-s-line text-gray-400"></i>
      </button>
      {open && (
        <div role="listbox" aria-multiselectable="true"
          className="absolute z-30 mt-1 w-64 max-h-72 overflow-auto rounded-xl border border-gray-200 bg-white shadow-lg p-2 space-y-1">
          {merged.length > 8 && (
            <input value={q} onChange={e => setQ(e.target.value)} placeholder="옵션 검색" aria-label={`${label} 옵션 검색`}
              className="w-full text-xs border border-gray-200 rounded-md px-2 py-1 mb-1 focus:outline-none focus:border-teal-300" />
          )}
          <div className="flex items-center justify-between px-1 pb-1 text-[11px]">
            <span className="text-gray-400">여러 개 선택 가능</span>
            {selected.length > 0 && <button type="button" onClick={() => onChange([])} className="text-teal-600 hover:underline">선택 해제</button>}
          </div>
          {shown.length === 0 && <p className="text-xs text-gray-400 px-1 py-2">옵션이 없습니다</p>}
          {shown.map(o => {
            const on = selected.includes(o.value);
            return (
              <label key={o.value} role="option" aria-selected={on}
                className={`flex items-center gap-2 text-xs rounded-md px-2 py-1.5 cursor-pointer ${on ? 'bg-teal-50 text-teal-800' : 'hover:bg-gray-50 text-gray-700'}`}>
                <input type="checkbox" checked={on} onChange={() => toggle(o.value)} className="accent-teal-600" />
                <span className="flex-1 truncate">{renderLabel(o.value)}</span>
                <span className="text-gray-400 tabular-nums">{o.count}</span>
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}
