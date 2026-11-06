# ThermalrightDisplayInfoScreen

Портативное самодостаточное приложение-дашборд для **Thermalright Trofeo Vision 11.3
LCD** (USB `0416:5408`, протокол "LY" bulk) как экран системной информации.

- **Реактивный рендеринг** — шлёт кадр только при изменении отображаемых данных
  (это панель-рамка для картинки).
- **Keepalive** — панель возвращается к логотипу через ~2–3 с, поэтому кадр
  пересылается не реже, чем раз в `keepalive_s` (по умолчанию 2 с).
- **Переподключение USB** — переживает потерю USB/питания (автомобильная установка):
  нативный драйвер пере-открывает + пере-handshake'ит сам; супервизор также может
  обернуть внешний движок (`trcc-linux`).
- **Портативность, без LLM** — чистый Python. На сборке/запуске хватает `pip install .`
  и конфиг-файла. Работает на macOS (dev) и Linux aarch64 (Raspberry Pi / Orange Pi).

## Установка

```bash
pip install -e .        # или: pip install .
```

## Запуск

```bash
hardwarescreen run                      # реактивный рендерер + keepalive + переподключение
hardwarescreen run --config config.yaml # явный конфиг
hardwarescreen --version
```

Обернуть внешний движок (готовый Control Center, GPL-3.0, опционально):

```bash
pip install .[trcc]             # опционально: trcc-linux
hardwarescreen supervise --cmd trcc daemon   # постоянный движок (владеет USB, рендерит + keepalive)
```

## Менеджер тем (headless, без железа)

Менеджер — локальный HTTP-сервис, владеющий плоскостью отображения; продюсеры `POST`-ят
JSON, а менеджер тем публикует атомарное состояние моста, которое читает Qt-адаптер
дашборда (см. `dev/`), чтобы рисовать гейджи поверх темы. Весь поток ниже работает без
панели и без `trcc`.

Установка с dev/quality-инструментами:

```bash
pip install -e ".[dev]"
```

Запуск менеджера и источника данных-заглушки (headless, без железа):

```bash
hardwarescreen-manager --config manager.yaml   # порт 8123, атомарный файл состояния моста
hardwarescreen-stubdata --config stubdata.yaml # кормит гейджи + переключения day/night
```

На реальном устройстве на панели включается trcc-модуль дашборда — см.
`memory/hardware_verification.md` для чек-листа на экране и `memory/`
для заметок про менеджер/архитектуру.

## Развёртывание на Linux (Raspberry Pi / Orange Pi)

- Скопировать `deploy/99-hardwarescreen-lcd.rules` в `/etc/udev/rules.d/` и перезагрузить udev.
- Скопировать `deploy/hardwarescreen-supervisor.service` в `~/.config/systemd/user/`
  (поправить пути `ExecStart` под свой venv/проект), затем:
  `systemctl --user enable --now hardwarescreen-supervisor`.

## Аппаратные факты (см. `AGENTS.md`)

- `VID 0x0416 PID 0x5408`, LY bulk; write EP `0x09`, read EP `0x81` (авто-детект).
- Handshake `PM=64+clamp(resp[20])`, `SUB=resp[22]+1`; `PM==69` → 11.3.
- Кадр: JPEG ≤ **450000 байт** (выше ~450 KB панель молча роняет).
- Потолок ~18 FPS (write+ACK ≈ 55 мс).

## Dev-инструменты

```bash
python tools/probe_ly.py       # handshake + лестница размеров кадра
python tools/probe_fps.py      # измерить потолок FPS
```

## Лицензии

- **Этот пакет** — **MIT**.
- **`trcc-linux`** — **GPL-3.0** и используется **только как внешний движок** —
  установленная зависимость / подпроцесс, запускаемый `hardwarescreen supervise`. Его код
  никогда не копируется в эти исходники, поэтому пакет остаётся MIT.

Опциональная интеграция trcc (внешняя, GPL-3.0):
[https://github.com/Lexonight1/thermalright-trcc-linux](https://github.com/Lexonight1/thermalright-trcc-linux)