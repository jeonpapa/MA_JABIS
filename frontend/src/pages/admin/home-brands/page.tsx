import { Fragment, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  listHomeBrands, createHomeBrand, updateHomeBrand, deleteHomeBrand, expandHomeBrands,
  approveHomeBrandCandidate,
  type HomeBrand,
} from '@/api/homeBrands';
import { fetchMe } from '@/utils/authUsers';

type Draft = {
  brand: string;
  therapeutic_area: string;
  active: boolean;
  dirty: boolean;
};

type DraftMap = Record<number, Draft>;

const toDraft = (it: HomeBrand): Draft => ({
  brand: it.brand,
  therapeutic_area: it.therapeutic_area ?? '',
  active: !!it.active,
  dirty: false,
});

export default function AdminHomeBrandsPage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);

  const [items, setItems] = useState<HomeBrand[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<DraftMap>({});
  const [savingId, setSavingId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const [newBrand, setNewBrand] = useState('');
  const [newArea, setNewArea] = useState('');
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const [expanding, setExpanding] = useState(false);
  const [expandMsg, setExpandMsg] = useState<string | null>(null);
  const [expandError, setExpandError] = useState<string | null>(null);

  const [candidateBusyId, setCandidateBusyId] = useState<number | null>(null);

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
      const rows = await listHomeBrands();
      setItems(rows);
      const next: DraftMap = {};
      for (const it of rows) next[it.id] = toDraft(it);
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
  }, [authChecked]);

  // 연관 후보(검토 대기): related 대기 큐 — 승인 시 시드의 보조 검색어로 편입 (독립 브랜드 아님)
  const candidates = useMemo(
    () => items.filter(it => it.source === 'related'),
    [items],
  );

  // 시드 브랜드: related 대기 큐 제외 전부
  const mainItems = useMemo(
    () => items.filter(it => it.source !== 'related'),
    [items],
  );

  // 대기 후보를 원본 시드(related_from) 아래에 중첩 표시하기 위한 그룹핑
  const candidatesBySeed = useMemo(() => {
    const map: Record<string, HomeBrand[]> = {};
    const seedNames = new Set(mainItems.map(it => it.brand));
    const orphans: HomeBrand[] = [];
    for (const c of candidates) {
      if (c.related_from && seedNames.has(c.related_from)) {
        (map[c.related_from] ??= []).push(c);
      } else {
        orphans.push(c);
      }
    }
    return { map, orphans };
  }, [candidates, mainItems]);

  const updateDraft = (id: number, patch: Partial<Draft>) => {
    setDrafts(prev => ({
      ...prev,
      [id]: { ...(prev[id] ?? { brand: '', therapeutic_area: '', active: true, dirty: false }), ...patch, dirty: true },
    }));
  };

  const handleSave = async (id: number) => {
    const d = drafts[id];
    if (!d) return;
    setSavingId(id);
    try {
      const saved = await updateHomeBrand(id, {
        brand: d.brand.trim(),
        therapeutic_area: d.therapeutic_area.trim() || null,
        active: d.active,
      });
      setItems(prev => prev.map(it => it.id === id ? saved : it));
      setDrafts(prev => ({ ...prev, [id]: toDraft(saved) }));
    } catch (e) {
      alert(e instanceof Error ? e.message : '저장 실패');
    } finally {
      setSavingId(null);
    }
  };

  const handleDelete = async (it: HomeBrand) => {
    if (!confirm(`'${it.brand}' 브랜드를 삭제할까요?`)) return;
    setDeletingId(it.id);
    try {
      await deleteHomeBrand(it.id);
      setItems(prev => prev.filter(x => x.id !== it.id));
      setDrafts(prev => {
        const next = { ...prev };
        delete next[it.id];
        return next;
      });
    } catch (e) {
      alert(e instanceof Error ? e.message : '삭제 실패');
    } finally {
      setDeletingId(null);
    }
  };

  const handleAdd = async () => {
    const brand = newBrand.trim();
    if (!brand) {
      setAddError('브랜드명을 입력해주세요.');
      return;
    }
    setAdding(true);
    setAddError(null);
    try {
      await createHomeBrand({
        brand,
        therapeutic_area: newArea.trim() || null,
        source: 'seed',
        active: true,
      });
      setNewBrand('');
      setNewArea('');
      await reload();
    } catch (e) {
      setAddError(e instanceof Error ? e.message : '추가 실패');
    } finally {
      setAdding(false);
    }
  };

  const handleApprove = async (it: HomeBrand) => {
    setCandidateBusyId(it.id);
    try {
      // 독립 브랜드 승격이 아니라 원본 시드의 보조 검색어(related_terms)로 편입
      await approveHomeBrandCandidate(it.id);
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : '승인 실패');
    } finally {
      setCandidateBusyId(null);
    }
  };

  const handleRemoveTerm = async (it: HomeBrand, term: string) => {
    if (!confirm(`'${it.brand}' 의 보조 검색어 '${term}' 를 제거할까요?`)) return;
    setSavingId(it.id);
    try {
      const saved = await updateHomeBrand(it.id, {
        related_terms: it.related_terms.filter(t => t !== term),
      });
      setItems(prev => prev.map(x => x.id === it.id ? saved : x));
    } catch (e) {
      alert(e instanceof Error ? e.message : '제거 실패');
    } finally {
      setSavingId(null);
    }
  };

  const handleRejectCandidate = async (it: HomeBrand) => {
    if (!confirm(`연관 후보 '${it.brand}' 를 삭제할까요?`)) return;
    setCandidateBusyId(it.id);
    try {
      await deleteHomeBrand(it.id);
      setItems(prev => prev.filter(x => x.id !== it.id));
    } catch (e) {
      alert(e instanceof Error ? e.message : '삭제 실패');
    } finally {
      setCandidateBusyId(null);
    }
  };

  const handleExpand = async () => {
    if (expanding) return;
    setExpanding(true);
    setExpandMsg(null);
    setExpandError(null);
    try {
      const r = await expandHomeBrands();
      setExpandMsg(`${r.candidates_added}건 후보 추가됨 (시드 ${r.seeds_processed}개 처리) — 검토 후 활성화하세요.`);
      await reload();
      setTimeout(() => setExpandMsg(null), 6000);
    } catch (e) {
      setExpandError(e instanceof Error ? e.message : '확장 실행 실패');
    } finally {
      setExpanding(false);
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
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-5 h-5 flex items-center justify-center">
                <i className="ri-capsule-line text-[#00E5CC]"></i>
              </span>
              <h1 className="text-2xl font-bold text-white">홈 브랜드 언급 — 관리</h1>
            </div>
            <p className="text-[#8B9BB4] text-sm">
              Home 브랜드 언급 카드가 집계하는 브랜드 목록. 활성 브랜드만 집계됩니다.
            </p>
          </div>
          <div className="flex flex-col items-end gap-1">
            <button
              onClick={handleExpand}
              disabled={expanding}
              className={`text-xs font-semibold px-4 py-2 rounded transition-colors cursor-pointer whitespace-nowrap flex items-center gap-2 ${
                !expanding
                  ? 'bg-[#00E5CC] text-[#0A0E1A] hover:bg-[#00C9B1]'
                  : 'bg-[#1E2530] text-[#4A5568] cursor-not-allowed'
              }`}
            >
              {expanding ? (
                <><i className="ri-loader-4-line animate-spin"></i>연관검색어 확장 실행 중…</>
              ) : (
                <><i className="ri-radar-line"></i>연관검색어 확장 실행</>
              )}
            </button>
            {expandMsg && (
              <p className="text-[#00E5CC] text-xs flex items-center gap-1">
                <i className="ri-check-line"></i>{expandMsg}
              </p>
            )}
            {expandError && (
              <p className="text-red-400 text-xs flex items-center gap-1">
                <i className="ri-error-warning-line"></i>{expandError}
              </p>
            )}
          </div>
        </div>
      </div>

      <div className="px-8 py-6 space-y-8 max-w-5xl">
        {loading && <p className="text-[#8B9BB4] text-sm">로드 중…</p>}
        {error && <p className="text-red-400 text-sm">{error}</p>}

        {!loading && (
          <>
            {/* 활성/시드 브랜드 (+ 각 시드 아래 보조 검색어 후보 중첩) */}
            <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] overflow-hidden">
              <div className="px-5 py-3 flex items-center gap-3 bg-[#0D1117]/50 border-b border-[#1E2530]">
                <h3 className="text-white font-semibold text-sm">활성/시드 브랜드</h3>
                <span className="text-[#8B9BB4] text-xs">{mainItems.length}건</span>
                {candidates.length > 0 && (
                  <span className="text-[#F59E0B] text-xs">보조 검색어 후보 {candidates.length}건 검토 대기</span>
                )}
                <button onClick={reload} className="ml-auto text-[#8B9BB4] text-xs hover:text-white cursor-pointer flex items-center gap-1">
                  <i className="ri-refresh-line"></i>새로고침
                </button>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-[#8B9BB4] text-[11px] border-b border-[#1E2530] uppercase tracking-wider">
                      <th className="text-left py-2 pl-5 pr-3 w-[26%]">브랜드</th>
                      <th className="text-left py-2 pr-3 w-[26%]">치료영역</th>
                      <th className="text-left py-2 pr-3 w-[100px]">출처</th>
                      <th className="text-center py-2 pr-3 w-[70px]">활성</th>
                      <th className="text-right py-2 pr-5 w-[140px]">조치</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mainItems.map(it => {
                      const d = drafts[it.id];
                      if (!d) return null;
                      const busy = savingId === it.id;
                      const del = deletingId === it.id;
                      const pending = candidatesBySeed.map[it.brand] ?? [];
                      return (
                        <Fragment key={it.id}>
                        <tr className="border-b border-[#1E2530]/50 last:border-b-0">
                          <td className="py-2 pl-5 pr-3">
                            <input
                              type="text"
                              value={d.brand}
                              onChange={e => updateDraft(it.id, { brand: e.target.value })}
                              className="w-full bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-white text-xs focus:outline-none focus:border-[#00E5CC]/50"
                            />
                            {it.related_terms.length > 0 && (
                              <div className="flex flex-wrap gap-1 mt-1.5">
                                {it.related_terms.map(term => (
                                  <span
                                    key={term}
                                    className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-[#00E5CC]/10 border border-[#00E5CC]/25 text-[#00E5CC]"
                                    title="승인된 보조 검색어 — 이 브랜드의 검색을 넓혀 함께 집계됩니다"
                                  >
                                    {term}
                                    <button
                                      onClick={() => handleRemoveTerm(it, term)}
                                      className="hover:text-white cursor-pointer"
                                      aria-label={`보조 검색어 ${term} 제거`}
                                    >
                                      <i className="ri-close-line"></i>
                                    </button>
                                  </span>
                                ))}
                              </div>
                            )}
                          </td>
                          <td className="py-2 pr-3">
                            <input
                              type="text"
                              value={d.therapeutic_area}
                              onChange={e => updateDraft(it.id, { therapeutic_area: e.target.value })}
                              placeholder="예: 항암, 당뇨"
                              className="w-full bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-white text-xs placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50"
                            />
                          </td>
                          <td className="py-2 pr-3">
                            <span className={`text-[10px] px-2 py-0.5 rounded-full ${
                              it.source === 'seed' ? 'bg-[#3B82F6]/15 text-[#3B82F6]' : 'bg-[#00E5CC]/15 text-[#00E5CC]'
                            }`}>
                              {it.source === 'seed' ? '시드' : '연관(승인됨)'}
                            </span>
                          </td>
                          <td className="py-2 pr-3 text-center">
                            <button
                              onClick={() => updateDraft(it.id, { active: !d.active })}
                              className={`inline-flex items-center justify-center w-10 h-6 rounded-full transition-colors cursor-pointer ${
                                d.active ? 'bg-[#00E5CC]' : 'bg-[#1E2530]'
                              }`}
                              aria-label="활성 여부 토글"
                            >
                              <span
                                className={`block w-4 h-4 rounded-full bg-white transform transition-transform ${
                                  d.active ? 'translate-x-2' : '-translate-x-2'
                                }`}
                              />
                            </button>
                          </td>
                          <td className="py-2 pr-5 text-right whitespace-nowrap">
                            <button
                              onClick={() => handleSave(it.id)}
                              disabled={!d.dirty || busy}
                              className={`text-xs font-semibold px-3 py-1 rounded transition-colors cursor-pointer mr-2 ${
                                d.dirty && !busy
                                  ? 'bg-[#00E5CC] text-[#0A0E1A] hover:bg-[#00C9B1]'
                                  : 'bg-[#1E2530] text-[#4A5568] cursor-not-allowed'
                              }`}
                            >
                              {busy ? '저장 중' : d.dirty ? '저장' : '완료'}
                            </button>
                            <button
                              onClick={() => handleDelete(it)}
                              disabled={del}
                              className="text-xs px-2 py-1 rounded bg-[#EF4444]/20 text-[#EF4444] hover:bg-[#EF4444]/30 disabled:opacity-50 cursor-pointer"
                            >
                              <i className="ri-delete-bin-line"></i>
                            </button>
                          </td>
                        </tr>

                        {/* 이 시드의 보조 검색어 후보 (검토 대기) — 승인 시 독립 브랜드가 아니라
                            시드의 보조 검색어로 편입되어 시드 집계에 합산 */}
                        {pending.map(c => {
                          const cBusy = candidateBusyId === c.id;
                          return (
                            <tr key={`cand-${c.id}`} className="border-b border-[#1E2530]/50 last:border-b-0 bg-[#0D1117]/30">
                              <td className="py-2 pl-5 pr-3" colSpan={3}>
                                <div className="flex items-center gap-2 pl-4">
                                  <i className="ri-corner-down-right-line text-[#4A5568]"></i>
                                  <span className="text-white text-xs font-medium">{c.brand}</span>
                                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-[#F59E0B]/15 text-[#F59E0B]">
                                    보조 검색어 후보
                                  </span>
                                </div>
                              </td>
                              <td className="py-2 pr-3"></td>
                              <td className="py-2 pr-5 text-right whitespace-nowrap">
                                <button
                                  onClick={() => handleApprove(c)}
                                  disabled={cBusy}
                                  className="text-xs font-semibold px-3 py-1 rounded bg-[#00E5CC] text-[#0A0E1A] hover:bg-[#00C9B1] disabled:opacity-50 cursor-pointer mr-2"
                                >
                                  {cBusy ? '처리 중…' : '이 브랜드의 보조 검색어로 추가'}
                                </button>
                                <button
                                  onClick={() => handleRejectCandidate(c)}
                                  disabled={cBusy}
                                  className="text-xs px-2 py-1 rounded bg-[#EF4444]/20 text-[#EF4444] hover:bg-[#EF4444]/30 disabled:opacity-50 cursor-pointer"
                                >
                                  <i className="ri-delete-bin-line"></i>
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                        </Fragment>
                      );
                    })}

                    {/* 추가 행 */}
                    <tr className="bg-[#0D1117]/40">
                      <td className="py-2 pl-5 pr-3">
                        <input
                          type="text"
                          value={newBrand}
                          onChange={e => setNewBrand(e.target.value)}
                          placeholder="새 브랜드명"
                          className="w-full bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-white text-xs placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50"
                        />
                      </td>
                      <td className="py-2 pr-3">
                        <input
                          type="text"
                          value={newArea}
                          onChange={e => setNewArea(e.target.value)}
                          placeholder="치료영역 (선택)"
                          className="w-full bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-white text-xs placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50"
                        />
                      </td>
                      <td className="py-2 pr-3 text-[#4A5568] text-[10px]">시드</td>
                      <td className="py-2 pr-3"></td>
                      <td className="py-2 pr-5 text-right whitespace-nowrap">
                        <button
                          onClick={handleAdd}
                          disabled={adding}
                          className="text-xs font-semibold px-3 py-1 rounded bg-[#00E5CC]/10 border border-[#00E5CC]/30 text-[#00E5CC] hover:bg-[#00E5CC]/20 disabled:opacity-50 cursor-pointer"
                        >
                          <i className="ri-add-line mr-1"></i>{adding ? '추가 중…' : '추가'}
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              {addError && <p className="text-red-400 text-xs px-5 pb-3">{addError}</p>}
              {mainItems.length === 0 && (
                <p className="text-[#4A5568] text-sm px-5 pb-5">등록된 브랜드가 없습니다.</p>
              )}
            </div>

            {/* 원본 시드가 없는 후보 (시드 삭제/이름 변경 등) — 승인 불가, 삭제만 가능 */}
            {candidatesBySeed.orphans.length > 0 && (
              <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] overflow-hidden">
                <div className="px-5 py-3 flex items-center gap-3 bg-[#0D1117]/50 border-b border-[#1E2530]">
                  <h3 className="text-white font-semibold text-sm">시드 없는 보조 검색어 후보</h3>
                  <span className="text-[#8B9BB4] text-xs">
                    {candidatesBySeed.orphans.length}건 — 원본 시드 브랜드가 삭제되었거나 이름이 변경됨 (삭제만 가능)
                  </span>
                </div>
                <div className="px-5 py-3 flex flex-wrap gap-2">
                  {candidatesBySeed.orphans.map(c => {
                    const cBusy = candidateBusyId === c.id;
                    return (
                      <span
                        key={c.id}
                        className="inline-flex items-center gap-2 text-xs px-3 py-1 rounded-full bg-[#0D1117] border border-[#1E2530] text-[#8B9BB4]"
                      >
                        {c.brand}
                        <span className="text-[#4A5568]">({c.related_from ?? '시드 미상'})</span>
                        <button
                          onClick={() => handleRejectCandidate(c)}
                          disabled={cBusy}
                          className="text-[#EF4444] hover:text-red-300 disabled:opacity-50 cursor-pointer"
                          aria-label={`후보 ${c.brand} 삭제`}
                        >
                          <i className="ri-delete-bin-line"></i>
                        </button>
                      </span>
                    );
                  })}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
