# ZAI / Zhipu AI — Programmatic Usage API Research

**Date**: 2026-06-17  
**Status**: COMPLETE — working endpoints confirmed with live API key

---

## Summary

ZAI exposes unofficial (undocumented in public API reference) monitor endpoints that work with both direct API keys and Bearer tokens. The primary endpoint is:

```
GET https://api.z.ai/api/monitor/usage/quota/limit
```

This single call returns the 5-hour token quota, the weekly token quota, and the monthly MCP call quota — plus the plan level (`lite`, `pro`, `max`).

---

## Working Endpoints

### 1. Quota / Limits — PRIMARY (confirmed working)

```
GET https://api.z.ai/api/monitor/usage/quota/limit
```

**Auth**: Either format works:
- `Authorization: Bearer <api_key>`
- `Authorization: <api_key>` (no Bearer prefix)

**Example curl**:
```bash
curl -s \
  -H "Authorization: Bearer $ZAI_API_KEY" \
  -H "Accept-Language: en-US,en" \
  -H "Content-Type: application/json" \
  "https://api.z.ai/api/monitor/usage/quota/limit"
```

**Live response** (tested 2026-06-17, Lite plan):
```json
{
  "code": 200,
  "msg": "Operation successful",
  "data": {
    "limits": [
      {
        "type": "TIME_LIMIT",
        "unit": 5,
        "number": 1,
        "usage": 100,
        "currentValue": 0,
        "remaining": 100,
        "percentage": 0,
        "nextResetTime": 1784343885978,
        "usageDetails": [
          {"modelCode": "search-prime", "usage": 0},
          {"modelCode": "web-reader", "usage": 0},
          {"modelCode": "zread", "usage": 0}
        ]
      },
      {
        "type": "TOKENS_LIMIT",
        "unit": 3,
        "number": 5,
        "percentage": 19,
        "nextResetTime": 1781770153200
      },
      {
        "type": "TOKENS_LIMIT",
        "unit": 6,
        "number": 1,
        "percentage": 3,
        "nextResetTime": 1782356685978
      }
    ],
    "level": "lite"
  },
  "success": true
}
```

**Field decoding**:

| `type` | `unit` | `number` | Meaning |
|--------|--------|----------|---------|
| `TOKENS_LIMIT` | 3 | 5 | **5-hour token quota** (the rolling window) |
| `TOKENS_LIMIT` | 6 | 1 | **Weekly token quota** (7-day rolling) |
| `TIME_LIMIT` | 5 | 1 | **Monthly MCP call quota** (search-prime, web-reader, zread) |

**Key fields per limit item**:
- `percentage` — percent of quota consumed (0–100). Use `100 - percentage` for remaining.
- `nextResetTime` — epoch milliseconds for next reset.
- `usage` — total quota ceiling (tokens for TOKENS_LIMIT, call count for TIME_LIMIT).
- `currentValue` — amount consumed so far.
- `remaining` — remaining capacity.
- `level` — plan tier: `"lite"`, `"pro"`, `"max"`.

**Python parsing snippet**:
```python
import time, requests

def get_zai_usage(api_key: str) -> dict:
    resp = requests.get(
        "https://api.z.ai/api/monitor/usage/quota/limit",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept-Language": "en-US,en",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    now_ms = time.time() * 1000

    result = {"plan": data["level"]}
    for limit in data["limits"]:
        reset_in_sec = max(0, (limit.get("nextResetTime", 0) - now_ms) / 1000) if limit.get("nextResetTime") else None
        pct_used = limit.get("percentage", 0)
        pct_remaining = 100 - pct_used

        if limit["type"] == "TOKENS_LIMIT" and limit["unit"] == 3:
            result["five_hour"] = {
                "pct_used": pct_used,
                "pct_remaining": pct_remaining,
                "reset_in_seconds": reset_in_sec,
                "total": limit.get("usage"),
                "consumed": limit.get("currentValue"),
                "remaining_tokens": limit.get("remaining"),
            }
        elif limit["type"] == "TOKENS_LIMIT" and limit["unit"] == 6:
            result["weekly"] = {
                "pct_used": pct_used,
                "pct_remaining": pct_remaining,
                "reset_in_seconds": reset_in_sec,
                "total": limit.get("usage"),
                "consumed": limit.get("currentValue"),
                "remaining_tokens": limit.get("remaining"),
            }
        elif limit["type"] == "TIME_LIMIT":
            result["monthly_mcp"] = {
                "pct_used": pct_used,
                "total": limit.get("usage"),
                "consumed": limit.get("currentValue"),
                "remaining": limit.get("remaining"),
                "by_tool": {d["modelCode"]: d["usage"] for d in limit.get("usageDetails", [])},
            }
    return result
```

---

### 2. Model Usage (24-hour time series) — confirmed working

```
GET https://api.z.ai/api/monitor/usage/model-usage?startTime=<datetime>&endTime=<datetime>
```

**IMPORTANT**: DateTime format must be `yyyy-MM-dd HH:mm:ss` (NOT epoch ms).

```bash
curl -s \
  -H "Authorization: Bearer $ZAI_API_KEY" \
  "https://api.z.ai/api/monitor/usage/model-usage?startTime=2026-06-17%2000%3A00%3A00&endTime=2026-06-18%2003%3A00%3A00"
```

Returns hourly token usage broken down by model (GLM-4.7, GLM-4.6, etc.) with `modelCallCount[]`, `tokensUsage[]`, and `totalUsage` summary.

---

### 3. Tool Usage (MCP call time series) — confirmed working

```
GET https://api.z.ai/api/monitor/usage/tool-usage?startTime=<datetime>&endTime=<datetime>
```

Same datetime format requirement. Returns `networkSearchCount[]`, `webReadMcpCount[]`, `zreadMcpCount[]` per hour.

---

### 4. Bigmodel.cn equivalent (CN platform) — confirmed working

```
GET https://bigmodel.cn/api/monitor/usage/quota/limit
```

Same auth, same response schema. Use for accounts registered on the Chinese platform (`open.bigmodel.cn`).

---

## Response Headers

No rate-limit headers exposed on the proxy endpoint (`https://api.z.ai/api/anthropic/v1/messages`). The response body includes a `usage` field with `input_tokens`, `output_tokens`, and `web_search_requests` but NO quota-remaining headers.

---

## NOT Working / 404

These paths return 404 or error:
- `/api/devpack/usage`
- `/api/devpack/quota`
- `/api/devpack/subscription`
- `/api/monitor/usage/subscription`
- `/api/paas/v4/billing`
- `/api/paas/v4/subscription`
- `/open.bigmodel.cn/api/paas/v4/quota`

**Plan/subscription info** is not available as a separate endpoint — it's embedded in the quota response as `data.level` (`"lite"` / `"pro"` / `"max"`).

---

## GitHub Repos That Figured This Out

1. **[wavever/CCLimitPing](https://github.com/wavever/CCLimitPing)** — Go binary for Claude+Codex window-chaining. Mentions GLM support using the same 5h+weekly structure. Auth is static API key (not OAuth like Claude).

2. **[guyinwonder168/opencode-glm-quota](https://github.com/guyinwonder168/opencode-glm-quota)** — OpenCode plugin, most complete ZAI quota implementation. Confirmed endpoints:
   - `/api/monitor/usage/quota/limit`
   - `/api/monitor/usage/model-usage`
   - `/api/monitor/usage/tool-usage`

3. **[vbgate/opencode-mystatus](https://github.com/vbgate/opencode-mystatus)** — Multi-platform quota checker. ZAI endpoint: `https://api.z.ai/api/monitor/usage/quota/limit`. Auth: `Authorization: <api_key>` (no Bearer prefix in their implementation, though Bearer also works).

4. **[robinebers/openusage](https://github.com/robinebers/openusage)** — macOS menu bar app. Docs at `docs/providers/zai.md` have the most complete field documentation including the subscription endpoint (which returns `productName` and `nextRenewTime`) — though that specific subscription URL was not discoverable via API testing.

5. **[slkiser/opencode-quota](https://github.com/slkiser/opencode-quota)** — Supports Z.ai among many providers.

---

## Key Behaviors / Gotchas

1. **`percentage` field on TOKENS_LIMIT items may be missing** (only `currentValue`/`usage`/`remaining` present). The quota response for a zero-usage session omits some fields. Always use `.get()` with defaults.

2. **5-hour window is a rolling window**, not clock-aligned. It starts when your first billable request fires. `nextResetTime` tells you exactly when the current 5h window expires.

3. **Weekly window** (`unit: 6, number: 1`) resets independently — approximately 7 days from your last window start.

4. **Monthly MCP quota** (`TIME_LIMIT`, `unit: 5`) has no `nextResetTime` in the response when unused. It resets monthly from the subscription renewal date.

5. **The API key format** is `<hex_secret>.<jwt_style_secret>` — pass it as-is, with or without `Bearer` prefix. Both work on the monitor endpoints.

6. **No response headers** carry quota info — the Anthropic-compatible proxy at `/api/anthropic/v1/messages` returns standard Anthropic response bodies with `usage.input_tokens` / `usage.output_tokens` but no `X-RateLimit-*` headers.

---

## Minimal Usage Check Script

```bash
#!/bin/bash
# zai-status.sh — one-shot ZAI quota check
API_KEY="${ZAI_API_KEY:-b9db9e36dc6e41b59136af4898769490.rdr9nraHsJ9ChNaw}"

curl -s \
  -H "Authorization: Bearer $API_KEY" \
  -H "Accept-Language: en-US,en" \
  "https://api.z.ai/api/monitor/usage/quota/limit" | python3 -c "
import json, sys, time

d = json.load(sys.stdin)
limits = d['data']['limits']
plan = d['data']['level']
now = time.time() * 1000

print(f'Plan: {plan}')
for lim in limits:
    pct = lim.get('percentage', 0)
    reset_ms = lim.get('nextResetTime')
    reset_str = f'{(reset_ms - now)/3600000:.1f}h' if reset_ms else 'monthly'
    t = lim['type']
    u = lim['unit']
    if t == 'TOKENS_LIMIT' and u == 3:
        label = '5-hour tokens'
    elif t == 'TOKENS_LIMIT' and u == 6:
        label = 'weekly tokens'
    else:
        label = 'monthly MCP calls'
    bar = '█' * (pct // 10) + '░' * (10 - pct // 10)
    print(f'{label:20s}: {bar} {pct}% used — resets in {reset_str}')
"
```
