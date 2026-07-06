import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchMe } from '@/utils/authUsers';
import { ApiError } from '@/api/client';
import {
  adminListRequests,
  adminPatchRequest,
  adminPackage,
  adminConfirm,
  adminSend,
  adminClaim,
  adminResolve,
  fetchServiceRequest,
  REQUEST_TYPE_LABELS,
  PRIORITY_LABELS,
  STATUS_LABELS,
  EVENT_TYPE_LABELS,
  EMPTY_CHECKLIST,
  type SR,
  type SREvent,
  type SRStatus,
  type SRPriority,
  type SRRequestType,
  type SRResolveStatus,
  type ChecklistState,
} from '@/api/serviceRequests';

// ── 배지/포맷 헬퍼 (daily-mailing-kanban 패턴) ──────────────────────────────

function statusTone(status: string | null | undefined): string {
  const v = (status || '').toLowerCase();
  if (v === 'sent' || v === 'done' || v === 'confirmed') return 'bg-[#00E5CC]/10 text-[#00E5CC]';
  if (v === 'rejected') return 'bg-red-500/10 text-red-400';
  if (v === 'in_review' || v === 'packaged' || v === 'in_progress') return 'bg-[#F59E0B]/10 text-[#F59E0B]';
  if (v === 'wont_fix') return 'bg-[#1E2530] text-[#8B9BB4]';
  return 'bg-[#3B82F6]/10 text-[#60A5FA]';
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

const STATUS_OPTIONS: SRStatus[] = [
  'open',
  'in_review',
  'packaged',
  'confirmed',
  'sent',
  'in_progress',
  'done',
  'wont_fix',
  'rejected',
];
const RESOLVE_STATUS_OPTIONS: SRResolveStatus[] = ['done', 'wont_fix'];
const PRIORITY_OPTIONS: SRPriority[] = ['low', 'medium', 'high', 'urgent'];
const TYPE_OPTIONS: SRRequestType[] = ['bug', 'improvement', 'feature', 'data', 'other'];

const CHECKLIST_ITEMS: { key: keyof ChecklistState; label: string }[] = [
  { key: 'scope_clear', label: '요청 범위(scope)가 명확하다' },
  { key: 'context_redacted', label: '컨텍스트에서 민감정보가 레닥션되었다' },
  { key: 'no_secrets', label: '패키지에 토큰/비밀번호/시크릿이 없다' },
  { key: 'expected_outcome_defined', label: '기대 결과가 정의되어 있다' },
  { key: 'no_deploy_ack', label: '승인 전 배포/푸시 금지를 확인했다' },
];

const inputCls =
  'w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-xs placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50 transition-colors';
const selectCls = `${inputCls} cursor-pointer`;

function normalizeChecklist(raw: Partial<ChecklistState> | null | undefined): ChecklistState {
  return {
    ...EMPTY_CHECKLIST,
    ...(raw && typeof raw === 'object' ? raw : {}),
  };
}

// ── 페이지 ──────────────────────────────────────────────────────────────────

export default function AdminServiceRequestsPage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);

  const [items, setItems] = useState<SR[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [filterStatus, setFilterStatus] = useState('');
  const [filterPriority, setFilterPriority] = useState('');
  const [filterType, setFilterType] = useState('');

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selected, setSelected] = useState<SR | null>(null);
  const [events, setEvents] = useState<SREvent[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  // 트리아지 편집 상태
  const [editStatus, setEditStatus] = useState<SRStatus>('open');
  const [editPriority, setEditPriority] = useState<SRPriority>('medium');
  const [editType, setEditType] = useState<SRRequestType>('improvement');
  const [adminNote, setAdminNote] = useState('');

  // Claude 패키지 상태
  const [packageMd, setPackageMd] = useState('');
  const [copied, setCopied] = useState(false);

  // 확인 체크리스트
  const [checklist, setChecklist] = useState<ChecklistState>({ ...EMPTY_CHECKLIST });

  // 처리(resolve) 패널 상태
  const [resolveStatus, setResolveStatus] = useState<SRResolveStatus>('done');
  const [resolveNote, setResolveNote] = useState('');
  const [resolveCommitRef, setResolveCommitRef] = useState('');

  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState('');
  const [actionErr, setActionErr] = useState('');

  // admin 셀프 가드 (daily-mailing-kanban 패턴)
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
      const rows = await adminListRequests({
        status: filterStatus || undefined,
        priority: filterPriority || undefined,
        type: filterType || undefined,
      });
      setItems(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : '요청 목록 조회 실패');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (authChecked) void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authChecked, filterStatus, filterPriority, filterType]);

  /** 선택된 요청 + 목록 행을 동시에 갱신 */
  const applyItem = (item: SR) => {
    setSelected(item);
    setItems(prev => prev.map(row => (row.id === item.id ? { ...row, ...item } : row)));
    // 편집/체크리스트/패키지 상태 동기화
    setEditStatus((item.status as SRStatus) || 'open');
    setEditPriority((item.priority as SRPriority) || 'medium');
    setEditType((item.request_type as SRRequestType) || 'improvement');
    setAdminNote(item.admin_note ?? '');
    setChecklist(normalizeChecklist(item.checklist));
    // 처리 패널 프리필 (필드 부재 시 기본값 — 방어적)
    const st = (item.status || '').toLowerCase();
    setResolveStatus(st === 'wont_fix' ? 'wont_fix' : 'done');
    setResolveNote(item.resolution_note ?? '');
    setResolveCommitRef(item.commit_ref ?? '');
  };

  const openDetail = async (id: number) => {
    setSelectedId(id);
    setSelected(null);
    setEvents([]);
    setDetailError(null);
    setActionMsg('');
    setActionErr('');
    setCopied(false);
    setDetailLoading(true);
    try {
      const r = await fetchServiceRequest(id);
      applyItem(r.item);
      setEvents(r.events);
      setPackageMd(r.item.package_markdown ?? '');
    } catch (e) {
      setDetailError(e instanceof Error ? e.message : '상세 조회 실패');
    } finally {
      setDetailLoading(false);
    }
  };

  const refreshEvents = async (id: number) => {
    try {
      const r = await fetchServiceRequest(id);
      setEvents(r.events);
    } catch {
      // 이벤트 재조회 실패는 치명적이지 않음
    }
  };

  const runAction = async (name: string, fn: () => Promise<void>) => {
    setBusyAction(name);
    setActionMsg('');
    setActionErr('');
    setCopied(false);
    try {
      await fn();
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.code === 'CHECKLIST_INCOMPLETE') {
          setActionErr('체크리스트 5개 항목을 모두 확인해야 confirm 할 수 있습니다.');
        } else if (e.code === 'INVALID') {
          setActionErr(`처리 입력이 유효하지 않습니다 (상태 done/wont_fix + 처리 내용 필수): ${e.message}`);
        } else if (e.code === 'NOT_CLAIMABLE') {
          setActionErr("sent 상태에서만 '작업 시작'을 할 수 있습니다 (이미 처리 중이거나 종료됨).");
        } else if (e.code === 'NOT_RESOLVABLE') {
          setActionErr('sent 또는 작업 중 상태에서만 처리 결과를 기록할 수 있습니다.');
        } else if (e.code === 'NOT_CONFIRMED' || e.status === 409) {
          setActionErr('confirm(최종 확인) 이후에만 Claude 로 전달할 수 있습니다.');
        } else {
          setActionErr(e.message);
        }
      } else {
        setActionErr(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusyAction(null);
    }
  };

  const handleSaveTriage = () =>
    runAction('triage', async () => {
      if (!selected) return;
      const item = await adminPatchRequest(selected.id, {
        status: editStatus,
        priority: editPriority,
        request_type: editType,
        admin_note: adminNote,
      });
      applyItem(item);
      await refreshEvents(item.id);
      setActionMsg('트리아지 내용이 저장되었습니다.');
    });

  const handlePackage = (mode: 'generate' | 'save_draft' | 'save_final') =>
    runAction(`package:${mode}`, async () => {
      if (!selected) return;
      const r = await adminPackage(
        selected.id,
        mode === 'generate' ? { mode } : { mode, markdown: packageMd },
      );
      applyItem(r.item);
      if (r.markdown) setPackageMd(r.markdown);
      else if (r.item.package_markdown != null) setPackageMd(r.item.package_markdown);
      await refreshEvents(r.item.id);
      setActionMsg(
        mode === 'generate'
          ? '패키지 마크다운이 생성되었습니다. 검토·편집 후 저장하세요.'
          : mode === 'save_draft'
            ? '패키지 초안이 저장되었습니다.'
            : '패키지 최종본이 저장되었습니다.',
      );
    });

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(packageMd);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setActionErr('클립보드 복사에 실패했습니다. 텍스트를 직접 선택해 복사해주세요.');
    }
  };

  const allChecked = CHECKLIST_ITEMS.every(c => checklist[c.key]);

  const handleConfirm = () =>
    runAction('confirm', async () => {
      if (!selected) return;
      const item = await adminConfirm(selected.id, checklist);
      applyItem(item);
      await refreshEvents(item.id);
      setActionMsg('최종 확인(confirm)되었습니다. 이제 Claude 로 전달할 수 있습니다.');
    });

  const handleSend = () =>
    runAction('send', async () => {
      if (!selected) return;
      const r = await adminSend(selected.id);
      applyItem(r.item);
      await refreshEvents(r.item.id);
      setActionMsg('Claude 핸드오프 패키지가 확정(sent) 저장되었습니다.');
    });

  const handleClaim = () =>
    runAction('claim', async () => {
      if (!selected) return;
      const item = await adminClaim(selected.id);
      applyItem(item);
      await refreshEvents(item.id);
      setActionMsg('작업 시작(claim)으로 기록되었습니다. 상태: 작업 중');
    });

  const handleResolve = () =>
    runAction('resolve', async () => {
      if (!selected) return;
      const item = await adminResolve(selected.id, {
        status: resolveStatus,
        resolution_note: resolveNote,
        ...(resolveCommitRef.trim() ? { commit_ref: resolveCommitRef.trim() } : {}),
      });
      applyItem(item);
      await refreshEvents(item.id);
      setActionMsg(
        resolveStatus === 'done'
          ? '처리 결과가 완료(done)로 기록되었습니다.'
          : '처리 결과가 반영 안 함(wont_fix)으로 기록되었습니다.',
      );
    });

  if (!authChecked) {
    return (
      <div className="min-h-screen flex items-center justify-center text-[#8B9BB4] text-sm">
        <i className="ri-loader-4-line animate-spin mr-2"></i>권한 확인 중…
      </div>
    );
  }

  const currentStatus = (selected?.status || '').toLowerCase();
  // sent 여부는 상태만으로 판정 불가 (done/wont_fix 는 수동 경로로도 도달) — sent_at 존재를 함께 확인
  const isSent = currentStatus === 'sent' || Boolean(selected?.sent_at);

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="w-5 h-5 flex items-center justify-center">
                <i className="ri-customer-service-2-line text-[#00E5CC]"></i>
              </span>
              <h1 className="text-2xl font-bold text-white">서비스 보완 요청 — 관리</h1>
            </div>
            <p className="text-[#8B9BB4] text-sm max-w-3xl">
              사용자 개선 요청을 트리아지하고 Claude 핸드오프 마크다운 패키지로 정리·확인·전달(기록)합니다.
              전달은 외부 호출 없이 최종 마크다운을 저장/표시하는 것까지입니다.
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

      <div className="px-8 py-6 space-y-5">
        {/* 필터 */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-4 flex flex-wrap items-end gap-3">
          <div className="w-40">
            <label className="block text-[#4A5568] text-[10px] font-semibold mb-1">상태</label>
            <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)} className={selectCls}>
              <option value="">전체</option>
              {STATUS_OPTIONS.map(s => (
                <option key={s} value={s}>{STATUS_LABELS[s]}</option>
              ))}
            </select>
          </div>
          <div className="w-36">
            <label className="block text-[#4A5568] text-[10px] font-semibold mb-1">우선순위</label>
            <select value={filterPriority} onChange={e => setFilterPriority(e.target.value)} className={selectCls}>
              <option value="">전체</option>
              {PRIORITY_OPTIONS.map(p => (
                <option key={p} value={p}>{PRIORITY_LABELS[p]}</option>
              ))}
            </select>
          </div>
          <div className="w-36">
            <label className="block text-[#4A5568] text-[10px] font-semibold mb-1">유형</label>
            <select value={filterType} onChange={e => setFilterType(e.target.value)} className={selectCls}>
              <option value="">전체</option>
              {TYPE_OPTIONS.map(t => (
                <option key={t} value={t}>{REQUEST_TYPE_LABELS[t]}</option>
              ))}
            </select>
          </div>
          <button
            onClick={() => setFilterStatus(filterStatus === 'sent' ? '' : 'sent')}
            className={`text-[10px] font-semibold px-3 py-2 rounded-lg border transition-colors cursor-pointer ${
              filterStatus === 'sent'
                ? 'bg-[#00E5CC]/10 border-[#00E5CC]/40 text-[#00E5CC]'
                : 'bg-[#0D1117] border-[#1E2530] text-[#8B9BB4] hover:text-white'
            }`}
          >
            <i className="ri-inbox-unarchive-line mr-1"></i>Outbox (전달됨)
          </button>
          <p className="text-[#4A5568] text-[10px] ml-auto">
            {loading ? '조회 중…' : `${items.length}건`}
          </p>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-5 gap-5 items-start">
          {/* 목록 */}
          <div className="xl:col-span-2 bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
            <h2 className="text-white font-bold text-sm mb-3">요청 목록</h2>
            {loading && (
              <div className="text-center py-10 text-sm text-[#8B9BB4]">
                <i className="ri-loader-4-line animate-spin mr-2"></i>불러오는 중…
              </div>
            )}
            {!loading && error && (
              <div className="text-center py-10">
                <p className="text-sm text-red-400">{error}</p>
                <button onClick={reload} className="mt-3 text-sm cursor-pointer hover:underline text-[#00E5CC]">
                  다시 시도
                </button>
              </div>
            )}
            {!loading && !error && items.length === 0 && (
              <p className="text-[#4A5568] text-sm py-6 text-center">조건에 맞는 요청이 없습니다.</p>
            )}
            {!loading && !error && items.length > 0 && (
              <ul className="space-y-2 max-h-[70vh] overflow-y-auto pr-1">
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
                        #{item.id} {item.title || '(제목 없음)'}
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
                      <p className="text-[#4A5568] text-[10px] truncate">
                        {item.owner_email || '—'} · {item.page_label || item.page_path || '—'} · {fmtDate(item.created_at)}
                      </p>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {/* 상세 / 트리아지 / 패키지 / 체크리스트 / 전달 */}
          <div className="xl:col-span-3 space-y-5">
            {selectedId == null && (
              <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
                <p className="text-[#4A5568] text-sm py-6 text-center">
                  왼쪽 목록에서 요청을 선택하면 트리아지·패키지·전달 작업을 진행할 수 있습니다.
                </p>
              </div>
            )}
            {selectedId != null && detailLoading && (
              <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5 text-center py-10 text-sm text-[#8B9BB4]">
                <i className="ri-loader-4-line animate-spin mr-2"></i>상세 로드 중…
              </div>
            )}
            {selectedId != null && !detailLoading && detailError && (
              <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5 text-center py-8">
                <p className="text-sm text-red-400">{detailError}</p>
              </div>
            )}

            {selectedId != null && !detailLoading && !detailError && selected && (
              <>
                {/* 요청 본문 */}
                <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5 space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-white text-base font-bold leading-snug">
                        #{selected.id} {selected.title || '(제목 없음)'}
                      </p>
                      <p className="text-[#4A5568] text-[10px] mt-1">
                        {selected.owner_email || '—'} · {selected.page_label || selected.page_path || '—'} ·{' '}
                        접수 {fmtDate(selected.created_at)}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5 justify-end">
                      <span className={`text-[10px] px-2 py-0.5 rounded ${statusTone(selected.status)}`}>
                        {statusLabel(selected.status)}
                      </span>
                      {isSent && (
                        <span className="text-[10px] px-2 py-0.5 rounded bg-[#00E5CC]/10 text-[#00E5CC]">
                          <i className="ri-send-plane-2-line mr-0.5"></i>전달됨 {fmtDate(selected.sent_at)}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                    <div className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3">
                      <p className="text-[#4A5568] text-[10px] mb-1">설명</p>
                      <p className="text-[#C9D4E3] whitespace-pre-wrap leading-relaxed">{selected.body || '—'}</p>
                    </div>
                    <div className="bg-[#0D1117] border border-[#1E2530] rounded-xl p-3">
                      <p className="text-[#4A5568] text-[10px] mb-1">기대 결과</p>
                      <p className="text-[#C9D4E3] whitespace-pre-wrap leading-relaxed">
                        {selected.expected_outcome || '—'}
                      </p>
                    </div>
                  </div>
                  {selected.source_url && (
                    <p className="text-[#4A5568] text-[10px] truncate">
                      <i className="ri-link mr-1"></i>{selected.source_url}
                    </p>
                  )}

                  {/* 처리(resolution) 현황 — 필드가 있을 때만 노출 (방어적) */}
                  {(selected.claimed_at || selected.claimed_by || selected.resolved_at ||
                    selected.resolved_by || selected.resolution_note || selected.commit_ref) && (
                    <div className="bg-[#0D1117] border border-[#00E5CC]/20 rounded-xl p-3 space-y-2 text-xs">
                      <p className="text-white text-xs font-bold">
                        <i className="ri-tools-line mr-1 text-[#00E5CC]"></i>처리 현황
                      </p>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                        {(selected.claimed_by || selected.claimed_at) && (
                          <div>
                            <p className="text-[#4A5568] text-[10px] mb-0.5">작업 시작 (claim)</p>
                            <p className="text-[#C9D4E3]">{selected.claimed_by || '—'}</p>
                            <p className="text-[#8B9BB4] text-[10px]">{fmtDate(selected.claimed_at)}</p>
                          </div>
                        )}
                        {(selected.resolved_by || selected.resolved_at) && (
                          <div>
                            <p className="text-[#4A5568] text-[10px] mb-0.5">처리 완료 (resolve)</p>
                            <p className="text-[#C9D4E3]">{selected.resolved_by || '—'}</p>
                            <p className="text-[#8B9BB4] text-[10px]">{fmtDate(selected.resolved_at)}</p>
                          </div>
                        )}
                      </div>
                      {selected.commit_ref && (
                        <div>
                          <p className="text-[#4A5568] text-[10px] mb-0.5">반영 커밋</p>
                          <p className="text-[#C9D4E3] text-[11px] font-mono break-all">{selected.commit_ref}</p>
                        </div>
                      )}
                      {selected.resolution_note && (
                        <div>
                          <p className="text-[#4A5568] text-[10px] mb-0.5">처리 내용</p>
                          <p className="text-[#C9D4E3] whitespace-pre-wrap leading-relaxed">
                            {selected.resolution_note}
                          </p>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* 트리아지 편집 */}
                <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5 space-y-3">
                  <h3 className="text-white font-bold text-sm">
                    <i className="ri-equalizer-line mr-1 text-[#00E5CC]"></i>트리아지
                  </h3>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <div>
                      <label className="block text-[#4A5568] text-[10px] font-semibold mb-1">상태</label>
                      <select value={editStatus} onChange={e => setEditStatus(e.target.value as SRStatus)} className={selectCls}>
                        {STATUS_OPTIONS.map(s => (
                          <option key={s} value={s}>{STATUS_LABELS[s]}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="block text-[#4A5568] text-[10px] font-semibold mb-1">우선순위</label>
                      <select value={editPriority} onChange={e => setEditPriority(e.target.value as SRPriority)} className={selectCls}>
                        {PRIORITY_OPTIONS.map(p => (
                          <option key={p} value={p}>{PRIORITY_LABELS[p]}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="block text-[#4A5568] text-[10px] font-semibold mb-1">유형</label>
                      <select value={editType} onChange={e => setEditType(e.target.value as SRRequestType)} className={selectCls}>
                        {TYPE_OPTIONS.map(t => (
                          <option key={t} value={t}>{REQUEST_TYPE_LABELS[t]}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <div>
                    <label className="block text-[#4A5568] text-[10px] font-semibold mb-1">관리자 메모</label>
                    <textarea
                      value={adminNote}
                      onChange={e => setAdminNote(e.target.value)}
                      rows={2}
                      placeholder="트리아지 판단, 처리 방향 등"
                      className={`${inputCls} resize-none`}
                    />
                  </div>
                  <div className="flex justify-end">
                    <button
                      onClick={handleSaveTriage}
                      disabled={busyAction !== null}
                      className="flex items-center gap-1.5 bg-[#00E5CC]/10 border border-[#00E5CC]/30 text-[#00E5CC] text-xs font-semibold px-4 py-2 rounded-lg hover:bg-[#00E5CC]/20 transition-colors disabled:opacity-60 cursor-pointer"
                    >
                      <i className={busyAction === 'triage' ? 'ri-loader-4-line animate-spin' : 'ri-save-3-line'}></i>
                      저장
                    </button>
                  </div>
                </div>

                {/* Claude 패키지 */}
                <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5 space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="text-white font-bold text-sm">
                      <i className="ri-file-code-line mr-1 text-[#00E5CC]"></i>Claude 패키지
                    </h3>
                    <span className={`text-[10px] px-2 py-0.5 rounded ${
                      selected.package_status === 'final'
                        ? 'bg-[#00E5CC]/10 text-[#00E5CC]'
                        : selected.package_status === 'draft'
                          ? 'bg-[#F59E0B]/10 text-[#F59E0B]'
                          : 'bg-[#1E2530] text-[#8B9BB4]'
                    }`}>
                      {selected.package_status === 'final' ? '최종본' : selected.package_status === 'draft' ? '초안' : '패키지 없음'}
                    </span>
                  </div>
                  <p className="text-[#8B9BB4] text-xs">
                    요청 내용을 Claude 핸드오프 마크다운으로 생성한 뒤 검토·편집해 저장합니다.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <button
                      onClick={() => handlePackage('generate')}
                      disabled={busyAction !== null}
                      className="flex items-center gap-1.5 bg-[#7C3AED]/10 border border-[#7C3AED]/30 text-[#A78BFA] text-xs font-semibold px-3 py-2 rounded-lg hover:bg-[#7C3AED]/20 transition-colors disabled:opacity-60 cursor-pointer"
                    >
                      <i className={busyAction === 'package:generate' ? 'ri-loader-4-line animate-spin' : 'ri-magic-line'}></i>
                      패키지 생성
                    </button>
                    <button
                      onClick={() => handlePackage('save_draft')}
                      disabled={busyAction !== null || !packageMd.trim()}
                      className="flex items-center gap-1.5 bg-[#1E2530] text-[#8B9BB4] text-xs font-semibold px-3 py-2 rounded-lg hover:text-white transition-colors disabled:opacity-60 cursor-pointer"
                    >
                      <i className={busyAction === 'package:save_draft' ? 'ri-loader-4-line animate-spin' : 'ri-draft-line'}></i>
                      초안 저장
                    </button>
                    <button
                      onClick={() => handlePackage('save_final')}
                      disabled={busyAction !== null || !packageMd.trim()}
                      className="flex items-center gap-1.5 bg-[#00E5CC]/10 border border-[#00E5CC]/30 text-[#00E5CC] text-xs font-semibold px-3 py-2 rounded-lg hover:bg-[#00E5CC]/20 transition-colors disabled:opacity-60 cursor-pointer"
                    >
                      <i className={busyAction === 'package:save_final' ? 'ri-loader-4-line animate-spin' : 'ri-checkbox-circle-line'}></i>
                      최종 저장
                    </button>
                    <button
                      onClick={handleCopy}
                      disabled={!packageMd.trim()}
                      className="ml-auto flex items-center gap-1.5 bg-[#1E2530] text-[#8B9BB4] text-xs font-semibold px-3 py-2 rounded-lg hover:text-white transition-colors disabled:opacity-60 cursor-pointer"
                    >
                      <i className={copied ? 'ri-check-line text-[#00E5CC]' : 'ri-clipboard-line'}></i>
                      {copied ? '복사됨' : '복사'}
                    </button>
                  </div>
                  <textarea
                    value={packageMd}
                    onChange={e => setPackageMd(e.target.value)}
                    rows={12}
                    placeholder="패키지 생성 버튼을 누르면 마크다운이 여기에 표시됩니다. 직접 편집할 수 있습니다."
                    className={`${inputCls} font-mono text-[11px] leading-relaxed resize-y`}
                  />
                </div>

                {/* 확인 체크리스트 + confirm */}
                <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5 space-y-3">
                  <h3 className="text-white font-bold text-sm">
                    <i className="ri-shield-check-line mr-1 text-[#00E5CC]"></i>최종 확인 체크리스트
                  </h3>
                  <div className="space-y-2">
                    {CHECKLIST_ITEMS.map(c => (
                      <label
                        key={c.key}
                        className="flex items-center gap-2.5 bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          checked={checklist[c.key]}
                          onChange={e => setChecklist(prev => ({ ...prev, [c.key]: e.target.checked }))}
                          className="w-3.5 h-3.5 accent-[#00E5CC] cursor-pointer"
                        />
                        <span className={`text-xs ${checklist[c.key] ? 'text-white' : 'text-[#8B9BB4]'}`}>
                          {c.label}
                        </span>
                      </label>
                    ))}
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-[#4A5568] text-[10px]">
                      {selected.confirmed_at
                        ? `확인 완료: ${fmtDate(selected.confirmed_at)}`
                        : '5개 항목을 모두 체크해야 confirm 할 수 있습니다.'}
                    </p>
                    <button
                      onClick={handleConfirm}
                      disabled={busyAction !== null || !allChecked}
                      className="flex items-center gap-1.5 bg-[#00E5CC] text-[#0A0E1A] text-xs font-bold px-4 py-2 rounded-lg hover:bg-[#00C9B1] transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                    >
                      <i className={busyAction === 'confirm' ? 'ri-loader-4-line animate-spin' : 'ri-check-double-line'}></i>
                      최종 확인 (confirm)
                    </button>
                  </div>
                </div>

                {/* send-to-claude */}
                <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5 space-y-3">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="text-white font-bold text-sm">
                      <i className="ri-send-plane-2-line mr-1 text-[#00E5CC]"></i>Claude 전달
                    </h3>
                    {isSent && (
                      <span className="text-[10px] px-2 py-0.5 rounded bg-[#00E5CC]/10 text-[#00E5CC]">
                        <i className="ri-checkbox-circle-line mr-0.5"></i>sent · {fmtDate(selected.sent_at)}
                      </span>
                    )}
                  </div>
                  <p className="text-[#8B9BB4] text-xs">
                    confirm 상태에서만 실행할 수 있습니다. 외부 호출 없이 최종 마크다운을 확정 저장(sent)합니다.
                  </p>
                  <div className="flex justify-end">
                    <button
                      onClick={handleSend}
                      disabled={busyAction !== null || currentStatus !== 'confirmed'}
                      className="flex items-center gap-1.5 bg-[#00E5CC] text-[#0A0E1A] text-xs font-bold px-4 py-2 rounded-lg hover:bg-[#00C9B1] transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                    >
                      <i className={busyAction === 'send' ? 'ri-loader-4-line animate-spin' : 'ri-send-plane-line'}></i>
                      Claude 로 전달 (send)
                    </button>
                  </div>
                  {(selected.sent_markdown || isSent) && (
                    <div>
                      <p className="text-[#4A5568] text-[10px] font-semibold mb-1">전달된 최종 마크다운</p>
                      <pre className="bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-[#C9D4E3] text-[11px] font-mono whitespace-pre-wrap max-h-64 overflow-y-auto">
                        {selected.sent_markdown || '(저장된 마크다운이 없습니다)'}
                      </pre>
                    </div>
                  )}
                </div>

                {/* 처리 (claim → resolve) — 수동 처리 경로 */}
                <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5 space-y-3">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="text-white font-bold text-sm">
                      <i className="ri-tools-line mr-1 text-[#00E5CC]"></i>처리 (resolve)
                    </h3>
                    <span className={`text-[10px] px-2 py-0.5 rounded ${statusTone(selected.status)}`}>
                      {statusLabel(selected.status)}
                    </span>
                  </div>
                  <p className="text-[#8B9BB4] text-xs">
                    전달된 요청의 실제 처리 결과를 기록합니다. &ldquo;작업 시작&rdquo;으로 작업 중(in_progress)
                    상태를 선언한 뒤, 완료(done) 또는 반영 안 함(wont_fix)으로 마감하세요.
                  </p>
                  <div className="flex justify-start">
                    <button
                      onClick={handleClaim}
                      disabled={busyAction !== null || currentStatus === 'in_progress'}
                      className="flex items-center gap-1.5 bg-[#F59E0B]/10 border border-[#F59E0B]/30 text-[#F59E0B] text-xs font-semibold px-4 py-2 rounded-lg hover:bg-[#F59E0B]/20 transition-colors disabled:opacity-60 cursor-pointer"
                    >
                      <i className={busyAction === 'claim' ? 'ri-loader-4-line animate-spin' : 'ri-play-circle-line'}></i>
                      작업 시작 (claim)
                    </button>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <label className="block text-[#4A5568] text-[10px] font-semibold mb-1">처리 결과 상태</label>
                      <select
                        value={resolveStatus}
                        onChange={e => setResolveStatus(e.target.value as SRResolveStatus)}
                        className={selectCls}
                      >
                        {RESOLVE_STATUS_OPTIONS.map(s => (
                          <option key={s} value={s}>{STATUS_LABELS[s]}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="block text-[#4A5568] text-[10px] font-semibold mb-1">커밋 SHA (선택)</label>
                      <input
                        value={resolveCommitRef}
                        onChange={e => setResolveCommitRef(e.target.value)}
                        placeholder="예: 0a22e5f8"
                        className={`${inputCls} font-mono`}
                      />
                    </div>
                  </div>
                  <div>
                    <label className="block text-[#4A5568] text-[10px] font-semibold mb-1">처리 내용</label>
                    <textarea
                      value={resolveNote}
                      onChange={e => setResolveNote(e.target.value)}
                      rows={3}
                      placeholder="무엇을 어떻게 반영했는지 (또는 반영하지 않은 사유)"
                      className={`${inputCls} resize-none`}
                    />
                  </div>
                  <div className="flex justify-end">
                    <button
                      onClick={handleResolve}
                      disabled={busyAction !== null || !resolveNote.trim()}
                      className="flex items-center gap-1.5 bg-[#00E5CC] text-[#0A0E1A] text-xs font-bold px-4 py-2 rounded-lg hover:bg-[#00C9B1] transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
                    >
                      <i className={busyAction === 'resolve' ? 'ri-loader-4-line animate-spin' : 'ri-check-double-line'}></i>
                      처리 결과 기록 (resolve)
                    </button>
                  </div>
                </div>

                {/* 액션 메시지 */}
                {(actionMsg || actionErr) && (
                  <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] px-5 py-3">
                    {actionErr && (
                      <p className="text-red-400 text-xs flex items-center gap-1">
                        <i className="ri-error-warning-line"></i>{actionErr}
                      </p>
                    )}
                    {actionMsg && (
                      <p className="text-emerald-400 text-xs flex items-center gap-1">
                        <i className="ri-check-line"></i>{actionMsg}
                      </p>
                    )}
                  </div>
                )}

                {/* 감사 타임라인 */}
                <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
                  <h3 className="text-white font-bold text-sm mb-3">
                    <i className="ri-time-line mr-1 text-[#00E5CC]"></i>감사 타임라인
                  </h3>
                  {events.length === 0 ? (
                    <p className="text-[#4A5568] text-xs">기록된 이벤트가 없습니다.</p>
                  ) : (
                    <ul className="space-y-2 border-l border-[#1E2530] ml-1.5 pl-4">
                      {events.map(ev => (
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
                          <p className="text-[#4A5568] text-[10px]">
                            {ev.actor_email || '—'} · {fmtDate(ev.created_at)}
                          </p>
                          {ev.note && <p className="text-[#8B9BB4] text-[11px] mt-0.5">{ev.note}</p>}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
