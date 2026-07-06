import { allNavItems, adminNavItems, type NavItem } from '@/components/feature/Sidebar';

// route pathname → 사람이 읽는 페이지 라벨.
// Sidebar nav 배열(권위 소스)을 재사용해 라벨 drift 를 방지한다.

function navEntries(): NavItem[] {
  return [...allNavItems, ...adminNavItems];
}

/** pathname 을 사이드바 라벨로 변환. '/' 는 exact, 그 외 prefix 매칭(긴 경로 우선). */
export function pageLabelFor(pathname: string): string {
  const path = (pathname || '/').split('?')[0].split('#')[0] || '/';
  const entries = navEntries();

  if (path === '/') {
    return entries.find(e => e.path === '/')?.label ?? 'Dashboard Overview';
  }

  const candidates = entries
    .filter(e => e.path !== '/')
    .sort((a, b) => b.path.length - a.path.length);
  for (const e of candidates) {
    if (path === e.path || path.startsWith(`${e.path}/`)) return e.label;
  }

  // fallback: pathname 정리 (예: /admin/some-page → admin / some page)
  const cleaned = path
    .replace(/^\/+|\/+$/g, '')
    .split('/')
    .filter(Boolean)
    .map(seg => seg.replace(/[-_]+/g, ' '))
    .join(' / ');
  return cleaned || path;
}
