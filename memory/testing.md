# testing.md — testing strategy (reused from trcc, adapted to us)

## Principle
Core logic is **pure Python**; frameworks (Qt, FastAPI, trcc) are thin adapters only at the edges.
Every key behavior is gated by a test. **"Verified" = a test run, not prose.** A phase is done only
when its tests pass on the fake device AND the behavior matches the plan (no phase/milestone names;
audit row flips MISSING -> WIRED by a green run).

## Injectable ports (so logic is testable without hardware)
- `FrameSink` — real `ly.py` USB transport / `FakePanel` (records frames; asserts 450 KB budget,
  chunking, keepalive cadence, dirty-check: no resend on unchanged).
- `SensorSource` — real `sensors.py` (psutil) / deterministic `FakeSource` (fixed metrics).
- `TrccBackground` — real `trcc daemon` / `FakeBackground` (records `show`/`end` and frames).
- `StateStore` — real atomic state file (seq bridge) / `FakeStore` (in-memory) for the manager.

## Test layout (mirrors trcc)
- `tests/` — pytest **unit** on the pure core: render dirty-check, frame-budget shrink, state merge,
  palette selection (mode -> palette), gauge layout, manager API via `fastapi.testclient.TestClient`
  + fakes, bridge seq/staleness guard, request validation (setup/update/settings/theme).
- `dev/` — **integration smoke harnesses** driving the whole pipeline with zero hardware:
  `dev/smoke_full_pipeline.py` (stub-data -> manager -> state file -> module -> FakePanel);
  `dev/devices.json` + a `MockPanel` (analog of trcc's `MockPlatform` + `devices.json`).
- `dev/tools/` — diagnostic tools (analog of trcc `diagnose_metrics.py`).

## Reused trcc solutions
- `MockPlatform` + `dev/devices.json` -> our `MockPanel` + fake transport: run the full pipeline
  with no USB (reporters/hardware never required just to verify).
- Hexagonal ABC ports at boundaries -> inject a fake at the port; the core never imports an adapter.
- Coverage **ratchet** (`tests/test_logging_coverage.py`) -> pytest-cov gate (threshold only goes up),
  plus a gate on the critical path (frame-budget, keepalive, dirty-check).
- "Reporter-gated is progress theater" -> never defer verification to hardware when a harness exists.

## Concrete rules for our code
- Every critical behavior gets a test: frame-budget shrink (never >450000), keepalive cadence,
  dirty-check (no resend on unchanged), state merge (last-wins, no growth), palette derivation from
  mode, request validation, seq staleness guard.
- Business logic = pure functions (no Qt/FastAPI imports) so one test runs headless.
- Coverage gate on manager core + render core; run in CI (pytest + pytest-cov).