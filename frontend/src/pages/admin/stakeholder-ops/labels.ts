// 모니터링 운영 현황 — 상태 문구·톤(.svb ok/info/warn/neutral/bad, common.css).
// 런 상태·saturation 은 소스 repo 런 JSON 의 값을 그대로 받는다. 모르는 값은 원문 그대로 중립 톤.

export type Tone = 'ok' | 'info' | 'warn' | 'neutral' | 'bad';

const RUN_STATUS: Record<string, [string, Tone]> = {
  completed: ['완료', 'ok'],
  complete: ['완료', 'ok'],
  success: ['완료', 'ok'],
  ok: ['정상', 'ok'],
  no_change: ['변동 없음', 'ok'],
  changed: ['변동 있음', 'info'],
  new_findings: ['신규 발견', 'info'],
  met: ['포화 충족', 'ok'],
  saturated: ['포화 충족', 'ok'],
  not_met: ['포화 미달', 'warn'],
  unsaturated: ['포화 미달', 'warn'],
  partial: ['부분 완료', 'warn'],
  degraded: ['부분 실행', 'warn'],
  failed: ['실패', 'bad'],
  error: ['오류', 'bad'],
};

export function runStatusMeta(v?: string | null): { label: string; tone: Tone; raw: string } {
  const raw = (v || '').trim();
  const hit = RUN_STATUS[raw.toLowerCase()];
  if (hit) return { label: hit[0], tone: hit[1], raw };
  return { label: raw || '—', tone: 'neutral', raw };
}

/** 무결성 상태 순서·문구 — 실패(빨강) 4종과 분모 밖 중립 2종(no_local·excluded)을 나눈다. */
export const CHECKED_STATUSES = ['verified', 'mismatch', 'missing', 'unchecked'] as const;
export const NEUTRAL_STATUSES = ['no_local', 'excluded'] as const;
export const BAD_STATUSES = new Set(['mismatch', 'missing']);

export const INTEGRITY_TILE_LABELS: Record<string, string> = {
  verified: 'SHA-256 일치',
  mismatch: 'SHA-256 불일치',
  missing: '원문 파일 없음',
  unchecked: '미검증',
  no_local: '로컬 원문 없음',
  excluded: '의도적 제외',
};

/** 수동 sync 실패 코드 → 무엇을 하면 되는지(서버 error 문구 앞에 붙인다). */
export const SYNC_ERROR_HINTS: Record<string, string> = {
  SYNC_RUNNING: '다른 sync(정기 잡 또는 다른 관리자)가 진행 중입니다. 끝난 뒤 다시 시도하세요.',
  SYNC_CONFIG: 'sync 설정이 갖춰지지 않았습니다(예: STAKEHOLDER_SYNC_TOKEN 없음). 서버 환경변수를 확인하세요.',
  SYNC_FAILED: 'sync 가 실패했습니다. 기존 데이터는 그대로 유지됩니다.',
  RUNBOOK_MISSING: 'sync 런북·모듈이 서버에 탑재되지 않았습니다.',
};

export const SIGNAL_STATE: Record<string, [string, Tone, string]> = {
  ok: ['정상 반입', 'ok', '최신 policy_signals.json 이 검증을 통과해 반입되었습니다.'],
  invalid: ['검증 실패', 'bad', '최신 시그널 파일이 검증에 실패했습니다. 화면은 마지막 정상본을 보여 줍니다.'],
  absent: ['파일 없음', 'neutral', '소스 repo 에 아직 정책 시그널 파일이 없습니다.'],
};
