# trcc_module_api.md — API расширения (модуля) trcc

- Модуль = подкласс `BaseModule` с `key=`; переопределить `_enable`/`_disable`; опционально `start`/`stop`.
- Папка модуля: `module.json`, `__init__.py`, `app.py`, опционально `core/`, `ui/<face>/`.
- `module.json`: `key`, `min_trcc`, `default_on`; объявить `capabilities`, чтобы использовать
  panels/background и пр.
- CLI: `trcc system modules {list|on|off|developer|install-folder}`.
- Установка: `trcc system modules developer on` -> `install-folder <path> --yes` (unsigned, off) ->
  `on <key>`; сохраняется между рестартами демона.

## Кадр / фон
- `FrameSource.latest() -> RawFrame`, `close()`.
- Показ: `host.background.show(key, source, fps)`; возврат: `background.end(key)`.
- `BackgroundKind`: регистрировать через `host.themes.add_kind`; `play/resume/snapshot`;
  префикс = папка.
- Наблюдать входящие кадры: `host.frames.watch(handler)` -> `PanelPicture`.
- Доступ: `host.panels`, `host.settings`, `host.files.data_root()`.

## Гейт по capability (проверено)
Модуль обязан объявить нужные capabilities в `module.json`, иначе, напр.,
`Stub asked for 'panels' without declaring it in its module.json`.

## Наш модуль-заглушка (референс)
`dev/trcc-module-stub/stub/` — ключ `stub`, min_trcc `9.12.0`, `default_on false`, установлен + ON,
вызывается (проверено) (`HARDWARESCREEN_STUB: enabled host=ModuleScope`). Только GPL-3.0
(импортирует внутренности trcc).