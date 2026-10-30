import type { Tables } from '@smartgym/supabase-client/types';

export type MemberListRow = Pick<Tables<'members'>, 'id' | 'full_name' | 'member_code' | 'phone' | 'email' | 'status' | 'branch_id' | 'created_at'>;
export type BranchOption = Pick<Tables<'branches'>, 'id' | 'name' | 'organization_id'>;
