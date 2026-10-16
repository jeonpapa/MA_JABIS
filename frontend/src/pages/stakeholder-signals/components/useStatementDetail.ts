import { useEffect, useState } from 'react';
import { fetchStatement, type SVStatementDetail } from '@/api/stakeholder';

/** 발언 상세 로드 — 001 드로어·002 상세 패널 공용. */
export function useStatementDetail(statementId: string) {
  const [detail, setDetail] = useState<SVStatementDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setDetail(null);
    fetchStatement(statementId)
      .then(d => {
        if (!cancelled) setDetail(d);
      })
      .catch(() => {
        if (!cancelled) setError('발언 상세를 불러오지 못했습니다.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [statementId, tick]);
  return { detail, loading, error, retry: () => setTick(t => t + 1) };
}
