# SESSION.md — живая передача (читать первой)

Состояние текущего прогона. Постоянные факты живут в индексе `MEMORY.md` + файлах-темах; этот файл
держит только «где мы и что дальше» для ЭТОГО прогона. Завершение: заархивировать этот файл в
`memory/sessions/YYYY-MM-DD-<slug>.md`, добавить строку в `README.md` этой папки, затем переписать его.

## Где мы
Задача: **менеджер тем / SDK** поверх `trcc daemon` (локальный HTTP-сервис + модуль-адаптер, рисующий
дашборд поверх темы). Dev + runtime = **macOS** сегодня; Pi 5 / Orange Pi 6 Plus — в будущем.

**Фазы 1-3 закодированы и зелёные headless**: менеджер (Фаза 1), продюсер-заглушка (Фаза 2),
ядро адаптера + GPL-модуль trcc + опциональный `TrccThemeSwitcher` (Фаза 3). **В этом прогоне панель
была подключена**, и `memory/hardware_verification.md` прогнан сверху вниз (см. этот файл для записи A-G:
A ✓, B ✓ (живое вынуть/воткнуть), C ✓, D ✓ (~18 fps), E ✓ на стороне менеджера + оговорка про switch видео,
F ✓, G ✓). Рантайм на данный момент: менеджер на `:8123`, продюсер-заглушка, trcc daemon
(`.venv-trcc/bin/python -m trcc daemon`, env
`HARDWARESCREEN_STATE_FILE=<abs manager_state.json>`), модуль дашборда установлен+включён
(`dashboard:true` в trcc.json -> авто-включение при старте демона).

## Сделано (этот прогон / недавно)
- **Переименование пакета** `trofeo` -> `hardwarescreen` (git-путь, директория пакета, скрипты, deploy,
  доки), remote не менялся. Ветка `web-contract-hardening` (в `SESSION.md` раньше было
  `rename-hardwarescreen` — устарело).
- **Фаза 1 менеджер** (`src/manager/`): FastAPI+uvicorn (1 async-воркер), pydantic v2, `AtomicStateFile`
  (temp+fsync+os.replace + монотонный `seq`), `/health /dashboard/setup /dashboard/update /settings
  /theme`, stream-bounded лимит тела (413), опциональный Bearer `auth_token`, `switch_theme` вне цикла
  (`asyncio.to_thread` + `switch_timeout_s`, non-fatal).
- **Фаза 2 заглушка** (`src/stubdata/` + `stubdata.yaml` + скрипт): `Producer` (seeded RNG, `start/step`),
  порт `ManagerClient` + `HttpManagerClient` (stdlib urllib), `make_gauges`.
- **Фаза 3 адаптер** (`src/hardwarescreen/adapter/`, MIT): `BridgePoller` (emit только на росте `seq`),
  `gauge_layout`+`compose_dashboard`+`hex_to_rgb` (raw RGB PIL), `DashboardSource.latest()` (держит
  новейшее, всегда возвращает `w*h*3` байт), `AdapterDriver` (show/end + attach-race + идемпотентность).
- **GPL-модуль trcc** `dev/trcc-module-dashboard/` (`dashboard/app.py` + `module.json`): оборачивает MIT
  адаптер в `BaseModule`/`FrameSource`/`background.show/end`; `_loop` использует `poll() or last`.
  Проверен только `py_compile`-ом (не импортируется в тестах).
- **`src/manager/trcc.py`** опциональный `TrccThemeSwitcher` (инжектируемый subprocess-раннер, литеральная
  замена `{theme}`); `config.theme_cmd`; подключён в `create_app` с live-резолвером `state.themes`.
- `src/hardwarescreen/imageutil.py` (`encode_within_budget`), рефактор `render.py` на него.
- **Вынесение Activity Rings в отдельный репозиторий** `github.com/AlexShutov/ActivityProgressRingsQt`
  (публичный) и подключено здесь как **git-сабмодуль** в `ActivityProgressRingsQt/` (коммит `fc36d49`).
  Удалено из этого репо: `src/hardwarescreen/rings*.py`, `tests/test_rings_focus.py`,
  `tools/*rings*preview.py`, `docs/rings*`, `activity_rings_compare.pdf`. Сабмодуль имеет свою
  историю на задачу (Pillow-рендерер → focus timeline + тесты → Qt focus preview + ассеты).
- Ворота анализа багов прогнаны дважды; найдено + исправлено HIGH/MED/MED/LOW/LOW (GPL keep-newest,
  краш маленького канваса, stale-vs-live резолвер, кривой seq, brace-placeholder). Пере-проверка: READY.
- **КРИТИЧНЫЙ баг на устройстве найден + исправлен:** GPL-модуль передавал `_KEY="dashboard"` как ключ
  ПАНЕЛИ в `background.show/end`; trcc трактует первый аргумент как ключ панели-устройства. Фикс сделал
  `AdapterDriver` ключ-осознанным (`update(state, attached, panel_key=None)` + `shown_key`), а модуль
  передаёт `self._panel_key = keys[0]`; `_disable` завершает `shown_key or panel_key or _KEY`. Файлы:
  `src/hardwarescreen/adapter/driver.py`, `dev/trcc-module-dashboard/dashboard/app.py`,
  `tests/test_adapter.py` (добавлены `test_driver_targets_the_panel_key_not_the_module_key` +
  `test_driver_handles_panel_key_change_while_shown`). ruff/mypy чисто, 69 прошло. **НЕ ЗАКОММИЧЕНО.**
- **Оверлей поверх темы (этот прогон):** тема-под-гейджами trcc сам не кладёт (см.
  `hardware_verification.md`); добавлен композит поверх кадра темы: `compose_dashboard(..., background)`,
  `DashboardSource(..., background)`, GPL-модуль читает env `HARDWARESCREEN_BACKGROUND_IMAGE`.
  Проверено на устройстве: гейджи поверх кадра темы a005. ruff/mypy чисто, 71 прошло, баг-хунт CLEAN.
  **НЕ ЗАКОММИЧЕНО.**
- **Правило языка:** в этом прогоне в `AGENTS.md` и `memory/` закреплено «правила/доки на русском,
  код на английском»; весь `AGENTS.md`, `README.md` и memory-файлы переведены на русский.

## Открытые вопросы (этот прогон)
- Переключатель менеджер->trcc — это CLI `trcc display play <id>` (опция через `theme_cmd`);
  альтернатива через REST-API trcc всё ещё открыта (`theme_cmd`-инъекция её покрывает).

## План (список фаз)
- **Фаза 0** цикл подтверждён на железе (пробный модуль; композит поверх темы). Готово.
- **Фаза 1** каркас менеджера — Готово (headless; `/theme` персистит + опциональный CLI-триггер).
- **Фаза 2** источник данных-заглушка — Готово (отдельный сервис; случайные обновления + день/ночь).
- **Фаза 3** модуль-адаптер дашборда — Готово (headless-ядро + GPL-обёртка trcc + switcher).
- **Фаза 4** стиль день/ночь через `/settings` — контракт + палитры есть; взаимодействие
  активной-палитры с адаптером подтверждено headless; на устройстве — через `hardware_verification.md` §E.
- **Будущее** железа и deploy: Pi 5 / Orange Pi 6 Plus (trcc aarch64, udev, systemd, deploy, доки).

## Следующее действие
1. Закоммитить незакоммиченный фикс panel-key + изменения оверлея поверх темы
   (driver.py, dashboard.py, source.py, dev/.../app.py, test_adapter.py) на `web-contract-hardening`.
   Оверлей подключён через env `HARDWARESCREEN_BACKGROUND_IMAGE` к теме-кадру PNG (dev/статический путь;
   живой композит видео-под-гейджами — цель Qt-адаптера вариант A). ruff/mypy чисто, 71 прошло, баг-хунт CLEAN.
2. Аппаратный чек-лист §A-G весь пройден на устройстве, кроме §E полного video-фон-switch (нужен
   `theme_cmd` в `manager.yaml` — опция: trcc CLI + доступность облачной темы). B и F
   проверены вживую с физическим вынуть/воткнуть.

## Заметки для передачи
- USB-доступ запрещён для trcc daemon -> какой-то клиент `hardwarescreen run`/`supervise` держит
  устройство; остановить его.
- Демон может зависнуть на пере-загрузке отравленного кэша (напр. `e005.mp4` как 404 HTML); повтор
  восстанавливает.
- Тест устройства (без приложения): `python tools/probe_ly.py`, `python tools/probe_fps.py`
  (exit 1, "Device not found").
- `FrameSource.latest()` trcc возвращает СЫРОЙ RGB (не JPEG); обёртка модуля обязана отдавать
  `img.tobytes()`.