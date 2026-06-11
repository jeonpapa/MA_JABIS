import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  listAdminDrugs, createDrug, updateDrug, deleteDrug,
  addDrugEvent, deleteDrugEvent,
  listMeetings, updateSession, createSession,
  QUEUE_STATES, QUEUE_STATE_LABEL,
  NEGOTIATION_STATUSES, NEGOTIATION_LABEL,
  TRACKING_PRIORITIES, TRACKING_PRIORITY_LABEL,
  COMMITTEE_LABEL, STAGE_LABEL,
  type AdminDrug, type Meeting, type Committee, type QueueState,
  type NegotiationStatus, type TrackingPriority, type ListingType, type Stage,
} from '@/api/reimbPipelineAdmin';
import { fetchMe } from '@/utils/authUsers';

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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 신규 약물
  const [draft, setDraft] = useState<DrugDraft>(EMPTY_DRUG);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  // 행 확장: 편집 / 이벤트
  const [expanded, setExpanded] = useState<{ id: number; mode: 'edit' | 'events' } | null>(null);
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
      const [d, m] = await Promise.all([listAdminDrugs(), listMeetings()]);
      setDrugs(d);
      setMeetings(m);
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
        {/* 신규 약물 등록 */}
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

        {/* 약물 관리 */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-white font-bold text-base">추적 약물 ({drugs.length})</h2>
            <button onClick={reload} className="text-[#8B9BB4] text-xs hover:text-white cursor-pointer flex items-center gap-1">
              <i className="ri-refresh-line"></i>새로고침
            </button>
          </div>
          {loading && <p className="text-[#8B9BB4] text-sm">로드 중…</p>}
          {error && <p className="text-red-400 text-sm">{error}</p>}
          {!loading && !error && drugs.length === 0 && <p className="text-[#4A5568] text-sm">등록된 약물이 없습니다.</p>}
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
                    <th className="text-left py-2 pr-3">단계</th>
                    <th className="text-left py-2 pr-3 whitespace-nowrap">최근 상태</th>
                    <th className="text-right py-2">관리</th>
                  </tr>
                </thead>
                <tbody>
                  {drugs.map(d => {
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

        {/* 회의 상태 관리 */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
          <h2 className="text-white font-bold text-base mb-1">회의 상태 관리</h2>
          <p className="text-[#8B9BB4] text-xs mb-4">암질심·약평위 차수별 일정 — 상태/공식 차수/메모/회의록 URL 편집</p>

          {/* 신규 회의 추가 */}
          <div className="grid grid-cols-12 gap-3 mb-5 pb-5 border-b border-[#1E2530]">
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
  onAddEvent, onDeleteEvent,
}: {
  drug: AdminDrug;
  expandedMode: 'edit' | 'events' | null;
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
        </td>
        <td className="py-2.5 pr-3 text-[#8B9BB4]">{drug.ingredient_inn ?? <Dash />}</td>
        <td className="py-2.5 pr-3 text-[#8B9BB4]">{drug.manufacturer ?? <Dash />}</td>
        <td className="py-2.5 pr-3 text-[#8B9BB4] max-w-[200px]"><span className="block truncate">{drug.indication ?? '—'}</span></td>
        <td className="py-2.5 pr-3 text-[#8B9BB4] whitespace-nowrap">{drug.listing_type ?? <Dash />}</td>
        <td className="py-2.5 pr-3 whitespace-nowrap">
          <span className={`text-[10px] px-2 py-0.5 rounded ${STAGE_BADGE[drug.stage]}`}>{STAGE_LABEL[drug.stage]}</span>
        </td>
        <td className="py-2.5 pr-3 whitespace-nowrap">
          {lq ? (
            <div className="flex flex-col gap-0.5">
              <span className={`text-[10px] px-2 py-0.5 rounded w-fit ${STATE_BADGE[lq.state]}`}>
                {lq.committee ? COMMITTEE_LABEL[lq.committee] : '?'} · {QUEUE_STATE_LABEL[lq.state]}
              </span>
              <span className="text-[#4A5568] text-[10px]">{lq.session_date ?? lq.queue_entry_date ?? '—'}</span>
            </div>
          ) : <Dash />}
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
                        {ev.sessionDate ? `${ev.cycle != null ? `${ev.cycle}차 ` : ''}(${ev.sessionDate})` : <Dash />}
                      </td>
                      <td className="py-1.5 pr-3 text-[#8B9BB4]">{ev.attempt ?? <Dash />}</td>
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
