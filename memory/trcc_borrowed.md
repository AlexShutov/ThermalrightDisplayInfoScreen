# trcc_borrowed.md — trcc design ideas we adapt (NOT copied code)

We borrow only DESIGN PATTERNS / CONVENTIONS from GPL-3.0 trcc, re-implemented in our own MIT code.
Source refs are to `.venv-trcc/lib/python3.14/site-packages/trcc/` (9.12.0). Follow these during dev.

## 1. Download / install pipeline (cloud themes)
- Background queue worker with dedup, bounded retry `(30,120,600)` s, and publishing failures to every
  open client, not just a log. (`adapters/infra/data_install_runner.py:80-237`, `services/data_install.py:43-117`)
- Staged atomic extraction: write `*.tmp` -> extract into `target.partial` -> `rename` only on success;
  a crash leaves nothing "looking populated". `_unwrap_nested_dir` flattens a single wrapping subdir.
  (`adapters/repo/data_install.py:168-231`)
- Zip-slip guard: list members first, reject any escaping the target, extract with a timeout; missing
  `7z` -> warn and skip, never crash. (`adapters/repo/data_install.py:78-140`)
- Fetcher: `max_bytes` (read `max_bytes+1` to detect over-cap), `allow` on URL AND every redirect
  (`_CheckedRedirects`), `public_only` (reject a resolved non-public peer, defeats DNS rebinding),
  certifi bundle on macOS. (`adapters/repo/http.py:71-221`)
- Best-effort ffmpeg thumbnail via `_run_ffmpeg_or_warn` (warn, never raise); regenerate PNG only if
  older than MP4; backfill off the request path. (`services/cloud_theme.py:147-226`)
- Per-OS tool aliasing `TOOL_ALIASES` (ffmpeg->ffmpeg/ffmpeg7, 7z->7zz/7za/7zr); `locate()`
  distinguishes installed-but-not-on-PATH from missing. (`core/toolchain.py:49-194`)

## 2. Error classification + retry/backoff (USB / transient IO)
- `RecoveryTracker`: walk `exc.__cause__` for USB errnos (EIO/EBADF/ENXIO/ENODEV/ESHUTDOWN) ->
  "non-disconnect"/"disconnect"/"threshold"; rate-limit warnings (once/30 s); EACCES/EBUSY get
  actionable per-OS hints; `note_success` resets. (`core/device_recovery.py:42-223`)
- `ReconnectWatcher`: doubling backoff 3 s -> 60 s (300 s "slow" for never-seen); `arm()` does not
  restart backoff on failed attempt; namespaced `KEY_PREFIX` so it never stops the device sender.
  (`services/reconnect_watcher.py:44-135`)
- `BaseSendTask`: subclass contract (`KEY_PREFIX`/`DEFAULT_INTERVAL_S`/`_run`), skip when away/game-mode,
  `FIXED_BEAT` beat-vs-rest. (`services/_send_task.py:35-127`)

## 3. Last-command-wins send serialization
- `DeviceSender`: ONE `_pending` slot; newer submit supersedes older (`superseded`), sets the prior
  waiter `ok=True` so it isn't hung; write happens OUTSIDE the inbox lock (slow USB never blocks
  submit); `wait=True` one-shot result channel; keepalive resends `_last` (0.150 s); `exclusive()`
  holds the wire during multi-frame uploads. (`services/device_sender.py:138-291`)
- One-thread-per-task scheduler + sync twin for tests. (`adapters/infra/send_scheduler.py:65-128`)
- Generic `QueueWorker` (lazily-spawned daemon thread, `_STOP` sentinel, `stall_hint`). (`adapters/infra/_worker.py:45-140`)

## 4. Settings persistence (bridge state / persistent JSON)
- Atomic save: `*.json.tmp` -> write -> `flush` -> `os.fsync` -> `tmp.replace(path)`; `ConfigError` on
  OSError. (`services/settings.py:1210-1230`)
- Schema versioning + in-place migrations: versions bumped ONLY on semantic change; each step gated on
  the version that introduced it, not "older than current"; missing `schema` read as pre-versioned.
  (`services/settings.py:135-169,1294-1454`)
- Distinct write-path vs read-path config resolution (so a rename can't strand users). (`services/settings.py:1238-1278`)
- Per-field JSON converters with a `_DROP` sentinel; tolerant of unknown fields. (`services/settings.py:1286-1592`)

## 5. Logging (manager + daemon + adapter share one log)
- Idempotent `configure_logging`, rotating file (1 MB x 5) + `<stem>.latest.log` per run, `_RunOwnership`
  so a client starting mid-run appends instead of wiping; `stderr` mirror of WARNING+; tags handlers so
  reconfig only closes its own (no live lock fd left). (`adapters/infra/logging.py:615-758,480-548`)
- Multi-process safe rotating handler: sidecar `.lock`, `os.stat` shared size + `st_ino` (detect peer
  rotation) instead of `stream.tell()`. (`adapters/infra/logging.py:111-429`)
- `RenderOnceRotatingFileHandler` (cache Formatter.format per record). (`adapters/infra/logging.py:67-102`)
- `ClassContextFilter` injects `record.classname` -> `Module.Class.method:line` for free. (`adapters/infra/logging.py:579-612`)
- `log_chain`/`tail_log`/`log_problems`: read the whole rotation chain, select by significance (INFO+,
  or WARNING/ERROR blocks), bounded deque; `OwnLines` excludes the report's own process. (`adapters/infra/logging.py:761-1055`)
- Early-logging buffer (`MemoryHandler`) until the real path is known; `ensure_configured` guards against
  a second call silently downgrading `-v`. (`adapters/infra/logging.py:1076-1231`)

## 6. Health checks + debug report (our /health + diagnostics)
- Check registry: functions -> `HealthCheckResult(name, severity=OK|WARN|FAIL, message, fix_hint)`;
  aggregation `worst_severity/fail_count/warn_count`; cheap, never raise. (`adapters/diagnostics/health.py:670-705`, `core/diagnostics.py:24-55`)
- Writable-dir probes (write+unlink a probe file). (`health.py:132-171`)
- Install-integrity detect: stale `.pyc`, duplicate binaries on PATH. (`health.py:57-105`)
- Whole-log graceful-degrade report; `_scrape_handshake_lines` digs the oldest first-of-session line
  out of the whole chain (the tail loses it). (`adapters/diagnostics/debug_report.py:116-184,492-529`)

## 7. Daemon / IPC lifecycle
- `ensure_daemon`: never-downgrade (replace only older, keep newer with a warning); spawn with
  `sys.executable -m trcc daemon`, `cwd="/"`, `start_new_session=True`, DEVNULL, `close_fds`; strip the
  daemon env toggle from the child so it never proxies to itself. (`daemon.py:169-232,400-420`)
- `_wait_for_the_spawn` treats child-exit-before-answer as evidence; then 60 s timeout then kill.
  (`daemon.py:235-271`)
- `AppLock` (flock) one App per user; `held()` probes "starting or serving". (`ipc.py:204-250`)
- `SingleInstance` per-UI socket + raise-existing on LAN; on Windows uses a named file lock. (`ipc.py:1188-1387`)
- `on_app_gone`: subscribe to `AppStopping` alone (not `events`), distinguish intentional stop vs crash.
  (`proxy.py:429-479`)
- Event-stream reconnect with backoff (0.2 s doubling to 10 s); surface EOF-for-good. (`proxy.py:301-349`)
- DESIGN RULE: a request handler must not be able to take down the process owning the device
  (`_dispatch_envelope` catches everything, marks raised Commands). (`ipc.py:1110-1151`, `proxy.py:160-212`)
- `_boot` factory + `_local_reason`: decide "shared engine or in-process" explicitly; don't pay for a
  renderer/deps you won't use. (`_boot.py:44-194`)

## 8. Module install (our SDK install path)
- Folder-install requires developer mode; consent `accepted.matches(manifest.asks)` (consent link
  survives unsigned); installs `switch_on=False`; never updated/swept. (`services/module_install.py:225-292`)
- Atomic swap with restore-on-failure (remove old -> install aside -> in place -> restore + re-enable on
  any exception). (`services/module_install.py:339-363`)
- Trust-chain ordering: root+index verified -> `min_trcc` -> consent -> bytes match listing -> signed by
  author (pinned on update) -> unpack-aside then swap. (`services/module_install.py:110-164`, `services/module_trust.py`)
- Rollback gates: `set_module_index_seen` only ever records `max(seen,root/index)` (an older index is
  refused as a rollback). (`services/settings.py:742-755`, `services/module_install.py:110-124`)

## 9. Sensors / metrics self-guarding (stub wrapper)
- `_read` degrades a raising source to None: first failure per label warns, later ones DEBUG.
  (`adapters/sensors/aggregator.py:277-304`)
- `unsupported()` = STATIC "no backend here" set -> withhold keys that can never have a value, never
  advertise them at 0.0. (`aggregator.py:362-385,469-473`)
- `_refresh_if_stale`: inline poll when cache older than interval (avoids boot-time values in a
  long-running headless loop). (`aggregator.py:489-525`)
- Log only what the sweep actually DID, on the per-frame family. (`aggregator.py:94-113`)

## 10. Safe file / JSON / path helpers (pure, reusable)
- `load_json_or_default` central fallback (FileNotFoundError debug, other OSError/JSON/Unicode warn).
  (`core/_safe.py:32-64`)
- `is_under` symlink-aware containment (resolve both sides; degrade to "not under" on error).
  (`core/_safe.py:127-161`)
- `is_safe_zip_member` / `is_safe_user_name` (reject traversal/absolute/drive-letter/NUL/dot-prefix).
  (`core/_safe.py:67-124`)

## 11. HTTP API / pydantic (our manager: FastAPI + pydantic v2)
- `WireView` = pydantic `RootModel[T]` with `ser_json_bytes="hex"` (declared once in the adapter).
  (`ui/api/schemas.py:32-51`)
- Projection response models: expose only what the client needs, never absolute server paths.
  (`ui/api/schemas.py:73-103`)
- Constraint-as-contract: `Field(..., ge/le/pattern)`; optional-with-sentinel `bool | None` = "null
  leaves it". (`ui/api/schemas.py:114-165,409-423`)

## 12. Broadcast / merge (manager -> adapter)
- One broadcast loop funnels every consumer through a SINGLE publisher (consumers never re-poll);
  subscribes to pref-change events to wake; reads interval each iteration. (`services/metrics_loop.py:50-307`)
- Personalize (temp-unit conversion, HDD filter) at ONE site; cache the RAW sample for per-tick consumers.
  (`services/metrics_loop.py:229-290`)

## 13. Small utilities
- `get_lan_ip`: UDP probe of default route to print a reachable URL instead of `0.0.0.0`. (`adapters/infra/network.py:15-31`)
- `Blob`/`BLOB_HEAD_BYTES`: render bytes as `<n> bytes, first <16>: <hex>` instead of `repr` (one
  1920x440 frame exceeds the whole 1 MB x 5 rotation). (`core/logs.py:93-151`)
- `per_frame`/`PER_FRAME_ROOT` + `recurring_warning`/`recurring_failure`; `sink_for(level)`.
  (`core/logs.py:202-323`)