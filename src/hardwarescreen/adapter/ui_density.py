"""Плотность «dp → px» в стиле Android для дизайн-размеров дашборда.

Одна dp — это логический пиксель дизайна; на панели он превращается в физический
пиксель умножением на ``density`` (аналог ``DisplayMetrics.density`` у Android:
``density = dpi / 160``, «базовая» mdpi — 160 dpi, где 1 dp == 1 px).

Формула (по developer.android.com): ``px = round(dp * density)`` — округление
как у Android для целочисленных *размеров* (``complexToDimensionPixelSize``);
обратно ``dp = px / density``.

Здесь ``density`` — это просто **выбранный масштаб дизайна** (сколько физических
пикселей панели приходится на одну dp), а не наблюдаемое свойство устройства: панель
фиксированная, поэтому масштаб задаёт владелец конфигурации (``config.yaml`` /
``HARDWARESCREEN_DENSITY``). По умолчанию ``1.0`` — тогда ``dp(v, 1.0) == round(v)``
и поведение не меняется (обратная совместимость: константы дизайна остаются теми же
px, пока не задана плотность).

Геометрия, которая уже задаётся физически (мм через ``px_per_mm``) или долями
(зазоры/скругления от толщины), сюда НЕ сводится — она и так масштабируется под
любое разрешение панели. dp применяется только к фиксированным px-константам
дизайна (шрифт, отступы, свечение, «нет данных»).
"""
# SPDX-License-Identifier: MIT

from __future__ import annotations


def dp(value: float, density: float = 1.0) -> float:
    """dp → px: ``round(value * density)`` (Android-округление размера).

    ``density`` — px-на-dp (``DisplayMetrics.density``), по умолчанию ``1.0``
    (dp == px).  Округляем как Android делает для целочисленных размеров.
    """
    return float(round(value * density))


def resolve_density(value: str | None, default: float = 1.0) -> float:
    """Разобрать ``density`` из строки (env/config): ``None``/не число → ``default``.

    Отрицательная плотность не имеет смысла — отбрасываем в пользу ``default``.
    """
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
