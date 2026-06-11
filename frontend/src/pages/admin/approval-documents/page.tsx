import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  fetchIndicationGrid,
  uploadApprovalDocument,
  listApprovalDocuments,
  deleteApprovalDocument,
  approvalDocFileUrl,
  triggerFdaSync,
  type Agency,
  type IndicationGridRow,
  type ApprovalDocument,
} from '@/api/approvalDocs';
import { fetchMe } from '@/utils/authUsers';

const AGENCIES: Agency[] = ['FDA', 'EMA', 'MHRA', 'PMDA', 'TGA', 'MFDS'];

const AGENCY_FLAGS: Record<Agency, string> = {
  FDA: '🇺🇸', EMA: '🇪🇺', MHRA: '🇬🇧', PMDA: '🇯🇵', TGA: '🇦🇺', MFDS: '🇰🇷',
};

const PRODUCT_INN: Record<string, string> = {
  keytruda: 'pembrolizumab',
  welireg: 'belzutifan',
  lynparza: 'olaparib',
  lenvima: 'lenvatinib',
  januvia: 'sitagliptin',
  gardasil: 'human_papillomavirus_vaccine',
  prevymis: 'letermovir',
};


interface UploadModalState {
  open: boolean;
  indication: IndicationGridRow | null;
  agency: Agency | null;
}


export default function AdminApprovalDocumentsPage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);
  const [product, setProduct] = useState('keytruda');
  const [grid, setGrid] = useState<IndicationGridRow[]>([]);
  const [docsByCell, setDocsByCell] = useState<Record<string, ApprovalDocument[]>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [diseaseFilter, setDiseaseFilter] = useState<string>('');
  const [stageFilter, setStageFilter] = useState<string>('');
  const [biomarkerFilter, setBiomarkerFilter] = useState<string>('');
  const [modal, setModal] = useState<UploadModalState>({ open: false, indication: null, agency: null });
  const [fdaSyncing, setFdaSyncing] = useState(false);
  const [fdaSyncResult, setFdaSyncResult] = useState<string | null>(null);

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
      const g = await fetchIndicationGrid(product);
      setGrid(g.indications);

      const docs = await listApprovalDocuments({ product });
      const byCell: Record<string, ApprovalDocument[]> = {};
      for (const d of docs) {
        const k = `${d.indication_id}__${d.agency}`;
        (byCell[k] ??= []).push(d);
      }
      setDocsByCell(byCell);
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

  const diseases = useMemo(() => {
    const set = new Set<string>();
    grid.forEach(g => { if (g.disease) set.add(g.disease); });
    return [...set].sort();
  }, [grid]);

  const stages = useMemo(() => {
    const set = new Set<string>();
    grid.forEach(g => { if (g.stage) set.add(g.stage); });
    return [...set].sort();
  }, [grid]);

  const biomarkers = useMemo(() => {
    const set = new Set<string>();
    grid.forEach(g => { if (g.biomarker_class) set.add(g.biomarker_class); });
    return [...set].sort();
  }, [grid]);

  const filtered = useMemo(() => grid.filter(g => {
    if (diseaseFilter && g.disease !== diseaseFilter) return false;
    if (stageFilter && g.stage !== stageFilter) return false;
    if (biomarkerFilter && g.biomarker_class !== biomarkerFilter) return false;
    return true;
  }), [grid, diseaseFilter, stageFilter, biomarkerFilter]);

  const fdaSync = async () => {
    setFdaSyncing(true);
    setFdaSyncResult(null);
    try {
      const inn = PRODUCT_INN[product] || product;
      const r = await triggerFdaSync({ drug: inn, product_slug: product });
      const fda = r.agencies.find(a => a.agency === 'FDA');
      setFdaSyncResult(fda
        ? `FDA sync 완료: ok=${fda.ok} skipped=${fda.skipped} errors=${fda.errors}`
        : 'FDA agency 결과 없음');
      await reload();
    } catch (e) {
      setFdaSyncResult(`실패: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setFdaSyncing(false);
    }
  };

  if (!authChecked) {
    return <div className="p-8 text-[#8B9BB4]">권한 확인 중...</div>;
  }

  return (
    <div className="min-h-screen bg-[#0A0E1A] text-white p-6">
      <div className="max-w-[1600px] mx-auto space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold">허가문서 관리 — 국가별 PDF 업로드</h1>
            <p className="text-[#8B9BB4] text-xs mt-1">
              FDA 는 자동(LLM/스크레이퍼) sync. 다른 5국은 PDF 업로드 → 적응증 매칭 → DB 영구 저장.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <select
              className="bg-[#161B27] border border-[#1E2530] rounded px-3 py-1.5 text-sm"
              value={product}
              onChange={(e) => setProduct(e.target.value)}
            >
              {Object.keys(PRODUCT_INN).map(p => <option key={p} value={p}>{p}</option>)}
            </select>
            <button
              onClick={fdaSync}
              disabled={fdaSyncing}
              className="px-3 py-1.5 rounded bg-[#00E5CC] text-[#0A0E1A] text-xs font-bold disabled:opacity-50 cursor-pointer"
            >
              {fdaSyncing ? 'FDA sync 중...' : `FDA sync (${PRODUCT_INN[product] || product})`}
            </button>
          </div>
        </div>

        {fdaSyncResult && (
          <div className="bg-[#1E3A5F]/30 border border-[#1E3A5F] rounded-lg px-3 py-2 text-xs">
            {fdaSyncResult}
          </div>
        )}

        {/* 필터 */}
        <div className="flex items-center gap-3 bg-[#161B27] rounded-2xl border border-[#1E2530] p-4">
          <span className="text-[#8B9BB4] text-xs">적응증 필터:</span>
          <select className="bg-[#0A0E1A] border border-[#1E2530] rounded px-2 py-1 text-xs"
                  value={diseaseFilter} onChange={e => setDiseaseFilter(e.target.value)}>
            <option value="">disease (전체)</option>
            {diseases.map(d => <option key={d} value={d}>{d}</option>)}
          </select>
          <select className="bg-[#0A0E1A] border border-[#1E2530] rounded px-2 py-1 text-xs"
                  value={stageFilter} onChange={e => setStageFilter(e.target.value)}>
            <option value="">stage (전체)</option>
            {stages.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="bg-[#0A0E1A] border border-[#1E2530] rounded px-2 py-1 text-xs"
                  value={biomarkerFilter} onChange={e => setBiomarkerFilter(e.target.value)}>
            <option value="">biomarker (전체)</option>
            {biomarkers.map(b => <option key={b} value={b}>{b}</option>)}
          </select>
          <span className="ml-auto text-[#4A5568] text-[10px]">
            {filtered.length}/{grid.length} 적응증
          </span>
        </div>

        {/* 매트릭스 */}
        {loading && <div className="text-[#8B9BB4] text-sm">로딩 중...</div>}
        {error && <div className="text-red-400 text-sm">오류: {error}</div>}

        {!loading && !error && (
          <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-[#0D1117] sticky top-0">
                <tr>
                  <th className="text-left p-3 text-[#8B9BB4]">적응증</th>
                  {AGENCIES.map(a => (
                    <th key={a} className="p-3 text-[#8B9BB4] min-w-[120px]">
                      <span className="mr-1">{AGENCY_FLAGS[a]}</span>{a}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map(ind => (
                  <tr key={ind.indication_id} className="border-t border-[#1E2530] hover:bg-[#1E2530]/30">
                    <td className="p-3 align-top">
                      <div className="font-medium">{ind.title || ind.indication_id}</div>
                      <div className="text-[#8B9BB4] text-[10px] mt-1">
                        {ind.disease}{ind.line_of_therapy ? ` · ${ind.line_of_therapy}` : ''}
                        {ind.stage ? ` · ${ind.stage}` : ''}
                        {ind.biomarker_class ? ` · ${ind.biomarker_class}` : ''}
                      </div>
                    </td>
                    {AGENCIES.map(a => {
                      const cell = ind.agencies[a];
                      const docs = docsByCell[`${ind.indication_id}__${a}`] || [];
                      const has = cell.doc_count > 0 || cell.approval_date;
                      return (
                        <td key={a} className="p-2 align-top">
                          <button
                            onClick={() => setModal({ open: true, indication: ind, agency: a })}
                            className={`w-full text-left px-2 py-1 rounded border transition-colors ${
                              has
                                ? 'border-emerald-500/30 bg-emerald-500/5 hover:bg-emerald-500/10'
                                : 'border-dashed border-[#2A3545] hover:border-[#00E5CC]/40 hover:bg-[#00E5CC]/5'
                            }`}
                          >
                            {cell.approval_date && (
                              <div className="text-emerald-300 text-[10px] mb-1">
                                ✓ {cell.approval_date}
                              </div>
                            )}
                            {cell.doc_count > 0 ? (
                              <div className="text-[#7FCEFF] text-[10px]">📄 {cell.doc_count}건</div>
                            ) : (
                              <div className="text-[#4A5568] text-[10px]">＋ 업로드</div>
                            )}
                            {docs.slice(0, 2).map(d => (
                              <div key={d.id} className="text-[9px] text-[#8B9BB4] truncate mt-0.5">
                                {d.original_filename || `doc#${d.id}`}
                              </div>
                            ))}
                          </button>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {modal.open && modal.indication && modal.agency && (
          <UploadModal
            indication={modal.indication}
            agency={modal.agency}
            existingDocs={docsByCell[`${modal.indication.indication_id}__${modal.agency}`] || []}
            onClose={() => setModal({ open: false, indication: null, agency: null })}
            onChange={() => { reload(); }}
          />
        )}
      </div>
    </div>
  );
}


interface UploadModalProps {
  indication: IndicationGridRow;
  agency: Agency;
  existingDocs: ApprovalDocument[];
  onClose: () => void;
  onChange: () => void;
}


function UploadModal({ indication, agency, existingDocs, onClose, onChange }: UploadModalProps) {
  const [file, setFile] = useState<File | null>(null);
  const [approvalDate, setApprovalDate] = useState('');
  const [labelExcerpt, setLabelExcerpt] = useState('');
  const [labelUrl, setLabelUrl] = useState('');
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    if (!file) {
      setErr('PDF 파일을 선택하세요');
      return;
    }
    setSubmitting(true);
    setErr(null);
    try {
      await uploadApprovalDocument({
        file,
        indication_id: indication.indication_id,
        agency,
        approval_date: approvalDate || undefined,
        label_excerpt: labelExcerpt || undefined,
        label_url: labelUrl || undefined,
        notes: notes || undefined,
      });
      onChange();
      setFile(null);
      setApprovalDate('');
      setLabelExcerpt('');
      setLabelUrl('');
      setNotes('');
    } catch (e) {
      setErr(e instanceof Error ? e.message : '업로드 실패');
    } finally {
      setSubmitting(false);
    }
  };

  const remove = async (docId: number) => {
    if (!confirm('삭제하시겠습니까?')) return;
    try {
      await deleteApprovalDocument(docId);
      onChange();
    } catch (e) {
      alert(e instanceof Error ? e.message : '삭제 실패');
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
         onClick={onClose}>
      <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] max-w-2xl w-full max-h-[90vh] overflow-y-auto"
           onClick={e => e.stopPropagation()}>
        <div className="p-5 border-b border-[#1E2530] flex items-center justify-between">
          <div>
            <div className="text-xs text-[#8B9BB4]">
              {AGENCY_FLAGS[agency]} {agency} · {indication.disease}
              {indication.line_of_therapy ? ` · ${indication.line_of_therapy}` : ''}
              {indication.biomarker_class ? ` · ${indication.biomarker_class}` : ''}
            </div>
            <div className="text-white font-bold text-sm mt-1">
              {indication.title || indication.indication_id}
            </div>
          </div>
          <button onClick={onClose} className="text-[#8B9BB4] hover:text-white text-xl cursor-pointer">×</button>
        </div>

        <div className="p-5 space-y-3">
          {existingDocs.length > 0 && (
            <div className="space-y-2">
              <div className="text-[#8B9BB4] text-[11px]">기존 업로드 ({existingDocs.length}건)</div>
              {existingDocs.map(d => (
                <div key={d.id} className="flex items-center justify-between bg-[#0D1117] rounded p-2 text-xs">
                  <div className="flex-1 min-w-0">
                    <div className="text-white truncate">{d.original_filename || `doc#${d.id}`}</div>
                    <div className="text-[#8B9BB4] text-[10px]">
                      {d.approval_date ? `${d.approval_date} · ` : ''}
                      {d.uploaded_at?.slice(0, 16) || ''}
                      {d.uploaded_by ? ` · ${d.uploaded_by}` : ''}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <a href={approvalDocFileUrl(d.id)} target="_blank" rel="noopener noreferrer"
                       className="text-[#00E5CC] text-[10px] hover:underline">열기 ↗</a>
                    <button onClick={() => remove(d.id)} className="text-red-400 text-[10px] hover:underline cursor-pointer">삭제</button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="border-t border-[#1E2530] pt-3 space-y-2">
            <div className="text-[#8B9BB4] text-[11px]">신규 업로드</div>

            <input type="file" accept="application/pdf"
                   onChange={e => setFile(e.target.files?.[0] || null)}
                   className="w-full text-xs file:mr-3 file:px-3 file:py-1.5 file:rounded file:bg-[#00E5CC] file:text-[#0A0E1A] file:font-bold file:cursor-pointer" />

            <div className="grid grid-cols-2 gap-2">
              <label className="text-[10px] text-[#8B9BB4]">
                허가일자 (YYYY-MM-DD)
                <input type="date" value={approvalDate}
                       onChange={e => setApprovalDate(e.target.value)}
                       className="mt-1 w-full bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-xs" />
              </label>
              <label className="text-[10px] text-[#8B9BB4]">
                원본 URL (선택)
                <input type="url" value={labelUrl}
                       onChange={e => setLabelUrl(e.target.value)}
                       placeholder="https://..."
                       className="mt-1 w-full bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-xs" />
              </label>
            </div>

            <label className="text-[10px] text-[#8B9BB4] block">
              적응증 본문 발췌 (선택, ~500자)
              <textarea value={labelExcerpt} onChange={e => setLabelExcerpt(e.target.value)}
                        rows={3} className="mt-1 w-full bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-xs" />
            </label>

            <label className="text-[10px] text-[#8B9BB4] block">
              메모 (선택)
              <input type="text" value={notes} onChange={e => setNotes(e.target.value)}
                     className="mt-1 w-full bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-xs" />
            </label>

            {err && <div className="text-red-400 text-xs">{err}</div>}

            <div className="flex justify-end gap-2 pt-2">
              <button onClick={onClose}
                      className="px-3 py-1.5 rounded text-xs text-[#8B9BB4] hover:text-white cursor-pointer">
                닫기
              </button>
              <button onClick={submit} disabled={submitting || !file}
                      className="px-3 py-1.5 rounded bg-[#00E5CC] text-[#0A0E1A] text-xs font-bold disabled:opacity-50 cursor-pointer">
                {submitting ? '업로드 중...' : '업로드'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
