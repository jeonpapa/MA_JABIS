import type { ReactNode } from 'react';
import { SV_ROOT } from './sv';

// Stakeholder Signals 라이트 셸 — 스케치 001/003/004 의 .main / .top(crumb) / .hero 구조.
// SvPage 의 .sv-light 래퍼가 팔레트 변수의 유일한 스코프다(사이드바 등 대쉬보드 셸은 그대로 다크).

export function SvPage({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`${SV_ROOT} min-h-screen ${className}`}>{children}</div>;
}

/** 스케치 .main — 좌우 34px(모바일 16px), 최대 1500px */
export function SvMain({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`px-4 sm:px-[34px] pb-14 max-w-[1500px] ${className}`}>{children}</div>;
}

/** 스케치 .top — 왼쪽 경로(crumb), 오른쪽 액션 */
export function SvTopBar({ crumb, children }: { crumb: ReactNode; children?: ReactNode }) {
  return (
    <div className="min-h-[72px] py-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
      <div className="text-[13px] text-sv-muted min-w-0">{crumb}</div>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

/** 스케치 .hero — 30px 제목 + 부제, 오른쪽 스탬프/상태 카드 */
export function SvHero({
  title,
  sub,
  aside,
  alignTop = false,
}: {
  title: ReactNode;
  sub?: ReactNode;
  aside?: ReactNode;
  /** 오른쪽 블록이 제목보다 높을 때(감사 2×2 지표) 위쪽 정렬 */
  alignTop?: boolean;
}) {
  return (
    <section className={`flex flex-col lg:flex-row ${alignTop ? 'lg:items-start' : 'lg:items-end'} justify-between gap-4 lg:gap-5 mt-3 mb-5`}>
      <div className="min-w-0">
        <h1 className="text-[26px] sm:text-[30px] font-extrabold tracking-[-1.4px] leading-tight text-sv-ink mb-2">{title}</h1>
        {sub && <div className="text-[14px] leading-[1.65] text-sv-text max-w-3xl sv-ko">{sub}</div>}
      </div>
      {aside && <div className="flex-shrink-0">{aside}</div>}
    </section>
  );
}
