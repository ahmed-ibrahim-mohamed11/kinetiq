import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { createClient } from '@supabase/supabase-js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');

function loadEnv(filePath) {
  if (fs.existsSync(filePath)) {
    for (const raw of fs.readFileSync(filePath, 'utf8').split(/\r?\n/)) {
      const line = raw.trim();
      if (!line || line.startsWith('#')) continue;
      const m = /^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(line);
      if (!m) continue;
      const key = m[1];
      let val = m[2] || '';
      if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
        val = val.slice(1, -1);
      }
      if (!process.env[key]) process.env[key] = val;
    }
  }
}

loadEnv(path.join(ROOT, '.env'));
loadEnv(path.join(ROOT, 'apps', 'admin-web', '.env.local'));
loadEnv(path.join(ROOT, 'apps', 'member-web', '.env.local'));

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL || process.env.SUPABASE_URL || 'https://vpwudyxfsbyrbpwqdmew.supabase.co';
const SERVICE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || '';

const supabase = createClient(SUPABASE_URL, SERVICE_KEY, {
  auth: { autoRefreshToken: false, persistSession: false },
});

async function main() {
  console.log('Connecting to Supabase at:', SUPABASE_URL);

  // 1. Get or create organization and branch
  const { data: orgs } = await supabase.from('organizations').select('id, name').limit(1);
  let orgId = orgs?.[0]?.id;
  if (!orgId) {
    orgId = 'a0000000-0000-4000-8000-000000000001';
    await supabase.from('organizations').upsert({
      id: orgId,
      name: 'DEMO Smart Gym (test data)',
      slug: 'demo-smart-gym',
      country_code: 'EG',
    });
  }

  const { data: branches } = await supabase.from('branches').select('id, name').eq('organization_id', orgId).limit(1);
  let branchId = branches?.[0]?.id;
  if (!branchId) {
    branchId = 'b0000000-0000-4000-8000-000000000001';
    await supabase.from('branches').upsert({
      id: branchId,
      organization_id: orgId,
      name: 'DEMO Branch — Nasr City',
      timezone: 'Africa/Cairo',
      address: 'Nasr City, Cairo, Egypt',
    });
  }

  console.log(`Using Org ID: ${orgId}, Branch ID: ${branchId}`);

  // 2. Setup Admin / Staff Account
  const adminEmail = 'admin@smartgym.demo';
  const adminPassword = 'SmartGymAdmin2026!';
  
  const { data: listData } = await supabase.auth.admin.listUsers();
  const existingAdmin = listData?.users?.find((u) => u.email === adminEmail);
  let adminUserId = existingAdmin?.id;

  if (existingAdmin) {
    console.log(`Admin account exists (${adminEmail}, ID: ${adminUserId}), updating password...`);
    await supabase.auth.admin.updateUserById(adminUserId, { password: adminPassword, email_confirm: true });
  } else {
    console.log(`Creating Admin account (${adminEmail})...`);
    const { data: newAdmin, error: err } = await supabase.auth.admin.createUser({
      email: adminEmail,
      password: adminPassword,
      email_confirm: true,
      user_metadata: { full_name: 'Smart Gym Administrator' },
    });
    if (err) throw err;
    adminUserId = newAdmin.user.id;
  }

  // Grant staff_roles (owner)
  const { error: staffRoleErr } = await supabase.from('staff_roles').upsert(
    {
      user_id: adminUserId,
      organization_id: orgId,
      branch_id: branchId,
      role: 'owner',
    },
    { onConflict: 'user_id,organization_id,branch_id,role' }
  );
  if (staffRoleErr) console.warn('Staff role warning:', staffRoleErr.message);
  console.log('✅ Admin account configured successfully.');

  // 3. Setup Member Account
  const memberEmail = 'member@smartgym.demo';
  const memberPassword = 'SmartGymMember2026!';

  const existingMemberUser = listData?.users?.find((u) => u.email === memberEmail);
  let memberUserId = existingMemberUser?.id;

  if (existingMemberUser) {
    console.log(`Member account exists (${memberEmail}, ID: ${memberUserId}), updating password...`);
    await supabase.auth.admin.updateUserById(memberUserId, { password: memberPassword, email_confirm: true });
  } else {
    console.log(`Creating Member account (${memberEmail})...`);
    const { data: newMember, error: err } = await supabase.auth.admin.createUser({
      email: memberEmail,
      password: memberPassword,
      email_confirm: true,
      user_metadata: { full_name: 'Ahmed Trainee' },
    });
    if (err) throw err;
    memberUserId = newMember.user.id;
  }

  // Link to members table
  const { data: existingMember } = await supabase.from('members').select('*').eq('user_id', memberUserId).maybeSingle();
  let memberId = existingMember?.id;
  if (!existingMember) {
    memberId = '33333333-3333-4333-8333-333333333333';
    const { error: memberUpsertErr } = await supabase.from('members').upsert({
      id: memberId,
      organization_id: orgId,
      branch_id: branchId,
      member_code: 'GYM-TEST-M2A7',
      full_name: 'Ahmed Trainee (Demo Member)',
      phone: '+201000000000',
      email: memberEmail,
      status: 'active',
      user_id: memberUserId,
    });
    if (memberUpsertErr) console.warn('Member upsert warning:', memberUpsertErr.message);
  } else {
    console.log(`Member profile already linked: ${existingMember.full_name} (${existingMember.member_code})`);
  }

  // 4. Ensure Membership Plan & Active Subscription with AI Entitlement
  const { data: plans } = await supabase
    .from('membership_plans')
    .select('id, name')
    .eq('organization_id', orgId)
    .limit(1);

  let planId = plans?.[0]?.id;
  if (!planId) {
    const { data: newPlan } = await supabase
      .from('membership_plans')
      .insert({
        organization_id: orgId,
        name: '6 Months Pro VIP (AI Training Included)',
        code: 'PLAN_PRO_6M',
        price: 5800.0,
        currency: 'EGP',
        duration_days: 180,
        ai_training_included: true,
        is_active: true,
      })
      .select('id')
      .single();
    planId = newPlan?.id;
  }

  // Add active subscription
  const now = new Date();
  const nextYear = new Date(now.getTime() + 365 * 24 * 3600 * 1000);
  const { error: subErr } = await supabase.from('subscriptions').upsert(
    {
      member_id: memberId,
      plan_id: planId,
      status: 'active',
      started_at: now.toISOString(),
      current_period_start: now.toISOString(),
      current_period_end: nextYear.toISOString(),
      original_expires_at: nextYear.toISOString(),
      ai_addon_active: true,
      price_paid: 5800.0,
    },
    { onConflict: 'member_id' }
  );
  if (subErr) console.warn('Subscription upsert note:', subErr.message);

  // 5. Ensure Privacy & Biometric Consents
  for (const consentType of ['biometric_face_identification', 'cctv_workout_tracking', 'data_processing']) {
    await supabase.from('consent_records').upsert({
      member_id: memberId,
      consent_type: consentType,
      status: 'granted',
      policy_version: 'privacy-v1',
    });
  }

  console.log('✅ Member account configured with active AI subscription and biometric consent.');

  console.log('\n======================================================');
  console.log('DEMO ACCOUNTS READY FOR LOGIN:');
  console.log('------------------------------------------------------');
  console.log('1. ADMIN / STAFF ACCOUNT (apps/admin-web on :3000):');
  console.log('   Email:    admin@smartgym.demo');
  console.log('   Password: SmartGymAdmin2026!');
  console.log('   Role:     Owner / Administrator (Full Access)');
  console.log('------------------------------------------------------');
  console.log('2. MEMBER / TRAINEE ACCOUNT (apps/member-web on :3001):');
  console.log('   Email:    member@smartgym.demo');
  console.log('   Password: SmartGymMember2026!');
  console.log('   Member:   Ahmed Trainee (GYM-TEST-M2A7)');
  console.log('   Status:   Active Pro Subscription + AI Included');
  console.log('======================================================\n');
}

main().catch(console.error);
