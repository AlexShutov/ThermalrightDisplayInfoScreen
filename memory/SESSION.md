# SESSION.md — живая передача (читать первой)

Состояние текущего прогона. Постоянные факты живут в индексе `MEMORY.md` + файлах-темах; этот файл
держит только «где мы и что дальше» для ЭТОГО прогона. Завершение: заархивировать этот файл в
`memory/sessions/YYYY-MM-DD-<slug>.md`, добавить строку в `README.md` этой папки, затем переписать его.

## Команды-шорткаты (в чате) — см. также `AGENTS.md`
- **`кп`** — закоммитить все изменения **атомарными коммитами**, проверить сборку/харнесс (pytest — жёсткий
  гейт; ruff/mypy — без новых ошибок), затем **запушить**.
- **`ктп`** — то же, что `кп`, но с **полным перетестом после каждого атомарного коммита**, затем запушить.
- Оба — к **обоим репозиториям**: сначала коммиты+пуш сабмодуля `DashboardWidgets`, затем подъём gitlink и
  коммиты+пуш main. Сообщения `<тип>: <описание>` на русском (feat/fix/refactor/docs/chore), один коммит = одна задача.

## Где мы
Задача: **менеджер тем / SDK** поверх `trcc daemon` (локальный HTTP-сервис + модуль-адаптер, рисующий
дашборд поверх темы). Dev + runtime = **macOS** сегодня; Pi 5 / Orange Pi 6 Plus — в будущем.

Дашборд — **прозрачный RGBA-оверлей поверх живой темы** (`module_overlay`, не background): композит
«линейный индикатор слева + кольца датчиков справа» (`HARDWARESCREEN_COMPOSITE=rings_right`). Рантайм:
trcc daemon + менеджер (`hardwarescreen-manager`) + продюсер-заглушка (`hardwarescreen-stubdata`).

## Сделано (этот прогон) — закоммичено и запушено
- **Пакет `common` в сабмодуле `DashboardWidgets` + единый фон `IndicatorBackgroundDrawer`**
  (submodule `8dcab3f`, поднят в main `bb52b61`): общие хелперы `common/easing` (clamp/ease_out_cubic/
  lerp/lerp_color), общая focus-модель `common/focus` (FocusTimelineBase/layout/default_schedule/
  progress_track/seed_progress_targets), отрисовка фона `common/background` (DEFAULT_BACKGROUND +
  IndicatorBackgroundDrawer, за основу — линейный индикатор: непрозрачный (16,18,24) или transparent).
  Оба индикатора (`activityrings`, `activity_linear_indicator`) рефакторены на `common`, локальные
  `_lerp/_lerp_c/ease_out_cubic/_lerp_frac` удалены. API сохранён (rings_focus/linear_focus/linear/rings).
- **Читаемые имена по всему проекту** (гейт «понятного кода»): `a0/a1/c1/c2/r2/g3/tmp/t0/s2/r/r2` и т.п.
  → `angle_start/angle_end/color_start/color_end/gap_3/temp_path/sample_start/store/result_first/...`
  (main `d2112f5`, submodule `2885c1f`).
- **Гейт проверки имён перед коммитом** (main `cd346be`, submodule `155dc3e`): `tools/check_names.py`
  (AST, без зависимостей) ловит числовые сокращения и запрещённые ярлыки; `githooks/pre-commit`
  (через `core.hooksPath`) гоняет его по staged-файлам. `check_names.py --all` → 0 нарушений.
- **Документация**: `AGENTS.md` — человеческие формулировки («Понятный код», «Проверка на ошибки») +
  раздел «Команды-шорткаты» (main `cc43bdb`). `memory/conventions.md` — шорткаты; `memory/SESSION.md` — здесь.
- **Единый нейтральный фон колец через `IndicatorBackgroundDrawer.backdrop()`** (submodule `e7dbacc`):
  подложка каждого кольца — один цвет (по умолчанию тёмный `DEFAULT_BACKGROUND`, композит передаёт
  `palette["muted"]`), а не цвет самого кольца — разноцветный фон убран. Композит:
  `dashboard/app.py` передаёт `track_color=palette["muted"]` (main `ebb60b4`). **Требует рестарта daemon**
  (`.venv-trcc/bin/python -m trcc daemon`) для подхвата — старый процесс держит старый `rings.py` в памяти.

## Ворота (актуальны)
- main: `tools/quality_check.sh` (ruff+mypy+pytest; pytest — жёсткий гейт). ruff — чисто; pytest зелёный;
  mypy — только 8 пре-существующих PyQt-`attr-defined` в `linear_dashboard.py` (не чинить).
- сабмодуль `DashboardWidgets`: pytest зелёный (88); ruff `src`+`tools` чисто; mypy — 11 пре-существующих
  PySide6-стабов в `linear.py`. Пре-существующие ruff-выводы в `tests/test_rings_focus.py` — не чинить.
- Общий гейт: `tools/check_names.py --all` → 0 нарушений в обоих репозиториях.

## Открытые вопросы / следующий шаг
- Визуально подтвердить на панели композит-кольца и фикс яркости темы (оба ранее закоммичены; демон с
  композитом жив, pid 63249). Подтверждение на железе — «потом», не мешает сборке.
- Переключатель менеджер->trcc (CLI `trcc display play <id>` vs REST-API trcc) — ещё открыт.
- Настройка колец: менять константы/`HARDWARESCREEN_RINGS_*` и перезапускать демон.

## Заметки для передачи
- USB-доступ запрещён для trcc daemon -> какой-то клиент `hardwarescreen run`/`supervise` держит устройство;
  остановить его.
- `frame_renderer` модуля возвращает RGBA (`w*h*4`) при композите, RGB (`w*h*3`) у Pillow-пути; обёртка
  обязана отдавать `img.tobytes()`.
- Тест устройства: `python tools/probe_ly.py`, `python tools/probe_fps.py`.
- Демон может зависнуть на пере-загрузке отравленного кэша; повтор восстанавливает.