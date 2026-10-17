import { useMemo, useState } from 'react';
import {
  createRound, updateRound, deleteRound,
  linkEventToRound, unlinkEventFromRound, setNhisRound,
  NEGOTIATION_LABEL,
  type AdminDrug, type AdminRound, type ListingType, type RoundPayload,
} from '@/api/reimbPipelineAdmin';

// ── 등재 차수(listing round) 관리 패널 ──────────────────────────────────────────
// 브랜드 1 : N 차수. 차수 추가 · 수정 · 삭제(링크만 해제) + 위원회 이벤트 / 공단 협상 행을 차수에 연결.
// 병용 차수(예: 키트루다 2026 확대 ↔ 파드셉 2026 신규)는 브랜드별 차수 + combo_key 공유로 교차 참조하고,
// 다른 브랜드 이벤트는 '공유 링크'로 붙인다 (원래 브랜드 차수 링크는 그대로 — 빼앗지 않음).

interface RoundDraft {
  round_label: string;
  list_type: '' | ListingType;
  indication_scope: string;
  amjilsim_pass_date: string;
  yakpyungwi_pass_date: string;
  reimb_start_date: string;
  combo_key: string;
  notes: string;
  needs_review: boolean;
}

const EMPTY_ROUND: RoundDraft = {
  round_label: '', list_type: '', indication_scope: '', amjilsim_pass_date: '', yakpyungwi_pass_date: '',
  reimb_start_date: '', combo_key: '', notes: '', needs_review: false,
};

const nul = (v: string) => (v.trim() ? v.trim() : null);

function draftToPayload(d: RoundDraft): RoundPayload {
  return {
    round_label: d.round_label.trim(),
    list_type: d.list_type || null,
    indication_scope: nul(d.indication_scope),
    amjilsim_pass_date: nul(d.amjilsim_pass_date),
    yakpyungwi_pass_date: nul(d.yakpyungwi_pass_date),
    reimb_start_date: nul(d.reimb_start_date),
    combo_key: nul(d.combo_key),
    notes: nul(d.notes),
    needs_review: d.needs_review,
  };
}

function roundToDraft(r: AdminRound): RoundDraft {
  return {
    round_label: r.round_label ?? '',
    list_type: r.list_type ?? '',
    indication_scope: r.indication_scope ?? '',
    amjilsim_pass_date: r.amjilsim_pass_date ?? '',
    yakpyungwi_pass_date: r.yakpyungwi_pass_date ?? '',
    reimb_start_date: r.reimb_start_date ?? '',
    combo_key: r.combo_key ?? '',
    notes: r.notes ?? '',
    needs_review: !!r.needs_review,
  };
}

const INPUT = 'w-full bg-[#0D1117] border border-[#1E2530] rounded-lg px-2.5 py-1.5 text-white text-xs placeholder-[#4A5568] focus:outline-none focus:border-[#00E5CC]/50';
const LABEL = 'block text-[#8B9BB4] text-[10px] mb-1';

function Field({ label, children, className = '' }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={className}>
      <span className={LABEL}>{label}</span>
      {children}
    </div>
  );
}

function RoundForm({ draft, setDraft }: { draft: RoundDraft; setDraft: (d: RoundDraft) => void }) {
  return (
    <div className="grid grid-cols-12 gap-2">
      <Field label="차수 라벨 * (예: 2026 확대 · 파드셉 병용 요로상피암 1차)" className="col-span-4">
        <input className={INPUT} value={draft.round_label} onChange={e => setDraft({ ...draft, round_label: e.target.value })} />
      </Field>
      <Field label="유형" className="col-span-1">
        <select className={INPUT} value={draft.list_type} onChange={e => setDraft({ ...draft, list_type: e.target.value as RoundDraft['list_type'] })}>
          <option value="">—</option>
          <option value="신규">신규</option>
          <option value="확대">확대</option>
        </select>
      </Field>
      <Field label="적응증 범위" className="col-span-4">
        <input className={INPUT} value={draft.indication_scope} onChange={e => setDraft({ ...draft, indication_scope: e.target.value })} />
      </Field>
      <Field label="병용 공유 키(combo_key)" className="col-span-3">
        <input className={INPUT} value={draft.combo_key} placeholder="같은 병용 차수 브랜드끼리 동일 값" onChange={e => setDraft({ ...draft, combo_key: e.target.value })} />
      </Field>
      <Field label="암질심 통과" className="col-span-2">
        <input type="date" className={INPUT} value={draft.amjilsim_pass_date} onChange={e => setDraft({ ...draft, amjilsim_pass_date: e.target.value })} />
      </Field>
      <Field label="약평위 통과" className="col-span-2">
        <input type="date" className={INPUT} value={draft.yakpyungwi_pass_date} onChange={e => setDraft({ ...draft, yakpyungwi_pass_date: e.target.value })} />
      </Field>
      <Field label="급여 시행일" className="col-span-2">
        <input type="date" className={INPUT} value={draft.reimb_start_date} onChange={e => setDraft({ ...draft, reimb_start_date: e.target.value })} />
      </Field>
      <Field label="메모" className="col-span-5">
        <input className={INPUT} value={draft.notes} onChange={e => setDraft({ ...draft, notes: e.target.value })} />
      </Field>
      <div className="col-span-1 flex items-end pb-1.5">
        <label className="flex items-center gap-1 text-[10px] text-[#8B9BB4] cursor-pointer" title="자동 생성 등 적응증 범위 확인 필요">
          <input type="checkbox" className="accent-[#00E5CC]" checked={draft.needs_review} onChange={e => setDraft({ ...draft, needs_review: e.target.checked })} />
          검토 필요
        </label>
      </div>
    </div>
  );
}

function negText(r: AdminRound): string {
  const st = r.negotiation_status ? (NEGOTIATION_LABEL[r.negotiation_status] ?? r.negotiation_status) : null;
  const reg = r.nhis_registered_ym ? `공단 ${r.nhis_registered_ym}` : null;
  const done = r.negotiation_complete_date ? `→ ${r.negotiation_complete_date}` : null;
  return [reg, done, st && `(${st})`].filter(Boolean).join(' ') || '—';
}

export default function RoundsPanel({
  drug, drugs, onChanged,
}: { drug: AdminDrug; drugs: AdminDrug[]; onChanged: () => Promise<void> | void }) {
  const rounds = drug.rounds ?? [];
  const realRounds = rounds.filter((r): r is AdminRound & { round_id: number } => r.round_id != null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newDraft, setNewDraft] = useState<RoundDraft>(EMPTY_ROUND);
  const [editId, setEditId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<RoundDraft>(EMPTY_ROUND);
  const [share, setShare] = useState<{ drugId: string; eventId: string; roundId: string }>({ drugId: '', eventId: '', roundId: '' });

  const shareDrug = useMemo(() => drugs.find(d => String(d.drug_id) === share.drugId) ?? null, [drugs, share.drugId]);
  const otherDrugs = useMemo(
    () => drugs.filter(d => d.drug_id !== drug.drug_id && d.events.length > 0)
      .sort((a, b) => a.brand_kr.localeCompare(b.brand_kr, 'ko')),
    [drugs, drug.drug_id],
  );

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : '요청 실패');
    } finally {
      setBusy(false);
    }
  };

  const addRound = () => {
    if (!newDraft.round_label.trim()) {
      setError('차수 라벨 필수');
      return;
    }
    run(async () => {
      await createRound(drug.drug_id, draftToPayload(newDraft));
      setNewDraft(EMPTY_ROUND);
    });
  };

  const saveRound = (roundId: number) => {
    if (!editDraft.round_label.trim()) {
      setError('차수 라벨은 비울 수 없음');
      return;
    }
    run(async () => {
      await updateRound(roundId, draftToPayload(editDraft));
      setEditId(null);
    });
  };

  const removeRound = (r: AdminRound & { round_id: number }) => {
    if (!confirm(`"${r.round_label}" 차수를 삭제할까요? 이벤트·공단 행 원본은 남고 차수 연결만 해제됩니다.`)) return;
    run(() => deleteRound(r.round_id));
  };

  const setEventRound = (eventId: number, current: number | null | undefined, next: string) => {
    run(async () => {
      if (next) await linkEventToRound(Number(next), eventId);
      else if (current != null) await unlinkEventFromRound(current, eventId);
    });
  };

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-white text-sm font-semibold">
          <i className="ri-stack-line text-[#00E5CC] mr-1"></i>등재 차수 — {drug.brand_kr}
        </h3>
        <p className="text-[#4A5568] text-[11px] mt-0.5">
          같은 브랜드의 등재 차수(예: 2025 확대 11개 적응증 / 2026 파드셉 병용 확대)를 분리해 위원회 이벤트·공단 협상을 차수별로 연결합니다.
          {realRounds.length === 0 && ' 아직 분할 전 — 약제 행 자체가 하나의 차수로 보입니다. 차수를 추가하면 현재 약제 행이 기본 차수로 고정되고 기존 이벤트·공단 행은 기본 차수에 그대로 남습니다.'}
        </p>
      </div>

      {/* 차수 목록 */}
      <table className="w-full text-xs">
        <thead>
          <tr className="text-[#8B9BB4] text-[11px] border-b border-[#1E2530]">
            <th className="text-left py-1.5 pr-3">차수</th>
            <th className="text-left py-1.5 pr-3">유형</th>
            <th className="text-left py-1.5 pr-3">암질심 · 약평위</th>
            <th className="text-left py-1.5 pr-3">공단 협상</th>
            <th className="text-left py-1.5 pr-3">다음 단계</th>
            <th className="text-left py-1.5 pr-3">연결</th>
            <th className="text-right py-1.5">관리</th>
          </tr>
        </thead>
        <tbody>
          {rounds.map((r, i) => (
            <tr key={r.round_id ?? `v${i}`} className="border-b border-[#1E2530]/40 last:border-b-0 align-top">
              <td className="py-1.5 pr-3 max-w-[280px]">
                <span className="text-white font-medium">{r.round_label ?? '(라벨 없음)'}</span>
                {r.round_id == null && <span className="ml-1.5 text-[9px] px-1.5 py-0.5 rounded bg-[#4A5568]/20 text-[#8B9BB4]">분할 전</span>}
                {r.is_primary && r.round_id != null && <span className="ml-1.5 text-[9px] px-1.5 py-0.5 rounded bg-[#00E5CC]/10 text-[#00E5CC]" title="브랜드 요약에 쓰는 대표(진행 중 최신) 차수">대표</span>}
                {r.needs_review && <span className="ml-1.5 text-[9px] px-1.5 py-0.5 rounded bg-[#F59E0B]/10 text-[#F59E0B]" title="자동 생성 — 적응증 범위 확인 필요">검토</span>}
                {r.indication_scope && <span className="block text-[10px] text-[#4A5568] truncate" title={r.indication_scope}>{r.indication_scope}</span>}
                {r.combo_partners.length > 0 && (
                  <span className="block text-[10px] text-[#8B9BB4]" title={r.combo_key ?? undefined}>
                    <i className="ri-links-line mr-0.5"></i>병용: {r.combo_partners.map(p => `${p.name ?? `#${p.drugId}`} · ${p.roundLabel}`).join(', ')}
                  </span>
                )}
                {r.source && <span className="block text-[9px] text-[#4A5568]">출처 {r.source}</span>}
              </td>
              <td className="py-1.5 pr-3 text-[#8B9BB4] whitespace-nowrap">{r.list_type ?? '—'}</td>
              <td className="py-1.5 pr-3 text-[#8B9BB4] whitespace-nowrap">
                {r.amjilsim_pass_date ?? '—'} · {r.yakpyungwi_pass_date ?? '—'}
                {r.reimb_start_date && <span className="block text-[10px] text-emerald-500">급여 {r.reimb_start_date}</span>}
              </td>
              <td className="py-1.5 pr-3 text-[#8B9BB4] whitespace-nowrap">{negText(r)}</td>
              <td className="py-1.5 pr-3 text-[#8B9BB4] whitespace-nowrap">{r.next_step}</td>
              <td className="py-1.5 pr-3 text-[#8B9BB4] whitespace-nowrap">
                이벤트 {r.event_ids.length} · 공단 {r.nhis_ids.length}
                {r.shared_events.length > 0 && (
                  <span className="block text-[10px]">
                    공유 {r.shared_events.map(ev => (
                      <span key={ev.id} className="inline-flex items-center gap-0.5 mr-1">
                        #{ev.id} {ev.committee} {ev.date ?? ''}
                        {r.round_id != null && (
                          <button disabled={busy} onClick={() => run(() => unlinkEventFromRound(r.round_id as number, ev.id))}
                                  className="text-[#8B9BB4] hover:text-red-400 cursor-pointer" aria-label="공유 링크 해제">×</button>
                        )}
                      </span>
                    ))}
                  </span>
                )}
              </td>
              <td className="py-1.5 text-right whitespace-nowrap">
                {r.round_id != null && (
                  <>
                    <button onClick={() => { setEditId(r.round_id); setEditDraft(roundToDraft(r)); setError(null); }}
                            className="text-[#8B9BB4] text-[11px] hover:text-[#00E5CC] mr-2 cursor-pointer">편집</button>
                    <button onClick={() => removeRound(r as AdminRound & { round_id: number })} disabled={busy}
                            className="text-[#8B9BB4] text-[11px] hover:text-red-400 cursor-pointer">삭제</button>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {editId != null && (
        <div className="bg-[#00E5CC]/5 border border-[#1E2530] rounded-xl p-3 space-y-2">
          <p className="text-white text-xs font-semibold">차수 편집</p>
          <RoundForm draft={editDraft} setDraft={setEditDraft} />
          <div className="flex gap-2">
            <button onClick={() => saveRound(editId)} disabled={busy}
                    className="bg-[#00E5CC] text-[#0A0E1A] px-3 py-1.5 rounded-lg text-xs font-semibold cursor-pointer hover:bg-[#00C9B1] disabled:opacity-50">
              {busy ? '저장 중…' : '저장'}
            </button>
            <button onClick={() => setEditId(null)} className="px-3 py-1.5 rounded-lg text-xs text-[#8B9BB4] border border-[#1E2530] hover:text-white cursor-pointer">취소</button>
          </div>
        </div>
      )}

      {/* 이벤트 → 차수 */}
      <div>
        <p className="text-white text-xs font-semibold mb-1.5">위원회 이벤트 ↔ 차수</p>
        {drug.events.length === 0 ? (
          <p className="text-[#4A5568] text-[11px]">이 브랜드의 심의 이벤트가 없습니다.</p>
        ) : (
          <table className="w-full text-xs">
            <tbody>
              {drug.events.map(ev => (
                <tr key={ev.id} className="border-b border-[#1E2530]/40 last:border-b-0">
                  <td className="py-1 pr-3 text-[#8B9BB4] whitespace-nowrap">#{ev.id} {ev.date ?? '—'}</td>
                  <td className="py-1 pr-3 text-white whitespace-nowrap">{ev.committee} {ev.stateLabel}</td>
                  <td className="py-1 pr-3 text-[#4A5568] truncate max-w-[260px]">{ev.evidenceText ?? ev.evidenceUrl ?? ''}</td>
                  <td className="py-1 text-right">
                    <select
                      className={`${INPUT} w-auto min-w-[220px]`}
                      value={ev.roundId != null ? String(ev.roundId) : ''}
                      disabled={busy || realRounds.length === 0}
                      onChange={e => setEventRound(ev.id, ev.roundId, e.target.value)}
                      aria-label={`이벤트 ${ev.id} 차수`}
                    >
                      <option value="">{realRounds.length === 0 ? '— 분할 전' : '— 미연결 (규칙 자동 배정)'}</option>
                      {realRounds.map(r => <option key={r.round_id} value={String(r.round_id)}>{r.round_label}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* 다른 브랜드 이벤트 공유 (병용) */}
      {realRounds.length > 0 && (
        <div className="grid grid-cols-12 gap-2 items-end bg-[#161B27] border border-[#1E2530] rounded-xl p-3">
          <p className="col-span-12 text-white text-xs font-semibold">병용 이벤트 공유 연결 <span className="text-[#4A5568] font-normal">— 다른 브랜드 이벤트를 이 브랜드 차수에도 연결 (원래 브랜드 연결은 유지)</span></p>
          <Field label="브랜드" className="col-span-3">
            <select className={INPUT} value={share.drugId} onChange={e => setShare({ ...share, drugId: e.target.value, eventId: '' })}>
              <option value="">— 선택</option>
              {otherDrugs.map(d => <option key={d.drug_id} value={String(d.drug_id)}>{d.brand_kr}</option>)}
            </select>
          </Field>
          <Field label="이벤트" className="col-span-4">
            <select className={INPUT} value={share.eventId} disabled={!shareDrug} onChange={e => setShare({ ...share, eventId: e.target.value })}>
              <option value="">— 선택</option>
              {(shareDrug?.events ?? []).map(ev => (
                <option key={ev.id} value={String(ev.id)}>#{ev.id} {ev.date ?? ''} {ev.committee} {ev.stateLabel}</option>
              ))}
            </select>
          </Field>
          <Field label="이 브랜드 차수" className="col-span-3">
            <select className={INPUT} value={share.roundId} onChange={e => setShare({ ...share, roundId: e.target.value })}>
              <option value="">— 선택</option>
              {realRounds.map(r => <option key={r.round_id} value={String(r.round_id)}>{r.round_label}</option>)}
            </select>
          </Field>
          <div className="col-span-2">
            <button
              disabled={busy || !share.eventId || !share.roundId}
              onClick={() => run(async () => {
                await linkEventToRound(Number(share.roundId), Number(share.eventId));
                setShare({ drugId: share.drugId, eventId: '', roundId: share.roundId });
              })}
              className="w-full bg-[#00E5CC] text-[#0A0E1A] px-3 py-1.5 rounded-lg text-xs font-semibold cursor-pointer hover:bg-[#00C9B1] disabled:opacity-50"
            >
              공유 연결
            </button>
          </div>
        </div>
      )}

      {/* 공단 협상 행 → 차수 */}
      <div>
        <p className="text-white text-xs font-semibold mb-1.5">공단 협상 행 ↔ 차수 <span className="text-[#4A5568] font-normal">(같은 협상의 협상중·완료 아카이브 행은 함께 이동)</span></p>
        {(drug.nhis_rows ?? []).length === 0 ? (
          <p className="text-[#4A5568] text-[11px]">매칭된 공단 협상 행이 없습니다.</p>
        ) : (
          <table className="w-full text-xs">
            <tbody>
              {(drug.nhis_rows ?? []).map(n => (
                <tr key={n.id} className={`border-b border-[#1E2530]/40 last:border-b-0 ${n.superseded ? 'opacity-60' : ''}`}>
                  <td className="py-1 pr-3 text-[#8B9BB4] whitespace-nowrap">#{n.id} {n.listType}</td>
                  <td className="py-1 pr-3 text-white truncate max-w-[260px]" title={n.productName}>{n.productName}</td>
                  <td className="py-1 pr-3 text-[#8B9BB4] whitespace-nowrap">
                    {n.registeredYm ?? '—'} → {n.completedYm ?? '협상 중'} {n.result && n.result !== '-' ? `(${n.result})` : ''}
                    {n.superseded && <span className="ml-1 text-[9px]" title="완료 행으로 대체된 협상 중 아카이브">대체됨</span>}
                  </td>
                  <td className="py-1 text-right">
                    <select
                      className={`${INPUT} w-auto min-w-[220px]`}
                      value={n.roundId != null ? String(n.roundId) : ''}
                      disabled={busy || realRounds.length === 0}
                      onChange={e => run(() => setNhisRound(n.id, e.target.value ? Number(e.target.value) : null))}
                      aria-label={`공단 행 ${n.id} 차수`}
                    >
                      <option value="">{realRounds.length === 0 ? '— 분할 전' : '— 미연결'}</option>
                      {realRounds.map(r => <option key={r.round_id} value={String(r.round_id)}>{r.round_label}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* 차수 추가 */}
      <div className="bg-[#161B27] border border-[#1E2530] rounded-xl p-3 space-y-2">
        <p className="text-white text-xs font-semibold">차수 추가</p>
        <RoundForm draft={newDraft} setDraft={setNewDraft} />
        <button onClick={addRound} disabled={busy || !newDraft.round_label.trim()}
                className="bg-[#00E5CC] text-[#0A0E1A] px-4 py-1.5 rounded-lg text-xs font-semibold cursor-pointer hover:bg-[#00C9B1] disabled:opacity-50">
          {busy ? '처리 중…' : '차수 추가'}
        </button>
      </div>
      {error && <p className="text-red-400 text-xs">{error}</p>}
    </div>
  );
}
