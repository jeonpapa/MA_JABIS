import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  listAdminDrugs, createDrug, updateDrug, deleteDrug,
  addDrugEvent, deleteDrugEvent,
  listMeetings, updateSession, createSession,
  listNhisUnmatched,
  QUEUE_STATES, QUEUE_STATE_LABEL,
  NEGOTIATION_STATUSES, NEGOTIATION_LABEL,
  TRACKING_PRIORITIES, TRACKING_PRIORITY_LABEL,
  COMMITTEE_LABEL, STAGE_LABEL,
  type AdminDrug, type AdminLastResult, type Meeting, type Committee, type QueueState,
  type NegotiationStatus, type TrackingPriority, type ListingType, type Stage,
  type NhisUnmatched,
} from '@/api/reimbPipelineAdmin';
import { fetchMe } from '@/utils/authUsers';
import RoundsPanel from './RoundsPanel';

// ── 약물 폼 draft ─────────────────────────────────────────────────────────────

type DrugDraft = {
  brand_kr: string;
  brand_en: string;
  ingredient_inn: string;
  manufacturer: string;
  indication: string;
  listing_type: '' | ListingType;
  submitted_date: string;
  msd_flag: boolean;
  tracking_priority: TrackingPriority; // DB NOT NULL DEFAULT 'generic_new_drug'
  notes: string;
  amjilsim_pass_date: string;
  yakpyungwi_pass_date: string;
  negotiation_status: '' | NegotiationStatus;
};

const EMPTY_DRUG: DrugDraft = {
  brand_kr: '', brand_en: '', ingredient_inn: '', manufacturer: '',
  indication: '', listing_type: '', submitted_date: '', msd_flag: false,
  tracking_priority: 'generic_new_drug', notes: '',
  amjilsim_pass_date: '', yakpyungwi_pass_date: '', negotiation_status: '',
};

const nul = (s: string) => (s.trim() ? s.trim() : null);

function draftToPayload(d: DrugDraft, withReviewFields: boolean) {
  const base = {
    brand_kr: d.brand_kr.trim(),
    brand_en: nul(d.brand_en),
    ingredient_inn: nul(d.ingredient_inn),
    manufacturer: nul(d.manufacturer),
    indication: nul(d.indication),
    listing_type: (d.listing_type || null) as ListingType | null,
    submitted_date: nul(d.submitted_date),
    msd_flag: d.msd_flag,
    tracking_priority: d.tracking_priority,
    notes: nul(d.notes),
  };
  if (!withReviewFields) return base;
  return {
    ...base,
    amjilsim_pass_date: nul(d.amjilsim_pass_date),
    yakpyungwi_pass_date: nul(d.yakpyungwi_pass_date),
    negotiation_status: (d.negotiation_status || null) as NegotiationStatus | null,
  };
}

// ── 이벤트 폼 draft ───────────────────────────────────────────────────────────

type EventDraft = {
  committee: Committee;
  state: QueueState;
  session_id: string;       // '' = 세션 미지정
  queue_entry_date: string;
  attempt: string;
};
const EMPTY_EVENT: EventDraft = {
  committee: 'cancer', state: 'QUEUE_PENDING', session_id: '',
  queue_entry_date: '', attempt: '1',
};

// ── 회의 폼 draft ─────────────────────────────────────────────────────────────

type SessionEditDraft = {
  status: string;
  ordinal_official: string;
  note: string;
  minutes_url: string;
};

type SessionCreateDraft = {
  committee: Committee;
  year: string;
  ordinal_assumed: string;
  session_date: string;
};
const EMPTY_SESSION: SessionCreateDraft = {
  committee: 'cancer', year: String(new Date().getFullYear()),
  ordinal_assumed: '', session_date: '',
};

const STAGE_BADGE: Record<Stage, string> = {
  cancer: 'bg-[#7C3AED]/10 text-[#7C3AED]',
  evaluation: 'bg-[#00E5CC]/10 text-[#00E5CC]',
  nhis: 'bg-[#F59E0B]/10 text-[#F59E0B]',
};

const STATE_BADGE: Record<QueueState, string> = {
  APPROVED: 'bg-[#00E5CC]/10 text-[#00E5CC]',
  REJECTED_REQUEUE: 'bg-red-500/10 text-red-400',
  WITHDRAWN: 'bg-[#4A5568]/20 text-[#8B9BB4]',
  QUEUE_PROCESSED: 'bg-[#3B82F6]/10 text-[#60A5FA]',
  QUEUE_PENDING: 'bg-[#F59E0B]/10 text-[#F59E0B]',
};

const Dash = () => <span className="text-[#4A5568]">—</span>;

export default function AdminReimbursementPipelinePage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);

  const [drugs, setDrugs] = useState<AdminDrug[]>([]);
  const [meetings, setMeetings] = useState<Meeting[]>([]);
  const [nhisUnmatched, setNhisUnmatched] = useState<NhisUnmatched[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 목록 검색·필터 (작업 대기열 칩과 공유)
  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<'' | 'cancer' | 'evaluation' | 'negotiating' | 'completed'>('');
  const [typeFilter, setTypeFilter] = useState<'' | ListingType | 'none'>('');
  const [mfrFilter, setMfrFilter] = useState('');
  const [queueFilter, setQueueFilter] = useState<'' | QueueKey>('');

  // 신규 약물
  const [draft, setDraft] = useState<DrugDraft>(EMPTY_DRUG);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  // 행 확장: 편집 / 이벤트
  const [expanded, setExpanded] = useState<{ id: number; mode: 'edit' | 'events' | 'rounds' } | null>(null);
  const [editDraft, setEditDraft] = useState<DrugDraft>(EMPTY_DRUG);
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  // 이벤트 추가
  const [eventDraft, setEventDraft] = useState<EventDraft>(EMPTY_EVENT);
  const [eventBusy, setEventBusy] = useState(false);
  const [eventError, setEventError] = useState<string | null>(null);

  // 회의 관리
  const [sessionEditId, setSessionEditId] = useState<number | null>(null);
  const [sessionDraft, setSessionDraft] = useState<SessionEditDraft>({ status: 'SCHEDULED', ordinal_official: '', note: '', minutes_url: '' });
  const [sessionBusy, setSessionBusy] = useState(false);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [newSession, setNewSession] = useState<SessionCreateDraft>(EMPTY_SESSION);
  const [newSessionBusy, setNewSessionBusy] = useState(false);
  const [newSessionError, setNewSessionError] = useState<string | null>(null);

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
      const [d, m, u] = await Promise.all([listAdminDrugs(), listMeetings(), listNhisUnmatched().catch(() => ({ items: [], count: 0 }))]);
      setDrugs(d);
      setMeetings(m);
      setNhisUnmatched(u.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : '파이프라인 조회 실패');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (authChecked) reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authChecked]);

  const manufacturers = useMemo(
    () => [...new Set(drugs.map(d => d.manufacturer).filter((m): m is string => !!m))].sort((a, b) => a.localeCompare(b, 'ko')),
    [drugs],
  );

  // 작업 대기열 — 오늘 손봐야 할 항목 수 (클릭 시 목록 필터)
  const queueCounts = useMemo(() => {
    const c: Record<QueueKey, number> = { no_indication: 0, no_inn: 0, stale: 0, unlinked: 0 };
    for (const d of drugs) for (const k of QUEUE_KEYS) if (QUEUE_TEST[k](d)) c[k] += 1;
    return c;
  }, [drugs]);

  const filteredDrugs = useMemo(() => {
    const q = query.trim().toLowerCase();
    return drugs.filter(d => {
      if (q) {
        const hay = [d.brand_kr, d.brand_en, d.ingredient_inn, d.manufacturer, d.indication, d.notes]
          .filter(Boolean).join(' ').toLowerCase();
        if (!hay.includes(q)) return false;
      }
      if (statusFilter) {
        const st = d.status;
        if (statusFilter === 'completed' && st !== 'completed') return false;
        if (statusFilter === 'negotiating' && !(d.stage === 'nhis' && st !== 'completed')) return false;
        if ((statusFilter === 'cancer' || statusFilter === 'evaluation') && (d.stage !== statusFilter || st === 'completed')) return false;
      }
      if (typeFilter === 'none' ? !!d.listing_type : typeFilter && d.listing_type !== typeFilter) return false;
      if (mfrFilter && d.manufacturer !== mfrFilter) return false;
      if (queueFilter && !QUEUE_TEST[queueFilter](d)) return false;
      return true;
    });
  }, [drugs, query, statusFilter, typeFilter, mfrFilter, queueFilter]);

  const filtersActive = !!(query || statusFilter || typeFilter || mfrFilter || queueFilter);
  const resetFilters = () => { setQuery(''); setStatusFilter(''); setTypeFilter(''); setMfrFilter(''); setQueueFilter(''); };

  // 이벤트 폼 — 선택 위원회의 세션만 드롭다운에 노출
  const committeeSessions = useMemo(
    () => meetings.filter(m => m.committee === eventDraft.committee),
    [meetings, eventDraft.committee],
  );

  // ── 약물 CRUD ──────────────────────────────────────────────────────────────

  const handleAdd = async () => {
    if (!draft.brand_kr.trim()) {
      setAddError('약물명(국문) 필수');
      return;
    }
    setAdding(true);
    setAddError(null);
    try {
      await createDrug(draftToPayload(draft, false));
      setDraft(EMPTY_DRUG);
      await reload();
    } catch (e) {
      setAddError(e instanceof Error ? e.message : '추가 실패');
    } finally {
      setAdding(false);
    }
  };

  const startEdit = (d: AdminDrug) => {
    setExpanded({ id: d.drug_id, mode: 'edit' });
    setEditError(null);
    setEditDraft({
      brand_kr: d.brand_kr ?? '',
      brand_en: d.brand_en ?? '',
      ingredient_inn: d.ingredient_inn ?? '',
      manufacturer: d.manufacturer ?? '',
      indication: d.indication ?? '',
      listing_type: d.listing_type ?? '',
      submitted_date: d.submitted_date ?? '',
      msd_flag: d.msd_flag,
      tracking_priority: d.tracking_priority ?? 'generic_new_drug',
      notes: d.notes ?? '',
      amjilsim_pass_date: d.amjilsim_pass_date ?? '',
      yakpyungwi_pass_date: d.yakpyungwi_pass_date ?? '',
      negotiation_status: d.negotiation_status ?? '',
    });
  };

  const saveEdit = async (drugId: number) => {
    if (!editDraft.brand_kr.trim()) {
      setEditError('약물명(국문)은 비울 수 없음');
      return;
    }
    setEditBusy(true);
    setEditError(null);
    try {
      await updateDrug(drugId, draftToPayload(editDraft, true));
      setExpanded(null);
      await reload();
    } catch (e) {
      setEditError(e instanceof Error ? e.message : '수정 실패');
    } finally {
      setEditBusy(false);
    }
  };

  const handleDelete = async (d: AdminDrug) => {
    if (!confirm(`"${d.brand_kr}" 삭제? 연결된 심의 이벤트도 함께 삭제됩니다.`)) return;
    try {
      await deleteDrug(d.drug_id);
      if (expanded?.id === d.drug_id) setExpanded(null);
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : '삭제 실패');
    }
  };

  const toggleEvents = (d: AdminDrug) => {
    if (expanded?.id === d.drug_id && expanded.mode === 'events') {
      setExpanded(null);
      return;
    }
    setExpanded({ id: d.drug_id, mode: 'events' });
    setEventDraft(EMPTY_EVENT);
    setEventError(null);
  };

  const toggleRounds = (d: AdminDrug) => {
    if (expanded?.id === d.drug_id && expanded.mode === 'rounds') {
      setExpanded(null);
      return;
    }
    setExpanded({ id: d.drug_id, mode: 'rounds' });
  };

  // ── 이벤트 CRUD ────────────────────────────────────────────────────────────

  const handleAddEvent = async (drugId: number) => {
    const attempt = Number(eventDraft.attempt);
    if (!Number.isInteger(attempt) || attempt < 1) {
      setEventError('시도 차수는 1 이상 정수');
      return;
    }
    setEventBusy(true);
    setEventError(null);
    try {
      await addDrugEvent(drugId, {
        committee: eventDraft.committee,
        state: eventDraft.state,
        session_id: eventDraft.session_id ? Number(eventDraft.session_id) : null,
        queue_entry_date: nul(eventDraft.queue_entry_date),
        attempt,
      });
      setEventDraft({ ...EMPTY_EVENT, committee: eventDraft.committee });
      await reload();
    } catch (e) {
      setEventError(e instanceof Error ? e.message : '이벤트 추가 실패');
    } finally {
      setEventBusy(false);
    }
  };

  const handleDeleteEvent = async (eventId: number) => {
    if (!confirm('이 심의 이벤트를 삭제할까요?')) return;
    try {
      await deleteDrugEvent(eventId);
      await reload();
    } catch (e) {
      alert(e instanceof Error ? e.message : '이벤트 삭제 실패');
    }
  };

  // ── 회의 CRUD ──────────────────────────────────────────────────────────────

  const startSessionEdit = (m: Meeting) => {
    setSessionEditId(m.id);
    setSessionError(null);
    setSessionDraft({
      status: m.status ?? 'SCHEDULED',
      ordinal_official: m.cycle != null ? String(m.cycle) : '',
      note: m.note ?? '',
      minutes_url: m.minutes_url ?? '',
    });
  };

  const saveSession = async (sessionId: number) => {
    const ord = sessionDraft.ordinal_official.trim();
    if (ord && !/^\d+$/.test(ord)) {
      setSessionError('공식 차수는 정수');
      return;
    }
    setSessionBusy(true);
    setSessionError(null);
    try {
      await updateSession(sessionId, {
        status: sessionDraft.status,
        ordinal_official: ord ? Number(ord) : null,
        note: nul(sessionDraft.note),
        official_minutes_url: nul(sessionDraft.minutes_url),
      });
      setSessionEditId(null);
      await reload();
    } catch (e) {
      setSessionError(e instanceof Error ? e.message : '회의 수정 실패');
    } finally {
      setSessionBusy(false);
    }
  };

  const handleAddSession = async () => {
    const year = Number(newSession.year);
    const ordinal = Number(newSession.ordinal_assumed);
    if (!Number.isInteger(year) || !Number.isInteger(ordinal) || !newSession.ordinal_assumed.trim()) {
      setNewSessionError('연도/차수는 정수 필수');
      return;
    }
    if (!newSession.session_date) {
      setNewSessionError('회의일 필수');
      return;
    }
    setNewSessionBusy(true);
    setNewSessionError(null);
    try {
      await createSession({
        committee: newSession.committee,
        year,
        ordinal_assumed: ordinal,
        session_date: newSession.session_date,
      });
      setNewSession(EMPTY_SESSION);
      await reload();
    } catch (e) {
      setNewSessionError(e instanceof Error ? e.message : '회의 추가 실패');
    } finally {
      setNewSessionBusy(false);
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
          <span className="w-5 h-5 flex items-center justify-center"><i className="ri-git-merge-line text-[#00E5CC]"></i></span>
          <h1 className="text-2xl font-bold text-white">심의 파이프라인 관리</h1>
        </div>
        <p className="text-[#8B9BB4] text-sm">암질심·약평위 추적 약물 / 심의 이벤트 / 회의 차수 관리 (Admin 전용). 급여 심사 현황 페이지에 자동 반영.</p>
      </div>

      <div className="px-8 py-6 space-y-5 max-w-7xl">
        {/* 작업 대기열 — 등록 폼보다 먼저: 오늘 입력·확인할 것 */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
          <h2 className="text-white font-bold text-base mb-1">작업 대기열</h2>
          <p className="text-[#8B9BB4] text-xs mb-4">비어 있거나 오래된 항목. 누르면 아래 목록이 해당 항목만 보이도록 걸러집니다.</p>
          <div className="grid grid-cols-5 gap-3">
            {QUEUE_KEYS.map(k => (
              <button
                key={k}
                onClick={() => setQueueFilter(queueFilter === k ? '' : k)}
                className={`text-left rounded-xl border p-3 cursor-pointer transition-colors ${
                  queueFilter === k ? 'border-[#00E5CC] bg-[#00E5CC]/5' : 'border-[#1E2530] hover:border-[#2A3441]'
                }`}
                title={QUEUE_HINT[k]}
              >
                <p className="text-[#8B9BB4] text-[11px]">{QUEUE_LABEL[k]}</p>
                <p className={`text-xl font-bold ${queueCounts[k] > 0 ? 'text-[#F59E0B]' : 'text-white'}`}>{loading ? '…' : queueCounts[k]}</p>
              </button>
            ))}
            <a href="#nhis-unmatched" className="text-left rounded-xl border border-[#1E2530] hover:border-[#2A3441] p-3" title="공단 공개자료 중 추적 약물과 자동 매칭되지 않은 건 — 아래 'NHIS 미매칭 검토'">
              <p className="text-[#8B9BB4] text-[11px]">NHIS 미매칭 검토</p>
              <p className={`text-xl font-bold ${nhisUnmatched.length > 0 ? 'text-[#F59E0B]' : 'text-white'}`}>{loading ? '…' : nhisUnmatched.length}</p>
            </a>
          </div>
        </div>

        {/* 약물 관리 */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-white font-bold text-base">
              추적 약물 ({filtersActive ? `${filteredDrugs.length} / ${drugs.length}` : drugs.length})
            </h2>
            <button onClick={reload} className="text-[#8B9BB4] text-xs hover:text-white cursor-pointer flex items-center gap-1">
              <i className="ri-refresh-line"></i>새로고침
            </button>
          </div>
          {/* 검색 + 필터 */}
          <div className="grid grid-cols-12 gap-2 mb-2">
            <div className="col-span-4">
              <input
                type="search"
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder="약물명 · 성분 · 제조사 · 적응증 · 메모 검색"
                aria-label="추적 약물 검색"
                className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-sm placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50"
              />
            </div>
            <FilterSelect label="상태" value={statusFilter} onChange={v => setStatusFilter(v as typeof statusFilter)}>
              <option value="">상태 전체</option>
              <option value="cancer">암질심 심의 단계</option>
              <option value="evaluation">약평위 심의 단계</option>
              <option value="negotiating">건보공단 협상 중</option>
              <option value="completed">협상 완료</option>
            </FilterSelect>
            <FilterSelect label="유형" value={typeFilter} onChange={v => setTypeFilter(v as typeof typeFilter)}>
              <option value="">유형 전체</option>
              <option value="신규">신규</option>
              <option value="확대">확대</option>
              <option value="none">유형 미지정</option>
            </FilterSelect>
            <FilterSelect label="제조사" value={mfrFilter} onChange={setMfrFilter} span={3}>
              <option value="">제조사 전체</option>
              {manufacturers.map(m => <option key={m} value={m}>{m}</option>)}
            </FilterSelect>
            <div className="col-span-1 flex items-center">
              {filtersActive && (
                <button onClick={resetFilters} className="text-[#8B9BB4] text-xs hover:text-white cursor-pointer whitespace-nowrap">초기화</button>
              )}
            </div>
          </div>
          <div className="flex items-center gap-1.5 flex-wrap mb-4">
            <label className="flex items-center gap-1.5 text-xs text-[#8B9BB4] cursor-pointer mr-2">
              <input
                type="checkbox"
                checked={queueFilter === 'no_indication'}
                onChange={e => setQueueFilter(e.target.checked ? 'no_indication' : '')}
                className="accent-[#00E5CC]"
              />
              적응증 비어 있음만
            </label>
            {queueFilter && queueFilter !== 'no_indication' && (
              <span className="text-[10px] px-2 py-0.5 rounded bg-[#00E5CC]/10 text-[#00E5CC]">
                작업 대기열: {QUEUE_LABEL[queueFilter]}
                <button onClick={() => setQueueFilter('')} className="ml-1 cursor-pointer" aria-label="대기열 필터 해제">×</button>
              </span>
            )}
            <span className="text-[#4A5568] text-[11px]">
              '다음 단계' = 지금 기다리는 절차 · '마지막 결과' = 가장 최근 사건일의 심의·협상 결과 (예: 다음 단계 약평위 · 마지막 결과 암질심 통과)
            </span>
          </div>
          {loading && <p className="text-[#8B9BB4] text-sm">로드 중…</p>}
          {error && <p className="text-red-400 text-sm">{error}</p>}
          {!loading && !error && drugs.length === 0 && <p className="text-[#4A5568] text-sm">등록된 약물이 없습니다.</p>}
          {!loading && !error && drugs.length > 0 && filteredDrugs.length === 0 && (
            <p className="text-[#4A5568] text-sm">조건에 맞는 약물이 없습니다. <button onClick={resetFilters} className="text-[#00E5CC] cursor-pointer">필터 초기화</button></p>
          )}
          {!loading && drugs.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[#8B9BB4] text-xs border-b border-[#1E2530]">
                    <th className="text-left py-2 pr-3">약물명</th>
                    <th className="text-left py-2 pr-3">성분</th>
                    <th className="text-left py-2 pr-3">제조사</th>
                    <th className="text-left py-2 pr-3">적응증</th>
                    <th className="text-left py-2 pr-3">유형</th>
                    <th className="text-left py-2 pr-3 whitespace-nowrap" title="약제가 지금 기다리는 절차">다음 단계</th>
                    <th className="text-left py-2 pr-3 whitespace-nowrap" title="사건일(회의일·큐 등재일·협상 완료연월)이 가장 늦은 심의·협상 결과">마지막 결과</th>
                    <th className="text-right py-2">관리</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredDrugs.map(d => {
                    const isExpanded = expanded?.id === d.drug_id;
                    return (
                      <DrugRowGroup
                        key={d.drug_id}
                        drug={d}
                        expandedMode={isExpanded ? expanded.mode : null}
                        editDraft={editDraft}
                        setEditDraft={setEditDraft}
                        editBusy={editBusy}
                        editError={editError}
                        onStartEdit={() => startEdit(d)}
                        onSaveEdit={() => saveEdit(d.drug_id)}
                        onCancelExpand={() => setExpanded(null)}
                        onDelete={() => handleDelete(d)}
                        onToggleEvents={() => toggleEvents(d)}
                        onToggleRounds={() => toggleRounds(d)}
                        allDrugs={drugs}
                        onRoundsChanged={reload}
                        eventDraft={eventDraft}
                        setEventDraft={setEventDraft}
                        eventBusy={eventBusy}
                        eventError={eventError}
                        committeeSessions={committeeSessions}
                        onAddEvent={() => handleAddEvent(d.drug_id)}
                        onDeleteEvent={handleDeleteEvent}
                      />
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* NHIS 미매칭 audit — 공단 공개자료 중 추적 약물과 매칭 안 된 행 (수동 등록 검토) */}
        <div id="nhis-unmatched" className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
          <div className="flex items-center justify-between mb-1">
            <h2 className="text-white font-bold text-base">NHIS 미매칭 검토 ({nhisUnmatched.length})</h2>
            {nhisUnmatched.length > 0 && (
              <span className="text-[10px] px-2 py-0.5 rounded bg-[#F59E0B]/10 text-[#F59E0B]">수동 등록 대상</span>
            )}
          </div>
          <p className="text-[#8B9BB4] text-xs mb-4">
            건강보험공단 공개자료 중 추적 약물과 자동 매칭되지 않은 건. 추적이 필요하면 아래 '신규 약물 등록'에서 직접 추가하세요.
          </p>
          {loading && <p className="text-[#8B9BB4] text-sm">로드 중…</p>}
          {!loading && nhisUnmatched.length === 0 && <p className="text-[#4A5568] text-sm">미매칭 건이 없습니다.</p>}
          {!loading && nhisUnmatched.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[#8B9BB4] text-xs border-b border-[#1E2530]">
                    <th className="text-left py-2 pr-3">구분</th>
                    <th className="text-left py-2 pr-3">제품명</th>
                    <th className="text-left py-2 pr-3">제약사명</th>
                    <th className="text-left py-2 pr-3">효능군</th>
                    <th className="text-left py-2 pr-3 whitespace-nowrap">등록연월</th>
                    <th className="text-left py-2 pr-3">협상결과</th>
                    <th className="text-left py-2 pr-3 whitespace-nowrap">완료연월</th>
                    <th className="text-right py-2">출처</th>
                  </tr>
                </thead>
                <tbody>
                  {nhisUnmatched.map(u => (
                    <tr key={u.id} className="border-b border-[#1E2530]/50 last:border-b-0 hover:bg-[#1E2530]/30">
                      <td className="py-2 pr-3 whitespace-nowrap">
                        <span className={`text-[10px] px-2 py-0.5 rounded ${u.listType === '신규' ? 'bg-[#0EA5E9]/10 text-[#38BDF8]' : 'bg-[#8B5CF6]/10 text-[#A78BFA]'}`}>
                          {u.listType === '신규' ? '신약' : '사용범위 확대'}
                        </span>
                      </td>
                      <td className="py-2 pr-3 text-white font-medium">{u.productName}</td>
                      <td className="py-2 pr-3 text-[#8B9BB4]">{u.manufacturer ?? <Dash />}</td>
                      <td className="py-2 pr-3 text-[#8B9BB4]">{u.efficacyGroup ?? <Dash />}</td>
                      <td className="py-2 pr-3 text-[#8B9BB4] whitespace-nowrap">{u.registeredYm ?? <Dash />}</td>
                      <td className="py-2 pr-3 text-[#8B9BB4]">{u.result ?? <Dash />}</td>
                      <td className={`py-2 pr-3 whitespace-nowrap ${u.completedYm ? 'text-emerald-400' : 'text-[#F59E0B]'}`}>{u.completedYm ?? '진행 중'}</td>
                      <td className="py-2 text-right whitespace-nowrap">
                        <a href={u.sourceUrl} target="_blank" rel="noreferrer" className="text-[#00E5CC] text-xs hover:underline">공단</a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        {/* 회의 상태 관리 */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
          <h2 className="text-white font-bold text-base mb-1">회의 상태 관리</h2>
          <p className="text-[#8B9BB4] text-xs mb-4">암질심·약평위 차수별 일정 — 상태/공식 차수/메모/회의록 URL 편집</p>

          {loading && <p className="text-[#8B9BB4] text-sm">로드 중…</p>}
          {!loading && meetings.length === 0 && <p className="text-[#4A5568] text-sm">등록된 회의가 없습니다.</p>}
          {!loading && meetings.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[#8B9BB4] text-xs border-b border-[#1E2530]">
                    <th className="text-left py-2 pr-3">위원회</th>
                    <th className="text-left py-2 pr-3">차수</th>
                    <th className="text-left py-2 pr-3">회의일</th>
                    <th className="text-left py-2 pr-3">상태</th>
                    <th className="text-left py-2 pr-3">메모</th>
                    <th className="text-left py-2 pr-3">회의록</th>
                    <th className="text-right py-2">관리</th>
                  </tr>
                </thead>
                <tbody>
                  {meetings.map(m => (
                    sessionEditId === m.id ? (
                      <tr key={m.id} className="border-b border-[#1E2530]/50 bg-[#00E5CC]/5 align-top">
                        <td className="py-2 pr-3 text-[#8B9BB4] text-xs whitespace-nowrap">{COMMITTEE_LABEL[m.committee]}</td>
                        <td className="py-2 pr-2 w-24">
                          <InlineInput type="number" value={sessionDraft.ordinal_official} onChange={v => setSessionDraft({ ...sessionDraft, ordinal_official: v })} placeholder="공식 차수" />
                        </td>
                        <td className="py-2 pr-3 text-[#8B9BB4] text-xs whitespace-nowrap">{m.date ?? '—'}</td>
                        <td className="py-2 pr-2">
                          <select
                            value={sessionDraft.status}
                            onChange={e => setSessionDraft({ ...sessionDraft, status: e.target.value })}
                            className="bg-[#0D1117] border border-[#1E2530] rounded px-2 py-1 text-white text-xs"
                          >
                            <option value="SCHEDULED">예정</option>
                            <option value="COMPLETED">완료</option>
                          </select>
                        </td>
                        <td className="py-2 pr-2"><InlineInput value={sessionDraft.note} onChange={v => setSessionDraft({ ...sessionDraft, note: v })} placeholder="메모" /></td>
                        <td className="py-2 pr-2"><InlineInput value={sessionDraft.minutes_url} onChange={v => setSessionDraft({ ...sessionDraft, minutes_url: v })} placeholder="https://…" /></td>
                        <td className="py-2 text-right whitespace-nowrap">
                          <button
                            onClick={() => saveSession(m.id)}
                            disabled={sessionBusy}
                            className="text-[#00E5CC] text-xs font-semibold mr-2 hover:text-[#00C9B1] cursor-pointer disabled:opacity-50"
                          >
                            저장
                          </button>
                          <button onClick={() => setSessionEditId(null)} className="text-[#8B9BB4] text-xs hover:text-white cursor-pointer">취소</button>
                          {sessionError && <p className="text-red-400 text-[10px] mt-1">{sessionError}</p>}
                        </td>
                      </tr>
                    ) : (
                      <tr key={m.id} className="border-b border-[#1E2530]/50 last:border-b-0 hover:bg-[#1E2530]/30">
                        <td className="py-2 pr-3 whitespace-nowrap">
                          <span className={`text-[10px] px-2 py-0.5 rounded ${m.committee === 'cancer' ? 'bg-[#7C3AED]/10 text-[#7C3AED]' : 'bg-[#00E5CC]/10 text-[#00E5CC]'}`}>
                            {COMMITTEE_LABEL[m.committee]}
                          </span>
                        </td>
                        <td className="py-2 pr-3 text-white whitespace-nowrap">{m.cycle != null ? `${m.cycle}차` : <Dash />}</td>
                        <td className="py-2 pr-3 text-[#8B9BB4] whitespace-nowrap">{m.date ?? <Dash />}</td>
                        <td className="py-2 pr-3 whitespace-nowrap">
                          <span className={`text-[10px] px-2 py-0.5 rounded ${m.status === 'COMPLETED' ? 'bg-[#4A5568]/20 text-[#8B9BB4]' : 'bg-[#F59E0B]/10 text-[#F59E0B]'}`}>
                            {m.status === 'COMPLETED' ? '완료' : '예정'}
                          </span>
                        </td>
                        <td className="py-2 pr-3 text-[#8B9BB4] text-xs max-w-[280px]"><span className="block truncate">{m.note ?? '—'}</span></td>
                        <td className="py-2 pr-3 text-xs max-w-[160px]">
                          {m.minutes_url
                            ? <a href={m.minutes_url} target="_blank" rel="noreferrer" className="text-[#00E5CC] hover:underline block truncate">{m.minutes_url}</a>
                            : <Dash />}
                        </td>
                        <td className="py-2 text-right whitespace-nowrap">
                          <button onClick={() => startSessionEdit(m)} className="text-[#8B9BB4] text-xs hover:text-[#00E5CC] cursor-pointer">편집</button>
                        </td>
                      </tr>
                    )
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {/* 신규 회의 추가 — 목록 아래 (작업 대기열·목록을 먼저) */}
          <div className="grid grid-cols-12 gap-3 mt-5 pt-5 border-t border-[#1E2530]">
            <SelectCell label="위원회" span={2} value={newSession.committee} onChange={v => setNewSession({ ...newSession, committee: v as Committee })}>
              <option value="cancer">암질심</option>
              <option value="evaluation">약평위</option>
            </SelectCell>
            <InputCell label="연도" span={2} type="number" value={newSession.year} onChange={v => setNewSession({ ...newSession, year: v })} placeholder="2026" />
            <InputCell label="가정 차수" span={2} type="number" value={newSession.ordinal_assumed} onChange={v => setNewSession({ ...newSession, ordinal_assumed: v })} placeholder="7" />
            <InputCell label="회의일" span={3} type="date" value={newSession.session_date} onChange={v => setNewSession({ ...newSession, session_date: v })} />
            <div className="col-span-3 flex items-end">
              <button
                onClick={handleAddSession}
                disabled={newSessionBusy}
                className="w-full bg-[#1E2530] border border-[#2A3441] text-white px-4 py-2 rounded-lg text-sm font-semibold cursor-pointer hover:border-[#00E5CC]/50 transition-colors disabled:opacity-50"
              >
                {newSessionBusy ? '추가 중…' : '신규 회의 추가'}
              </button>
            </div>
            {newSessionError && <p className="col-span-12 text-red-400 text-xs">{newSessionError}</p>}
          </div>

        </div>

        {/* 신규 약물 등록 — 작업 대기열·목록 아래 */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
          <h2 className="text-white font-bold text-base mb-4">신규 약물 등록</h2>
          <div className="grid grid-cols-12 gap-3">
            <InputCell label="약물명(국문) *" span={2} value={draft.brand_kr} onChange={v => setDraft({ ...draft, brand_kr: v })} placeholder="키트루다" />
            <InputCell label="약물명(영문)" span={2} value={draft.brand_en} onChange={v => setDraft({ ...draft, brand_en: v })} placeholder="Keytruda" />
            <InputCell label="성분(INN)" span={2} value={draft.ingredient_inn} onChange={v => setDraft({ ...draft, ingredient_inn: v })} placeholder="pembrolizumab" />
            <InputCell label="제조사" span={2} value={draft.manufacturer} onChange={v => setDraft({ ...draft, manufacturer: v })} placeholder="한국MSD" />
            <InputCell label="적응증" span={4} value={draft.indication} onChange={v => setDraft({ ...draft, indication: v })} placeholder="비소세포폐암 1차" />
          </div>
          <div className="mt-3 grid grid-cols-12 gap-3">
            <SelectCell label="등재 유형" span={2} value={draft.listing_type} onChange={v => setDraft({ ...draft, listing_type: v as DrugDraft['listing_type'] })}>
              <option value="">— 미지정</option>
              <option value="신규">신규</option>
              <option value="확대">확대</option>
            </SelectCell>
            <InputCell label="신청일" span={2} type="date" value={draft.submitted_date} onChange={v => setDraft({ ...draft, submitted_date: v })} />
            <SelectCell label="추적 우선순위" span={2} value={draft.tracking_priority} onChange={v => setDraft({ ...draft, tracking_priority: v as TrackingPriority })}>
              {TRACKING_PRIORITIES.map(p => <option key={p} value={p}>{TRACKING_PRIORITY_LABEL[p]}</option>)}
            </SelectCell>
            <InputCell label="메모" span={3} value={draft.notes} onChange={v => setDraft({ ...draft, notes: v })} placeholder="선택 항목" />
            <div className="col-span-1 flex items-end pb-2">
              <label className="flex items-center gap-1.5 text-xs text-[#8B9BB4] cursor-pointer">
                <input
                  type="checkbox"
                  checked={draft.msd_flag}
                  onChange={e => setDraft({ ...draft, msd_flag: e.target.checked })}
                  className="accent-[#00E5CC]"
                />
                MSD
              </label>
            </div>
            <div className="col-span-2 flex items-end">
              <button
                onClick={handleAdd}
                disabled={adding || !draft.brand_kr.trim()}
                className="w-full bg-[#00E5CC] text-[#0A0E1A] px-4 py-2 rounded-lg text-sm font-semibold cursor-pointer hover:bg-[#00C9B1] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {adding ? '추가 중…' : '약물 추가'}
              </button>
            </div>
          </div>
          {addError && <p className="text-red-400 text-xs mt-2">{addError}</p>}
        </div>

      </div>
    </div>
  );
}

// ── 약물 행 + 확장 패널 ───────────────────────────────────────────────────────

function DrugRowGroup({
  drug, expandedMode,
  editDraft, setEditDraft, editBusy, editError, onStartEdit, onSaveEdit, onCancelExpand, onDelete,
  onToggleEvents, eventDraft, setEventDraft, eventBusy, eventError, committeeSessions,
  onAddEvent, onDeleteEvent, onToggleRounds, allDrugs, onRoundsChanged,
}: {
  drug: AdminDrug;
  expandedMode: 'edit' | 'events' | 'rounds' | null;
  onToggleRounds: () => void;
  allDrugs: AdminDrug[];
  onRoundsChanged: () => Promise<void> | void;
  editDraft: DrugDraft;
  setEditDraft: (d: DrugDraft) => void;
  editBusy: boolean;
  editError: string | null;
  onStartEdit: () => void;
  onSaveEdit: () => void;
  onCancelExpand: () => void;
  onDelete: () => void;
  onToggleEvents: () => void;
  eventDraft: EventDraft;
  setEventDraft: (d: EventDraft) => void;
  eventBusy: boolean;
  eventError: string | null;
  committeeSessions: Meeting[];
  onAddEvent: () => void;
  onDeleteEvent: (eventId: number) => void;
}) {
  const lq = drug.latest_queue;
  return (
    <>
      <tr className={`border-b border-[#1E2530]/50 ${expandedMode ? 'bg-[#1E2530]/30' : 'hover:bg-[#1E2530]/30'}`}>
        <td className="py-2.5 pr-3">
          <div className="flex items-center gap-1.5">
            <span className="text-white font-medium">{drug.brand_kr}</span>
            {drug.msd_flag && (
              <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-[#00E5CC]/10 text-[#00E5CC] font-medium flex-shrink-0">MSD</span>
            )}
          </div>
          {drug.brand_en && <span className="text-[#4A5568] text-xs">{drug.brand_en}</span>}
          {/* 등재 차수 — 브랜드 아래 차수 라벨 (분할 전이면 약제 행 라벨 1개) */}
          {(drug.rounds ?? []).filter(r => r.round_label).map((r, i) => (
            <span
              key={r.round_id ?? `v${i}`}
              className={`block text-[10px] leading-snug ${r.is_primary ? 'text-[#00E5CC]' : 'text-[#4A5568]'}`}
              title={r.round_id == null ? '분할 전 — 약제 행 = 차수' : `${r.list_type ?? ''} 차수 · ${r.next_step}`}
            >
              {r.round_label}{r.status === 'completed' ? ' ✓' : ''}{r.needs_review ? ' *' : ''}
            </span>
          ))}
        </td>
        <td className="py-2.5 pr-3 text-[#8B9BB4]">{drug.ingredient_inn ?? <Dash />}</td>
        <td className="py-2.5 pr-3 text-[#8B9BB4]">{drug.manufacturer ?? <Dash />}</td>
        <td className="py-2.5 pr-3 text-[#8B9BB4] max-w-[200px]">
          {drug.indication
            ? <span className="block truncate" title={drug.indication}>{drug.indication}</span>
            : <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#F59E0B]/10 text-[#F59E0B]">미입력</span>}
        </td>
        <td className="py-2.5 pr-3 text-[#8B9BB4] whitespace-nowrap">{drug.listing_type ?? <Dash />}</td>
        <td className="py-2.5 pr-3 whitespace-nowrap">
          {drug.status === 'completed' ? (
            <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-600" title="공단 협상 타결 — 급여 등재 단계 종료">협상 완료</span>
          ) : (
            <span className={`text-[10px] px-2 py-0.5 rounded ${STAGE_BADGE[drug.stage]}`} title={drug.next_step}>
              {drug.next_step ?? STAGE_LABEL[drug.stage]}
            </span>
          )}
          {drug.is_stale && (
            <span className="block text-[10px] text-[#F59E0B] mt-0.5" title="60일 넘게 심의·협상 기록 변경 없음 (공단 자료로도 최근 확인 안 됨)">
              {drug.stale_days}일 미갱신
            </span>
          )}
        </td>
        <td className="py-2.5 pr-3 whitespace-nowrap">
          <LastResultCell result={drug.last_result ?? null} fallback={lq} unlinked={drug.unlinked_events ?? 0} />
        </td>
        <td className="py-2.5 text-right whitespace-nowrap">
          <button onClick={onStartEdit} className="text-[#8B9BB4] text-xs hover:text-[#00E5CC] mr-3 cursor-pointer">편집</button>
          <button onClick={onDelete} className="text-[#8B9BB4] text-xs hover:text-red-400 mr-3 cursor-pointer">삭제</button>
          <button
            onClick={onToggleEvents}
            className={`text-xs cursor-pointer ${expandedMode === 'events' ? 'text-[#00E5CC] font-semibold' : 'text-[#8B9BB4] hover:text-[#00E5CC]'}`}
          >
            이벤트 ({drug.events.length})
          </button>
          <button
            onClick={onToggleRounds}
            className={`ml-3 text-xs cursor-pointer ${expandedMode === 'rounds' ? 'text-[#00E5CC] font-semibold' : 'text-[#8B9BB4] hover:text-[#00E5CC]'}`}
            title="등재 차수 — 추가 · 이벤트/공단 협상 연결"
          >
            차수 ({drug.round_count ?? 0})
          </button>
        </td>
      </tr>

      {expandedMode === 'edit' && (
        <tr className="border-b border-[#1E2530]/50 bg-[#00E5CC]/5">
          <td colSpan={8} className="py-4 px-3">
            <div className="grid grid-cols-12 gap-3">
              <InputCell label="약물명(국문) *" span={2} value={editDraft.brand_kr} onChange={v => setEditDraft({ ...editDraft, brand_kr: v })} />
              <InputCell label="약물명(영문)" span={2} value={editDraft.brand_en} onChange={v => setEditDraft({ ...editDraft, brand_en: v })} />
              <InputCell label="성분(INN)" span={2} value={editDraft.ingredient_inn} onChange={v => setEditDraft({ ...editDraft, ingredient_inn: v })} />
              <InputCell label="제조사" span={2} value={editDraft.manufacturer} onChange={v => setEditDraft({ ...editDraft, manufacturer: v })} />
              <InputCell label="적응증" span={4} value={editDraft.indication} onChange={v => setEditDraft({ ...editDraft, indication: v })} />
            </div>
            <div className="mt-3 grid grid-cols-12 gap-3">
              <SelectCell label="등재 유형" span={2} value={editDraft.listing_type} onChange={v => setEditDraft({ ...editDraft, listing_type: v as DrugDraft['listing_type'] })}>
                <option value="">— 미지정</option>
                <option value="신규">신규</option>
                <option value="확대">확대</option>
              </SelectCell>
              <InputCell label="신청일" span={2} type="date" value={editDraft.submitted_date} onChange={v => setEditDraft({ ...editDraft, submitted_date: v })} />
              <SelectCell label="추적 우선순위" span={2} value={editDraft.tracking_priority} onChange={v => setEditDraft({ ...editDraft, tracking_priority: v as TrackingPriority })}>
                {TRACKING_PRIORITIES.map(p => <option key={p} value={p}>{TRACKING_PRIORITY_LABEL[p]}</option>)}
              </SelectCell>
              <InputCell label="암질심 통과일" span={2} type="date" value={editDraft.amjilsim_pass_date} onChange={v => setEditDraft({ ...editDraft, amjilsim_pass_date: v })} />
              <InputCell label="약평위 통과일" span={2} type="date" value={editDraft.yakpyungwi_pass_date} onChange={v => setEditDraft({ ...editDraft, yakpyungwi_pass_date: v })} />
              <SelectCell label="협상 상태" span={2} value={editDraft.negotiation_status} onChange={v => setEditDraft({ ...editDraft, negotiation_status: v as DrugDraft['negotiation_status'] })}>
                <option value="">— 미지정</option>
                {NEGOTIATION_STATUSES.map(s => <option key={s} value={s}>{NEGOTIATION_LABEL[s]} ({s})</option>)}
              </SelectCell>
            </div>
            <div className="mt-3 grid grid-cols-12 gap-3 items-end">
              <InputCell label="메모" span={7} value={editDraft.notes} onChange={v => setEditDraft({ ...editDraft, notes: v })} />
              <div className="col-span-2 flex items-center pb-2">
                <label className="flex items-center gap-1.5 text-xs text-[#8B9BB4] cursor-pointer">
                  <input
                    type="checkbox"
                    checked={editDraft.msd_flag}
                    onChange={e => setEditDraft({ ...editDraft, msd_flag: e.target.checked })}
                    className="accent-[#00E5CC]"
                  />
                  MSD 자산
                </label>
              </div>
              <div className="col-span-3 flex gap-2">
                <button
                  onClick={onSaveEdit}
                  disabled={editBusy || !editDraft.brand_kr.trim()}
                  className="flex-1 bg-[#00E5CC] text-[#0A0E1A] px-4 py-2 rounded-lg text-sm font-semibold cursor-pointer hover:bg-[#00C9B1] transition-colors disabled:opacity-50"
                >
                  {editBusy ? '저장 중…' : '저장'}
                </button>
                <button onClick={onCancelExpand} className="px-4 py-2 rounded-lg text-sm text-[#8B9BB4] border border-[#1E2530] hover:text-white cursor-pointer">취소</button>
              </div>
            </div>
            {editError && <p className="text-red-400 text-xs mt-2">{editError}</p>}
          </td>
        </tr>
      )}

      {expandedMode === 'rounds' && (
        <tr className="border-b border-[#1E2530]/50 bg-[#0D1117]/60">
          <td colSpan={8} className="py-4 px-3">
            <RoundsPanel drug={drug} drugs={allDrugs} onChanged={onRoundsChanged} />
          </td>
        </tr>
      )}

      {expandedMode === 'events' && (
        <tr className="border-b border-[#1E2530]/50 bg-[#0D1117]/60">
          <td colSpan={8} className="py-4 px-3">
            <h3 className="text-white text-sm font-semibold mb-3">
              <i className="ri-time-line text-[#00E5CC] mr-1"></i>심의 이벤트 — {drug.brand_kr}
            </h3>
            {drug.events.length === 0 ? (
              <p className="text-[#4A5568] text-xs mb-4">등록된 심의 이벤트가 없습니다.</p>
            ) : (
              <table className="w-full text-xs mb-4">
                <thead>
                  <tr className="text-[#8B9BB4] text-[11px] border-b border-[#1E2530]">
                    <th className="text-left py-1.5 pr-3">날짜</th>
                    <th className="text-left py-1.5 pr-3">위원회</th>
                    <th className="text-left py-1.5 pr-3">상태</th>
                    <th className="text-left py-1.5 pr-3">차수(회의일)</th>
                    <th className="text-left py-1.5 pr-3">시도</th>
                    <th className="text-left py-1.5 pr-3">등재 차수</th>
                    <th className="text-right py-1.5">관리</th>
                  </tr>
                </thead>
                <tbody>
                  {drug.events.map(ev => (
                    <tr key={ev.id} className="border-b border-[#1E2530]/40 last:border-b-0">
                      <td className="py-1.5 pr-3 text-[#8B9BB4] whitespace-nowrap">{ev.date ?? <Dash />}</td>
                      <td className="py-1.5 pr-3 text-white whitespace-nowrap">{ev.committee}</td>
                      <td className="py-1.5 pr-3 whitespace-nowrap">
                        <span className={`text-[10px] px-2 py-0.5 rounded ${STATE_BADGE[ev.state] ?? 'bg-[#4A5568]/20 text-[#8B9BB4]'}`}>{ev.stateLabel}</span>
                      </td>
                      <td className="py-1.5 pr-3 text-[#8B9BB4] whitespace-nowrap">
                        {ev.sessionDate
                          ? `${ev.cycleLabel ?? (ev.cycle != null ? `${ev.cycle}차` : '')} (${ev.sessionDate})`
                          : <span className="text-[#F59E0B]" title="회의 차수에 연결되지 않은 이벤트 — 사건일은 큐 등재일 기준">차수 미연결</span>}
                        {(ev.evidenceText || ev.evidenceUrl) && (
                          <span className="block text-[10px] text-[#4A5568] truncate max-w-[260px]" title={ev.evidenceText ?? ev.evidenceUrl ?? undefined}>
                            {ev.evidenceUrl ? <a href={ev.evidenceUrl} target="_blank" rel="noreferrer" className="text-[#00E5CC] hover:underline">근거 링크</a> : ev.evidenceText}
                          </span>
                        )}
                      </td>
                      <td className="py-1.5 pr-3 text-[#8B9BB4]">{ev.attempt ?? <Dash />}</td>
                      <td className="py-1.5 pr-3 text-[#8B9BB4] text-[10px] max-w-[180px] truncate">
                        {(drug.rounds ?? []).find(r => r.round_id != null && r.round_id === ev.roundId)?.round_label ?? <Dash />}
                      </td>
                      <td className="py-1.5 text-right">
                        <button onClick={() => onDeleteEvent(ev.id)} className="text-[#8B9BB4] text-[11px] hover:text-red-400 cursor-pointer">삭제</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {/* 이벤트 추가 폼 */}
            <div className="grid grid-cols-12 gap-3 items-end bg-[#161B27] border border-[#1E2530] rounded-xl p-3">
              <SelectCell label="위원회" span={2} value={eventDraft.committee} onChange={v => setEventDraft({ ...eventDraft, committee: v as Committee, session_id: '' })}>
                <option value="cancer">암질심</option>
                <option value="evaluation">약평위</option>
              </SelectCell>
              <SelectCell label="상태" span={2} value={eventDraft.state} onChange={v => setEventDraft({ ...eventDraft, state: v as QueueState })}>
                {QUEUE_STATES.map(s => <option key={s} value={s}>{QUEUE_STATE_LABEL[s]} ({s})</option>)}
              </SelectCell>
              <SelectCell label="연결 차수(회의)" span={3} value={eventDraft.session_id} onChange={v => setEventDraft({ ...eventDraft, session_id: v })}>
                <option value="">— 미연결</option>
                {committeeSessions.map(s => (
                  <option key={s.id} value={String(s.id)}>
                    {s.cycle != null ? `${s.cycle}차` : '차수 미정'} ({s.date ?? '날짜 미정'})
                  </option>
                ))}
              </SelectCell>
              <InputCell label="큐 등재일" span={2} type="date" value={eventDraft.queue_entry_date} onChange={v => setEventDraft({ ...eventDraft, queue_entry_date: v })} />
              <InputCell label="시도 차수" span={1} type="number" value={eventDraft.attempt} onChange={v => setEventDraft({ ...eventDraft, attempt: v })} />
              <div className="col-span-2">
                <button
                  onClick={onAddEvent}
                  disabled={eventBusy}
                  className="w-full bg-[#00E5CC] text-[#0A0E1A] px-3 py-2 rounded-lg text-xs font-semibold cursor-pointer hover:bg-[#00C9B1] transition-colors disabled:opacity-50"
                >
                  {eventBusy ? '추가 중…' : '이벤트 추가'}
                </button>
              </div>
              {eventError && <p className="col-span-12 text-red-400 text-xs">{eventError}</p>}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ── 작업 대기열 정의 ─────────────────────────────────────────────────────────

type QueueKey = 'no_indication' | 'no_inn' | 'stale' | 'unlinked';
const QUEUE_KEYS: QueueKey[] = ['no_indication', 'no_inn', 'stale', 'unlinked'];
const QUEUE_LABEL: Record<QueueKey, string> = {
  no_indication: '적응증 비어 있음',
  no_inn: '성분(INN) 비어 있음',
  stale: '60일+ 미갱신',
  unlinked: '차수 미연결 이벤트',
};
const QUEUE_HINT: Record<QueueKey, string> = {
  no_indication: '적응증이 비어 있는 추적 약물 — 급여 현황 카드·모달에 "정보 없음" 으로 보입니다',
  no_inn: '성분(INN)이 비어 있는 추적 약물 — 카드에 "성분 정보 없음" 으로 보입니다',
  stale: '심의·협상 기록이 60일 넘게 바뀌지 않았고 공단 자료로도 최근 확인되지 않은 약물 (협상 완료 제외)',
  unlinked: '회의 차수에 연결되지 않은 심의 이벤트가 있는 약물 — 이벤트를 열어 연결 차수를 지정하세요',
};
const QUEUE_TEST: Record<QueueKey, (d: AdminDrug) => boolean> = {
  no_indication: d => !d.indication?.trim(),
  no_inn: d => !d.ingredient_inn?.trim(),
  stale: d => !!d.is_stale && d.status !== 'completed',
  unlinked: d => (d.unlinked_events ?? 0) > 0,
};

function FilterSelect({
  label, value, onChange, children, span = 2,
}: { label: string; value: string; onChange: (v: string) => void; children: React.ReactNode; span?: number }) {
  return (
    <div className={SPAN_CLASS[span] ?? 'col-span-2'}>
      <select
        aria-label={label}
        value={value}
        onChange={e => onChange(e.target.value)}
        className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-[#00E5CC]/50"
      >
        {children}
      </select>
    </div>
  );
}

const LAST_RESULT_BADGE: Record<string, string> = {
  APPROVED: STATE_BADGE.APPROVED,
  REJECTED_REQUEUE: STATE_BADGE.REJECTED_REQUEUE,
  WITHDRAWN: STATE_BADGE.WITHDRAWN,
  QUEUE_PROCESSED: STATE_BADGE.QUEUE_PROCESSED,
  QUEUE_PENDING: STATE_BADGE.QUEUE_PENDING,
  AGREED: 'bg-emerald-500/10 text-emerald-600',
  REJECTED: 'bg-red-500/10 text-red-400',
  IN_PROGRESS: 'bg-[#3B82F6]/10 text-[#60A5FA]',
  STALLED: 'bg-[#F59E0B]/10 text-[#F59E0B]',
};

/** '마지막 결과' 셀 — 위원회·상태 + 사건일 (+차수/출처). 백엔드 last_result 우선, 없으면 latest_queue. */
function LastResultCell({ result, fallback, unlinked }: { result: AdminLastResult | null; fallback: AdminDrug['latest_queue']; unlinked: number }) {
  if (!result && !fallback) return <Dash />;
  if (!result && fallback) {
    return (
      <div className="flex flex-col gap-0.5">
        <span className={`text-[10px] px-2 py-0.5 rounded w-fit ${STATE_BADGE[fallback.state]}`}>
          {fallback.committee ? COMMITTEE_LABEL[fallback.committee] : '?'} · {QUEUE_STATE_LABEL[fallback.state]}
        </span>
        <span className="text-[#4A5568] text-[10px]">{fallback.event_date ?? fallback.session_date ?? fallback.queue_entry_date ?? '—'}</span>
      </div>
    );
  }
  const r = result as AdminLastResult;
  const when = r.kind === 'negotiation' ? (r.month ?? '연월 미상') : (r.date ?? '—');
  const meta = r.kind === 'negotiation'
    ? (r.source === 'nhis_official' ? '공단 공식' : '수기 입력')
    : r.source === 'pass_date' ? '통과일 기준' : r.cycleLabel ?? (r.sessionLinked ? '' : '차수 미연결');
  return (
    <div className="flex flex-col gap-0.5">
      <span className={`text-[10px] px-2 py-0.5 rounded w-fit ${LAST_RESULT_BADGE[r.state] ?? 'bg-[#4A5568]/20 text-[#8B9BB4]'}`}>
        {r.committeeLabel} · {r.label}
      </span>
      <span className="text-[#4A5568] text-[10px]">
        {when}{meta ? ` · ${meta}` : ''}
        {unlinked > 0 && r.kind === 'committee' && r.sessionLinked && <span className="text-[#F59E0B]"> · 미연결 {unlinked}</span>}
      </span>
    </div>
  );
}

// ── 공통 입력 셀 ──────────────────────────────────────────────────────────────

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

function SelectCell({
  label, span, value, onChange, children,
}: {
  label: string; span: number; value: string;
  onChange: (v: string) => void; children: React.ReactNode;
}) {
  return (
    <div className={SPAN_CLASS[span] ?? 'col-span-3'}>
      <label className="block text-[#8B9BB4] text-[11px] mb-1">{label}</label>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className="w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-3 py-2 text-white text-sm focus:outline-none focus:border-[#00E5CC]/50"
      >
        {children}
      </select>
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
