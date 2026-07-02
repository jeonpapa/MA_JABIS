import { useEffect, useState } from 'react';
import {
  downloadPolicyDocument,
  fetchPolicyDocumentText,
  fetchPolicyEventDetail,
  PolicyEventDetail,
  PolicyEventDocument,
} from '@/api/policyIntelligence';

// 룰 엔진(대문자) + Hermes 큐레이션(소문자, "low" 포함) 모두 색상 매핑 — 대소문자 무관
function severityTone(sev?: string): string {
  const map: Record<string, string> = {
    'very high': 'bg-red-50 text-red-700 border-red-200',
    high: 'bg-orange-50 text-orange-700 border-orange-200',
    'medium-high': 'bg-amber-50 text-amber-700 border-amber-200',
    medium: 'bg-blue-50 text-blue-700 border-blue-200',
    low: 'bg-slate-100 text-slate-600 border-slate-200',
  };
  return map[(sev || '').toLowerCase()] || 'bg-gray-50 text-gray-700 border-gray-200';
}

function fmt(value: string | null | undefined): string {
  if (!value) return '-';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value.slice(0, 10);
  return d.toLocaleDateString('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

/** 정책/위원회 이벤트 상세 모달 — 메일 본문 + 첨부(추출텍스트 열람/원본 다운로드). */
export default function PolicyEventModal({ eventId, onClose }: { eventId: string; onClose: () => void }) {
  const [detail, setDetail] = useState<PolicyEventDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const [docText, setDocText] = useState<Record<string, string>>({});
  const [docBusy, setDocBusy] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    setLoading(true); setError('');
    fetchPolicyEventDetail(eventId)
      .then(d => { if (mounted) setDetail(d); })
      .catch(e => { if (mounted) setError(e instanceof Error ? e.message : '상세를 불러오지 못했습니다.'); })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, [eventId]);

  const toggleDocText = async (doc: PolicyEventDocument) => {
    if (openDoc === doc.id) { setOpenDoc(null); return; }
    setOpenDoc(doc.id);
    if (!docText[doc.id] && doc.text_available) {
      setDocBusy(doc.id);
      try {
        const r = await fetchPolicyDocumentText(doc.id);
        setDocText(prev => ({ ...prev, [doc.id]: r.text }));
      } catch (e) {
        setDocText(prev => ({ ...prev, [doc.id]: `텍스트를 불러오지 못했습니다: ${e instanceof Error ? e.message : ''}` }));
      } finally {
        setDocBusy(null);
      }
    }
  };

  const handleDownload = async (doc: PolicyEventDocument) => {
    setDocBusy(doc.id);
    try { await downloadPolicyDocument(doc); }
    catch (e) { setError(e instanceof Error ? e.message : '다운로드 실패'); }
    finally { setDocBusy(null); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-4 sm:p-8" onClick={onClose}>
      <div className="w-full max-w-3xl rounded-2xl bg-white shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-4 border-b border-gray-200 p-5">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-teal-700">관련 메일 원문</p>
            <h3 className="mt-1 text-lg font-bold leading-6 text-gray-950">{detail?.subject || '(제목 없음)'}</h3>
            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-500">
              <span>{fmt(detail?.date)}</span>
              {detail?.from && <span>· {detail.from}</span>}
              {detail?.agencies?.length ? <span>· {detail.agencies.join(', ')}</span> : null}
              {detail?.severity && (
                <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-semibold ${severityTone(detail.severity)}`}>{detail.severity}</span>
              )}
              {detail?.curation_source && (
                <span className={`text-xs px-2 py-0.5 rounded ${detail.curation_source === 'hermes' ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>
                  {detail.curation_source === 'hermes' ? 'AI 큐레이션' : '규칙 기본값'}
                </span>
              )}
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700"><i className="ri-close-line text-xl" /></button>
        </div>

        <div className="max-h-[70vh] overflow-y-auto p-5">
          {loading && <p className="text-sm text-gray-500">불러오는 중...</p>}
          {error && <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</p>}
          {detail && (
            <>
              {detail.msd_implication?.rationale && (
                <div className="mt-3 text-sm">
                  <div className="font-medium">MSD 시사점</div>
                  <p>{detail.msd_implication.rationale}</p>
                  <p className="text-slate-500">→ {detail.msd_implication.next_action}</p>
                </div>
              )}
              {(detail.evidence_quotes?.length ?? 0) > 0 && (
                <ul className="mt-2 text-xs text-slate-600 space-y-1">
                  {detail.evidence_quotes!.map((q, i) => (
                    <li key={i}>“{q.quote}” <span className="text-slate-400">— {q.source}{q.loc ? ` ${q.loc}` : ''}</span></li>
                  ))}
                </ul>
              )}
              <section>
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">메일 본문</p>
                {detail.email_body
                  ? <pre className="whitespace-pre-wrap break-words rounded-xl border border-gray-200 bg-gray-50 p-4 text-sm leading-6 text-gray-800 font-sans">{detail.email_body}</pre>
                  : <p className="rounded-xl border border-dashed border-gray-200 p-4 text-sm text-gray-500">본문 텍스트가 없습니다.</p>}
              </section>

              <section className="mt-5">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">첨부 파일 ({detail.documents.length})</p>
                {detail.documents.length === 0 && <p className="text-sm text-gray-500">첨부 없음</p>}
                <div className="space-y-2">
                  {detail.documents.map(doc => (
                    <div key={doc.id} className="rounded-xl border border-gray-200">
                      <div className="flex items-center justify-between gap-3 p-3">
                        <div className="flex min-w-0 items-center gap-2">
                          <i className="ri-file-text-line text-gray-500" />
                          <span className="truncate text-sm font-medium text-gray-800" title={doc.filename || ''}>{doc.filename}</span>
                          <span className="shrink-0 text-xs text-gray-400">{doc.char_count.toLocaleString()}자</span>
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          {doc.text_available && (
                            <button onClick={() => toggleDocText(doc)} className="rounded-lg border border-gray-200 px-2.5 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50">
                              {openDoc === doc.id ? '접기' : '본문 보기'}
                            </button>
                          )}
                          {doc.file_available && (
                            <button onClick={() => handleDownload(doc)} disabled={docBusy === doc.id} className="rounded-lg border border-teal-200 px-2.5 py-1 text-xs font-semibold text-teal-700 hover:bg-teal-50 disabled:opacity-50">
                              {docBusy === doc.id ? '...' : '원본 다운로드'}
                            </button>
                          )}
                        </div>
                      </div>
                      {openDoc === doc.id && (
                        <div className="border-t border-gray-100 p-3">
                          {docBusy === doc.id && !docText[doc.id]
                            ? <p className="text-xs text-gray-500">텍스트 불러오는 중...</p>
                            : <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-gray-50 p-3 text-xs leading-6 text-gray-700 font-sans">{docText[doc.id]}</pre>}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
