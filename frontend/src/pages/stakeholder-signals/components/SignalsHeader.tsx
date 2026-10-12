import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { SVOverview } from '@/api/stakeholder';
import RelationshipMapModal from './RelationshipMapModal';
import { btn, focusRing } from '../theme/sv';
import { SvHero, SvTopBar } from '../theme/SvPage';

// 타임라인·워크벤치 공용 헤더.
//   timeline  — 스케치 001: 경로(crumb) + 보기 전환·관계도 / 30px 제목·부제 + 우측 스탬프(마지막 sync · 출처 무결성)
//   workbench — 스케치 002: 62px 상단 바(로고 "Policy Signals / Analyst") + 보기 전환·관계도 + 스탬프
// 스케치의 "내보내기"·"+ 행사 업데이트"·"검증 큐" 는 사용자 결정으로 보류 — 버튼을 두지 않는다.

function fmtStamp(s?: string | null): string {
  if (!s) return '—';
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? s : d.toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' });
}

function ViewNav({ view }: { view: 'timeline' | 'workbench' }) {
  const cls = (active: boolean) =>
    `inline-flex items-center gap-1.5 px-3 py-2 text-[13px] font-bold transition-colors ${focusRing} ${
      active ? 'bg-sv-dark text-sv-on-dark' : 'bg-sv-surface text-sv-text hover:text-sv-ink hover:bg-sv-surface-3'
    }`;
  return (
    <nav className="inline-flex rounded-lg shadow-sv-ring overflow-hidden divide-x divide-sv-line" aria-label="보기 전환">
      <Link to="/stakeholder-signals" className={cls(view === 'timeline')} aria-current={view === 'timeline' ? 'page' : undefined}>
        <i className="ri-time-line"></i>타임라인
      </Link>
      <Link
        to="/stakeholder-signals/workbench"
        className={cls(view === 'workbench')}
        aria-current={view === 'workbench' ? 'page' : undefined}
      >
        <i className="ri-layout-column-line"></i>워크벤치
      </Link>
    </nav>
  );
}

function Stamp({ overview, compact = false }: { overview?: SVOverview | null; compact?: boolean }) {
  const integ = overview?.integrity;
  const hasInteg = integ && typeof integ.sources_total === 'number';
  const problems = (integ?.mismatch ?? 0) + (integ?.missing ?? 0);
  return (
    <div className={`text-right leading-relaxed text-sv-muted ${compact ? 'text-[11px]' : 'text-[12px]'}`}>
      <p>
        마지막 sync <span className="text-sv-ink font-semibold">{fmtStamp(integ?.last_sync_at)}</span>
      </p>
      {hasInteg && (
        <p>
          공식·언론 출처 무결성{' '}
          <span className={problems > 0 ? 'text-sv-bad-fg font-bold' : 'text-sv-ok-fg font-bold'}>
            {integ?.sources_verified ?? 0}/{integ?.sources_total ?? 0}
          </span>{' '}
          확인
          {problems > 0 && (
            <span className="ml-1 text-sv-bad-fg font-semibold">
              (불일치 {integ?.mismatch ?? 0} · 누락 {integ?.missing ?? 0})
            </span>
          )}
        </p>
      )}
    </div>
  );
}

export default function SignalsHeader({
  view,
  overview,
}: {
  view: 'timeline' | 'workbench';
  overview?: SVOverview | null;
}) {
  const [showMap, setShowMap] = useState(false);
  const mapBtn = (
    <button onClick={() => setShowMap(true)} className={btn}>
      <i className="ri-share-circle-line"></i>관계도
    </button>
  );

  return (
    <>
      {view === 'workbench' ? (
        <header className="min-h-[62px] flex flex-wrap items-center gap-x-6 gap-y-2 px-4 sm:px-[22px] py-2.5 bg-sv-surface border-b border-sv-line">
          <div className="text-[16px] font-extrabold text-sv-ink whitespace-nowrap">Policy Signals / Analyst</div>
          <div className="flex items-center gap-2">
            <ViewNav view={view} />
            {mapBtn}
          </div>
          <div className="ml-auto hidden md:block">
            <Stamp overview={overview} compact />
          </div>
        </header>
      ) : (
        <>
          <SvTopBar crumb="약가·건강보험 재정 / Intelligence Dashboard">
            <ViewNav view={view} />
            {mapBtn}
          </SvTopBar>
          <SvHero
            title="정책 발언 인텔리전스"
            sub={
              <>
                사람의 말에서 제도 변화의 방향과 실행 시그널을 읽습니다. 복지부·심평원·공단 핵심 인물의 공개 발언을 인용은 원문
                그대로, 요약·해석과 분리해 표시합니다.
              </>
            }
            aside={<Stamp overview={overview} />}
          />
        </>
      )}

      {showMap && <RelationshipMapModal onClose={() => setShowMap(false)} />}
    </>
  );
}
