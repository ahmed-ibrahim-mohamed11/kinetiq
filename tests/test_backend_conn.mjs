// Test connectivity to Supabase Cloud
const url = "https://vpwudyxfsbyrbpwqdmew.supabase.co";
const anonKey = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZwd3VkeXhmc2J5cmJwd3FkbWV3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTMzNTkyMjUsImV4cCI6MjEwODkzNTIyNX0.8nwPrDld5wfapevY0N23zq8VMwovjpBpIb4kLoHJnss";

async function run() {
  console.log("Checking Supabase Cloud:", url);
  try {
    const res = await fetch(`${url}/rest/v1/`, {
      headers: { apikey: anonKey }
    });
    console.log("REST status:", res.status);
  } catch (err) {
    console.error("REST error:", err.message);
  }

  try {
    const res = await fetch(`${url}/functions/v1/ingest-events`, {
      method: "POST",
      headers: { "content-type": "application/json" }
    });
    console.log("Ingest Edge function status:", res.status);
    const text = await res.text();
    console.log("Ingest Edge function response:", text.slice(0, 200));
  } catch (err) {
    console.error("Ingest Edge function error:", err.message);
  }

  try {
    const res = await fetch(`${url}/functions/v1/device-sync`, {
      method: "GET"
    });
    console.log("Device-sync Edge function status:", res.status);
    const text = await res.text();
    console.log("Device-sync Edge function response:", text.slice(0, 200));
  } catch (err) {
    console.error("Device-sync Edge function error:", err.message);
  }
}

run();

