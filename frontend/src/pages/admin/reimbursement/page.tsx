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

// 내부 코드 → 읽을 수 있는 한글 (화면 표시용; 저장 값은 그대로)
const LINE_KR: Record<string, string> = {
  '1L': '1차', '2L': '2차', '2L+': '2차 이상', '3L': '3차', '3L+': '3차 이상',
  adjuvant: '수술 후 보조', neoadjuvant: '수술 전 선행', perioperative: '수술 전후',
};
const STAGE_KR: Record<string, string> = {
  metastatic: '전이성', advanced: '진행성', 'locally advanced': '국소진행성', locally_advanced: '국소진행성',
  unresectable: '절제 불가', resectable: '절제 가능', recurrent: '재발성',
  unresectable_or_metastatic: '절제 불가·전이성', locally_advanced_or_metastatic: '국소진행성·전이성',
};
const BIOMARKER_KR: Record<string, string> = {
  all_comers: '바이오마커 무관', pdl1_1: 'PD-L1 ≥1', pdl1_10: 'PD-L1 ≥10', pdl1_50: 'PD-L1 ≥50%',
  pdl1_pos: 'PD-L1 양성', msi_h: 'MSI-H/dMMR', tmb_h: 'TMB-H', her2_neg: 'HER2 음성', her2_pos: 'HER2 양성',
};
const krOf = (map: Record<string, string>, v: string | null) => (v ? map[v] ?? map[v.toLowerCase()] ?? v.replace(/_/g, ' ') : '');

type RowState = 'reimbursed' | 'not_reimbursed' | 'missing';
type ViewFilter = 'all' | 'missing' | 'no_evidence';
/** 체크리스트 행이 없으면(updated_at 없음) '미입력' — 토글 기본값(비급여)과 구분 */
const rowState = (it: ReimbursementItem, draftReimbursed?: boolean): RowState =>
  !it.updated_at ? 'missing' : (draftReimbursed ?? it.is_reimbursed) ? 'reimbursed' : 'not_reimbursed';
const lacksEvidence = (it: ReimbursementItem) => !!it.updated_at && it.is_reimbursed && (!it.effective_date || !it.notice_url);

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
  const [mfdsOnly, setMfdsOnly] = useState(true);  // 국내 MFDS 허가 적응증만 (기본 on)
  const [view, setView] = useState<ViewFilter>('all');

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
      const rows = await listReimbursement(product, mfdsOnly);
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
  }, [authChecked, product, mfdsOnly]);

  const visibleItems = useMemo(() => items.filter(it =>
    view === 'all' || (view === 'missing' ? !it.updated_at : lacksEvidence(it))), [items, view]);

  const grouped = useMemo(() => {
    const map = new Map<string, ReimbursementItem[]>();
    for (const it of visibleItems) {
      const key = it.disease || '기타';
      const arr = map.get(key) ?? [];
      arr.push(it);
      map.set(key, arr);
    }
    return Array.from(map.entries()).sort((a, b) => b[1].length - a[1].length);
  }, [visibleItems]);

  const totals = useMemo(() => {
    const total = items.length;
    const reimbursed = items.filter(it => drafts[it.indication_id]?.is_reimbursed).length;
    const dirty = items.filter(it => drafts[it.indication_id]?.dirty).length;
    const missing = items.filter(it => !it.updated_at).length;
    const noEvidence = items.filter(lacksEvidence).length;
    const notReimbursed = items.filter(it => it.updated_at && !drafts[it.indication_id]?.is_reimbursed).length;
    return { total, reimbursed, dirty, missing, noEvidence, notReimbursed };
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
    const label = DISEASE_KR[disease] ?? disease;
    if (!window.confirm(`${label} 적응증 ${ids.length}건을 모두 '${value ? '급여' : '비급여'}'로 바꿉니다.\n저장 버튼을 누르기 전까지는 반영되지 않습니다. 계속할까요?`)) return;
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
          HIRA 「암환자에게 사용되는 약제 및 요법」 공고 기준으로 적응증별 급여 여부·개시일·조건·근거 공고를 관리합니다. 저장하면 대시보드 카드에 바로 반영됩니다.
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
          <label className="flex items-center gap-2 cursor-pointer select-none" title="국내 MFDS 허가가 있는 적응증만 표시 (외국 적응증 제외)">
            <input
              type="checkbox"
              checked={mfdsOnly}
              onChange={e => setMfdsOnly(e.target.checked)}
              className="accent-[#00E5CC] w-4 h-4"
            />
            <span className="text-[#8B9BB4] text-xs">국내 허가만 보기 (MFDS)</span>
          </label>
          <div className="text-[#8B9BB4] text-sm">
            총 <span className="text-white font-semibold">{totals.total}</span> 적응증 ·
            급여 <span className="text-[#00E5CC] font-semibold ml-1">{totals.reimbursed}</span> ·
            비급여 <span className="text-[#FF6B6B] font-semibold ml-1">{totals.notReimbursed}</span> ·
            미입력 <span className="text-[#F59E0B] font-semibold ml-1">{totals.missing}</span>
            {totals.dirty > 0 && (
              <span className="ml-3 text-[#FFA500]">저장 안 된 변경 {totals.dirty}건</span>
            )}
          </div>
          <div className="flex items-center gap-1 rounded-lg p-1 bg-[#1E2530]" role="group" aria-label="보기 필터">
            {([['all', `전체 ${totals.total}`], ['missing', `미입력 ${totals.missing}`], ['no_evidence', `근거 누락 ${totals.noEvidence}`]] as [ViewFilter, string][]).map(([k, label]) => (
              <button key={k} onClick={() => setView(k)} aria-pressed={view === k}
                className={`text-xs px-3 py-1.5 rounded-md cursor-pointer ${view === k ? 'bg-[#161B27] text-white font-semibold shadow' : 'text-[#8B9BB4]'}`}>{label}</button>
            ))}
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
          <p className="text-[#4A5568] text-sm">이 제품의 적응증 목록이 비어 있습니다.</p>
        )}
        {!loading && items.length > 0 && visibleItems.length === 0 && (
          <p className="text-[#4A5568] text-sm">{view === 'missing' ? '미입력 적응증이 없습니다.' : '급여인데 개시일·근거 링크가 빠진 적응증이 없습니다.'}</p>
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
                   title="이 암종 전체를 급여로 표시 (저장 전까지 미반영)">이 암종 전체 급여</button>
                  <button
                    onClick={() => bulkToggleDisease(disease, false)}
                    className="text-[10px] px-2 py-1 rounded bg-[#FF6B6B]/10 text-[#FF6B6B] hover:bg-[#FF6B6B]/20 cursor-pointer"
                   title="이 암종 전체를 비급여로 표시 (저장 전까지 미반영)">이 암종 전체 비급여</button>
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
                        <th className="text-right py-2 pr-5 w-[80px]">행 저장</th>
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
                              <div className="text-[#8B9BB4] text-[11px] mt-0.5">
                                {[krOf(LINE_KR, it.line_of_therapy), krOf(STAGE_KR, it.stage), krOf(BIOMARKER_KR, it.biomarker_class)]
                                  .filter(Boolean).join(' · ')}
                              </div>
                              <div className="flex flex-wrap items-center gap-1.5 mt-1">
                                {(() => {
                                  const st = rowState(it, d.dirty ? d.is_reimbursed : undefined);
                                  const cls = st === 'missing' ? 'bg-[#F59E0B]/10 text-[#F59E0B]' : st === 'reimbursed' ? 'bg-[#00E5CC]/10 text-[#00E5CC]' : 'bg-[#1E2530] text-[#8B9BB4]';
                                  return <span className={`text-[10px] px-1.5 py-0.5 rounded ${cls}`}>{st === 'missing' ? '미입력' : st === 'reimbursed' ? '급여' : '비급여'}</span>;
                                })()}
                                {lacksEvidence(it) && <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#F59E0B]/10 text-[#F59E0B]">근거 누락</span>}
                                {it.mfds_date && <span className="text-[#4A5568] text-[11px]">식약처 허가일 {it.mfds_date}</span>}
                              </div>
                              <div className="text-[#4A5568] text-[10px] mt-0.5 font-mono break-all" title="적응증 ID">{it.indication_id}</div>
                              {it.updated_at && <div className="text-[#4A5568] text-[10px] mt-0.5">최종 수정 {it.updated_at.slice(0, 10)}{it.updated_by ? ` · ${it.updated_by}` : ''}</div>}
                            </td>
                            <td className="py-3 pr-3 text-center">
                              <button
                                onClick={() => updateDraft(it.indication_id, { is_reimbursed: !d.is_reimbursed })}
                                className={`inline-flex items-center justify-center w-10 h-6 rounded-full transition-colors cursor-pointer ${
                                  d.is_reimbursed ? 'bg-[#00E5CC]' : 'bg-[#1E2530]'
                                }`}
                                aria-label={`급여 여부: ${d.is_reimbursed ? '급여' : '비급여'} (눌러서 변경)`}
                                aria-pressed={d.is_reimbursed}
                              >
                                <span
                                  className={`block w-4 h-4 rounded-full bg-white transform transition-transform ${
                                    d.is_reimbursed ? 'translate-x-2' : '-translate-x-2'
                                  }`}
                                />
                              </button>
                              <div className={`text-[10px] mt-1 ${d.is_reimbursed ? 'text-[#00E5CC]' : 'text-[#8B9BB4]'}`}>{d.is_reimbursed ? '급여' : '비급여'}</div>
                              {d.is_reimbursed && !d.effective_date && <div className="text-[10px] mt-0.5 text-[#F59E0B]">개시일 필요</div>}
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
                                placeholder="예: PD-L1 TPS≥50%, 1차 단독요법, 급여인정기관 요건"
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
                                {busy ? '저장 중' : d.dirty ? '저장' : '저장됨'}
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
