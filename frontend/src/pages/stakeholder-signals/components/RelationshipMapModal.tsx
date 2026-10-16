import { useEffect, useState } from 'react';
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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="sxm-wrap" onClick={onClose} role="dialog" aria-modal="true" aria-label="기관·인물 관계도">
      <div className="sxm wide" onClick={e => e.stopPropagation()}>
        <div className="sxm-head">
          <h2>기관·인물 관계도</h2>
          <button onClick={onClose} aria-label="닫기">
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
