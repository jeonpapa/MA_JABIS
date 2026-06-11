import { api, getToken } from './client';

// ─────────────────────────────────────────────────────────────────────────────
// 허가문서 관리 — api/server.py /api/admin/indication-grid, /api/admin/approval-document*
// ─────────────────────────────────────────────────────────────────────────────

export type Agency = 'FDA' | 'EMA' | 'MHRA' | 'PMDA' | 'TGA' | 'MFDS';

export interface AgencyCell {
  approval_date?: string | null;
  label_url?: string | null;
  label_excerpt?: string | null;
  doc_count: number;
}

export interface IndicationGridRow {
  indication_id: string;
  title: string | null;
  disease: string | null;
  line_of_therapy: string | null;
  stage: string | null;
  biomarker_class: string | null;
  agencies: Record<Agency, AgencyCell>;
}

export interface IndicationGridResponse {
  product: string;
  indications: IndicationGridRow[];
}

export async function fetchIndicationGrid(product: string): Promise<IndicationGridResponse> {
  return api.get<IndicationGridResponse>(
    `/api/admin/indication-grid?product=${encodeURIComponent(product)}`,
  );
}

export interface ApprovalDocument {
  id: number;
  indication_id: string;
  agency: Agency;
  file_path: string;
  original_filename: string | null;
  file_size: number | null;
  content_type: string | null;
  approval_date: string | null;
  label_excerpt: string | null;
  label_url: string | null;
  notes: string | null;
  uploaded_by: string | null;
  uploaded_at: string;
  product?: string;
  disease?: string | null;
  title?: string | null;
}

export async function listApprovalDocuments(params: {
  indication_id?: string;
  agency?: Agency;
  product?: string;
}): Promise<ApprovalDocument[]> {
  const qs = new URLSearchParams();
  if (params.indication_id) qs.set('indication_id', params.indication_id);
  if (params.agency) qs.set('agency', params.agency);
  if (params.product) qs.set('product', params.product);
  const r = await api.get<{ items: ApprovalDocument[] }>(
    `/api/admin/approval-document?${qs.toString()}`,
  );
  return r.items;
}

export async function uploadApprovalDocument(args: {
  file: File;
  indication_id: string;
  agency: Agency;
  approval_date?: string;
  label_excerpt?: string;
  label_url?: string;
  notes?: string;
}): Promise<{ ok: boolean; doc_id: number; file_path: string }> {
  const fd = new FormData();
  fd.append('file', args.file);
  fd.append('indication_id', args.indication_id);
  fd.append('agency', args.agency);
  if (args.approval_date) fd.append('approval_date', args.approval_date);
  if (args.label_excerpt) fd.append('label_excerpt', args.label_excerpt);
  if (args.label_url) fd.append('label_url', args.label_url);
  if (args.notes) fd.append('notes', args.notes);

  // FormData 는 fetch 가 multipart boundary 를 자동 설정 — api.post 는 JSON 전용이므로 직접 fetch
  const token = getToken() ?? '';
  const res = await fetch('/api/admin/approval-document', {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body: fd,
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`upload 실패: ${res.status} ${t}`);
  }
  return res.json();
}

export async function deleteApprovalDocument(docId: number): Promise<{ ok: boolean }> {
  return api.delete<{ ok: boolean }>(`/api/admin/approval-document/${docId}`);
}

export function approvalDocFileUrl(docId: number): string {
  return `/api/admin/approval-document/${docId}/file`;
}

export interface FdaSyncResult {
  product_slug: string;
  drug: string;
  agencies: Array<{ agency: string; ok: number; skipped: number; errors: number }>;
}

export async function triggerFdaSync(args: {
  drug: string;
  product_slug: string;
  wipe?: boolean;
}): Promise<FdaSyncResult> {
  return api.post<FdaSyncResult>('/api/admin/fda-sync', args);
}
