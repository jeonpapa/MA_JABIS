import {
  GRADE_CLASS_LABELS,
  gradeClassOf,
  gradeLabel,
  type SVGradeClass,
} from '@/api/stakeholder';

// 증거 배지 3종 — 증거등급 / 인용↔원문 대조(locator_match) / 원문 파일 무결성(integrity_status).
// 규칙(stakeholder_verbatim_rules.md §4.2): "원문 SHA-256 일치" 는 integrity_status==='verified' 일 때만,
// locator_match==='none' 은 숨기지 않고 "원문 불일치" 로 드러낸다.

const GRADE_TONE: Record<SVGradeClass, string> = {
  a: 'bg-[#00E5CC]/10 text-[#00E5CC] border-[#00E5CC]/30',
  b: 'bg-[#3B82F6]/10 text-[#60A5FA] border-[#3B82F6]/30',
  c: 'bg-[#F59E0B]/10 text-[#F59E0B] border-[#F59E0B]/30',
  role: 'bg-[#1E2530] text-[#8B9BB4] border-[#2A3545]',
};

const GRADE_DOT: Record<SVGradeClass, string> = {
  a: 'bg-[#00E5CC]',
  b: 'bg-[#60A5FA]',
  c: 'bg-[#F59E0B]',
  role: 'bg-[#8B9BB4]',
};

export function GradeBadge({
  grade,
  gradeClass,
  compact = false,
}: {
  grade?: string | null;
  gradeClass?: string | null;
  compact?: boolean;
}) {
  const cls = gradeClassOf(grade, gradeClass);
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold whitespace-nowrap ${GRADE_TONE[cls]}`}
      title={`증거등급 ${grade || '미표기'} — ${gradeLabel(grade)}`}
    >
      {grade || '등급 미표기'}
      {!compact && grade && <span className="font-normal opacity-80">· {gradeLabel(grade)}</span>}
    </span>
  );
}

const MATCH_META: Record<string, { label: string; tone: string; icon: string; title: string }> = {
  range: {
    label: '원문 위치 일치',
    tone: 'bg-[#00E5CC]/10 text-[#00E5CC]',
    icon: 'ri-checkbox-circle-line',
    title: '인용 문구가 근거 locator 줄 범위 안에서 그대로 확인됨',
  },
  file: {
    label: '원문 내 일치(위치 상이)',
    tone: 'bg-[#3B82F6]/10 text-[#60A5FA]',
    icon: 'ri-file-search-line',
    title: '인용 문구가 원문 파일에는 있으나 locator 줄 범위 밖에서 확인됨',
  },
  none: {
    label: '원문 불일치',
    tone: 'bg-red-500/15 text-red-400 border border-red-500/40',
    icon: 'ri-error-warning-line',
    title: '인용 문구를 근거 원문에서 찾지 못함 — 파서·원문 결함 가능성. 인용 사용 전 원문 확인 필요',
  },
  unchecked: {
    label: '원문 대조 전',
    tone: 'bg-[#1E2530] text-[#8B9BB4]',
    icon: 'ri-time-line',
    title: 'locator 가 없거나 아직 대조하지 않음',
  },
};

export function LocatorMatchBadge({ match }: { match?: string | null }) {
  const m = MATCH_META[(match || 'unchecked').toLowerCase()] ?? MATCH_META.unchecked;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold whitespace-nowrap ${m.tone}`}
      title={m.title}
    >
      <i className={m.icon}></i>
      {m.label}
    </span>
  );
}

const INTEGRITY_META: Record<string, { label: string; tone: string; icon: string; title: string }> = {
  verified: {
    label: '원문 SHA-256 일치',
    tone: 'bg-[#00E5CC]/10 text-[#00E5CC]',
    icon: 'ri-shield-check-line',
    title: 'sync 가 볼륨 원문 파일의 SHA-256 을 재계산해 manifest 값과 일치함을 확인',
  },
  mismatch: {
    label: 'SHA-256 불일치',
    tone: 'bg-red-500/15 text-red-400 border border-red-500/40',
    icon: 'ri-error-warning-line',
    title: '볼륨 원문 파일의 SHA-256 이 manifest 와 다름 — 원문 변조·손상 가능성',
  },
  missing: {
    label: '원문 파일 없음',
    tone: 'bg-red-500/10 text-red-400',
    icon: 'ri-file-warning-line',
    title: 'manifest 에 경로가 있으나 볼륨에 파일이 없음',
  },
  no_local: {
    label: '로컬 원문 없음',
    tone: 'bg-[#1E2530] text-[#8B9BB4]',
    icon: 'ri-link',
    title: '원문 로컬 사본이 없는 출처(예: 영상) — URL 로만 확인 가능',
  },
  unchecked: {
    label: '무결성 미검증',
    tone: 'bg-[#1E2530] text-[#8B9BB4]',
    icon: 'ri-time-line',
    title: '아직 무결성 검사를 하지 않음',
  },
};

export function IntegrityBadge({ status }: { status?: string | null }) {
  const m = INTEGRITY_META[(status || 'unchecked').toLowerCase()] ?? INTEGRITY_META.unchecked;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold whitespace-nowrap ${m.tone}`}
      title={m.title}
    >
      <i className={m.icon}></i>
      {m.label}
    </span>
  );
}

export function GradeLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-[#8B9BB4]" aria-label="증거등급 범례">
      <span className="font-semibold text-[#4A5568]">증거등급</span>
      {(Object.keys(GRADE_CLASS_LABELS) as SVGradeClass[]).map(k => (
        <span key={k} className="inline-flex items-center gap-1.5">
          <i className={`inline-block h-2 w-2 rounded-full ${GRADE_DOT[k]}`}></i>
          {GRADE_CLASS_LABELS[k]}
        </span>
      ))}
    </div>
  );
}
