# Dragon ↔ Hawk Handshake (File Contract)

Purpose: Dragon governs Pytrel Hawk by observing explicit runtime artifacts and (later) asserting narrow controls via flags.
No network calls required. File-based determinism only.

---

## Directory Model (conceptual)

/runtime/
  /hawk/            (owned by Hawk)
  /dragon/          (owned by Dragon)

Dragon reads only from /runtime/hawk.
Dragon writes only to /runtime/dragon.

---

## Hawk → Dragon (read-only inputs)

### 1) /runtime/hawk/status.json
Minimal system status and freshness.

Required fields:
- as_of_utc: ISO-8601 string
- hawk_version: string
- mode: "paper" | "live" | "dry_run"
- loop_state: "ok" | "degraded" | "halted"
- last_tick_utc: ISO-8601 string
- data_freshness_sec: integer

### 2) /runtime/hawk/positions.json
Required fields:
- as_of_utc: ISO-8601 string
- positions: array (may be empty)
Each position object:
- symbol: string
- qty: number
- avg_price: number
- unrealized_pl: number (optional)
- opened_utc: ISO-8601 string (optional)

### 3) /runtime/hawk/orders.json
Required fields:
- as_of_utc: ISO-8601 string
- open_orders: array (may be empty)
Each order object:
- id: string
- symbol: string
- side: "buy" | "sell"
- qty: number
- type: string
- status: string

### 4) /runtime/hawk/risk.json
Required fields:
- as_of_utc: ISO-8601 string
- risk_state: "green" | "yellow" | "red"
- reserve_pct: number
- max_concurrent_positions: integer
- kill_switch: boolean (hawk-local)
- notes: string (optional)

### 5) /runtime/hawk/performance.json
Required fields:
- as_of_utc: ISO-8601 string
- equity: number
- day_pl: number
- total_pl: number (optional)

---

## Dragon → Hawk (write-only outputs, future governor)

### A) /runtime/dragon/dragon_heartbeat.json
Dragon presence artifact.

Required fields:
- as_of_utc: ISO-8601 string
- dragon_version: string
- dragon_mode: "observer" | "governor"
- observed_hawk_as_of_utc: ISO-8601 string (optional)
- status: "ok" | "blocked" | "degraded"
- message: string (optional)

### B) /runtime/dragon/dragon_ledger.jsonl
Append-only event log (one JSON per line).
Event fields:
- ts_utc: ISO-8601 string
- event_type: "OBSERVATION" | "ESCALATION" | "DECISION" | "ERROR"
- severity: "INFO" | "WARN" | "ERR"
- summary: string
- evidence: object (optional)

### C) /runtime/dragon/dragon_flags.json  (governor assertions)
Narrow, one-way controls. Hawk may honor these if enabled.
Required fields:
- as_of_utc: ISO-8601 string
- halt_new_orders: boolean
- halt_adds: boolean
- halt_exits: boolean
- reason: string

Default: all false.

---

## Freshness / Staleness Rules (Observer Mode)

Dragon considers Hawk inputs stale if:
- data_freshness_sec > 180, OR
- last_tick_utc older than 3 minutes from dragon time

Observer behavior on staleness:
- Write heartbeat status="degraded"
- Append ledger event (WARN)
- Take no other action

---

## Governor Mode (later, not now)

Governor mode is allowed only when:
- DRC is satisfied
- VERSION.lock is present and matches expected
- Hawk explicitly enables honoring dragon_flags.json

Governor scope is intentionally narrow:
- Halt new orders
- Halt adds
- Halt exits (rare; requires explicit owner intent)

No other control is permitted.