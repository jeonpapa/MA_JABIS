import { useEffect } from 'react';
import type { SVPerson } from '@/api/stakeholder';
import PersonAvatar from '@/pages/stakeholder-signals/components/PersonAvatar';

// 스케치 003 인물 상세 드로어 그대로 — .drawer-backdrop + aside.drawer(470px) > .drawer-head / .profile /
// .detail-grid(부서·확인 상태·검색 별칭·반영 예정·최근 변경) / .notice / .drawer-actions(수정 · 일시중지/활성화).
// 삭제 기능은 없다(일시중지로만 — 소스와 동일). 사진은 수정 모달의 사진 업로드/삭제로 관리한다(스케치와 같은 흐름).

export default function PersonDrawer({
  person,
  photoVersion,
  onClose,
  onEdit,
  onToggleStatus,
  blockEscape,
}: {
  person: SVPerson | null;
  photoVersion: number;
  onClose: () => void;
  onEdit: () => void;
  onToggleStatus: () => void;
  blockEscape: boolean;
}) {
  const open = !!person;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !blockEscape) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, blockEscape]);

  const p = person;
  return (
    <>
      <div className={`drawer-backdrop${open ? ' open' : ''}`} onClick={onClose}></div>
      <aside className={`drawer${open ? ' open' : ''}`} aria-hidden={!open}>
        <div className="drawer-head">
          <span className="muted">모니터링 대상 상세</span>
          <button className="close" onClick={onClose} aria-label="상세 닫기" tabIndex={open ? 0 : -1}>
            ×
          </button>
        </div>
        {p && (
          <div>
            <div className="profile">
              <PersonAvatar key={`${p.person_id}-${photoVersion}`} personId={p.person_id} name={p.name} hasPhoto={p.has_photo} />
              <div>
                <h2>{p.name}</h2>
                <div>
                  {p.organization} · {p.role}
                </div>
                <span className={`badge ${p.status}`}>{p.status === 'active' ? '● 활성' : '일시중지'}</span>
              </div>
            </div>
            <div className="detail-grid">
              <span>부서</span>
              <b>{p.department}</b>
              <span>확인 상태</span>
              <b>{p.verification_status === 'verified' ? '검증됨' : '후보 — 공식 근거 필요'}</b>
              <span>검색 별칭</span>
              <b>{(p.aliases ?? []).join(', ') || '없음'}</b>
              <span>반영 예정</span>
              <b>{p.effective_from} 일일 모니터링</b>
              <span>최근 변경</span>
              <b>{p.updated_at}</b>
            </div>
            <div className="notice">
              {p.status === 'active'
                ? `이 인물은 ${p.effective_from} 모니터링부터 계속 포함됩니다.`
                : `일시중지 상태는 ${p.effective_from} 모니터링부터 적용됩니다. 기존 수집 데이터는 유지됩니다.`}
            </div>
            <div className="drawer-actions">
              <button className="btn primary" onClick={onEdit}>
                수정
              </button>
              <button className="btn" onClick={onToggleStatus}>
                {p.status === 'active' ? '일시중지' : '활성화'}
              </button>
            </div>
          </div>
        )}
      </aside>
    </>
  );
}
