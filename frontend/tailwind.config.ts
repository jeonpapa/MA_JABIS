/** @type {import('tailwindcss').Config} */

// Stakeholder Signals 라이트 테마 토큰 — 값은 src/pages/stakeholder-signals/theme/svLight.css 의 CSS 변수(.sv-light 스코프).
// 여기서는 이름만 붙인다(bg-sv-surface, text-sv-muted …). .sv-light 밖에서는 변수가 없어 쓰지 않는다.
const v = (name: string) => `var(--sv-${name})`;
const svColors = {
  page: v('page'),
  surface: v('surface'),
  'surface-2': v('surface-2'),
  'surface-3': v('surface-3'),
  zebra: v('zebra'),
  hover: v('hover'),
  selected: v('selected'),
  line: v('line'),
  'line-soft': v('line-soft'),
  'line-strong': v('line-strong'),
  ink: v('ink'),
  'ink-2': v('ink-2'),
  text: v('text'),
  muted: v('muted'),
  faint: v('faint'),
  accent: v('accent'),
  'accent-ink': v('accent-ink'),
  'accent-soft': v('accent-soft'),
  focus: v('focus'),
  dark: v('dark'),
  'dark-hover': v('dark-hover'),
  'on-dark': v('on-dark'),
  ok: { bg: v('ok-bg'), fg: v('ok-fg'), line: v('ok-line'), dot: v('ok-dot') },
  info: { bg: v('info-bg'), fg: v('info-fg'), line: v('info-line'), dot: v('info-dot') },
  warn: { bg: v('warn-bg'), fg: v('warn-fg'), line: v('warn-line'), dot: v('warn-dot'), soft: v('warn-soft'), 'soft-fg': v('warn-soft-fg') },
  bad: { bg: v('bad-bg'), fg: v('bad-fg'), line: v('bad-line'), strong: v('bad-strong'), solid: v('bad-solid') },
  neutral: { bg: v('neutral-bg'), fg: v('neutral-fg'), dot: v('neutral-dot') },
  violet: { bg: v('violet-bg'), fg: v('violet-fg'), line: v('violet-line') },
  notice: { bg: v('notice-bg'), fg: v('notice-fg') },
  avatar: { bg: v('avatar-bg'), fg: v('avatar-fg'), ring: v('avatar-ring') },
  scrim: v('scrim'),
  'scrim-strong': v('scrim-strong'),
};

export default {
    content: [
      "./index.html",
      "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
      extend: {
        colors: { sv: svColors },
        boxShadow: {
          'sv-card': v('shadow-card'),
          'sv-ring': v('shadow-ring'),
          'sv-drawer': v('shadow-drawer'),
          'sv-modal': v('shadow-modal'),
          'sv-toast': v('shadow-toast'),
        },
      },
    },
    plugins: [],
  }
