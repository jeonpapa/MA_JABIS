import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  listKeywords, createKeyword, updateKeyword, deleteKeyword, type Keyword,
} from '@/api/keywordCloud';
import { fetchMe } from '@/utils/authUsers';

type Draft = { text: string; weight: string; color: string };
const EMPTY: Draft = { text: '', weight: '60', color: '#8B9BB4' };

export default function AdminKeywordCloudPage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);
  const [items, setItems] = useState<Keyword[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const [editId, setEditId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<Draft>(EMPTY);

  useEffect(() => {
    (async () => {
      try {
        const me = await fetchMe();
        if (!me || me.role !== 'admin') { navigate('/', { replace: true }); return; }
        setAuthChecked(true);
      } catch { navigate('/login', { replace: true }); }
    })();
  }, [navigate]);

  const reload = async () => {
    setLoading(true); setError(null);
    try { setItems(await listKeywords()); }
    catch (e) { setError(e instanceof Error ? e.message : '조회 실패'); }
    finally { setLoading(false); }
  };

  useEffect(() => { if (authChecked) reload(); }, [authChecked]);

  const handleAdd = async () => {
    if (!draft.text.trim()) { setAddError('키워드 필수'); return; }
    const w = Number(draft.weight);
    if (!Number.isFinite(w) || w < 0 || w > 1000) { setAddError('weight 0-1000'); return; }
    setAdding(true); setAddError(null);
    try {
      await createKeyword({ text: draft.text.trim(), weight: w, color: draft.color });
      setDraft(EMPTY);
      await reload();
    } catch (e) { setAddError(e instanceof Error ? e.message : '추가 실패'); }
    finally { setAdding(false); }
  };

  const saveEdit = async () => {
    if (editId == null) return;
    const w = Number(editDraft.weight);
    try {
      await updateKeyword(editId, {
        text: editDraft.text.trim(),
        weight: Number.isFinite(w) ? w : undefined,
        color: editDraft.color,
      });
      setEditId(null);
      await reload();
    } catch (e) { alert(e instanceof Error ? e.message : '수정 실패'); }
  };

  const handleDelete = async (id: number, text: string) => {
    if (!confirm(`"${text}" 삭제?`)) return;
    try { await deleteKeyword(id); await reload(); }
    catch (e) { alert(e instanceof Error ? e.message : '삭제 실패'); }
  };

  if (!authChecked) return null;

  return (
    <div className="min-h-screen bg-[#0D1117] text-white px-8 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Keyword Cloud — 관리</h1>
        <p className="text-[#8B9BB4] text-sm mt-1">Home 워드클라우드 키워드 CRUD</p>
      </div>

      <div className="bg-[#161B27] border border-[#1E2530] rounded-xl p-5 mb-6">
        <h2 className="text-sm font-semibold mb-3 text-[#00E5CC]">신규 키워드 추가</h2>
        <div className="grid grid-cols-4 gap-3">
          <input className="bg-[#0D1117] border border-[#1E2530] rounded px-3 py-2 text-sm col-span-2" placeholder="키워드 *"
            value={draft.text} onChange={e => setDraft({ ...draft, text: e.target.value })} />
          <input className="bg-[#0D1117] border border-[#1E2530] rounded px-3 py-2 text-sm" placeholder="가중치 (0-1000)"
            type="number" value={draft.weight} onChange={e => setDraft({ ...draft, weight: e.target.value })} />
          <input className="bg-[#0D1117] border border-[#1E2530] rounded px-3 py-2 text-sm" type="color"
            value={draft.color} onChange={e => setDraft({ ...draft, color: e.target.value })} />
        </div>
        <div className="flex items-center justify-between mt-3">
          <div className="text-xs text-[#EF4444]">{addError}</div>
          <button onClick={handleAdd} disabled={adding}
            className="bg-[#00E5CC] text-[#0A0E1A] text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-50">
            {adding ? '추가 중…' : '+ 추가'}
          </button>
        </div>
      </div>

      {loading ? (
        <div className="text-center py-8 text-[#8B9BB4]">로딩 중…</div>
      ) : error ? (
        <div className="text-center py-6 text-[#EF4444]">{error}</div>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          {items.map(it => editId === it.id ? (
            <div key={it.id} className="bg-[#161B27] border border-[#00E5CC] rounded-lg p-3 flex items-center gap-2">
              <input className="bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-sm flex-1"
                value={editDraft.text} onChange={e => setEditDraft({ ...editDraft, text: e.target.value })} />
              <input className="bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-sm w-20" type="number"
                value={editDraft.weight} onChange={e => setEditDraft({ ...editDraft, weight: e.target.value })} />
              <input className="bg-[#0D1117] border border-[#1E2530] rounded px-1 py-1 text-sm" type="color"
                value={editDraft.color} onChange={e => setEditDraft({ ...editDraft, color: e.target.value })} />
              <button onClick={saveEdit} className="bg-[#00E5CC] text-[#0A0E1A] text-xs font-semibold px-2 py-1 rounded">저장</button>
              <button onClick={() => setEditId(null)} className="bg-[#1E2530] text-xs px-2 py-1 rounded">취소</button>
            </div>
          ) : (
            <div key={it.id} className="bg-[#161B27] border border-[#1E2530] rounded-lg p-3 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 flex-1 min-w-0">
                <div className="w-3 h-3 rounded-full flex-shrink-0" style={{ backgroundColor: it.color || '#8B9BB4' }} />
                <span className="font-semibold text-sm truncate" style={{ color: it.color || '#8B9BB4' }}>{it.text}</span>
                <span className="text-xs text-[#4A5568]">w={it.weight}</span>
              </div>
              <div className="flex gap-1 flex-shrink-0">
                <button onClick={() => { setEditId(it.id); setEditDraft({ text: it.text, weight: String(it.weight), color: it.color ?? '#8B9BB4' }); }}
                  className="text-xs px-2 py-1 rounded bg-[#1E2530]">편집</button>
                <button onClick={() => handleDelete(it.id, it.text)}
                  className="text-xs px-2 py-1 rounded bg-[#EF4444]/20 text-[#EF4444]"><i className="ri-delete-bin-line"></i></button>
              </div>
            </div>
          ))}
          {items.length === 0 && <p className="col-span-2 text-center py-8 text-[#4A5568]">등록된 키워드가 없습니다</p>}
        </div>
      )}
    </div>
  );
}
