/** 최소단위 이름 — 원문 제형·포장 문구 우선. 경구제라고 모두 '정' 이 아니다(과립 포·캡슐).
 *  문구가 없으면 기존 규칙(경구=정, 주사=바이알). 과립 판정을 정제보다 먼저 한다. */
export interface UnitWordInput { formType?: string; dosageForm?: string; packageUnit?: string }

export function unitWordFor(cell: UnitWordInput): string {
  const text = `${cell.dosageForm || ''} ${cell.packageUnit || ''}`.toLowerCase();
  if (cell.formType === 'injection') {
    if (/prefilled|pre-filled|syringe|siringa|seringue|fertigspritze|シリンジ|주사기/.test(text)) return '시린지';
    if (/ampoule|ampul|fiala|アンプル|앰플/.test(text)) return '앰플';
    return '바이알';
  }
  if (/granul|sachet|packet|bustin|beutel|顆粒|分包|과립/.test(text)) return '포';
  if (/capsul|gélule|gelule|kapsel|カプセル|캡슐/.test(text)) return '캡슐';
  if (/tablet|comprim|compress|tablett|錠|정/.test(text)) return '정';
  if (cell.formType === 'oral') return text.trim() ? '단위' : '정';
  return '단위';
}
