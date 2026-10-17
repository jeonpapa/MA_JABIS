import { useEffect, useId, useRef, useState } from 'react';
import { fetchRelationshipMapObjectUrl } from '@/api/stakeholder';

// 관계도 모달(워크벤치 보기 탭 "관계도") — 인증 라우트의 SVG 를 fetch → blob → objectURL 로 <img> 에 표시.
// (<img src> 에 API 경로를 직접 넣으면 Authorization 헤더가 붙지 않는다.) 모달 모양은 common.css .sxm(스케치 003 수치).

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

  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  // Esc 닫기(캡처 단계 — 다른 Esc 핸들러보다 먼저) + 열릴 때 닫기 버튼 포커스 · 닫힐 때 이전 포커스 복원
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onCloseRef.current();
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      prev?.focus?.();
    };
  }, []);

  return (
    <div className="sxm-wrap" onClick={onClose}>
      <div className="sxm wide" role="dialog" aria-modal="true" aria-labelledby={titleId} onClick={e => e.stopPropagation()}>
        <div className="sxm-head">
          <h2 id={titleId}>기관·인물 관계도</h2>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="관계도 닫기 (Esc)" title="닫기 (Esc)">
            ×
          </button>
        </div>
        <div className="sxm-body">
          {state === 'loading' && <p>관계도를 불러오는 중…</p>}
          {state === 'empty' && <p>아직 동기화된 관계도가 없습니다.</p>}
          {state === 'error' && <div className="svx-err">관계도를 불러오지 못했습니다.</div>}
          {state === 'ready' && url && <img src={url} alt="기관·인물 관계도" style={{ display: 'block', width: '100%', height: 'auto' }} />}
        </div>
      </div>
    </div>
  );
}
