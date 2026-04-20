import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  listBrandTraffic, createBrandTraffic, updateBrandTraffic, deleteBrandTraffic,
  type BrandTrafficItem, type BrandNews,
} from '@/api/brandTraffic';
import { fetchMe } from '@/utils/authUsers';

type Draft = {
  rank: string;
  brand: string;
  company: string;
  category: string;
  color: string;
  trafficIndex: string;
  change: string;
  sparkline: string; // CSV of 7 numbers
  newsJson: string;  // JSON array
};

const EMPTY: Draft = {
  rank: '', brand: '', company: '', category: '', color: '#00E5CC',
  trafficIndex: '0', change: '0', sparkline: '', newsJson: '[]',
};

function parseSparkline(csv: string): number[] {
  if (!csv.trim()) return [];
  return csv.split(',').map(s => Number(s.trim())).filter(n => Number.isFinite(n));
}

function parseNews(raw: string): BrandNews[] | null {
  const t = raw.trim();
  if (!t) return [];
  try {
    const parsed = JSON.parse(t);
    if (!Array.isArray(parsed)) return null;
    return parsed as BrandNews[];
  } catch {
    return null;
  }
}

export default function AdminBrandTrafficPage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);
  const [items, setItems] = useState<BrandTrafficItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const [editId, setEditId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<Draft>(EMPTY);
  const [editBusy, setEditBusy] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const me = await fetchMe();
        if (!me || me.role !== 'admin') { navigate('/', { replace: true }); return; }
        setAuthChecked(true);
      } catch {
        navigate('/login', { replace: true });
      }
    })();
  }, [navigate]);

  const reload = async () => {
    setLoading(true); setError(null);
    try { setItems(await listBrandTraffic()); }
    catch (e) { setError(e instanceof Error ? e.message : '조회 실패'); }
    finally { setLoading(false); }
  };

  useEffect(() => { if (authChecked) reload(); }, [authChecked]);

  const draftToInput = (d: Draft) => {
    const news = parseNews(d.newsJson);
    if (news === null) throw new Error('뉴스 JSON 파싱 실패');
    return {
      rank: d.rank.trim() ? Number(d.rank) : undefined,
      brand: d.brand.trim(),
      company: d.company.trim() || null,
      category: d.category.trim() || null,
      color: d.color.trim() || null,
      trafficIndex: d.trafficIndex.trim() ? Number(d.trafficIndex) : 0,
      change: d.change.trim() ? Number(d.change) : 0,
      sparkline: parseSparkline(d.sparkline),
      news,
    };
  };

  const handleAdd = async () => {
    if (!draft.brand.trim()) { setAddError('브랜드명 필수'); return; }
    setAdding(true); setAddError(null);
    try {
      await createBrandTraffic(draftToInput(draft));
      setDraft(EMPTY);
      await reload();
    } catch (e) {
      setAddError(e instanceof Error ? e.message : '추가 실패');
    } finally {
      setAdding(false);
    }
  };

  const startEdit = (it: BrandTrafficItem) => {
    setEditId(it.id);
    setEditDraft({
      rank: String(it.rank),
      brand: it.brand,
      company: it.company ?? '',
      category: it.category ?? '',
      color: it.color ?? '#00E5CC',
      trafficIndex: String(it.trafficIndex),
      change: String(it.change),
      sparkline: it.sparkline.join(', '),
      newsJson: JSON.stringify(it.news, null, 2),
    });
  };
  const cancelEdit = () => setEditId(null);

  const saveEdit = async () => {
    if (editId == null) return;
    if (!editDraft.brand.trim()) return;
    setEditBusy(true);
    try {
      await updateBrandTraffic(editId, draftToInput(editDraft));
      setEditId(null);
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : '수정 실패');
    } finally {
      setEditBusy(false);
    }
  };

  const handleDelete = async (it: BrandTrafficItem) => {
    if (!confirm(`"${it.brand}" 삭제?`)) return;
    try { await deleteBrandTraffic(it.id); await reload(); }
    catch (e) { alert(e instanceof Error ? e.message : '삭제 실패'); }
  };

  if (!authChecked) {
    return <div className="min-h-screen flex items-center justify-center text-[#8B9BB4] text-sm"><i className="ri-loader-4-line animate-spin mr-2"></i>권한 확인 중…</div>;
  }

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center gap-2 mb-1">
          <span className="w-5 h-5 flex items-center justify-center"><i className="ri-fire-line text-[#F59E0B]"></i></span>
          <h1 className="text-2xl font-bold text-white">브랜드 미디어 트래픽 — 관리</h1>
        </div>
        <p className="text-[#8B9BB4] text-sm">Home “브랜드 언급 Top 10”에 노출되는 데이터. 순위/트래픽 지수/주간 스파크라인/관련 뉴스 편집.</p>
      </div>

      <div className="px-8 py-6 space-y-5 max-w-6xl">
        {/* 신규 등록 */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
          <h2 className="text-white font-bold text-base mb-4">신규 브랜드 등록</h2>
          <div className="grid grid-cols-12 gap-3">
            <Cell label="순위" span={1} value={draft.rank} onChange={v => setDraft({ ...draft, rank: v })} type="number" placeholder="auto" />
            <Cell label="브랜드 *" span={2} value={draft.brand} onChange={v => setDraft({ ...draft, brand: v })} placeholder="Keytruda" />
            <Cell label="회사" span={2} value={draft.company} onChange={v => setDraft({ ...draft, company: v })} placeholder="한국MSD" />
            <Cell label="카테고리" span={2} value={draft.category} onChange={v => setDraft({ ...draft, category: v })} placeholder="면역항암제" />
            <Cell label="컬러" span={1} value={draft.color} onChange={v => setDraft({ ...draft, color: v })} placeholder="#00E5CC" />
            <Cell label="트래픽 지수" span={2} value={draft.trafficIndex} onChange={v => setDraft({ ...draft, trafficIndex: v })} type="number" placeholder="9840" />
            <Cell label="전주 대비 %" span={2} value={draft.change} onChange={v => setDraft({ ...draft, change: v })} type="number" placeholder="12.4" />
          </div>
          <div className="mt-3 grid grid-cols-12 gap-3">
            <Cell label="스파크라인 (7개, 콤마 구분)" span={12} value={draft.sparkline} onChange={v => setDraft({ ...draft, sparkline: v })} placeholder="6200, 7100, 6800, 7900, 8400, 8750, 9840" />
          </div>
          <div className="mt-3">
            <label className="block text-[#8B9BB4] text-[11px] mb-1">관련 뉴스 (JSON 배열 — title/source/date/tag/url)</label>
            <textarea
              value={draft.newsJson}
              onChange={e => setDraft({ ...draft, newsJson: e.target.value })}
              rows={5}
              className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-xs font-mono placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50"
              placeholder='[{"title":"...","source":"...","date":"2025-04-14","tag":"급여","url":"https://..."}]'
            />
          </div>
          <div className="mt-4 flex items-center gap-3">
            <button
              onClick={handleAdd}
              disabled={adding || !draft.brand.trim()}
              className="bg-[#00E5CC] text-[#0A0E1A] px-4 py-2 rounded-lg text-sm font-semibold cursor-pointer hover:bg-[#00C9B1] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {adding ? '추가 중…' : '추가'}
            </button>
            {addError && <span className="text-red-400 text-xs">{addError}</span>}
          </div>
        </div>

        {/* 목록 */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-white font-bold text-base">등록된 브랜드 ({items.length})</h2>
            <button onClick={reload} className="text-[#8B9BB4] text-xs hover:text-white cursor-pointer flex items-center gap-1">
              <i className="ri-refresh-line"></i>새로고침
            </button>
          </div>
          {loading && <p className="text-[#8B9BB4] text-sm">로드 중…</p>}
          {error && <p className="text-red-400 text-sm">{error}</p>}
          {!loading && items.length === 0 && <p className="text-[#4A5568] text-sm">등록된 항목이 없습니다.</p>}
          {!loading && items.length > 0 && (
            <div className="space-y-2">
              {items.map(it => (
                editId === it.id ? (
                  <div key={it.id} className="bg-[#00E5CC]/5 border border-[#00E5CC]/20 rounded-xl p-4 space-y-3">
                    <div className="grid grid-cols-12 gap-3">
                      <Cell label="순위" span={1} value={editDraft.rank} onChange={v => setEditDraft({ ...editDraft, rank: v })} type="number" />
                      <Cell label="브랜드" span={2} value={editDraft.brand} onChange={v => setEditDraft({ ...editDraft, brand: v })} />
                      <Cell label="회사" span={2} value={editDraft.company} onChange={v => setEditDraft({ ...editDraft, company: v })} />
                      <Cell label="카테고리" span={2} value={editDraft.category} onChange={v => setEditDraft({ ...editDraft, category: v })} />
                      <Cell label="컬러" span={1} value={editDraft.color} onChange={v => setEditDraft({ ...editDraft, color: v })} />
                      <Cell label="트래픽" span={2} value={editDraft.trafficIndex} onChange={v => setEditDraft({ ...editDraft, trafficIndex: v })} type="number" />
                      <Cell label="변동 %" span={2} value={editDraft.change} onChange={v => setEditDraft({ ...editDraft, change: v })} type="number" />
                    </div>
                    <Cell label="스파크라인 (콤마 구분)" span={12} value={editDraft.sparkline} onChange={v => setEditDraft({ ...editDraft, sparkline: v })} />
                    <div>
                      <label className="block text-[#8B9BB4] text-[11px] mb-1">관련 뉴스 (JSON)</label>
                      <textarea
                        value={editDraft.newsJson}
                        onChange={e => setEditDraft({ ...editDraft, newsJson: e.target.value })}
                        rows={5}
                        className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-xs font-mono focus:outline-none focus:border-[#00E5CC]/50"
                      />
                    </div>
                    <div className="flex items-center gap-2 justify-end">
                      <button onClick={saveEdit} disabled={editBusy} className="bg-[#00E5CC] text-[#0A0E1A] px-3 py-1.5 rounded text-xs font-semibold cursor-pointer hover:bg-[#00C9B1] disabled:opacity-50">저장</button>
                      <button onClick={cancelEdit} className="text-[#8B9BB4] text-xs hover:text-white cursor-pointer px-3 py-1.5">취소</button>
                    </div>
                  </div>
                ) : (
                  <div key={it.id} className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-4 flex items-center gap-4 hover:border-[#2A3545] transition-colors">
                    <span className="text-[#F59E0B] font-black text-sm w-6 text-center">{it.rank}</span>
                    <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: it.color ?? '#6B7280' }}></span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-white text-sm font-semibold">{it.brand}</span>
                        <span className="text-[#4A5568] text-xs">·</span>
                        <span className="text-[#8B9BB4] text-xs">{it.company ?? '—'}</span>
                        <span className="text-[#4A5568] text-xs">·</span>
                        <span className="text-[#8B9BB4] text-xs">{it.category ?? '—'}</span>
                      </div>
                      <div className="flex items-center gap-3 mt-1 text-xs">
                        <span className="text-[#00E5CC] tabular-nums">{it.trafficIndex.toLocaleString()}</span>
                        <span className={it.change >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                          {it.change >= 0 ? '+' : ''}{it.change}%
                        </span>
                        <span className="text-[#4A5568]">스파크 {it.sparkline.length}개</span>
                        <span className="text-[#4A5568]">뉴스 {it.news.length}건</span>
                      </div>
                    </div>
                    <button onClick={() => startEdit(it)} className="text-[#8B9BB4] text-xs hover:text-[#00E5CC] cursor-pointer">편집</button>
                    <button onClick={() => handleDelete(it)} className="text-[#8B9BB4] text-xs hover:text-red-400 cursor-pointer">삭제</button>
                  </div>
                )
              ))}
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

function Cell({
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
