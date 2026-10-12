import { useCallback, useEffect, useRef, useState } from 'react';
import { btn, btnDanger as svBtnDanger, btnDark, input, pill } from '@/pages/stakeholder-signals/theme/sv';

// admin 스테이크홀더 페이지 공용 순수 헬퍼·훅 (컴포넌트 파일과 분리 — react-refresh 규칙).

export function fmtDateTime(s?: string | null): string {
  if (!s) return '—';
  const d = new Date(s);
  return Number.isNaN(d.getTime())
    ? s
    : d.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', dateStyle: 'medium', timeStyle: 'short' });
}

export function fmtDay(s?: string | null): string {
  if (!s) return '—';
  return s.slice(0, 10);
}

// admin 스테이크홀더 버튼·입력 별칭 — 스케치 003/004 .btn.primary(검정) / .btn / .btn.danger / .field input
export const btnPrimary = btnDark;
export const btnGhost = btn;
export const btnDanger = svBtnDanger;
export const inputCls = input;

/** 스케치 003/004 .chip — 알약형, 활성은 검정 */
export function chipCls(active: boolean): string {
  return pill(active);
}

export interface ToastState {
  msg: string;
  tone: 'ok' | 'error';
}

export function useToast(): [ToastState | null, (msg: string, tone?: 'ok' | 'error') => void] {
  const [toast, setToast] = useState<ToastState | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const show = useCallback((msg: string, tone: 'ok' | 'error' = 'ok') => {
    setToast({ msg, tone });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setToast(null), tone === 'error' ? 6000 : 3500);
  }, []);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return [toast, show];
}

