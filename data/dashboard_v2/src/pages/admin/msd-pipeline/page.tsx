import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  listPipeline, createPipeline, updatePipeline, deletePipeline,
  type PipelineItem, type PipelineStatus,
} from '@/api/msdPipeline';
import { fetchMe } from '@/utils/authUsers';

type DraftItem = {
  name: string;
  phase: string;
  indication: string;
  expected_year: string;
  status: PipelineStatus;
  note: string;
};

const EMPTY_DRAFT: DraftItem = { name: '', phase: '', indication: '', expected_year: '', status: 'upcoming', note: '' };

const STATUS_LABEL: Record<PipelineStatus, string> = {
  current: '진행 중',
  upcoming: '예정',
};

export default function AdminMsdPipelinePage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);
  const [items, setItems] = useState<PipelineItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 신규 입력 폼
  const [draft, setDraft] = useState<DraftItem>(EMPTY_DRAFT);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  // 인라인 편집
  const [editId, setEditId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<DraftItem>(EMPTY_DRAFT);
  const [editBusy, setEditBusy] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const me = await fetchMe();
        if (!me || me.role !== 'admin') {
          navigate('/', { replace: true });
          return;
        }
        setAuthChecked(true);
      } catch {
        navigate('/login', { replace: true });
      }
    })();
  }, [navigate]);

  const reload = async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await listPipeline());
    } catch (e) {
      setError(e instanceof Error ? e.message : '파이프라인 조회 실패');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (authChecked) reload();
  }, [authChecked]);

  const toItem = (d: DraftItem) => ({
    name: d.name.trim(),
    phase: d.phase.trim() || null,
    indication: d.indication.trim() || null,
    expected_year: d.expected_year.trim() ? Number(d.expected_year) : null,
    status: d.status,
    note: d.note.trim() || null,
  });

  const handleAdd = async () => {
    if (!draft.name.trim()) {
      setAddError('제품명 필수');
      return;
    }
    setAdding(true);
    setAddError(null);
    try {
      await createPipeline(toItem(draft));
      setDraft(EMPTY_DRAFT);
      await reload();
    } catch (e) {
      setAddError(e instanceof Error ? e.message : '추가 실패');
    } finally {
      setAdding(false);
    }
  };

  const startEdit = (it: PipelineItem) => {
    setEditId(it.id);
    setEditDraft({
      name: it.name,
      phase: it.phase ?? '',
      indication: it.indication ?? '',
      expected_year: it.expected_year != null ? String(it.expected_year) : '',
      status: it.status,
      note: it.note ?? '',
    });
  };
  const cancelEdit = () => setEditId(null);

  const saveEdit = async () => {
    if (editId == null) return;
    if (!editDraft.name.trim()) return;
    setEditBusy(true);
    try {
      await updatePipeline(editId, toItem(editDraft));
      setEditId(null);
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : '수정 실패');
    } finally {
      setEditBusy(false);
    }
  };

  const handleDelete = async (it: PipelineItem) => {
    if (!confirm(`"${it.name}" 삭제?`)) return;
    try {
      await deletePipeline(it.id);
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : '삭제 실패');
    }
  };

  if (!authChecked) {
    return (
      <div className="min-h-screen flex items-center justify-center text-[#8B9BB4] text-sm">
        <i className="ri-loader-4-line animate-spin mr-2"></i>권한 확인 중…
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center gap-2 mb-1">
          <span className="w-5 h-5 flex items-center justify-center"><i className="ri-flask-line text-[#7C3AED]"></i></span>
          <h1 className="text-2xl font-bold text-white">MSD Korea 파이프라인 — 관리</h1>
        </div>
        <p className="text-[#8B9BB4] text-sm">출시 예정 제품 등록·편집·삭제 (Admin 전용). Home 카드에 자동 반영.</p>
      </div>

      <div className="px-8 py-6 space-y-5 max-w-6xl">
        {/* 신규 등록 폼 */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
          <h2 className="text-white font-bold text-base mb-4">신규 파이프라인 등록</h2>
          <div className="grid grid-cols-12 gap-3">
            <InputCell label="제품명 *" span={3} value={draft.name} onChange={v => setDraft({ ...draft, name: v })} placeholder="e.g. MK-1234" />
            <InputCell label="개발 단계" span={2} value={draft.phase} onChange={v => setDraft({ ...draft, phase: v })} placeholder="Phase 2, Phase 3…" />
            <InputCell label="적응증" span={3} value={draft.indication} onChange={v => setDraft({ ...draft, indication: v })} placeholder="비소세포폐암" />
            <InputCell label="예상 연도" span={2} value={draft.expected_year} onChange={v => setDraft({ ...draft, expected_year: v })} placeholder="2027" type="number" />
            <div className="col-span-2">
              <label className="block text-[#8B9BB4] text-[11px] mb-1">상태</label>
              <select
                value={draft.status}
                onChange={e => setDraft({ ...draft, status: e.target.value as PipelineStatus })}
                className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-[#00E5CC]/50"
              >
                <option value="upcoming">예정</option>
                <option value="current">진행 중</option>
              </select>
            </div>
          </div>
          <div className="mt-3 grid grid-cols-12 gap-3">
            <InputCell label="메모" span={10} value={draft.note} onChange={v => setDraft({ ...draft, note: v })} placeholder="선택 항목" />
            <div className="col-span-2 flex items-end">
              <button
                onClick={handleAdd}
                disabled={adding || !draft.name.trim()}
                className="w-full bg-[#00E5CC] text-[#0A0E1A] px-4 py-2 rounded-lg text-sm font-semibold cursor-pointer hover:bg-[#00C9B1] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {adding ? '추가 중…' : '추가'}
              </button>
            </div>
          </div>
          {addError && (
            <p className="text-red-400 text-xs mt-2">{addError}</p>
          )}
        </div>

        {/* 목록 */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-white font-bold text-base">등록된 파이프라인 ({items.length})</h2>
            <button onClick={reload} className="text-[#8B9BB4] text-xs hover:text-white cursor-pointer flex items-center gap-1">
              <i className="ri-refresh-line"></i>새로고침
            </button>
          </div>
          {loading && <p className="text-[#8B9BB4] text-sm">로드 중…</p>}
          {error && <p className="text-red-400 text-sm">{error}</p>}
          {!loading && items.length === 0 && <p className="text-[#4A5568] text-sm">등록된 항목이 없습니다.</p>}
          {!loading && items.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[#8B9BB4] text-xs border-b border-[#1E2530]">
                    <th className="text-left py-2 pr-3">제품명</th>
                    <th className="text-left py-2 pr-3">단계</th>
                    <th className="text-left py-2 pr-3">적응증</th>
                    <th className="text-left py-2 pr-3 whitespace-nowrap">예상 연도</th>
                    <th className="text-left py-2 pr-3">상태</th>
                    <th className="text-left py-2 pr-3">메모</th>
                    <th className="text-right py-2">관리</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map(it => (
                    editId === it.id ? (
                      <tr key={it.id} className="border-b border-[#1E2530]/50 bg-[#00E5CC]/5">
                        <td className="py-2 pr-2"><InlineInput value={editDraft.name} onChange={v => setEditDraft({ ...editDraft, name: v })} /></td>
                        <td className="py-2 pr-2"><InlineInput value={editDraft.phase} onChange={v => setEditDraft({ ...editDraft, phase: v })} placeholder="Phase 3" /></td>
                        <td className="py-2 pr-2"><InlineInput value={editDraft.indication} onChange={v => setEditDraft({ ...editDraft, indication: v })} /></td>
                        <td className="py-2 pr-2"><InlineInput value={editDraft.expected_year} onChange={v => setEditDraft({ ...editDraft, expected_year: v })} type="number" /></td>
                        <td className="py-2 pr-2">
                          <select
                            value={editDraft.status}
                            onChange={e => setEditDraft({ ...editDraft, status: e.target.value as PipelineStatus })}
                            className="bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-white text-xs"
                          >
                            <option value="upcoming">예정</option>
                            <option value="current">진행 중</option>
                          </select>
                        </td>
                        <td className="py-2 pr-2"><InlineInput value={editDraft.note} onChange={v => setEditDraft({ ...editDraft, note: v })} /></td>
                        <td className="py-2 text-right whitespace-nowrap">
                          <button
                            onClick={saveEdit}
                            disabled={editBusy}
                            className="text-[#00E5CC] text-xs font-semibold mr-2 hover:text-[#00C9B1] cursor-pointer disabled:opacity-50"
                          >
                            저장
                          </button>
                          <button onClick={cancelEdit} className="text-[#8B9BB4] text-xs hover:text-white cursor-pointer">취소</button>
                        </td>
                      </tr>
                    ) : (
                      <tr key={it.id} className="border-b border-[#1E2530]/50 last:border-b-0 hover:bg-[#1E2530]/30">
                        <td className="py-2 pr-3 text-white font-medium">{it.name}</td>
                        <td className="py-2 pr-3 text-[#8B9BB4]">{it.phase ?? <span className="text-[#4A5568]">—</span>}</td>
                        <td className="py-2 pr-3 text-[#8B9BB4]">{it.indication ?? <span className="text-[#4A5568]">—</span>}</td>
                        <td className="py-2 pr-3 text-[#8B9BB4]">{it.expected_year ?? <span className="text-[#4A5568]">—</span>}</td>
                        <td className="py-2 pr-3">
                          <span className={`text-[10px] px-2 py-0.5 rounded ${it.status === 'current' ? 'bg-[#00E5CC]/10 text-[#00E5CC]' : 'bg-[#7C3AED]/10 text-[#7C3AED]'}`}>
                            {STATUS_LABEL[it.status]}
                          </span>
                        </td>
                        <td className="py-2 pr-3 text-[#8B9BB4] text-xs truncate max-w-[200px]">{it.note ?? <span className="text-[#4A5568]">—</span>}</td>
                        <td className="py-2 text-right whitespace-nowrap">
                          <button onClick={() => startEdit(it)} className="text-[#8B9BB4] text-xs hover:text-[#00E5CC] mr-3 cursor-pointer">편집</button>
                          <button onClick={() => handleDelete(it)} className="text-[#8B9BB4] text-xs hover:text-red-400 cursor-pointer">삭제</button>
                        </td>
                      </tr>
                    )
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const SPAN_CLASS: Record<number, string> = {
  1: 'col-span-1', 2: 'col-span-2', 3: 'col-span-3', 4: 'col-span-4', 5: 'col-span-5',
  6: 'col-span-6', 7: 'col-span-7', 8: 'col-span-8', 9: 'col-span-9', 10: 'col-span-10',
  11: 'col-span-11', 12: 'col-span-12',
};

function InputCell({
  label, span, value, onChange, placeholder, type,
}: {
  label: string; span: number; value: string;
  onChange: (v: string) => void; placeholder?: string; type?: string;
}) {
  return (
    <div className={SPAN_CLASS[span] ?? 'col-span-3'}>
      <label className="block text-[#8B9BB4] text-[11px] mb-1">{label}</label>
      <input
        type={type ?? 'text'}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-sm placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50"
      />
    </div>
  );
}

function InlineInput({ value, onChange, placeholder, type }: { value: string; onChange: (v: string) => void; placeholder?: string; type?: string }) {
  return (
    <input
      type={type ?? 'text'}
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      className="w-full bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-white text-xs placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50"
    />
  );
}
