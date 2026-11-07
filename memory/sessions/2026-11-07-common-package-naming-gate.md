# SESSION.md — живая передача (читать первой)

Состояние текущего прогона. Постоянные факты живут в индексе `MEMORY.md` + файлах-темах; этот файл
держит только «где мы и что дальше» для ЭТОГО прогона. Завершение: заархивировать этот файл в
`memory/sessions/YYYY-MM-DD-<slug>.md`, добавить строку в `README.md` этой папки, затем переписать его.

## Где мы
Задача: **менеджер тем / SDK** поверх `trcc daemon` (локальный HTTP-сервис + модуль-адаптер, рисующий
дашборд поверх темы). Dev + runtime = **macOS** сегодня; Pi 5 / Orange Pi 6 Plus — в будущем.

Дашборд рисуется как **прозрачный RGBA-оверлей поверх живой темы** (`module_overlay`, не background):
тема продолжает играть ПОД гейджами. Композит — «линейный индикатор слева + кольца датчиков справа»
(`HARDWARESCREEN_COMPOSITE=rings_right`). Рантайм: trcc daemon (`.venv-trcc/bin/python -m trcc daemon`)
+ менеджер (`hardwarescreen-manager`) + продюсер-заглушка (`hardwarescreen-stubdata`).

## Сделано (этот прогон / недавно)
- **Диагностика «тема не показывается» — РАЗРЕШЕНО.** `trcc status` пишет «the theme's background» даже
  при живом дашборде, потому что `host.overlay` — это overlay-порт (`_Overlay.show` →
  `set_module_overlay`), который НЕ трогает `module_source`. `display_source` смотрит на `module_source`,
  поэтому при оверлее статус честно говорит «тема». Ожидаемо, не баг. Дашборд реально поверх темы,
  тема просвечивает в прозрачных зонах.
- **Геометрия композита-колец (ЗАПУШЕНО `2308fae`/`2c73375` + сабмодуль `3c6edb1`):** только 4 кольца
  (`COMPOSITE_MAX_RINGS=4`), зона колец — полная высота панели, прижата вправо (`rings_x = panel_w -
  panel_h`), поля 2мм (`COMPOSITE_RINGS_MARGIN_MM=2.0`, `COMPOSITE_RINGS_OUTER_FRACTION=0.96`), зазор 1мм
  (`RING_GAP_MM=1.0`), радиус внутреннего кольца 10мм (`RINGS_INNER_RADIUS_MM=10.0`). Env-переопределения:
  `HARDWARESCREEN_COMPOSITE_MAX_RINGS`, `HARDWARESCREEN_RINGS_GAP_MM`, `HARDWARESCREEN_RINGS_INNER_RADIUS_MM`.
  В сабмодуле `DashboardWidgets`: `render_gauge_rings(..., outer_fraction, edge_margin, gap_px,
  inner_radius_px)` → `SelectionRingsLayoutManager`; `BaseRingsLayoutManager.ring_gap_px`/`inner_radius`.
  Ворота: main ruff/pytest зелёные (mypy — только 8 пре-существующих PyQt в `linear_dashboard.py`);
  сабмодуль pytest зелёный (4 ruff пре-существующих в `test_rings_focus.py`).
- **БАГ ЯРКОСТИ ТЕМЫ — ИСПРАВЛЕН (этот прогон, НЕ ЗАКОММИЧЕНО).** Симптом: перетаскивание ползунка
  яркости темы в `theme_reviews/preview_app.py` затемняло и тему, и дашборд-контролы. Причина: модуль
  вызывал `set_dev_brightness` (`trcc display set-brightness`) → в trcc `build_frame`
  (`src/trcc/services/display.py:379→396` и `539→545`) `apply_brightness` применяется к ВСЕМУ кадру
  ПОСЛЕ композита модульного оверлея (дашборда) поверх темы, поэтому нативная яркость затемняла и
  контролы дашборда. Фикс в `theme_reviews/preview_app.py`:
  - Убран `set_dev_brightness(...)` из `_on_brightness_debounce`, `_restore_normal`, `_apply_baked`,
    `_teardown_brightness`; убран импорт. Нативная яркость больше не трогается вообще.
  - Яркость темы несёт ТОЛЬКО запечённый ffmpeg-клип (`encode_brightness_video`, eq=brightness) — он
    влияет только на видео темы, оверлей не затемняет.
  - `_apply_baked` при неудаче загрузки клипа падает на `_load_panel(tid, force=True)` (яркая тема).
  - `_PanelLoader.request(tid, force=False)` + `run` (`force or tid != self._last`): смогли обойти
    дедуп одного и того же tid, чтобы реально перезагрузить яркую тему после запечённого клипа.
  - `_restore_normal` и фолбэк `_apply_baked` используют `force=True`.
  Ворота: ruff/pytest зелёные; семантический баг-хунт: CLEAN (3 минорных пре-существующих — тема без
  локального src не печётся, GUI-preview не обновляется в фолбэк-ветке, возможный «loading…» при
  дедуп-запросе). Программа `preview_app.py` перезапущена (pid 75595). **НЕ ЗАКОММИЧЕНО.**

## Консолидация индикаторов — уже сделано (не трогать)
Проверено (код + `gh repo list AlexShutov`): оба индикатора УЖЕ в одном репозитории `DashboardWidgets`
(сабмодуль `DashboardWidgets/`, `github.com/AlexShutov/DashboardWidgets`), как соседние под-пакеты:
`DashboardWidgets/src/activityrings/` (кольцевой) и `DashboardWidgets/src/activity_linear_indicator/`
(линейный, добавлен коммитом `0733a82` при переименовании `ActivityProgressRingsQt` → `DashboardWidgets`).
Отдельного «репозитория линейного индикатора» НЕТ; пустого репозитория для удаления тоже нет.
В главном репо `src/hardwarescreen/adapter/linear_dashboard.py` — это app-рендерер дашборда (MIT), он лишь
импортирует `activity_linear_indicator` из сабмодуля, а не является отдельным индикатором-репозиторием.
Ссылки в приложении (`activityrings`, `activity_linear_indicator`) уже указывают на под-пакеты сабмодуля.
**Решение пользователя (07.11): переименовывать/удалять репозитории НЕ нужно** — структура консолидирована.
Отдельного `AGENTS.md` в `DashboardWidgets` нет; «более продвинутое окружение» там — pyproject с
ruff/mypy/pytest-конфигом (`tools/qlinear_preview.py`, `tools/qrings_preview.py`, `docs/linear*` и т.д.).

## Открытые вопросы (этот прогон)
- Переключатель менеджер->trcc — это CLI `trcc display play <id>` (опция через `theme_cmd`);
  альтернатива через REST-API trcc всё ещё открыта.

## План (список фаз)
- **Фаза 0** цикл подтверждён на железе. Готово.
- **Фаза 1** каркас менеджера — Готово (headless).
- **Фаза 2** источник данных-заглушка — Готово.
- **Фаза 3** модуль-адаптер дашборда — Готово (композит поверх темы).
- **Фаза 4** стиль день/ночь через `/settings` — контракт + палитры есть; взаимодействие подтверждено headless.
- **Будущее** железа и deploy: Pi 5 / Orange Pi 6 Plus.

## Следующее действие
1. Закоммитить/запушить фикс яркости темы (`theme_reviews/preview_app.py`) после подтверждения на панели:
   перетаскивание ползунка должно затемнять ТОЛЬКО тему, дашборд-контролы остаются яркими.
2. Визуально подтвердить на панели композит-кольца (`HARDWARESCREEN_COMPOSITE=rings_right`, демон
   pid 63249; профиль: бар 10мм/зазор 3мм/паддинг 5мм/align left, кольца 4 шт, радиус внутр. 10мм).
3. При дальнейшей подстройке колец менять константы/`HARDWARESCREEN_RINGS_*` и перезапускать демон.

## Заметки для передачи
- USB-доступ запрещён для trcc daemon -> какой-то клиент `hardwarescreen run`/`supervise` держит
  устройство; остановить его.
- `frame_renderer` модуля возвращает RGBA (`w*h*4`) при композите, RGB (`w*h*3`) у Pillow-пути;
  обёртка обязана отдавать `img.tobytes()`.
- Тест устройства: `python tools/probe_ly.py`, `python tools/probe_fps.py`.
- Демон может зависнуть на пере-загрузке отравленного кэша (напр. `e005.mp4` как 404 HTML); повтор
  восстанавливает.