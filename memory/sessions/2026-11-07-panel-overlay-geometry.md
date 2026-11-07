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
- **Цвет-по-значению + стратегии выбора + композит «линейный слева + кольца справа» (ЗАПУШЕНО):**
  - `e727111`/`7f25fc8` — цвет колонок из шкалы «значение → цвет» (value_color), свечение выделенного
    бара цветом состояния, единая подложка при цветовой палитре (`HARDWARESCREEN_VALUE_COLOR`).
  - `f382d3a` — `adapter/selection_strategy.py`: `SelectSingleIndicator`/`SelectMultipleIndicators`
    (при >1 выбранных и соседних зазор ≥ `GLOW_MIN_GAP_MM`), интеграция в `LinearDashboardRenderer`.
  - сабмодуль `7104a7a`+`c69d3b6`+`63e098d` — то же для колец (`rings_strategy.py`, `rings_layout` через
    `min_gap_px`/`_growth_plan`), `gauge_ring_bases`/`render_gauge_rings` (кольцо на датчик),
    transparent RGBA-оверлей `render_rings(transparent=True)` для укладки поверх темы.
  - `dea9f1d` — композит `CompositeDashboardRenderer` в GPL-модуле (`dev/trcc-module-dashboard/dashboard/app.py`):
    линейный индикатор слева + кольца датчиков справа, правый отступ `COMPOSITE_RIGHT_MARGIN_MM=5.0`
    (~34px), включение `HARDWARESCREEN_COMPOSITE=rings_right`. Кольца = те же данные (min/max → доля,
    цвет `value_color`). Плюс фикс утечки `_QRECTF` в `tests/test_linear_dashboard.py`
    (autouse-фикстура) и tests `tests/test_composite_renderer.py`, probe `tools/probe_composite.py`.
  - Ворота: main ruff/pytest зелёные, mypy — только 8 пре-существующих (PyQt-attr-defined, подтверждены по
    базе 7f25fc8); сабмодуль pytest зелёный, mypy/ruff по activityrings без новых ошибок (4 ruff —
    пре-существующие в `test_rings_focus.py`). Баг-хунт по композиту: найдены+исправлены major
    (progress-ключ `str(gid)`) и minors (хрупкий фолбэк `_palette` → убран, непрозрачный квадрат колец →
    transparent RGBA, cover-подгонка фона).
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
- **Qt linear-indicator дашборд (этот прогон):** бары дашборда переписаны на Qt (PySide6) по аналогии с
  Activity Rings — выделение (фокус), тень, плавное переключение (без прыжков), цвет активного контура
  (`HARDWARESCREEN_FOCUS_COLOR`, по умолчанию `#00c85a`). Новый рендерер
  `src/hardwarescreen/adapter/linear_dashboard.py` (`LinearDashboardRenderer`, MIT, ленивый Qt) пишет
  кадр напрямую в `QImage`+`QPainter` **без `QApplication`/`QGraphicsScene`** — иначе в daemon trcc
  сегфолт (сигнал 11); текст label/value рисуется PIL. `DashboardSource` получил опциональный
  `frame_renderer`, GPL-модуль выбирает рендерер через `HARDWARESCREEN_RENDERER` (по умолчанию `qt`,
  фолбэк — Pillow). Он же создаёт `activity_linear_indicator` (новый пакет сабмодуля).
- **Сабмодуль переименован `ActivityProgressRingsQt` -> `DashboardWidgets`** (GitHub
  `AlexShutov/DashboardWidgets`, pyproject name `dashboardwidgets`); там новый пакет
  `src/activity_linear_indicator/` (`linear_focus.py` pure + `linear.py` Qt `LinearItem`), инструмент
  `tools/qlinear_preview.py`, тесты `tests/test_linear_focus.py` (38 зелёных), README. Путь сабмодуля в
  главном репо теперь `DashboardWidgets/` (`.gitmodules` + `.git/config` обновлены, editable пересобран
  в `.venv-trcc`).
- **Проверено на устройстве:** daemon trcc стабилен (PID без падений), `trcc status` →
  `LCD 0 [0416:5408] showing: the Dashboard module`, `panel_keys: ('0416:5408',)`, исключений нет.
  Баг-хунт по рендереру: найдены и исправлены MAJOR-зависание (easing прогресса замирал при смене цели
  каждый кадр → экспоненциальное сглаживание) и MAJOR-переполнение (выделение вылезало в футер →
  scale-to-fit в available-область) + minor (ignored background, «no data», выравнивание value).
- **Закоммичено в главном репо:** `8d9820b` (интеграция Qt-рендерера + frame_renderer + wiring модуля
  + поднятие указателя сабмодуля), `3c12ccc` (переименование сабмодуля в DashboardWidgets), `94c0962`
  (память под DashboardWidgets). Ветка `web-contract-hardening` запушена в origin. ruff/mypy чисто,
  **71 pytest** прошло.
- **Мерцание/чёрные кадры linear-индикатора (этот прогон):** причина — рендерер анимировал фокус от
  `time.monotonic()`, поэтому `DashboardSource.latest()` отдавал НОВЫЙ кадр каждый тик trcc (15 fps),
  что сидело у потолка панели ~18 fps и переполняло USB → дропы/чёрное. Первый фикс `0941265` сделал
  источник реактивным (только на изменение setup/values/palette) — мерцание ушло, но при статичных
  данных менеджера анимация полностью замирала. **Итоговый фикс `9b72642`**: `DashboardSource` рендерит
  непрерывно с каденсом `fps` (по умолчанию 8.0, env `HARDWARESCREEN_RENDER_FPS`) и немедленно при
  реальном изменении контента; между кадрами возвращает кэш. Так анимация жива всегда, но не генерирует
  кадры каждым тиком (потолок USB не перегружается). `anim_step = 1/fps` → фокус-цикл идёт в реальном
  времени. ruff/mypy чисто, 71 pytest, синтетика: за 2с при пуле 15 fps → ~6.5 fps новых кадров (не
  заморожено, не 15). **Запушено.**
- **USB-панель в прогоне ЗАКЛИНИЛА:** после череды аварийных SIGTERM демона посреди передачи —
  `ConnectDevice failed: LyLcd handshake I/O failed: USB write failed: [Errno 60] Operation timed out`;
  reset через pyusb проходит, но handshake всё равно таймаутит. **Решено физическим replug**
  (панель вернулась; демон стартовал заново с тем же окружением — `showing: the Dashboard module`).
- **«no data» по центру (этот прогон):** причина — модуль и `DashboardSource` делят ОДИН
  `BridgePoller`; `_loop` модуля первым вызывает `poll()` и потребляет bump seq, а источник в
  `latest()` вызывал голый `poll()`, получал `None` и терял состояние → пустой кадр «no data».
  Фикс `64b0b33`: `latest()` использует `poll() or self._poller.last` (как `_loop`), поэтому
  держится последнее реальное состояние даже при замороженном seq. Плюс продюсер
  `hardwarescreen-stubdata` не был запущен (seq замер на 29698, updated_at ~23 мин) — запущен заново
  (pid 88709, `--config stubdata.yaml`), seq снова растёт. Демон перезапущен → загрузил фикс.
  ruff/mypy чисто, 71 pytest, синтетика: замороженный seq держит реальный кадр + анимацию
  (~8 fps), не «no data». **Запушено.**
- **Qt linear-индикатор: выделение/тень (этот прогон, после `64b0b33`).** Введён
  `SelectionLayoutManager` в `adapter/layout.py` — геометрия ВСЕГДА постоянная (равные высоты +
  равномерный зазор), выделение — чисто render-side эффект. Выделенный бар растёт вокруг центра
  (клампится в `edge_margin`, не вылезает за панель при n≤2), соседние гаснут
  (`DIM_MIN=0.35 + DIM_RANGE=0.65*f`), выделенный в foreground (z-порядок). Рендерер
  `adapter/linear_dashboard.py` рисует бары `QPainter`-ом в `QImage` (без `QApplication`; glow —
  PIL-гауссово размытие, поэтому совместим с потоком демона). Коммиты: `3f46069` (зазор выделения
  0.3mm, позже вытеснен), `f24a274` (постоянная геометрия + render-side выделение),
  `886d4ca` (гладкая тень ТОЛЬКО наружу: силуэт размывается, затем вырезается собственный
  footprint — свет не тонирует заливку; + правило русского языка для комментариев).
- **Правило языка уточнено (в этом прогоне):** в `AGENTS.md` теперь: идентификаторы — латиницей,
  ПОЯСНЕНИЯ (комментарии/docstring) — на русском; существующие англоязычные комментарии переводим
  по мере правки файла, а не массово.
- **Ворота человеко-читаемого кода (этот прогон, НЕ ЗАКОММИЧЕНО):** добавлен в `AGENTS.md` блок
  «Ворота человеко-читаемого кода» — после каждой доработки проверять, что код читается как логика,
  без «магических» сокращений (`bx0`/`bw`/`bh`, одиночные `t`/`l`/`w`) и без «магических» строк/чисел
  (`"RGB"`, `48`, `16`) в теле функций; все они выносятся в именованные константы
  (`UPPER_CASE_WITH_UNDERSCORES`) блоком в конце файла. Сейчас идёт рефактор
  `adapter/linear_dashboard.py`: `_paint_glow_pil`/`_paint_bar` переписаны на читаемые имена
  (`box_left`/`box_top`/`bar_height`/`corner_radius`), магические литералы вынесены в константы в конец
  файла. ruff/mypy чисто, 82 pytest, баг-хунт (см. ниже). **НЕ ЗАКОММИЧЕНО.**
- **Случайный выбор и скорость анимации (этот прогон, НЕ ЗАКОММИЧЕНО):** выбор бара
  теперь меняется ВМЕСТЕ со сменой данных и держится всё окно (~5 с), а не мигает на
  собственном таймере. В `adapter/linear_dashboard.py`: `_content_signature` (сигнатура
  setup/values/palette) → при её смене `_advance_selection(now, count)` чередует
  select/skip («через раз»), `_selection_focus` плавно наращивает/гасит и держит
  выделение до следующего окна. Позиция выбранного бара случайна
  (`_rng.randrange(count)`). Значения «добегают» до цели за **0.3 с** (фиксированная
  длительность в `_eased_progress`: `from + (target-from)*ease_out(frac)`, вместо
  старой экспоненты; `_last_time`/`exp` убраны, добавлены
  `_progress_from/_progress_target/_progress_start`). Константы `VALUE_TWEEN_S=0.3`,
  `SELECTION_TRANSITION_S=0.6`; удалены `SELECTION_PROB/HOLD/IDLE`, `selection_prob`.
  `_ensure_qt` с фолбэком `_fallback_ease_out_cubic`. `stubdata.yaml` `poll_s=5.0`
  (окно данных ~5 с). Тесты `tests/test_linear_dashboard.py` (чередование, удержание,
  случайные позиции, static-layout off, 300 мс, сигнатура). ruff/mypy чисто,
  88 pytest, баг-хунт: 0 critical / 0 major (7 minor, исправлены неточный комментарий
  и новая строка). Демон и продюсер перезапущены → панель `showing: the Dashboard module`.
  **НЕ ЗАКОММИЧЕНО.**
- **Диагностика «тема не показывается» — РАЗРЕШЕНО (этот прогон).** Дашборд рисуется как
   **прозрачный оверлей поверх темы** (`module_overlay`), а не как background-источник
   (`module_source`). Ключ к пониманию: `host.overlay` в модуле — это **overlay-порт**
   (`trcc/services/modules.py` `_Overlay.show` → `services.show_overlay` → `set_module_overlay`),
   он НЕ трогает `module_source`. `display_source` (для «showing: ...») смотрит на `module_source`,
   поэтому при живом оверлее `trcc status` честно пишет «the theme's background» — это ожидаемо,
   тема продолжает играть ПОД дашбордом (StopVideo не вызывается). В логе подтверждено:
   `composite renderer (linear-left + gauge rings right)`, `show_overlay: 0416:5408 <- dashboard.source`,
   `set_module_overlay: key=0416:5408 module=dashboard`. Прозрачный RGBA-оверлей (`e367591`)
   не запекает тему — она просвечивает в не-прозрачных зонах. Свежий демон (bgp_…, pid 13289)
   с `HARDWARESCREEN_COMPOSITE=rings_right` показывает дашборд поверх темы. Установленная копия
   модуля синхронизирована с dev. Для диагностики модуль временно писал в `/tmp` (убрано).

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
1. **Визуально подтвердить на панели** (мне недоступно): дашборд с живыми данными и анимацией фокуса,
   БЕЗ «no data» и без мерцания/чёрных вспышек. Продюсер (pid 88709) и демон (pid 89411) запущены,
   seq растёт, `trcc status` → «showing: the Dashboard module», panel_keys=('0416:5408',).
2. При желании подкрутить `HARDWARESCREEN_RENDER_FPS` (меньше/больше) и `HARDWARESCREEN_FOCUS_COLOR`;
   при мерцании — снизить `HARDWARESCREEN_RENDER_FPS` (больше головы ниже потолка ~18 fps).
3. Аппаратный чек-лист §A-G пройден на устройстве, кроме §E полного video-фон-switch (нужен
   `theme_cmd` в `manager.yaml`). B и F проверены вживую с физическим вынуть/воткнуть.

## Заметки для передачи
- USB-доступ запрещён для trcc daemon -> какой-то клиент `hardwarescreen run`/`supervise` держит
  устройство; остановить его.
- Демон может зависнуть на пере-загрузке отравленного кэша (напр. `e005.mp4` как 404 HTML); повтор
  восстанавливает.
- Тест устройства (без приложения): `python tools/probe_ly.py`, `python tools/probe_fps.py`
  (exit 1, "Device not found").
- `FrameSource.latest()` trcc возвращает СЫРОЙ RGB (не JPEG); обёртка модуля обязана отдавать
  `img.tobytes()`.