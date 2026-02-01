 # Dragon Runtime Contract (DRC)

Dragon is the governing agent for Pytrel systems.

## Operating Mode
- Default: OBSERVER (read-only)
- Future: GOVERNOR (can assert one-way controls via files)

## Inputs (read-only)
Dragon reads only from defined runtime files:
- hawk runtime snapshots (positions, orders, equity, risk state)
- system health (timestamps, version locks, checksums)

## Outputs (write-only, Dragon-owned)
Dragon writes only:
- dragon_heartbeat.json (current presence + version + freshness)
- dragon_ledger.jsonl (append-only events: observations, decisions, escalations)
- dragon_digest.json (optional, daily/periodic summary artifact)
- dragon_flags.json (future: controlled assertions like HALT_NEW_ORDERS)

## State
Dragon maintains no hidden state.
State is explicit in files under `runtime/`.

## Failure Policy
- If inputs are missing/stale: Dragon records and remains inert.
- If outputs cannot be written: Dragon exits non-zero.
- Dragon never invents data.

## Authority Boundaries
- Dragon does not place orders.
- Dragon does not modify Hawk state.
- Dragon does not override the broker.
- Dragon may only assert governance through a narrow file-flag interface (future).

## Timebase
All timestamps in UTC ISO-8601.

## Versioning
Dragon behavior is pinned to repository tag + VERSION.lock.