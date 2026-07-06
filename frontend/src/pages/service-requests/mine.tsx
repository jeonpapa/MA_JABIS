import { useEffect, useState } from 'react';
import {
  fetchMyRequests,
  fetchServiceRequest,
  REQUEST_TYPE_LABELS,
  PRIORITY_LABELS,
  STATUS_LABELS,
  EVENT_TYPE_LABELS,
  type SR,
  type SREvent,
} from '@/api/serviceRequests';

// ── 배지 톤 (daily-mailing-kanban badgeTone 미러) ──────────────────────────

function statusTone(status: string | null | undefined): string {
  const v = (status || '').toLowerCase();
  if (v === 'sent' || v === 'done' || v === 'confirmed') return 'bg-[#00E5CC]/10 text-[#00E5CC]';
  if (v === 'rejected') return 'bg-red-500/10 text-red-400';
  if (v === 'in_review' || v === 'packaged' || v === 'in_progress') return 'bg-[#F59E0B]/10 text-[#F59E0B]';
  if (v === 'wont_fix') return 'bg-[#1E2530] text-[#8B9BB4]';
  return 'bg-[#3B82F6]/10 text-[#60A5FA]';
}

/** 처리 결과 섹션 노출 대상 상태 (delegation-loop resolution) */
function isResolutionStatus(status: string | null | undefined): boolean {
  const v = (status || '').toLowerCase();
  return v === 'in_progress' || v === 'done' || v === 'wont_fix';
}

function priorityTone(priority: string | null | undefined): string {
  const v = (priority || '').toLowerCase();
  if (v === 'urgent') return 'bg-red-500/10 text-red-400';
  if (v === 'high') return 'bg-[#F59E0B]/10 text-[#F59E0B]';
  if (v === 'medium') return 'bg-[#3B82F6]/10 text-[#60A5FA]';
  return 'bg-[#1E2530] text-[#8B9BB4]';
}

function fmtDate(s: string | null | undefined): string {
  if (!s) return '—';
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? s : d.toLocaleString('ko-KR');
}

function statusLabel(status: string | null | undefined): string {
  if (!status) return '—';
  return STATUS_LABELS[status] ?? status;
}

// ── 페이지 ──────────────────────────────────────────────────────────────────

export default function MyRequestsPage() {
  const [items, setItems] = useState<SR[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<{ item: SR; events: SREvent[] } | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const reload = async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await fetchMyRequests();
      setItems(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : '요청 목록 조회 실패');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void reload();
  }, []);

  const openDetail = async (id: number) => {
    setSelectedId(id);
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    try {
      const r = await fetchServiceRequest(id);
      setDetail(r);
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : '상세 조회 실패');
    } finally {
      setDetailLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-5 h-5 flex items-center justify-center">
                <i className="ri-feedback-line text-[#00E5CC]"></i>
              </span>
              <h1 className="text-2xl font-bold text-white">내 개선 요청</h1>
            </div>
            <p className="text-[#8B9BB4] text-sm max-w-3xl">
              내가 보낸 서비스 보완/개선 요청과 처리 상태를 확인합니다. 새 요청은 화면 우측 하단의
              &ldquo;개선 요청&rdquo; 버튼으로 보낼 수 있습니다.
            </p>
          </div>
          <button
            onClick={reload}
            className="text-[#8B9BB4] text-xs hover:text-white cursor-pointer flex items-center gap-1 whitespace-nowrap"
          >
            <i className="ri-refresh-line"></i>새로고침
          </button>
        </div>
      </div>

      <div className="px-8 py-6 grid grid-cols-1 lg:grid-cols-5 gap-5 items-start">
        {/* 목록 */}
        <div className="lg:col-span-2 bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
          <h2 className="text-white font-bold text-sm mb-3">
            요청 목록 {!loading && !error && <span className="text-[#4A5568] font-normal">({items.length}건)</span>}
          </h2>

          {loading && (
            <div className="text-center py-10 text-sm text-[#8B9BB4]">
              <i className="ri-loader-4-line animate-spin mr-2"></i>불러오는 중…
            </div>
          )}
          {!loading && error && (
            <div className="text-center py-10">
              <p className="text-sm text-red-400">{error}</p>
              <button
                onClick={reload}
                className="mt-3 text-sm cursor-pointer hover:underline text-[#00E5CC]"
              >
                다시 시도
              </button>
            </div>
          )}
          {!loading && !error && items.length === 0 && (
            <p className="text-[#4A5568] text-sm py-6 text-center">
              아직 보낸 요청이 없습니다. 우측 하단 &ldquo;개선 요청&rdquo; 버튼으로 첫 요청을 남겨보세요.
            </p>
          )}

          {!loading && !error && items.length > 0 && (
            <ul className="space-y-2">
              {items.map(item => (
                <li key={item.id}>
                  <button
                    onClick={() => openDetail(item.id)}
                    className={`w-full text-left bg-[#0D1117] border rounded-xl p-3 space-y-1.5 cursor-pointer transition-colors ${
                      selectedId === item.id
                        ? 'border-[#00E5CC]/60'
                        : 'border-[#1E2530] hover:border-[#2A3441]'
                    }`}
                  >
                    <p className="text-white text-xs font-semibold leading-snug">
                      {item.title || '(제목 없음)'}
                    </p>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className={`text-[10px] px-2 py-0.5 rounded ${statusTone(item.status)}`}>
                        {statusLabel(item.status)}
                      </span>
                      <span className={`text-[10px] px-2 py-0.5 rounded ${priorityTone(item.priority)}`}>
                        {PRIORITY_LABELS[item.priority ?? ''] ?? item.priority ?? '—'}
                      </span>
                      <span className="text-[10px] px-2 py-0.5 rounded bg-[#1E2530] text-[#8B9BB4]">
                        {REQUEST_TYPE_LABELS[item.request_type ?? ''] ?? item.request_type ?? '—'}
                      </span>
                    </div>
                    <p className="text-[#4A5568] text-[10px]">
                      {item.page_label || item.page_path || '—'} · {fmtDate(item.created_at)}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* 상세 + 타임라인 */}
        <div className="lg:col-span-3 bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
          <h2 className="text-white font-bold text-sm mb-3">요청 상세</h2>

          {selectedId == null && (
            <p className="text-[#4A5568] text-sm py-6 text-center">
              왼쪽 목록에서 요청을 선택하면 상세와 처리 타임라인이 표시됩니다.
            </p>
          )}
          {selectedId != null && detailLoading && (
            <div className="text-center py-10 text-sm text-[#8B9BB4]">
              <i className="ri-loader-4-line animate-spin mr-2"></i>상세 로드 중…
            </div>
          )}
          {selectedId != null && !detailLoading && detailError && (
            <p className="text-sm text-red-400 py-6 text-center">{detailError}</p>
          )}

          {selectedId != null && !detailLoading && !detailError && detail && (
            <div className="space-y-4">
              <div>
                <p className="text-white text-base font-bold leading-snug">
                  {detail.item.title || '(제목 없음)'}
                </p>
                <div className="flex flex-wrap items-center gap-1.5 mt-2">
                  <span className={`text-[10px] px-2 py-0.5 rounded ${statusTone(detail.item.status)}`}>
                    {statusLabel(detail.item.status)}
                  </span>
                  <span className={`text-[10px] px-2 py-0.5 rounded ${priorityTone(detail.item.priority)}`}>
                    우선순위: {PRIORITY_LABELS[detail.item.priority ?? ''] ?? detail.item.priority ?? '—'}
                  </span>
                  <span className="text-[10px] px-2 py-0.5 rounded bg-[#1E2530] text-[#8B9BB4]">
                    유형: {REQUEST_TYPE_LABELS[detail.item.request_type ?? ''] ?? detail.item.request_type ?? '—'}
                  </span>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                <div className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3">
                  <p className="text-[#4A5568] text-[10px] mb-1">요청 페이지</p>
                  <p className="text-white">{detail.item.page_label || '—'}</p>
                  <p className="text-[#8B9BB4] truncate">{detail.item.page_path || '—'}</p>
                </div>
                <div className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3">
                  <p className="text-[#4A5568] text-[10px] mb-1">접수 / 최근 갱신</p>
                  <p className="text-white">{fmtDate(detail.item.created_at)}</p>
                  <p className="text-[#8B9BB4]">{fmtDate(detail.item.updated_at)}</p>
                </div>
              </div>

              <div className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3">
                <p className="text-[#4A5568] text-[10px] mb-1">설명</p>
                <p className="text-[#C9D4E3] text-xs whitespace-pre-wrap leading-relaxed">
                  {detail.item.body || '—'}
                </p>
              </div>
              <div className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3">
                <p className="text-[#4A5568] text-[10px] mb-1">기대 결과</p>
                <p className="text-[#C9D4E3] text-xs whitespace-pre-wrap leading-relaxed">
                  {detail.item.expected_outcome || '—'}
                </p>
              </div>

              {/* 처리 결과 (delegation-loop resolution) */}
              {isResolutionStatus(detail.item.status) && (
                <div className="bg-[#0D1117] border border-[#00E5CC]/20 rounded-xl p-3 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-white text-xs font-bold">
                      <i className="ri-tools-line mr-1 text-[#00E5CC]"></i>처리 결과
                    </p>
                    <span className={`text-[10px] px-2 py-0.5 rounded ${statusTone(detail.item.status)}`}>
                      {statusLabel(detail.item.status)}
                    </span>
                  </div>
                  {(detail.item.status || '').toLowerCase() === 'in_progress' && !detail.item.resolution_note && (
                    <p className="text-[#8B9BB4] text-xs">
                      요청이 접수되어 현재 작업이 진행 중입니다. 완료되면 처리 내용이 여기에 표시됩니다.
                    </p>
                  )}
                  {detail.item.resolution_note && (
                    <div>
                      <p className="text-[#4A5568] text-[10px] mb-1">처리 내용</p>
                      <p className="text-[#C9D4E3] text-xs whitespace-pre-wrap leading-relaxed">
                        {detail.item.resolution_note}
                      </p>
                    </div>
                  )}
                  {detail.item.commit_ref && (
                    <div>
                      <p className="text-[#4A5568] text-[10px] mb-1">반영 커밋</p>
                      <p className="text-[#C9D4E3] text-[11px] font-mono break-all">
                        {detail.item.commit_ref}
                      </p>
                    </div>
                  )}
                  {(detail.item.claimed_at || detail.item.resolved_at) && (
                    <p className="text-[#4A5568] text-[10px]">
                      {detail.item.claimed_at && <>작업 시작 {fmtDate(detail.item.claimed_at)}</>}
                      {detail.item.claimed_at && detail.item.resolved_at && ' · '}
                      {detail.item.resolved_at && <>처리 완료 {fmtDate(detail.item.resolved_at)}</>}
                    </p>
                  )}
                </div>
              )}

              {/* 타임라인 */}
              <div>
                <p className="text-white text-xs font-bold mb-2">
                  <i className="ri-time-line mr-1 text-[#00E5CC]"></i>처리 타임라인
                </p>
                {detail.events.length === 0 ? (
                  <p className="text-[#4A5568] text-xs">기록된 이벤트가 없습니다.</p>
                ) : (
                  <ul className="space-y-2 border-l border-[#1E2530] ml-1.5 pl-4">
                    {detail.events.map(ev => (
                      <li key={ev.id} className="relative">
                        <span className="absolute -left-[21px] top-1.5 w-2 h-2 rounded-full bg-[#00E5CC]"></span>
                        <p className="text-white text-xs font-semibold">
                          {EVENT_TYPE_LABELS[ev.event_type ?? ''] ?? ev.event_type ?? '이벤트'}
                          {ev.from_status && ev.to_status && (
                            <span className="text-[#8B9BB4] font-normal ml-1.5">
                              {statusLabel(ev.from_status)} → {statusLabel(ev.to_status)}
                            </span>
                          )}
                        </p>
                        <p className="text-[#4A5568] text-[10px]">{fmtDate(ev.created_at)}</p>
                        {ev.note && <p className="text-[#8B9BB4] text-[11px] mt-0.5">{ev.note}</p>}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
