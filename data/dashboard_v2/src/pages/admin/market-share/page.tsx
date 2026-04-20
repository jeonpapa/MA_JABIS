import { useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  fetchMarketShareUploads, uploadMarketShareXlsx,
  type MsUploadEntry, type MsIngestResult, quarterLabel,
} from '@/api/marketShare';
import { fetchMe } from '@/utils/authUsers';

export default function AdminMarketSharePage() {
  const navigate = useNavigate();
  const [authChecked, setAuthChecked] = useState(false);
  const [uploads, setUploads] = useState<MsUploadEntry[]>([]);
  const [totals, setTotals] = useState<{ products: number; quarterly_points: number; quarters_available: string[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [busy, setBusy] = useState(false);
  const [uploadResult, setUploadResult] = useState<MsIngestResult | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  // admin 권한 확인
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

  const reload = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetchMarketShareUploads();
      setUploads(r.uploads);
      setTotals(r.totals);
    } catch (e) {
      setError(e instanceof Error ? e.message : '업로드 이력 조회 실패');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (authChecked) reload();
  }, [authChecked]);

  const handleUpload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      setUploadError('파일을 선택하세요');
      return;
    }
    setUploadError(null);
    setUploadResult(null);
    setBusy(true);
    try {
      const r = await uploadMarketShareXlsx(file);
      setUploadResult(r);
      if (fileRef.current) fileRef.current.value = '';
      await reload();
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : '업로드 실패');
    } finally {
      setBusy(false);
    }
  };

  if (!authChecked) {
    return (
      <div className="min-h-screen flex items-center justify-center text-[#8B9BB4] text-sm">
        <i className="ri-loader-4-line animate-spin mr-2"></i>권한 확인 중…
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0D1117] text-white">
      <div className="px-8 pt-8 pb-6 border-b border-[#1E2530]">
        <div className="flex items-center gap-2 mb-1">
          <span className="w-5 h-5 flex items-center justify-center"><i className="ri-upload-cloud-2-line text-[#00E5CC]"></i></span>
          <h1 className="text-2xl font-bold text-white">Market Share — 데이터 관리</h1>
        </div>
        <p className="text-[#8B9BB4] text-sm">IQVIA NSA-E Master 분기 Excel 업로드 (Admin 전용)</p>
      </div>

      <div className="px-8 py-6 space-y-5 max-w-4xl">
        {/* 현재 데이터 현황 */}
        <div className="grid grid-cols-3 gap-4">
          <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
            <p className="text-[#8B9BB4] text-xs mb-2">등록 제품 (pack 단위)</p>
            <p className="text-3xl font-bold text-[#00E5CC]">{totals?.products?.toLocaleString() ?? '—'}</p>
          </div>
          <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
            <p className="text-[#8B9BB4] text-xs mb-2">분기 데이터 포인트</p>
            <p className="text-3xl font-bold text-[#7C3AED]">{totals?.quarterly_points?.toLocaleString() ?? '—'}</p>
          </div>
          <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-5">
            <p className="text-[#8B9BB4] text-xs mb-2">사용 가능 분기</p>
            <p className="text-3xl font-bold text-[#F59E0B]">{totals?.quarters_available?.length ?? 0}<span className="text-base text-[#8B9BB4] ml-2">분기</span></p>
            {totals && totals.quarters_available.length > 0 && (
              <p className="text-[#4A5568] text-[11px] mt-2">
                {quarterLabel(totals.quarters_available[0])} ~ {quarterLabel(totals.quarters_available.at(-1)!)}
              </p>
            )}
          </div>
        </div>

        {/* 업로드 폼 */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
          <h2 className="text-white font-bold text-base mb-4">새 분기 파일 업로드</h2>
          <div className="space-y-4">
            <div>
              <label className="block text-[#8B9BB4] text-xs mb-2">NSA 시트가 포함된 .xlsx 파일</label>
              <input
                ref={fileRef}
                type="file"
                accept=".xlsx"
                disabled={busy}
                className="block w-full text-sm text-[#8B9BB4] file:mr-4 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-[#00E5CC]/10 file:text-[#00E5CC] hover:file:bg-[#00E5CC]/20 cursor-pointer"
              />
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={handleUpload}
                disabled={busy}
                className="bg-[#00E5CC] text-[#0A0E1A] px-5 py-2 rounded-lg text-sm font-semibold cursor-pointer hover:bg-[#00C9B1] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {busy ? (
                  <span className="flex items-center gap-2"><i className="ri-loader-4-line animate-spin"></i>업로드 중…</span>
                ) : (
                  <span className="flex items-center gap-2"><i className="ri-upload-line"></i>업로드 + 적재</span>
                )}
              </button>
              <p className="text-[#4A5568] text-xs">
                동일 product+quarter 는 REPLACE (덮어쓰기). 기존 데이터에 영향 없음.
              </p>
            </div>

            {uploadError && (
              <div className="bg-red-500/10 border border-red-500/30 rounded-xl p-3">
                <p className="text-red-400 text-sm">{uploadError}</p>
              </div>
            )}
            {uploadResult && (
              <div className="bg-[#00E5CC]/10 border border-[#00E5CC]/30 rounded-xl p-4">
                <p className="text-[#00E5CC] text-sm font-semibold mb-2">
                  <i className="ri-check-line mr-1"></i> {uploadResult.filename} 적재 완료
                </p>
                <div className="grid grid-cols-3 gap-3 text-xs">
                  <div>
                    <p className="text-[#8B9BB4]">처리 행</p>
                    <p className="text-white font-bold">{uploadResult.rows_ingested.toLocaleString()}</p>
                  </div>
                  <div>
                    <p className="text-[#8B9BB4]">unique 제품</p>
                    <p className="text-white font-bold">{uploadResult.unique_products.toLocaleString()}</p>
                  </div>
                  <div>
                    <p className="text-[#8B9BB4]">분기 포인트</p>
                    <p className="text-white font-bold">{uploadResult.quarterly_points.toLocaleString()}</p>
                  </div>
                </div>
                <p className="text-[#8B9BB4] text-[11px] mt-2">
                  Quarters: {uploadResult.quarters.join(', ')}
                </p>
              </div>
            )}
          </div>
        </div>

        {/* 업로드 이력 */}
        <div className="bg-[#161B27] rounded-2xl border border-[#1E2530] p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-white font-bold text-base">업로드 이력</h2>
            <button
              onClick={reload}
              className="text-[#8B9BB4] text-xs hover:text-white cursor-pointer flex items-center gap-1"
            >
              <i className="ri-refresh-line"></i> 새로고침
            </button>
          </div>
          {loading && <p className="text-[#8B9BB4] text-sm">로드 중…</p>}
          {error && <p className="text-red-400 text-sm">{error}</p>}
          {!loading && !error && uploads.length === 0 && (
            <p className="text-[#4A5568] text-sm">업로드 이력이 없습니다.</p>
          )}
          {!loading && uploads.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[#8B9BB4] text-xs border-b border-[#1E2530]">
                    <th className="text-left py-2 pr-3">시각</th>
                    <th className="text-left py-2 pr-3">파일</th>
                    <th className="text-left py-2 pr-3">업로더</th>
                    <th className="text-right py-2 pr-3">행 수</th>
                    <th className="text-left py-2">분기 범위</th>
                  </tr>
                </thead>
                <tbody>
                  {uploads.map(u => (
                    <tr key={u.id} className="border-b border-[#1E2530]/50 last:border-b-0">
                      <td className="py-2 pr-3 text-[#8B9BB4] text-xs whitespace-nowrap">
                        {u.uploaded_at.replace('T', ' ').slice(0, 19)}
                      </td>
                      <td className="py-2 pr-3 text-white">{u.filename ?? '—'}</td>
                      <td className="py-2 pr-3 text-[#8B9BB4]">{u.uploaded_by ?? '—'}</td>
                      <td className="py-2 pr-3 text-white text-right">{u.rows_ingested.toLocaleString()}</td>
                      <td className="py-2 text-[#4A5568] text-xs">
                        {u.quarters.length > 0
                          ? `${quarterLabel(u.quarters[0])} ~ ${quarterLabel(u.quarters.at(-1)!)} (${u.quarters.length})`
                          : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
