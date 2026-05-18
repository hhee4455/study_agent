import { useQuery, UseQueryResult } from '@tanstack/react-query';

export interface Budget {
  total_usd: number;
  spent_usd: number;
  remaining_usd: number;
  breakdown?: Record<string, number>;
}

async function fetchBudget(): Promise<Budget> {
  const res = await fetch('/api/budget');
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json() as Promise<Budget>;
}

export function useBudgetQuery(): UseQueryResult<Budget> {
  return useQuery<Budget>({
    queryKey: ['budget'],
    queryFn: fetchBudget,
    staleTime: 5_000,
    refetchInterval: 3_000,
  });
}
