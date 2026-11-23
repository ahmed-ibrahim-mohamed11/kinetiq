// Query Supabase Cloud database using service role key
import { createClient } from "@supabase/supabase-js";
import fs from "fs";

// Read service role key from omar/.env
const envContent = fs.readFileSync("omar/.env", "utf8");
const urlMatch = envContent.match(/SUPABASE_URL=(.*)/);
const keyMatch = envContent.match(/SUPABASE_SERVICE_ROLE_KEY=(.*)/);

const url = urlMatch ? urlMatch[1].trim() : "";
const serviceKey = keyMatch ? keyMatch[1].trim() : "";

const supabase = createClient(url, serviceKey, {
  auth: { autoRefreshToken: false, persistSession: false }
});

async function run() {
  console.log("Connecting to Supabase Cloud:", url);

  // 1. Check organizations & branches
  const { data: orgs, error: orgErr } = await supabase.from("organizations").select("id, name, slug");
  console.log("Organizations:", orgs, orgErr || "");

  const { data: branches, error: brErr } = await supabase.from("branches").select("id, name, organization_id");
  console.log("Branches:", branches, brErr || "");

  // 2. Check edge devices
  const { data: devices, error: devErr } = await supabase.from("edge_devices").select("id, name, branch_id, status");
  console.log("Edge devices:", devices, devErr || "");

  // 3. Check members
  const { data: members, error: memErr } = await supabase.from("members").select("id, full_name, member_code, status").limit(5);
  console.log("Members (top 5):", members, memErr || "");

  // 4. Check device secrets via RPC or table
  const { data: secrets, error: secErr } = await supabase.from("edge_device_secrets").select("device_id, secret_hash, created_at, revoked_at");
  console.log("Device secrets count:", secrets ? secrets.length : 0, secErr || "");
}

run();

