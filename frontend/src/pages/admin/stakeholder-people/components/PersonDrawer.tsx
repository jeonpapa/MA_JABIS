import { useEffect } from 'react';
import { headlineQuoteOf, TOPIC_LABELS, type SVPerson, type SVPersonDetail } from '@/api/stakeholder';
import PersonAvatar from '@/pages/stakeholder-signals/components/PersonAvatar';
import { btnDanger, btnGhost, btnPrimary } from '@/pages/admin/stakeholder-audit/components/ui';
import { fmtDateTime } from '@/pages/admin/stakeholder-audit/components/uiHelpers';

// 인물 상세 드로어 (스케치 003). 삭제 기능은 없다(일시중지로만 — 소스와 동일).
// photo_url(언론 CDN)은 admin 상세에 오더라도 **텍스트 메타로만** 표시한다(이미지·링크로 렌더링 금지).

const ROLE_STATUS_LABELS: Record<string, string> = {
  scheduled: '적용 예정',
  applied: '현재',
  superseded: '이전',
  failed: '반영 실패',
};

export function StatusBadge({ status }: { status?: string | null }) {
  const active = status === 'active';
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold whitespace-nowrap ${
        active ? 'bg-[#00E5CC]/10 text-[#00E5CC]' : 'bg-[#1E2530] text-[#8B9BB4]'
      }`}
    >
      {active ? '● 활성' : '일시중지'}
    </span>
  );
}

export function VerifyTag({ status }: { status?: string | null }) {
  return status === 'verified' ? (
    <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold whitespace-nowrap bg-[#3B82F6]/10 text-[#60A5FA]">
      <i className="ri-verified-badge-line"></i>검증됨
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold whitespace-nowrap bg-[#F59E0B]/10 text-[#F59E0B]">
      후보 · 공식 직책 확인 필요
    </span>
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
    ['인물 ID', <span key="id" className="font-mono">{p.person_id}</span>],
  ];

  return (
    <>
      <div className="fixed inset-0 z-[55] bg-black/40" onClick={onClose} />
      <aside
        className="fixed right-0 top-0 z-[56] h-full w-full max-w-md bg-[#11161F] border-l border-[#1E2530] flex flex-col"
        aria-label="모니터링 대상 상세"
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-[#1E2530]">
          <span className="text-[11px] text-[#8B9BB4]">모니터링 대상 상세</span>
          <button onClick={onClose} aria-label="상세 닫기" className="text-[#8B9BB4] hover:text-white cursor-pointer">
            <i className="ri-close-line text-lg"></i>
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
          <div className="flex items-center gap-3">
            <PersonAvatar key={`${p.person_id}-${photoVersion}`} personId={p.person_id} name={p.name} hasPhoto={p.has_photo} size="lg" />
            <div className="min-w-0">
              <h2 className="text-lg font-bold text-white truncate">{p.name}</h2>
              <p className="text-xs text-[#8B9BB4] truncate">
                {p.organization} · {p.role}
              </p>
              <div className="mt-1">
                <StatusBadge status={p.status} />
              </div>
            </div>
          </div>

          <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1.5 text-xs">
            {grid.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-[#8B9BB4]">{k}</dt>
                <dd className="text-white break-words">{v}</dd>
              </div>
            ))}
          </dl>

          <div className="rounded-lg bg-[#3B82F6]/10 px-3 py-2 text-[11px] leading-relaxed text-[#93C5FD]">
            {p.status === 'active'
              ? `이 인물은 ${p.effective_from ?? '익일'} 모니터링부터 계속 포함됩니다.`
              : `일시중지 상태는 ${p.effective_from ?? '익일'} 모니터링부터 적용됩니다. 기존 수집 데이터는 유지됩니다.`}{' '}
            변경은 모두 익일 07:00 KST 부터 반영됩니다.
          </div>

          {p.photo_url && (
            <div className="rounded-lg border border-[#1E2530] px-3 py-2">
              <p className="text-[10px] font-semibold text-[#8B9BB4]">출처 사진 링크 (메타데이터 · 표시하지 않음)</p>
              <p className="mt-0.5 font-mono text-[10px] text-[#4A5568] break-all select-all">{p.photo_url}</p>
              <p className="mt-0.5 text-[10px] text-[#4A5568]">언론사 사진은 저작권상 화면에 쓰지 않습니다. 필요하면 내부 사진을 업로드하세요.</p>
            </div>
          )}

          <section>
            <h3 className="text-xs font-bold text-white mb-1.5">직책 이력</h3>
            {loading && !detail ? (
              <div className="h-10 rounded bg-[#1E2530] animate-pulse" />
            ) : error ? (
              <p className="text-[11px] text-red-400">상세를 불러오지 못했습니다 — {error}</p>
            ) : (p.role_history ?? []).length === 0 ? (
              <p className="text-[11px] text-[#4A5568]">기록된 직책 변경이 없습니다.</p>
            ) : (
              <ol className="space-y-1.5">
                {(p.role_history ?? []).map((h, i) => (
                  <li key={h.id ?? i} className="rounded-lg border border-[#1E2530] px-3 py-2 text-[11px]">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded bg-[#1E2530] px-1.5 py-0.5 text-[10px] font-semibold text-[#C9D1DD]">
                        {ROLE_STATUS_LABELS[h.status ?? ''] ?? h.status ?? '—'}
                      </span>
                      <span className="text-white">
                        {h.organization} · {h.department} · {h.role}
                      </span>
                    </div>
                    <p className="mt-0.5 text-[#8B9BB4]">
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
              <h3 className="text-xs font-bold text-white mb-1.5">최근 발언</h3>
              <ul className="space-y-1.5">
                {(p.recent_statements ?? []).map(s => {
                  const hq = headlineQuoteOf(s);
                  return (
                    <li key={s.statement_id} className="rounded-lg border border-[#1E2530] px-3 py-2 text-[11px]">
                      <p className="text-[#8B9BB4]">
                        {s.event?.event_date ?? '—'} · {TOPIC_LABELS[s.topic ?? ''] ?? s.topic ?? '—'} · {s.event?.title ?? ''}
                      </p>
                      <p className="mt-0.5 text-[#E2E8F0] line-clamp-2">{hq?.quote_text ?? s.summary_text ?? '—'}</p>
                    </li>
                  );
                })}
              </ul>
            </section>
          )}
        </div>
        <div className="border-t border-[#1E2530] px-5 py-3 grid grid-cols-2 gap-2">
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
