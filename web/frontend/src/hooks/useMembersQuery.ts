import { useQuery, UseQueryResult } from '@tanstack/react-query';
import { fetchMembers, MemberCard } from '../api/members';

export type { MemberCard, AgentStatus } from '../api/members';

export function useMembersQuery(): UseQueryResult<MemberCard[]> {
  return useQuery<MemberCard[]>({
    queryKey: ['members'],
    queryFn: fetchMembers,
    staleTime: 2_000,
    refetchInterval: 2_000,
  });
}
