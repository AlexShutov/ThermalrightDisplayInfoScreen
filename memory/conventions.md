# conventions.md — код, лицензии, git, репозиторий

## Рантайм / код
- Чистый Python; зависимости `pyusb`, `pillow`, `psutil` (+ опционально `trcc-linux`). Без AI/LLM на
  сборке/запуске; `pip install .` должно его запускать.
- Раскладка `src/`, `pyproject.toml`, точка входа `hardwarescreen = hardwarescreen.__main__:main`.
- YAML-первичный конфиг (`config.yaml`); добавлять опции в конфиг до загрузки в `config.py`.
- Держать платформенные особенности (ffmpeg hwaccel, headless Qt `QT_QPA_PLATFORM=offscreen`,
  DPI/devicePixelRatio, ветку температуры в `sensors`, версию Python, udev/systemd) за `config.py`,
  чтобы порт был конфиг-онли.
- Заголовок лицензии в `.py`-файлах: `SPDX-License-Identifier: MIT`.
- Пробы (`tools/probe_ly.py`, `tools/probe_fps.py`) — dev-инструменты, не поставляются.

## Лицензии
- Наш код = **MIT**. `trcc-linux` и **модуль Qt-адаптера** = **GPL-3.0**, внешние интеграции
  (субпроцесс/установленная зависимость). Никогда не копировать их код в наши MIT-исходники.

## Git / политика коммитов
- Один коммит на задачу; сообщение читается как описание задачи. Коммитить задачу, когда она готова.
- Ручные команды сверены с собственной конвенцией памяти репо trcc (его цикл старта/завершения CLAUDE.md).
- **Шорткаты в чате** (`AGENTS.md`, раздел «Команды-шорткаты»):
  - `кп` — закоммитить все изменения атомарными коммитами, проверить сборку/харнесс (pytest — жёсткий гейт;
    ruff/mypy — без новых ошибок), затем запушить. Применяется к обоим репозиториям: сначала коммиты+пуш
    сабмодуля `DashboardWidgets`, затем подъём gitlink и коммиты+пуш main. Сообщения `<тип>: <описание>` на русском.
  - `ктп` — то же, что `кп`, но с полным перетестом (pytest+ruff+mypy) после каждого атомарного коммита.

## Репозиторий
- Публичный: `github.com/AlexShutov/ThermalrightDisplayInfoScreen`.
- Коммиты: `47ba71c` (trcc engine + daemon), `322765b` (рендер в полном разрешении 1920x440),
  `b9f3a38` (licenses README), `fd84893` (модуль-заглушка), `3c24f89` (каталог тем PDF).
- `trcc_themes_catalog.pdf` — WB-стиль сетка всех 210 тем с превью (14 страниц), закоммичен.
- **`DashboardWidgets`** (публичный репозиторий, `github.com/AlexShutov/DashboardWidgets`) —
  работа с Activity Rings в стиле Apple и новый линейный индикатор вынесены ИЗ `hardwarescreen`
  в отдельный репозиторий и пере-подключена здесь как **git-сабмодуль**. Его история (один коммит на задачу):
  Pillow-рендерер + превью → чистая модель focus-shift timeline + тесты → Qt-превью focus + ассеты →
  пакет `activity_linear_indicator` (Qt-бары по аналогии с Rings) + переименование в **DashboardWidgets**.
  Пакеты `activityrings` и `activity_linear_indicator` (`src/activityrings/`, `src/activity_linear_indicator/`),
  MIT; PySide6 — опциональный `qt`-экстра для превью.
  В основном репо сабмодуль лежит в `DashboardWidgets/`; бывшие
  `src/hardwarescreen/rings*.py`, `tests/test_rings_focus.py`,
  `tools/*rings*preview.py` и `docs/rings*`/`activity_rings_compare.pdf` удалены.
  Обновление сабмодуля: `git submodule update --init --recursive`.