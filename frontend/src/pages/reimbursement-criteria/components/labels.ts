import type { OncoRow } from '@/api/criteria';

// 급여 기준 화면 — 쉬운 말 라벨·용어 설명(상수/함수만; 컴포넌트는 shared.tsx)

export const GLOSS: Record<string, string> = {
  고식적: '완치보다 증상 조절·생명 연장이 목표인 치료입니다. 주로 진행성·전이성 암에서 씁니다.',
  차수: "'1차'는 첫 치료에서만, '1차 이상'은 첫 치료부터 그 이후 어느 차수에서도 쓸 수 있다는 뜻입니다. '2차 이상'은 이전 치료가 1번 이상 있어야 합니다.",
  병용신약: '새 약을 기존 급여 항암요법에 더해 쓸 때, 기존 요법 부분만 급여(본인일부부담)로 인정하는 요법입니다. 새 약 자체는 이 표로 급여되지 않습니다(다른 표에 급여 기준이 있으면 그쪽이 적용).',
  KCD: '한국표준질병사인분류 — 진료비 청구에 쓰는 질병 코드입니다(현재 9차).',
  고시: '급여 기준을 정한 공식 문서 번호와 시행일입니다. 가장 최근 것이 현재 기준입니다.',
  최신반영: '반기 책자 발간 이후 나온 개정 고시로 내용을 바꿔 최신 기준을 보여줍니다.',
  묶음기준: '이 약만의 단독 기준이 없고, 같은 계열 약을 함께 다루는 기준(예: 당뇨병용제) 안에 포함되어 있습니다.',
};

const SETTING_SHORT: Record<string, string> = {
  neoadjuvant: '수술 전(선행)', perioperative: '수술 전후', adjuvant: '수술 후(보조)', ccrt: '동시항암방사선',
  primary: '일차요법', induction: '관해유도', consolidation: '공고요법', maintenance: '유지요법',
  palliative: '진행·전이(고식적)', salvage: '구제요법', relapsed_refractory: '재발·불응', newly_diagnosed: '새로 진단',
  intravesical: '방광 내 주입', conditioning: '이식 전처치', NA: '', other: '기타',
};
const LINE_SHORT: Record<string, string> = {
  '1L': '1차', '1L+': '1차 이상', '1L_or_1L+': '1차·1차 이상', '2L': '2차', '2L+': '2차 이상', '2L_or_3L': '2차 또는 3차',
  '2L_or_3L+': '2차 또는 3차 이상', '3L': '3차', '3L+': '3차 이상', NA: '',
};
export const settingShort = (c: string) => c.split('|').map(x => SETTING_SHORT[x] ?? x).filter(Boolean).join(' · ');
export const lineShort = (c: string) => c.split('|').map(x => LINE_SHORT[x] ?? x).filter(Boolean).join(' / ');
export const stepLabel = (r: { setting_code: string; line_code: string }) =>
  [settingShort(r.setting_code), lineShort(r.line_code)].filter(Boolean).join(' · ') || '치료 단계 구분 없음';

export const copyRow = (r: OncoRow) =>
  `[${r.cancer} · ${stepLabel(r)}] ${r.regimen}\n${r.is_combo_table ? `추가 약제 조건: ${r.added_condition}\n급여되는 기존 요법: ${r.base_regimen ?? ''}\n` : ''}${r.target}\n(${r.notice ?? ''}, ${r.effective ?? ''} 시행 · 심평원 암환자 약제 공고)`;

