import { useCallback, useEffect, useRef, useState } from 'react';

// admin 스테이크홀더 페이지 공용 훅 (컴포넌트 파일과 분리 — react-refresh 규칙).

export interface ToastState {
  msg: string;
  tone: 'ok' | 'error';
}

/** 스케치 003/004 toast() — 3초 남짓 표시 후 사라짐(오류는 조금 더 길게). */
export function useToast(): [ToastState | null, (msg: string, tone?: 'ok' | 'error') => void] {
  const [toast, setToast] = useState<ToastState | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const show = useCallback((msg: string, tone: 'ok' | 'error' = 'ok') => {
    setToast({ msg, tone });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setToast(null), tone === 'error' ? 6000 : 3200);
  }, []);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return [toast, show];
}
