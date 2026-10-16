import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { fetchMe } from '@/utils/authUsers';
import { adminListPeople, adminSetStatus, svErrorMessage, type SVPeopleList, type SVPerson } from '@/api/stakeholder';
import PersonAvatar from '@/pages/stakeholder-signals/components/PersonAvatar';
import { ConfirmModal, Toast } from '@/pages/admin/stakeholder-audit/components/ui';
import { useToast } from '@/pages/admin/stakeholder-audit/components/uiHelpers';
import { fmtKoDate } from '@/pages/stakeholder-signals/sketch/helpers';
import PersonDrawer from './components/PersonDrawer';
import PersonFormModal, { type SaveOutcome } from './components/PersonFormModal';
import '@/pages/stakeholder-signals/sketch/base.css';
import '@/pages/stakeholder-signals/sketch/common.css';
import '@/pages/stakeholder-signals/sketch/s003.css';
import '@/pages/stakeholder-signals/sketch/s003.additions.css';

// 모니터링 인물 관리 = 스케치 003(main 영역) 그대로 — /admin/stakeholder-people (admin 전용)
//   경로 + "+ 인물 등록" / 제목·부제 + 활성 인물 상태 카드 / 검색 + 상태 칩 / 인물 표 / 상세 드로어 / 등록·수정 모달 / 토스트.
//   스케치의 운영자 키(prompt) 대신 대쉬보드 admin 인증. 상태 전환 확인은 confirm() 대신 같은 시각 언어의 확인 모달.
//   검증 인물의 기관·부서·직책은 감사 제안(직책 변경) 승인으로만 바뀐다 — 서버 409/422 문구를 그대로 보여준다.

type StatusFilter = 'all' | 'active' | 'paused';

export default function AdminStakeholderPeoplePage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);
  const [list, setList] = useState<SVPeopleList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<StatusFilter>('all');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<{ person: SVPerson | null } | null>(null);
  const [confirm, setConfirm] = useState<SVPerson | null>(null);
  const [photoVersions, setPhotoVersions] = useState<Record<string, number>>({});
  const [toast, showToast] = useToast();

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

  const load = useCallback(async () => {
    try {
      setList(await adminListPeople());
      setError(null);
    } catch (e) {
      setError(svErrorMessage(e));
    }
  }, []);

  useEffect(() => {
    if (authChecked) void load();
  }, [authChecked, load]);

  // 스케치 API 정렬: 활성 먼저 → 기관 → 이름
  const people = useMemo(
    () =>
      [...(list?.items ?? [])].sort(
        (a, b) =>
          Number(a.status !== 'active') - Number(b.status !== 'active') ||
          (a.organization ?? '').localeCompare(b.organization ?? '') ||
          a.name.localeCompare(b.name),
      ),
    [list],
  );
  const visible = useMemo(() => {
    const q = query.toLowerCase();
    return people.filter(
      p =>
        (filter === 'all' || p.status === filter) &&
        [p.name, p.organization, p.department, p.role, ...(p.aliases ?? [])].join(' ').toLowerCase().includes(q),
    );
  }, [people, filter, query]);

  const selected = selectedId ? people.find(p => p.person_id === selectedId) ?? null : null;
  const closeDrawer = useCallback(() => setSelectedId(null), []);

  const onSaved = (o: SaveOutcome) => {
    setForm(null);
    const id = o.person?.person_id;
    if (o.photoChanged && id) setPhotoVersions(v => ({ ...v, [id]: (v[id] ?? 0) + 1 }));
    if (o.photoError) showToast(`정보는 저장됐지만 사진 처리에 실패했습니다 — ${o.photoError}`, 'error');
    else showToast(`저장되었습니다. ${o.person?.effective_from ?? '익일'} 모니터링부터 반영됩니다.`);
    void load();
  };

  if (!authChecked) return <div className="sxbase sx3" />;

  return (
    <div className="sxbase sx3">
      <div className="main">
        <div className="top">
          <div className="crumb">약가·건강보험 재정 / 인물 관리</div>
          <button className="btn primary" onClick={() => setForm({ person: null })}>
            + 인물 등록
          </button>
        </div>
        <section className="hero">
          <div>
            <h1>모니터링 인물 관리</h1>
            <p>활성 인물은 익일부터 약가·건강보험 재정 일일 모니터링에 자동 반영됩니다.</p>
          </div>
          <div className="status-card">
            <span>현재 유효 활성 인물</span>
            <b>{typeof list?.active_effective_count === 'number' ? `${list.active_effective_count}명` : '—'}</b>
            <span>{list ? `최근 변경 ${fmtKoDate(list.updated_at)}` : '레지스트리 불러오는 중'}</span>
          </div>
        </section>
        <section className="toolbar">
          <div className="search">
            <span>⌕</span>
            <input aria-label="인물 검색" placeholder="이름, 기관, 부서, 직책 검색" value={query} onChange={e => setQuery(e.target.value)} />
          </div>
          <div className="chips" role="group" aria-label="상태 필터">
            {(
              [
                ['all', '전체'],
                ['active', '활성'],
                ['paused', '일시중지'],
              ] as [StatusFilter, string][]
            ).map(([k, label]) => (
              <button key={k} className={`chip${filter === k ? ' active' : ''}`} onClick={() => setFilter(k)}>
                {label}
              </button>
            ))}
          </div>
        </section>
        <section className="panel" aria-live="polite">
          <div className="thead">
            <div>인물</div>
            <div>기관·부서</div>
            <div>직책</div>
            <div>상태</div>
            <div>최근 변경</div>
            <div></div>
          </div>
          <div>
            {error ? (
              <div className="empty">
                <b>인물 목록을 불러오지 못했습니다.</b>
                <p>{error}</p>
              </div>
            ) : !list ? (
              <>
                <div className="skeleton"></div>
                <div className="skeleton"></div>
                <div className="skeleton"></div>
              </>
            ) : visible.length === 0 ? (
              <div className="empty">
                <b>검색 조건에 맞는 인물이 없습니다.</b>
                <p>검색어를 지우거나 다른 상태를 선택해 주세요.</p>
              </div>
            ) : (
              visible.map(p => {
                const open = () => setSelectedId(p.person_id);
                return (
                  <article
                    key={p.person_id}
                    className="person-row"
                    tabIndex={0}
                    role="button"
                    aria-label={`${p.name} 상세 열기`}
                    onClick={open}
                    onKeyDown={e => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        open();
                      }
                    }}
                  >
                    <div className="identity">
                      <PersonAvatar key={`${p.person_id}-${photoVersions[p.person_id] ?? 0}`} personId={p.person_id} name={p.name} hasPhoto={p.has_photo} />
                      <span>
                        <b>{p.name}</b>
                        <small>{p.verification_status === 'verified' ? '공식 근거 확인' : '후보 · 공식 직책 확인 필요'}</small>
                      </span>
                    </div>
                    <div className="org">
                      <b>{p.organization}</b>
                      <div className="muted">{p.department}</div>
                    </div>
                    <div className="role">{p.role}</div>
                    <div className="status-col">
                      <span className={`badge ${p.status}`}>{p.status === 'active' ? '● 활성' : '일시중지'}</span>
                      <div className={`verify${p.verification_status === 'candidate' ? ' candidate' : ''}`}>
                        {p.verification_status === 'verified' ? '검증됨' : '후보 단계'}
                      </div>
                    </div>
                    <div className="updated">
                      <span>{fmtKoDate(p.updated_at)}</span>
                      <div className="muted">{p.effective_from}부터 반영</div>
                    </div>
                    <span className="more" aria-hidden="true">
                      ⋯
                    </span>
                  </article>
                );
              })
            )}
          </div>
        </section>
      </div>

      <PersonDrawer
        person={selected}
        photoVersion={selected ? photoVersions[selected.person_id] ?? 0 : 0}
        blockEscape={!!form || !!confirm}
        onClose={closeDrawer}
        onEdit={() => {
          setForm({ person: selected });
          setSelectedId(null);
        }}
        onToggleStatus={() => setConfirm(selected)}
      />

      {form && (
        <PersonFormModal
          person={form.person}
          photoVersion={form.person ? photoVersions[form.person.person_id] ?? 0 : 0}
          onClose={() => setForm(null)}
          onSaved={onSaved}
        />
      )}

      {confirm && (
        <ConfirmModal
          title={confirm.status === 'active' ? '모니터링 일시중지' : '모니터링 활성화'}
          confirmLabel={confirm.status === 'active' ? '일시중지' : '활성화'}
          tone={confirm.status === 'active' ? 'danger' : 'primary'}
          body={
            confirm.status === 'active'
              ? '익일부터 신규 모니터링을 중단할까요? 기존 데이터는 유지됩니다.'
              : '익일부터 일일 모니터링에 다시 포함할까요?'
          }
          onClose={() => setConfirm(null)}
          onConfirm={async () => {
            const next = confirm.status === 'active' ? 'paused' : 'active';
            await adminSetStatus(confirm.person_id, next);
            setConfirm(null);
            setSelectedId(null);
            await load();
            showToast(next === 'active' ? '활성화했습니다. 익일부터 반영됩니다.' : '일시중지했습니다. 익일부터 반영됩니다.');
          }}
        />
      )}

      <Toast toast={toast} />
    </div>
  );
}
