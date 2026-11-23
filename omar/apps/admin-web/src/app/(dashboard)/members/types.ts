import type { Tables } from '@smartgym/supabase-client/types';

export type MemberListRow = Pick<Tables<'members'>, 'id' | 'full_name' | 'member_code' | 'phone' | 'email' | 'status' | 'branch_id' | 'created_at'>;
export type BranchOption = Pick<Tables<'branches'>, 'id' | 'name' | 'organization_id'>;
export type PlanOption = Pick<Tables<'membership_plans'>, 'id' | 'name' | 'code' | 'duration_days' | 'price' | 'currency' | 'ai_training_included' | 'ai_addon_fee'>;

