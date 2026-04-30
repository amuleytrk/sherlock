# Sherlock — Deployment Plan

> Goal: get Sherlock running as an internal tool reachable from any Trackonomy browser, with **maximum reuse of existing Azure infrastructure** and **minimum new spend**. No code changes are required for this plan — it is a configuration + infrastructure brief.

---

## TL;DR

| What | Where | Marginal cost / month |
|---|---|---|
| Compute (FastAPI + React) | New Pod on the **existing shared AKS cluster** | **$0** (uses spare node capacity) |
| Postgres + pgvector | New `sherlock` database on the **existing PG Flexible Server** (per env) | **$0** (database, not server) |
| Container image | Existing **Azure Container Registry** (`acrtrkmtv2shared.azurecr.io`) | **$0** |
| Identity (cross-cluster kubectl) | **Workload Identity + UAMI** federation, no kubeconfig secrets | **$0** |
| Secrets (API keys, DB creds) | **Azure Key Vault + CSI Secret Store driver** | < $1 |
| TLS + DNS | **cert-manager + Let's Encrypt + Azure Private DNS Zone** | **~$0.50** |
| Ingress | Reuse the **existing nginx-ingress** + internal LB | **$0** marginal |
| CI/CD | **GitHub Actions** (OIDC → ACR → AKS) | **$0** (within free minutes) |
| Periodic corpus refresh | **AKS CronJob** reusing the same image | **$0** |
| **Total new Azure infra** | | **< $10/month** |
| AI API costs (variable, by usage) | Anthropic + OpenAI | **~$150–300/month** at steady state with prompt caching |

**Key insight:** Sherlock plugs into infra Trackonomy already pays for. The only meaningful new spend is API usage, and that's bounded by prompt caching + Haiku-first routing.

---

## 1. Compute — AKS pod on the existing shared cluster

The shared cluster `aks-trk-mt-v2-shared-eastus2` (in `rg-mt-global-v2-eastus2`) already has node-pool headroom. Sherlock's footprint is small:

- **Resource requests:** 0.5 vCPU / 1 GiB
- **Limits:** 1 vCPU / 2 GiB
- **Replicas:** 1 (it's an internal tool; HPA can be added later if traffic justifies it)
- **Storage:** SQLite + investigations scratch dir → a `PersistentVolumeClaim` of 5 GiB on `default` (Azure Disk) storage class
- **Image:** built once and pushed to existing ACR, pulled via cluster's existing acrPull permission

This sits in its own namespace (e.g. `sherlock`) so it doesn't share the blast radius of `ppe`/`stage` workloads.

**Why not Azure Container Apps?** Plausible alternative — has scale-to-zero pricing — but the benefit is < $10/month and the migration introduces a second deployment surface for the team to learn. Stay with AKS for now.

---

## 2. Database — new `sherlock` DB on the existing PG Flexible Server

Trackonomy already runs Azure Database for PostgreSQL — Flexible Server for the new system migration (Stage on `trk-mt-nprd-sub`, PPE on `trk-mt-ppe-sub`). Add a database called `sherlock`, do not create a new server.

**Enable pgvector** ([MS Learn — Vector search on Azure PG](https://learn.microsoft.com/en-us/azure/postgresql/extensions/how-to-use-pgvector)):

```bash
# 1. Server parameter — adds VECTOR to the allowlist (one-time, requires server restart)
az postgres flexible-server parameter set \
  --resource-group <rg> --server-name <pg-server> \
  --name azure.extensions --value VECTOR

# 2. Create the database + extension
psql "host=<server>.postgres.database.azure.com user=<admin> dbname=postgres sslmode=require" \
  -c "CREATE DATABASE sherlock;"
psql "host=<server>.postgres.database.azure.com user=<admin> dbname=sherlock sslmode=require" \
  -c "CREATE EXTENSION vector;"
```

**3072-dim caveat — already handled.** Azure caps HNSW/IVFFlat indexes at **2,000 dimensions** per column. Sherlock's `text-embedding-3-large` is 3072-dim and the existing schema (`indexer/db.py`) already uses `halfvec(3072)` for HNSW indexing — fp16 storage doubles the indexable ceiling. **No code change needed.** ([MS Learn — pgvector performance limits](https://learn.microsoft.com/en-us/azure/postgresql/extensions/how-to-optimize-performance-pgvector).)

**Optional upgrade — DiskANN.** Microsoft ships a `pg_diskann` extension on Flexible Server that beats HNSW on speed × recall and supports up to 16,000 dims natively. Worth a one-line benchmark after deployment; if it wins, the halfvec workaround can be dropped. ([pg_diskann docs](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/how-to-use-pgdiskann).)

**Connection from Sherlock pod:** the pod's UAMI (see §3) gets `Reader` + a database-level role. Use Azure AD authentication via [psycopg-azure-ad-token](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/how-to-configure-sign-in-azure-ad-authentication) — no PG password in the pod.

**Schema deploy:** one-time `uv run python -m indexer.db` against the new database, run from a developer machine with VPN access (or from a one-shot K8s Job).

**Initial corpus build:** ~10 min wallclock + ~$1.20 in OpenAI embeddings (one time, plus periodic refreshes).

---

## 3. Identity — Workload Identity, no long-lived kubeconfigs

The existing dev workflow uses self-contained admin kubeconfigs per env. **In production, mounting those as Kubernetes Secrets is wrong** — they're long-lived bearer tokens and rotation is manual.

**The recommended pattern** ([MS Learn — Workload Identity](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview)):

1. **Create one User-Assigned Managed Identity** (UAMI) per Sherlock env: `uami-sherlock-ppe`, `uami-sherlock-stage`.
2. **Federate the K8s ServiceAccount with the UAMI's OIDC issuer** so the pod gets short-lived (1h) tokens automatically.
3. **Grant cross-cluster RBAC scoped to read-only:**
   - On every cluster Sherlock investigates (PPE AKS, Stage AKS): `Azure Kubernetes Service Cluster User Role` for the UAMI.
   - Inside each cluster: a `ClusterRoleBinding` mapping the UAMI's Entra object ID to a custom role with `pods/log get,list` and `pods get,list,watch`. Do **not** grant `view` (too broad — includes secrets).
4. **The pod's runtime fetches kubeconfigs on demand** via `azure-mgmt-containerservice.list_cluster_user_credentials()` — token rotates with the UAMI, no long-lived material in the cluster.

**Cross-subscription is fine** — UAMIs can hold role assignments in any subscription Sherlock needs to reach. RBAC policy mapping is the only per-cluster cost.

**Prerequisite:** all clusters Sherlock will read from need OIDC issuer + Workload Identity enabled (`--enable-oidc-issuer --enable-workload-identity`). Both `aks-trk-mt-v2-shared-eastus2` (stage) and the PPE cluster need this turned on if not already.

---

## 4. Secrets — Key Vault + CSI Secret Store

Anthropic API key, OpenAI API key, MSSQL/Cosmos/Redis credentials per env. **Don't put them in plain Kubernetes Secrets** — etcd-at-rest encryption is not on by default in shared clusters.

**Pattern:**

1. One Key Vault per env: `kv-sherlock-ppe`, `kv-sherlock-stage`.
2. Store secrets there; grant the UAMI from §3 `Key Vault Secrets User` role.
3. Install **Azure Key Vault Provider for Secrets Store CSI Driver** on the cluster (probably already there for other workloads — check with `kubectl get crd | grep secretproviderclass`).
4. Define a `SecretProviderClass` per env that maps Key Vault secrets into a CSI volume mounted at `/mnt/secrets`. Optionally mirror to a K8s Secret only if the app needs them as env vars (Sherlock does — pydantic-settings reads `.env` style variables). Rotation polling is built in (`--rotation-poll-interval=2m`).
5. **CI/CD never touches secrets** — Helm chart ships only the SecretProviderClass + Deployment manifests; values come from Key Vault at pod start.

**Cost:** Key Vault is ~$0.03 per 10,000 transactions. Sherlock makes a handful of fetches per pod start. Negligible.

---

## 5. Ingress — reuse existing nginx-ingress, internal LB

The shared cluster already runs nginx-ingress (visible to other Trackonomy services). Add a route:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: sherlock
  namespace: sherlock
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
    nginx.ingress.kubernetes.io/proxy-read-timeout: "300"   # SSE long-poll
    nginx.ingress.kubernetes.io/proxy-buffering: "off"      # SSE streaming
spec:
  ingressClassName: nginx-internal
  tls:
  - hosts: [sherlock.internal.trackonomy.com]
    secretName: sherlock-tls
  rules:
  - host: sherlock.internal.trackonomy.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: sherlock
            port: { number: 80 }
```

**TLS:** cert-manager + Let's Encrypt with **DNS-01 challenge** against an Azure Private DNS Zone. Free, automatic renewal, no public DNS exposure.

**DNS:** Azure Private DNS Zone (`internal.trackonomy.com`) — $0.50/zone/month + $0.40/M queries. Trivial. Map `sherlock.internal.trackonomy.com` → internal LB IP.

**Why not Application Gateway?** ~$125–250/month for a Standard_v2 with WAF. Overkill for an internal tool when nginx-ingress is already operational. ([App Gateway pricing](https://azure.microsoft.com/en-us/pricing/details/application-gateway/).)

**External access (if needed):** front it with the existing AppGW Trackonomy already operates for other internal-facing services rather than spinning up a new one. SSE keep-alive notes: [App Gateway for Containers SSE](https://learn.microsoft.com/en-us/azure/application-gateway/for-containers/server-sent-events).

---

## 6. CI/CD — GitHub Actions, OIDC to Azure

You're already on GitHub. **Don't introduce Azure Pipelines.** GitHub Actions is free for the typical build cadence (build ~3–5 min, well within free minutes for an internal repo).

**Workflow shape** (`.github/workflows/deploy.yml`, to be added later):

1. On push to `main`: lint → run `pytest` (157 tests, ~15s) → build the React bundle → build the multi-stage Docker image (FastAPI + bundled static assets).
2. **Auth to Azure via OIDC federation** to the same UAMI from §3 — `azure/login@v2` action, no stored secrets in GitHub.
3. `docker push` to ACR.
4. `kubectl set image deployment/sherlock sherlock=<new-tag> -n sherlock` (or Helm-based rollout).

**Alternative — ACR Tasks.** Builds inside Azure (no egress fee), free for first 6,000 build-minutes/month. Skip unless the build context grows large; loses GitHub-native PR checks.

---

## 7. Cron — AKS CronJob for corpus refresh

When Trackonomy release branches roll over, the indexer needs to re-run. AKS CronJob is the cheapest, simplest option — reuses the same image and the same Workload Identity:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata: { name: sherlock-indexer, namespace: sherlock }
spec:
  schedule: "0 4 * * 1"   # Monday 04:00 UTC
  jobTemplate:
    spec:
      template:
        spec:
          serviceAccountName: sherlock         # Workload Identity
          restartPolicy: OnFailure
          containers:
          - name: indexer
            image: acrtrkmtv2shared.azurecr.io/sherlock:<tag>
            command: ["uv", "run", "python", "-m", "indexer.run"]
            envFrom: [{ secretRef: { name: sherlock-secrets } }]
```

**Why not Azure Functions Timer?** Functions consumption plan does not yet support Python 3.13 (3.11 is current as of April 2026). Skip.

**Why not Logic Apps?** Massive overkill for a Python script.

---

## 8. AI API spend — realistic estimate

Current per-million-token rates:

| Model | Input | Output |
|---|---|---|
| Claude Haiku 4.5 | $1 | $5 |
| Claude Sonnet 4.6 | $3 | $15 |
| Claude Opus 4.7 | $5 | $25 |
| OpenAI text-embedding-3-large | $0.13 | (none) |

**Workload assumptions** (moderately optimistic for an internal tool with 30 daily users):

| Workflow | Volume/month | Tokens (rough) | Naive cost |
|---|---|---|---|
| Discovery queries | ~6,000 | 57M in / 9M out (Sonnet) | ~$306 |
| RCA (Sonnet primary) | ~810 | 20M in / 3.2M out | ~$108 |
| RCA (Opus escalation, 10%) | ~90 | 2.2M in / 0.36M out | ~$20 |
| Briefings | ~120 | 1.5M in / 0.36M out | ~$10 |
| Embeddings (4 refreshes) | 80M | — | ~$10 |
| **Naive total** | | | **~$455/mo** |

**Optimizations that bring this down to ~$150–250/mo:**

1. **Prompt caching** (5-min TTL, 1.25× write / 0.1× read) for the system prompt + corpus context shared across Discovery queries. In steady state cuts the input bill by 60–80%.
2. **Haiku 4.5 for triage / first-pass.** Already used as router and verifier; could be expanded to handle "easy" Discovery queries directly.
3. **Output token cap** — Sonnet RCA reports already capped at 4,000 output tokens. Verify caps are applied in code (`max_tokens` argument in agent loops).

**Realistic steady-state budget: $150–250/month.** Set up an Anthropic + OpenAI usage alert at $400 to catch runaway loops.

---

## 9. End-to-end deployment checklist

A concrete path to "Sherlock is live":

### One-time platform setup (DevOps, ~half a day)

- [ ] Confirm `aks-trk-mt-v2-shared-eastus2` + PPE AKS have `--enable-oidc-issuer --enable-workload-identity`.
- [ ] Create UAMIs: `uami-sherlock-ppe`, `uami-sherlock-stage`.
- [ ] Federate K8s ServiceAccount → UAMI per cluster.
- [ ] Grant cross-cluster RBAC (read-only `pods/log`, `pods get,list,watch`).
- [ ] Confirm Azure Key Vault Provider CSI driver is installed; if not, install via `helm install csi-secrets-store-provider-azure`.
- [ ] Create Key Vaults: `kv-sherlock-ppe`, `kv-sherlock-stage`. Populate secrets.
- [ ] Confirm nginx-ingress + cert-manager + Azure Private DNS Zone are in place; if not, set them up.
- [ ] Confirm `azure.extensions = VECTOR` on PG flex servers; create `sherlock` DB on each; `CREATE EXTENSION vector`.

### Sherlock-specific deploy (Sherlock owner, ~half a day)

- [ ] Add a `Dockerfile` to the repo (multi-stage: `node:20` for the Vite build, `python:3.13-slim` runtime).
- [ ] Add Helm chart or manifest set under `deploy/k8s/`: Deployment, Service, Ingress, ServiceAccount, SecretProviderClass, PVC, ConfigMap (for `SHERLOCK_ENVS=stage,ppe`).
- [ ] Add `.github/workflows/deploy.yml` with OIDC login, build, push, rollout.
- [ ] Run the indexer once against the new DB (one-shot K8s Job or developer machine).
- [ ] Smoke: visit `https://sherlock.internal.trackonomy.com`, confirm Briefings populates within ~30s of pod start.
- [ ] Add a CronJob for weekly corpus refresh.
- [ ] Add Azure Monitor alerts: pod restart count > 3 in 1h; Anthropic + OpenAI usage thresholds.

### Per-env credential population (you, < 30 min)

For each env (PPE, Stage):

- [ ] Generate the Sherlock-specific kubeconfig (or use Workload Identity tokens directly via `azure-mgmt-containerservice` SDK at runtime).
- [ ] Push MSSQL, Cosmos, Redis read-only creds to the env's Key Vault.
- [ ] Push Anthropic + OpenAI keys to Key Vault.

---

## 10. What this plan deliberately leaves out

- **High availability.** Single replica is fine for an internal tool; if Sherlock goes down for 5 minutes, no customer is affected. Add HPA + 2 replicas only when traffic justifies it.
- **Authentication / authorization.** Internal-DNS + corp-network gating is the assumed access control — Sherlock itself has no user model. If multi-tenant isolation becomes necessary, add Entra ID OIDC via the existing Auth0 / Azure AD setup later. (Reading sessions/audit log requires being inside the network.)
- **Observability beyond pod logs.** Sherlock has its own audit log + briefings. If deeper telemetry is needed, ship pod logs to the existing Log Analytics workspace via Container Insights (likely already on for the shared cluster).
- **Multi-region.** Single region (East US 2) is fine. If demand grows, the corpus is regional anyway (PG flex servers are pinned).
- **Quota / per-user rate limiting.** Add only if a runaway user is observed in practice.

---

## 11. References

- [Vector search on Azure Database for PostgreSQL — pgvector](https://learn.microsoft.com/en-us/azure/postgresql/extensions/how-to-use-pgvector)
- [pgvector performance + 2000-dim limit + halfvec](https://learn.microsoft.com/en-us/azure/postgresql/extensions/how-to-optimize-performance-pgvector)
- [pg_diskann for higher-dim ANN on Azure PG](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/how-to-use-pgdiskann)
- [AKS Workload Identity overview](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview)
- [Azure Key Vault Provider for Secrets Store CSI Driver](https://learn.microsoft.com/en-us/azure/aks/csi-secrets-store-driver)
- [Azure Database for PostgreSQL — Azure AD authentication](https://learn.microsoft.com/en-us/azure/postgresql/flexible-server/how-to-configure-sign-in-azure-ad-authentication)
- [App Gateway for Containers SSE keep-alive](https://learn.microsoft.com/en-us/azure/application-gateway/for-containers/server-sent-events)
- [Container Apps pricing](https://azure.microsoft.com/en-us/pricing/details/container-apps/)
- [Anthropic Claude pricing](https://platform.claude.com/docs/en/docs/about-claude/models/overview)
- [OpenAI API pricing](https://openai.com/api/pricing/)
