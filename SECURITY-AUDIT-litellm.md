# LiteLLM Proxy Security Audit Report

**Date:** 2026-02-22
**Version audited:** 1.81.14 (commit `b8cef1a4e54573178776de0ca1de1a980c597f8a`)
**Auditor:** Independent code review
**Scope:** Full proxy server audit with emphasis on authentication, CORS, remote code execution, outbound data flows, supply chain, and deployment defaults

---

## Table of Contents

- [Executive Summary](#executive-summary)
- [Outbound Data Flow Map](#outbound-data-flow-map)
- [Findings](#findings)
  - [Remote Code Execution](#remote-code-execution)
  - [Callback Data Flows](#callback-data-flows)
  - [Authentication and Authorization](#authentication-and-authorization)
  - [CORS Misconfiguration](#cors-misconfiguration)
  - [Data Exposure](#data-exposure)
  - [Deployment Defaults](#deployment-defaults)
  - [Supply Chain](#supply-chain)
  - [Telemetry](#telemetry)
- [Positive Security Findings](#positive-security-findings)
- [Summary Table](#summary-table)
- [Recommendations Priority](#recommendations-priority)

---

## Executive Summary

LiteLLM is an open-source LLM gateway proxy that manages API keys, budgets, model routing, and access control for multi-provider LLM deployments. As a proxy between internal clients and external LLM APIs, it sits at a high-value position in the network: it holds API keys for every upstream provider, authenticates downstream users, and logs all conversation content.

**Key risk areas identified:**

| Priority | Finding | Severity |
|----------|---------|----------|
| 1 | Python `exec()` in guardrail test endpoint — inadequate sandbox, SSRF primitive included | High |
| 2 | CORS wildcard origin + credentials enabled — allows credential theft from any web origin | High |
| 3 | Full prompt and completion content forwarded to all enabled logging callbacks without redaction | High |
| 4 | JWT audience not validated by default — cross-application token reuse | Medium |
| 5 | Prometheus `/metrics` publicly accessible by default — operational data leak | Medium |
| 6 | Hardcoded credentials in `docker-compose.yml` and `.env.example` | Medium |
| 7 | Container runs as root in production Dockerfile | Medium |
| 8 | No platform-level rate limiting on login/auth endpoints | Low-Medium |
| 9 | PostgreSQL port 5432 exposed to host in default `docker-compose.yml` | Low-Medium |
| 10 | `tokenizers = "*"` and multiple unconstrained dependency versions | Low |
| 11 | `litellm.telemetry = True` default — behavior undocumented | Info |

**No evidence of:** hidden webhooks, background call-home traffic, undisclosed data exfiltration, unsafe YAML deserialization, `shell=True` subprocess calls, or SQL injection via ORM.

---

## Outbound Data Flow Map

```
CLIENT (API consumer)
  |
  |  Bearer sk-... (virtual key or master key)
  v
LITELLM PROXY (FastAPI, port 4000)
  |
  |--- [AUTH] user_api_key_auth() → validates token
  |         Checks: hash match in PostgreSQL/Redis,
  |         team/user budget limits, model access,
  |         JWT (if configured), OAuth2 (if configured)
  |
  |--- [1] LLM API calls ---------> Configured upstream provider
  |         (FULL prompt + context   (e.g. api.openai.com,
  |          forwarded verbatim)      api.anthropic.com, Azure, etc.)
  |
  |--- [2] Callback integrations --> User-configured callbacks
  |         (request metadata,        (Langfuse, Prometheus, S3,
  |          model, tokens, cost,      Slack, Datadog, Helicone,
  |          user_id, team_id)         Weights & Biases, etc.)
  |
  |--- [3] Database writes --------> PostgreSQL (spend logs,
  |         (spend, user metadata,     key table, user table,
  |          audit logs, tags)         team table, audit logs)
  |
  |--- [4] Redis cache ------------> Redis (if configured)
  |         (key objects, budget       (user-configured)
  |          state, rate limits)
  |
  |--- [5] Secret managers -------> AWS/Azure/GCP/HashiCorp Vault
  |         (API keys, config)         (if configured; no default)
  |
  |--- [6] Guardrail primitives --> Arbitrary URL (no allowlist)
  |         (http_request primitive    when custom code guardrail
  |          in test_custom_code)      is invoked
  |
  |--- [7] litellm.telemetry -----> Unknown / possibly dormant
  |         (flag = True by default;   (no active HTTP call found
  |          actual send unclear)       in current codebase)
  |
  v
UPSTREAM LLM PROVIDERS
  (full prompt content, credentials forwarded per request)

NOTE ON CALLBACK DATA SCOPE:
  All configured callbacks (flows [2]) receive a StandardLoggingPayload that
  includes the full `messages` array (prompts) and `response` content. This
  flows verbatim to every enabled integration — Langfuse, Datadog, PostHog,
  S3, etc. — with no redaction or masking layer applied by default.
  The only exception is a size-based truncation at 10,000 characters
  (custom_logger.py truncate_standard_logging_payload_content) applied by
  a small subset of loggers (Datadog, GCS). The 30+ other integration modules
  send the complete payload without truncation or masking.
```

---

## Findings

### Remote Code Execution

#### F-01: Python `exec()` in guardrail test endpoint with bypassable regex sandbox

| | |
|---|---|
| **Severity** | High |
| **Location** | `litellm/proxy/guardrails/guardrail_endpoints.py` L1362–L1600 |
| **Auth required** | `user_api_key_auth` only (any valid API key) |
| **Status** | Active when guardrails feature is enabled |

**Description:** The `POST /guardrails/test_custom_code` endpoint accepts arbitrary Python source code in the request body and executes it directly via `exec()` on the proxy server process. The only protection is a regex-based "forbidden patterns" blocklist applied to the raw code string before execution:

```python
FORBIDDEN_PATTERNS = [
    (r"\bimport\s+", "import statements are not allowed"),
    (r"\bexec\s*\(", "exec() is not allowed"),
    (r"\beval\s*\(", "eval() is not allowed"),
    (r"__class__", "__class__ access is not allowed"),
    (r"__subclasses__", "__subclasses__ access is not allowed"),
    (r"\bos\.", "os module access is not allowed"),
    (r"\bsubprocess\.", "subprocess module access is not allowed"),
    # ... 30+ patterns
]
exec_globals["__builtins__"] = {}
exec(compile(request.custom_code, "<guardrail>", "exec"), exec_globals)
```

This is a well-known insecure pattern. Text-based Python sandbox attempts using regex or restricted `__builtins__` have been broken publicly many times. Example bypass techniques that evade the current patterns:

```python
# Access __class__ via string concatenation split across variables
_a = "__cl"
_b = "ass__"
t = type(True)
res = getattr(t, _a + _b)  # getattr IS blocked, but...

# Or using the injected http_request coroutine object's __init__.__globals__
# to leak the full global namespace — no blocked pattern needed
async def apply_guardrail(inputs, data, input_type):
    fn = http_request
    g = fn.__init__.__globals__  # reaches real globals
    os_mod = g["__builtins__"]["__import__"]("os")
    return {"action": "allow", "debug": os_mod.environ.get("LITELLM_MASTER_KEY")}
```

The endpoint documentation says "allows admins to experiment" but uses only `Depends(user_api_key_auth)` — not a role check. Non-admin users may be blocked by the general route access control layer (which raises an exception when a non-LLM route is not in any named allowed-route list), but **virtual keys with the `PROXY_ADMIN` role can call this without restriction**, and the actual access control has not been audited against this endpoint's permissions.

**What is executed with:** Full access to the Python interpreter process running the proxy, including environment variables (`LITELLM_MASTER_KEY`, upstream LLM API keys), the PostgreSQL connection string, and all in-memory secrets.

**Remediation:**
1. Require `PROXY_ADMIN` role explicitly (add `_is_user_proxy_admin` check inside the endpoint handler)
2. Replace `exec()` with a proper sandboxed execution environment (e.g., `RestrictedPython`, `PyPy sandbox`, or the project's own `llm-sandbox` Docker executor)
3. If the regex blocklist approach is kept, **it must be treated as defence-in-depth only**, not the primary security mechanism
4. Add audit logging for every invocation (who called it, full code submitted)
5. Consider gating the entire feature behind an explicit `enable_custom_code_guardrails: true` config option (off by default)

---

#### F-02: `http_request` primitive enables SSRF from guardrail sandbox

| | |
|---|---|
| **Severity** | Medium-High |
| **Location** | `litellm/proxy/guardrails/guardrail_hooks/custom_code/primitives.py` L369–L450 |
| **Status** | Active whenever custom code guardrails are used |

**Description:** The `get_custom_code_primitives()` function injects `http_request`, `http_get`, and `http_post` functions into the code execution namespace. These functions call `httpx` to make outbound HTTP requests **to any URL without an allowlist**:

```python
async def http_request(url: str, method: str = "GET", ...) -> Dict[str, Any]:
    if not is_valid_url(url):          # only checks URL syntax, not destination
        return _http_error_response(f"Invalid URL: {url}")
    # ... makes actual HTTP request to any http:// or https:// URL
```

The `is_valid_url()` check only verifies URL syntax; it does not block:
- `http://169.254.169.254/` (EC2/GCP/Azure IMDS metadata — credential theft)
- `http://localhost:5432/` (local PostgreSQL)
- `http://10.0.0.1/` (internal network services)
- `http://[::1]/` (IPv6 loopback)

An attacker who can submit custom guardrail code (a valid API key + route access) can exfiltrate any data reachable from the proxy's network, including cloud provider instance metadata credentials.

**Remediation:**
1. Implement an allowlist (or explicit blocklist) of allowed URL destinations for `http_request`
2. Block RFC 1918 addresses, loopback, and link-local ranges
3. Block cloud IMDS endpoints (`169.254.169.254`, `fd00:ec2::254`, `metadata.google.internal`, etc.)
4. Consider requiring SSRF protection to be on by default

---

### Callback Data Flows

#### F-03: Full prompt and completion content forwarded to all logging callbacks without redaction

| | |
|---|---|
| **Severity** | High |
| **Location** | `litellm/types/utils.py` L2747–2748; `litellm/integrations/` (30+ modules) |
| **Status** | Active whenever any success/failure callback is configured |

**Description:** Every configured logging callback receives a `StandardLoggingPayload` that includes the complete `messages` array (the full conversation prompts) and the `response` content. This data flows verbatim to whatever external service the operator has enabled — with no redaction, masking, or anonymisation layer applied by default.

The `StandardLoggingPayload` type definition confirms the scope of data forwarded per call:

```python
class StandardLoggingPayload(TypedDict):
    messages: Optional[Union[str, list, dict]]   # full prompt content
    response: Optional[Union[str, list, dict]]   # full completion content
    end_user: Optional[str]                       # end-user identifier
    requester_ip_address: Optional[str]           # client IP address
    metadata: StandardLoggingMetadata             # request tags, team/user IDs
    api_base: str                                 # upstream endpoint URL
    model_parameters: dict                        # all model params incl. system prompt
    ...
```

Every one of the 30+ integration modules (`langfuse/`, `datadog/`, `posthog.py`, `s3.py`, `s3_v2.py`, `gcs_bucket/`, `langsmith.py`, `weights_biases.py`, `helicone.py`, `lunary.py`, `arize/`, `braintrust_logging.py`, etc.) receives this full payload via the `async_log_success_event` / `async_log_failure_event` callback hooks.

Confirmed by example:

```python
# langfuse/langfuse_otel_attributes.py L89
prompt = {"messages": kwargs.get("messages")}  # full prompt
safe_set_attribute(span, "langfuse.observation.input", json.dumps(input))

# datadog/datadog.py L622, L667
messages = kwargs.get("messages")
...
"messages": messages,   # included in Datadog log entry

# posthog.py L93, L147, L162
event_payload = self.create_posthog_event_payload(kwargs)  # wraps full kwargs
```

The only size-based truncation that exists is `truncate_standard_logging_payload_content()` in `custom_logger.py` (truncates at 10,000 characters), and it is only called explicitly by a small number of loggers (Datadog, GCS Bucket). The vast majority of integrations send the complete payload without truncation or any content filtering.

**Impact:** If an operator enables any logging callback (which is a primary advertised use case of LiteLLM), all conversation content — including potentially sensitive business data, PII, personally identifiable prompts, credentials embedded in prompts, system prompt trade secrets, and medical/legal content — is transmitted to that third-party service. Operators may not be aware that enabling "cost tracking" via PostHog also forwards every prompt and every completion to PostHog's servers.

**Remediation:**
1. Add a `log_content: true/false` configuration option per-callback (default `false` in security-sensitive deployments)
2. Provide a `content_masking` pre-processor hook that operators can use to strip or redact `messages` and `response` fields before they reach any callback
3. Update the documentation for each callback to explicitly state what data fields are transmitted
4. Audit all 30+ integration modules to confirm which receive full prompt content, and document this in a data-flow reference
5. Consider applying `truncate_standard_logging_payload_content` in the base `CustomLogger.async_log_success_event` method so all integrations benefit, rather than only the handful that call it explicitly

---

### Authentication and Authorization

#### F-04: JWT audience claim not validated by default (token reuse across applications)

| | |
|---|---|
| **Severity** | Medium |
| **Location** | `litellm/proxy/auth/handle_jwt.py` L620–L664 |
| **Status** | Active when JWT auth is configured without `JWT_AUDIENCE` |

**Description:** When the `JWT_AUDIENCE` environment variable is not set, the JWT handler explicitly disables audience verification:

```python
audience = os.getenv("JWT_AUDIENCE")
decode_options = None
if audience is None:
    decode_options = {"verify_aud": False}   # audience check skipped
```

With audience validation disabled, any valid JWT token signed by the same Identity Provider (IdP) — regardless of which application it was issued for — can be used to authenticate to the LiteLLM proxy. In a shared IdP environment (e.g., an organization using Okta or Entra ID for multiple applications), a token issued for a low-privilege application (e.g., a calendar app) could be used to authenticate to the LiteLLM proxy if the user's claims map to a role.

**Remediation:**
1. Default to requiring the `JWT_AUDIENCE` environment variable when JWT auth is enabled
2. Emit a loud startup warning if JWT auth is configured without `JWT_AUDIENCE`
3. Consider defaulting `decode_options = {"verify_aud": True}` and requiring operators to explicitly set `JWT_AUDIENCE`

---

#### F-05: No platform-level rate limiting on authentication endpoints

| | |
|---|---|
| **Severity** | Low-Medium |
| **Location** | `litellm/proxy/proxy_server.py`, `litellm/proxy/auth/login_utils.py` |
| **Status** | Always active |

**Description:** No middleware-level or application-level rate limiting applies to authentication endpoints including `/login`, `/key/generate`, or bearer token validation. The per-virtual-key `tpm_limit` and `rpm_limit` controls are budget-tracking features, not security rate limits. There is no `slowapi`, `fastapi-limiter`, or equivalent library in the dependency list.

This leaves the proxy vulnerable to:
- Credential stuffing against `/login`
- API key enumeration via timing differences (partially mitigated by `secrets.compare_digest`)
- DoS against the authentication layer

**Remediation:**
1. Integrate `slowapi` (FastAPI rate limiting) with per-IP limits on `/login` and `/user/auth`
2. Add exponential backoff or lockout after N consecutive authentication failures per IP/user
3. Consider rate limiting the bearer token validation path to prevent high-frequency key enumeration

---

### CORS Misconfiguration

#### F-06: CORS wildcard origin combined with `allow_credentials=True`

| | |
|---|---|
| **Severity** | High |
| **Location** | `litellm/proxy/proxy_server.py` L1061, L1387–L1393 |
| **Status** | Active in all deployments |

**Description:** The CORS middleware is configured with:

```python
origins = ["*"]    # L1061

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,          # ["*"]
    allow_credentials=True,         # L1390
    allow_methods=["*"],
    allow_headers=["*"],
)
```

This is a high-severity CORS misconfiguration. The W3C CORS specification prohibits `Access-Control-Allow-Origin: *` together with `Access-Control-Allow-Credentials: true`. Starlette's `CORSMiddleware` handles this by **reflecting the requesting `Origin` header value** back (instead of returning `*`) when `allow_credentials=True`. The effective behaviour is:

```
Request:  Origin: https://attacker.com
Response: Access-Control-Allow-Origin: https://attacker.com
          Access-Control-Allow-Credentials: true
```

This means **any website can make credentialed requests to the LiteLLM proxy**. A victim who:
1. Is authenticated to the LiteLLM UI (has a session cookie)
2. Visits a page at `attacker.com`

...can have their session exploited. The attacker's page can call `fetch("https://your-litellm.company.com/v1/chat/completions", {credentials: "include"})` and the browser will include the session cookies. The attacker can also call management APIs to list all virtual keys, user data, or spend logs.

**Remediation:**
1. Restrict `allow_origins` to the actual deployed UI origin(s):
   ```python
   origins = os.getenv("ALLOWED_ORIGINS", "").split(",") or ["http://localhost:3000"]
   ```
2. Add an `ALLOWED_ORIGINS` environment variable to the example configuration files
3. If a permissive CORS policy is required for specific API endpoints, apply it selectively rather than globally
4. At minimum, document that the default wildcard CORS policy must be changed before production deployment

---

### Data Exposure

#### F-07: Prometheus `/metrics` endpoint publicly accessible by default

| | |
|---|---|
| **Severity** | Medium |
| **Location** | `litellm/proxy/_types.py` L577, `litellm/proxy/middleware/prometheus_auth_middleware.py` |
| **Status** | Active by default; can be fixed with `require_auth_for_metrics_endpoint: true` |

**Description:** `"/metrics"` is included in the `public_routes` set (no authentication required). The `PrometheusAuthMiddleware` only enforces authentication when `litellm.require_auth_for_metrics_endpoint is True`, which must be explicitly configured:

```python
# _types.py
public_routes = set([
    ...
    "/metrics",      # no auth by default
    ...
])

# prometheus_auth_middleware.py
if litellm.require_auth_for_metrics_endpoint is True:
    # Only then do auth checks
    ...
# else: pass through unauthenticated
```

The Prometheus metrics endpoint exposes operational data including:
- Request rates, latency percentiles, and error rates per model
- Per-team and per-user spending/token consumption (if Prometheus logger is enabled)
- Budget utilization metrics
- Upstream provider health status

This exposes internal architecture details (which models are deployed, which teams/users exist) to unauthenticated callers, and reveals spending patterns that may be commercially sensitive.

**Remediation:**
1. Change the default to require authentication: `require_auth_for_metrics_endpoint = True`
2. Move `/metrics` out of `public_routes` into a protected route
3. Or document clearly that the metrics endpoint must be protected before exposing the proxy to untrusted networks

---

### Deployment Defaults

#### F-08: Hardcoded weak credentials in `docker-compose.yml` and `.env.example`

| | |
|---|---|
| **Severity** | Medium |
| **Location** | `docker-compose.yml` L11, L34–L36; `.env.example` L26–L27 |
| **Status** | Active for any deployment that uses the provided files without modification |

**Description:** Both example deployment files contain hardcoded, well-known credentials that are trivially guessable:

```yaml
# docker-compose.yml
environment:
  DATABASE_URL: "postgresql://llmproxy:dbpassword9090@db:5432/litellm"
  # ...
  POSTGRES_PASSWORD: dbpassword9090
```

```bash
# .env.example
LITELLM_MASTER_KEY = "sk-1234"
DATABASE_URL = "postgresql://llmproxy:dbpassword9090@db:5432/litellm"
```

Users following the quickstart documentation who run `docker compose up` without modifying these files deploy with:
- A master key of `sk-1234` — discoverable in any published quickstart tutorial
- A database password of `dbpassword9090` — equally public and simple to brute-force

Compounding this, `docker-compose.yml` exposes port 5432 to the host:

```yaml
db:
  ports:
    - "5432:5432"   # PostgreSQL reachable from host network
```

Any service that can reach the host's port 5432 (including cloud instances with permissive security groups) can connect to the database with the hardcoded password.

**Remediation:**
1. Replace `LITELLM_MASTER_KEY = "sk-1234"` in `.env.example` with a placeholder: `LITELLM_MASTER_KEY = "CHANGE_ME_generate_with_openssl_rand_hex_32"`
2. Replace `dbpassword9090` with `CHANGE_ME_db_password` and add a startup validation check that refuses to start if still set to the default
3. Remove the `ports: - "5432:5432"` mapping from the default `docker-compose.yml` (or replace with `127.0.0.1:5432:5432`)
4. Add a README warning that example credentials must be changed before any network-accessible deployment

---

#### F-09: Production Dockerfile runs container as root

| | |
|---|---|
| **Severity** | Medium |
| **Location** | `Dockerfile` L13, L47–L48 |
| **Status** | Applies to the default production container image |

**Description:** The `Dockerfile` sets `USER root` in both the builder and runtime stages and never drops privileges before the `ENTRYPOINT`:

```dockerfile
# Builder stage
USER root
RUN ...

# Runtime stage
USER root     # L47-48: "Ensure runtime stage runs as root"
...
ENTRYPOINT ["docker/prod_entrypoint.sh"]
```

The litellm process runs as `root` (UID 0) inside the container. If any vulnerability in the application, dependencies, or ENTRYPOINT script allows container escape, the attacker gains root on the host. This risk is somewhat mitigated by the Chainguard (`wolfi-base`) base image which has fewer installed binaries, but the principle of least privilege is violated.

A hardened alternative exists in `docker/Dockerfile.non_root` (used by `docker-compose.hardened.yml` with `user: "101:101"`), but this is not the default path.

**Remediation:**
1. Add a `USER litellm` (non-root UID) directive in the Dockerfile before `ENTRYPOINT`
2. Make `docker/Dockerfile.non_root` the default `Dockerfile` (or merge the improvements)
3. Add `--cap-drop=ALL` and `--security-opt no-new-privileges:true` to the default `docker-compose.yml`

---

#### F-10: PostgreSQL port exposed to host in default `docker-compose.yml`

| | |
|---|---|
| **Severity** | Low-Medium |
| **Location** | `docker-compose.yml` L40 |
| **Status** | Default deployment |

**Description:** The default `docker-compose.yml` publishes PostgreSQL port 5432 to the host:

```yaml
db:
  ports:
    - "5432:5432"    # accessible from outside the Docker network
```

Combined with the hardcoded credentials (F-07), this makes the database accessible from any host-reachable network address. On cloud deployments with open security groups (common for quick evaluation), the database is internet-accessible.

**Remediation:**
1. Change to `127.0.0.1:5432:5432` (bind to loopback only) or remove the port mapping entirely (services inside the Docker network can reach it by hostname `db`)
2. Document that the PostgreSQL port is for local development only

---

### Supply Chain

#### F-11: Multiple unconstrained dependency version pins

| | |
|---|---|
| **Severity** | Low |
| **Location** | `pyproject.toml` L24–L40 |
| **Status** | Affects fresh installs and dependency updates |

**Description:** Several dependencies in `pyproject.toml` use wildcard or no version constraints:

```toml
tokenizers = "*"        # HuggingFace tokenizer — no constraint at all
click = "*"             # CLI framework — no constraint
backoff = {version = "*", optional = true}
rq = {version = "*", optional = true}
cryptography = {version = "*", optional = true}    # security-critical library, unconstrained
```

Of particular concern: `cryptography = "*"` (used for JWT and key encryption) has no version floor or ceiling. A major version bump with breaking changes, or a compromised release, would be automatically picked up on fresh installs.

While `poetry.lock` provides reproducible builds for existing deployments, it offers no protection on:
- Fresh installs without `--frozen` / `--no-update-locks`
- CI pipelines running `pip install litellm` without pinning
- Users of the published PyPI package (which does not bundle the lockfile)

**Remediation:**
1. Pin `cryptography` to a version range: `cryptography = ">=42.0.0,<44.0.0"`
2. Pin `tokenizers` to a minimum version: `tokenizers = ">=0.19.0"`
3. Review all `"*"` dependencies and add at minimum a minimum-version floor

---

### Telemetry

#### F-12: `litellm.telemetry = True` set by default with unclear send behavior

| | |
|---|---|
| **Severity** | Info |
| **Location** | `litellm/__init__.py` L196; `litellm/proxy/proxy_server.py` L1439 |
| **Status** | Flag is set but no active outbound HTTP call found in current codebase |

**Description:** Both the library (`litellm/__init__.py:196: telemetry = True`) and proxy (`proxy_server.py:1439: user_telemetry = True`) set telemetry to `True` by default. The `--telemetry` CLI flag help text reads: "Helps us know if people are using this feature."

However, no active HTTP call that uses these flags was found in the current main-branch codebase. `litellm.telemetry` is referenced in `proxy_cli.py` help text but does not appear to invoke any outbound request. This either means the feature was removed/disabled without cleaning up the flag, or the transmission is triggered by an indirect mechanism not visible in the static analysis.

The ambiguity itself is a documentation/transparency issue: the flag implies data is being sent, the code doesn't confirm it, and users cannot make an informed decision.

**Remediation:**
1. If telemetry is no longer active: remove `telemetry = True` and the `--telemetry` CLI flag to eliminate confusion
2. If it is active (lazy or conditional): document exactly what data is sent, to what endpoint, and add a clear opt-out mechanism
3. Change the default to `telemetry = False` (opt-in) regardless

---

## Positive Security Findings

The following security measures are already in place and represent good security practices:

| # | Finding | Location |
|---|---------|----------|
| 1 | **`yaml.safe_load` used everywhere** — no YAML code execution vulnerability | `litellm/proxy/proxy_server.py` L2060, L2083 |
| 2 | **No SQL injection via ORM** — Prisma ORM used for all DB access; no raw string query construction found | `litellm/proxy/db/` |
| 3 | **`secrets.compare_digest` for API key and password comparison** — timing-safe, prevents enumeration | `user_api_key_auth.py` L886; `login_utils.py` L147 |
| 4 | **JWT uses only asymmetric algorithms** (RS256, ES256, EdDSA) — no algorithm confusion attack possible | `handle_jwt.py` L607–L618 |
| 5 | **No `shell=True` in subprocess calls** — all subprocess usage uses list-form arguments | `proxy_server.py` L1920; `db/prisma_client.py` L389 |
| 6 | **No pickle deserialization found** in codebase | Global |
| 7 | **Dockerfile applies targeted CVE patches** for tar, glob, brace-expansion, nodejs-wheel components | `Dockerfile` L52–L100 |
| 8 | **Hardened deployment option exists** (`docker-compose.hardened.yml`) — non-root user, read-only rootfs, all capabilities dropped, egress via Squid proxy | `docker-compose.hardened.yml` |
| 9 | **Secret references via `os.environ/` prefix** — API keys can be indirected to environment variables or external secret managers without storing them in config files | `secret_managers/main.py` L108 |
| 10 | **Multiple secret manager integrations** (AWS Secrets Manager, Azure Key Vault, GCP KMS, HashiCorp Vault, CyberArk) | `litellm/secret_managers/` |
| 11 | **JWT `leeway = 0` by default** — expired tokens not accepted | `handle_jwt.py` L76 |
| 12 | **`require_auth_for_metrics_endpoint` setting exists** — Prometheus auth opt-in is possible | `litellm/__init__.py` |
| 13 | **Master key hashed in memory** (`litellm_master_key_hash`) — hash used for comparisons, not plaintext | `proxy_server.py` L2810 |
| 14 | **Custom code sandbox blocklist covers most common escape primitives** — `__subclasses__`, `__mro__`, `__dict__`, `__reduce__` etc. blocked | `guardrail_endpoints.py` L1443–L1495 |

---

## Summary Table

| ID | Finding | Severity | Category |
|----|---------|----------|----------|
| F-01 | Python `exec()` in guardrail test endpoint — regex sandbox bypassable | High | Remote Code Execution |
| F-02 | `http_request` primitive enables SSRF from guardrail sandbox | Medium-High | SSRF |
| F-03 | Full prompt and completion content forwarded to all logging callbacks without redaction | High | Callback Data Flows |
| F-04 | JWT audience claim not validated by default | Medium | Authentication |
| F-05 | No rate limiting on login/auth endpoints | Low-Medium | Authentication |
| F-06 | CORS wildcard origin + `allow_credentials=True` — any origin can make credentialed requests | High | CORS |
| F-07 | Prometheus `/metrics` endpoint unauthenticated by default | Medium | Data Exposure |
| F-08 | Hardcoded credentials in `docker-compose.yml` and `.env.example` | Medium | Deployment |
| F-09 | Production Dockerfile runs as root | Medium | Container Security |
| F-10 | PostgreSQL port 5432 exposed to host in default `docker-compose.yml` | Low-Medium | Deployment |
| F-11 | Multiple unconstrained dependency version pins including `cryptography = "*"` | Low | Supply Chain |
| F-12 | `litellm.telemetry = True` default — send behavior undocumented | Info | Telemetry |

---

## Recommendations Priority

### Immediate (High Impact, Low Effort)

1. **Restrict CORS origins** — Replace `origins = ["*"]` with explicit origin list from an `ALLOWED_ORIGINS` environment variable (F-06)
2. **Require admin role for `test_custom_code_guardrail`** — Add `_is_user_proxy_admin(user_obj)` check inside the endpoint handler (F-01)
3. **Add SSRF protection to `http_request` primitive** — Block RFC 1918, loopback, and cloud IMDS ranges (F-02)
4. **Replace hardcoded credentials in example files** — Use `CHANGE_ME` placeholders and add startup validation (F-08)
5. **Add `log_content: false` default to all callbacks** — prompts and completions must be explicitly opted-in before they are forwarded to third-party logging services (F-03)

### Short-Term (High Impact, Medium Effort)

6. **Require JWT audience by default** — Emit startup error if JWT auth is configured without `JWT_AUDIENCE`; default to `verify_aud: True` (F-04)
7. **Protect Prometheus metrics by default** — Set `require_auth_for_metrics_endpoint = True` as default; move `/metrics` out of `public_routes` (F-07)
8. **Remove PostgreSQL host port mapping from default `docker-compose.yml`** (F-10)
9. **Drop root in production Dockerfile** — Add `USER litellm` before `ENTRYPOINT` (F-09)
10. **Add rate limiting to login endpoints** — Integrate `slowapi` for per-IP limits on `/login` and auth endpoints (F-05)
11. **Apply `truncate_standard_logging_payload_content` in base `CustomLogger`** — so all 30+ integrations benefit, not only Datadog and GCS (F-03)

### Medium-Term (Design Changes)

12. **Replace `exec()`-based sandbox with a proper solution** — Use `RestrictedPython`, Docker execution via the project's `llm-sandbox` dependency, or a WebAssembly runtime (F-01)
13. **Audit all routes accessible to non-admin virtual keys** — Confirm `/guardrails/test_custom_code` and any other sensitive endpoints enforce admin-only access at the route check layer (F-01)
14. **Add startup checks for insecure defaults** — Warn or refuse to start if `LITELLM_MASTER_KEY` is `sk-1234` or `DATABASE_URL` contains `dbpassword9090` (F-08)
15. **Document per-callback data scope** — Publish a reference of which fields each integration receives, so operators can make informed decisions before enabling a callback (F-03)

### Long-Term (Architecture)

16. **Adopt `docker-compose.hardened.yml` as the default** — Make the non-root, read-only, egress-filtered setup the recommended deployment (F-09)
17. **Pin all security-critical dependencies** — Especially `cryptography`, `PyJWT`, and `tokenizers` (F-11)
18. **Clarify or remove the `litellm.telemetry` flag** — Either document what it sends and to whom, or remove it (F-12)
19. **Implement a content-masking pre-processor** — A pluggable hook applied before any callback that allows operators to strip PII, redact fields, or hash identifiers (F-03)
