import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { USER_KEY } from '@/api/client';
import { downloadPriceRequest, fetchPriceCatalog, fetchPriceRequest, fetchPriceRequests, fetchPriceWorkerStatus, submitPriceRequest } from '@/api/priceRequests';
import type { PriceCatalog, PriceIdentity, PriceRequest, PriceScope, PriceWorkerStatus } from '@/api/priceRequests';

interface RequestTarget { query: string; identity?: PriceIdentity }
export interface PriceRequestPanelHandle { submit: (countries?: string[], target?: RequestTarget) => void }
interface Props {
  scope: PriceScope; query?: string; identity?: PriceIdentity; isDark?: boolean; targetRequestId?: string;
  onResultsChanged?: (request: PriceRequest) => void;
}
const validEmail = (value: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) && value.toLowerCase() !== 'admin@marketintel.kr';
const date = (value?: string | null) => value ? new Date(value).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) : '아직 없음';
const statusLabel: Record<string, string> = { queued: '접수 대기', processing: '국가별 조사 중', awaiting_verification: '대시보드 검증 대기', verified: '대시보드 검증됨 (전 국가 가격 확보 의미 아님)', completed: '완료 · 전체 가격 확인 및 이메일 발송', partial_completed: '부분 완료 · 미확정 국가 포함, 이메일 발송', blocked: '진행 차단 · 확인 필요' };
const countryStatus: Record<string, string> = { pending: '대기', priced: '가격 확인', unconfirmed: '가격 미확정', failed: '수집 실패' };
const notificationLabel: Record<string, string> = { pending: '발송 대기', sending: '발송 중', sent: '발송됨', uncertain: '발송 여부 미확정 · 중복 발송 주의' };
const refreshStatuses = new Set(['verified', 'completed', 'partial_completed', 'blocked']);
const timeOnly = (value?: string | null) => value ? new Date(value).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';

/** 접수 ≠ 처리. 워커(Aside 루틴)가 최근 큐를 확인했는지 그대로 보여준다. */
function WorkerNotice({ worker, error }: { worker: PriceWorkerStatus | null; error: string }) {
  if (error) return <div className="ui-notice ui-notice-info" role="status"><span className="ui-dot mt-1.5" style={{ background: 'var(--label-3)' }} />자동 조사 상태를 확인하지 못했습니다. 요청은 저장되지만 처리 시작 시점은 확인할 수 없습니다.</div>;
  if (!worker) return null;
  if (worker.state === 'active') return (
    <div className="ui-notice ui-notice-ok" role="status"><span className="ui-dot mt-1.5" style={{ background: 'var(--green-solid)' }} />
      <span><b>자동 조사 작동 중</b> · 마지막 큐 확인 {timeOnly(worker.last_seen_at)}. 약 10분 간격으로 요청을 가져가며, 완료 시간은 국가 수와 사이트 상태에 따라 달라집니다.</span></div>);
  return (
    <div className="ui-notice ui-notice-warn" role="status"><span className="ui-dot mt-1.5" style={{ background: 'var(--orange-solid)' }} />
      <span><b>자동 조사가 현재 멈춰 있습니다.</b> {worker.state === 'never' ? '조사 담당 워커가 아직 연결되지 않았습니다.' : `마지막 큐 확인 ${timeOnly(worker.last_seen_at)}.`} 요청은 저장되며, 처리가 재개되면 접수 순서대로 조사합니다{worker.open_requests > 0 ? ` (현재 대기 ${worker.open_requests}건)` : ''}. 급한 건은 관리자에게 알려 주세요.</span></div>);
}

const PriceRequestPanel = forwardRef<PriceRequestPanelHandle, Props>(function PriceRequestPanel({ scope, query = '', identity, isDark = false, targetRequestId = '', onResultsChanged }, ref) {
  const [queryText, setQueryText] = useState(query);
  const [email, setEmail] = useState(() => {
    try { const value = localStorage.getItem(USER_KEY)?.trim() || ''; return validEmail(value) ? value : ''; } catch { return ''; }
  });
  const [catalog, setCatalog] = useState<PriceCatalog | null>(null);
  const [catalogError, setCatalogError] = useState('');
  const [countries, setCountries] = useState<string[]>([]);
  const [requests, setRequests] = useState<PriceRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [downloadBusy, setDownloadBusy] = useState<Record<string, boolean>>({});
  const [downloadErrors, setDownloadErrors] = useState<Record<string, string>>({});
  const downloadingRef = useRef(new Set<string>());
  const [targetError, setTargetError] = useState('');
  const targetRef = useRef<HTMLElement>(null);
  const scrolledTargetRef = useRef('');
  const [submitError, setSubmitError] = useState('');
  const [success, setSuccess] = useState('');
  const [busy, setBusy] = useState(false);
  const [worker, setWorker] = useState<PriceWorkerStatus | null>(null);
  const [workerError, setWorkerError] = useState('');
  const busyRef = useRef(false);
  const panelRef = useRef<HTMLElement>(null);
  const callbackRef = useRef(onResultsChanged);
  const seenRef = useRef(new Map<string, string>());
  callbackRef.current = onResultsChanged;
  useEffect(() => { setQueryText(query); }, [query]);
  const loadCatalog = useCallback(async () => {
    setCatalogError('');
    try { setCatalog(await fetchPriceCatalog()); }
    catch (error) { setCatalogError(error instanceof Error ? error.message : String(error)); }
  }, []);
  useEffect(() => { void loadCatalog(); }, [loadCatalog]);
  useEffect(() => { setCountries(scope === 'a8' ? (catalog?.a8 ?? []).map(c => c.code) : []); }, [scope, catalog]);
  const acceptRequests = useCallback((items: PriceRequest[]) => {
    for (const item of items) {
      const signature = JSON.stringify([item.status, item.updated_at, item.verified_at, item.country_results]);
      if (refreshStatuses.has(item.status) && seenRef.current.get(item.id) !== signature) callbackRef.current?.(item);
      seenRef.current.set(item.id, signature);
    }
    setRequests(items);
  }, []);
  useEffect(() => {
    let active = true;
    let polling = false;
    setRequests([]); setLoading(true); setListError(''); setTargetError('');
    const poll = async () => {
      if (polling) return;
      polling = true;
      try {
        const [recent, target, workerState] = await Promise.allSettled([
          fetchPriceRequests(scope),
          targetRequestId ? fetchPriceRequest(targetRequestId) : Promise.resolve(null),
          fetchPriceWorkerStatus(),
        ]);
        if (!active) return;
        if (workerState.status === 'fulfilled') { setWorker(workerState.value); setWorkerError(''); }
        else setWorkerError(workerState.reason instanceof Error ? workerState.reason.message : String(workerState.reason));
        let items: PriceRequest[] = recent.status === 'fulfilled' ? recent.value : [];
        setListError(recent.status === 'rejected' ? (recent.reason instanceof Error ? recent.reason.message : String(recent.reason)) : '');
        if (target.status === 'rejected') {
          setTargetError('링크의 요청을 불러오지 못했습니다: ' + (target.reason instanceof Error ? target.reason.message : String(target.reason)));
        } else if (target.value && target.value.scope !== scope) {
          setTargetError('링크의 요청은 다른 국가 범위에 있습니다. 해당 탭을 선택해 주세요.');
        } else {
          setTargetError('');
          if (target.value) items = [target.value, ...items.filter(r => r.id !== target.value.id)];
        }
        if (recent.status === 'fulfilled' || (target.status === 'fulfilled' && target.value?.scope === scope)) acceptRequests(items);
      }
      catch (error) { if (active) setListError(error instanceof Error ? error.message : String(error)); }
      finally { polling = false; if (active) setLoading(false); }
    };
    void poll();
    const timer = window.setInterval(() => { void poll(); }, 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, [scope, targetRequestId, acceptRequests]);
  useEffect(() => {
    const key = scope + ':' + targetRequestId;
    if (targetRequestId && requests.some(r => r.id === targetRequestId) && targetRef.current && scrolledTargetRef.current !== key) {
      scrolledTargetRef.current = key;
      targetRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [requests, scope, targetRequestId]);
  const downloadResult = async (id: string) => {
    if (downloadingRef.current.has(id)) return;
    downloadingRef.current.add(id);
    setDownloadBusy(prev => ({ ...prev, [id]: true }));
    setDownloadErrors(prev => ({ ...prev, [id]: '' }));
    try { await downloadPriceRequest(id); }
    catch (error) { setDownloadErrors(prev => ({ ...prev, [id]: error instanceof Error ? error.message : String(error) })); }
    finally {
      downloadingRef.current.delete(id);
      setDownloadBusy(prev => ({ ...prev, [id]: false }));
    }
  };
  const enqueue = async (override?: string[], target?: RequestTarget) => {
    if (busyRef.current) return;
    panelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setSubmitError(''); setSuccess('');
    const selectedCountries = override ?? countries;
    const effectiveQuery = target?.query ?? queryText;
    const effectiveIdentity = target ? target.identity : (queryText.trim() === query.trim() ? identity : undefined);
    if (target) setQueryText(target.query);
    if (!effectiveQuery.trim()) { setSubmitError('제품명 또는 성분명을 입력하세요.'); return; }
    if (effectiveQuery.trim().length > 160) { setSubmitError('제품명 또는 성분명은 160자 이내로 입력하세요.'); return; }
    if (!validEmail(email.trim())) { setSubmitError('실제 수신 이메일을 명시해 주세요. 기본 관리자 주소는 사용할 수 없습니다.'); return; }
    if (!catalog || selectedCountries.length === 0) { setSubmitError('국가 목록을 확인하고 조사할 국가를 선택하세요.'); return; }
    const allowed = new Set(catalog[scope].map(c => c.code));
    if (selectedCountries.some(c => !allowed.has(c))) { setSubmitError('이 탭의 국가 목록에서 선택해 주세요.'); return; }
    busyRef.current = true; setBusy(true);
    try {
      const request = await submitPriceRequest({ query: effectiveQuery.trim(), scope, countries: selectedCountries, notify_email: email.trim(), ...(effectiveIdentity ? { identity: effectiveIdentity } : {}) });
      setRequests(prev => [request, ...prev.filter(r => r.id !== request.id)]);
      setSuccess(worker?.state === 'active' ? '요청이 접수되었습니다. 조사와 대시보드 검증을 마치면 지정한 이메일로 결과를 알립니다.' : '요청이 저장되었습니다. 자동 조사가 멈춰 있어 재개된 뒤 처리됩니다. 완료되면 지정한 이메일로 알립니다.');
    } catch (error) { setSubmitError(error instanceof Error ? error.message : String(error)); }
    finally { busyRef.current = false; setBusy(false); }
  };
  useImperativeHandle(ref, () => ({ submit: (override, target) => { void enqueue(override, target); } }));
  const shell = isDark ? 'bg-[#161B27] border-[#1E2530] text-gray-100' : 'ui-card';
  const input = 'ui-field';
  const labels = new Map((catalog?.[scope] ?? []).map(c => [c.code, c.label]));
  return (
    <section ref={panelRef} className={'p-6 space-y-4 ' + shell} aria-label="해외약가 조사 요청">
      <div className="space-y-3"><div><p className="ui-eyebrow">조사 요청</p><h2 className="text-[19px] font-semibold tracking-tight mt-0.5">{scope === 'a8' ? 'A8 8개국' : 'OECD·공단 추가국가'} 약가 조사</h2></div>
        <WorkerNotice worker={worker} error={workerError} />
        <p className="text-xs" style={{ color: 'var(--label-2)' }}>조사 후 대시보드 검증을 거쳐 이메일로 알립니다. 상태는 30초마다 갱신됩니다. 가격 미확정·수집 실패는 미허가를 뜻하지 않으며, 0은 유효한 가격으로 표시하지 않습니다.</p>
        {scope === 'oecd' && <p className="text-xs opacity-70 mt-1">{catalog?.note || '공단 기준 A8 외 29개국으로 대만·싱가포르 등 OECD 비회원국도 포함합니다.'}</p>}
      </div>
      <form onSubmit={e => { e.preventDefault(); void enqueue(); }} className="space-y-3">
        <div className="grid gap-3 md:grid-cols-2">
          <label className="text-xs space-y-1.5 block"><span className="font-medium" style={{ color: 'var(--label-2)' }}>제품명 / 성분명</span><input required maxLength={160} value={queryText} onChange={e => setQueryText(e.target.value)} placeholder="예: Keytruda / pembrolizumab" className={input} /></label>
          <label className="text-xs space-y-1.5 block"><span className="font-medium" style={{ color: 'var(--label-2)' }}>결과 수신 이메일 (필수)</span><input type="email" required value={email} onChange={e => setEmail(e.target.value)} placeholder="실제 수신 이메일을 입력하세요" className={input} /></label>
        </div>
        {catalogError ? <p role="alert" className="text-sm text-red-500">국가 목록 로딩 실패: {catalogError} <button type="button" onClick={() => void loadCatalog()} className="underline">다시 시도</button></p>
          : !catalog ? <p className="text-xs opacity-70">국가 목록 로딩 중…</p>
          : scope === 'a8' ? <p className="text-xs">기본 요청: {catalog.a8.map(c => c.label).join(' · ')} (전체 {catalog.a8.length}개국)</p>
          : <fieldset className="rounded-xl p-3" style={{ background: 'var(--surface-inset)' }}><legend className="text-xs px-1">조사 국가 선택 ({countries.length}개국)</legend>
            <div className="flex gap-3 mb-2 text-xs"><button type="button" onClick={() => setCountries(catalog.oecd.map(c => c.code))} className="underline">전체 선택</button><button type="button" onClick={() => setCountries([])} className="underline">선택 해제</button></div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">{catalog.oecd.map(c => <label key={c.code} className="flex gap-2 text-xs items-center"><input type="checkbox" checked={countries.includes(c.code)} onChange={e => setCountries(prev => e.target.checked ? [...prev, c.code] : prev.filter(code => code !== c.code))} />{c.label} ({c.currency})</label>)}</div>
          </fieldset>}
        <button type="submit" disabled={busy || !catalog} className="ui-button">{busy ? '접수 중…' : '조사 요청 접수'}</button>
        {submitError && <p role="alert" className="text-sm text-red-500">접수 실패: {submitError}</p>}
        {success && <p role="status" className="text-sm text-teal-600">{success}</p>}
      </form>
      <div className="space-y-3 pt-4" style={{ borderTop: '1px solid var(--separator)' }}>
        <h3 className="text-sm font-semibold">최근 요청</h3>
        {targetRequestId && <p className="text-xs opacity-70 break-all">링크의 요청: {targetRequestId}</p>}
        {targetError && <p role="alert" className="text-sm text-red-500">{targetError}</p>}
        {loading && <p className="text-xs opacity-70">요청 목록 로딩 중…</p>}
        {listError && <p role="alert" className="text-sm text-red-500">상태 갱신 실패: {listError}. 기존 표시는 마지막 조회 상태이며, 30초 후 재시도합니다.</p>}
        {!loading && !listError && requests.length === 0 && <p className="text-xs opacity-70">아직 요청이 없습니다.</p>}
        {requests.map(r => {
          const canDownload = r.country_results.some(c => c.status !== 'pending');
          const priced = r.country_results.filter(c => c.status === 'priced').length;
          const unresolved = r.country_results.filter(c => c.status === 'failed' || c.status === 'unconfirmed');
          return <article key={r.id} id={'price-request-' + r.id} ref={r.id === targetRequestId ? targetRef : undefined}
            className={'rounded-xl p-4 space-y-2 text-xs ' + (r.id === targetRequestId ? 'ring-2 ring-teal-500 scroll-mt-6' : '')} style={{ background: 'var(--surface-inset)' }}>
            {r.id === targetRequestId && <p className="font-semibold text-teal-600">링크에서 지정한 요청</p>}
            <div className="flex justify-between gap-2 flex-wrap"><strong>{r.query}</strong><span>{statusLabel[r.status] || r.status}</span></div>
            <p>가격 확인 {priced}/{r.countries.length}개국 · 요청 {date(r.created_at)} · 마지막 상태 갱신 {date(r.updated_at)}</p>
            <div className="space-y-1">
              <button type="button" disabled={!canDownload || downloadBusy[r.id]} aria-busy={!!downloadBusy[r.id]}
                aria-describedby={'request-export-hint-' + r.id}
                onClick={e => { e.stopPropagation(); void downloadResult(r.id); }}
                onKeyDown={e => e.stopPropagation()}
                className="ui-button-secondary">
                {downloadBusy[r.id] ? '다운로드 중…' : '결과 Excel 다운로드'}
              </button>
              <p id={'request-export-hint-' + r.id} className="opacity-70">{canDownload ? '이 요청의 저장 결과 스냅샷입니다. 미확정·실패 국가의 상태와 사유도 포함합니다.' : '아직 국가별 결과가 없습니다. 한 국가라도 처리되면 다운로드할 수 있습니다.'}</p>
              {downloadErrors[r.id] && <p role="alert" className="text-red-500">다운로드 실패: {downloadErrors[r.id]}</p>}
            </div>
            <p>마지막 대시보드 검증: {date(r.verified_at)}</p>
            <p>이메일: {notificationLabel[r.notification_status] || r.notification_status} · {r.notify_email}{r.notified_at ? ' · ' + date(r.notified_at) : ''}</p>
            {unresolved.length > 0 && <p className="text-amber-600">부분 미확정/실패 {unresolved.length}개국: 아래 사유를 확인하세요. 미허가 또는 가격 0을 의미하지 않습니다.</p>}
            <div className="grid gap-2 md:grid-cols-2">{r.countries.map(code => {
              const result = r.country_results.find(c => c.country === code);
              return <div key={code} className="rounded-lg p-2.5" style={{ background: 'var(--surface)' }}><strong>{labels.get(code) || code}</strong> · <span className={result?.status === 'failed' ? 'text-red-500' : result?.status === 'unconfirmed' ? 'text-amber-600' : ''}>{countryStatus[result?.status || 'pending'] || result?.status}</span>{result?.reason && <p className="mt-1 break-words opacity-80">{result.reason}</p>}{result?.updated_at && <p className="opacity-60 mt-1">{date(result.updated_at)}</p>}</div>;
            })}</div>
          </article>;
        })}
      </div>
    </section>
  );
});
export default PriceRequestPanel;
