#!/usr/bin/env python3
"""Desktop theme reviewer for the TRCC cloud catalog.

A PySide6 GUI that pages through the hosted theme list, shows a 120x120 preview
thumbnail (``~/Library/Application Support/trcc/data/web/4401920/<id>.png``), the
theme's category + id, its ordinal, and a LIKED/NEW badge.  Left/Right (or a/d)
navigate, Enter/Space like, Esc quits.  Navigation loads the theme onto the LCD
panel via ``trcc theme cloud-load`` and persists the position; a like is written
atomically to ``favorites.json``.

Dev/review tool (MIT) -- not part of the shipped ``src/trofeo`` package.
"""

# SPDX-License-Identifier: MIT
from __future__ import annotations

import argparse
import contextlib
import os
import sys
import threading

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget

    _HAS_VIDEO = True
except Exception:
    QAudioOutput = QMediaPlayer = QVideoWidget = None
    _HAS_VIDEO = False

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import theme_db  # noqa: E402
from browse import index_of, load_state, save_state  # noqa: E402
from review import (  # noqa: E402
    FAVORITES,
    RAW_LIST,
    encode_brightness_video,
    filter_panel_themes,
    load_favorites,
    load_local_video,
    load_theme,
    load_themes,
    save_favorites,
    set_dev_brightness,
)

THUMB_DIR = os.path.expanduser(
    "~/Library/Application Support/trcc/data/web/4401920"
)

APP_SS = """
#app { background-color: #1b1e24; }
QLabel#title { color: #e8ecf4; font-size: 20px; font-weight: 700; }
QLabel#number { color: #97a0b2; font-size: 14px; }
QLabel#status { color: #8a93a6; font-size: 12px; }
QGroupBox {
    border: 1px solid white;
    border-radius: 8px;
    margin-top: 8px;
    color: white;
}
QGroupBox::title {
    color: white;
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 4px;
}
QRadioButton { color: white; }
"""

DOCK_WIDTH = 240
DOCK_COLS = 2
TILE_W = 104
TILE_H = 92


def _row_matches(key: str, row: dict | None) -> bool:
    """Whether a metadata *row* belongs to the given filter group.

    Shared by :meth:`PreviewApp._apply_filter` and the favorites dock so the
    group predicates cannot drift apart.  Unknown rows (not in the DB) belong
    only to the ``"all"`` group.
    """
    if key == "all":
        return True
    if row is None or row.get("is_dark") is None:
        return False
    dark = row["is_dark"] == 1
    flash = row["has_flash"] == 1
    if key == "dark":
        return dark and not flash
    if key == "light":
        return not dark
    if key == "flash":
        return dark and flash
    return True


class KeyCap(QPushButton):
    """A keyboard-keycap-styled button with a pressed/flash animation."""

    def __init__(self, glyph: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._glyph = glyph
        self._release = QTimer(self)
        self._release.setSingleShot(True)
        self._release.timeout.connect(lambda: self.setDown(False))
        self.setFixedSize(64, 52)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

    def flash(self) -> None:
        self.setDown(True)
        self._release.start(140)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        pad, depth = 3, 5
        down = self.isDown()
        face = QColor("#3b414d") if not down else QColor("#2c313a")
        side = QColor("#20242c")
        glyph_c = QColor("#dfe4ef") if not down else QColor("#9fb0d8")
        rw, rh = w - 2 * pad, h - 2 * pad
        radius = 9.0
        x, y = pad, pad + (depth if down else 0)
        painter.setPen(Qt.NoPen)
        if not down:
            sp = QPainterPath()
            sp.addRoundedRect(QRectF(pad, pad + depth, rw, rh), radius, radius)
            painter.setBrush(side)
            painter.drawPath(sp)
        fp = QPainterPath()
        fp.addRoundedRect(QRectF(x, y, rw, rh), radius, radius)
        painter.setBrush(face)
        painter.drawPath(fp)
        painter.setPen(Qt.NoPen)
        painter.setPen(QPen(glyph_c))
        font = painter.font()
        font.setPixelSize(int(rh * 0.5))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(x, y, rw, rh), Qt.AlignCenter, self._glyph)
        painter.end()


class DockToggle(QPushButton):
    """Narrow right-edge button whose chevron flips (animated) with the panel."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._arrow = 0.0  # 0.0 -> chevron points right; 1.0 -> points left
        self.setFixedWidth(28)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip("Show/hide favorites")
        self._anim = QPropertyAnimation(self, b"arrow", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

    def _get_arrow(self) -> float:
        return self._arrow

    def _set_arrow(self, value: float) -> None:
        self._arrow = value
        self.update()

    arrow = Property(float, _get_arrow, _set_arrow)

    def animate_to(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._arrow)
        self._anim.setEndValue(target)
        self._anim.start()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        rect = QRectF(1, 6, w - 2, h - 12)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#2a3040"))
        painter.drawRoundedRect(rect, 6, 6)
        direction = 1.0 - 2.0 * self._arrow
        cx, cy = w / 2.0, h / 2.0
        arm_x = min(4.5, w / 3.5)
        arm_y = 5.5
        path = QPainterPath()
        path.moveTo(cx - direction * arm_x, cy - arm_y)
        path.lineTo(cx + direction * arm_x, cy)
        path.lineTo(cx - direction * arm_x, cy + arm_y)
        pen = QPen(QColor("#dfe4ef"))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.end()


class FavoriteTile(QWidget):
    """One liked-theme tile: rounded preview + id, green border when selected."""

    clicked = Signal(str)

    def __init__(self, tid: str, thumb_dir: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tid = tid
        self._selected = False
        path = os.path.join(thumb_dir, f"{tid}.png")
        self._pix = QPixmap(path) if os.path.exists(path) else QPixmap()
        self.setFixedSize(TILE_W, TILE_H)
        self.setCursor(Qt.PointingHandCursor)

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self.update()

    def is_selected(self) -> bool:
        return self._selected

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if (
            event.button() == Qt.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit(self.tid)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        pad = 3.0
        rect = QRectF(0, 0, w, h).adjusted(pad, pad, -pad, -pad)
        radius = 10.0
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#232830"))
        painter.drawPath(path)
        if not self._pix.isNull():
            target = rect.adjusted(0, 0, 0, -18)
            scaled = self._pix.scaled(
                QSize(int(target.width()), int(target.height())),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            x = int(target.x() + (target.width() - scaled.width()) / 2)
            y = int(target.y() + (target.height() - scaled.height()) / 2)
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(x, y, scaled)
            painter.restore()
        bar = QRectF(rect.x(), rect.bottom() - 18, rect.width(), 18)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(11, 15, 20, 200))
        painter.drawRoundedRect(bar, 4, 4)
        painter.setPen(QColor("#c3cbdb"))
        f = painter.font()
        f.setPixelSize(11)
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(bar, Qt.AlignCenter, self.tid)
        if self._selected:
            painter.setPen(QPen(QColor("#35d08a")))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1.0, 1.0, -1.0, -1.0), radius, radius)
        painter.end()


class ThumbnailView(QLabel):
    """Scaled 120x120 preview PNG, or a rounded placeholder with the theme id."""

    def __init__(self, thumb_dir: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thumb_dir = thumb_dir
        self._tid: str | None = None
        self._src: QPixmap | None = None
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(240, 240)

    def set_theme(self, tid: str) -> None:
        self._tid = tid
        path = os.path.join(self._thumb_dir, f"{tid}.png")
        pix = QPixmap(path) if os.path.exists(path) else QPixmap()
        self._src = pix if not pix.isNull() else None
        self._rerender()

    def _rerender(self) -> None:
        if self._src is not None and self.width() > 1 and self.height() > 1:
            scaled = self._src.scaled(
                self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.setPixmap(scaled)
        else:
            self.setPixmap(self._placeholder())

    def _placeholder(self) -> QPixmap:
        pm = QPixmap(self.size())
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0, 0, pm.width(), pm.height()).adjusted(6, 6, -6, -6)
        p.setPen(QPen(QColor("#2a3040"), 2))
        p.setBrush(QColor("#232830"))
        p.drawRoundedRect(rect, 16, 16)
        p.setPen(QColor("#8a93a6"))
        f = p.font()
        f.setPixelSize(max(16, int(pm.height() * 0.14)))
        f.setBold(True)
        p.setFont(f)
        p.drawText(rect, Qt.AlignCenter, self._tid or "?")
        p.end()
        return pm

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._rerender()


class PreviewPane(QWidget):
    """Static thumbnail with an optional looping video overlay for one theme."""

    def __init__(self, thumb_dir: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._thumb_dir = thumb_dir
        self._tid: str | None = None
        self.thumb = ThumbnailView(thumb_dir)
        self._media: QMediaPlayer | None = None
        self._audio: QAudioOutput | None = None
        self._video_widget: QVideoWidget | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.thumb, stretch=1)
        self._init_video(lay)
        self._overlay = QLabel(self)
        self._overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._overlay.setGeometry(self.rect())
        self._overlay.setStyleSheet("background: transparent;")
        self._overlay.raise_()
        self.setMinimumSize(240, 240)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._overlay.setGeometry(self.rect())
        self._overlay.raise_()

    def _init_video(self, lay: QVBoxLayout) -> None:
        if not _HAS_VIDEO:
            return
        try:
            self._video_widget = QVideoWidget()
            self._video_widget.setMinimumSize(240, 240)
            with contextlib.suppress(Exception):
                self._video_widget.setAspectRatioMode(
                    Qt.AspectRatioMode.KeepAspectRatio
                )
            self._media = QMediaPlayer(self)
            self._audio = QAudioOutput(self)
            self._media.setAudioOutput(self._audio)
            self._media.setVideoOutput(self._video_widget)
            self._media.errorOccurred.connect(self._on_media_error)
            lay.addWidget(self._video_widget, stretch=1)
            self._video_widget.hide()
        except Exception:
            self._video_widget = None
            self._media = None
            self._audio = None

    def set_theme(self, tid: str, brightness: int = 100) -> None:
        self._tid = tid
        path = self._resolve_video(tid, brightness)
        if path is not None and self._media is not None:
            self._play_video(path)
        else:
            self._stop_video()
            self.thumb.set_theme(tid)

    def set_brightness(self, pct: int) -> None:
        pct = max(0, min(255, int(pct)))
        if pct == 100:
            self._overlay.setStyleSheet("background: transparent;")
        elif pct < 100:
            a = int(255 * (100 - pct) / 100)
            self._overlay.setStyleSheet(f"background: rgba(0, 0, 0, {a});")
        else:
            a = min(255, int(255 * (pct - 100) / 100))
            self._overlay.setStyleSheet(f"background: rgba(255, 255, 255, {a});")

    def _play_video(self, path: str) -> None:
        try:
            self._media.stop()
            self._media.setSource(QUrl.fromLocalFile(path))
            with contextlib.suppress(Exception):
                self._media.setLoops(-1)
            self.thumb.hide()
            if self._video_widget is not None:
                self._video_widget.show()
            self._media.play()
        except Exception:
            self._stop_video()

    def _stop_video(self) -> None:
        if self._media is not None:
            with contextlib.suppress(Exception):
                self._media.stop()
                self._media.setSource(QUrl())
        if self._video_widget is not None:
            self._video_widget.hide()
        self.thumb.show()

    def _on_media_error(self, *_args) -> None:
        self._stop_video()
        if self._tid:
            self.thumb.set_theme(self._tid)

    def _resolve_video(self, tid: str, brightness: int = 100) -> str | None:
        if not tid:
            return None
        candidates: list[str] = []
        if brightness != 100:
            baked = os.path.join(
                theme_db.THEMES_ROOT, tid, f"{tid}_brightness_adjusted.mp4"
            )
            if os.path.exists(baked):
                candidates.append(os.path.normpath(baked))
        candidates.extend(
            [
                os.path.join(theme_db.THEMES_ROOT, tid, f"{tid}.mp4"),
                os.path.join(
                    os.path.dirname(self._thumb_dir),
                    "..",
                    "web",
                    "1920440",
                    f"{tid}.mp4",
                ),
            ]
        )
        try:
            import trcc as _trcc  # noqa: F401

            data_dir = getattr(_trcc, "DATA_DIR", None)
            if data_dir:
                candidates.append(
                    os.path.join(data_dir, "web", "1920440", f"{tid}.mp4")
                )
        except Exception:
            pass
        for c in candidates:
            if c and os.path.exists(c):
                return os.path.normpath(c)
        return None


class _PanelLoader(QThread):
    """Serial, latest-wins loader that keeps blocking subprocess work off the GUI.

    ``request(tid)`` records the newest target and wakes the thread; the worker
    picks up only the latest request, runs ``load_theme`` (a blocking subprocess)
    off the GUI thread, and publishes the outcome via the ``loaded`` signal,
    which is delivered to the main thread as a queued connection.  A previously
    queued-but-not-started theme is overwritten, so rapid paging never stacks
    subprocesses.  Older completions are dropped by the seq guard on the main
    thread (only the newest request's status is shown).
    """

    loaded = Signal(bool, str, int)  # ok, tid, seq

    def __init__(self, device: str) -> None:
        super().__init__()
        self._device = device
        self._job: tuple[str, int] | None = None
        self._seq = 0
        self._last: str | None = None
        self._stopped = False
        self._cond = threading.Condition(threading.Lock())

    def request(self, tid: str) -> int:
        with self._cond:
            self._job = (tid, self._seq)
            seq = self._seq
            self._seq += 1
            self._cond.notify()
        return seq

    def run(self) -> None:
        while not self._stopped:
            with self._cond:
                while self._job is None and not self._stopped:
                    self._cond.wait()
                if self._stopped:
                    break
                job = self._job
                self._job = None
            tid, seq = job
            if tid != self._last:
                self._last = tid
                ok = load_theme(self._device, tid)
                self.loaded.emit(ok, tid, seq)

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify()


class _BakeWorker(QThread):
    """Off-thread ffmpeg brightness bake; emits ``done`` when finished."""

    done = Signal(str, int, int, bool, str)  # tid, pct, seq, ok, path

    def __init__(self, tid: str, pct: int, seq: int, src: str, dst: str) -> None:
        super().__init__()
        self._tid = tid
        self._pct = pct
        self._seq = seq
        self._src = src
        self._dst = dst

    def run(self) -> None:
        ok = encode_brightness_video(self._src, self._dst, self._pct)
        self.done.emit(self._tid, self._pct, self._seq, ok, self._dst)


class PreviewApp(QWidget):
    """Main window: thumbnail + name/number + keycap buttons, keyboard-aware."""

    def __init__(
        self,
        device: str = "0416:5408",
        thumb_dir: str = THUMB_DIR,
        auto_secs: float = 10.0,
        panel_only: bool = True,
    ) -> None:
        super().__init__()
        self.device = device
        self.thumb_dir = thumb_dir
        self._all_themes = load_themes(RAW_LIST)
        if panel_only:
            self._all_themes = filter_panel_themes(self._all_themes, thumb_dir)
        self.themes = list(self._all_themes)
        self._filter = "all"
        self.fav = set(load_favorites(FAVORITES))
        self._fav = list(self.fav)
        self.index = index_of(self.themes, load_state(), -1)
        if self.index < 0:
            self.index = 0
        self._pending_seq = -1
        self._auto_enabled = False
        self._dragging = False
        self._auto_ms = int(auto_secs * 1000)
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self._on_auto_timeout)
        self._meta = theme_db.load_meta()
        self._loader = _PanelLoader(device)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.start()
        self._bake_timer = QTimer(self)
        self._bake_timer.setSingleShot(True)
        self._bake_timer.timeout.connect(self._on_bake_debounce)
        self._bake_target: tuple[str, int, str] | None = None
        self._bake_seq = 0
        self._bake_thread: _BakeWorker | None = None
        self._brightness_cache: dict[tuple[str, int], str] = {}
        self._build_ui()
        self._refresh(load_panel=False)

    def _build_ui(self) -> None:
        self.setObjectName("app")
        self.setWindowTitle("TRCC Theme Reviewer")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(APP_SS)
        self.setFocusPolicy(Qt.StrongFocus)
        root = QHBoxLayout(self)
        root.setContentsMargins(18, 18, 6, 12)
        root.setSpacing(6)
        v = QVBoxLayout()
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.preview = PreviewPane(self.thumb_dir)
        v.addWidget(self.preview, stretch=1)
        row = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("title")
        row.addWidget(self.title)
        row.addStretch(1)
        self.number = QLabel()
        self.number.setObjectName("number")
        row.addWidget(self.number)
        v.addLayout(row)
        self.mark = QLabel()
        self.mark.setObjectName("mark")
        pager = QHBoxLayout()
        pager.addWidget(self.mark)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(1, len(self.themes))
        self._slider.setValue(self.index + 1)
        self._slider.valueChanged.connect(self._on_slider_moved)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        pager.addWidget(self._slider, 1)
        v.addLayout(pager)
        bri = QHBoxLayout()
        bri_label = QLabel("Яркость %")
        self._brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self._brightness_slider.setRange(0, 150)
        self._brightness_slider.setValue(100)
        self._brightness_slider.setToolTip("Brightness")
        self._brightness_slider.valueChanged.connect(self._on_brightness_changed)
        bri.addWidget(bri_label)
        bri.addWidget(self._brightness_slider, 1)
        v.addLayout(bri)
        self.status = QLabel()
        self.status.setObjectName("status")
        v.addWidget(self.status)
        self.classif = QLabel()
        self.classif.setObjectName("status")
        v.addWidget(self.classif)
        mode_group = QGroupBox("Листание")
        mode = QHBoxLayout(mode_group)
        self.manual_radio = QRadioButton("Ручное листание")
        self.auto_radio = QRadioButton("Авто")
        self.manual_radio.setChecked(True)
        self.auto_radio.toggled.connect(self._on_auto_toggled)
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.manual_radio)
        self._mode_group.addButton(self.auto_radio)
        mode.addWidget(self.manual_radio)
        mode.addWidget(self.auto_radio)
        mode.addStretch(1)
        v.addWidget(mode_group)
        filter_group = QGroupBox("Фильтр")
        flt = QVBoxLayout(filter_group)
        self.filter_all = QRadioButton("Все")
        self.filter_dark = QRadioButton("Тёмная")
        self.filter_light = QRadioButton("Светлая")
        self.filter_flash = QRadioButton("Тёмная со вспышками")
        self.filter_all.setChecked(True)
        self._filter_group = QButtonGroup(self)
        for rb, key in (
            (self.filter_all, "all"),
            (self.filter_dark, "dark"),
            (self.filter_light, "light"),
            (self.filter_flash, "flash"),
        ):
            self._filter_group.addButton(rb)
            rb.toggled.connect(
                lambda checked, k=key: self._on_filter_toggled(k, checked)
            )
        for rb in (self.filter_all, self.filter_dark, self.filter_light, self.filter_flash):
            flt.addWidget(rb)
        v.addWidget(filter_group)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.prev_key = KeyCap("◀")
        self.like_key = KeyCap("＋")
        self.next_key = KeyCap("▶")
        self.prev_key.clicked.connect(self.prev)
        self.next_key.clicked.connect(self.next)
        self.like_key.clicked.connect(self.like)
        buttons.addWidget(self.prev_key)
        buttons.addSpacing(14)
        buttons.addWidget(self.like_key)
        buttons.addSpacing(14)
        buttons.addWidget(self.next_key)
        buttons.addStretch(1)
        v.addLayout(buttons)
        root.addLayout(v, stretch=1)
        self._build_dock_panel()
        self._dock_toggle = DockToggle(self)
        self._dock_toggle.clicked.connect(self._toggle_dock)
        root.addWidget(self._dock_panel)
        root.addWidget(self._dock_toggle)
        self.setMinimumSize(560, 480)
        self._refresh_dock()

    def _build_dock_panel(self) -> None:
        self._dock_open = False
        self._dock_anim: QPropertyAnimation | None = None
        self._base_w: int | None = None
        self._dock_tiles: dict[str, FavoriteTile] = {}
        self._dock_panel = QWidget(self)
        self._dock_panel.setObjectName("dock")
        self._dock_panel.setStyleSheet(
            "QWidget#dock { background: #20242c; border: 1px solid #2a3040; "
            "border-radius: 8px; }"
            "QLabel#dock_header { color: #e8ecf4; font-weight: 700; font-size: 13px; "
            "padding-left: 2px; }"
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        pl = QVBoxLayout(self._dock_panel)
        pl.setContentsMargins(8, 8, 8, 8)
        pl.setSpacing(6)
        self._dock_header = QLabel("Favorites")
        self._dock_header.setObjectName("dock_header")
        pl.addWidget(self._dock_header)
        self._favs_scroll = QScrollArea(self._dock_panel)
        self._favs_scroll.setObjectName("favs_scroll")
        self._favs_scroll.setWidgetResizable(True)
        self._favs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._favs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._favs_container = QWidget()
        self._favs_container.setObjectName("favs_container")
        self._favs_container.setStyleSheet("background: transparent;")
        self._favs_grid = QGridLayout(self._favs_container)
        self._favs_grid.setContentsMargins(6, 0, 0, 0)
        self._favs_grid.setSpacing(8)
        self._favs_grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._favs_scroll.setWidget(self._favs_container)
        pl.addWidget(self._favs_scroll, 1)
        self._dock_panel.setMinimumWidth(0)
        self._dock_panel.setMaximumWidth(0)

    def _toggle_dock(self) -> None:
        self._dock_open = not self._dock_open
        self._dock_toggle.animate_to(1.0 if self._dock_open else 0.0)
        target = DOCK_WIDTH if self._dock_open else 0
        if self._dock_anim is None:
            self._dock_anim = QPropertyAnimation(self._dock_panel, b"maximumWidth", self)
            self._dock_anim.setDuration(180)
            self._dock_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
            self._dock_anim.valueChanged.connect(self._on_dock_anim)
        self._dock_anim.stop()
        self._base_w = self.width() - self._dock_panel.maximumWidth()
        self._dock_anim.setStartValue(self._dock_panel.maximumWidth())
        self._dock_anim.setEndValue(target)
        self._dock_anim.start()

    def _on_dock_anim(self, value: float) -> None:
        if self._base_w is not None:
            self.resize(int(self._base_w + value), self.height())

    def _refresh_dock(self) -> None:
        current = self.themes[self.index][0] if self.themes else None
        show = [tid for tid in self._fav if _row_matches(self._filter, self._meta.get(tid))]
        for tid in show:
            if tid not in self._dock_tiles:
                tile = FavoriteTile(tid, self.thumb_dir)
                tile.clicked.connect(self._on_tile_clicked)
                self._dock_tiles[tid] = tile
        while self._favs_grid.count():
            item = self._favs_grid.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
        for i, tid in enumerate(show):
            tile = self._dock_tiles[tid]
            tile.set_selected(tid == current)
            self._favs_grid.addWidget(tile, i // DOCK_COLS, i % DOCK_COLS)
            tile.show()
        for tid, tile in self._dock_tiles.items():
            if tid not in show:
                tile.hide()

    def _on_tile_clicked(self, tid: str) -> None:
        for i, (t, _cat) in enumerate(self.themes):
            if t == tid:
                if i != self.index:
                    self.index = i
                    self._refresh()
                else:
                    self._refresh_dock()
                return

    def _refresh(self, load_panel: bool = True) -> None:
        self._teardown_brightness()
        tid, cat = self.themes[self.index]
        self._preview_index(self.index)
        self._sync_slider()
        save_state(tid)
        self._refresh_dock()
        if load_panel:
            self._load_panel(tid)

    def _apply_filter(self) -> None:
        key = self._filter
        meta = self._meta
        self.themes = [t for t in self._all_themes if _row_matches(key, meta.get(t[0]))]
        if not self.themes:
            self.index = 0
            self._slider.setRange(1, 1)
            self._slider.setValue(1)
            self._refresh_dock()
            return
        if self.index >= len(self.themes):
            self.index = len(self.themes) - 1
        self._slider.setRange(1, len(self.themes))
        self._slider.setValue(self.index + 1)
        self._refresh(load_panel=True)

    def _on_filter_toggled(self, key: str, checked: bool) -> None:
        if checked:
            self._filter = key
            self._apply_filter()

    def _load_panel(self, tid: str) -> None:
        self._auto_timer.stop()
        self._set_status("loading…")
        self._pending_seq = self._loader.request(tid)

    def _on_slider_pressed(self) -> None:
        self._dragging = True
        self._auto_timer.stop()

    def _on_slider_moved(self, val: int) -> None:
        i = val - 1
        if 0 <= i < len(self.themes):
            self.index = i
            self._preview_index(i)

    def _on_slider_released(self) -> None:
        self._dragging = False
        self.index = self._slider.value() - 1
        self.index = max(0, min(len(self.themes) - 1, self.index))
        self._refresh(load_panel=True)

    def _preview_index(self, i: int) -> None:
        tid, cat = self.themes[i]
        self.preview.set_theme(tid)
        b = theme_db.get_brightness(tid)
        prev = self._brightness_slider.value()
        self._brightness_slider.setValue(b)
        if prev == b and b != 100:
            self._on_brightness_changed(b)
        self.title.setText(f"{cat} · {tid}")
        self.number.setText(f"[{i + 1}/{len(self.themes)}]")
        self._update_mark(tid)
        self._update_classification(tid)
        self._prioritize(tid)

    def _update_classification(self, tid: str) -> None:
        row = self._meta.get(tid)
        if row:
            dark = "dark" if row["is_dark"] else "light"
            flash = "flash" if row["has_flash"] else "no-flash"
            ready = "ready" if theme_db.is_ready(row) else "fetching"
        else:
            dark = flash = ready = "unknown"
        self.classif.setText(f"{dark} · {flash} · {ready}")

    def _prioritize(self, tid: str) -> None:
        with contextlib.suppress(Exception):
            theme_db.bump_queue(tid, front=True)

    def _sync_slider(self) -> None:
        self._slider.setValue(self.index + 1)

    def _on_loaded(self, ok: bool, tid: str, seq: int) -> None:
        if seq != self._pending_seq:
            return
        if ok:
            self._meta = theme_db.load_meta()
        self._set_status(f"● loaded {tid}" if ok else f"✗ load failed: {tid}")
        if ok and self._auto_enabled and not self._dragging:
            self._auto_timer.start(self._auto_ms)

    def _on_auto_toggled(self, checked: bool) -> None:
        if checked:
            self._auto_enabled = True
            self._auto_timer.stop()
            self._load_panel(self.themes[self.index][0])
        else:
            self._auto_enabled = False
            self._auto_timer.stop()

    def _on_auto_timeout(self) -> None:
        if self._auto_enabled:
            self.next()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._loader.stop()
        self._loader.wait(3000)
        super().closeEvent(event)

    def _update_mark(self, tid: str) -> None:
        liked = tid in self.fav
        text = "LIKED" if liked else "NEW"
        bg = "#35d08a" if liked else "#2a3040"
        fg = "#0b0f14" if liked else "#c3cbdb"
        self.mark.setText(text)
        self.mark.setStyleSheet(
            f"color:{fg}; background:{bg}; border-radius:8px; padding:4px 12px; "
            f"font-weight:700; font-size:11px;"
        )

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    def _on_brightness_changed(self, pct: int) -> None:
        if not self.themes:
            return
        tid = self.themes[self.index][0]
        theme_db.set_brightness(tid, pct)
        with contextlib.suppress(Exception):
            set_dev_brightness(self.device, pct)
        self.preview.set_brightness(pct)
        self._schedule_bake(tid, pct)

    def _src_video(self, tid: str) -> str | None:
        if not tid:
            return None
        cand = os.path.join(theme_db.THEMES_ROOT, tid, f"{tid}.mp4")
        return os.path.normpath(cand) if os.path.exists(cand) else None

    def _schedule_bake(self, tid: str, pct: int) -> None:
        self._bake_timer.stop()
        if tid is None:
            return
        if pct == 100:
            self._restore_normal(tid)
            return
        if self._bake_thread is not None and self._bake_thread.isRunning():
            return
        cached = self._brightness_cache.get((tid, pct))
        if cached and os.path.exists(cached):
            self._apply_baked(tid, pct, cached)
            return
        src = self._src_video(tid)
        if src is None:
            return
        self._bake_target = (tid, pct, src)
        self._bake_timer.start(350)

    def _restore_normal(self, tid: str) -> None:
        try:
            set_dev_brightness(self.device, 100)
            self.preview.set_brightness(100)
            self._load_panel(tid)
        except Exception:
            pass

    def _on_bake_debounce(self) -> None:
        if self._bake_target is None:
            return
        tid, pct, src = self._bake_target
        self._bake_seq += 1
        seq = self._bake_seq
        dst = os.path.join(
            theme_db.THEMES_ROOT, tid, f"{tid}_brightness_adjusted.mp4"
        )
        worker = _BakeWorker(tid, pct, seq, src, dst)
        worker.done.connect(self._on_bake_done)
        self._bake_thread = worker
        worker.start()

    def _on_bake_done(self, tid: str, pct: int, seq: int, ok: bool, path: str) -> None:
        if ok:
            self._brightness_cache[(tid, pct)] = path
        if seq != self._bake_seq:
            return
        current = self._brightness_slider.value()
        if ok and current == pct and self.themes and self.themes[self.index][0] == tid:
            self._apply_baked(tid, pct, path)

    def _apply_baked(self, tid: str, pct: int, path: str) -> None:
        try:
            load_local_video(self.device, path)
            self.preview.set_theme(tid, brightness=pct)
            set_dev_brightness(self.device, 100)
            self.preview.set_brightness(100)
        except Exception:
            pass

    def _teardown_brightness(self) -> None:
        try:
            self._bake_timer.stop()
            self._bake_target = None
            self._bake_seq += 1
            set_dev_brightness(self.device, 100)
            self.preview.set_brightness(100)
        except Exception:
            pass

    def prev(self) -> None:
        self.prev_key.flash()
        if self.index > 0:
            self.index -= 1
            self._refresh()

    def next(self) -> None:
        self.next_key.flash()
        if self.index < len(self.themes) - 1:
            self.index += 1
            self._refresh()

    def like(self) -> None:
        self.like_key.flash()
        if not self.themes:
            return
        tid = self.themes[self.index][0]
        if tid not in self.fav:
            self._fav.append(tid)
            self.fav.add(tid)
            save_favorites(FAVORITES, self._fav, dict(self.themes))
        self._update_mark(tid)
        self._refresh_dock()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.isAutoRepeat():
            event.accept()
            return
        key = event.key()
        if key == Qt.Key_Escape:
            self.close()
        elif key in (Qt.Key_Left, Qt.Key_A):
            self.prev()
        elif key in (Qt.Key_Right, Qt.Key_D):
            self.next()
        elif key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.like()
        else:
            super().keyPressEvent(event)
            return
        event.accept()


def main() -> int:
    ap = argparse.ArgumentParser(description="Browse cloud themes with a GUI.")
    ap.add_argument("--key", default="0416:5408", help="device key")
    ap.add_argument("--thumb-dir", default=THUMB_DIR, help="preview thumbnail dir")
    ap.add_argument(
        "--auto-secs",
        default=10.0,
        type=float,
        help="seconds to linger on each theme in Auto (flipping) mode",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="also show themes for other displays (some will fail to load)",
    )
    args = ap.parse_args()
    app = QApplication(sys.argv)
    window = PreviewApp(
        device=args.key,
        thumb_dir=args.thumb_dir,
        auto_secs=args.auto_secs,
        panel_only=not args.all,
    )
    window.show()
    window.setFocus()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
