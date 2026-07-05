import { useEffect, useState } from 'react';
import {
  listCompetitorBrands,
  createCompetitorBrand,
  updateCompetitorBrand,
  deleteCompetitorBrand,
  type CompetitorBrand,
  type CompetitorBrandInput,
} from '@/api/editableFactors';

type Draft = {
  query: string;
  company: string;
  anchor: string;
  kind: 'competitor' | 'msd_asset';
  logo: string;
  color: string;
  active: boolean;
};

const EMPTY: Draft = {
  query: '', company: '', anchor: '', kind: 'competitor', logo: '', color: '#00E5CC', active: true,
};

const inputCls = 'bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1.5 text-sm text-white placeholder-[#4A5568]';

function toInput(d: Draft): CompetitorBrandInput {
  return {
    query: d.query.trim(),
    company: d.company.trim(),
    anchor: d.anchor.trim() || null,
    kind: d.kind,
    logo: d.logo.trim() || null,
    color: d.color.trim() || null,
    active: d.active,
  };
}

function toDraft(b: CompetitorBrand): Draft {
  return {
    query: b.query,
    company: b.company,
    anchor: b.anchor ?? '',
    kind: b.kind,
    logo: b.logo ?? '',
    color: b.color ?? '#00E5CC',
    active: Boolean(b.active),
  };
}

/** ⚙ 추적 브랜드/MNC 편집 — competitor_brand 테이블 CRUD (경쟁사 + MSD 자산). */
export default function CompetitorBrandsEditor() {
  const [items, setItems] = useState<CompetitorBrand[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [editId, setEditId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<Draft>(EMPTY);
  const [editBusy, setEditBusy] = useState(false);

  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const reload = async () => {
    setLoading(true); setError(null);
    try { setItems(await listCompetitorBrands()); }
    catch (e) { setError(e instanceof Error ? e.message : '조회 실패'); }
    finally { setLoading(false); }
  };

  useEffect(() => { reload(); }, []);

  const startEdit = (b: CompetitorBrand) => { setEditId(b.id); setEditDraft(toDraft(b)); };

  const saveEdit = async () => {
    if (editId == null) return;
    if (!editDraft.query.trim() || !editDraft.company.trim()) { alert('query / company 필수'); return; }
    setEditBusy(true);
    try {
      await updateCompetitorBrand(editId, toInput(editDraft));
      setEditId(null);
      await reload();
    } catch (e) { alert(e instanceof Error ? e.message : '수정 실패'); }
    finally { setEditBusy(false); }
  };

  const handleDelete = async (id: number, query: string) => {
    if (!confirm(`${query} 브랜드를 삭제할까요?`)) return;
    try { await deleteCompetitorBrand(id); await reload(); }
    catch (e) { alert(e instanceof Error ? e.message : '삭제 실패'); }
  };

  const handleAdd = async () => {
    if (!draft.query.trim() || !draft.company.trim()) { setAddError('query / company 필수'); return; }
    setAdding(true); setAddError(null);
    try {
      await createCompetitorBrand(toInput(draft));
      setDraft(EMPTY);
      await reload();
    } catch (e) {
      setAddError(e instanceof Error ? e.message : '추가 실패');
    } finally { setAdding(false); }
  };

  return (
    <div className="space-y-4">
      {loading && (
        <div className="text-center py-8 text-[#8B9BB4] text-sm">
          <i className="ri-loader-4-line animate-spin mr-2"></i>로딩 중…
        </div>
      )}
      {!loading && error && (
        <p className="text-sm text-[#EF4444]"><i className="ri-error-warning-line mr-1"></i>{error}</p>
      )}

      {!loading && !error && (
        <div className="space-y-2">
          {items.map(b => editId === b.id ? (
            <div key={b.id} className="bg-[#0D1117] border border-[#00E5CC] rounded-xl p-3">
              <div className="grid grid-cols-3 gap-2 mb-2">
                <input className={inputCls} placeholder="query (검색용)"
                  value={editDraft.query} onChange={e => setEditDraft({ ...editDraft, query: e.target.value })} />
                <input className={inputCls} placeholder="company"
                  value={editDraft.company} onChange={e => setEditDraft({ ...editDraft, company: e.target.value })} />
                <select className={inputCls}
                  value={editDraft.kind}
                  onChange={e => setEditDraft({ ...editDraft, kind: e.target.value as Draft['kind'] })}
                >
                  <option value="competitor">competitor</option>
                  <option value="msd_asset">msd_asset</option>
                </select>
                <input className={`${inputCls} col-span-2`} placeholder="anchor (예: PD-(L)1)"
                  value={editDraft.anchor} onChange={e => setEditDraft({ ...editDraft, anchor: e.target.value })} />
                <input className={inputCls} placeholder="로고 (2~3자)"
                  value={editDraft.logo} onChange={e => setEditDraft({ ...editDraft, logo: e.target.value })} />
                <input className={inputCls} type="color"
                  value={editDraft.color} onChange={e => setEditDraft({ ...editDraft, color: e.target.value })} />
                <label className="flex items-center gap-1.5 text-xs text-[#8B9BB4]">
                  <input type="checkbox" checked={editDraft.active}
                    onChange={e => setEditDraft({ ...editDraft, active: e.target.checked })} />
                  활성
                </label>
              </div>
              <div className="flex gap-2">
                <button onClick={saveEdit} disabled={editBusy} className="bg-[#00E5CC] text-[#0A0E1A] text-xs font-semibold px-3 py-1.5 rounded disabled:opacity-50">저장</button>
                <button onClick={() => setEditId(null)} className="bg-[#1E2530] text-white text-xs px-3 py-1.5 rounded">취소</button>
              </div>
            </div>
          ) : (
            <div key={b.id} className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2.5 min-w-0 flex-1">
                <span
                  className="w-8 h-8 rounded-lg flex items-center justify-center text-[11px] font-bold flex-shrink-0 text-white"
                  style={{ backgroundColor: (b.color || '#1E2530') + '25', border: `1px solid ${(b.color || '#1E2530')}40` }}
                >
                  {b.logo || b.query.slice(0, 2).toUpperCase()}
                </span>
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-sm font-semibold text-white">{b.query}</span>
                    <span className="text-xs text-[#8B9BB4]">· {b.company}</span>
                    <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${b.kind === 'msd_asset' ? 'bg-blue-500/20 text-blue-400' : 'bg-[#1E2530] text-[#8B9BB4]'}`}>
                      {b.kind}
                    </span>
                    {!b.active && <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-[#EF4444]/20 text-[#EF4444]">비활성</span>}
                  </div>
                  {b.anchor && <p className="text-[11px] text-[#4A5568] mt-0.5 truncate">{b.anchor}</p>}
                </div>
              </div>
              <div className="flex gap-1 flex-shrink-0">
                <button onClick={() => startEdit(b)} className="text-xs px-2 py-1 rounded bg-[#1E2530] hover:bg-[#2A3545] text-white">편집</button>
                <button onClick={() => handleDelete(b.id, b.query)} className="text-xs px-2 py-1 rounded bg-[#EF4444]/20 text-[#EF4444] hover:bg-[#EF4444]/30">
                  <i className="ri-delete-bin-line"></i>
                </button>
              </div>
            </div>
          ))}
          {items.length === 0 && <p className="text-center py-6 text-[#4A5568] text-sm">등록된 브랜드가 없습니다</p>}
        </div>
      )}

      {/* 추가 폼 */}
      <div className="bg-[#0D1117] border border-dashed border-[#2A3545] rounded-xl p-3">
        <p className="text-xs font-semibold text-[#00E5CC] mb-2">+ 브랜드/MNC 추가</p>
        <div className="grid grid-cols-3 gap-2 mb-2">
          <input className={inputCls} placeholder="query (검색용) *"
            value={draft.query} onChange={e => setDraft({ ...draft, query: e.target.value })} />
          <input className={inputCls} placeholder="company *"
            value={draft.company} onChange={e => setDraft({ ...draft, company: e.target.value })} />
          <select className={inputCls}
            value={draft.kind}
            onChange={e => setDraft({ ...draft, kind: e.target.value as Draft['kind'] })}
          >
            <option value="competitor">competitor</option>
            <option value="msd_asset">msd_asset</option>
          </select>
          <input className={`${inputCls} col-span-2`} placeholder="anchor (예: PD-(L)1)"
            value={draft.anchor} onChange={e => setDraft({ ...draft, anchor: e.target.value })} />
          <input className={inputCls} placeholder="로고 (2~3자)"
            value={draft.logo} onChange={e => setDraft({ ...draft, logo: e.target.value })} />
          <input className={inputCls} type="color"
            value={draft.color} onChange={e => setDraft({ ...draft, color: e.target.value })} />
          <label className="flex items-center gap-1.5 text-xs text-[#8B9BB4]">
            <input type="checkbox" checked={draft.active}
              onChange={e => setDraft({ ...draft, active: e.target.checked })} />
            활성
          </label>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-xs text-[#EF4444]">{addError}</span>
          <button onClick={handleAdd} disabled={adding} className="bg-[#00E5CC] text-[#0A0E1A] text-xs font-semibold px-3 py-1.5 rounded disabled:opacity-50">
            {adding ? '추가 중…' : '+ 추가'}
          </button>
        </div>
      </div>

      <p className="text-[11px] text-[#4A5568]">여기서 추가한 브랜드는 다음 크롤·승격부터 반영됩니다.</p>
    </div>
  );
}
