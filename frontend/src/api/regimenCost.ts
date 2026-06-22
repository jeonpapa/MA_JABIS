import { api } from './client';

// 투약비용비교 — 국내약가 기반 레지멘(약제 2~5개) 구성 → 일/월/연 치료비 비교.
// 비용은 enrichBulk 스냅샷(저장 시점). 서버 DB(regimen_comparisons)에 payload 통째 저장.

export interface RegimenDrug {
  insuranceCode: string;
  name: string;
  ingredient: string;
  currentPrice: number | null;
  dailyCost: number | null;
  monthlyCost: number | null;
  yearlyCost: number | null;
}

export interface Regimen {
  name: string;
  drugs: RegimenDrug[];   // 2~5개
}

export interface RegimenPayload {
  base: Regimen;
  comparators: Regimen[]; // 최대 5개
  snapshotDate?: string;
}

export interface RegimenComparison {
  id: number;
  name: string;
  owner_email?: string;
  payload: RegimenPayload;
  created_at?: string;
  updated_at?: string;
}

export async function listRegimens(): Promise<RegimenComparison[]> {
  const res = await api.get<{ items: RegimenComparison[] }>('/api/regimen-comparisons');
  return res.items || [];
}

export async function createRegimen(name: string, payload: RegimenPayload): Promise<RegimenComparison> {
  return api.post<RegimenComparison>('/api/regimen-comparisons', { name, payload });
}

export async function updateRegimen(id: number, name: string, payload: RegimenPayload): Promise<RegimenComparison> {
  return api.put<RegimenComparison>(`/api/regimen-comparisons/${id}`, { name, payload });
}

export async function deleteRegimen(id: number): Promise<void> {
  await api.delete(`/api/regimen-comparisons/${id}`);
}

/** 레지멘 총 일/월/연 치료비 = 구성 약제 합산 (null 은 0 취급, 일부 미상 표시). */
export function regimenTotals(r: Regimen): { daily: number; monthly: number; yearly: number; hasMissing: boolean } {
  let daily = 0, monthly = 0, yearly = 0, hasMissing = false;
  for (const d of r.drugs) {
    if (d.dailyCost == null && d.monthlyCost == null && d.yearlyCost == null) hasMissing = true;
    daily += d.dailyCost ?? 0;
    monthly += d.monthlyCost ?? (d.dailyCost != null ? d.dailyCost * 30 : 0);
    yearly += d.yearlyCost ?? (d.dailyCost != null ? d.dailyCost * 365 : 0);
  }
  return { daily, monthly, yearly, hasMissing };
}
