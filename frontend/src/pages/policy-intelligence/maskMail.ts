// 메일 원문 표시 전용 마스킹 — 외부 서명(이름·이메일·전화번호)을 화면에서만 가린다. 저장 데이터는 그대로.
//   · 전화/팩스 번호: 전체 본문에서 가림 (날짜 2026-10-17 등은 건드리지 않음)
//   · 이메일: 로컬파트를 가리고 도메인은 남김 (기관 식별용) — h***@krpia.or.kr
//   · 이름: 서명 영역(맺음말 이후 · 연락처 라벨 줄)과 From/보낸 사람 헤더 줄에서만 — 본문 속 공개 발언자명은 그대로

const PHONE_RE = /(?<![\d-])(?:\+?82[-.\s]?\(?0?\)?|0)\d{1,2}[-.\s)]{1,2}\d{3,4}[-.\s]\d{4}(?![\d-])/g;
const PHONE_SHORT_RE = /(?<![\d-])1[5-9]\d{2}-\d{4}(?![\d-])/g; // 대표번호 1588-0000
const EMAIL_RE = /([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)/g;
const TITLE_WORDS =
  '과장|대리|차장|부장|팀장|사원|주임|매니저|이사|상무|전무|대표|실장|선임|책임|수석|연구원|위원|간사|국장|본부장|드림|올림|배상';
const NAME_WITH_TITLE_RE = new RegExp(`([가-힣]{2,4})(\\s*)(${TITLE_WORDS})`, 'g');
const SIGNOFF_RE = /^\s*(?:--\s*$|-{2,}\s*$|_{3,}|감사합니다|고맙습니다|수고하세요|best regards|kind regards|regards|thanks|thank you|sincerely)/i;
const CONTACT_LABEL_RE = /^\s*(?:tel|t|phone|mobile|m|hp|h\.p|fax|f|e-?mail|e|direct|office|휴대폰|휴대전화|핸드폰|전화|직통|팩스|이메일|메일)\s*[.:)]/i;
const HEADER_RE = /^(\s*>*\s*(?:from|to|cc|보낸\s*사람|받는\s*사람|참조|발신|수신)\s*:\s*)(.+)$/i;

export const MASK = '○○○';

function maskEmails(s: string): string {
  return s.replace(EMAIL_RE, (_m, first: string, domain: string) => `${first}***@${domain}`);
}

function maskPhones(s: string): string {
  return s.replace(PHONE_RE, '***-****-****').replace(PHONE_SHORT_RE, '****-****');
}

function maskNames(s: string): string {
  return s.replace(NAME_WITH_TITLE_RE, (_m, _name: string, sp: string, title: string) => `${MASK}${sp}${title}`);
}

/** 헤더 표시명('"홍길동" <hong@x.or.kr>', '홍길동 <...>') → 표시명 가림 */
function maskHeaderValue(v: string): string {
  return maskEmails(v.replace(/^\s*"?([^"<]+?)"?\s*(?=<)/, (m, name: string) => (name.trim() ? `${MASK} ` : m)));
}

export function maskMailText(text: string): { text: string; masked: boolean } {
  if (!text) return { text, masked: false };
  const lines = text.split('\n');
  // 서명 시작 = 끝에서 25줄 이내의 첫 맺음말 줄 (없으면 연락처 라벨 줄만 처리)
  const tailFrom = Math.max(0, lines.length - 25);
  let sigStart = -1;
  for (let i = tailFrom; i < lines.length; i++) {
    if (SIGNOFF_RE.test(lines[i])) {
      sigStart = i;
      break;
    }
  }
  const out = lines.map((line, i) => {
    const header = line.match(HEADER_RE);
    if (header) return header[1] + maskHeaderValue(header[2]);
    let l = maskPhones(maskEmails(line));
    if ((sigStart >= 0 && i >= sigStart) || CONTACT_LABEL_RE.test(line)) {
      l = maskNames(l);
      // 서명 영역의 '이름 단독 줄'(2~4자 한글만) 도 가림
      if (sigStart >= 0 && i > sigStart && /^\s*[가-힣]{2,4}\s*$/.test(l)) l = l.replace(/[가-힣]{2,4}/, MASK);
    }
    return l;
  });
  const result = out.join('\n');
  return { text: result, masked: result !== text };
}

/** 헤더(발신자) 한 줄 — 이메일 로컬파트만 가림 */
export function maskSender(from: string): string {
  return maskEmails(from);
}
