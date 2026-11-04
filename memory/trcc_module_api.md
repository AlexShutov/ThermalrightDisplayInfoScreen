# trcc_module_api.md — trcc extension (module) API

- Module = `BaseModule` subclass with `key=`; override `_enable`/`_disable`; optional `start`/`stop`.
- Module folder: `module.json`, `__init__.py`, `app.py`, optional `core/`, `ui/<face>/`.
- `module.json`: `key`, `min_trcc`, `default_on`; declare `capabilities` to use panels/background/etc.
- CLI: `trcc system modules {list|on|off|developer|install-folder}`.
- Install: `trcc system modules developer on` -> `install-folder <path> --yes` (unsigned, off) ->
  `on <key>`; persists across daemon restarts.

## Frame / background
- `FrameSource.latest() -> RawFrame`, `close()`.
- Show: `host.background.show(key, source, fps)`; return: `background.end(key)`.
- `BackgroundKind`: register with `host.themes.add_kind`; `play/resume/snapshot`; prefix = folder.
- Watch incoming frames: `host.frames.watch(handler)` -> `PanelPicture`.
- Access: `host.panels`, `host.settings`, `host.files.data_root()`.

## Capability gate (verified)
Module must declare needed capabilities in `module.json`, else e.g.
`Stub asked for 'panels' without declaring it in its module.json`.

## Our stub module (reference)
`dev/trcc-module-stub/stub/` — key `stub`, min_trcc `9.12.0`, `default_on false`, installed + ON,
verified called (`TROFEO_STUB: enabled host=ModuleScope`). GPL-3.0-only (imports trcc internals).