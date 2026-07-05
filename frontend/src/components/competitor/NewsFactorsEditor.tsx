import { useEffect, useMemo, useState } from 'react';
import {
  listNewsFactors,
  createNewsFactor,
  updateNewsFactor,
  deleteNewsFactor,
  type NewsFactor,
  type NewsFactorInput,
  type NewsFactorScope,
  type NewsFactorKind,
} from '@/api/editableFactors';

type Draft = {
  scope: NewsFactorScope;
  kind: NewsFactorKind;
  agency: string;
  term: string;
  active: boolean;
};

const EMPTY: Draft = { scope: 'competitor', kind: 'relevance', agency: '', term: '', active: true };

const inputCls = 'bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1.5 text-sm text-white placeholder-[#4A5568]';

function toInput(d: Draft): NewsFactorInput {
  return {
    scope: d.scope,
    kind: d.kind,
    agency: d.kind === 'gov_seed' ? (d.agency.trim() || null) : null,
    term: d.term.trim(),
    active: d.active,
  };
}

function toDraft(f: NewsFactor): Draft {
  return { scope: f.scope, kind: f.kind, agency: f.agency ?? '', term: f.term, active: Boolean(f.active) };
}

function Row({
  f, onEdit, onDelete,
}: { f: NewsFactor; onEdit: () => void; onDelete: () => void }) {
  return (
    <div className="flex items-center justify-between gap-2 bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-1.5">
      <div className="min-w-0 flex items-center gap-2 flex-wrap">
        {f.agency && <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-[#1E2530] text-[#8B9BB4] flex-shrink-0">{f.agency}</span>}
        <span className="text-sm text-white truncate">{f.term}</span>
        {!f.active && <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-[#EF4444]/20 text-[#EF4444] flex-shrink-0">비활성</span>}
      </div>
      <div className="flex gap-1 flex-shrink-0">
        <button onClick={onEdit} className="text-xs px-2 py-1 rounded bg-[#1E2530] hover:bg-[#2A3545] text-white">편집</button>
        <button onClick={onDelete} className="text-xs px-2 py-1 rounded bg-[#EF4444]/20 text-[#EF4444] hover:bg-[#EF4444]/30">
          <i className="ri-delete-bin-line"></i>
        </button>
      </div>
    </div>
  );
}

/** ⚙ 뉴스 키워드 팩터 편집 — news_keyword_factor 테이블 CRUD (경쟁사 relevance + 정부 seed/맥락어). */
export default function NewsFactorsEditor() {
  const [items, setItems] = useState<NewsFactor[]>([]);
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
    try { setItems(await listNewsFactors()); }
    catch (e) { setError(e instanceof Error ? e.message : '조회 실패'); }
    finally { setLoading(false); }
  };

  useEffect(() => { reload(); }, []);

  const competitorRelevance = useMemo(
    () => items.filter(i => i.scope === 'competitor' && i.kind === 'relevance'),
    [items],
  );
  const govSeed = useMemo(() => items.filter(i => i.scope === 'gov' && i.kind === 'gov_seed'), [items]);
  const govContext = useMemo(() => items.filter(i => i.scope === 'gov' && i.kind === 'context_anchor'), [items]);
  const other = useMemo(
    () => items.filter(i => !(i.scope === 'competitor' && i.kind === 'relevance')
      && !(i.scope === 'gov' && i.kind === 'gov_seed')
      && !(i.scope === 'gov' && i.kind === 'context_anchor')),
    [items],
  );

  const startEdit = (f: NewsFactor) => { setEditId(f.id); setEditDraft(toDraft(f)); };

  const saveEdit = async () => {
    if (editId == null) return;
    if (!editDraft.term.trim()) { alert('term 필수'); return; }
    setEditBusy(true);
    try {
      await updateNewsFactor(editId, toInput(editDraft));
      setEditId(null);
      await reload();
    } catch (e) { alert(e instanceof Error ? e.message : '수정 실패'); }
    finally { setEditBusy(false); }
  };

  const handleDelete = async (id: number, term: string) => {
    if (!confirm(`"${term}" 팩터를 삭제할까요?`)) return;
    try { await deleteNewsFactor(id); await reload(); }
    catch (e) { alert(e instanceof Error ? e.message : '삭제 실패'); }
  };

  const handleAdd = async () => {
    if (!draft.term.trim()) { setAddError('term 필수'); return; }
    setAdding(true); setAddError(null);
    try {
      await createNewsFactor(toInput(draft));
      setDraft({ ...EMPTY, scope: draft.scope, kind: draft.kind });
      await reload();
    } catch (e) {
      setAddError(e instanceof Error ? e.message : '추가 실패');
    } finally { setAdding(false); }
  };

  const editRowFields = (d: Draft, setD: (d: Draft) => void, busy: boolean, onSave: () => void, onCancel: () => void) => (
    <div className="bg-[#0D1117] border border-[#00E5CC] rounded-xl p-3">
      <div className="grid grid-cols-2 gap-2 mb-2">
        <select className={inputCls} value={d.scope} onChange={e => setD({ ...d, scope: e.target.value as NewsFactorScope })}>
          <option value="competitor">competitor</option>
          <option value="gov">gov</option>
        </select>
        <select className={inputCls} value={d.kind} onChange={e => setD({ ...d, kind: e.target.value as NewsFactorKind })}>
          <option value="relevance">relevance</option>
          <option value="context_anchor">context_anchor</option>
          <option value="gov_seed">gov_seed</option>
        </select>
        {d.kind === 'gov_seed' && (
          <input className={inputCls} placeholder="agency (예: 보건복지부)"
            value={d.agency} onChange={e => setD({ ...d, agency: e.target.value })} />
        )}
        <input className={`${inputCls} ${d.kind === 'gov_seed' ? '' : 'col-span-2'}`} placeholder="term *"
          value={d.term} onChange={e => setD({ ...d, term: e.target.value })} />
        <label className="flex items-center gap-1.5 text-xs text-[#8B9BB4] col-span-2">
          <input type="checkbox" checked={d.active} onChange={e => setD({ ...d, active: e.target.checked })} />
          활성
        </label>
      </div>
      <div className="flex gap-2">
        <button onClick={onSave} disabled={busy} className="bg-[#00E5CC] text-[#0A0E1A] text-xs font-semibold px-3 py-1.5 rounded disabled:opacity-50">저장</button>
        <button onClick={onCancel} className="bg-[#1E2530] text-white text-xs px-3 py-1.5 rounded">취소</button>
      </div>
    </div>
  );

  const renderGroup = (label: string, rows: NewsFactor[]) => (
    <div className="space-y-1.5">
      <p className="text-[11px] font-semibold text-[#8B9BB4] uppercase tracking-wide">{label} ({rows.length})</p>
      {rows.length === 0 && <p className="text-xs text-[#4A5568] py-1">등록된 항목이 없습니다</p>}
      {rows.map(f => editId === f.id
        ? <div key={f.id}>{editRowFields(editDraft, setEditDraft, editBusy, saveEdit, () => setEditId(null))}</div>
        : <Row key={f.id} f={f} onEdit={() => startEdit(f)} onDelete={() => handleDelete(f.id, f.term)} />)}
    </div>
  );

  return (
    <div className="space-y-5">
      {loading && (
        <div className="text-center py-8 text-[#8B9BB4] text-sm">
          <i className="ri-loader-4-line animate-spin mr-2"></i>로딩 중…
        </div>
      )}
      {!loading && error && (
        <p className="text-sm text-[#EF4444]"><i className="ri-error-warning-line mr-1"></i>{error}</p>
      )}

      {!loading && !error && (
        <>
          {renderGroup('경쟁사 관련성 (competitor / relevance)', competitorRelevance)}
          {renderGroup('정부 seed 기관어 (gov / gov_seed)', govSeed)}
          {renderGroup('정부 맥락어 (gov / context_anchor)', govContext)}
          {other.length > 0 && renderGroup('기타', other)}
        </>
      )}

      {/* 추가 폼 */}
      <div className="bg-[#0D1117] border border-dashed border-[#2A3545] rounded-xl p-3">
        <p className="text-xs font-semibold text-[#00E5CC] mb-2">+ 키워드 팩터 추가</p>
        <div className="grid grid-cols-2 gap-2 mb-2">
          <select className={inputCls} value={draft.scope} onChange={e => setDraft({ ...draft, scope: e.target.value as NewsFactorScope })}>
            <option value="competitor">competitor</option>
            <option value="gov">gov</option>
          </select>
          <select className={inputCls} value={draft.kind} onChange={e => setDraft({ ...draft, kind: e.target.value as NewsFactorKind })}>
            <option value="relevance">relevance</option>
            <option value="context_anchor">context_anchor</option>
            <option value="gov_seed">gov_seed</option>
          </select>
          {draft.kind === 'gov_seed' && (
            <input className={inputCls} placeholder="agency (예: 보건복지부)"
              value={draft.agency} onChange={e => setDraft({ ...draft, agency: e.target.value })} />
          )}
          <input className={`${inputCls} ${draft.kind === 'gov_seed' ? '' : 'col-span-2'}`} placeholder="term *"
            value={draft.term} onChange={e => setDraft({ ...draft, term: e.target.value })} />
          <label className="flex items-center gap-1.5 text-xs text-[#8B9BB4] col-span-2">
            <input type="checkbox" checked={draft.active} onChange={e => setDraft({ ...draft, active: e.target.checked })} />
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

      <p className="text-[11px] text-[#4A5568]">정부 seed/맥락어 편집은 다음 정책뉴스 크롤부터 반영됩니다.</p>
    </div>
  );
}
