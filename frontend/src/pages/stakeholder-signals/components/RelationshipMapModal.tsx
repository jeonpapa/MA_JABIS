import { useEffect, useState } from 'react';
import { fetchRelationshipMapObjectUrl } from '@/api/stakeholder';
import { closeBtn, scrimStrong } from '../theme/sv';

// 관계도 모달 — 인증 라우트의 SVG 를 fetch → blob → objectURL 로 <img> 에 표시.
// (<img src> 에 API 경로를 직접 넣으면 Authorization 헤더가 붙지 않는다.)

export default function RelationshipMapModal({ onClose }: { onClose: () => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'empty' | 'error'>('loading');

  useEffect(() => {
    let cancelled = false;
    let created: string | null = null;
    fetchRelationshipMapObjectUrl()
      .then(u => {
        created = u;
        if (cancelled) return;
        setUrl(u);
        setState(u ? 'ready' : 'empty');
      })
      .catch(() => {
        if (!cancelled) setState('error');
      });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className={`fixed inset-0 z-[60] ${scrimStrong} flex items-center justify-center p-4`}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="기관·인물 관계도"
    >
      <div
        className="bg-sv-surface rounded-xl shadow-sv-modal w-full max-w-6xl max-h-[90vh] flex flex-col overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 px-[22px] py-4 border-b border-sv-line-soft">
          <div>
            <h2 className="text-sv-ink font-extrabold text-[18px]">기관·인물 관계도</h2>
            <p className="text-sv-muted text-[12px]">외부 에이전트가 발행한 정본 산출물(SVG) — 읽기 전용</p>
          </div>
          <button onClick={onClose} className={closeBtn} aria-label="닫기">
            <i className="ri-close-line text-lg"></i>
          </button>
        </div>
        <div className="flex-1 overflow-auto p-4 bg-sv-surface">
          {state === 'loading' && (
            <p className="text-center py-16 text-sm text-sv-muted">
              <i className="ri-loader-4-line animate-spin mr-2"></i>관계도를 불러오는 중…
            </p>
          )}
          {state === 'empty' && <p className="text-center py-16 text-sm text-sv-muted">아직 동기화된 관계도가 없습니다.</p>}
          {state === 'error' && <p className="text-center py-16 text-sm text-sv-bad-fg font-semibold">관계도를 불러오지 못했습니다.</p>}
          {state === 'ready' && url && <img src={url} alt="기관·인물 관계도" className="w-full h-auto mx-auto" />}
        </div>
      </div>
    </div>
  );
}
