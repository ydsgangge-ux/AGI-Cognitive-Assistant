"""
Floating Window
Always-on-top, semi-transparent, draggable mini chat window
Click to expand/collapse, quick message sending
Embedded SimLife life status panel
"""

from PyQt6.QtCore    import Qt, QPoint, pyqtSignal, QPropertyAnimation, QEasingCurve, QSize, QTimer
from PyQt6.QtGui     import (QColor, QPainter, QPainterPath, QFont,
                              QLinearGradient, QMouseEvent, QCursor)
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                              QLineEdit, QPushButton, QLabel,
                              QTextEdit, QSizePolicy, QCheckBox, QScrollArea)

from engine.i18n import t


class FloatBubble(QWidget):
    """Single message bubble"""
    def __init__(self, text: str, is_user: bool, is_proactive: bool = False,
                 on_replied=None, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(2)

        # Proactive message checkbox
        if not is_user and is_proactive:
            top = QHBoxLayout()
            top.setContentsMargins(4, 0, 4, 0)
            chk = QCheckBox("Replied")
            chk.setStyleSheet(
                "QCheckBox{color:#8b949e;font-size:10px;spacing:3px;}"
                "QCheckBox::indicator{width:12px;height:12px;"
                "border:1px solid #30363d;border-radius:2px;}"
                "QCheckBox::indicator:checked{background:#3fb950;"
                "border-color:#3fb950;image:none;}"
            )
            status_lbl = QLabel("Not replied")
            status_lbl.setStyleSheet("color:#d29922;font-size:10px;")

            def _toggle(state, s=status_lbl, msg=text, cb=on_replied):
                if state == Qt.CheckState.Checked.value:
                    s.setText("Replied")
                    s.setStyleSheet("color:#3fb950;font-size:10px;")
                    if cb:
                        cb(msg)
                else:
                    s.setText("Not replied")
                    s.setStyleSheet("color:#d29922;font-size:10px;")
            chk.stateChanged.connect(_toggle)
            top.addWidget(chk)
            top.addWidget(status_lbl)
            top.addStretch()
            layout.addLayout(top)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)

        label = QLabel(text)
        label.setWordWrap(True)
        label.setMaximumWidth(260)
        label.setStyleSheet(f"""
            background: {'#1f6feb' if is_user else '#21262d'};
            color: #e6edf3;
            border-radius: 10px;
            padding: 7px 11px;
            font-size: 12px;
            line-height: 1.5;
        """)

        if is_user:
            row.addStretch()
            row.addWidget(label)
        else:
            row.addWidget(label)
            row.addStretch()

        layout.addLayout(row)


class SimLifePanel(QWidget):
    """SimLife life status panel (embedded in floating window)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._log_page = 0
        self._log_per_page = 6
        self._log_data = []
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(6)

        # -- Status header --
        header = QHBoxLayout()
        header.setSpacing(8)

        self._avatar = QLabel("😊")
        self._avatar.setFixedSize(32, 32)
        self._avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar.setStyleSheet(
            "background:#1f6feb; border-radius:16px; font-size:16px;"
        )

        info_col = QVBoxLayout()
        info_col.setSpacing(1)

        self._name_lbl = QLabel("ZeroOne")
        self._name_lbl.setStyleSheet("color:#e6edf3; font-size:13px; font-weight:600;")

        self._scene_lbl = QLabel("Evening Relax")
        self._scene_lbl.setStyleSheet("color:#8b949e; font-size:11px;")

        info_col.addWidget(self._name_lbl)
        info_col.addWidget(self._scene_lbl)

        header.addWidget(self._avatar)
        header.addLayout(info_col)
        header.addStretch()

        # Mood
        mood_box = QVBoxLayout()
        mood_box.setSpacing(0)
        mood_box.setAlignment(Qt.AlignmentFlag.AlignRight)

        self._mood_emoji = QLabel("😊")
        self._mood_emoji.setStyleSheet("font-size:20px;")
        self._mood_emoji.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._mood_lbl = QLabel("87")
        self._mood_lbl.setStyleSheet("color:#e6edf3; font-size:12px; font-weight:700;")
        self._mood_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        mood_box.addWidget(self._mood_emoji)
        mood_box.addWidget(self._mood_lbl)
        header.addLayout(mood_box)

        root.addLayout(header)

        # -- Separator --
        sep = QLabel("")
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#21262d;")
        root.addWidget(sep)

        # -- Current activity --
        self._activity_row = QHBoxLayout()
        self._activity_row.setSpacing(6)
        act_icon = QLabel("▶")
        act_icon.setStyleSheet("color:#58a6ff; font-size:10px; font-weight:bold;")
        self._activity_lbl = QLabel("Browsing phone")
        self._activity_lbl.setStyleSheet("color:#c9d1d9; font-size:12px;")
        self._activity_lbl.setWordWrap(True)
        self._activity_row.addWidget(act_icon)
        self._activity_row.addWidget(self._activity_lbl, 1)
        root.addLayout(self._activity_row)

        # -- Weather + time --
        self._weather_time_lbl = QLabel("☁️ Cloudy · 22:00")
        self._weather_time_lbl.setStyleSheet("color:#8b949e; font-size:11px;")
        root.addWidget(self._weather_time_lbl)

        # -- Today activity title --
        log_header = QHBoxLayout()
        log_header.setSpacing(4)
        log_title = QLabel("📋 Today's Activity")
        log_title.setStyleSheet("color:#8b949e; font-size:11px; font-weight:600;")
        self._log_count_lbl = QLabel("")
        self._log_count_lbl.setStyleSheet("color:#484f58; font-size:10px;")
        log_header.addWidget(log_title)
        log_header.addWidget(self._log_count_lbl)
        log_header.addStretch()
        root.addLayout(log_header)

        # -- Log scroll area --
        self._log_container = QWidget()
        self._log_layout = QVBoxLayout(self._log_container)
        self._log_layout.setContentsMargins(0, 0, 0, 0)
        self._log_layout.setSpacing(3)
        self._log_layout.addStretch()

        self._log_scroll = QScrollArea()
        self._log_scroll.setWidget(self._log_container)
        self._log_scroll.setWidgetResizable(True)
        self._log_scroll.setFixedHeight(130)
        self._log_scroll.setStyleSheet("""
            QScrollArea { background:transparent; border:none; }
            QScrollBar:vertical { width:3px; }
            QScrollBar::handle:vertical { background:#30363d; border-radius:2px; }
        """)
        root.addWidget(self._log_scroll)

        # No data placeholder
        self._empty_lbl = QLabel("No activity today")
        self._empty_lbl.setStyleSheet("color:#484f58; font-size:11px;")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._log_layout.insertWidget(0, self._empty_lbl)

    def update_data(self, summary: dict):
        """Update panel with SimLife data"""
        if not summary:
            return

        # Character name
        name = summary.get("name", "")
        if name:
            self._name_lbl.setText(name)

        # Scene
        scene = summary.get("scene", "")
        self._scene_lbl.setText(scene if scene else "Unknown scene")

        # Mood
        mood = summary.get("mood", 70)
        self._mood_emoji.setText(summary.get("mood_emoji", "😊"))
        self._mood_lbl.setText(str(mood))

        # Mood color
        if mood >= 80:
            avatar_bg = "#238636"
        elif mood >= 60:
            avatar_bg = "#1f6feb"
        elif mood >= 40:
            avatar_bg = "#9e6a03"
        else:
            avatar_bg = "#da3633"
        self._avatar.setStyleSheet(
            f"background:{avatar_bg}; border-radius:16px; font-size:16px;"
        )

        # Current activity
        activity = summary.get("activity", "")
        self._activity_lbl.setText(activity if activity else "Idle")

        # Weather + time + holiday
        weather = summary.get("weather", "")
        time_str = summary.get("time_str", "")
        holiday = summary.get("holiday")
        parts = []
        if weather:
            parts.append(weather)
        if holiday and holiday.get("label"):
            parts.append(f"🎉 {holiday['label']}")
        if time_str:
            parts.append(time_str)
        self._weather_time_lbl.setText(" · ".join(parts) if parts else "")

        # Color scene label on holidays
        if holiday and holiday.get("type") == "public_holiday":
            self._scene_lbl.setStyleSheet("color:#3fb950; font-size:11px;")
        else:
            self._scene_lbl.setStyleSheet("color:#8b949e; font-size:11px;")

        # Logs
        logs = summary.get("today_log", [])
        self._log_data = logs
        self._log_count_lbl.setText(f"{len(logs)} items")
        self._render_logs(logs)

    def _render_logs(self, logs: list, page: int = 0):
        """Render log list (by page)"""
        # Clear old logs (keep stretch)
        while self._log_layout.count() > 1:
            item = self._log_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not logs:
            lbl = QLabel("No activity today")
            lbl.setStyleSheet("color:#484f58; font-size:11px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._log_layout.insertWidget(0, lbl)
            return

        # Reverse order (newest first)
        total = len(logs)
        start = max(0, total - (page + 1) * self._log_per_page)
        end = total - page * self._log_per_page

        for i in range(end - 1, start - 1, -1):
            entry = logs[i]
            time_str = entry.get("time", "")
            event = entry.get("event", "")

            row = QHBoxLayout()
            row.setSpacing(6)

            time_lbl = QLabel(time_str)
            time_lbl.setFixedWidth(36)
            time_lbl.setStyleSheet("color:#484f58; font-size:10px;")

            event_lbl = QLabel(event)
            event_lbl.setWordWrap(True)
            event_lbl.setStyleSheet("color:#c9d1d9; font-size:11px;")

            row.addWidget(time_lbl)
            row.addWidget(event_lbl, 1)
            self._log_layout.insertWidget(0, row)


class FloatingWindow(QWidget):
    """
    Floating Window Main
    - Always on top
    - Draggable
    - Expand/collapse animation
    - Semi-transparent background
    - Embedded SimLife life status panel
    """

    message_sent    = pyqtSignal(str)   # User sends message
    screenshot_requested = pyqtSignal() # Request screenshot
    closed          = pyqtSignal()
    proactive_replied = pyqtSignal(str, str) # (Proactive msg, user reply content)

    COLLAPSED_H = 56    # Collapsed height (titlebar height)
    EXPANDED_H  = 520   # Expanded height (larger to fit status panel)
    WIDTH       = 340

    def __init__(self, opacity: float = 0.95, parent=None):
        super().__init__(parent)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowOpacity(opacity)
        self.resize(self.WIDTH, self.EXPANDED_H)

        self._drag_pos: QPoint | None = None
        self._expanded = True
        self._simlife_shown = False  # Whether status panel is expanded

        # -- Proactive speech state (migrated to main.py global management) --
        self.agent = None  # Injected by main.py
        self._pending_proactive_msg = None  # Pending proactive message content
        self.simlife_client = None  # Injected by main.py

        self._setup_ui()
        self._setup_animation()
        self._position_bottom_right()

    def _setup_ui(self):
        # Add Layout to main window itself, ensure container fits perfectly
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self._container = QWidget(self)
        self._container.setObjectName("float_container")
        self._container.setStyleSheet("""
            #float_container {
                background: rgba(13,17,23,0.96);
                border: 1px solid #30363d;
                border-radius: 14px;
            }
        """)
        main_layout.addWidget(self._container)

        root = QVBoxLayout(self._container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- Titlebar --
        self._titlebar = QWidget()
        self._titlebar.setFixedHeight(self.COLLAPSED_H)
        self._titlebar.setStyleSheet("background: transparent;")
        tb_layout = QHBoxLayout(self._titlebar)
        tb_layout.setContentsMargins(14, 8, 10, 8)

        self._brain_icon = QLabel("AG")
        self._brain_icon.setStyleSheet(
            "color:#58a6ff; font-weight:700; font-size:13px; "
            "background:#1f6feb; border-radius:6px; "
            "min-width:22px; max-width:22px; padding:2px 0px;"
        )
        self._brain_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._title_lbl = QLabel(t("app_name"))
        self._title_lbl.setStyleSheet(
            "color:#58a6ff; font-weight:700; font-size:13px;"
        )

        self._emotion_lbl = QLabel(f"· {t('ready')}")
        self._emotion_lbl.setStyleSheet("color:#8b949e; font-size:11px;")

        btn_shot = QPushButton("P")
        btn_shot.setFixedSize(28, 28)
        btn_shot.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_shot.setStyleSheet(
            "QPushButton{background:#1f6feb;border:none;border-radius:6px;"
            "color:#ffffff;font-size:13px;font-weight:bold;}"
            "QPushButton:hover{background:#388bfd;}"
        )
        btn_shot.clicked.connect(self.screenshot_requested)

        self._btn_toggle = QPushButton("-")
        self._btn_toggle.setFixedSize(28, 28)
        self._btn_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_toggle.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;border-radius:6px;"
            "color:#ffffff;font-size:16px;font-weight:bold;}"
            "QPushButton:hover{background:#30363d;border-color:#58a6ff;}"
        )
        self._btn_toggle.clicked.connect(self.toggle_expand)

        btn_close = QPushButton("X")
        btn_close.setFixedSize(28, 28)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;border-radius:6px;"
            "color:#ffffff;font-size:13px;font-weight:bold;}"
            "QPushButton:hover{color:#f85149;border-color:#f85149;}"
        )
        btn_close.clicked.connect(self._on_close)

        tb_layout.addWidget(self._brain_icon)
        tb_layout.addWidget(self._title_lbl)
        tb_layout.addWidget(self._emotion_lbl)
        tb_layout.addStretch()
        tb_layout.addWidget(btn_shot)
        tb_layout.addWidget(self._btn_toggle)
        tb_layout.addWidget(btn_close)

        # -- SimLife status trigger bar --
        self._simlife_tab = QWidget()
        self._simlife_tab.setFixedHeight(36)
        self._simlife_tab.setCursor(Qt.CursorShape.PointingHandCursor)
        self._simlife_tab.setStyleSheet(
            "background:transparent; border-bottom:1px solid #21262d;"
        )
        tab_layout = QHBoxLayout(self._simlife_tab)
        tab_layout.setContentsMargins(12, 0, 12, 0)

        self._simlife_indicator = QLabel("😊")
        self._simlife_indicator.setFixedSize(18, 18)
        self._simlife_indicator.setStyleSheet(
            "background:#238636; border-radius:9px; font-size:10px;"
        )
        self._simlife_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._simlife_brief = QLabel("Click to view life status")
        self._simlife_brief.setStyleSheet("color:#8b949e; font-size:11px;")

        self._simlife_arrow = QLabel("▼")
        self._simlife_arrow.setStyleSheet("color:#484f58; font-size:9px;")

        tab_layout.addWidget(self._simlife_indicator)
        tab_layout.addWidget(self._simlife_brief, 1)
        tab_layout.addWidget(self._simlife_arrow)

        # Click to expand/collapse status panel
        self._simlife_tab.mousePressEvent = lambda e: self._toggle_simlife_panel()

        # -- SimLife status panel --
        self._simlife_panel = SimLifePanel()
        self._simlife_panel.setStyleSheet("background:transparent;")
        self._simlife_panel.hide()

        # -- Message area --
        self._msg_area = QWidget()
        self._msg_area.setStyleSheet("background:transparent;")
        self._msg_layout = QVBoxLayout(self._msg_area)
        self._msg_layout.setContentsMargins(10, 4, 10, 4)
        self._msg_layout.setSpacing(4)
        self._msg_layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(self._msg_area)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("""
            QScrollArea { background:transparent; border:none; }
            QScrollBar:vertical { width:4px; }
            QScrollBar::handle:vertical { background:#30363d; border-radius:2px; }
        """)
        self._scroll = scroll

        # -- Input bar --
        self._input_bar = QWidget()
        self._input_bar.setStyleSheet(
            "background:transparent; border-top:1px solid #21262d;"
        )
        self._input_bar.setFixedHeight(52)
        in_layout = QHBoxLayout(self._input_bar)
        in_layout.setContentsMargins(10, 8, 10, 8)

        self._input = QLineEdit()
        self._input.setPlaceholderText(t("float_input_placeholder"))
        self._input.setStyleSheet("""
            QLineEdit {
                background:#161b22; border:1px solid #30363d;
                border-radius:8px; padding:6px 10px;
                color:#e6edf3; font-size:12px;
            }
            QLineEdit:focus { border-color:#58a6ff; }
        """)
        self._input.returnPressed.connect(self._send)

        btn_send = QPushButton("↑")
        btn_send.setFixedSize(32, 32)
        btn_send.setStyleSheet("""
            QPushButton {
                background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
                    stop:0 #1f6feb,stop:1 #7c3aed);
                border:none; border-radius:8px;
                color:white; font-size:16px; font-weight:bold;
            }
            QPushButton:hover { opacity:0.9; }
        """)
        btn_send.clicked.connect(self._send)

        in_layout.addWidget(self._input)
        in_layout.addWidget(btn_send)

        # -- Assembly --
        root.addWidget(self._titlebar)
        root.addWidget(self._simlife_tab)
        root.addWidget(self._simlife_panel)
        root.addWidget(scroll)
        root.addWidget(self._input_bar)

        self._scroll.hide() if not self._expanded else None

    def _setup_animation(self):
        self._anim = QPropertyAnimation(self, b"size")
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _position_bottom_right(self):
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            screen.right()  - self.WIDTH - 20,
            screen.bottom() - self.EXPANDED_H - 20
        )

    def update_chat_time(self):
        """Called when user sends message, reset idle timer (compatible with old interface)"""
        pass  # Already managed globally by main.py AGIApp

    # -- SimLife panel --
    def _toggle_simlife_panel(self):
        if self._simlife_shown:
            self._simlife_panel.hide()
            self._simlife_arrow.setText("▼")
            self._simlife_shown = False
            # Restore height
            self.resize(self.WIDTH, self.EXPANDED_H)
        else:
            self._refresh_simlife()
            self._simlife_panel.show()
            self._simlife_arrow.setText("▲")
            self._simlife_shown = True
            # Increase height to fit panel
            self.resize(self.WIDTH, self.EXPANDED_H + 200)

    def _refresh_simlife(self):
        """Read data from simlife_client and refresh panel"""
        if not self.simlife_client:
            return
        try:
            summary = self.simlife_client.get_life_summary()
            if summary:
                self._simlife_panel.update_data(summary)
                # Update tab bar brief
                emoji = summary.get("mood_emoji", "😊")
                scene = summary.get("scene", "")
                activity = summary.get("activity", "")
                if activity:
                    brief = f"{scene} · {activity[:12]}"
                else:
                    brief = scene if scene else "Click to view life status"
                self._simlife_brief.setText(brief)
                self._simlife_indicator.setText(emoji)
                # Mood color
                mood = summary.get("mood", 70)
                if mood >= 80:
                    bg = "#238636"
                elif mood >= 60:
                    bg = "#1f6feb"
                elif mood >= 40:
                    bg = "#9e6a03"
                else:
                    bg = "#da3633"
                self._simlife_indicator.setStyleSheet(
                    f"background:{bg}; border-radius:9px; font-size:10px;"
                )
        except Exception:
            pass

    def refresh_simlife_state(self):
        """Called externally by timer, refresh SimLife panel data (if panel visible)"""
        if self._simlife_shown:
            self._refresh_simlife()

    # -- Expand / Collapse --
    def toggle_expand(self):
        if self._expanded:
            self._collapse()
        else:
            self._expand()

    def _expand(self):
        self._expanded = True
        self._btn_toggle.setText("-")
        self._scroll.show()
        self._input_bar.show()
        self._simlife_tab.show()
        target_h = self.EXPANDED_H + (200 if self._simlife_shown else 0)
        self._anim.setStartValue(self.size())
        self._anim.setEndValue(QSize(self.WIDTH, target_h))
        try:
            self._anim.finished.disconnect()
        except Exception:
            pass
        self._anim.start()

    def _collapse(self):
        self._expanded = False
        self._btn_toggle.setText("+")
        self._scroll.hide()
        self._input_bar.hide()
        self._simlife_tab.hide()
        self._simlife_panel.hide()
        self._anim.setStartValue(self.size())
        self._anim.setEndValue(QSize(self.WIDTH, self.COLLAPSED_H))
        try:
            self._anim.finished.disconnect()
        except Exception:
            pass
        self._anim.start()

    # -- Messages --
    def _send(self):
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self.update_chat_time()
        self.add_message(text, is_user=True)
        # When there is pending proactive message, associate proactive msg + user reply together
        if self._pending_proactive_msg:
            self.proactive_replied.emit(self._pending_proactive_msg, text)
            self._pending_proactive_msg = None
        self.message_sent.emit(text)

    def _on_proactive_check(self, message: str):
        """Triggered when proactive message checkbox 'Replied' is checked (manual check, no reply text)"""
        self.proactive_replied.emit(message, "")
        self._pending_proactive_msg = None

    def add_message(self, text: str, is_user: bool = False, is_proactive: bool = False):
        if not is_user and is_proactive:
            self._pending_proactive_msg = text
        bubble = FloatBubble(text, is_user, is_proactive=is_proactive,
                             on_replied=self._on_proactive_check)
        self._msg_layout.insertWidget(
            self._msg_layout.count() - 1, bubble
        )
        # Scroll to bottom
        QTimer.singleShot(50, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))

    def set_thinking(self, thinking: bool):
        if thinking:
            self._emotion_lbl.setText(f"· {t('thinking')}")
            self._brain_icon.setText("⏳")
        else:
            self._brain_icon.setText("🧠")

    def update_emotion(self, emotion: str, intensity: float):
        emoji = {
            "joy": "😊", "sadness": "😔", "anger": "😤",
            "fear": "😨", "surprise": "😲", "curious": "🤔",
            "nostalgic": "😌", "trust": "🤝", "neutral": "😐"
        }.get(emotion, "🧠")
        self._brain_icon.setText(emoji)
        self._emotion_lbl.setText(f"· {emotion} {int(intensity*10)}/10")

    # -- Drag to move --
    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(e.position().toPoint())
            if isinstance(child, (QPushButton, QLineEdit, QTextEdit)):
                return
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._drag_pos and e.buttons() == Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def _on_close(self):
        self.hide()
        self.closed.emit()
