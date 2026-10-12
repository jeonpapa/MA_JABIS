import { useCallback, useEffect, useRef, useState } from 'react';

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

export function chipCls(active: boolean): string {
  return `inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold whitespace-nowrap cursor-pointer transition-colors ${
    active
      ? 'bg-[#00E5CC]/10 text-[#00E5CC] border border-[#00E5CC]/30'
      : 'border border-[#1E2530] bg-[#161B27] text-[#8B9BB4] hover:text-white'
  }`;
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

