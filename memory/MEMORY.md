# MEMORY.md — индекс постоянных файлов-тем

Сначала читать **`SESSION.md`** (живая передача), затем этот индекс, потом открывать файлы-темы,
релевантные задаче. Индекс — это каталог, а не детали — у каждой темы свой файл. Постоянные факты
никогда не кладутся в `SESSION.md`; они живут в файле-теме + этом индексе.

Авто-загружается в контекст. Держать индекс в пределах ~200 строк; детали — в связанных файлах.

## Файлы-темы

- **`hardware_protocol.md`** — факты устройство/протокол (VID/PID, handshake, чанки кадра, потолок 450 KB,
  ~18 FPS, возврат-к-логотипу, жизненный цикл USB + правила `max_frame_bytes`, конфликт USB).
- **`trcc_engine.md`** — движок trcc-linux (`trcc daemon`), каталог облачных тем (210 реальных тем,
  день `d037` / ночь `d029`), ffmpeg/7z, превью, вердикты по железу Raspberry Pi.
- **`trcc_module_api.md`** — API расширения trcc: BaseModule, FrameSource/background.show/end,
  BackgroundKind, frames.watch, гейт по capability, шаги установки, наш модуль-заглушка.
- **`architecture.md`** — протокол решения менеджера тем: две плоскости, FastAPI+uvicorn, атомарный
  state-file мост с `seq`, контракт API, жизненный цикл рендеринга, Qt-адаптер (вариант A), стиль день/ночь.
- **`testing.md`** — стратегия тестирования, переиспользованная из trcc: инжектируемые порты
  (FrameSink/SensorSource/TrccBackground/StateStore), MockPanel + fake-транспорт, pytest `tests/` +
  харнессы smoke в `dev/`, ratchet покрытия, верификация-прогоном.
- **`trcc_borrowed.md`** — каталог дизайн-идей trcc, которые мы адаптируем (только дизайн, НИКОГДА не
  копируем GPL-код): пайплайн download/install, retry/backoff с классификацией errno, отправитель
  last-command-wins, атомарные+версионированные настройки, многопроцессное логирование, health/report,
  жизненный цикл daemon/IPC, установщик модулей, самозащита сенсоров, безопасные file/JSON-хелперы,
  паттерны FastAPI/pydantic, merge с одной рассылкой, мелкие утилиты. СЛЕДОВАТЬ ЭТОМУ при разработке.
- **`conventions.md`** — код/стиль, лицензии (наши MIT / внешние GPL-3.0), политика git/коммитов, репозиторий.
- **`conventions.md`** также фиксирует сабмодуль `ActivityProgressRingsQt` (Activity Rings
  вынесены в отдельный публичный репозиторий `github.com/AlexShutov/ActivityProgressRingsQt`,
  подключён здесь как git-сабмодуль в `ActivityProgressRingsQt/`; пакет `activityrings`, MIT, PySide6 опционально).
- **`hardware_verification.md`** — чек-лист на экране (только с реальной панелью + trcc daemon):
  композит-поверх-темы, attach-race, бюджет 450 KB, FPS, день/ночь без рестарта, конфликт USB.
  Каждая фаза иначе доказана headless через `tools/quality_check.sh`.

## Живое состояние
- **`SESSION.md`** — где мы, решения текущего прогона, следующий шаг. Переписывается каждую сессию.
- Архив прошлых передач: `memory/sessions/` (датированы, неизменяемы) — см. его `README.md`.

## Жёсткие правила, которые стоит повторить здесь
- Рантайм — чистый Python; зависимости `pyusb`, `pillow`, `psutil` (+ опционально `trcc-linux`).
  Без AI/LLM на запуске.
- `max_frame_bytes = 450000`; keepalive `keepalive_s = 2.0`; жизненный цикл USB — только в `supervise.py`.
- Наш код = **MIT**; `trcc-linux` + Qt-адаптер-модуль = **GPL-3.0** внешние (не копировать в MIT).
- Платформенные особенности за `config.py`, чтобы порт на Pi 5 / Orange Pi 6 Plus был конфиг-онли.