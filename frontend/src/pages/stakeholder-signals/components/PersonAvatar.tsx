import { useEffect, useState } from 'react';
import { fetchPersonPhotoObjectUrl } from '@/api/stakeholder';

// 인물 아바타 — 스케치의 아바타 상자(.avatar / .mini-avatar 등, 크기는 화면 CSS 가 정한다)를 그대로 쓰고,
// 안에는 이니셜, 또는 서버가 내부 사진이 있다고 알려줄 때만 인증 라우트(/api/stakeholder/people/<id>/photo)
// 에서 blob 으로 받은 사진을 그린다. 언론 CDN 링크(photo_url)는 어떤 경우에도 쓰지 않는다
// (stakeholder_verbatim_rules.md §5) — 스케치가 CDN 사진을 쓰는 자리도 이 상자로 대신한다.

function initialOf(name?: string | null): string {
  const n = (name || '').trim();
  return n ? Array.from(n)[0] : '?';
}

export default function PersonAvatar({
  name,
  personId,
  hasPhoto,
  className = 'avatar',
}: {
  name?: string | null;
  personId?: string | null;
  hasPhoto?: boolean | null;
  className?: string;
}) {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSrc(null);
    if (personId && hasPhoto) {
      fetchPersonPhotoObjectUrl(personId).then(url => {
        if (!cancelled) setSrc(url);
      });
    }
    return () => {
      cancelled = true;
    };
  }, [personId, hasPhoto]);

  return (
    <span className={className} aria-hidden={src ? undefined : true}>
      {src ? <img src={src} alt={`${name || ''} 프로필 사진`} onError={() => setSrc(null)} /> : initialOf(name)}
    </span>
  );
}
