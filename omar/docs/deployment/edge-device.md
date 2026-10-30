# Commissioning an edge device

## Host

- Ubuntu 22.04 LTS, NVIDIA driver ≥ 550, Docker ≥ 26, `nvidia-container-toolkit`, `chrony` enabled.
- Two NICs: `cam0` on the camera VLAN (no default route), `lan0` with HTTPS egress. Host firewall: allow inbound 8088/9090/3000 from the admin VLAN only; outbound 443 to the Supabase project host; RTSP (554) outbound on `cam0` only.
- Full-disk encryption (LUKS) on the data volume holding `/data` (queue + encrypted templates).

## Cloud registration (owner/admin, SQL editor or admin UI)

```sql
insert into public.edge_devices (branch_id, name) values ('<branch uuid>', 'edge-01') returning id;
-- then, signed in as owner/admin in the admin app (Devices → Issue new secret), or:
select public.issue_device_secret('<device uuid>');   -- shows the secret ONCE
```

Register cameras (`cameras` rows with `stream_url` **without** credentials), equipment, and zones through the admin app.

## Device configuration

```bash
git clone <repo> && cd ai-smart-gym
cp services/edge-ai/.env.example services/edge-ai/.env         # fill EDGE_DEVICE_ID, EDGE_BRANCH_ID, EDGE_DEVICE_SECRET, EDGE_INGEST_URL
python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"  # → EDGE_BIOMETRIC_KEY_B64 (store in TPM/KMS in production)
cp services/edge-ai/config/cameras.example.yaml services/edge-ai/config/cameras.yaml           # ids must match the cloud rows
cp services/edge-ai/config/camera-credentials.example.yaml services/edge-ai/config/camera-credentials.yaml && chmod 600 services/edge-ai/config/camera-credentials.yaml
mkdir -p secrets && openssl rand -base64 24 > secrets/grafana_admin_password
```

## Phase 0 probe (before any AI)

```bash
docker build -f infrastructure/docker/edge-ai.Dockerfile -t smartgym/edge-ai:0.1.0 .
docker run --rm -v $PWD/services/edge-ai/config:/config:ro smartgym/edge-ai:0.1.0 \
  edge-ai probe --cameras /config/cameras.yaml --secrets /config/camera-credentials.yaml --seconds 30 > docs/phase0/probe-$(date +%F).json
```

Acceptance: every camera `ok: true`, `measured_fps ≥ 12`, `read_failures` < 1 % of frames, resolution ≥ 1280×720, `open_latency_ms` < 3000. Commit the JSON as evidence.

## Run

```bash
docker compose -f infrastructure/docker/docker-compose.edge.yml --env-file services/edge-ai/.env up -d
curl -s localhost:8088/healthz | jq .
```

Place the chosen detector at `services/edge-ai/models/detector.onnx` (see `docs/architecture/model-selection.md`). Without a model the service runs in camera-health-only mode and says so in its logs.

## Operations

- Logs: `docker compose logs -f edge-ai` (JSON lines, no secrets).
- Metrics: Prometheus `localhost:9090`, Grafana `localhost:3000`.
- Secret rotation: issue a new secret in the admin app, update `.env`, `docker compose up -d edge-ai`; the old secret stays valid for 24 h.
- Upgrade: pull, rebuild image, `up -d`; the SQLite queue and templates persist in the `edge-data` volume.
