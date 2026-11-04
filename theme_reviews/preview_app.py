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

from PySide6.QtCore import QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import theme_db  # noqa: E402
from browse import index_of, load_state, save_state  # noqa: E402
from review import (  # noqa: E402
    FAVORITES,
    RAW_LIST,
    filter_panel_themes,
    load_favorites,
    load_theme,
    load_themes,
    save_favorites,
)

THUMB_DIR = os.path.expanduser(
    "~/Library/Application Support/trcc/data/web/4401920"
)

APP_SS = """
#app { background-color: #1b1e24; }
QLabel#title { color: #e8ecf4; font-size: 20px; font-weight: 700; }
QLabel#number { color: #97a0b2; font-size: 14px; }
QLabel#status { color: #8a93a6; font-size: 12px; }
"""


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
        self.themes = load_themes(RAW_LIST)
        if panel_only:
            self.themes = filter_panel_themes(self.themes, thumb_dir)
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
        self._build_ui()
        self._refresh(load_panel=False)

    def _build_ui(self) -> None:
        self.setObjectName("app")
        self.setWindowTitle("TRCC Theme Reviewer")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(APP_SS)
        self.setFocusPolicy(Qt.StrongFocus)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 18, 18, 18)
        v.setSpacing(12)
        self.thumb = ThumbnailView(self.thumb_dir)
        v.addWidget(self.thumb, stretch=1)
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
        self.status = QLabel()
        self.status.setObjectName("status")
        v.addWidget(self.status)
        self.classif = QLabel()
        self.classif.setObjectName("status")
        v.addWidget(self.classif)
        mode = QHBoxLayout()
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
        v.addLayout(mode)
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
        self.setMinimumSize(360, 480)

    def _refresh(self, load_panel: bool = True) -> None:
        tid, cat = self.themes[self.index]
        self._preview_index(self.index)
        self._sync_slider()
        save_state(tid)
        if load_panel:
            self._load_panel(tid)

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
        self.thumb.set_theme(tid)
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
        tid = self.themes[self.index][0]
        if tid not in self.fav:
            self._fav.append(tid)
            self.fav.add(tid)
            save_favorites(FAVORITES, self._fav, dict(self.themes))
        self._update_mark(tid)

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
