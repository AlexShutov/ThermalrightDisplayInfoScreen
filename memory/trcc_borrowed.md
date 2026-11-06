# trcc_borrowed.md — дизайн-идеи trcc, которые мы адаптируем (НЕ скопированный код)

Мы заимствуем только ПАТТЕРНЫ/КОНВЕНЦИИ из GPL-3.0 trcc, переимплементируя их в своём MIT-коде.
Ссылки на исходники — это `.venv-trcc/lib/python3.14/site-packages/trcc/` (9.12.0). Следовать этому
при разработке.

## 1. Пайплайн загрузки/установки (облачные темы)
- Фоновый воркер очереди с дедупликацией, ограниченным ретраем `(30,120,600)` с и публикацией ошибок
  каждому открытому клиенту, а не только в лог. (`adapters/infra/data_install_runner.py:80-237`, `services/data_install.py:43-117`)
- Ступенчатое атомарное извлечение: писать `*.tmp` -> распаковать в `target.partial` -> `rename`
  только при успехе; краш не оставляет «похоже заполненного». `_unwrap_nested_dir` схлопывает одну
  обёрточную поддиректорию. (`adapters/repo/data_install.py:168-231`)
- Защита от zip-slip: сначала список членов, отклонять любой, выходящий за целевой каталог, распаковка
  с таймаутом; нет `7z` -> предупредить и пропустить, никогда не падать. (`adapters/repo/data_install.py:78-140`)
- Фетчер: `max_bytes` (читать `max_bytes+1` для детекта превышения), `allow` на URL И на каждом
  редиректе (`_CheckedRedirects`), `public_only` (отклонять разрезолвленный непубличный peer — ломает
  DNS rebinding), бандл certifi на macOS. (`adapters/repo/http.py:71-221`)
- Best-effort ffmpeg-миниатюра через `_run_ffmpeg_or_warn` (предупредить, никогда не бросать);
  регенерировать PNG только если он старше MP4; дозаполнять вне пути запроса. (`services/cloud_theme.py:147-226`)
- Пер-ОС алиасы инструментов `TOOL_ALIASES` (ffmpeg->ffmpeg/ffmpeg7, 7z->7zz/7za/7zr); `locate()`
  отличает установленный-но-не-в-PATH от отсутствующего. (`core/toolchain.py:49-194`)

## 2. Классификация ошибок + retry/backoff (USB / транзиентный IO)
- `RecoveryTracker`: обходить `exc.__cause__` на предмет USB-errno (EIO/EBADF/ENXIO/ENODEV/ESHUTDOWN) ->
  "non-disconnect"/"disconnect"/"threshold"; рейт-лимит предупреждений (раз/30 с); EACCES/EBUSY
  получают действенные пер-ОС хинты; `note_success` сбрасывает. (`core/device_recovery.py:42-223`)
- `ReconnectWatcher`: удваивающий backoff 3 с -> 60 с (300 с "slow" для никогда не виданных); `arm()`
  не перезапускает backoff при неудачной попытке; namespaced `KEY_PREFIX`, чтобы никогда не останавливать
  отправитель устройства. (`services/reconnect_watcher.py:44-135`)
- `BaseSendTask`: контракт подкласса (`KEY_PREFIX`/`DEFAULT_INTERVAL_S`/`_run`), пропуск когда
  away/game-mode, `FIXED_BEAT` beat-vs-rest. (`services/_send_task.py:35-127`)

## 3. Сериализация отправки last-command-wins
- `DeviceSender`: ОДИН слот `_pending`; новее вытесняет старшее (`superseded`), ставит предыдущему
  ожидающему `ok=True`, чтобы тот не завис; запись происходит ВНЕ замка inbox (медленный USB никогда
  не блокирует submit); `wait=True` одноразовый канал результата; keepalive пересылает `_last` (0.150 с);
  `exclusive()` держит провод на время много-кадровой загрузки. (`services/device_sender.py:138-291`)
- Планировщик один-поток-на-задачу + синхронный близнец для тестов. (`adapters/infra/send_scheduler.py:65-128`)
- Обобщённый `QueueWorker` (лениво-спавнящийся поток-демон, сентинел `_STOP`, `stall_hint`). (`adapters/infra/_worker.py:45-140`)

## 4. Персистентность настроек (bridge state / persistent JSON)
- Атомарное сохранение: `*.json.tmp` -> запись -> `flush` -> `os.fsync` -> `tmp.replace(path)`;
  `ConfigError` на OSError. (`services/settings.py:1210-1230`)
- Версионирование схемы + миграции на месте: версии поднимаются ТОЛЬКО при семантическом изменении;
  каждый шаг гейтится версией, которая его ввела, а не «старше текущей»; отсутствие `schema`
  читается как до-версионное. (`services/settings.py:135-169,1294-1454`)
- Раздельная резолюция конфига путь-записи vs путь-чтения (чтобы переименование не «застрявило»
  пользователей). (`services/settings.py:1238-1278`)
- Пер-полевые JSON-конвертеры с сентинелом `_DROP`; терпимы к незнакомым полям. (`services/settings.py:1286-1592`)

## 5. Логирование (менеджер + демон + адаптер делят один лог)
- Идемпотентный `configure_logging`, вращающийся файл (1 MB x 5) + `<stem>.latest.log` на прогон,
  `_RunOwnership`, чтобы клиент, стартующий в середине прогона, дописывал, а не затирал; `stderr`
  зеркалит WARNING+; тегирует хендлеры, чтобы реконфиг закрывал только свои (без залипшего fd замка).
  (`adapters/infra/logging.py:615-758,480-548`)
- Мульти-процессно-безопасный вращающийся хендлер: сайдкар `.lock`, `os.stat` общего размера +
  `st_ino` (детект ротации соседа) вместо `stream.tell()`. (`adapters/infra/logging.py:111-429`)
- `RenderOnceRotatingFileHandler` (кэш Formatter.format на запись). (`adapters/infra/logging.py:67-102`)
- `ClassContextFilter` подставляет `record.classname` -> `Module.Class.method:line` бесплатно. (`adapters/infra/logging.py:579-612`)
- `log_chain`/`tail_log`/`log_problems`: читать всю цепочку ротации, выбирать по значимости (INFO+,
  или блоки WARNING/ERROR), ограниченный deque; `OwnLines` исключает собственный процесс отчёта. (`adapters/infra/logging.py:761-1055`)
- Ранний буфер логирования (`MemoryHandler`) пока неизвестен реальный путь; `ensure_configured`
  защищает от второго вызова, молча понижающего `-v`. (`adapters/infra/logging.py:1076-1231`)

## 6. Проверки состояния + debug-отчёт (наш /health + диагностика)
- Реестр проверок: функции -> `HealthCheckResult(name, severity=OK|WARN|FAIL, message, fix_hint)`;
  агрегация `worst_severity/fail_count/warn_count`; дёшево, никогда не бросает. (`adapters/diagnostics/health.py:670-705`, `core/diagnostics.py:24-55`)
- Пробы записываемости каталога (записать+unlink пробный файл). (`health.py:132-171`)
- Детект целостности установки: устаревшие `.pyc`, дубликаты бинарей на PATH. (`health.py:57-105`)
- Отчёт graceful-degrade по всему логу; `_scrape_handshake_lines` достаёт самую старую первую-в-сессии
  строку из всей цепочки (хвост её теряет). (`adapters/diagnostics/debug_report.py:116-184,492-529`)

## 7. Жизненный цикл демона / IPC
- `ensure_daemon`: never-downgrade (заменять только старшее, новее оставлять с предупреждением);
  спавн с `sys.executable -m trcc daemon`, `cwd="/"`, `start_new_session=True`, DEVNULL, `close_fds`;
  снимать с потомка env-переключатель демона, чтобы он не проксировал сам на себя. (`daemon.py:169-232,400-420`)
- `_wait_for_the_spawn` трактует child-exit-до-ответа как свидетельство; затем таймаут 60 с и kill.
  (`daemon.py:235-271`)
- `AppLock` (flock) один App на пользователя; `held()` проверяет «starting or serving». (`ipc.py:204-250`)
- `SingleInstance` пер-UI сокет + raise-existing на LAN; на Windows — файловый замок. (`ipc.py:1188-1387`)
- `on_app_gone`: подписка только на `AppStopping` (не на `events`), различать намеренный стоп и краш.
  (`proxy.py:429-479`)
- Переподключение event-стрима с backoff (0.2 с удвоение до 10 с); всплывать EOF-навсегда. (`proxy.py:301-349`)
- ПРАВИЛО ДИЗАЙНА: обработчик запроса не должен иметь возможности уронить процесс, владеющий
  устройством (`_dispatch_envelope` ловит всё, помечает поднятые Commands). (`ipc.py:1110-1151`, `proxy.py:160-212`)
- Фабрика `_boot` + `_local_reason`: явно решать «общий движок или in-process»; не платить за
  рендерер/зависимости, которые не используешь. (`_boot.py:44-194`)

## 8. Установка модуля (наш путь установки SDK)
- Папка-установка требует developer-режима; согласие `accepted.matches(manifest.asks)` (ссылка
  согласия переживает unsigned); ставится `switch_on=False`; никогда не обновляется/не вычищается.
  (`services/module_install.py:225-292`)
- Атомарная подмена с restore-on-failure (удалить старое -> установить рядом -> на место ->
  restore + re-enable при любом исключении). (`services/module_install.py:339-363`)
- Порядок trust-цепочки: root+index проверены -> `min_trcc` -> согласие -> байты совпадают со
  списком -> подпись автора (пин на обновлении) -> распаковка рядом, затем подмена. (`services/module_install.py:110-164`, `services/module_trust.py`)
- Гейты отката: `set_module_index_seen` записывает только `max(seen,root/index)` (старый индекс
  отклоняется как откат). (`services/settings.py:742-755`, `services/module_install.py:110-124`)

## 9. Самозащита сенсоров / метрик (обёртка-заглушка)
- `_read` деградирует бросающий источник до None: первая ошибка по метке — warn, последующие — DEBUG.
  (`adapters/sensors/aggregator.py:277-304`)
- `unsupported()` = СТАТИЧЕСКОЕ множество «нет бэкенда здесь» -> withhold ключей, которые никогда не
  смогут иметь значение, никогда не рекламировать их как 0.0. (`aggregator.py:362-385,469-473`)
- `_refresh_if_stale`: инлайн-опрос, когда кэш старше интервала (избегает boot-time значений в
  долгоживущем headless-цикле). (`aggregator.py:489-525`)
- Логировать только то, что проход РЕАЛЬНО сделал, в пер-кадровом семействе. (`aggregator.py:94-113`)

## 10. Безопасные file/JSON/path-хелперы (чистые, переиспользуемые)
- `load_json_or_default` центральный фолбэк (FileNotFoundError debug, прочее OSError/JSON/Unicode warn).
  (`core/_safe.py:32-64`)
- `is_under` контейнирование с учётом symlink (резолвить обе стороны; на ошибке — «not under»).
  (`core/_safe.py:127-161`)
- `is_safe_zip_member` / `is_safe_user_name` (отклонять traversal/absolute/drive-letter/NUL/dot-prefix).
  (`core/_safe.py:67-124`)

## 11. HTTP API / pydantic (наш менеджер: FastAPI + pydantic v2)
- `WireView` = pydantic `RootModel[T]` с `ser_json_bytes="hex"` (объявлен один раз в адаптере).
  (`ui/api/schemas.py:32-51`)
- Проекционные модели ответов: отдавать только нужное клиенту, никогда абсолютные пути сервера.
  (`ui/api/schemas.py:73-103`)
- Ограничение-как-контракт: `Field(..., ge/le/pattern)`; опционально-с-сентинелом `bool | None` =
  "null его не меняет". (`ui/api/schemas.py:114-165,409-423`)

## 12. Рассылка / merge (менеджер -> адаптер)
- Один цикл рассылки гонит всех потребителей через ЕДИНЫЙ издатель (потребители не пере-поллят);
  подписывается на события смены pref, чтобы проснуться; читает интервал на каждой итерации.
  (`services/metrics_loop.py:50-307`)
- Персонализация (конвертация единиц температуры, фильтр HDD) в ОДНОМ месте; кэшировать СЫРОЙ сэмпл
  для потребителей на тик. (`services/metrics_loop.py:229-290`)

## 13. Мелкие утилиты
- `get_lan_ip`: UDP-проба маршрута по умолчанию, чтобы печатать достижимый URL вместо `0.0.0.0`. (`adapters/infra/network.py:15-31`)
- `Blob`/`BLOB_HEAD_BYTES`: отображать байты как `<n> bytes, first <16>: <hex>` вместо `repr` (один
  кадр 1920x440 превышает всю ротацию 1 MB x 5). (`core/logs.py:93-151`)
- `per_frame`/`PER_FRAME_ROOT` + `recurring_warning`/`recurring_failure`; `sink_for(level)`.
  (`core/logs.py:202-323`)