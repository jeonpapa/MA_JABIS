import { api } from './client';

export interface ReimbursementItem {
  indication_id: string;
  product: string;
  disease: string | null;
  line_of_therapy: string | null;
  stage: string | null;
  biomarker_class: string | null;
  title: string | null;
  is_reimbursed: boolean;
  effective_date: string | null;
  criteria_text: string | null;
  notice_date: string | null;
  notice_url: string | null;
  updated_by: string | null;
  updated_at: string | null;
}

export interface ReimbursementPatch {
  is_reimbursed: boolean;
  effective_date?: string | null;
  criteria_text?: string | null;
  notice_date?: string | null;
  notice_url?: string | null;
}

export async function listReimbursement(product?: string): Promise<ReimbursementItem[]> {
  const q = product ? `?product=${encodeURIComponent(product)}` : '';
  const r = await api.get<{ items: ReimbursementItem[] }>(`/api/admin/reimbursement${q}`);
  return r.items;
}

export async function saveReimbursement(
  indicationId: string,
  patch: ReimbursementPatch,
): Promise<ReimbursementItem> {
  const r = await api.put<{ item: ReimbursementItem }>(
    `/api/admin/reimbursement/${encodeURIComponent(indicationId)}`,
    patch,
  );
  return r.item;
}
