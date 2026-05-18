import { useQuery, UseQueryResult } from '@tanstack/react-query';

export interface Conflict {
  id: string;
  description: string;
  agent_ids?: string[];
  severity?: string;
  created_at?: string;
}

async function fetchConflicts(): Promise<Conflict[]> {
  const res = await fetch('/api/conflicts');
  if (res.status === 404) return [];
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<Conflict[]>;
}

export function useConflictsQuery(): UseQueryResult<Conflict[]> {
  return useQuery<Conflict[]>({
    queryKey: ['conflicts'],
    queryFn: fetchConflicts,
    staleTime: 3_000,
    refetchInterval: 3_000,
    placeholderData: [],
  });
}
