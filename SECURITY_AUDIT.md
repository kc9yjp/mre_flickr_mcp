# Security Audit Report

**Date:** August 19, 2026
**Targets:**
- `https://mre-workbench.mermaid-ghost.ts.net` (Tailscale) — full scan completed
- `https://flick-mcp.photo516.me` (Cloudflare Tunnel) — full scan completed

---

## TLS/SSL — Grade A+ (testssl.sh)

**Strong.** No critical issues.

| Check | Tailscale | Cloudflare |
|-------|-----------|------------|
| SSLv2/SSLv3/TLS 1.0/1.1 | Not offered | TLS 1.0/1.1 available via Cloudflare (see below) |
| TLS 1.2 + 1.3 | Both offered | Both offered |
| Forward Secrecy | Yes (all ciphers) | Yes |
| Heartbleed / CCS / ROBOT / BEAST / POODLE / SWEET32 / FREAK / DROWN | Not vulnerable | N/A (Cloudflare edge) |
| Certificate | Let's Encrypt EC 256-bit, valid | Wildcard `*.photo516.me` (Google Trust Services), valid |
| **LUCKY13 (CVE-2013-0169)** | **Potentially vulnerable** — CBC ciphers present on TLS 1.2 | N/A (Cloudflare-managed) |
| **Weak cipher suites** | N/A | TLS 1.0 `TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA`, TLS 1.1 `TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA` |

---

## Missing Security Headers

nuclei confirmed **zero** of the standard security headers are present on both targets:

| Header | Status | Risk |
|--------|--------|------|
| `Strict-Transport-Security` | **Missing** | No HSTS — browser won't enforce HTTPS |
| `Content-Security-Policy` | **Missing** | No XSS mitigation |
| `X-Content-Type-Options` | **Missing** | MIME sniffing risk |
| `X-Frame-Options` | **Missing** | Clickjacking risk |
| `Referrer-Policy` | **Missing** | Referrer leakage |
| `Permissions-Policy` | **Missing** | No feature policy |
| `Cross-Origin-Opener-Policy` | **Missing** | No cross-origin isolation |
| `Cross-Origin-Resource-Policy` | **Missing** | No resource policy |
| `Cross-Origin-Embedder-Policy` | **Missing** | No embedder policy |
| `X-Permitted-Cross-Domain-Policies` | **Missing** | No cross-domain policy |

---

## Authentication & Session — Good

| Test | Tailscale | Cloudflare |
|------|-----------|------------|
| Unauthenticated `/app` | 307 → `/login` | 404 (tunnel scoped) |
| Unauthenticated `/api/*` | 401 | 404 (tunnel scoped) |
| MCP endpoints without API key | 401 | 401 |
| Session cookie flags | `httponly`, `secure`, `samesite=lax` | `httponly`, `secure`, `samesite=lax` |
| Session fixation via `/oauth/callback` | 307 → `/login?msg=err` (rejected) | N/A |
| Host header injection | No effect | 403 from Cloudflare |
| X-Forwarded-For injection | No effect | N/A |

---

## CSRF — Good

| Test | Result |
|------|--------|
| POST `/api/sync` without CSRF token | 403 (blocked) |
| POST `/api/sync` with fake CSRF token | 403 (blocked) |
| Chat API POST without session | CSRF validation failed |

---

## Cloudflare Tunnel Path Scoping

The Cloudflare tunnel is **properly scoped** — only specific paths reach the backend:

| Path | Reaches app? | Notes |
|------|-------------|-------|
| `/login` | Yes (200) | Login page served |
| `/mcp` | Yes (401) | MCP endpoint reachable from internet |
| `/.well-known/oauth-authorization-server` | Yes (200) | OAuth discovery reachable |
| `/app` | No (404) | Blocked by tunnel rules |
| `/api/*` | No (404) | All API endpoints blocked |
| `/sse` | No (404) | SSE transport blocked |
| `/messages` | No (404) | Legacy MCP endpoint blocked |

---

## Information Disclosure — Low Risk

| Finding | Detail |
|---------|--------|
| Server header: `uvicorn` (Tailscale) | Leaks server software. Add middleware to suppress. |
| Server header: `cloudflare` (Cloudflare) | Properly hidden behind Cloudflare edge. |
| OAuth2 discovery endpoint | Exposed by design — lists all endpoints, PKCE S256 required |
| OAuth2 client registration | Open by design (RFC 7591 compliant) |
| API error messages | `str(e)` in JSON responses may leak internal paths/URLs to authenticated users |

---

## Endpoint Fuzzing (ffuf)

Only discovered expected endpoints from the wordlist:

| Path | Status | Notes |
|------|--------|-------|
| `/login` | 200 | Expected |
| `/.well-known/oauth-authorization-server` | 200 | Expected (OAuth 2.1) |

No hidden admin panels, config files, debug endpoints, or `.env`/`.git` exposure.

---

## Vulnerability Scan (nuclei)

- **0 critical/high/medium findings** on Tailscale target
- **1 low finding** on Cloudflare (TLS 1.0/1.1 cipher suites — Cloudflare-managed)
- 11 info-level findings (all missing headers, covered above)
- No CVEs, RCE, LFI, SSTI, or known exploits detected

---

## Port Scan (nmap)

### Tailscale target

| Port | Service | Notes |
|------|---------|-------|
| 443/tcp | HTTPS (uvicorn via Go net/http — Tailscale serve) | Only port open |
| 8000/tcp | HTTP (uvicorn) | **Exposed directly — not behind TLS** |

### Cloudflare target

| Port | Service | Notes |
|------|---------|-------|
| 80/tcp | HTTP | Cloudflare edge |
| 443/tcp | HTTPS | Cloudflare edge |
| 8080/tcp | HTTPS | Cloudflare edge |
| 8443/tcp | HTTPS | Cloudflare edge |

---

## Summary by Severity

| Severity | Count | Findings |
|----------|-------|----------|
| **Medium** | 3 | LUCKY13 CBC ciphers, 10 missing security headers (collectively), port 8000 exposed on Tailscale |
| **Low** | 3 | Server header disclosure, `str(e)` in API error responses, TLS 1.0/1.1 on Cloudflare |
| **Info** | 11 | OAuth discovery, missing HSTS details, session cookie 30-day expiry |
| **None (passed)** | 14 | Auth gating, CSRF, SQL injection, XSS, path traversal, command injection, session fixation, CORS, IDOR, rate limiting |

---

## Recommended Fixes

### Priority 1 — Add security headers

Add these via Starlette middleware in `scripts/web.py`:

```python
@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' https://live.staticflickr.com data:; connect-src 'self' https://api.flickr.com"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    return response
```

### Priority 2 — Remove TLS 1.2 CBC ciphers (Tailscale)

Configure Tailscale serve to only offer AEAD ciphers for TLS 1.2, or drop TLS 1.2 entirely.

### Priority 3 — Disable TLS 1.0/1.1 (Cloudflare)

In Cloudflare dashboard: SSL/TLS → Minimum TLS Version → set to 1.2.

### Priority 4 — Block port 8000 (Tailscale)

Ensure port 8000 is firewalled and only reachable via port 443.

### Priority 5 — Suppress Server header

Override uvicorn's server header via Starlette middleware:

```python
@app.middleware("http")
async def hide_server_header(request, call_next):
    response = await call_next(request)
    response.headers["Server"] = ""
    return response
```

### Priority 6 — Sanitize error responses

Replace `str(e)` in `scripts/webapi.py` error responses with generic messages, log details server-side.

### Priority 7 — Enable HSTS in Cloudflare

In Cloudflare dashboard: Edge Certificates → HSTS → Enable with `max-age=31536000`.
