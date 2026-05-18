import { useQuery, UseQueryResult } from '@tanstack/react-query';

export interface Goal {
  goal_id: string;
  title: string;
  status: string;
  agent_id?: string;
  description?: string;
}

async function fetchGoals(): Promise<Goal[]> {
  const res = await fetch('/api/goals');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<Goal[]>;
}

export function useGoalsQuery(): UseQueryResult<Goal[]> {
  return useQuery<Goal[]>({
    queryKey: ['goals'],
    queryFn: fetchGoals,
    staleTime: 3_000,
    refetchInterval: 3_000,
  });
}
