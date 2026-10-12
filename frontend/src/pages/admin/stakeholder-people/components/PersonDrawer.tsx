import { useEffect } from 'react';
import { headlineQuoteOf, TOPIC_LABELS, type SVPerson, type SVPersonDetail } from '@/api/stakeholder';
import PersonAvatar from '@/pages/stakeholder-signals/components/PersonAvatar';
import { btnDanger, btnGhost, btnPrimary, fmtDateTime } from '@/pages/admin/stakeholder-audit/components/uiHelpers';
import { badge, closeBtn, errorBox, scrim, tone } from '@/pages/stakeholder-signals/theme/sv';

// 인물 상세 드로어 (스케치 003). 삭제 기능은 없다(일시중지로만 — 소스와 동일).
// photo_url(언론 CDN)은 admin 상세에 오더라도 **텍스트 메타로만** 표시한다(이미지·링크로 렌더링 금지).
// 화이트 톤(스케치 003 .drawer): 우측 470px, 옅은 스크림, 회색 닫기, 72px 프로필, 105px 상세 그리드, 파란 안내.

const ROLE_STATUS_LABELS: Record<string, string> = {
  scheduled: '적용 예정',
  applied: '현재',
  superseded: '이전',
  failed: '반영 실패',
};

export function StatusBadge({ status }: { status?: string | null }) {
  const active = status === 'active';
  return (
    <span className={`${badge} !text-[11px] !px-[9px] !py-[5px] ${active ? tone.ok : tone.neutral}`}>
      {active ? '● 활성' : '일시중지'}
    </span>
  );
}

/** 확인 상태. compact = 스케치 003 .verify 한 줄 텍스트(검증됨 / 후보 단계 — 주황), 기본은 배지. */
export function VerifyTag({ status, compact = false }: { status?: string | null; compact?: boolean }) {
  const verified = status === 'verified';
  if (compact) {
    return (
      <span className={`text-[11px] ${verified ? 'text-sv-muted' : 'text-sv-warn-fg font-semibold'}`}>
        {verified ? '검증됨' : '후보 단계'}
      </span>
    );
  }
  return verified ? (
    <span className={`${badge} ${tone.info}`}>
      <i className="ri-verified-badge-line"></i>검증됨
    </span>
  ) : (
    <span className={`${badge} ${tone.warn}`}>후보 · 공식 직책 확인 필요</span>
  );
}

export default function PersonDrawer({
  base,
  detail,
  loading,
  error,
  photoVersion,
  onClose,
  onEdit,
  onToggleStatus,
  onPhoto,
  onDeletePhoto,
  blockEscape,
}: {
  base: SVPerson;
  detail: SVPersonDetail | null;
  loading: boolean;
  error: string | null;
  photoVersion: number;
  onClose: () => void;
  onEdit: () => void;
  onToggleStatus: () => void;
  onPhoto: () => void;
  onDeletePhoto: () => void;
  blockEscape: boolean;
}) {
  const p: SVPersonDetail = { ...base, ...(detail ?? {}) };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !blockEscape) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, blockEscape]);

  const grid: [string, React.ReactNode][] = [
    ['부서', p.department || '—'],
    ['직책', p.role || '—'],
    ['확인 상태', <VerifyTag key="v" status={p.verification_status} />],
    ['검색 별칭', (p.aliases ?? []).join(', ') || '없음'],
    ['주제 태그', (p.topic_tags ?? []).join(', ') || '—'],
    ['반영 예정', p.effective_from ? `${p.effective_from} 일일 모니터링` : '—'],
    ['중요도', typeof p.importance_score === 'number' ? `${p.importance_score} / 5` : '—'],
    ['발언 블록', typeof p.statements_count === 'number' ? `${p.statements_count}건` : '—'],
    ['최근 변경', `${fmtDateTime(p.updated_at)}${p.updated_by ? ` · ${p.updated_by}` : ''}`],
    ['등록 경로', `${p.origin ?? '—'}${p.discovered_from_proposal ? ` · 제안 ${p.discovered_from_proposal}` : ''}`],
    ['인물 ID', <span key="id" className="font-mono text-[12px]">{p.person_id}</span>],
  ];

  return (
    <>
      <div className={`fixed inset-0 z-[55] ${scrim}`} onClick={onClose} />
      <aside
        className="fixed right-0 top-0 z-[56] h-full w-full sm:w-[min(470px,94vw)] bg-sv-surface shadow-sv-drawer flex flex-col"
        aria-label="모니터링 대상 상세"
      >
        <div className="flex items-center justify-between px-7 pt-6 pb-2">
          <span className="text-[12px] text-sv-muted">모니터링 대상 상세</span>
          <button onClick={onClose} aria-label="상세 닫기" className={closeBtn}>
            <i className="ri-close-line text-lg"></i>
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-7 pb-5 space-y-4">
          <div className="flex items-center gap-4 my-4">
            <PersonAvatar key={`${p.person_id}-${photoVersion}`} personId={p.person_id} name={p.name} hasPhoto={p.has_photo} size="xl" />
            <div className="min-w-0">
              <h2 className="text-[22px] font-extrabold text-sv-ink truncate">{p.name}</h2>
              <p className="text-[13px] text-sv-ink-2 truncate">
                {p.organization} · {p.role}
              </p>
              <div className="mt-1.5">
                <StatusBadge status={p.status} />
              </div>
            </div>
          </div>

          <dl className="grid grid-cols-[105px_1fr] gap-x-3 gap-y-3 py-4 border-t border-sv-line-soft text-[13px]">
            {grid.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-sv-muted">{k}</dt>
                <dd className="text-sv-ink font-bold break-words">{v}</dd>
              </div>
            ))}
          </dl>

          <div className={`rounded-lg px-3.5 py-3 text-[13px] leading-relaxed ${tone.notice}`}>
            {p.status === 'active'
              ? `이 인물은 ${p.effective_from ?? '익일'} 모니터링부터 계속 포함됩니다.`
              : `일시중지 상태는 ${p.effective_from ?? '익일'} 모니터링부터 적용됩니다. 기존 수집 데이터는 유지됩니다.`}{' '}
            변경은 모두 익일 07:00 KST 부터 반영됩니다.
          </div>

          {p.photo_url && (
            <div className="rounded-lg border border-sv-line bg-sv-surface-2 px-3.5 py-2.5">
              <p className="text-[11px] font-bold text-sv-text">출처 사진 링크 (메타데이터 · 표시하지 않음)</p>
              <p className="mt-0.5 font-mono text-[11px] text-sv-muted break-all select-all">{p.photo_url}</p>
              <p className="mt-0.5 text-[11px] text-sv-muted">언론사 사진은 저작권상 화면에 쓰지 않습니다. 필요하면 내부 사진을 업로드하세요.</p>
            </div>
          )}

          <section>
            <h3 className="text-[13px] font-extrabold text-sv-ink mb-2">직책 이력</h3>
            {loading && !detail ? (
              <div className="h-10 rounded bg-sv-surface-3 animate-pulse" />
            ) : error ? (
              <p className={errorBox}>상세를 불러오지 못했습니다 — {error}</p>
            ) : (p.role_history ?? []).length === 0 ? (
              <p className="text-[12px] text-sv-muted">기록된 직책 변경이 없습니다.</p>
            ) : (
              <ol className="space-y-1.5">
                {(p.role_history ?? []).map((h, i) => (
                  <li key={h.id ?? i} className="rounded-lg border border-sv-line px-3 py-2 text-[12px]">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`${badge} ${h.status === 'applied' ? tone.ok : h.status === 'scheduled' ? tone.info : h.status === 'failed' ? tone.bad : tone.neutral}`}>
                        {ROLE_STATUS_LABELS[h.status ?? ''] ?? h.status ?? '—'}
                      </span>
                      <span className="text-sv-ink">
                        {h.organization} · {h.department} · {h.role}
                      </span>
                    </div>
                    <p className="mt-0.5 text-sv-muted">
                      {h.effective_from ?? '—'} ~ {h.effective_to ?? ''}
                      {h.proposal_id ? ` · 제안 ${h.proposal_id}` : ''}
                    </p>
                  </li>
                ))}
              </ol>
            )}
          </section>

          {(p.recent_statements ?? []).length > 0 && (
            <section>
              <h3 className="text-[13px] font-extrabold text-sv-ink mb-2">최근 발언</h3>
              <ul className="space-y-1.5">
                {(p.recent_statements ?? []).map(s => {
                  const hq = headlineQuoteOf(s);
                  return (
                    <li key={s.statement_id} className="rounded-lg border border-sv-line px-3 py-2 text-[12px]">
                      <p className="text-sv-muted">
                        {s.event?.event_date ?? '—'} · {TOPIC_LABELS[s.topic ?? ''] ?? s.topic ?? '—'} · {s.event?.title ?? ''}
                      </p>
                      <p className="mt-0.5 text-sv-ink-2 line-clamp-2">
                        {(hq || s.summary_text) && (
                          <span
                            className={`mr-1.5 rounded px-1.5 py-px text-[10px] font-bold ${hq ? tone.info : tone.neutral}`}
                          >
                            {hq ? '인용' : '요약'}
                          </span>
                        )}
                        <span data-verbatim={hq ? 'true' : undefined} className={hq ? '' : 'italic'}>
                          {hq?.quote_text ?? s.summary_text ?? '—'}
                        </span>
                      </p>
                    </li>
                  );
                })}
              </ul>
            </section>
          )}
        </div>
        <div className="border-t border-sv-line-soft px-7 py-4 grid grid-cols-2 gap-2">
          <button className={btnPrimary} onClick={onEdit}>
            <i className="ri-edit-line"></i>편집
          </button>
          <button className={btnGhost} onClick={onToggleStatus}>
            <i className={p.status === 'active' ? 'ri-pause-circle-line' : 'ri-play-circle-line'}></i>
            {p.status === 'active' ? '일시중지' : '재개'}
          </button>
          <button className={btnGhost} onClick={onPhoto}>
            <i className="ri-image-add-line"></i>
            {p.has_photo ? '사진 교체' : '사진 업로드'}
          </button>
          <button className={btnDanger} onClick={onDeletePhoto} disabled={!p.has_photo} title={p.has_photo ? undefined : '내부 사진이 없습니다'}>
            <i className="ri-delete-bin-line"></i>사진 삭제
          </button>
        </div>
      </aside>
    </>
  );
}
