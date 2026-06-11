import { api } from './client';

export type PipelineStatus = 'current' | 'upcoming';

export interface PipelineItem {
  id: number;
  name: string;
  phase: string | null;
  indication: string | null;
  expected_year: number | null;
  status: PipelineStatus;
  note: string | null;
  created_at: string;
  updated_at: string;
}

export interface PipelineCreateInput {
  name: string;
  phase?: string | null;
  indication?: string | null;
  expected_year?: number | null;
  status?: PipelineStatus;
  note?: string | null;
}

/**
 * Home PipelineModal 은 부가 필드(약제클래스·대상질환·국내 허가/급여일)를
 * note 에 JSON(`{"custom":true,...}`) 으로 보존한다 (src/api/home.ts 참조).
 * admin 목록에서는 이를 요약 렌더하고, 편집 시 raw 문자열 그대로 round-trip 한다.
 */
export interface PipelineNoteExtras {
  custom?: boolean;
  drugClass?: string;
  targetDisease?: string;
  domesticApprovalDate?: string;
  domesticReimbursementDate?: string;
}

export function parseNoteExtras(note: string | null): PipelineNoteExtras | null {
  if (!note) return null;
  try {
    const parsed = JSON.parse(note);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return parsed as PipelineNoteExtras;
    }
    return null;
  } catch {
    return null;
  }
}

export async function listPipeline(): Promise<PipelineItem[]> {
  const r = await api.get<{ items: PipelineItem[] }>('/api/msd/pipeline');
  return r.items;
}

export async function createPipeline(input: PipelineCreateInput): Promise<PipelineItem> {
  const r = await api.post<{ item: PipelineItem }>('/api/admin/msd/pipeline', input);
  return r.item;
}

export async function updatePipeline(
  id: number,
  patch: Partial<PipelineCreateInput>
): Promise<PipelineItem> {
  const r = await api.patch<{ item: PipelineItem }>(`/api/admin/msd/pipeline/${id}`, patch);
  return r.item;
}

export async function deletePipeline(id: number): Promise<void> {
  await api.delete<{ ok: true }>(`/api/admin/msd/pipeline/${id}`);
}
