import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  listReimbursement, saveReimbursement,
  type ReimbursementItem, type ReimbursementPatch,
} from '@/api/reimbursement';
import { fetchMe } from '@/utils/authUsers';

type DraftMap = Record<string, ReimbursementPatch & { dirty: boolean }>;

const DISEASE_KR: Record<string, string> = {
  NSCLC: '비소세포폐암', MEL: '흑색종', HNSCC: '두경부암',
  cHL: '호지킨림프종', UC: '요로상피암', GC: '위암/위식도접합부암',
  ESC: '식도암', EC: '자궁내막암', CC: '자궁경부암', TNBC: '삼중음성유방암',
  RCC: '신세포암', HCC: '간세포암', CRC: '대장암', MCC: '메르켈세포암',
  BTC: '담도암', MPM: '악성흉막중피종', PMBCL: '원발성종격동B세포림프종',
  cSCC: '피부편평세포암', OC: '난소암', SOLID: '고형암(MSI-H/TMB-H)',
};

export default function AdminReimbursementPage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);
  const [product, setProduct] = useState<string>('keytruda');
  const [items, setItems] = useState<ReimbursementItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<DraftMap>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const [bulkSaving, setBulkSaving] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

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
      const rows = await listReimbursement(product);
      setItems(rows);
      const next: DraftMap = {};
      for (const r of rows) {
        next[r.indication_id] = {
          is_reimbursed: r.is_reimbursed,
          effective_date: r.effective_date ?? '',
          criteria_text: r.criteria_text ?? '',
          notice_date: r.notice_date ?? '',
          notice_url: r.notice_url ?? '',
          dirty: false,
        };
      }
      setDrafts(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : '조회 실패');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (authChecked) reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authChecked, product]);

  const grouped = useMemo(() => {
    const map = new Map<string, ReimbursementItem[]>();
    for (const it of items) {
      const key = it.disease || '기타';
      const arr = map.get(key) ?? [];
      arr.push(it);
      map.set(key, arr);
    }
    return Array.from(map.entries()).sort((a, b) => b[1].length - a[1].length);
  }, [items]);

  const totals = useMemo(() => {
    const total = items.length;
    const reimbursed = items.filter(it => drafts[it.indication_id]?.is_reimbursed).length;
    const dirty = items.filter(it => drafts[it.indication_id]?.dirty).length;
    return { total, reimbursed, dirty };
  }, [items, drafts]);

  const products = useMemo(() => {
    const set = new Set<string>(['keytruda']);
    items.forEach(it => set.add(it.product));
    return Array.from(set).sort();
  }, [items]);

  const updateDraft = (id: string, patch: Partial<ReimbursementPatch>) => {
    setDrafts(prev => ({
      ...prev,
      [id]: { ...(prev[id] ?? { is_reimbursed: false, dirty: false }), ...patch, dirty: true },
    }));
  };

  const bulkToggleDisease = (disease: string, value: boolean) => {
    const ids = items.filter(it => (it.disease || '기타') === disease).map(it => it.indication_id);
    setDrafts(prev => {
      const next = { ...prev };
      for (const id of ids) {
        next[id] = { ...(prev[id] ?? { is_reimbursed: false, dirty: false }), is_reimbursed: value, dirty: true };
      }
      return next;
    });
  };

  const handleSave = async (id: string) => {
    const d = drafts[id];
    if (!d) return;
    setSavingId(id);
    try {
      const saved = await saveReimbursement(id, {
        is_reimbursed: d.is_reimbursed,
        effective_date: d.effective_date || null,
        criteria_text: d.criteria_text || null,
        notice_date: d.notice_date || null,
        notice_url: d.notice_url || null,
      });
      setItems(prev => prev.map(it => it.indication_id === id ? saved : it));
      setDrafts(prev => ({
        ...prev,
        [id]: {
          is_reimbursed: saved.is_reimbursed,
          effective_date: saved.effective_date ?? '',
          criteria_text: saved.criteria_text ?? '',
          notice_date: saved.notice_date ?? '',
          notice_url: saved.notice_url ?? '',
          dirty: false,
        },
      }));
    } catch (e) {
      alert(e instanceof Error ? e.message : '저장 실패');
    } finally {
      setSavingId(null);
    }
  };

  const handleSaveAll = async () => {
    const dirtyIds = items.filter(it => drafts[it.indication_id]?.dirty).map(it => it.indication_id);
    if (dirtyIds.length === 0) return;
    setBulkSaving(true);
    const failures: string[] = [];
    for (const id of dirtyIds) {
      const d = drafts[id];
      try {
        const saved = await saveReimbursement(id, {
          is_reimbursed: d.is_reimbursed,
          effective_date: d.effective_date || null,
          criteria_text: d.criteria_text || null,
          notice_date: d.notice_date || null,
          notice_url: d.notice_url || null,
        });
        setItems(prev => prev.map(it => it.indication_id === id ? saved : it));
        setDrafts(prev => ({
          ...prev,
          [id]: {
            is_reimbursed: saved.is_reimbursed,
            effective_date: saved.effective_date ?? '',
            criteria_text: saved.criteria_text ?? '',
            notice_date: saved.notice_date ?? '',
            notice_url: saved.notice_url ?? '',
            dirty: false,
          },
        }));
      } catch {
        failures.push(id);
      }
    }
    setBulkSaving(false);
    if (failures.length > 0) alert(`${failures.length}건 저장 실패`);
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
          <span className="w-5 h-5 flex items-center justify-center">
            <i className="ri-check-double-line text-[#00E5CC]"></i>
          </span>
          <h1 className="text-2xl font-bold text-white">적응증 급여 체크리스트 — 관리</h1>
        </div>
        <p className="text-[#8B9BB4] text-sm">
          HIRA 항암화학요법 공고 기반 수동 편집. 적응증별 급여 여부 / 조건 / 공고 링크 를 저장하면 Home 카드에 자동 반영됩니다.
        </p>
      </div>

      <div className="px-8 py-6 space-y-5 max-w-7xl">
        {/* 필터 + 요약 + 일괄 저장 */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5 flex items-center gap-6 flex-wrap">
          <div className="flex items-center gap-2">
            <label className="text-[#8B9BB4] text-xs">제품</label>
            <select
              value={product}
              onChange={e => setProduct(e.target.value)}
              className="bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-[#00E5CC]/50"
            >
              {products.map(p => (<option key={p} value={p}>{p}</option>))}
            </select>
          </div>
          <div className="text-[#8B9BB4] text-sm">
            총 <span className="text-white font-semibold">{totals.total}</span> 적응증 /
            급여 <span className="text-[#00E5CC] font-semibold ml-1">{totals.reimbursed}</span> /
            비급여 <span className="text-[#FF6B6B] font-semibold ml-1">{totals.total - totals.reimbursed}</span>
            {totals.dirty > 0 && (
              <span className="ml-3 text-[#FFA500]">변경 {totals.dirty}건 대기</span>
            )}
          </div>
          <div className="ml-auto flex items-center gap-3">
            <button
              onClick={handleSaveAll}
              disabled={totals.dirty === 0 || bulkSaving}
              className={`text-xs font-semibold px-4 py-2 rounded transition-colors cursor-pointer ${
                totals.dirty > 0 && !bulkSaving
                  ? 'bg-[#00E5CC] text-[#0A0E1A] hover:bg-[#00C9B1]'
                  : 'bg-[#1E2530] text-[#4A5568] cursor-not-allowed'
              }`}
            >
              {bulkSaving ? '저장 중…' : `변경사항 모두 저장${totals.dirty > 0 ? ` (${totals.dirty})` : ''}`}
            </button>
            <button onClick={reload} className="text-[#8B9BB4] text-xs hover:text-white cursor-pointer flex items-center gap-1">
              <i className="ri-refresh-line"></i>새로고침
            </button>
          </div>
        </div>

        {loading && <p className="text-[#8B9BB4] text-sm">로드 중…</p>}
        {error && <p className="text-red-400 text-sm">{error}</p>}
        {!loading && items.length === 0 && (
          <p className="text-[#4A5568] text-sm">해당 product 의 indications_master 가 비어있습니다.</p>
        )}

        {/* 질환별 그룹 */}
        {!loading && grouped.map(([disease, group]) => {
          const isCollapsed = collapsed[disease];
          const reimbursedInGroup = group.filter(it => drafts[it.indication_id]?.is_reimbursed).length;
          const kr = DISEASE_KR[disease] ?? disease;
          return (
            <div key={disease} className="bg-[#161B27] rounded-2xl border border-[#1E2530] overflow-hidden">
              <div className="px-5 py-3 flex items-center gap-3 bg-[#0D1117]/50 border-b border-[#1E2530]">
                <button
                  onClick={() => setCollapsed(prev => ({ ...prev, [disease]: !prev[disease] }))}
                  className="text-[#8B9BB4] hover:text-white cursor-pointer w-5 h-5 flex items-center justify-center"
                  aria-label="접기/펼치기"
                >
                  <i className={`ri-arrow-${isCollapsed ? 'right' : 'down'}-s-line`}></i>
                </button>
                <h3 className="text-white font-semibold text-sm">
                  {kr} <span className="text-[#4A5568] ml-1 text-xs">({disease})</span>
                </h3>
                <span className="text-[#8B9BB4] text-xs">
                  {group.length}건 / 급여 <span className="text-[#00E5CC]">{reimbursedInGroup}</span>
                </span>
                <div className="ml-auto flex items-center gap-2">
                  <button
                    onClick={() => bulkToggleDisease(disease, true)}
                    className="text-[10px] px-2 py-1 rounded bg-[#00E5CC]/10 text-[#00E5CC] hover:bg-[#00E5CC]/20 cursor-pointer"
                  >모두 급여</button>
                  <button
                    onClick={() => bulkToggleDisease(disease, false)}
                    className="text-[10px] px-2 py-1 rounded bg-[#FF6B6B]/10 text-[#FF6B6B] hover:bg-[#FF6B6B]/20 cursor-pointer"
                  >모두 비급여</button>
                </div>
              </div>

              {!isCollapsed && (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-[#8B9BB4] text-[11px] border-b border-[#1E2530] uppercase tracking-wider">
                        <th className="text-left py-2 pl-5 pr-3 w-[22%]">적응증</th>
                        <th className="text-center py-2 pr-3 w-[70px]">급여</th>
                        <th className="text-left py-2 pr-3 w-[110px]">개시일</th>
                        <th className="text-left py-2 pr-3">조건 / 제한</th>
                        <th className="text-left py-2 pr-3 w-[110px]">공고일</th>
                        <th className="text-left py-2 pr-3 w-[180px]">공고 링크</th>
                        <th className="text-right py-2 pr-5 w-[70px]">저장</th>
                      </tr>
                    </thead>
                    <tbody>
                      {group.map(it => {
                        const d = drafts[it.indication_id];
                        if (!d) return null;
                        const busy = savingId === it.indication_id;
                        return (
                          <tr key={it.indication_id} className="border-b border-[#1E2530]/50 last:border-b-0 align-top">
                            <td className="py-3 pl-5 pr-3">
                              <div className="text-white text-sm font-medium leading-tight">
                                {it.title ?? it.indication_id}
                              </div>
                              <div className="text-[#4A5568] text-[11px] mt-0.5">
                                {[it.line_of_therapy, it.stage, it.biomarker_class]
                                  .filter(Boolean).join(' · ')}
                              </div>
                            </td>
                            <td className="py-3 pr-3 text-center">
                              <button
                                onClick={() => updateDraft(it.indication_id, { is_reimbursed: !d.is_reimbursed })}
                                className={`inline-flex items-center justify-center w-10 h-6 rounded-full transition-colors cursor-pointer ${
                                  d.is_reimbursed ? 'bg-[#00E5CC]' : 'bg-[#1E2530]'
                                }`}
                                aria-label="급여 여부 토글"
                              >
                                <span
                                  className={`block w-4 h-4 rounded-full bg-white transform transition-transform ${
                                    d.is_reimbursed ? 'translate-x-2' : '-translate-x-2'
                                  }`}
                                />
                              </button>
                            </td>
                            <td className="py-3 pr-3">
                              <input
                                type="date"
                                value={d.effective_date ?? ''}
                                onChange={e => updateDraft(it.indication_id, { effective_date: e.target.value })}
                                className="w-full bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-white text-xs focus:outline-none focus:border-[#00E5CC]/50"
                              />
                            </td>
                            <td className="py-3 pr-3">
                              <textarea
                                value={d.criteria_text ?? ''}
                                onChange={e => updateDraft(it.indication_id, { criteria_text: e.target.value })}
                                placeholder="예: 본인부담 30%, MSI-H 확인 필요"
                                rows={2}
                                className="w-full bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-white text-xs placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50 resize-y"
                              />
                            </td>
                            <td className="py-3 pr-3">
                              <input
                                type="date"
                                value={d.notice_date ?? ''}
                                onChange={e => updateDraft(it.indication_id, { notice_date: e.target.value })}
                                className="w-full bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-white text-xs focus:outline-none focus:border-[#00E5CC]/50"
                              />
                            </td>
                            <td className="py-3 pr-3">
                              <input
                                type="url"
                                value={d.notice_url ?? ''}
                                onChange={e => updateDraft(it.indication_id, { notice_url: e.target.value })}
                                placeholder="https://hira.or.kr/…"
                                className="w-full bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-white text-xs placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50"
                              />
                            </td>
                            <td className="py-3 pr-5 text-right whitespace-nowrap">
                              <button
                                onClick={() => handleSave(it.indication_id)}
                                disabled={!d.dirty || busy}
                                className={`text-xs font-semibold px-3 py-1 rounded transition-colors cursor-pointer ${
                                  d.dirty && !busy
                                    ? 'bg-[#00E5CC] text-[#0A0E1A] hover:bg-[#00C9B1]'
                                    : 'bg-[#1E2530] text-[#4A5568] cursor-not-allowed'
                                }`}
                              >
                                {busy ? '저장 중' : d.dirty ? '저장' : '완료'}
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
