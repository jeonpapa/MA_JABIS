import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  listCompetitorTrends, createCompetitorTrend, updateCompetitorTrend, deleteCompetitorTrend,
  refreshCompetitorTrends, COMPETITOR_BADGES,
  type CompetitorTrend, type CompetitorTrendInput, type CompetitorRefreshResult,
} from '@/api/competitorTrends';
import { fetchMe } from '@/utils/authUsers';

const BADGE_COLOR_DEFAULT: Record<string, string> = {
  '신규 출시': 'bg-emerald-500/20 text-emerald-400',
  '가격 변동': 'bg-amber-500/20 text-amber-400',
  '임상 진행': 'bg-violet-500/20 text-violet-400',
  '급여 등재': 'bg-emerald-500/20 text-emerald-400',
  '파이프라인': 'bg-blue-500/20 text-blue-400',
  '전략 변화': 'bg-rose-500/20 text-rose-400',
};

type Draft = {
  company: string;
  logo: string;
  color: string;
  badge: string;
  badgeColor: string;
  headline: string;
  detail: string;
  date: string;
  source: string;
  url: string;
};

const today = () => new Date().toISOString().slice(0, 10);

const EMPTY: Draft = {
  company: '', logo: '', color: '#00E5CC',
  badge: '신규 출시', badgeColor: BADGE_COLOR_DEFAULT['신규 출시'],
  headline: '', detail: '', date: today(), source: '', url: '',
};

export default function AdminCompetitorTrendsPage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);
  const [items, setItems] = useState<CompetitorTrend[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const [editId, setEditId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<Draft>(EMPTY);
  const [editBusy, setEditBusy] = useState(false);

  const [refreshBusy, setRefreshBusy] = useState(false);
  const [refreshResult, setRefreshResult] = useState<CompetitorRefreshResult | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [refreshDays, setRefreshDays] = useState(7);

  const handleRefresh = async (dryRun: boolean) => {
    if (refreshBusy) return;
    setRefreshBusy(true);
    setRefreshError(null);
    setRefreshResult(null);
    try {
      const r = await refreshCompetitorTrends({ days: refreshDays, dry_run: dryRun });
      setRefreshResult(r);
      if (!dryRun) await reload();
    } catch (e) {
      setRefreshError(e instanceof Error ? e.message : '크롤 실패');
    } finally {
      setRefreshBusy(false);
    }
  };

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
    try { setItems(await listCompetitorTrends()); }
    catch (e) { setError(e instanceof Error ? e.message : '조회 실패'); }
    finally { setLoading(false); }
  };

  useEffect(() => { if (authChecked) reload(); }, [authChecked]);

  const toInput = (d: Draft): CompetitorTrendInput => ({
    company: d.company.trim(),
    logo: d.logo.trim() || null,
    color: d.color.trim() || null,
    badge: d.badge,
    badgeColor: d.badgeColor.trim() || BADGE_COLOR_DEFAULT[d.badge] || null,
    headline: d.headline.trim(),
    detail: d.detail.trim(),
    date: d.date,
    source: d.source.trim() || null,
    url: d.url.trim() || null,
  });

  const handleAdd = async () => {
    if (!draft.company.trim() || !draft.headline.trim() || !draft.detail.trim()) {
      setAddError('회사 / 헤드라인 / 상세 필수'); return;
    }
    setAdding(true); setAddError(null);
    try {
      await createCompetitorTrend(toInput(draft));
      setDraft(EMPTY);
      await reload();
    } catch (e) {
      setAddError(e instanceof Error ? e.message : '추가 실패');
    } finally { setAdding(false); }
  };

  const startEdit = (it: CompetitorTrend) => {
    setEditId(it.id);
    setEditDraft({
      company: it.company,
      logo: it.logo ?? '',
      color: it.color ?? '#00E5CC',
      badge: it.badge,
      badgeColor: it.badgeColor ?? '',
      headline: it.headline,
      detail: it.detail,
      date: it.date,
      source: it.source ?? '',
      url: it.url ?? '',
    });
  };

  const saveEdit = async () => {
    if (editId == null) return;
    setEditBusy(true);
    try {
      await updateCompetitorTrend(editId, toInput(editDraft));
      setEditId(null);
      await reload();
    } catch (e) { alert(e instanceof Error ? e.message : '수정 실패'); }
    finally { setEditBusy(false); }
  };

  const handleDelete = async (id: number, company: string) => {
    if (!confirm(`${company} 동향을 삭제할까요?`)) return;
    try { await deleteCompetitorTrend(id); await reload(); }
    catch (e) { alert(e instanceof Error ? e.message : '삭제 실패'); }
  };

  if (!authChecked) return null;

  return (
    <div className="min-h-screen bg-[#0D1117] text-white px-8 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Competitor Trends — 관리</h1>
        <p className="text-[#8B9BB4] text-sm mt-1">경쟁사 동향 카드 CRUD · 주 1회 자동 크롤 + LLM 필터 (B안)</p>
      </div>

      {/* 자동 크롤 패널 */}
      <div className="bg-[#161B27] border border-[#1E2530] rounded-xl p-5 mb-6">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-sm font-semibold text-[#00E5CC]">자동 크롤 (Naver + GPT-4o)</h2>
            <p className="text-[#8B9BB4] text-xs mt-0.5">
              경쟁 브랜드(옵디보·타그리소·임핀지·테쎈트릭·린파자·레블리미드·다잘렉스) 지난 N 일 뉴스 → LLM 중요도 필터 → critical/moderate 만 카드 자동 생성. manual 편집은 덮어쓰지 않음.
            </p>
          </div>
        </div>
        <div className="flex items-end gap-3">
          <div>
            <label className="block text-[#8B9BB4] text-[11px] mb-1">기간 (일)</label>
            <input
              type="number" min={1} max={30} value={refreshDays}
              onChange={e => setRefreshDays(Math.max(1, Math.min(30, Number(e.target.value) || 7)))}
              className="bg-[#0D1117] border border-[#1E2530] rounded px-3 py-2 text-sm w-24"
            />
          </div>
          <button
            onClick={() => handleRefresh(true)}
            disabled={refreshBusy}
            className="bg-[#1E2530] text-[#8B9BB4] text-sm font-semibold px-4 py-2 rounded-lg hover:text-white disabled:opacity-50"
          >
            {refreshBusy ? '…' : '드라이런'}
          </button>
          <button
            onClick={() => handleRefresh(false)}
            disabled={refreshBusy}
            className="bg-[#00E5CC] text-[#0A0E1A] text-sm font-semibold px-4 py-2 rounded-lg hover:bg-[#00C9B1] disabled:opacity-50"
          >
            {refreshBusy ? '크롤 중…' : '지금 크롤 실행'}
          </button>
        </div>
        {refreshError && <p className="text-red-400 text-xs mt-3">{refreshError}</p>}
        {refreshResult && (
          <div className="mt-4 bg-[#0D1117] border border-[#1E2530] rounded-lg p-3">
            <div className="text-[#8B9BB4] text-xs mb-2">
              결과 · 총 fetched={refreshResult.totals.fetched} · accepted={refreshResult.totals.accepted} · upserted={refreshResult.totals.upserted}
              {refreshResult.dry_run && <span className="ml-2 text-[#F59E0B]">[DRY-RUN]</span>}
            </div>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[#4A5568] border-b border-[#1E2530]">
                  <th className="text-left py-1 pr-3">브랜드</th>
                  <th className="text-right py-1 pr-3">fetched</th>
                  <th className="text-right py-1 pr-3">accepted</th>
                  <th className="text-right py-1 pr-3">upserted</th>
                  <th className="text-right py-1">skipped_low</th>
                </tr>
              </thead>
              <tbody>
                {refreshResult.brands.map(b => (
                  <tr key={b.brand} className="border-b border-[#1E2530]/50 last:border-b-0">
                    <td className="py-1 pr-3 text-white">{b.brand} <span className="text-[#4A5568]">· {b.company}</span></td>
                    <td className="py-1 pr-3 text-right text-[#8B9BB4]">{b.fetched}</td>
                    <td className="py-1 pr-3 text-right text-[#00E5CC]">{b.accepted}</td>
                    <td className="py-1 pr-3 text-right text-[#3B82F6]">{b.upserted}</td>
                    <td className="py-1 text-right text-[#4A5568]">{b.skipped_low}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 추가 폼 */}
      <div className="bg-[#161B27] border border-[#1E2530] rounded-xl p-5 mb-6">
        <h2 className="text-sm font-semibold mb-4 text-[#00E5CC]">신규 동향 추가</h2>
        <div className="grid grid-cols-4 gap-3">
          <input className="bg-[#0D1117] border border-[#1E2530] rounded px-3 py-2 text-sm" placeholder="회사명 *"
            value={draft.company} onChange={e => setDraft({ ...draft, company: e.target.value })} />
          <input className="bg-[#0D1117] border border-[#1E2530] rounded px-3 py-2 text-sm" placeholder="로고 (2~3자)"
            value={draft.logo} onChange={e => setDraft({ ...draft, logo: e.target.value })} />
          <input className="bg-[#0D1117] border border-[#1E2530] rounded px-3 py-2 text-sm" placeholder="#RRGGBB" type="color"
            value={draft.color} onChange={e => setDraft({ ...draft, color: e.target.value })} />
          <select className="bg-[#0D1117] border border-[#1E2530] rounded px-3 py-2 text-sm"
            value={draft.badge}
            onChange={e => setDraft({ ...draft, badge: e.target.value, badgeColor: BADGE_COLOR_DEFAULT[e.target.value] || '' })}
          >
            {COMPETITOR_BADGES.map(b => <option key={b} value={b}>{b}</option>)}
          </select>
          <input className="bg-[#0D1117] border border-[#1E2530] rounded px-3 py-2 text-sm col-span-2" placeholder="헤드라인 *"
            value={draft.headline} onChange={e => setDraft({ ...draft, headline: e.target.value })} />
          <input className="bg-[#0D1117] border border-[#1E2530] rounded px-3 py-2 text-sm" placeholder="날짜 YYYY-MM-DD *"
            value={draft.date} onChange={e => setDraft({ ...draft, date: e.target.value })} type="date" />
          <input className="bg-[#0D1117] border border-[#1E2530] rounded px-3 py-2 text-sm" placeholder="출처"
            value={draft.source} onChange={e => setDraft({ ...draft, source: e.target.value })} />
          <textarea className="bg-[#0D1117] border border-[#1E2530] rounded px-3 py-2 text-sm col-span-4" placeholder="상세 *" rows={2}
            value={draft.detail} onChange={e => setDraft({ ...draft, detail: e.target.value })} />
          <input className="bg-[#0D1117] border border-[#1E2530] rounded px-3 py-2 text-sm col-span-4" placeholder="URL (선택)"
            value={draft.url} onChange={e => setDraft({ ...draft, url: e.target.value })} />
        </div>
        <div className="flex items-center justify-between mt-4">
          <div className="text-xs text-[#EF4444]">{addError}</div>
          <button
            onClick={handleAdd} disabled={adding}
            className="bg-[#00E5CC] text-[#0A0E1A] text-sm font-semibold px-4 py-2 rounded-lg disabled:opacity-50"
          >
            {adding ? '추가 중…' : '+ 추가'}
          </button>
        </div>
      </div>

      {/* 목록 */}
      {loading ? (
        <div className="text-center py-8 text-[#8B9BB4]">로딩 중…</div>
      ) : error ? (
        <div className="text-center py-6 text-[#EF4444]">{error}</div>
      ) : (
        <div className="space-y-3">
          {items.map(it => editId === it.id ? (
            <div key={it.id} className="bg-[#161B27] border border-[#00E5CC] rounded-xl p-4">
              <div className="grid grid-cols-4 gap-2 mb-2">
                <input className="bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-sm" placeholder="회사"
                  value={editDraft.company} onChange={e => setEditDraft({ ...editDraft, company: e.target.value })} />
                <input className="bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-sm" placeholder="로고"
                  value={editDraft.logo} onChange={e => setEditDraft({ ...editDraft, logo: e.target.value })} />
                <input className="bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-sm" type="color"
                  value={editDraft.color} onChange={e => setEditDraft({ ...editDraft, color: e.target.value })} />
                <select className="bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-sm"
                  value={editDraft.badge}
                  onChange={e => setEditDraft({ ...editDraft, badge: e.target.value, badgeColor: BADGE_COLOR_DEFAULT[e.target.value] || editDraft.badgeColor })}
                >
                  {COMPETITOR_BADGES.map(b => <option key={b} value={b}>{b}</option>)}
                </select>
                <input className="bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-sm col-span-2" placeholder="헤드라인"
                  value={editDraft.headline} onChange={e => setEditDraft({ ...editDraft, headline: e.target.value })} />
                <input className="bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-sm" type="date"
                  value={editDraft.date} onChange={e => setEditDraft({ ...editDraft, date: e.target.value })} />
                <input className="bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-sm" placeholder="출처"
                  value={editDraft.source} onChange={e => setEditDraft({ ...editDraft, source: e.target.value })} />
                <textarea className="bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-sm col-span-4" rows={2}
                  value={editDraft.detail} onChange={e => setEditDraft({ ...editDraft, detail: e.target.value })} />
                <input className="bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-sm col-span-4" placeholder="URL"
                  value={editDraft.url} onChange={e => setEditDraft({ ...editDraft, url: e.target.value })} />
              </div>
              <div className="flex gap-2">
                <button onClick={saveEdit} disabled={editBusy} className="bg-[#00E5CC] text-[#0A0E1A] text-xs font-semibold px-3 py-1.5 rounded disabled:opacity-50">저장</button>
                <button onClick={() => setEditId(null)} className="bg-[#1E2530] text-white text-xs px-3 py-1.5 rounded">취소</button>
              </div>
            </div>
          ) : (
            <div key={it.id} className="bg-[#161B27] border border-[#1E2530] rounded-xl p-4 flex items-start justify-between gap-4">
              <div className="flex items-start gap-3 flex-1">
                <div className="w-9 h-9 rounded-xl flex items-center justify-center text-xs font-bold flex-shrink-0"
                  style={{ backgroundColor: (it.color || '#1E2530') + '25', border: `1px solid ${(it.color || '#1E2530')}40` }}>
                  {it.logo || it.company.slice(0, 2).toUpperCase()}
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs text-[#8B9BB4]">{it.company}</span>
                    <span className={`text-xs px-2 py-0.5 rounded-full ${it.badgeColor || 'bg-[#1E2530] text-[#8B9BB4]'}`}>{it.badge}</span>
                    <span className="text-xs text-[#4A5568]">{it.date}</span>
                  </div>
                  <p className="text-sm font-semibold">{it.headline}</p>
                  <p className="text-xs text-[#8B9BB4] mt-1 line-clamp-2">{it.detail}</p>
                  {it.source && <p className="text-xs text-[#4A5568] mt-1">{it.source}</p>}
                </div>
              </div>
              <div className="flex gap-1 flex-shrink-0">
                <button onClick={() => startEdit(it)} className="text-xs px-2 py-1 rounded bg-[#1E2530] hover:bg-[#2A3545]">편집</button>
                <button onClick={() => handleDelete(it.id, it.company)} className="text-xs px-2 py-1 rounded bg-[#EF4444]/20 text-[#EF4444] hover:bg-[#EF4444]/30">
                  <i className="ri-delete-bin-line"></i>
                </button>
              </div>
            </div>
          ))}
          {items.length === 0 && <p className="text-center py-8 text-[#4A5568]">등록된 동향이 없습니다</p>}
        </div>
      )}
    </div>
  );
}
