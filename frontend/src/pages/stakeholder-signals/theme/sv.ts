import './svLight.css';

// Stakeholder Signals 라이트 테마 — 조합 클래스 모듈 (원 레포 스케치 001~004 의 .card/.btn/.chip/.badge/.field 대응).
// 색 값은 svLight.css(.sv-light 스코프 CSS 변수) 한 곳에만 있고, 여기는 tailwind `sv-*` 이름으로 조합만 한다.
// 이 모듈을 쓰는 컴포넌트는 반드시 .sv-light 래퍼(SvPage) 안에서 렌더링되어야 한다(모달·드로어·토스트 포함 —
// 모두 포털 없이 같은 DOM 트리에 그려지므로 변수가 상속된다).

export const SV_ROOT = 'sv-light';

export const focusRing =
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sv-focus';

/** 스케치 .card — 흰 면 + 1px 링 그림자 */
export const card = 'bg-sv-surface rounded-[10px] shadow-sv-card';
/** 스케치 .panel/.table — 목록 컨테이너 */
export const panel = `${card} overflow-hidden`;
/** 스케치 .stat/.principle — 테두리형 작은 카드 */
export const boxCard = 'bg-sv-surface rounded-[10px] border border-sv-line';

const btnBase = `inline-flex items-center justify-center gap-1.5 rounded-lg px-3.5 py-2 min-h-[38px] text-[13px] font-bold cursor-pointer transition-colors disabled:cursor-not-allowed whitespace-nowrap ${focusRing}`;
/** 스케치 .btn — 흰 버튼 */
export const btn = `${btnBase} bg-sv-surface text-sv-ink shadow-sv-ring hover:bg-sv-surface-3 disabled:opacity-50`;
/** 스케치 .btn.dark / .btn.primary */
export const btnDark = `${btnBase} bg-sv-dark text-sv-on-dark hover:bg-sv-dark-hover disabled:bg-sv-neutral-bg disabled:text-sv-muted`;
/** 스케치 .btn.danger */
export const btnDanger = `${btnBase} bg-sv-surface text-sv-bad-fg shadow-sv-ring hover:bg-sv-bad-bg disabled:opacity-50`;
/** 작은 텍스트 버튼(다시 시도·접기 등) */
export const linkBtn = `text-sv-accent-ink font-semibold hover:underline cursor-pointer ${focusRing}`;
/** 닫기 버튼(스케치 .close — 회색 사각) */
export const closeBtn = `inline-flex items-center justify-center gap-1 rounded-md bg-sv-neutral-bg px-2.5 h-9 min-w-9 text-[13px] font-semibold text-sv-ink hover:bg-sv-line cursor-pointer disabled:opacity-40 ${focusRing}`;

/** 스케치 .tab/.chip — 알약형, 활성은 검정 */
export function pill(active: boolean): string {
  return `inline-flex items-center gap-1.5 rounded-full px-3 py-2 text-[13px] font-bold whitespace-nowrap cursor-pointer transition-colors ${focusRing} ${
    active ? 'bg-sv-dark text-sv-on-dark' : 'bg-sv-surface text-sv-text shadow-[0_0_0_1px_var(--sv-line)] hover:text-sv-ink'
  }`;
}
/** 알약 안 개수 표시 */
export function pillCount(active: boolean): string {
  return `text-[11px] font-semibold ${active ? 'text-sv-on-dark opacity-80' : 'text-sv-muted'}`;
}

/** 스케치 .field input */
export const input =
  'w-full rounded-lg border border-sv-line-strong bg-sv-surface px-3 py-2.5 text-[13px] text-sv-ink placeholder:text-sv-faint focus:outline focus:outline-2 focus:outline-sv-focus focus:border-transparent transition-colors disabled:bg-sv-surface-3 disabled:text-sv-muted disabled:cursor-not-allowed';
/** 스케치 .field label */
export const fieldLabel = 'block text-[12px] font-bold text-sv-ink mb-1.5';
export const hint = 'text-[11px] leading-relaxed text-sv-muted';

/** 의미 색 — 배지/칩 배경+글자 */
export const tone = {
  ok: 'bg-sv-ok-bg text-sv-ok-fg',
  info: 'bg-sv-info-bg text-sv-info-fg',
  warn: 'bg-sv-warn-bg text-sv-warn-fg',
  bad: 'bg-sv-bad-bg text-sv-bad-fg',
  /** 원문 불일치·SHA 불일치 — 숨기지 않고 가장 강하게 */
  badStrong: 'bg-sv-bad-solid text-sv-bad-fg ring-1 ring-inset ring-sv-bad-line',
  neutral: 'bg-sv-neutral-bg text-sv-neutral-fg',
  violet: 'bg-sv-violet-bg text-sv-violet-fg',
  notice: 'bg-sv-notice-bg text-sv-notice-fg',
} as const;
export type Tone = keyof typeof tone;

/** 스케치 .badge — 알약형 작은 배지 */
export const badge = 'inline-flex items-center gap-1 rounded-full px-2 py-[3px] text-[10px] font-extrabold whitespace-nowrap';

/** 섹션 라벨(스케치 .label / thead) */
export const eyebrow = 'text-[11px] font-extrabold text-sv-accent-ink';
export const sectionLabel = 'text-[11px] font-bold text-sv-muted';
export const thead = 'bg-sv-surface-3 text-[11px] text-sv-muted';

/** 오류 박스 */
export const errorBox = 'rounded-lg border border-sv-bad-line bg-sv-bad-bg px-3 py-2 text-[12px] text-sv-bad-fg';

/** 오버레이 */
export const scrim = 'bg-sv-scrim';
export const scrimStrong = 'bg-sv-scrim-strong';
