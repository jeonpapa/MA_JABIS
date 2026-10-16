import { api, ApiError, clearAuth, getToken } from './client';

export type PriceScope = 'a8' | 'oecd';
export interface PriceCountry { code: string; label: string; currency: string; source_url?: string }
export interface PriceCatalog { a8: PriceCountry[]; oecd: PriceCountry[]; note: string }
export interface PriceIdentity { slug: string; brand: string; inn: string }
export interface CountryProgress {
  country: string; status: 'pending' | 'priced' | 'unconfirmed' | 'failed';
  reason: string | null; row_ids: number[]; updated_at: string;
}
export interface PriceRequest {
  id: string; query: string; scope: PriceScope; countries: string[]; notify_email: string;
  status: 'queued' | 'processing' | 'awaiting_verification' | 'verified' | 'completed' | 'partial_completed' | 'blocked';
  created_at: string; updated_at: string; verified_at: string | null;
  notification_status: 'pending' | 'sending' | 'sent' | 'uncertain'; notified_at: string | null;
  country_results: CountryProgress[]; dashboard_url: string;
}
export interface SubmitPriceRequest {
  query: string; scope: PriceScope; countries: string[]; notify_email: string; identity?: PriceIdentity;
}
export interface OecdPrice {
  id: number; country: string; product_name: string; ingredient: string; dosage_strength: string;
  dosage_form: string; package_unit: string; pack_count: number; local_price: number; currency: string;
  price_tier: 'ex_factory' | 'pharmacy_purchase' | 'pharmacy_retail' | 'reimbursement' | 'other';
  price_tier_original: string; vat_status: 'included' | 'excluded' | 'unknown';
  effective_date: string | null; observed_at: string; source_url: string; source_label: string;
  evidence_ref: string; ex_factory_price: number | null; pharmacy_purchase_price: number | null;
  derivation_note: string | null; grade: 'A' | 'B' | 'C' | '-'; normalized_price: number | null;
  reference_mg: number | null; total_pkg_mg: number | null;
}
export const fetchPriceCatalog = () => api.get<PriceCatalog>('/api/price-requests/catalog');
export const submitPriceRequest = async (body: SubmitPriceRequest) =>
  (await api.post<{ request: PriceRequest }>('/api/price-requests', body)).request;
export const fetchPriceRequests = async (scope: PriceScope, query?: string) => {
  const params = new URLSearchParams({ scope });
  if (query?.trim()) params.set('q', query.trim());
  return (await api.get<{ requests: PriceRequest[] }>('/api/price-requests?' + params)).requests;
};
export const fetchPriceRequest = async (id: string) =>
  (await api.get<{ request: PriceRequest }>('/api/price-requests/' + encodeURIComponent(id))).request;
export const fetchOecdPrices = async (query: string) =>
  (await api.get<{ query: string; results: OecdPrice[] }>('/api/oecd/prices?q=' + encodeURIComponent(query))).results;


const XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

function safeExcelFilename(value: string, fallback: string): string {
  const clean = (name: string) => name.replace(/[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069/\\:*?"<>|]/g, '_')
    .replace(/^[.\s]+|[.\s]+$/g, '').slice(0, 180);
  const filename = clean(value) || clean(fallback) || 'price-results.xlsx';
  return /\.xlsx$/i.test(filename) ? filename : filename + '.xlsx';
}

function excelFilename(disposition: string, fallback: string): string {
  const extended = disposition.match(/(?:^|;)\s*filename\*\s*=\s*(?:"([^"\r\n]*)"|([^;\r\n]*))/i);
  if (extended) {
    const value = (extended[1] ?? extended[2]).trim();
    const utf8 = value.match(/^UTF-8'[^']*'(.*)$/i);
    if (utf8) {
      try { return safeExcelFilename(decodeURIComponent(utf8[1]), fallback); }
      catch { /* Fall back to the plain filename or our own safe name. */ }
    }
  }
  const plain = disposition.match(/(?:^|;)\s*filename\s*=\s*(?:"([^"\r\n]*)"|([^;\r\n]*))/i);
  return safeExcelFilename(plain ? (plain[1] ?? plain[2]).trim() : '', fallback);
}

async function downloadExcel(url: string, fallback: string): Promise<void> {
  const token = getToken();
  const response = await fetch(url, {
    headers: { Accept: XLSX_TYPE, ...(token ? { Authorization: 'Bearer ' + token } : {}) },
  });
  if (response.status === 401) clearAuth();
  if (!response.ok) {
    let message = response.status === 401 ? '로그인이 만료되었습니다. 다시 로그인해 주세요.'
      : response.status === 403 ? 'Excel 다운로드 권한이 없습니다.'
      : response.status === 404 ? '다운로드할 저장 결과가 없습니다.' : 'Excel 다운로드 실패 (HTTP ' + response.status + ')';
    let code: string | undefined;
    try {
      const text = await response.text();
      try {
        const data = JSON.parse(text);
        if (typeof data?.error === 'string') message = data.error;
        else if (typeof data?.message === 'string') message = data.message;
        if (typeof data?.code === 'string') code = data.code;
      } catch {
        if (text.trim() && !text.includes('<')) message = text.trim().slice(0, 300);
      }
    } catch { /* Keep the HTTP fallback when the error body cannot be read. */ }
    throw new ApiError(message, response.status, code);
  }
  const contentType = response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase();
  if (contentType && contentType !== XLSX_TYPE && contentType !== 'application/octet-stream') {
    throw new Error('Excel 파일이 아닌 응답을 받았습니다. 다시 로그인하거나 재시도해 주세요.');
  }
  const blob = await response.blob();
  if (!blob.size) throw new Error('다운로드 파일이 비어 있습니다. 다시 시도해 주세요.');
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  try {
    link.href = objectUrl;
    link.download = excelFilename(response.headers.get('Content-Disposition') || '', fallback);
    document.body.appendChild(link);
    link.click();
  } finally {
    link.remove();
    // Let the browser start consuming the blob before releasing it.
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  }
}

/** A particular request's stored result snapshot, including status/reason rows. */
export const downloadPriceRequest = (id: string) =>
  downloadExcel('/api/price-requests/' + encodeURIComponent(id) + '/export.xlsx', 'price-request-' + id + '.xlsx');

/** Current stored cache, not the historical snapshot of any request. */
export const downloadPriceHistory = (scope: PriceScope, query: string) => {
  const params = new URLSearchParams({ scope, q: query.trim() });
  return downloadExcel('/api/price-history/export.xlsx?' + params, scope + '-' + query.trim() + '-current-cache.xlsx');
};
