import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchMe } from '@/utils/authUsers';
import {
  adminDeletePhoto,
  adminListPeople,
  adminSetStatus,
  adminUploadPhoto,
  fetchPerson,
  svErrorMessage,
  type SVPeopleList,
  type SVPerson,
  type SVPersonDetail,
} from '@/api/stakeholder';
import PersonAvatar from '@/pages/stakeholder-signals/components/PersonAvatar';
import { ConfirmModal, Modal, Toast } from '@/pages/admin/stakeholder-audit/components/ui';
import { boxCard, errorBox, focusRing, panel, thead } from '@/pages/stakeholder-signals/theme/sv';
import { SvHero, SvMain, SvPage, SvTopBar } from '@/pages/stakeholder-signals/theme/SvPage';
import { btnGhost, btnPrimary, chipCls, fmtDateTime, inputCls, useToast } from '@/pages/admin/stakeholder-audit/components/uiHelpers';
import PersonDrawer, { StatusBadge, VerifyTag } from './components/PersonDrawer';
import PersonFormModal, { type SaveOutcome } from './components/PersonFormModal';
import PhotoPicker, { type PhotoIntent } from './components/PhotoPicker';

// 모니터링 인물 관리 (스케치 003, 화이트 톤 .sv-light) — /admin/stakeholder-people
//   목록·등록·편집·일시중지/재개(삭제 없음)·사진 업로드(admin, 서버 WEBP 정규화).
//   검증 인물의 기관·부서·직책은 감사 제안(직책 변경) 승인으로만 바뀐다 — 서버 409/422 문구를 그대로 보여준다.
//   모든 변경은 익일 07:00 KST 모니터링부터 반영.

type StatusFilter = 'all' | 'active' | 'paused';
type Dialog =
  | { kind: 'form'; person: SVPersonDetail | null }
  | { kind: 'status'; person: SVPerson }
  | { kind: 'photo'; person: SVPerson }
  | { kind: 'deletePhoto'; person: SVPerson }
  | null;

function PhotoModal({
  person,
  photoVersion,
  onClose,
  onDone,
}: {
  person: SVPerson;
  photoVersion: number;
  onClose: () => void;
  onDone: (p: SVPersonDetail, msg: string) => void;
}) {
  const [intent, setIntent] = useState<PhotoIntent>({ kind: 'keep' });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    if (intent.kind === 'keep') return;
    setBusy(true);
    setErr(null);
    try {
      const r =
        intent.kind === 'upload'
          ? await adminUploadPhoto(person.person_id, intent.file)
          : await adminDeletePhoto(person.person_id);
      onDone(r, intent.kind === 'upload' ? '사진을 업로드했습니다.' : '사진을 삭제했습니다.');
    } catch (e) {
      setErr(svErrorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`${person.name} — 사진`}
      onClose={onClose}
      busy={busy}
      footer={
        <>
          <button className={btnGhost} onClick={onClose} disabled={busy}>
            취소
          </button>
          <button className={btnPrimary} onClick={save} disabled={busy || intent.kind === 'keep'}>
            {busy ? '저장 중…' : '저장'}
          </button>
        </>
      }
    >
      <PhotoPicker
        personId={person.person_id}
        name={person.name}
        hasPhoto={!!person.has_photo}
        photoVersion={photoVersion}
        value={intent}
        onChange={setIntent}
        disabled={busy}
      />
      {err && (
        <p role="alert" className={`mt-3 ${errorBox}`}>
          {err}
        </p>
      )}
    </Modal>
  );
}

export default function AdminStakeholderPeoplePage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);

  const [list, setList] = useState<SVPeopleList | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<StatusFilter>('all');

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SVPersonDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  // 사진 교체 후 아바타 재마운트(같은 personId·hasPhoto 라도 새 blob 을 받게)
  const [photoVersions, setPhotoVersions] = useState<Record<string, number>>({});
  const [toast, showToast] = useToast();

  // admin 셀프 가드 (service-requests 패턴)
  useEffect(() => {
    (async () => {
      try {
        const me = await fetchMe();
        if (!me || me.role !== 'admin') {
          navigate('/', { replace: true });
          return;
        }
        setAuthChecked(true);
      } catch {
        navigate('/login', { replace: true });
      }
    })();
  }, [navigate]);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setList(await adminListPeople());
    } catch (e) {
      setError(svErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (id: string) => {
    setDetailLoading(true);
    setDetailError(null);
    try {
      setDetail(await fetchPerson(id));
    } catch (e) {
      setDetailError(svErrorMessage(e));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (authChecked) void reload();
  }, [authChecked, reload]);

  const people = useMemo(() => list?.items ?? [], [list]);
  const counts = useMemo(
    () => ({
      all: people.length,
      active: people.filter(p => p.status === 'active').length,
      paused: people.filter(p => p.status === 'paused').length,
    }),
    [people],
  );
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return people.filter(p => {
      if (filter !== 'all' && p.status !== filter) return false;
      if (!q) return true;
      return [p.name, p.organization, p.department, p.role, ...(p.aliases ?? [])].join(' ').toLowerCase().includes(q);
    });
  }, [people, filter, query]);

  const selected = selectedId ? people.find(p => p.person_id === selectedId) ?? null : null;

  const openPerson = (id: string) => {
    setSelectedId(id);
    setDetail(null);
    void loadDetail(id);
  };

  const bumpPhoto = (id: string) => setPhotoVersions(v => ({ ...v, [id]: (v[id] ?? 0) + 1 }));

  const afterChange = async (id: string | null) => {
    await reload();
    if (id && selectedId === id) void loadDetail(id);
  };

  const onSaved = (o: SaveOutcome) => {
    setDialog(null);
    if (o.photoChanged && o.person?.person_id) bumpPhoto(o.person.person_id);
    const when = o.person?.effective_from ? `${o.person.effective_from} 모니터링부터` : '익일부터';
    if (o.photoError) {
      showToast(`정보는 저장됐지만 사진 처리에 실패했습니다 — ${o.photoError}`, 'error');
    } else {
      showToast(`${o.created ? '등록했습니다' : '저장했습니다'}. ${when} 반영됩니다.`);
    }
    void afterChange(o.person?.person_id ?? null);
  };

  if (!authChecked) return <SvPage>{null}</SvPage>;
  const candidates = people.filter(p => p.verification_status !== 'verified').length;

  const gridCols = 'lg:grid-cols-[minmax(230px,1.25fr)_minmax(170px,1fr)_minmax(150px,.8fr)_110px_125px_40px]';

  return (
    <SvPage>
      <SvMain>
        <SvTopBar crumb="약가·건강보험 재정 / 인물 관리">
          <button className={btnGhost} onClick={() => void reload()}>
            <i className="ri-refresh-line"></i>새로고침
          </button>
          <button className={btnPrimary} onClick={() => setDialog({ kind: 'form', person: null })}>
            + 인물 등록
          </button>
        </SvTopBar>

        <SvHero
          title="모니터링 인물 관리"
          sub="활성 인물은 익일부터 약가·건강보험 재정 일일 모니터링에 자동 반영됩니다. 삭제 대신 일시중지로 관리하며, 검증 인물의 직책 변경은 감사 제안 승인으로만 이뤄집니다."
          aside={
            <div className={`${boxCard} px-4 py-[13px] text-[12px] leading-[1.55] text-sv-text min-w-[220px]`}>
              <span>현재 유효 활성 인물</span>
              <b className="block text-[17px] font-extrabold text-sv-ink">
                {typeof list?.active_effective_count === 'number' ? `${list.active_effective_count}명` : '—'}
              </b>
              <span className="block">최근 변경 {fmtDateTime(list?.updated_at)}</span>
              <span className="block text-sv-muted">
                등록 {counts.all} · 활성 {counts.active} · 일시중지 {counts.paused} ·{' '}
                <span className={candidates ? 'text-sv-warn-fg font-semibold' : ''}>후보 {candidates}</span>
              </span>
            </div>
          }
        />

        <section className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-[13px]">
          <div className="relative flex-1 max-w-[420px]">
            <i className="ri-search-line absolute left-3 top-1/2 -translate-y-1/2 text-sv-muted text-sm" aria-hidden="true"></i>
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="이름, 기관, 부서, 직책, 별칭 검색"
              aria-label="인물 검색"
              className={`${inputCls} !pl-9`}
            />
          </div>
          <div className="flex gap-[7px] overflow-x-auto p-0.5" role="group" aria-label="상태 필터">
            {(
              [
                ['all', '전체'],
                ['active', '활성'],
                ['paused', '일시중지'],
              ] as [StatusFilter, string][]
            ).map(([k, label]) => (
              <button key={k} className={chipCls(filter === k)} onClick={() => setFilter(k)} aria-pressed={filter === k}>
                {label}
                <span className="opacity-70">{counts[k]}</span>
              </button>
            ))}
          </div>
        </section>

        <section className={panel} aria-live="polite">
          <div className={`hidden lg:grid ${gridCols} gap-3.5 px-[18px] py-3 ${thead}`}>
            <div>인물</div>
            <div>기관·부서</div>
            <div>직책</div>
            <div>상태</div>
            <div>최근 변경</div>
            <div></div>
          </div>
          {loading && !list ? (
            <div className="animate-pulse">
              {[0, 1, 2].map(i => (
                <div key={i} className="h-[77px] border-t border-sv-line-soft bg-sv-surface-2" />
              ))}
            </div>
          ) : error ? (
            <div className="px-5 py-14 text-center">
              <p className="text-sm font-bold text-sv-bad-fg">인물 목록을 불러오지 못했습니다.</p>
              <p className="mt-1 text-[13px] text-sv-bad-fg">{error}</p>
            </div>
          ) : visible.length === 0 ? (
            <div className="px-5 py-14 text-center">
              <p className="text-sm font-bold text-sv-ink">
                {people.length ? '검색 조건에 맞는 인물이 없습니다.' : '등록된 인물이 없습니다.'}
              </p>
              <p className="mt-1 text-[13px] text-sv-muted">
                {people.length ? '검색어를 지우거나 다른 상태를 선택해 주세요.' : '"인물 등록" 으로 추가하거나 sync 를 기다려 주세요.'}
              </p>
            </div>
          ) : (
            visible.map(p => (
              <div
                key={p.person_id}
                role="button"
                tabIndex={0}
                aria-label={`${p.name} 상세 열기`}
                onClick={() => openPerson(p.person_id)}
                onKeyDown={e => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    openPerson(p.person_id);
                  }
                }}
                className={`grid grid-cols-[minmax(0,1fr)_auto] ${gridCols} gap-3.5 items-center px-[18px] py-[15px] border-t border-sv-line-soft cursor-pointer hover:bg-sv-hover ${focusRing} focus-visible:-outline-offset-2 ${
                  selectedId === p.person_id ? 'bg-sv-selected' : ''
                }`}
              >
                <div className="flex items-center gap-[11px] min-w-0">
                  <PersonAvatar
                    key={`${p.person_id}-${photoVersions[p.person_id] ?? 0}`}
                    personId={p.person_id}
                    name={p.name}
                    hasPhoto={p.has_photo}
                  />
                  <div className="min-w-0">
                    <p className="text-[15px] font-bold text-sv-ink truncate">{p.name}</p>
                    <p className="text-[11px] text-sv-muted truncate">
                      {p.verification_status === 'verified' ? '공식 근거 확인' : '후보 · 공식 직책 확인 필요'}
                    </p>
                    <p className="lg:hidden mt-0.5 text-[12px] text-sv-text truncate">
                      {p.organization} · {p.role}
                    </p>
                  </div>
                </div>
                <div className="hidden lg:block min-w-0">
                  <p className="text-[13px] font-bold text-sv-ink truncate">{p.organization}</p>
                  <p className="text-[11px] text-sv-muted truncate">{p.department}</p>
                </div>
                <div className="hidden lg:block text-[13px] text-sv-ink-2 truncate">{p.role}</div>
                <div>
                  <StatusBadge status={p.status} />
                  <div className="mt-1">
                    <VerifyTag status={p.verification_status} compact />
                  </div>
                </div>
                <div className="hidden lg:block text-[12px] text-sv-ink-2">
                  {fmtDateTime(p.updated_at)}
                  <span className="block text-[11px] text-sv-muted">{p.effective_from ? `${p.effective_from}부터 반영` : ''}</span>
                </div>
                <span className="hidden lg:block text-[21px] leading-none text-sv-muted text-center" aria-hidden="true">
                  ⋯
                </span>
              </div>
            ))
          )}
        </section>
      </SvMain>

      {selected && (
        <PersonDrawer
          base={selected}
          detail={detail?.person_id === selected.person_id ? detail : null}
          loading={detailLoading}
          error={detailError}
          photoVersion={photoVersions[selected.person_id] ?? 0}
          blockEscape={!!dialog}
          onClose={() => setSelectedId(null)}
          onEdit={() =>
            setDialog({
              kind: 'form',
              person: detail?.person_id === selected.person_id ? { ...selected, ...detail } : selected,
            })
          }
          onToggleStatus={() => setDialog({ kind: 'status', person: selected })}
          onPhoto={() => setDialog({ kind: 'photo', person: selected })}
          onDeletePhoto={() => setDialog({ kind: 'deletePhoto', person: selected })}
        />
      )}

      {dialog?.kind === 'form' && (
        <PersonFormModal
          person={dialog.person}
          photoVersion={dialog.person ? photoVersions[dialog.person.person_id] ?? 0 : 0}
          onClose={() => setDialog(null)}
          onSaved={onSaved}
        />
      )}

      {dialog?.kind === 'status' && (
        <ConfirmModal
          title={dialog.person.status === 'active' ? '모니터링 일시중지' : '모니터링 재개'}
          confirmLabel={dialog.person.status === 'active' ? '일시중지' : '재개'}
          tone={dialog.person.status === 'active' ? 'danger' : 'primary'}
          body={
            dialog.person.status === 'active'
              ? `${dialog.person.name}의 신규 모니터링을 익일부터 중단합니다. 기존 수집 데이터는 유지되며 언제든 재개할 수 있습니다.`
              : `${dialog.person.name}을(를) 익일부터 일일 모니터링에 다시 포함합니다.`
          }
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            const next = dialog.person.status === 'active' ? 'paused' : 'active';
            const r = await adminSetStatus(dialog.person.person_id, next);
            setDialog(null);
            const when = r?.effective_from ? `${r.effective_from}부터` : '익일부터';
            showToast(next === 'active' ? `재개했습니다. ${when} 반영됩니다.` : `일시중지했습니다. ${when} 반영됩니다.`);
            void afterChange(dialog.person.person_id);
          }}
        />
      )}

      {dialog?.kind === 'photo' && (
        <PhotoModal
          person={dialog.person}
          photoVersion={photoVersions[dialog.person.person_id] ?? 0}
          onClose={() => setDialog(null)}
          onDone={(_, msg) => {
            const id = dialog.person.person_id;
            setDialog(null);
            bumpPhoto(id);
            showToast(msg);
            void afterChange(id);
          }}
        />
      )}

      {dialog?.kind === 'deletePhoto' && (
        <ConfirmModal
          title="내부 사진 삭제"
          confirmLabel="삭제"
          tone="danger"
          body={`${dialog.person.name}의 내부 사진을 삭제합니다. 이후 이니셜 아바타로 표시됩니다(인물 정보는 유지).`}
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            const id = dialog.person.person_id;
            await adminDeletePhoto(id);
            setDialog(null);
            bumpPhoto(id);
            showToast('사진을 삭제했습니다.');
            void afterChange(id);
          }}
        />
      )}

      <Toast toast={toast} />
    </SvPage>
  );
}
