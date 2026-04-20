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
