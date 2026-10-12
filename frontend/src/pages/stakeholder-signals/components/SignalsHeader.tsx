import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { SVOverview } from '@/api/stakeholder';
import RelationshipMapModal from './RelationshipMapModal';

// 타임라인·워크벤치 공용 헤더: 제목 / 부제 / 우측 스탬프(마지막 sync · 출처 무결성) / 뷰 탭 / 관계도 버튼.

function fmtStamp(s?: string | null): string {
  if (!s) return '—';
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? s : d.toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' });
}

export default function SignalsHeader({
  view,
  overview,
}: {
  view: 'timeline' | 'workbench';
  overview?: SVOverview | null;
}) {
  const [showMap, setShowMap] = useState(false);
  const integ = overview?.integrity;
  const hasInteg = integ && typeof integ.sources_total === 'number';
  const problems = (integ?.mismatch ?? 0) + (integ?.missing ?? 0);

  const tabCls = (active: boolean) =>
    `inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors ${
      active ? 'bg-[#00E5CC]/10 text-[#00E5CC]' : 'text-[#8B9BB4] hover:text-white hover:bg-[#1E2530]'
    }`;

  return (
    <div className="px-8 pt-7 pb-5 border-b border-[#1E2530]">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="w-5 h-5 flex items-center justify-center">
              <i className="ri-user-voice-line text-[#00E5CC]"></i>
            </span>
            <h1 className="text-2xl font-bold text-white">정책 발언 인텔리전스</h1>
          </div>
          <p className="text-[#8B9BB4] text-sm max-w-3xl">
            복지부·심평원·공단 핵심 인물의 공개 발언에서 약가·건보재정 제도 변화의 방향과 실행 시그널을 읽습니다.
            인용은 원문 그대로, 요약·해석과 분리해 표시합니다.
          </p>
        </div>
        <div className="text-right text-[11px] leading-relaxed text-[#8B9BB4]">
          <p>
            마지막 sync <span className="text-white">{fmtStamp(integ?.last_sync_at)}</span>
          </p>
          {hasInteg && (
            <p>
              출처 무결성{' '}
              <span className={problems > 0 ? 'text-red-400 font-semibold' : 'text-[#00E5CC] font-semibold'}>
                {integ?.sources_verified ?? 0}/{integ?.sources_total ?? 0}
              </span>{' '}
              확인
              {problems > 0 && (
                <span className="ml-1 text-red-400">
                  (불일치 {integ?.mismatch ?? 0} · 누락 {integ?.missing ?? 0})
                </span>
              )}
            </p>
          )}
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <nav className="flex items-center gap-1 rounded-xl bg-[#161B27] border border-[#1E2530] p-1" aria-label="보기 전환">
          <Link to="/stakeholder-signals" className={tabCls(view === 'timeline')} aria-current={view === 'timeline' ? 'page' : undefined}>
            <i className="ri-time-line"></i>타임라인
          </Link>
          <Link
            to="/stakeholder-signals/workbench"
            className={tabCls(view === 'workbench')}
            aria-current={view === 'workbench' ? 'page' : undefined}
          >
            <i className="ri-layout-column-line"></i>워크벤치
          </Link>
        </nav>
        <button
          onClick={() => setShowMap(true)}
          className="inline-flex items-center gap-1.5 rounded-xl border border-[#1E2530] bg-[#161B27] px-3 py-2 text-xs font-semibold text-[#8B9BB4] hover:text-white cursor-pointer"
        >
          <i className="ri-share-circle-line"></i>관계도
        </button>
      </div>

      {showMap && <RelationshipMapModal onClose={() => setShowMap(false)} />}
    </div>
  );
}
