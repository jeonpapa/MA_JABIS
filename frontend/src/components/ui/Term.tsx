import { useId, useRef, useState } from 'react';
import type { ReactNode } from 'react';

/**
 * 용어 툴팁 — 전문 용어 옆에 점선 밑줄 + 마우스오버/포커스 시 쉬운 한국어 정의.
 * 키보드(Tab 포커스)·스크린리더(aria-describedby) 모두 접근 가능. Esc 로 닫힘.
 *
 *   <Term k="A8" />                       → 사전 기본 라벨 'A8' + 정의
 *   <Term k="adjusted">조정가</Term>       → 자식 텍스트를 라벨로
 *   <Term def="직접 정의">임의 용어</Term>  → 사전 밖 용어
 */
const GLOSSARY = {
  A8: {
    label: 'A8',
    def: '약가 비교에 쓰는 8개 참조국(미국·영국·독일·프랑스·이탈리아·스위스·일본·캐나다). 신약 약가 협상과 사후관리에서 해외 가격 기준으로 씁니다.',
  },
  adjusted: {
    label: '조정가',
    def: '해외 표시가를 한국 약가와 비교할 수 있게 환산한 값. 최소단위(정·바이알)당 가격 × 환율 × 공장도율 × 1.10(국내 부가세) × 1.0869(국내 유통거래폭).',
  },
  factoryRatio: {
    label: '공장도율',
    def: '공장도 출하비율 — 해외 소매·약국가에서 제약사 출하가(공장도가)만 남기기 위해 곱하는 국가별 비율. 이미 출하가인 자료원(예: 이탈리아 Class H)은 1.0입니다.',
  },
  fxPeriod: {
    label: '환율 기간',
    def: '하나은행 고시 매매기준율(최종 회차)의 직전 36개월 평균. 국가별 통화를 원화로 바꿀 때 이 평균값 하나를 씁니다.',
  },
  priceTier: {
    label: '가격단계',
    def: '자료원이 공개하는 가격의 단계. 미국 WAC(도매 취득가, 출하가에 가까움) · 독일 AVP(약국 판매가) · ex-factory(공장도 출하가) · 스위스 Publikumspreis(소비자가) 등 국가마다 달라 공장도율로 맞춥니다.',
  },
  dailyCost: {
    label: '일일 투약비용',
    def: '조정가 ÷ 최소단위 함량 × 1일 평균 투여량. 괄호 안 용법(예: 200 mg Q3W = 3주마다 200 mg) 가정에 따라 달라지며, 제형마다 허가 용법이 다를 수 있습니다.',
  },
} as const;

export type GlossaryKey = keyof typeof GLOSSARY;

interface TermProps {
  k?: GlossaryKey;
  def?: string;
  children?: ReactNode;
  className?: string;
}

export default function Term({ k, def, children, className = '' }: TermProps) {
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const anchor = useRef<HTMLSpanElement>(null);
  const id = useId();
  const open = pos != null;
  // position:fixed — 카드 그리드의 overflow-hidden 에 잘리지 않게 뷰포트 기준으로 띄운다 (오른쪽 끝은 안쪽으로 당김)
  const show = () => {
    const r = anchor.current?.getBoundingClientRect();
    if (!r) return;
    const W = 288;
    const left = Math.max(8, Math.min(r.left, window.innerWidth - W - 8));
    const below = r.bottom + 6;
    setPos({ top: below + 90 > window.innerHeight ? Math.max(8, r.top - 6 - 96) : below, left });
  };
  const hide = () => setPos(null);
  const entry = k ? GLOSSARY[k] : undefined;
  const text = def ?? entry?.def ?? '';
  const label = children ?? entry?.label ?? '';
  if (!text) return <>{label}</>;
  return (
    <span className={`relative inline-flex items-baseline ${className}`}
      onMouseEnter={show} onMouseLeave={hide}>
      <span ref={anchor} tabIndex={0} aria-describedby={id}
        onFocus={show} onBlur={hide}
        onKeyDown={e => { if (e.key === 'Escape') hide(); }}
        className="cursor-help underline decoration-dotted underline-offset-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] rounded-sm"
        style={{ textDecorationColor: 'var(--label-2)' }}>
        {label}
      </span>
      {/* hidden 이어도 aria-describedby 설명으로는 읽힌다 — 화면에는 hover/focus 때만 */}
      <span id={id} role="tooltip" hidden={!open}
        className="fixed z-[100] w-72 rounded-xl px-3 py-2 text-xs font-normal normal-case tracking-normal leading-relaxed whitespace-normal text-left"
        style={{ top: pos?.top ?? 0, left: pos?.left ?? 0, background: 'var(--surface)', color: 'var(--label)', boxShadow: 'var(--shadow-pop)', border: '1px solid var(--separator)' }}>
        {text}
      </span>
    </span>
  );
}
