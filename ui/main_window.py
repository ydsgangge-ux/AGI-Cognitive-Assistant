"""
Main Window
Contains five tabs: Chat, Memory, Personality, Toolbox, System Settings
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime

from PyQt6.QtCore    import Qt, QThread, pyqtSignal, QTimer, QSize
import random
from PyQt6.QtGui     import QFont, QIcon, QColor, QPixmap, QPainter
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QTextEdit, QPushButton,
    QListWidget, QListWidgetItem, QTabWidget,
    QSplitter, QGroupBox, QCheckBox, QSlider,
    QComboBox, QScrollArea, QFrame, QStatusBar,
    QSizePolicy, QApplication, QFileDialog,
    QGridLayout, QSpacerItem, QMessageBox,
    QTextBrowser, QProgressBar, QSpinBox
)

from desktop.config import APP_NAME, load_config, save_config, DARK_QSS
from desktop.system import make_tray_icon


def _get_desktop() -> Path:
    """Get user Desktop folder (cross-platform)"""
    import subprocess
    p = Path.home() / "Desktop"
    if p.exists():
        return p
    if sys.platform == "linux":
        try:
            result = subprocess.run(
                ["xdg-user-dir", "DESKTOP"],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0 and result.stdout.strip():
                alt = Path(result.stdout.strip())
                if alt.exists():
                    return alt
        except Exception:
            pass
    return Path.home()

def _make_label(text: str, style: str) -> QLabel:
    """Create styled QLabel (PyQt6 does not support styleSheet in constructor)"""
    lbl = QLabel(text)
    lbl.setStyleSheet(style)
    return lbl



# -- AGI Worker Thread --
class AGIWorker(QThread):
    """Run A layer processing in background thread to avoid UI freezing"""

    finished = pyqtSignal(dict)
    error    = pyqtSignal(str)
    confirm_requested = pyqtSignal(str, object)  # (tool_name, params_dict)

    TIMEOUT_SEC = 120   # Max wait time, error on timeout

    def __init__(self, agent, user_input: str):
        super().__init__()
        self.agent = agent
        self.user_input = user_input
        self._confirm_result = None   # Main thread writes, worker thread reads
        self._confirm_event  = None   # threading.Event for cross-thread waiting

    def run(self):
        if self.agent is None:
            self.error.emit("AGI engine not initialized yet, please wait")
            return

        import threading
        self._confirm_event = threading.Event()

        # Replace confirm with thread-safe version
        original_confirm = self.agent.b.confirm
        self.agent.b.confirm = self._thread_safe_confirm

        try:
            result = self.agent.process(self.user_input)
            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(f"{e}\n{traceback.format_exc()[-300:]}")
        finally:
            self.agent.b.confirm = original_confirm

    def _thread_safe_confirm(self, tool_name: str, params: dict) -> bool:
        """Called from worker thread -> signal to main thread for popup -> wait for result"""
        self._confirm_result = None
        self._confirm_event.clear()
        self.confirm_requested.emit(tool_name, params)
        # Wait for main thread to set result (timeout 120s)
        self._confirm_event.wait(timeout=120)
        return self._confirm_result if self._confirm_result is not None else False

    def set_confirm_result(self, allowed: bool):
        """Main thread slot: set confirmation result and wake worker thread"""
        self._confirm_result = allowed
        if self._confirm_event:
            self._confirm_event.set()


# -- Message Bubble Component --
class MessageBubble(QFrame):
    """Message Bubble - QLabel + wordWrap, highly reliable"""

    def __init__(self, text: str, is_user: bool,
                 meta: dict = None, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._is_proactive = (meta or {}).get("proactive", False)
        self._replied = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        bg = "#1f6feb" if is_user else "#21262d"

        # -- Proactive message: top checkbox bar --
        if not is_user and self._is_proactive:
            top_bar = QHBoxLayout()
            top_bar.setContentsMargins(2, 0, 2, 0)
            self._reply_chk = QCheckBox()
            self._reply_chk.setText("Replied")
            self._reply_chk.setStyleSheet(
                "QCheckBox{color:#8b949e;font-size:11px;spacing:4px;}"
                "QCheckBox::indicator{width:14px;height:14px;"
                "border:1px solid #30363d;border-radius:3px;}"
                "QCheckBox::indicator:checked{background:#3fb950;"
                "border-color:#3fb950;image:none;}"
            )
            self._reply_chk.stateChanged.connect(self._on_reply_checked)

            self._reply_status = QLabel("📌 Not replied")
            self._reply_status.setStyleSheet("color:#d29922;font-size:11px;")

            top_bar.addWidget(self._reply_chk)
            top_bar.addWidget(self._reply_status)
            top_bar.addStretch()
            layout.addLayout(top_bar)

        # -- Content bubble: QLabel, wordWrap, selectable --
        content = QLabel(text)
        content.setWordWrap(True)
        content.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse |
            Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        content.setStyleSheet(f"""
            QLabel {{
                background: {bg};
                color: #e6edf3;
                border-radius: 10px;
                padding: 10px 14px;
                font-size: 13px;
                line-height: 1.5;
            }}
        """)
        content.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum
        )

        if is_user:
            row = QHBoxLayout()
            row.addStretch()
            row.addWidget(content)
            layout.addLayout(row)
        else:
            layout.addWidget(content)

        # -- AI message bottom: metadata + speak button --
        if not is_user:
            bottom = QHBoxLayout()
            bottom.setContentsMargins(2, 0, 2, 0)

            # Metadata
            if meta:
                parts = []
                if meta.get("emotion"):
                    e = meta["emotion"]
                    parts.append(f"Emotion:{e.get('primary','?')} "
                                 f"{int(e.get('intensity',0)*10)}/10")
                if meta.get("tools_used"):
                    parts.append(f"🔧 {','.join(meta['tools_used'])}")
                if meta.get("stored"):
                    parts.append("📝 Memorized")
                if parts:
                    ml = QLabel("  ·  ".join(parts))
                    ml.setStyleSheet("color:#6e7681;font-size:10px;")
                    bottom.addWidget(ml)

            bottom.addStretch()

            btn_tts = QPushButton("Speak")
            btn_tts.setFixedSize(36, 22)
            btn_tts.setToolTip("Speak this message / Click to stop")
            btn_tts.setStyleSheet(
                "QPushButton{background:#21262d;border:1px solid #30363d;"
                "border-radius:6px;color:#58a6ff;font-size:11px;padding:0 4px;}"
                "QPushButton:hover{background:#30363d;border-color:#58a6ff;}"
            )
            _tts_active = [False]
            _msg_text   = text

            def _speak(_, t=_msg_text, b=btn_tts, active=_tts_active):
                try:
                    from engine.tts_engine import get_tts
                    tts = get_tts()
                    if active[0]:
                        tts.stop()
                        active[0] = False
                        b.setText("Speak")
                        return
                    from desktop.config import load_config
                    cfg = load_config()
                    tts.set_voice(cfg.get("tts_voice", "zh-CN-XiaoxiaoNeural"))
                    tts.set_rate(cfg.get("tts_rate", 0))
                    active[0] = True
                    b.setText("Stop")
                    def _done():
                        active[0] = False
                        b.setText("Speak")
                    def _on_err(e):
                        print(f"[TTS] Speak failed: {e}")
                        active[0] = False
                        b.setText("Speak")
                    tts.speak(t, on_done=_done, on_error=_on_err)
                except Exception as ex:
                    print(f"[TTS] Call error: {ex}")

            btn_tts.clicked.connect(_speak)
            bottom.addWidget(btn_tts)
            layout.addLayout(bottom)

            # Tool steps
            if meta:
                for s in (meta.get("tool_steps") or [])[:5]:
                    ok  = s.get("result", {}).get("ok", False)
                    lbl = QLabel(
                        f"  {'✅' if ok else '❌'} {s['tool']}"
                        f"({str(s.get('params',''))[:40]})"
                    )
                    lbl.setStyleSheet(
                        f"color:{'#3fb950' if ok else '#f85149'};"
                        "font-size:10px;font-family:monospace;"
                    )
                    layout.addWidget(lbl)

    def _on_reply_checked(self, state):
        """Check reply status"""
        self._replied = (state == Qt.CheckState.Checked.value)
        if self._replied:
            self._reply_status.setText("✅ Replied")
            self._reply_status.setStyleSheet("color:#3fb950;font-size:11px;")
        else:
            self._reply_status.setText("📌 Not replied")
            self._reply_status.setStyleSheet("color:#d29922;font-size:11px;")


# -- Tool Panel (right side) --
class ToolPanel(QWidget):
    """
    Right-side tool panel
    Shows all tools, click to fill input box
    """
    tool_clicked = pyqtSignal(str, str)   # (tool_name, description)

    RISK_COLOR = {"low": "#3fb950", "medium": "#d29922", "high": "#f85149"}
    RISK_LABEL = {"low": "Safe", "medium": "Medium", "high": "High Risk"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(220)
        self.setStyleSheet("background:#161b22;border-left:1px solid #30363d;")
        self._setup_ui()
        self._load_tools()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Title bar
        header = QWidget()
        header.setFixedHeight(42)
        header.setStyleSheet(
            "background:#1c2128;border-bottom:1px solid #30363d;"
        )
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(12, 0, 12, 0)
        title = QLabel("🔧  Toolbox")
        title.setStyleSheet("color:#58a6ff;font-weight:700;font-size:13px;")
        hint = QLabel("Click to insert")
        hint.setStyleSheet("color:#8b949e;font-size:10px;")
        hlay.addWidget(title)
        hlay.addStretch()
        hlay.addWidget(hint)

        # Search box
        search_wrap = QWidget()
        search_wrap.setStyleSheet("background:#161b22;padding:8px;")
        slay = QVBoxLayout(search_wrap)
        slay.setContentsMargins(8, 8, 8, 4)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search tools…")
        self._search.setStyleSheet(
            "QLineEdit{background:#21262d;border:1px solid #30363d;"
            "border-radius:6px;padding:5px 8px;color:#e6edf3;font-size:11px;}"
            "QLineEdit:focus{border-color:#58a6ff;}"
        )
        self._search.textChanged.connect(self._filter)
        slay.addWidget(self._search)

        # Tool list
        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget{background:#161b22;border:none;outline:none;}"
            "QListWidget::item{padding:0px;border:none;}"
            "QListWidget::item:hover{background:transparent;}"
        )
        self._list.setSpacing(2)
        self._list.itemClicked.connect(self._on_click)

        layout.addWidget(header)
        layout.addWidget(search_wrap)
        layout.addWidget(self._list)

    def _load_tools(self):
        try:
            from engine.tools import TOOL_REGISTRY
            self._tools = [
                {
                    "name": name,
                    "desc": info["schema"]["description"],
                    "risk": info["risk"],
                    "params": list(
                        info["schema"]["input_schema"]
                        .get("properties", {}).keys()
                    )
                }
                for name, info in TOOL_REGISTRY.items()
            ]
        except Exception:
            self._tools = []
        self._render(self._tools)

    def _render(self, tools):
        self._list.clear()
        for t in tools:
            item = QListWidgetItem()
            item.setSizeHint(QSize(200, 68))
            item.setData(Qt.ItemDataRole.UserRole, t)
            self._list.addItem(item)

            # Custom card widget
            card = self._make_card(t)
            self._list.setItemWidget(item, card)

    def _make_card(self, t: dict) -> QWidget:
        card = QWidget()
        card.setStyleSheet(
            "QWidget{background:#1c2128;border-radius:6px;margin:2px 6px;}"
            "QWidget:hover{background:#21262d;border:1px solid #30363d;}"
        )
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(3)

        # First row: name + risk label
        top = QHBoxLayout()
        name_lbl = QLabel(t["name"])
        name_lbl.setStyleSheet(
            "color:#e6edf3;font-size:12px;font-weight:600;"
            "background:transparent;"
        )
        risk_color = self.RISK_COLOR.get(t["risk"], "#8b949e")
        risk_lbl = QLabel(self.RISK_LABEL.get(t["risk"], t["risk"]))
        risk_lbl.setStyleSheet(
            f"color:{risk_color};font-size:10px;background:transparent;"
            f"border:1px solid {risk_color};border-radius:3px;padding:1px 5px;"
        )
        top.addWidget(name_lbl)
        top.addStretch()
        top.addWidget(risk_lbl)

        # Second row: description
        desc_lbl = QLabel(t["desc"][:52] + ("…" if len(t["desc"]) > 52 else ""))
        desc_lbl.setStyleSheet(
            "color:#8b949e;font-size:11px;background:transparent;"
        )
        desc_lbl.setWordWrap(True)

        lay.addLayout(top)
        lay.addWidget(desc_lbl)
        return card

    def _filter(self, text: str):
        filtered = [
            t for t in self._tools
            if text.lower() in t["name"].lower()
            or text.lower() in t["desc"].lower()
        ] if text else self._tools
        self._render(filtered)

    def _on_click(self, item: QListWidgetItem):
        t = item.data(Qt.ItemDataRole.UserRole)
        if t:
            self.tool_clicked.emit(t["name"], t["desc"])


# -- Slash Command Completer Popup --
class SlashCompleter(QWidget):
    """
    Command completion list when typing /
    Fills input box on selection
    """
    selected = pyqtSignal(str)   # Selected command text

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setFixedWidth(280)
        self.setStyleSheet(
            "QWidget{background:#161b22;border:1px solid #30363d;"
            "border-radius:8px;}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget{background:transparent;border:none;outline:none;}"
            "QListWidget::item{padding:7px 12px;border-radius:4px;"
            "font-size:12px;color:#e6edf3;}"
            "QListWidget::item:selected{background:#1f6feb;}"
            "QListWidget::item:hover{background:#21262d;}"
        )
        self._list.itemClicked.connect(
            lambda item: self.selected.emit(item.data(Qt.ItemDataRole.UserRole))
        )
        layout.addWidget(self._list)
        self._all_commands = []

    def load_commands(self):
        """Load all commands from tool registry"""
        try:
            from engine.tools import TOOL_REGISTRY
            self._all_commands = [
                {
                    "cmd":   f"/{name}",
                    "label": f"/{name}  —  {info['schema']['description'][:40]}",
                    "fill":  f"Please help me use the {name} tool, "
                }
                for name, info in TOOL_REGISTRY.items()
            ]
        except Exception:
            self._all_commands = []

    def show_for(self, text: str, pos):
        """Filter and show based on typed /xxx"""
        query = text.lstrip("/").lower()
        filtered = [
            c for c in self._all_commands
            if query in c["cmd"].lower() or query in c["label"].lower()
        ] if query or text == "/" else self._all_commands

        self._list.clear()
        for c in filtered[:10]:
            item = QListWidgetItem(c["label"])
            item.setData(Qt.ItemDataRole.UserRole, c["fill"])
            self._list.addItem(item)

        if filtered:
            h = min(len(filtered), 10) * 32 + 8
            self.setFixedHeight(h)
            self.move(pos)
            self.show()
        else:
            self.hide()


# -- Chat Page --
class ChatPage(QWidget):

    message_sent = pyqtSignal(str)
    simlife_toggled = pyqtSignal(bool)  # SimLife scene mode toggle

    def __init__(self, parent=None):
        super().__init__(parent)
        self._completer = SlashCompleter(self)
        self._completer.load_commands()
        self._setup_ui()

    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Left: messages + input
        left = QWidget()
        layout = QVBoxLayout(left)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Message scroll area
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            "QScrollArea{border:none;background:#0d1117;}"
        )
        self._msg_container = QWidget()
        self._msg_container.setStyleSheet("background:#0d1117;")
        self._msg_layout = QVBoxLayout(self._msg_container)
        self._msg_layout.setContentsMargins(16, 16, 16, 16)
        self._msg_layout.setSpacing(10)
        self._msg_layout.addStretch()
        self._scroll.setWidget(self._msg_container)

        # Input area
        input_frame = QFrame()
        input_frame.setStyleSheet(
            "QFrame{background:#161b22;border-top:1px solid #30363d;}"
        )
        input_frame.setFixedHeight(100)
        in_layout = QHBoxLayout(input_frame)
        in_layout.setContentsMargins(14, 10, 14, 10)

        self._input = QTextEdit()
        self._input.setPlaceholderText(
            "Enter message or task… / Type / to select tool  (Enter to send, Shift+Enter for newline)"
        )
        self._input.setFixedHeight(72)
        # Disable auto URL detection to prevent text loss when typing URLs
        self._input.setAutoFormatting(QTextEdit.AutoFormattingFlag.AutoNone)
        self._input.setStyleSheet(
            "QTextEdit{background:#21262d;border:1px solid #30363d;"
            "border-radius:8px;padding:8px;color:#e6edf3;font-size:13px;}"
            "QTextEdit:focus{border-color:#58a6ff;}"
        )
        self._input.installEventFilter(self)
        self._input.textChanged.connect(self._on_text_changed)

        # Attachment button (image/file)
        self._pending_file = None   # Pending attachment path
        btn_attach = QPushButton("📎 File")
        btn_attach.setFixedSize(56, 72)
        btn_attach.setToolTip("Upload image or Office file")
        btn_attach.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:8px;color:#8b949e;font-size:12px;padding:2px;}"
            "QPushButton:hover{color:#58a6ff;border-color:#58a6ff;}"
        )
        btn_attach.clicked.connect(self._pick_file)

        # Attachment preview label
        self._attach_lbl = QLabel("")
        self._attach_lbl.setStyleSheet(
            "color:#58a6ff;font-size:11px;padding:0 4px;"
        )
        self._attach_lbl.setMaximumWidth(160)
        self._attach_lbl.setWordWrap(False)

        btn_send = QPushButton("Send")
        btn_send.setObjectName("btn_primary")
        btn_send.setFixedSize(72, 72)
        btn_send.setStyleSheet(
            "QPushButton#btn_primary{"
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #1f6feb,stop:1 #7c3aed);"
            "border:none;border-radius:8px;color:white;"
            "font-size:13px;font-weight:700;}"
        )
        btn_send.clicked.connect(self._send)

        in_layout.addWidget(btn_attach)
        in_layout.addWidget(self._attach_lbl)
        in_layout.addWidget(self._input)

        # SimLife scene toggle button
        self._simlife_mode = False
        self.btn_simlife = QPushButton("🌱 Enter Scene")
        self.btn_simlife.setFixedSize(72, 72)
        self.btn_simlife.setToolTip("Left click: Enter/Exit scene\nRight click: Open SimLife settings")
        self._style_simlife_btn()
        self.btn_simlife.clicked.connect(self._toggle_simlife)
        self.btn_simlife.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.btn_simlife.customContextMenuRequested.connect(
            self._simlife_context_menu
        )

        in_layout.addWidget(self.btn_simlife)
        in_layout.addWidget(btn_send)

        layout.addWidget(self._scroll)
        layout.addWidget(input_frame)

        # Right: VRM avatar + tool panel
        right_col = QWidget()
        right_col.setFixedWidth(220)
        right_col.setStyleSheet("background:#161b22;")
        right_lay = QVBoxLayout(right_col)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        # VRM panel (modular loading, hide on failure)
        self.vrm_widget = None
        try:
            from vrm_module import VRM_AVAILABLE, vrm_widget_class
            from desktop.config import load_config
            _cfg = load_config()
            if VRM_AVAILABLE and _cfg.get("vrm_enabled", True):
                self.vrm_widget = vrm_widget_class(
                    parent=right_col,
                    width=_cfg.get("vrm_width", 220),
                    height=_cfg.get("vrm_height", 220),
                )
                right_lay.addWidget(self.vrm_widget)
        except Exception as e:
            print(f"[VRM] ChatPage load skipped: {e}")

        # Tool panel
        self.tool_panel = ToolPanel()
        self.tool_panel.tool_clicked.connect(self._on_tool_clicked)
        # Tool panel removes own fixed width and background (controlled by parent)
        self.tool_panel.setFixedWidth(220)
        self.tool_panel.setStyleSheet("")  # Clear own background
        right_lay.addWidget(self.tool_panel, stretch=1)

        # Completion selected
        self._completer.selected.connect(self._on_completer_selected)

        outer.addWidget(left, stretch=1)
        outer.addWidget(right_col)

    def _on_text_changed(self):
        """Detect / prefix, show completion"""
        text = self._input.toPlainText()
        if text.startswith("/") and "\n" not in text:
            # Calculate popup position (above input box)
            pos = self._input.mapToGlobal(self._input.pos())
            from PyQt6.QtCore import QPoint
            popup_pos = self._input.mapToGlobal(
                QPoint(0, -self._completer.height() - 4)
            )
            self._completer.show_for(text, popup_pos)
        else:
            self._completer.hide()

    def _on_completer_selected(self, fill_text: str):
        """Completion selected: replace input box content"""
        self._input.setPlainText(fill_text)
        self._completer.hide()
        # Move cursor to end
        cursor = self._input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._input.setTextCursor(cursor)
        self._input.setFocus()

    def _on_tool_clicked(self, tool_name: str, desc: str):
        """Click tool panel card: fill input box"""
        current = self._input.toPlainText().strip()
        if current:
            # Existing content: append tool instruction
            self._input.setPlainText(
                f"{current} (use {tool_name} tool to complete)"
            )
        else:
            self._input.setPlainText(f"Please use the {tool_name} tool, ")
        cursor = self._input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._input.setTextCursor(cursor)
        self._input.setFocus()

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if obj is self._input and event.type() == QEvent.Type.KeyPress:
            # Tab / Enter confirm completion
            if (self._completer.isVisible() and
                    event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Down)):
                self._completer._list.setFocus()
                self._completer._list.setCurrentRow(0)
                return True
            if event.key() == Qt.Key.Key_Escape:
                self._completer.hide()
                return False
            if (event.key() == Qt.Key.Key_Return and
                    not event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                self._completer.hide()
                self._send()
                return True
        return super().eventFilter(obj, event)

    def _style_simlife_btn(self):
        """Update button style based on simlife_mode state"""
        if self._simlife_mode:
            self.btn_simlife.setText("🌱 In Scene")
            self.btn_simlife.setStyleSheet(
                "QPushButton{background:#238636;border:none;"
                "border-radius:8px;color:white;font-size:11px;font-weight:700;}"
            )
        else:
            self.btn_simlife.setText("🌱 Enter Scene")
            self.btn_simlife.setStyleSheet(
                "QPushButton{background:#21262d;border:1px solid #30363d;"
                "border-radius:8px;color:#8b949e;font-size:11px;font-weight:700;}"
                "QPushButton:hover{color:#58a6ff;border-color:#58a6ff;}"
            )

    def _toggle_simlife(self):
        self._simlife_mode = not self._simlife_mode
        self._style_simlife_btn()
        self.simlife_toggled.emit(self._simlife_mode)

    def _simlife_context_menu(self, pos):
        """SimLife button right-click menu"""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self.btn_simlife)
        menu.setStyleSheet(
            "QMenu{background:#21262d;border:1px solid #30363d;"
            "color:#e6edf3;padding:4px;}"
            "QMenu::item{padding:6px 16px;}"
            "QMenu::item:hover{background:#30363d;}"
        )
        act_setup = menu.addAction("🔧 Open SimLife Settings")
        act_open = menu.addAction("🌐 Open in Browser")

        chosen = menu.exec(self.btn_simlife.mapToGlobal(pos))
        if chosen == act_setup:
            import webbrowser
            webbrowser.open("http://127.0.0.1:8769")
        elif chosen == act_open:
            import webbrowser
            webbrowser.open("http://127.0.0.1:8769")

    def _pick_file(self):
        """Open file picker, supports images and Office files"""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select File",
            str(Path.home()),
            "Images and Documents (*.png *.jpg *.jpeg *.gif *.webp "
            "*.docx *.xlsx *.pptx *.pdf *.csv *.txt *.md);;"
            "Images (*.png *.jpg *.jpeg *.gif *.webp);;"
            "Office Documents (*.docx *.xlsx *.pptx *.pdf *.csv)"
        )
        if path:
            self._pending_file = path
            fname = Path(path).name
            self._attach_lbl.setText(f"📎 {fname[:20]}")
            self._attach_lbl.setToolTip(path)

    def _send(self):
        text = self._input.toPlainText().strip()
        pending = self._pending_file

        if not text and not pending:
            return

        # -- Extract URL from rich text (browser copy link returns only title in toPlainText) --
        html = self._input.toHtml()
        if "<a href=" in html and "http" not in text:
            import re
            urls = re.findall(r'<a[^>]+href="([^"]+)"', html)
            if urls:
                url_str = "\n".join(urls)
                text = (text + "\n" + url_str).strip()

        # Clear input
        self._input.clear()
        self._pending_file = None
        self._attach_lbl.setText("")

        if pending:
            ext = Path(pending).suffix.lower()
            is_image = ext in (".png", ".jpg", ".jpeg", ".gif", ".webp")

            if is_image:
                self._show_image_bubble(pending)
                msg = f"[Image: {pending}]"
                if text:
                    msg += f"\n{text}"
                else:
                    msg += "\nPlease analyze this image"
            else:
                self.add_user_message(f"📎 {Path(pending).name}\n{text or 'Please analyze this file'}")
                msg = f"[File: {pending}]\n{text or 'Please analyze this file content'}"

            self.message_sent.emit(msg)
        elif text:
            self.message_sent.emit(text)

    def _show_image_bubble(self, image_path: str, is_user: bool = True):
        """Show image preview bubble in chat area"""
        from PyQt6.QtGui import QPixmap
        bubble = QFrame()
        bubble.setFrameShape(QFrame.Shape.NoFrame)
        bl = QHBoxLayout(bubble)
        img_lbl = QLabel()
        pix = QPixmap(image_path)
        if not pix.isNull():
            pix = pix.scaledToWidth(
                280, Qt.TransformationMode.SmoothTransformation
            )
            img_lbl.setPixmap(pix)
        else:
            img_lbl.setText(f"🖼 {Path(image_path).name}")
        if is_user:
            bl.addStretch()
            img_lbl.setStyleSheet(
                "background:#1f6feb;border-radius:10px;padding:6px;"
            )
        else:
            img_lbl.setStyleSheet(
                "background:#21262d;border-radius:10px;padding:6px;"
            )
        bl.addWidget(img_lbl)
        if not is_user:
            bl.addStretch()
        self._msg_layout.insertWidget(self._msg_layout.count() - 1, bubble)
        self._scroll_to_bottom()

    def fill_input(self, text: str):
        """External call: fill input box (e.g. OCR result)"""
        self._input.setPlainText(text)
        cursor = self._input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._input.setTextCursor(cursor)
        self._input.setFocus()

    def add_user_message(self, text: str):
        bubble = MessageBubble(text, is_user=True)
        self._msg_layout.insertWidget(
            self._msg_layout.count() - 1, bubble
        )
        self._scroll_to_bottom()

    def add_ai_message(self, text: str, meta: dict = None):
        bubble = MessageBubble(text, is_user=False, meta=meta)
        self._msg_layout.insertWidget(
            self._msg_layout.count() - 1, bubble
        )
        self._scroll_to_bottom()

    def add_thinking_indicator(self) -> QLabel:
        lbl = QLabel("⏳ Thinking…")
        lbl.setStyleSheet(
            "color:#8b949e;font-size:12px;padding:8px 14px;"
            "background:#21262d;border-radius:8px;"
        )
        lbl.setObjectName("thinking_indicator")
        self._msg_layout.insertWidget(
            self._msg_layout.count() - 1, lbl,
            alignment=Qt.AlignmentFlag.AlignLeft
        )
        self._scroll_to_bottom()
        return lbl

    def remove_thinking_indicator(self):
        for i in range(self._msg_layout.count()):
            item = self._msg_layout.itemAt(i)
            if item and item.widget():
                w = item.widget()
                if w.objectName() == "thinking_indicator":
                    self._msg_layout.removeWidget(w)
                    w.deleteLater()
                    break

    def _scroll_to_bottom(self):
        QTimer.singleShot(
            80,
            lambda: self._scroll.verticalScrollBar().setValue(
                self._scroll.verticalScrollBar().maximum()
            )
        )


# -- Memory Page --
class MemoryPage(QWidget):
    def __init__(self, db_file: str, auth_ref=None, parent=None):
        super().__init__(parent)
        self.db_file = db_file
        self._auth_ref = auth_ref
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        # Search bar
        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Semantic search memories…")
        self._search.returnPressed.connect(self.search)
        btn_search = QPushButton("Search")
        btn_search.clicked.connect(self.search)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.load)

        # Clear memory button (red, right side)
        btn_clear = QPushButton("🗑  Clear Memory")
        btn_clear.setFixedHeight(30)
        btn_clear.setStyleSheet(
            "QPushButton{background:rgba(248,81,73,.1);border:1px solid #f85149;"
            "border-radius:6px;color:#f85149;font-size:12px;padding:0 12px;}"
            "QPushButton:hover{background:rgba(248,81,73,.25);}"
        )
        btn_clear.clicked.connect(self._clear_memory_dialog)

        search_row.addWidget(self._search)
        search_row.addWidget(btn_search)
        search_row.addWidget(btn_refresh)
        search_row.addStretch()
        search_row.addWidget(btn_clear)

        # Filter tags
        filter_row = QHBoxLayout()
        self._filters = {}
        for f, lbl in [("all","All"),("detail","Detail"),
                        ("outline","Outline"),("summary","Summary"),
                        ("emotional","Emotional"),("semantic","Semantic")]:
            btn = QPushButton(lbl)
            btn.setCheckable(True)
            btn.setChecked(f == "all")
            btn.setFixedHeight(28)
            btn.setStyleSheet(
                "QPushButton{background:#21262d;border:1px solid #30363d;"
                "border-radius:12px;padding:0 12px;font-size:11px;}"
                "QPushButton:checked{background:rgba(31,111,235,.2);"
                "color:#58a6ff;border-color:#58a6ff;}"
            )
            btn.clicked.connect(lambda checked, flt=f: self._apply_filter(flt))
            self._filters[f] = btn
            filter_row.addWidget(btn)
        filter_row.addStretch()

        # Statistics bar
        self._stats_lbl = QLabel("")
        self._stats_lbl.setStyleSheet("color:#8b949e;font-size:11px;margin:4px 0;")

        # Memory list
        self._list = QListWidget()
        self._list.setSpacing(4)
        self._list.setStyleSheet(
            "QListWidget{background:#161b22;border:1px solid #30363d;"
            "border-radius:8px;}"
            "QListWidget::item{padding:10px;border-radius:6px;"
            "border-bottom:1px solid #21262d;}"
            "QListWidget::item:selected{background:#1f3a5c;}"
        )

        layout.addLayout(search_row)
        layout.addLayout(filter_row)
        layout.addWidget(self._stats_lbl)
        layout.addWidget(self._list)

        self._all_items = []
        self._current_filter = "all"

    def load(self):
        from engine.db_guard import guarded_connect

        # Show hint when not logged in
        if self._auth_ref and self._auth_ref() and self._auth_ref().is_guest():
            self._list.clear()
            self._stats_lbl.setText("")
            item = QListWidgetItem("🔒  Please login to view memory")
            item.setForeground(QColor("#8b949e"))
            self._list.addItem(item)
            return

        # Get current user ID
        user_id = None
        if self._auth_ref and self._auth_ref() and not self._auth_ref().is_guest():
            user_id = self._auth_ref().user_id

        try:
            with guarded_connect(self.db_file) as conn:
                if user_id:
                    rows = conn.execute(
                        "SELECT id,content,modality,level,emotion_json,"
                        "importance,created_at FROM memories "
                        "WHERE user_id=? OR user_id='default' OR user_id='system' "
                        "ORDER BY created_at DESC, importance DESC LIMIT 300",
                        (user_id,)
                    ).fetchall()
                    total = conn.execute(
                        "SELECT COUNT(*) FROM memories "
                        "WHERE user_id=? OR user_id='default' OR user_id='system'",
                        (user_id,)
                    ).fetchone()[0]
                else:
                    rows = conn.execute(
                        "SELECT id,content,modality,level,emotion_json,"
                        "importance,created_at FROM memories "
                        "ORDER BY created_at DESC, importance DESC LIMIT 300"
                    ).fetchall()
                    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                edges = conn.execute(
                    "SELECT COUNT(*) FROM memory_edges"
                ).fetchone()[0] if self._table_exists(conn, "memory_edges") else 0
            self._all_items = rows
            self._stats_lbl.setText(
                f"Total {total} memories  ·  {edges} edges  "
                f"·  Showing {min(len(rows), 300)}  "
                f"·  Double-click for full content"
            )
            self._render(rows)
        except Exception as e:
            self._list.clear()
            self._list.addItem(f"Load failed: {e}")

    def _table_exists(self, conn, table_name: str) -> bool:
        res = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        ).fetchone()
        return res is not None

    def _clear_memory_dialog(self):
        """
        Triple confirm memory clear
        1st: Select clear scope
        2nd: Text confirmation
        3rd: Final confirmation
        """
        from engine.db_guard import guarded_connect

        # -- Step 1: Select clear scope --
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QRadioButton, QButtonGroup

        dlg1 = QDialog(self)
        dlg1.setWindowTitle("Clear Memory — Step 1/3")
        dlg1.setFixedWidth(420)
        dlg1.setStyleSheet(
            "QDialog{background:#161b22;color:#e6edf3;}"
            "QLabel{color:#e6edf3;}"
            "QRadioButton{color:#e6edf3;padding:6px;font-size:13px;}"
            "QRadioButton::indicator{width:16px;height:16px;}"
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:6px;padding:6px 16px;color:#e6edf3;}"
            "QPushButton:hover{border-color:#58a6ff;}"
        )
        lay1 = QVBoxLayout(dlg1)
        lay1.setSpacing(12)
        lay1.setContentsMargins(20, 20, 20, 20)

        warning_lbl = QLabel(
            "⚠️  <b style='color:#f85149;'>Clearing memory is irreversible!</b><br>"
            "Please select the scope to clear:"
        )
        warning_lbl.setTextFormat(Qt.TextFormat.RichText)
        warning_lbl.setWordWrap(True)
        lay1.addWidget(warning_lbl)

        btn_group = QButtonGroup(dlg1)
        options = [
            ("all",      "🗑  Clear all memories (including association network)"),
            ("detail",   "Clear detail level memories (keep outline and summary)"),
            ("outline",  "Clear outline level memories"),
            ("summary",  "Clear summary level memories"),
            ("emotional","Clear emotional modality memories"),
            ("semantic", "Clear semantic modality memories"),
        ]
        radios = {}
        for val, text in options:
            rb = QRadioButton(text)
            if val == "all":
                rb.setChecked(True)
            btn_group.addButton(rb)
            radios[rb] = val
            lay1.addWidget(rb)

        btns1 = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns1.button(QDialogButtonBox.StandardButton.Ok).setText("Next →")
        btns1.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        btns1.accepted.connect(dlg1.accept)
        btns1.rejected.connect(dlg1.reject)
        lay1.addWidget(btns1)

        if dlg1.exec() != QDialog.DialogCode.Accepted:
            return

        clear_scope = next((v for rb, v in radios.items() if rb.isChecked()), "all")
        scope_label = dict(options)[clear_scope]

        # -- Step 2: Enter confirmation text --
        from PyQt6.QtWidgets import QInputDialog
        confirm_word = "CONFIRM CLEAR"
        text, ok = QInputDialog.getText(
            self,
            "Clear Memory — Step 2/3",
            f"Action: {scope_label}\n\n"
            f"Please type '{confirm_word}' to continue:",
        )
        if not ok or text.strip() != confirm_word:
            QMessageBox.warning(self, "Cancelled", "Input mismatch, operation cancelled.")
            return

        # -- Step 3: Final confirmation popup --
        final = QMessageBox(self)
        final.setWindowTitle("Clear Memory — Step 3/3 · Final Confirmation")
        final.setIcon(QMessageBox.Icon.Critical)
        final.setText(
            f"<b style='color:#f85149; font-size:14px;'>Final Confirmation</b><br><br>"
            f"Action: <b>{scope_label}</b><br><br>"
            "This operation <b>cannot be undone</b>, are you sure?"
        )
        final.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        final.setDefaultButton(QMessageBox.StandardButton.No)
        final.button(QMessageBox.StandardButton.Yes).setText("✅ Confirm Clear")
        final.button(QMessageBox.StandardButton.No).setText("❌ Cancel")
        final.setStyleSheet(
            "QMessageBox{background:#161b22;}"
            "QLabel{color:#e6edf3;font-size:13px;}"
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:6px;padding:6px 20px;color:#e6edf3;min-width:100px;}"
            "QPushButton:hover{border-color:#58a6ff;}"
        )
        if final.exec() != QMessageBox.StandardButton.Yes:
            return

        # -- Execute clear --
        self._do_clear(clear_scope)

    def _do_clear(self, scope: str):
        """Actually execute clear operation"""
        from engine.db_guard import guarded_connect
        try:
            with guarded_connect(self.db_file) as conn:
                if scope == "all":
                    conn.execute("DELETE FROM memories")
                    if self._table_exists(conn, "memory_edges"):
                        conn.execute("DELETE FROM memory_edges")
                    if self._table_exists(conn, "memory_entities"):
                        conn.execute("DELETE FROM memory_entities")
                    if self._table_exists(conn, "formed_cognition"):
                        conn.execute("DELETE FROM formed_cognition")
                    # Reset auto-increment sequence
                    conn.execute(
                        "DELETE FROM sqlite_sequence WHERE name='memories'"
                    ) if self._table_exists(conn, "sqlite_sequence") else None
                    deleted_msg = "All memories and association network"
                elif scope in ("detail", "outline", "summary"):
                    count = conn.execute(
                        "SELECT COUNT(*) FROM memories WHERE level=?", (scope,)
                    ).fetchone()[0]
                    conn.execute("DELETE FROM memories WHERE level=?", (scope,))
                    deleted_msg = f"{count} {scope} level memories"
                elif scope in ("emotional", "semantic", "visual",
                               "auditory", "procedural", "autobio"):
                    count = conn.execute(
                        "SELECT COUNT(*) FROM memories WHERE modality=?", (scope,)
                    ).fetchone()[0]
                    conn.execute("DELETE FROM memories WHERE modality=?", (scope,))
                    deleted_msg = f"{count} {scope} modality memories"
                else:
                    deleted_msg = "Unknown scope"

                conn.commit()

            # Refresh list
            self.load()
            QMessageBox.information(
                self, "Clear Complete",
                f"✅ Cleared: {deleted_msg}\n\nMemory database updated."
            )
        except Exception as e:
            QMessageBox.critical(self, "Clear Failed", f"❌ Operation failed: {e}")

    def search(self):
        q = self._search.text().strip()
        if not q:
            self._render(self._all_items)
            return
        # Simple keyword filter (vector search in production)
        filtered = [r for r in self._all_items if q.lower() in r[1].lower()]
        self._render(filtered)

    def _apply_filter(self, flt: str):
        for k, btn in self._filters.items():
            btn.setChecked(k == flt)
        self._current_filter = flt
        if flt == "all":
            self._render(self._all_items)
        else:
            filtered = [r for r in self._all_items
                        if r[2] == flt or r[3] == flt]
            self._render(filtered)

    def _render(self, rows):
        self._list.clear()
        level_color = {"detail": "#3fb950", "outline": "#d29922", "summary": "#58a6ff"}
        for row in rows:
            mid, content, modality, level, em_json, importance, created = row
            try:
                em = json.loads(em_json)
            except Exception:
                em = {}
            color = level_color.get(level, "#8b949e")
            lbl = {"detail":"Detail","outline":"Outline","summary":"Summary"}.get(level, level)
            preview = content[:120] + ("…" if len(content) > 120 else "")

            item = QListWidgetItem(
                f"[{lbl}·{modality}]  {preview}\n"
                f"Importance:{int(importance*10)}/10  "
                f"Emotion:{em.get('primary','—')}  "
                f"{(created or '')[:16]}"
            )
            item.setForeground(QColor(color))
            # Full content in tooltip
            item.setToolTip(content)
            item.setData(Qt.ItemDataRole.UserRole, content)
            self._list.addItem(item)

        # Double-click to show full content
        try:
            self._list.itemDoubleClicked.disconnect()
        except Exception:
            pass
        def _show_full(item):
            full = item.data(Qt.ItemDataRole.UserRole) or item.text()
            dlg = QTextBrowser()
            dlg.setWindowTitle("Full Memory Content")
            dlg.setWindowFlag(Qt.WindowType.Window)
            dlg.setPlainText(full)
            dlg.setMinimumSize(500, 300)
            dlg.setStyleSheet(
                "QTextBrowser{background:#161b22;color:#e6edf3;"
                "font-size:13px;padding:16px;border:none;}"
            )
            dlg.show()
            self._detail_dlg = dlg  # keep ref
        self._list.itemDoubleClicked.connect(_show_full)


# -- Settings Page --
class LearnerPage(QWidget):
    """
    Active Learning Page
    - Manually trigger AGI active learning (fetch news/articles)
    - Configure learning topics, learning time
    - View experience/cognition list
    - Real-time log
    """
    learn_requested = pyqtSignal(list)  # Emit topic list

    def __init__(self, db_file: str, parent=None):
        super().__init__(parent)
        self.db_file = db_file
        self._worker  = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top bar
        header = QWidget()
        header.setFixedHeight(48)
        header.setStyleSheet("background:#161b22;border-bottom:1px solid #30363d;")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(16, 0, 16, 0)
        title = QLabel("🎓  Active Learning")
        title.setStyleSheet("color:#e6edf3;font-size:15px;font-weight:700;")
        self._status_lbl = QLabel("Ready")
        self._status_lbl.setStyleSheet("color:#8b949e;font-size:12px;")
        h_lay.addWidget(title)
        h_lay.addStretch()
        h_lay.addWidget(self._status_lbl)
        layout.addWidget(header)

        # Main body: top-bottom split
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setStyleSheet("QSplitter::handle{background:#21262d;height:1px;}")

        # -- Top: config + experience cognition --
        top_widget = QWidget()
        top_widget.setStyleSheet("background:#0d1117;")
        top_lay = QHBoxLayout(top_widget)
        top_lay.setContentsMargins(16, 16, 16, 16)
        top_lay.setSpacing(16)

        # Left: learning config
        config_box = QWidget()
        config_box.setStyleSheet(
            "QWidget{background:#161b22;border:1px solid #30363d;border-radius:10px;}"
        )
        config_box.setFixedWidth(300)
        cl = QVBoxLayout(config_box)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        cl.addWidget(_make_label("📚  Learning Configuration", "color:#e6edf3;font-size:13px;font-weight:700;background:transparent;border:none;"))

        cl.addWidget(_make_label("Learning topics (one per line)", "color:#8b949e;font-size:11px;background:transparent;border:none;"))
        self._topics_edit = QTextEdit()
        self._topics_edit.setFixedHeight(100)
        self._topics_edit.setStyleSheet(
            "QTextEdit{background:#0d1117;border:1px solid #30363d;border-radius:6px;"
            "color:#e6edf3;font-size:12px;padding:6px;}"
        )
        self._topics_edit.setPlainText("AI Artificial Intelligence\nTechnology News\nWorld News")
        cl.addWidget(self._topics_edit)

        cl.addWidget(_make_label("Scheduled learning (daily time)", "color:#8b949e;font-size:11px;background:transparent;border:none;"))
        hour_row = QHBoxLayout()
        self._hour_spin = QComboBox()
        self._hour_spin.addItems([f"{h:02d}:00" for h in range(24)])
        self._hour_spin.setCurrentIndex(8)
        self._hour_spin.setStyleSheet(
            "QComboBox{background:#0d1117;border:1px solid #30363d;border-radius:6px;"
            "color:#e6edf3;padding:4px 8px;font-size:12px;}"
            "QComboBox::drop-down{border:none;}"
            "QComboBox QAbstractItemView{background:#161b22;color:#e6edf3;border:1px solid #30363d;}"
        )
        self._auto_learn_chk = QCheckBox("Enable scheduled learning")
        self._auto_learn_chk.setStyleSheet("color:#c9d1d9;font-size:12px;")
        hour_row.addWidget(self._hour_spin)
        hour_row.addWidget(self._auto_learn_chk)
        cl.addLayout(hour_row)

        cl.addStretch()

        btn_learn = QPushButton("🚀  Start Learning")
        btn_learn.setFixedHeight(36)
        btn_learn.setStyleSheet(
            "QPushButton{background:rgba(31,111,235,.2);border:1px solid #1f6feb;"
            "border-radius:8px;color:#58a6ff;font-size:13px;font-weight:600;}"
            "QPushButton:hover{background:rgba(31,111,235,.4);}"
            "QPushButton:disabled{opacity:0.4;}"
        )
        btn_learn.clicked.connect(self._start_learning)
        self._btn_learn = btn_learn
        cl.addWidget(btn_learn)

        top_lay.addWidget(config_box)

        # Right: experience cognition display
        cognition_box = QWidget()
        cognition_box.setStyleSheet(
            "QWidget{background:#161b22;border:1px solid #30363d;border-radius:10px;}"
        )
        cog_lay = QVBoxLayout(cognition_box)
        cog_lay.setContentsMargins(16, 14, 16, 14)
        cog_lay.setSpacing(8)

        cog_header = QHBoxLayout()
        cog_header.addWidget(_make_label("🧠  Formed Cognitions (immutable)",
            "color:#e6edf3;font-size:13px;font-weight:700;background:transparent;border:none;"))
        self._cog_count = QLabel("0 items")
        self._cog_count.setStyleSheet("color:#8b949e;font-size:11px;")
        cog_header.addStretch()
        cog_header.addWidget(self._cog_count)
        btn_refresh_cog = QPushButton("Refresh")
        btn_refresh_cog.setFixedHeight(24)
        btn_refresh_cog.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:4px;color:#8b949e;font-size:11px;padding:0 8px;}"
            "QPushButton:hover{color:#c9d1d9;}"
        )
        btn_refresh_cog.clicked.connect(self._load_cognitions)
        cog_header.addWidget(btn_refresh_cog)
        cog_lay.addLayout(cog_header)

        hint = QLabel("💡 Automatically formed from conversations and learning, only clearing all memory can delete them")
        hint.setStyleSheet("color:#6e7681;font-size:11px;font-style:italic;")
        cog_lay.addWidget(hint)

        self._cog_list = QListWidget()
        self._cog_list.setStyleSheet(
            "QListWidget{background:#0d1117;border:1px solid #21262d;"
            "border-radius:6px;outline:none;}"
            "QListWidget::item{color:#c9d1d9;padding:8px 12px;font-size:12px;"
            "border-bottom:1px solid #21262d;}"
            "QListWidget::item:selected{background:#21262d;}"
        )
        cog_lay.addWidget(self._cog_list)
        top_lay.addWidget(cognition_box)

        splitter.addWidget(top_widget)

        # -- Bottom: real-time log --
        log_widget = QWidget()
        log_widget.setStyleSheet("background:#0d1117;")
        ll = QVBoxLayout(log_widget)
        ll.setContentsMargins(16, 8, 16, 12)
        ll.setSpacing(6)
        log_header = QHBoxLayout()
        log_header.addWidget(_make_label("📋  Learning Log", "color:#8b949e;font-size:12px;font-weight:600;"))
        btn_clear_log = QPushButton("Clear")
        btn_clear_log.setFixedHeight(22)
        btn_clear_log.setStyleSheet(
            "QPushButton{background:transparent;border:none;color:#6e7681;font-size:11px;}"
            "QPushButton:hover{color:#c9d1d9;}"
        )
        btn_clear_log.clicked.connect(lambda: self._log_view.clear())
        log_header.addStretch()
        log_header.addWidget(btn_clear_log)
        ll.addLayout(log_header)

        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setStyleSheet(
            "QTextEdit{background:#161b22;border:1px solid #30363d;border-radius:8px;"
            "color:#8b949e;font-family:Consolas,'Courier New',monospace;font-size:11px;padding:8px;}"
        )
        ll.addWidget(self._log_view)
        splitter.addWidget(log_widget)
        splitter.setSizes([320, 200])

        layout.addWidget(splitter)
        self._load_cognitions()

    def _load_cognitions(self):
        try:
            from engine.learner import FormedCognitionStore
            store = FormedCognitionStore(self.db_file)
            items = store.get_all()
            self._cog_count.setText(f"{len(items)} entries")
            self._cog_list.clear()
            SOURCE_ICON = {"conversation": "💬", "learning": "📖", "reflection": "🔍"}
            for it in items:
                icon = SOURCE_ICON.get(it["source"], "·")
                strength_mark = " ★" if it["strength"] >= 1.5 else ""
                text = f"{icon} {it['content']}{strength_mark}"
                item = QListWidgetItem(text)
                item.setToolTip(
                    f"Source: {it['source']}\nTrigger: {it.get('trigger','')}\n"
                    f"Time: {it['formed_at'][:16]}\nStrength: {it['strength']:.1f}"
                )
                self._cog_list.addItem(item)
            if not items:
                self._cog_list.addItem("(None yet, will form automatically through conversations and learning)")
        except Exception:
            pass

    def _start_learning(self):
        topics_text = self._topics_edit.toPlainText().strip()
        topics = [t.strip() for t in topics_text.splitlines() if t.strip()]
        if not topics:
            return
        self._btn_learn.setEnabled(False)
        self._btn_learn.setText("Learning…")
        self._status_lbl.setText("🔄 Learning…")
        self._log("=" * 40)
        self._log(f"Starting learning, topics: {', '.join(topics)}")
        self.learn_requested.emit(topics)

    def _log(self, msg: str):
        self._log_view.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        self._log_view.verticalScrollBar().setValue(
            self._log_view.verticalScrollBar().maximum()
        )

    def on_learn_done(self):
        self._btn_learn.setEnabled(True)
        self._btn_learn.setText("🚀  Start Learning")
        self._status_lbl.setText("✅ Learning complete")
        self._load_cognitions()
        QTimer.singleShot(3000, lambda: self._status_lbl.setText("Ready"))

    def on_learn_log(self, msg: str):
        self._log(msg)


class SettingsPage(QWidget):

    settings_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cfg = load_config()
        self._setup_ui()

    def _setup_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;}")

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # -- LLM Config (multi-provider) --
        from engine.llm_client import PROVIDER_INFO
        from engine.i18n import LANGUAGES, set_language, get_language

        api_box = QGroupBox("LLM Configuration")
        api_lay = QGridLayout(api_box)
        COMBO_STYLE = (
            "QComboBox{background:#21262d;border:1px solid #30363d;"
            "border-radius:5px;padding:5px 8px;color:#e6edf3;font-size:12px;}"
            "QComboBox QAbstractItemView{background:#21262d;color:#e6edf3;"
            "selection-background-color:#1f6feb;border:1px solid #30363d;}"
        )

        # Language selection
        api_lay.addWidget(QLabel("Interface Language:"), 0, 0)
        self._lang_combo = QComboBox()
        self._lang_combo.setStyleSheet(COMBO_STYLE)
        for code, name in LANGUAGES.items():
            self._lang_combo.addItem(name, code)
        saved_lang = self._cfg.get("language", "zh")
        for i in range(self._lang_combo.count()):
            if self._lang_combo.itemData(i) == saved_lang:
                self._lang_combo.setCurrentIndex(i); break
        api_lay.addWidget(self._lang_combo, 0, 1)

        # Provider selection
        api_lay.addWidget(QLabel("LLM Provider:"), 1, 0)
        self._provider = QComboBox()
        self._provider.setStyleSheet(COMBO_STYLE)
        self._provider_keys = list(PROVIDER_INFO.keys())
        for key in self._provider_keys:
            self._provider.addItem(PROVIDER_INFO[key]["name"], key)
        saved_provider = self._cfg.get("api_provider", "deepseek")
        for i in range(self._provider.count()):
            if self._provider.itemData(i) == saved_provider:
                self._provider.setCurrentIndex(i); break
        self._provider.currentIndexChanged.connect(self._on_provider_changed)
        api_lay.addWidget(self._provider, 1, 1)

        # Model selection
        api_lay.addWidget(QLabel("Model:"), 2, 0)
        self._model_combo = QComboBox()
        self._model_combo.setStyleSheet(COMBO_STYLE)
        self._model_combo.setEditable(True)
        api_lay.addWidget(self._model_combo, 2, 1)

        # API Key
        api_lay.addWidget(QLabel("API Key:"), 3, 0)
        self._api_key = QLineEdit(self._cfg.get("api_key", ""))
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("Enter your API key")
        api_lay.addWidget(self._api_key, 3, 1)

        # Registration link
        self._api_link_lbl = QLabel("")
        self._api_link_lbl.setOpenExternalLinks(True)
        self._api_link_lbl.setStyleSheet("color:#58a6ff;font-size:11px;")
        api_lay.addWidget(self._api_link_lbl, 4, 1)

        # -- Multimodal Model Config (Vision) --
        from engine.vision_client import VISION_PROVIDER_INFO, check_vision_available

        vision_box = QGroupBox("👁️ Multimodal Model (Vision)")
        vision_lay = QGridLayout(vision_box)

        # Info label
        vision_desc = QLabel(
            "Configure independent multimodal model for image/video/audio understanding.\n"
            "Runs independently from text LLM. Leave empty to auto-inherit main LLM multimodal capability."
        )
        vision_desc.setStyleSheet("color:#8b949e;font-size:11px;")
        vision_desc.setWordWrap(True)
        vision_lay.addWidget(vision_desc, 0, 0, 1, 2)

        # Vision Provider selection
        vision_lay.addWidget(QLabel("Vision Provider:"), 1, 0)
        self._vision_provider = QComboBox()
        self._vision_provider.setStyleSheet(COMBO_STYLE)
        self._vision_provider.addItem("🔄 Auto inherit from main LLM", "")
        for key in VISION_PROVIDER_INFO:
            info = VISION_PROVIDER_INFO[key]
            self._vision_provider.addItem(info["name"], key)
        saved_vision_provider = self._cfg.get("vision_provider", "")
        for i in range(self._vision_provider.count()):
            if self._vision_provider.itemData(i) == saved_vision_provider:
                self._vision_provider.setCurrentIndex(i); break
        self._vision_provider.currentIndexChanged.connect(self._on_vision_provider_changed)
        vision_lay.addWidget(self._vision_provider, 1, 1)

        # Vision Model selection
        vision_lay.addWidget(QLabel("Vision Model:"), 2, 0)
        self._vision_model = QComboBox()
        self._vision_model.setStyleSheet(COMBO_STYLE)
        self._vision_model.setEditable(True)
        vision_lay.addWidget(self._vision_model, 2, 1)

        # Vision API Key
        vision_lay.addWidget(QLabel("API Key:"), 3, 0)
        self._vision_api_key = QLineEdit(self._cfg.get("vision_api_key", ""))
        self._vision_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._vision_api_key.setPlaceholderText("Leave empty to inherit main LLM API Key")
        vision_lay.addWidget(self._vision_api_key, 3, 1)

        # Vision Base URL (advanced settings)
        vision_lay.addWidget(QLabel("Custom URL:"), 4, 0)
        self._vision_base_url = QLineEdit(self._cfg.get("vision_base_url", ""))
        self._vision_base_url.setPlaceholderText("Leave empty for default URL")
        vision_lay.addWidget(self._vision_base_url, 4, 1)

        # Supported modality labels
        self._vision_support_lbl = QLabel("")
        self._vision_support_lbl.setStyleSheet("font-size:11px;color:#8b949e;")
        vision_lay.addWidget(self._vision_support_lbl, 5, 0, 1, 2)

        # Vision registration link
        self._vision_link_lbl = QLabel("")
        self._vision_link_lbl.setOpenExternalLinks(True)
        self._vision_link_lbl.setStyleSheet("color:#58a6ff;font-size:11px;")
        vision_lay.addWidget(self._vision_link_lbl, 6, 1)

        # Initialize vision provider state
        self._on_vision_provider_changed(self._vision_provider.currentIndex())

        # Ollama extra settings (shown only when Ollama selected)
        self._ollama_widget = QWidget()
        ol_lay = QGridLayout(self._ollama_widget)
        ol_lay.setContentsMargins(0, 0, 0, 0)
        ol_lay.addWidget(QLabel("Ollama URL:"), 0, 0)
        self._ollama_url = QLineEdit(self._cfg.get("ollama_url", "http://localhost:11434"))
        ol_lay.addWidget(self._ollama_url, 0, 1)
        ol_lay.addWidget(QLabel("Ollama Model:"), 1, 0)
        self._ollama_model = QLineEdit(self._cfg.get("ollama_model", "qwen2.5:7b"))
        self._ollama_model.setPlaceholderText("qwen2.5:7b / llama3.1:8b / ...")
        ol_lay.addWidget(self._ollama_model, 1, 1)
        btn_check_ollama = QPushButton("🔍 Test Ollama")
        btn_check_ollama.setFixedHeight(28)
        btn_check_ollama.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:5px;color:#e6edf3;font-size:12px;}"
            "QPushButton:hover{border-color:#58a6ff;}"
        )
        btn_check_ollama.clicked.connect(self._check_ollama)
        self._ollama_status = QLabel("")
        self._ollama_status.setStyleSheet("font-size:11px;color:#8b949e;")
        ol_row = QHBoxLayout()
        ol_row.addWidget(btn_check_ollama)
        ol_row.addWidget(self._ollama_status)
        ol_row.addStretch()
        ol_lay.addLayout(ol_row, 2, 1)
        api_lay.addWidget(self._ollama_widget, 5, 0, 1, 2)

        self._on_provider_changed(self._provider.currentIndex())

        # Hotkey config
        hotkey_box = QGroupBox("Global Hotkeys")
        hk_lay = QGridLayout(hotkey_box)

        hk_lay.addWidget(QLabel("Activate floating window:"), 0, 0)
        self._hk_activate = QLineEdit(
            self._cfg.get("hotkey_activate", "ctrl+shift+space")
        )
        hk_lay.addWidget(self._hk_activate, 0, 1)

        hk_lay.addWidget(QLabel("Screenshot OCR:"), 1, 0)
        self._hk_screenshot = QLineEdit(
            self._cfg.get("hotkey_screenshot", "ctrl+shift+s")
        )
        hk_lay.addWidget(self._hk_screenshot, 1, 1)

        # Window behavior
        win_box = QGroupBox("Window Behavior")
        win_lay = QVBoxLayout(win_box)

        self._chk_tray = QCheckBox("Minimize to tray on close (don't quit)")
        self._chk_tray.setChecked(self._cfg.get("tray_minimize", True))
        win_lay.addWidget(self._chk_tray)

        self._chk_autostart = QCheckBox("Launch at startup")
        from desktop.system import AutoStart
        self._chk_autostart.setChecked(AutoStart.is_enabled())
        self._chk_autostart.stateChanged.connect(self._toggle_autostart)
        win_lay.addWidget(self._chk_autostart)

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Floating window opacity:"))
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(40, 100)
        self._opacity_slider.setValue(int(self._cfg.get("float_opacity", 0.95) * 100))
        self._opacity_lbl = QLabel(f"{self._opacity_slider.value()}%")
        self._opacity_slider.valueChanged.connect(
            lambda v: self._opacity_lbl.setText(f"{v}%")
        )
        opacity_row.addWidget(self._opacity_slider)
        opacity_row.addWidget(self._opacity_lbl)
        win_lay.addLayout(opacity_row)

        # OCR
        ocr_box = QGroupBox("OCR Settings")
        ocr_lay = QGridLayout(ocr_box)
        ocr_lay.addWidget(QLabel("Recognition language:"), 0, 0)
        self._ocr_lang = QLineEdit(self._cfg.get("ocr_language", "chi_sim+eng"))
        ocr_lay.addWidget(self._ocr_lang, 0, 1)
        ocr_lay.addWidget(QLabel(
            "Tesseract language codes, e.g. chi_sim+eng\n"
            "Install from: https://github.com/tesseract-ocr/tesseract"
        ), 1, 1)

        # Save button
        btn_save = QPushButton("💾  Save Settings")
        btn_save.setObjectName("btn_primary")
        btn_save.setFixedHeight(40)
        btn_save.clicked.connect(self._save)

        self._save_msg = QLabel("")
        self._save_msg.setStyleSheet("color:#3fb950;font-size:12px;")

        # Text-to-Speech (TTS)
        tts_box = QGroupBox("🔊 Text-to-Speech (TTS)")
        tts_lay = QGridLayout(tts_box)

        tts_lay.addWidget(QLabel("Enable TTS:"), 0, 0)
        self._tts_enable = QCheckBox("Auto-read after reply")
        self._tts_enable.setChecked(self._cfg.get("tts_enabled", False))
        tts_lay.addWidget(self._tts_enable, 0, 1)

        tts_lay.addWidget(QLabel("Voice:"), 1, 0)
        self._tts_voice = QComboBox()
        from engine.tts_engine import VOICE_OPTIONS
        for vid, vname in VOICE_OPTIONS:
            self._tts_voice.addItem(vname, vid)
        saved_voice = self._cfg.get("tts_voice", "zh-CN-XiaoxiaoNeural")
        for i in range(self._tts_voice.count()):
            if self._tts_voice.itemData(i) == saved_voice:
                self._tts_voice.setCurrentIndex(i)
                break
        self._tts_voice.setStyleSheet(
            "QComboBox{background:#21262d;border:1px solid #30363d;"
            "border-radius:5px;padding:5px 8px;color:#e6edf3;}"
            "QComboBox QAbstractItemView{background:#21262d;color:#e6edf3;"
            "selection-background-color:#1f6feb;}"
        )
        tts_lay.addWidget(self._tts_voice, 1, 1)

        tts_lay.addWidget(QLabel("Speech rate:"), 2, 0)
        rate_row = QHBoxLayout()
        self._tts_rate = QSlider(Qt.Orientation.Horizontal)
        self._tts_rate.setRange(-50, 50)
        self._tts_rate.setValue(self._cfg.get("tts_rate", 0))
        self._tts_rate_lbl = QLabel(f"{self._tts_rate.value():+d}%")
        self._tts_rate.valueChanged.connect(
            lambda v: self._tts_rate_lbl.setText(f"{v:+d}%")
        )
        rate_row.addWidget(self._tts_rate)
        rate_row.addWidget(self._tts_rate_lbl)
        tts_lay.addLayout(rate_row, 2, 1)

        # Check if edge-tts is installed
        try:
            import edge_tts
            tts_status = "✅ edge-tts installed (high quality)"
            tts_status_color = "#3fb950"
        except ImportError:
            tts_status = "⚠️ edge-tts not installed, run: pip install edge-tts"
            tts_status_color = "#d29922"
        tts_status_lbl = QLabel(tts_status)
        tts_status_lbl.setStyleSheet(f"color:{tts_status_color};font-size:11px;")
        tts_lay.addWidget(tts_status_lbl, 3, 1)

        # -- Thinking Mode --
        think_box = QGroupBox("🧠 Thinking Mode")
        think_lay = QGridLayout(think_box)

        think_desc = QLabel(
            "Enable deep thinking during reasoning to improve response quality.\n"
            "Auto mode: Perception layer judges question complexity, skips thinking for simple ones.\n"
            "Providers without thinking support (Groq/Baidu/Xunfei/Ollama) will auto-ignore."
        )
        think_desc.setStyleSheet("color:#8b949e;font-size:11px;")
        think_desc.setWordWrap(True)
        think_lay.addWidget(think_desc, 0, 0, 1, 2)

        think_lay.addWidget(QLabel("Thinking mode:"), 1, 0)
        self._thinking_mode = QComboBox()
        self._thinking_mode.setStyleSheet(COMBO_STYLE)
        self._thinking_mode.addItem("Auto (recommended) — Perception layer judges complexity", "auto")
        self._thinking_mode.addItem("Always on — Deep thinking for all reasoning", "always_on")
        self._thinking_mode.addItem("Always off — Prioritize speed", "always_off")
        saved_mode = self._cfg.get("thinking_mode", "auto")
        for i in range(self._thinking_mode.count()):
            if self._thinking_mode.itemData(i) == saved_mode:
                self._thinking_mode.setCurrentIndex(i); break
        think_lay.addWidget(self._thinking_mode, 1, 1)

        think_lay.addWidget(QLabel("Thinking depth:"), 2, 0)
        self._thinking_effort = QComboBox()
        self._thinking_effort.setStyleSheet(COMBO_STYLE)
        self._thinking_effort.addItem("Low", "low")
        self._thinking_effort.addItem("Medium", "medium")
        self._thinking_effort.addItem("High", "high")
        self._thinking_effort.addItem("Max", "max")
        saved_effort = self._cfg.get("thinking_effort", "high")
        for i in range(self._thinking_effort.count()):
            if self._thinking_effort.itemData(i) == saved_effort:
                self._thinking_effort.setCurrentIndex(i); break
        think_lay.addWidget(self._thinking_effort, 2, 1)

        think_lay.addWidget(QLabel("Thinking budget:"), 3, 0)
        budget_row = QHBoxLayout()
        self._thinking_budget = QSpinBox()
        self._thinking_budget.setRange(1024, 32768)
        self._thinking_budget.setSingleStep(1024)
        self._thinking_budget.setValue(self._cfg.get("thinking_budget", 8000))
        self._thinking_budget.setSuffix(" tokens")
        self._thinking_budget.setStyleSheet(
            "QSpinBox{background:#21262d;border:1px solid #30363d;"
            "border-radius:5px;padding:5px 8px;color:#e6edf3;}"
        )
        budget_row.addWidget(self._thinking_budget)
        budget_lbl = QLabel("(Claude/Gemini/Qwen/Zhipu use, DeepSeek/OpenAI ignore)")
        budget_lbl.setStyleSheet("color:#8b949e;font-size:11px;")
        budget_row.addWidget(budget_lbl)
        budget_row.addStretch()
        think_lay.addLayout(budget_row, 3, 1)

        # -- Speech Recognition (STT) --
        stt_box = QGroupBox("🎤 Speech Recognition (STT)")
        stt_lay = QGridLayout(stt_box)

        stt_desc = QLabel(
            "Voice input feature. Convert microphone audio to text and send to AI.\n"
            "Select recognition engine and configure parameters."
        )
        stt_desc.setStyleSheet("color:#8b949e;font-size:11px;")
        stt_desc.setWordWrap(True)
        stt_lay.addWidget(stt_desc, 0, 0, 1, 2)

        # STT Provider selection
        stt_lay.addWidget(QLabel("Recognition engine:"), 1, 0)
        self._stt_provider = QComboBox()
        self._stt_provider.setStyleSheet(COMBO_STYLE)
        self._stt_provider.addItem("DeepSeek Whisper (online, reuse main API Key)", "deepseek")
        self._stt_provider.addItem("Xunfei Speech (online, best for Chinese)", "xunfei")
        self._stt_provider.addItem("Local Whisper (offline, need download model)", "whisper_local")
        saved_stt = self._cfg.get("stt_provider", "deepseek")
        for i in range(self._stt_provider.count()):
            if self._stt_provider.itemData(i) == saved_stt:
                self._stt_provider.setCurrentIndex(i); break
        self._stt_provider.currentIndexChanged.connect(self._on_stt_provider_changed)
        stt_lay.addWidget(self._stt_provider, 1, 1)

        # Xunfei credentials area (hidden by default)
        self._stt_xunfei_widget = QWidget()
        xunfei_lay = QGridLayout(self._stt_xunfei_widget)
        xunfei_lay.setContentsMargins(0, 0, 0, 0)

        xunfei_lay.addWidget(QLabel("APPID:"), 0, 0)
        self._xunfei_app_id = QLineEdit(self._cfg.get("xunfei_app_id", ""))
        self._xunfei_app_id.setPlaceholderText("Xunfei platform APPID")
        xunfei_lay.addWidget(self._xunfei_app_id, 0, 1)

        xunfei_lay.addWidget(QLabel("API Key:"), 1, 0)
        self._xunfei_api_key = QLineEdit(self._cfg.get("xunfei_api_key", ""))
        self._xunfei_api_key.setPlaceholderText("Xunfei API Key")
        xunfei_lay.addWidget(self._xunfei_api_key, 1, 1)

        xunfei_lay.addWidget(QLabel("API Secret:"), 2, 0)
        self._xunfei_api_secret = QLineEdit(self._cfg.get("xunfei_api_secret", ""))
        self._xunfei_api_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._xunfei_api_secret.setPlaceholderText("Xunfei API Secret")
        xunfei_lay.addWidget(self._xunfei_api_secret, 2, 1)

        xunfei_link = QLabel('<a href="https://www.xfyun.cn/services/voicedictation" style="color:#58a6ff;">Apply for free Xunfei Speech API →</a>')
        xunfei_link.setOpenExternalLinks(True)
        xunfei_link.setStyleSheet("font-size:11px;")
        xunfei_lay.addWidget(xunfei_link, 3, 1)

        stt_lay.addWidget(self._stt_xunfei_widget, 2, 0, 1, 2)

        # Local Whisper model selection (hidden by default)
        self._stt_whisper_widget = QWidget()
        whisper_lay = QHBoxLayout(self._stt_whisper_widget)
        whisper_lay.setContentsMargins(0, 0, 0, 0)
        whisper_lay.addWidget(QLabel("Model:"))
        self._whisper_model = QComboBox()
        self._whisper_model.setStyleSheet(COMBO_STYLE)
        for m in ["tiny", "base", "small", "medium", "large"]:
            size_info = {"tiny": "~39MB", "base": "~74MB", "small": "~244MB",
                         "medium": "~769MB", "large": "~1.5GB"}.get(m, "")
            self._whisper_model.addItem(f"{m} ({size_info})", m)
        saved_model = self._cfg.get("whisper_model", "base")
        for i in range(self._whisper_model.count()):
            if self._whisper_model.itemData(i) == saved_model:
                self._whisper_model.setCurrentIndex(i); break
        whisper_lay.addWidget(self._whisper_model)
        whisper_lay.addStretch()
        stt_lay.addWidget(self._stt_whisper_widget, 3, 0, 1, 2)

        # STT status check
        try:
            import sounddevice
            stt_status = "✅ sounddevice installed (recording available)"
            stt_status_color = "#3fb950"
        except ImportError:
            stt_status = "⚠️ sounddevice not installed, run: pip install sounddevice SoundFile"
            stt_status_color = "#d29922"
        stt_status_lbl = QLabel(stt_status)
        stt_status_lbl.setStyleSheet(f"color:{stt_status_color};font-size:11px;")
        stt_lay.addWidget(stt_status_lbl, 4, 1)

        # Initialize Xunfei/Whisper area visibility
        self._on_stt_provider_changed(self._stt_provider.currentIndex())

        # -- Sensor Module (Sensor Agent) --
        sensor_box = QGroupBox("🤖 Sensor Agent Module")
        sensor_lay = QGridLayout(sensor_box)

        sensor_desc = QLabel(
            "Connect to robot dog/robot hardware sensors.\n"
            "Enable mock mode to test without hardware."
        )
        sensor_desc.setStyleSheet("color:#8b949e;font-size:11px;")
        sensor_desc.setWordWrap(True)
        sensor_lay.addWidget(sensor_desc, 0, 0, 1, 2)

        sensor_lay.addWidget(QLabel("Enable sensor:"), 1, 0)
        self._sensor_enable = QCheckBox("Enable sensor module")
        self._sensor_enable.setChecked(self._cfg.get("sensor_enabled", False))
        sensor_lay.addWidget(self._sensor_enable, 1, 1)

        sensor_lay.addWidget(QLabel("Mock mode:"), 2, 0)
        self._sensor_mock = QCheckBox("Use mock data (no hardware)")
        self._sensor_mock.setChecked(self._cfg.get("sensor_mock", True))
        sensor_lay.addWidget(self._sensor_mock, 2, 1)

        sensor_lay.addWidget(QLabel("Device type:"), 3, 0)
        self._sensor_type = QComboBox()
        self._sensor_type.setStyleSheet(COMBO_STYLE)
        self._sensor_type.addItem("Robot Dog", "robot_dog")
        self._sensor_type.addItem("Robot Arm", "robot_arm")
        self._sensor_type.addItem("Custom", "custom")
        saved_stype = self._cfg.get("sensor_type", "robot_dog")
        for i in range(self._sensor_type.count()):
            if self._sensor_type.itemData(i) == saved_stype:
                self._sensor_type.setCurrentIndex(i); break
        sensor_lay.addWidget(self._sensor_type, 3, 1)

        sensor_lay.addWidget(QLabel("MQTT address:"), 4, 0)
        mqtt_row = QHBoxLayout()
        self._sensor_mqtt_host = QLineEdit(self._cfg.get("sensor_mqtt_host", "localhost"))
        self._sensor_mqtt_host.setPlaceholderText("localhost")
        self._sensor_mqtt_host.setMaximumWidth(180)
        mqtt_row.addWidget(self._sensor_mqtt_host)
        mqtt_row.addWidget(QLabel("Port:"))
        self._sensor_mqtt_port = QLineEdit(str(self._cfg.get("sensor_mqtt_port", 1883)))
        self._sensor_mqtt_port.setMaximumWidth(80)
        mqtt_row.addWidget(self._sensor_mqtt_port)
        mqtt_row.addStretch()
        sensor_lay.addLayout(mqtt_row, 4, 1)

        sensor_lay.addWidget(QLabel("Push interval:"), 5, 0)
        interval_row = QHBoxLayout()
        self._sensor_interval = QSpinBox()
        self._sensor_interval.setRange(5, 300)
        self._sensor_interval.setValue(self._cfg.get("sensor_push_interval", 30))
        self._sensor_interval.setSuffix(" sec")
        self._sensor_interval.setStyleSheet(
            "QSpinBox{background:#21262d;border:1px solid #30363d;"
            "border-radius:5px;padding:5px 8px;color:#e6edf3;}"
        )
        interval_row.addWidget(self._sensor_interval)
        interval_row.addStretch()
        sensor_lay.addLayout(interval_row, 5, 1)

        # MQTT status check
        try:
            import paho.mqtt
            sensor_status = "✅ paho-mqtt installed"
            sensor_status_color = "#3fb950"
        except ImportError:
            sensor_status = "⚠️ paho-mqtt not installed, run: pip install paho-mqtt"
            sensor_status_color = "#d29922"
        sensor_status_lbl = QLabel(sensor_status)
        sensor_status_lbl.setStyleSheet(f"color:{sensor_status_color};font-size:11px;")
        sensor_lay.addWidget(sensor_status_lbl, 6, 1)

        # News API (NewsAPI)
        news_box = QGroupBox("📰 News API (NewsAPI)")
        news_lay = QGridLayout(news_box)

        news_lay.addWidget(QLabel("API Key:"), 0, 0)
        self._newsapi_key = QLineEdit(self._cfg.get("newsapi_key", ""))
        self._newsapi_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._newsapi_key.setPlaceholderText("API Key from newsapi.org")
        news_lay.addWidget(self._newsapi_key, 0, 1)

        try:
            import newsapi
            news_status = "✅ newsapi-python installed"
            news_status_color = "#3fb950"
        except ImportError:
            news_status = "⚠️ Not installed, run install_newsapi.bat"
            news_status_color = "#d29922"
        news_status_lbl = QLabel(news_status)
        news_status_lbl.setStyleSheet(f"color:{news_status_color};font-size:11px;")
        news_lay.addWidget(news_status_lbl, 1, 1)

        news_link = QLabel('<a href="https://newsapi.org/register" style="color:#58a6ff;">Apply for free NewsAPI Key →</a>')
        news_link.setOpenExternalLinks(True)
        news_link.setStyleSheet("font-size:11px;")
        news_lay.addWidget(news_link, 2, 1)

        layout.addWidget(api_box)
        layout.addWidget(vision_box)
        layout.addWidget(hotkey_box)
        layout.addWidget(win_box)
        layout.addWidget(tts_box)
        layout.addWidget(think_box)
        layout.addWidget(stt_box)
        layout.addWidget(sensor_box)
        layout.addWidget(news_box)
        layout.addWidget(ocr_box)
        layout.addWidget(btn_save)
        layout.addWidget(self._save_msg)
        layout.addStretch()

        scroll.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _on_provider_changed(self, idx: int):
        from engine.llm_client import PROVIDER_INFO
        key = self._provider.itemData(idx) or "deepseek"
        info = PROVIDER_INFO.get(key, {})
        is_ollama = key == "ollama"

        # Update model list
        self._model_combo.clear()
        for m in info.get("models", []):
            self._model_combo.addItem(m)
        saved_model = self._cfg.get("llm_model") or info.get("default_model", "")
        self._model_combo.setCurrentText(saved_model)

        # Update registration link
        url = info.get("url", "")
        name = info.get("name", "")
        if url:
            self._api_link_lbl.setText(
                f'Get API key: <a href="{url}" style="color:#58a6ff;">{url}</a>'
            )
        else:
            self._api_link_lbl.setText("")

        # Ollama extra settings show/hide
        self._ollama_widget.setVisible(is_ollama)
        self._api_key.setEnabled(not is_ollama)
        self._api_key.setPlaceholderText("" if is_ollama else "Enter your API key")

    def _on_vision_provider_changed(self, idx: int):
        from engine.vision_client import VISION_PROVIDER_INFO
        key = self._vision_provider.itemData(idx) or ""

        # Update model list
        self._vision_model.clear()
        if key and key in VISION_PROVIDER_INFO:
            info = VISION_PROVIDER_INFO[key]
            for m in info.get("models", []):
                self._vision_model.addItem(m)
            saved_vision_model = self._cfg.get("vision_model") or info.get("default_model", "")
            self._vision_model.setCurrentText(saved_vision_model)

            # Show supported modalities
            supports = info.get("supports", [])
            support_icons = {"image": "🖼️ Image", "video": "🎬 Video", "audio_note": "📝 Video frame", "audio": "🎵 Audio"}
            support_text = "Supports: " + " | ".join(
                support_icons.get(s, s) for s in supports
            )
            self._vision_support_lbl.setText(support_text)

            # Registration link
            url = info.get("url", "")
            if url:
                self._vision_link_lbl.setText(
                    f'Register: <a href="{url}" style="color:#58a6ff;">{url}</a>'
                )
            else:
                self._vision_link_lbl.setText("")

            self._vision_api_key.setEnabled(key != "ollama")
            self._vision_api_key.setPlaceholderText(
                "Ollama runs locally, no API Key needed" if key == "ollama"
                else "Leave empty to inherit main LLM API Key"
            )
        else:
            self._vision_support_lbl.setText("Will auto-use main LLM's multimodal capability")
            self._vision_link_lbl.setText("")
            self._vision_api_key.setEnabled(False)
            self._vision_api_key.setPlaceholderText("Auto inherit, no separate config needed")

    def _check_ollama(self):
        from engine.llm_client import OllamaClient
        url = self._ollama_url.text().strip() or "http://localhost:11434"
        client = OllamaClient(base_url=url)
        if client.is_running():
            models = client.list_models()
            # Update model dropdown
            self._ollama_model.setText(models[0] if models else "qwen2.5:7b")
            self._ollama_status.setText(
                f"✅ Connected  |  Models: {', '.join(models[:4]) or 'none'}"
            )
            self._ollama_status.setStyleSheet("font-size:11px;color:#3fb950;")
        else:
            self._ollama_status.setText("❌ Not running. Run: ollama serve")
            self._ollama_status.setStyleSheet("font-size:11px;color:#f85149;")

    def _on_stt_provider_changed(self, idx: int):
        """Show/hide corresponding config area when switching STT engine"""
        provider = self._stt_provider.itemData(idx) or "deepseek"
        self._stt_xunfei_widget.setVisible(provider == "xunfei")
        self._stt_whisper_widget.setVisible(provider == "whisper_local")

    def _toggle_autostart(self, state):
        from desktop.system import AutoStart
        if state == Qt.CheckState.Checked.value:
            AutoStart.enable()
        else:
            AutoStart.disable()

    def _save(self):
        provider_key = self._provider.currentData() or "deepseek"
        lang_key     = self._lang_combo.currentData() or "zh"

        self._cfg["api_provider"]      = provider_key
        self._cfg["language"]          = lang_key
        self._cfg["api_key"]           = self._api_key.text().strip()
        self._cfg["llm_model"]         = self._model_combo.currentText().strip()
        self._cfg["ollama_url"]        = self._ollama_url.text().strip()
        self._cfg["ollama_model"]      = self._ollama_model.text().strip()
        self._cfg["hotkey_activate"]   = self._hk_activate.text().strip()
        self._cfg["hotkey_screenshot"] = self._hk_screenshot.text().strip()
        self._cfg["tray_minimize"]     = self._chk_tray.isChecked()
        self._cfg["float_opacity"]     = self._opacity_slider.value() / 100.0
        self._cfg["tts_enabled"]       = self._tts_enable.isChecked()
        self._cfg["tts_voice"]         = self._tts_voice.currentData()
        self._cfg["tts_rate"]          = self._tts_rate.value()
        # STT Speech Recognition
        self._cfg["stt_provider"]      = self._stt_provider.currentData() or "deepseek"
        self._cfg["xunfei_app_id"]     = self._xunfei_app_id.text().strip()
        self._cfg["xunfei_api_key"]    = self._xunfei_api_key.text().strip()
        self._cfg["xunfei_api_secret"] = self._xunfei_api_secret.text().strip()
        self._cfg["whisper_model"]     = self._whisper_model.currentData() or "base"
        # Sensor Module
        self._cfg["sensor_enabled"]    = self._sensor_enable.isChecked()
        self._cfg["sensor_mock"]       = self._sensor_mock.isChecked()
        self._cfg["sensor_type"]       = self._sensor_type.currentData() or "robot_dog"
        self._cfg["sensor_mqtt_host"]  = self._sensor_mqtt_host.text().strip()
        self._cfg["sensor_mqtt_port"]  = int(self._sensor_mqtt_port.text() or 1883)
        self._cfg["sensor_push_interval"] = self._sensor_interval.value()
        # Thinking Mode
        self._cfg["thinking_mode"]     = self._thinking_mode.currentData() or "auto"
        self._cfg["thinking_effort"]   = self._thinking_effort.currentData() or "high"
        self._cfg["thinking_budget"]   = self._thinking_budget.value()
        # OCR      = self._ocr_lang.text().strip()
        self._cfg["newsapi_key"]       = self._newsapi_key.text().strip()
        # Multimodal Vision Config
        self._cfg["vision_provider"]   = self._vision_provider.currentData() or ""
        self._cfg["vision_model"]      = self._vision_model.currentText().strip()
        self._cfg["vision_api_key"]    = self._vision_api_key.text().strip()
        self._cfg["vision_base_url"]   = self._vision_base_url.text().strip()

        # Apply language immediately
        try:
            from engine.i18n import set_language
            set_language(lang_key)
        except Exception:
            pass

        save_config(self._cfg)
        self.settings_changed.emit(self._cfg)
        self._save_msg.setText("✅ Saved. Restart to apply.")
        QTimer.singleShot(3000, lambda: self._save_msg.setText(""))


# -- Tool Test Page --
class ToolTestPage(QWidget):
    """
    Tool Test Bench
    Left: Tool list + param input
    Right: Results (raw JSON + formatted)
    History is traceable
    """

    RISK_COLOR = {"low": "#3fb950", "medium": "#d29922", "high": "#f85149"}
    RISK_LABEL = {"low": "🟢 Safe", "medium": "🟡 Medium Risk", "high": "🔴 High Risk"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tools      = {}   # name -> info
        self._current    = None # Currently selected tool name
        self._param_widgets = {}  # param_name -> QLineEdit/QTextEdit
        self._history    = []   # Execution history
        self._setup_ui()
        self._load_tools()

    # ---- UI Build ----
    def _setup_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- Left: tool list --
        left = QWidget()
        left.setFixedWidth(240)
        left.setStyleSheet(
            "background:#161b22;border-right:1px solid #30363d;"
        )
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(0)

        # Left title
        left_header = QLabel("  🔧  Select Tool")
        left_header.setFixedHeight(42)
        left_header.setStyleSheet(
            "background:#1c2128;color:#58a6ff;font-weight:700;"
            "font-size:13px;border-bottom:1px solid #30363d;padding-left:8px;"
        )

        # Category filter
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(8, 6, 8, 4)
        self._risk_filter = QComboBox()
        self._risk_filter.addItems(["All Risks", "🟢 Safe", "🟡 Medium", "🔴 High Risk"])
        self._risk_filter.setStyleSheet(
            "QComboBox{background:#21262d;border:1px solid #30363d;"
            "border-radius:5px;padding:4px 8px;color:#e6edf3;font-size:11px;}"
            "QComboBox::drop-down{border:none;}"
            "QComboBox QAbstractItemView{background:#21262d;color:#e6edf3;"
            "selection-background-color:#1f6feb;}"
        )
        self._risk_filter.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self._risk_filter)

        # Search
        self._tool_search = QLineEdit()
        self._tool_search.setPlaceholderText("Search…")
        self._tool_search.setStyleSheet(
            "QLineEdit{background:#21262d;border:1px solid #30363d;"
            "border-radius:5px;padding:4px 8px;color:#e6edf3;font-size:11px;}"
            "QLineEdit:focus{border-color:#58a6ff;}"
        )
        self._tool_search.textChanged.connect(self._apply_filter)

        # Tool list
        self._tool_list = QListWidget()
        self._tool_list.setStyleSheet(
            "QListWidget{background:transparent;border:none;outline:none;}"
            "QListWidget::item{padding:10px 12px;border-bottom:1px solid #21262d;"
            "color:#e6edf3;font-size:12px;}"
            "QListWidget::item:selected{background:#1f3a5c;color:#58a6ff;}"
            "QListWidget::item:hover{background:#21262d;}"
        )
        self._tool_list.currentItemChanged.connect(self._on_tool_selected)

        # History button + self-test button
        bottom_row = QHBoxLayout()
        btn_history = QPushButton("📋  History")
        btn_history.setFixedHeight(34)
        btn_history.setStyleSheet(
            "QPushButton{background:#1c2128;border:none;color:#8b949e;"
            "font-size:12px;border-top:1px solid #30363d;}"
            "QPushButton:hover{color:#58a6ff;}"
        )
        btn_history.clicked.connect(self._show_history)

        btn_self_test = QPushButton("🔬  Self Test")
        btn_self_test.setFixedHeight(34)
        btn_self_test.setStyleSheet(
            "QPushButton{background:#1c2128;border:none;color:#8b949e;"
            "font-size:12px;border-top:1px solid #30363d;"
            "border-left:1px solid #30363d;}"
            "QPushButton:hover{color:#3fb950;}"
        )
        btn_self_test.clicked.connect(self.run_self_test)
        bottom_row.addWidget(btn_history)
        bottom_row.addWidget(btn_self_test)
        bottom_row.setSpacing(0)
        bottom_row.setContentsMargins(0, 0, 0, 0)

        left_lay.addWidget(left_header)
        left_lay.addLayout(filter_row)
        left_lay.addWidget(self._tool_search)
        left_lay.addWidget(self._tool_list, stretch=1)
        left_lay.addLayout(bottom_row)

        # -- Middle: param input + execution --
        mid = QWidget()
        mid.setMinimumWidth(340)
        mid.setStyleSheet("background:#0d1117;")
        mid_lay = QVBoxLayout(mid)
        mid_lay.setContentsMargins(16, 12, 16, 12)
        mid_lay.setSpacing(10)

        # Tool title
        self._tool_title = QLabel("← Please select a tool first")
        self._tool_title.setStyleSheet(
            "color:#58a6ff;font-size:15px;font-weight:700;"
        )
        self._tool_desc = QLabel("")
        self._tool_desc.setWordWrap(True)
        self._tool_desc.setStyleSheet(
            "color:#8b949e;font-size:12px;line-height:1.5;"
        )
        self._risk_badge = QLabel("")
        self._risk_badge.setStyleSheet("font-size:12px;")

        # Param area (dynamically generated)
        params_scroll = QScrollArea()
        params_scroll.setWidgetResizable(True)
        params_scroll.setStyleSheet(
            "QScrollArea{border:1px solid #30363d;border-radius:8px;"
            "background:#161b22;}"
        )
        self._params_container = QWidget()
        self._params_container.setStyleSheet("background:#161b22;")
        self._params_layout = QVBoxLayout(self._params_container)
        self._params_layout.setContentsMargins(12, 10, 12, 10)
        self._params_layout.setSpacing(8)
        self._params_layout.addStretch()
        params_scroll.setWidget(self._params_container)

        # Execution button area
        exec_row = QHBoxLayout()
        self._btn_run = QPushButton("▶  Execute Tool")
        self._btn_run.setFixedHeight(40)
        self._btn_run.setEnabled(False)
        self._btn_run.setObjectName("btn_primary")
        self._btn_run.setStyleSheet(
            "QPushButton#btn_primary{"
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #1f6feb,stop:1 #7c3aed);"
            "border:none;border-radius:8px;color:white;"
            "font-size:13px;font-weight:700;}"
            "QPushButton#btn_primary:disabled{"
            "background:#21262d;color:#8b949e;}"
        )
        self._btn_run.clicked.connect(self._run_tool)

        self._btn_clear = QPushButton("Clear Params")
        self._btn_clear.setFixedHeight(40)
        self._btn_clear.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:8px;color:#8b949e;font-size:12px;}"
            "QPushButton:hover{border-color:#58a6ff;color:#e6edf3;}"
        )
        self._btn_clear.clicked.connect(self._clear_params)

        exec_row.addWidget(self._btn_run, stretch=2)
        exec_row.addWidget(self._btn_clear, stretch=1)

        mid_lay.addWidget(self._tool_title)
        mid_lay.addWidget(self._risk_badge)
        mid_lay.addWidget(self._tool_desc)
        mid_lay.addWidget(_make_label("Parameters:", "color:#8b949e;font-size:11px;margin-top:4px;"))
        mid_lay.addWidget(params_scroll, stretch=1)
        mid_lay.addLayout(exec_row)

        # -- Right: execution result --
        right = QWidget()
        right.setStyleSheet("background:#0d1117;")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 12, 16, 12)
        right_lay.setSpacing(8)

        result_header = QHBoxLayout()
        result_lbl = QLabel("Execution Result")
        result_lbl.setStyleSheet(
            "color:#e6edf3;font-size:13px;font-weight:700;"
        )
        self._result_status = QLabel("")
        self._result_status.setStyleSheet("font-size:12px;")

        self._btn_copy = QPushButton("Copy Result")
        self._btn_copy.setFixedHeight(28)
        self._btn_copy.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:5px;color:#8b949e;font-size:11px;padding:0 10px;}"
            "QPushButton:hover{border-color:#58a6ff;color:#e6edf3;}"
        )
        self._btn_copy.clicked.connect(self._copy_result)

        self._btn_send_to_chat = QPushButton("Send to Chat")
        self._btn_send_to_chat.setFixedHeight(28)
        self._btn_send_to_chat.setStyleSheet(
            "QPushButton{background:rgba(31,111,235,.15);border:1px solid #1f6feb;"
            "border-radius:5px;color:#58a6ff;font-size:11px;padding:0 10px;}"
            "QPushButton:hover{background:rgba(31,111,235,.3);}"
        )
        self._btn_send_to_chat.clicked.connect(self._send_result_to_chat)
        self._result_to_send = ""

        result_header.addWidget(result_lbl)
        result_header.addWidget(self._result_status)
        result_header.addStretch()
        result_header.addWidget(self._btn_copy)
        result_header.addWidget(self._btn_send_to_chat)

        # Result display (Tabs: formatted / raw JSON)
        self._result_tabs = QTabWidget()
        self._result_tabs.setStyleSheet(
            "QTabWidget::pane{border:1px solid #30363d;border-radius:6px;}"
            "QTabBar::tab{background:#161b22;border:1px solid #30363d;"
            "padding:5px 14px;margin-right:2px;border-radius:4px 4px 0 0;"
            "font-size:11px;color:#8b949e;}"
            "QTabBar::tab:selected{background:#21262d;color:#58a6ff;"
            "border-bottom-color:#21262d;}"
        )

        # Formatted view
        self._result_formatted = QTextEdit()
        self._result_formatted.setReadOnly(True)
        self._result_formatted.setStyleSheet(
            "QTextEdit{background:#161b22;border:none;color:#e6edf3;"
            "font-size:13px;padding:12px;line-height:1.6;}"
        )

        # Raw JSON view
        self._result_raw = QTextEdit()
        self._result_raw.setReadOnly(True)
        self._result_raw.setStyleSheet(
            "QTextEdit{background:#0d1117;border:none;"
            "color:#3fb950;font-family:Consolas,'Courier New',monospace;"
            "font-size:12px;padding:12px;}"
        )

        self._result_tabs.addTab(self._result_formatted, "📄 Formatted")
        self._result_tabs.addTab(self._result_raw,       "{ } Raw JSON")

        right_lay.addLayout(result_header)
        right_lay.addWidget(self._result_tabs, stretch=1)

        # -- Combine three columns --
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(
            "QSplitter::handle{background:#30363d;width:1px;}"
        )
        splitter.addWidget(left)
        splitter.addWidget(mid)
        splitter.addWidget(right)
        splitter.setSizes([240, 360, 480])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        root.addWidget(splitter)

    # ------ Tool Loading ------
    def _load_tools(self):
        try:
            from engine.tools import TOOL_REGISTRY
            self._tools = {
                name: {
                    "desc":     info["schema"]["description"],
                    "risk":     info["risk"],
                    "params":   info["schema"]["input_schema"].get("properties", {}),
                    "required": info["schema"]["input_schema"].get("required", []),
                }
                for name, info in TOOL_REGISTRY.items()
            }
        except Exception as e:
            self._tools = {}
        self._apply_filter()

    def _apply_filter(self):
        risk_idx  = self._risk_filter.currentIndex()  # 0=all,1=low,2=med,3=high
        risk_map  = {1: "low", 2: "medium", 3: "high"}
        risk_filter = risk_map.get(risk_idx)
        search    = self._tool_search.text().lower()

        self._tool_list.clear()
        for name, info in self._tools.items():
            if risk_filter and info["risk"] != risk_filter:
                continue
            if search and search not in name.lower() and search not in info["desc"].lower():
                continue
            risk_icon = {"low":"🟢","medium":"🟡","high":"🔴"}.get(info["risk"],"⚪")
            item = QListWidgetItem(f"{risk_icon}  {name}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._tool_list.addItem(item)

    # ------ Tool Selected ------
    def _on_tool_selected(self, current, previous):
        if not current:
            return
        name = current.data(Qt.ItemDataRole.UserRole)
        if not name or name not in self._tools:
            return
        self._current = name
        info = self._tools[name]

        # Update title
        self._tool_title.setText(f"🔧  {name}")
        self._tool_desc.setText(info["desc"])
        self._risk_badge.setText(
            self.RISK_LABEL.get(info["risk"], info["risk"])
        )
        risk_color = self.RISK_COLOR.get(info["risk"], "#8b949e")
        self._risk_badge.setStyleSheet(
            f"color:{risk_color};font-size:12px;"
        )

        # Dynamically generate param input fields
        self._build_param_widgets(info["params"], info["required"])
        self._btn_run.setEnabled(True)

        # Clear result
        self._result_formatted.clear()
        self._result_raw.clear()
        self._result_status.setText("")

    def _build_param_widgets(self, params: dict, required: list):
        """Dynamically generate param input area"""
        # Clear old widgets
        while self._params_layout.count() > 1:
            item = self._params_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self._param_widgets.clear()

        if not params:
            lbl = QLabel("This tool requires no parameters, click Execute directly")
            lbl.setStyleSheet("color:#8b949e;font-size:12px;")
            self._params_layout.insertWidget(0, lbl)
            return

        for i, (pname, pinfo) in enumerate(params.items()):
            is_req = pname in required
            pdesc  = pinfo.get("description", "")
            ptype  = pinfo.get("type", "string")

            # Label row
            lbl_row = QHBoxLayout()
            name_lbl = QLabel(pname)
            name_lbl.setStyleSheet(
                "color:#e6edf3;font-size:12px;font-weight:600;"
            )
            req_lbl = QLabel("Required" if is_req else "Optional")
            req_lbl.setStyleSheet(
                f"color:{'#f85149' if is_req else '#8b949e'};"
                "font-size:10px;"
                f"{'border:1px solid #f85149;' if is_req else ''}"
                "border-radius:3px;padding:0 4px;"
            )
            type_lbl = QLabel(ptype)
            type_lbl.setStyleSheet(
                "color:#58a6ff;font-size:10px;"
                "border:1px solid #1f6feb;border-radius:3px;padding:0 4px;"
            )
            lbl_row.addWidget(name_lbl)
            lbl_row.addWidget(req_lbl)
            lbl_row.addWidget(type_lbl)
            lbl_row.addStretch()

            # Description
            desc_lbl = QLabel(pdesc)
            desc_lbl.setStyleSheet(
                "color:#8b949e;font-size:11px;margin-bottom:3px;"
            )
            desc_lbl.setWordWrap(True)

            # Input widget: QTextEdit for long text, QLineEdit otherwise
            if ptype == "boolean":
                widget = QComboBox()
                widget.addItems(["false", "true"])
                widget.setStyleSheet(
                    "QComboBox{background:#21262d;border:1px solid #30363d;"
                    "border-radius:5px;padding:5px 8px;color:#e6edf3;font-size:12px;}"
                    "QComboBox QAbstractItemView{background:#21262d;color:#e6edf3;"
                    "selection-background-color:#1f6feb;}"
                )
            elif pname in ("content", "code", "text") or ptype in ("object", "array"):
                widget = QTextEdit()
                widget.setFixedHeight(90)
                widget.setPlaceholderText(f"Enter {pname}…")
                widget.setStyleSheet(
                    "QTextEdit{background:#21262d;border:1px solid #30363d;"
                    "border-radius:5px;padding:6px;color:#e6edf3;font-size:12px;"
                    "font-family:Consolas,'Courier New',monospace;}"
                    "QTextEdit:focus{border-color:#58a6ff;}"
                )
            else:
                widget = QLineEdit()
                widget.setFixedHeight(34)
                widget.setPlaceholderText(f"Enter {pname}…")
                widget.setStyleSheet(
                    "QLineEdit{background:#21262d;border:1px solid #30363d;"
                    "border-radius:5px;padding:5px 8px;color:#e6edf3;font-size:12px;}"
                    "QLineEdit:focus{border-color:#58a6ff;}"
                )

            self._param_widgets[pname] = widget

            container = QWidget()
            container.setStyleSheet(
                "QWidget{background:#1c2128;border-radius:6px;padding:2px;}"
            )
            clay = QVBoxLayout(container)
            clay.setContentsMargins(10, 8, 10, 8)
            clay.setSpacing(3)
            clay.addLayout(lbl_row)
            clay.addWidget(desc_lbl)
            clay.addWidget(widget)

            self._params_layout.insertWidget(i, container)

    # ------ Tool Execution ------
    def _run_tool(self):
        if not self._current:
            return

        info = self._tools.get(self._current, {})
        risk = info.get("risk", "low")

        # High-risk operation double confirmation
        if risk == "high":
            box = QMessageBox(self)
            box.setWindowTitle("⚠️ High-Risk Operation Confirmation")
            box.setText(
                f"<b>Tool {self._current}</b> is a high-risk operation (Risk Level: 🔴 High)<br><br>"
                "This operation may modify/delete files or execute system commands, <b>irreversible</b>.<br>"
                "Confirm to execute directly?"
            )
            box.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
            )
            box.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return

        # Collect parameters
        params = {}
        for pname, widget in self._param_widgets.items():
            if isinstance(widget, QTextEdit):
                val = widget.toPlainText().strip()
            elif isinstance(widget, QComboBox):
                val = widget.currentText() == "true"
            else:
                val = widget.text().strip()
            if val != "" and val != False:
                params[pname] = val

        # Check required
        required = info.get("required", [])
        missing = [r for r in required if not params.get(r)]
        if missing:
            self._show_result(
                    {"ok": False, "error": f"Missing required params: {', '.join(missing)}"},
                success=False
            )
            return

        # Execute
        self._btn_run.setText("⏳ Executing…")
        self._btn_run.setEnabled(False)
        self._result_status.setText("Executing…")
        self._result_status.setStyleSheet("color:#d29922;font-size:12px;")
        QApplication.processEvents()

        try:
            from engine.tools import execute_tool
            result = execute_tool(self._current, params)
            success = result.get("ok", True)
            self._show_result(result, success=success)

            # Record history
            self._history.append({
                "tool":    self._current,
                "params":  params,
                "result":  result,
                "success": success
            })
            if len(self._history) > 50:
                self._history = self._history[-50:]

        except Exception as e:
            self._show_result({"ok": False, "error": str(e)}, success=False)
        finally:
            self._btn_run.setText("▶  Execute Tool")
            self._btn_run.setEnabled(True)

    def _show_result(self, result: dict, success: bool = True):
        """Show execution result"""
        # Status label
        if success:
            self._result_status.setText("✅ Execution succeeded")
            self._result_status.setStyleSheet("color:#3fb950;font-size:12px;")
        else:
            self._result_status.setText("❌ Execution failed")
            self._result_status.setStyleSheet("color:#f85149;font-size:12px;")

        # Raw JSON
        raw_json = json.dumps(result, ensure_ascii=False, indent=2)
        self._result_raw.setPlainText(raw_json)
        self._result_to_send = raw_json

        # Formatted view
        formatted = self._format_result(result)
        self._result_formatted.setHtml(formatted)

        # Switch to formatted tab
        self._result_tabs.setCurrentIndex(0)

    def _format_result(self, result: dict) -> str:
        """Convert result to readable HTML"""
        if not result.get("ok", True):
            err = result.get("error", "Unknown error")
            return (
                f"<div style='color:#f85149;font-size:13px;padding:8px;'>"
                f"<b>❌ Error</b><br><br>{err}</div>"
            )

        lines = ["<div style='padding:8px;font-size:13px;line-height:1.8;'>"]
        lines.append("<span style='color:#3fb950;font-weight:700;'>✅ Success</span><br><br>")

        for key, val in result.items():
            if key == "ok":
                continue
            key_html = f"<span style='color:#58a6ff;font-weight:600;'>{key}</span>"
            if isinstance(val, str) and len(val) > 200:
                # Long text display
                lines.append(
                    f"{key_html}:<br>"
                    f"<pre style='background:#161b22;padding:10px;border-radius:6px;"
                    f"white-space:pre-wrap;color:#e6edf3;font-size:12px;"
                    f"font-family:Consolas,monospace;max-height:300px;overflow-y:auto;'>"
                    f"{val[:3000]}{'...(truncated)' if len(val)>3000 else ''}</pre>"
                )
            elif isinstance(val, list):
                lines.append(f"{key_html}({len(val)} items):<br>")
                for item in val[:20]:
                    if isinstance(item, dict):
                        name = item.get("name", item.get("file", str(item)))
                        ftype = item.get("type","")
                        size = f"  {item.get('size','')}B" if item.get("size") else ""
                        icon = "📁" if ftype == "dir" else "📄"
                        lines.append(
                            f"&nbsp;&nbsp;{icon} "
                            f"<span style='color:#e6edf3;'>{name}</span>"
                            f"<span style='color:#8b949e;font-size:11px;'>"
                            f"  {item.get('modified','')}{size}</span><br>"
                        )
                    else:
                        lines.append(
                            f"&nbsp;&nbsp;<span style='color:#e6edf3;'>{item}</span><br>"
                        )
                if len(val) > 20:
                    lines.append(
                        f"<span style='color:#8b949e;font-size:11px;'>"
                        f"  … {len(val)} total</span><br>"
                    )
            elif isinstance(val, dict):
                lines.append(f"{key_html}:<br>")
                for k2, v2 in val.items():
                    lines.append(
                        f"&nbsp;&nbsp;<span style='color:#8b949e;'>{k2}</span>: "
                        f"<span style='color:#e6edf3;'>{v2}</span><br>"
                    )
            else:
                lines.append(f"{key_html}: <span style='color:#e6edf3;'>{val}</span><br>")

        lines.append("</div>")
        return "".join(lines)

    # ---- Helpers ----
    def _clear_params(self):
        for w in self._param_widgets.values():
            if isinstance(w, QTextEdit):
                w.clear()
            elif isinstance(w, QLineEdit):
                w.clear()
            elif isinstance(w, QComboBox):
                w.setCurrentIndex(0)

    def _copy_result(self):
        QApplication.clipboard().setText(self._result_to_send)
        orig = self._btn_copy.text()
        self._btn_copy.setText("✅ Copied")
        QTimer.singleShot(1500, lambda: self._btn_copy.setText(orig))

    def _send_result_to_chat(self):
        """Send result to chat page input box (routed through main window)"""
        self.parent_ref and self.parent_ref.chat_page.fill_input(
            f"Execution result of tool {self._current}:\n{self._result_to_send[:500]}"
        )

    def set_parent_ref(self, main_win):
        self.parent_ref = main_win

    def _show_history(self):
        if not self._history:
            QMessageBox.information(self, "Execution History", "No execution records yet")
            return
        lines = []
        for i, h in enumerate(reversed(self._history[-20:]), 1):
            ok = "✅" if h["success"] else "❌"
            lines.append(
                f"{ok} #{i}  {h['tool']}\n"
                f"   Params: {json.dumps(h['params'], ensure_ascii=False)[:80]}\n"
            )
        QMessageBox.information(self, f"Execution History ({len(self._history)} records)",
                                "\n".join(lines))

    def run_self_test(self):
        """One-click self-test: test all safe tools + check deps"""
        from engine.tools import self_test, check_all_deps
        self._result_status.setText("🔄 Self-testing…")
        self._result_status.setStyleSheet("color:#d29922;font-size:12px;")
        QApplication.processEvents()

        results  = self_test()
        dep_results = check_all_deps()

        # Collect install commands for missing deps
        all_missing_cmds = []
        for dep in dep_results.values():
            if not dep["ok"]:
                all_missing_cmds.extend(dep["install"])
        # Deduplicate
        all_missing_cmds = list(dict.fromkeys(all_missing_cmds))

        # Organize HTML report
        lines_fmt = ["<div style='padding:8px;font-size:13px;line-height:1.9;'>"]
        lines_fmt.append("<b style='color:#58a6ff;'>🔬 Tool Self-Test Report</b><br><br>")

        pass_n = sum(1 for r in results if r["status"] == "pass")
        fail_n = sum(1 for r in results if r["status"] == "fail")
        skip_n = sum(1 for r in results if r["status"] == "skipped")

        lines_fmt.append(
            f"<b>Safe tool tests:</b> ✅ {pass_n} passed  "
            f"{'❌ '+str(fail_n)+' failed  ' if fail_n else ''}"
            f"⏭ {skip_n} skipped<br><br>"
        )
        for r in results:
            icon  = {"pass":"✅","fail":"❌","skipped":"⏭","error":"💥"}.get(r["status"],"❓")
            color = {"pass":"#3fb950","fail":"#f85149",
                     "skipped":"#8b949e","error":"#f85149"}.get(r["status"],"#e6edf3")
            err_hint = ""
            if r["status"] in ("fail","error"):
                err_text = (r.get("result",{}).get("error","") or r.get("error",""))[:120]
                err_hint = f"<br><span style='color:#8b949e;font-size:11px;font-family:monospace;'>&nbsp;&nbsp;Reason: {err_text}</span>"
            lines_fmt.append(
                f"<span style='color:{color};'>{icon} {r['tool']}</span>"
                f"<span style='color:#8b949e;font-size:11px;'> {r.get('reason','')}</span>"
                f"{err_hint}<br>"
            )

        # Dependency check
        lines_fmt.append("<br><b style='color:#58a6ff;'>📦 Dependency Check</b><br>")
        for tool_name, dep in dep_results.items():
            if dep["ok"]:
                lines_fmt.append(
                    f"✅ <span style='color:#3fb950;'>{tool_name}</span>"
                    f"<span style='color:#8b949e;font-size:11px;'>  deps installed</span><br>"
                )
            else:
                cmds = " && ".join(dep["install"])
                lines_fmt.append(
                    f"⚠️ <span style='color:#d29922;'>{tool_name}</span>"
                    f"<span style='color:#8b949e;font-size:11px;'>"
                    f"  missing: {', '.join(dep['missing'])}</span><br>"
                    f"<span style='color:#8b949e;font-size:11px;font-family:monospace;'>"
                    f"  install: {cmds}</span><br>"
                )

        if all_missing_cmds:
            lines_fmt.append(
                "<br><span style='color:#d29922;'>⚠️ Missing dependencies, "
                "click top-right \"Install Missing Deps\" button for one-click install</span><br>"
            )

        lines_fmt.append("</div>")

        self._result_formatted.setHtml("".join(lines_fmt))
        self._result_raw.setPlainText(
            json.dumps({"tests": results, "deps": dep_results},
                       ensure_ascii=False, indent=2)
        )
        self._result_tabs.setCurrentIndex(0)

        # If missing deps, show install button
        if all_missing_cmds:
            self._btn_install = QPushButton(
                f"📦  Install Missing Deps ({len(all_missing_cmds)} commands)"
            )
            self._btn_install.setFixedHeight(36)
            self._btn_install.setStyleSheet(
                "QPushButton{background:rgba(210,153,34,.2);border:1px solid #d29922;"
                "border-radius:6px;color:#d29922;font-size:12px;font-weight:600;}"
                "QPushButton:hover{background:rgba(210,153,34,.4);}"
            )
            self._btn_install.clicked.connect(
                lambda: self._install_deps(all_missing_cmds)
            )
            # Insert above result tab
            parent_lay = self._result_tabs.parent().layout()
            if parent_lay:
                idx = parent_lay.indexOf(self._result_tabs)
                parent_lay.insertWidget(idx, self._btn_install)

        status = f"{'✅' if fail_n==0 else '⚠️'} Self-test complete ({pass_n} passed/{fail_n} failed/{skip_n} skipped)"
        self._result_status.setText(status)
        self._result_status.setStyleSheet(
            f"color:{'#3fb950' if fail_n==0 else '#d29922'};font-size:12px;"
        )

    def _install_deps(self, cmds: list):
        """Execute install commands in terminal"""
        import subprocess
        from engine.tools import execute_tool

        self._btn_install.setText("⏳ Installing…")
        self._btn_install.setEnabled(False)
        QApplication.processEvents()

        results = []
        for cmd in cmds:
            r = execute_tool("run_command", {"command": cmd, "timeout": 120})
            results.append({"cmd": cmd, "ok": r.get("ok"), "out": r.get("stdout","")[:200]})

        all_ok = all(r["ok"] for r in results)
        msg = "\n".join(
            f"{'✅' if r['ok'] else '❌'} {r['cmd']}\n   {r['out']}"
            for r in results
        )
        QMessageBox.information(
            self,
            "Install Complete" if all_ok else "Partial Install Failed",
            f"{'✅ All installed successfully!' if all_ok else '⚠️ Partial failure, please check manually'}\n\n{msg[:1000]}"
        )

        if all_ok:
            self._btn_install.setText("✅ Installed")
        else:
            self._btn_install.setText("⚠️ Partially failed, click to retry")
            self._btn_install.setEnabled(True)

        # Re-run self-test
        self.run_self_test()


# -- Coder Page --
class CoderWorker(QThread):
    """Run coding agent in background, push logs in real-time"""
    log     = pyqtSignal(str, str)    # (message, level)
    done    = pyqtSignal(object)      # CodingSession
    error   = pyqtSignal(str)

    def __init__(self, agent_llm, task: str, language: str, save_to: str,
                 context: str = "", model: str = ""):
        super().__init__()
        self.agent_llm = agent_llm
        self.task      = task
        self.language  = language
        self.save_to   = save_to
        self.context   = context
        self.model     = model

    def run(self):
        try:
            from engine.coder import CodingAgent
            coder = CodingAgent(
                llm_client=self.agent_llm,
                on_progress=lambda msg, level="info": self.log.emit(msg, level),
                model=self.model
            )
            session = coder.run(self.task, self.language, self.save_to,
                                context=self.context)
            self.done.emit(session)
        except Exception as e:
            import traceback
            self.error.emit(f"{e}\n{traceback.format_exc()}")


class CoderPage(QWidget):
    """
    Autonomous Coding Agent Interface
    Input task -> real-time log -> auto package save
    """

    LOG_COLORS = {
        "start":   "#58a6ff",
        "iter":    "#d29922",
        "write":   "#79c0ff",
        "fix":     "#ffa657",
        "run":     "#56d364",
        "stdout":  "#8b949e",
        "stderr":  "#f85149",
        "analyse": "#bc8cff",
        "pass":    "#3fb950",
        "warn":    "#d29922",
        "done":    "#3fb950",
        "error":   "#f85149",
        "info":    "#8b949e",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker  = None
        self._agent_llm = None
        self._setup_ui()

    def set_llm(self, llm_client):
        self._agent_llm = llm_client
        # Populate model combo based on provider
        self._populate_model_combo(llm_client)

    def _populate_model_combo(self, llm_client):
        """Populate available coding models based on LLM type"""
        self._model_combo.clear()
        provider = ""
        try:
            cls_name = llm_client.__class__.__name__
            if "DeepSeek" in cls_name:
                provider = "deepseek"
            elif "OpenAI" in cls_name:
                provider = "openai"
            elif "Claude" in cls_name:
                provider = "claude"
            elif "Qwen" in cls_name:
                provider = "qwen"
            elif "Ollama" in cls_name:
                provider = "ollama"
        except Exception:
            pass

        from engine.coder import CODER_MODELS
        models = CODER_MODELS.get(provider, [])

        if provider == "ollama" and hasattr(llm_client, "list_models"):
            ollama_models = llm_client.list_models()
            for m in ollama_models:
                self._model_combo.addItem(m, m)
        elif models:
            for model_id, model_desc in models:
                self._model_combo.addItem(f"{model_id}  {model_desc}", model_id)

        # Default to "strong reasoning" model (second option)
        if self._model_combo.count() >= 2:
            self._model_combo.setCurrentIndex(1)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # -- Top toolbar --
        toolbar = QWidget()
        toolbar.setFixedHeight(56)
        toolbar.setStyleSheet(
            "background:#161b22;border-bottom:1px solid #30363d;"
        )
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(16, 8, 16, 8)

        title = QLabel("💻  Coding Agent")
        title.setStyleSheet(
            "color:#58a6ff;font-size:14px;font-weight:700;"
        )

        self._lang_combo = QComboBox()
        self._lang_combo.addItems([
            "python", "javascript", "html", "bash", "bat",
            "java", "c", "cpp", "csharp", "go"
        ])
        self._lang_combo.setFixedWidth(120)
        self._lang_combo.setStyleSheet(
            "QComboBox{background:#21262d;border:1px solid #30363d;"
            "border-radius:5px;padding:4px 8px;color:#e6edf3;font-size:12px;}"
            "QComboBox QAbstractItemView{background:#21262d;color:#e6edf3;"
            "selection-background-color:#1f6feb;}"
        )

        save_lbl = QLabel("Save to:")
        save_lbl.setStyleSheet("color:#8b949e;font-size:12px;")
        self._save_path = QLineEdit()
        self._save_path.setPlaceholderText("Default save to Desktop")
        self._save_path.setFixedWidth(200)
        self._save_path.setStyleSheet(
            "QLineEdit{background:#21262d;border:1px solid #30363d;"
            "border-radius:5px;padding:4px 8px;color:#e6edf3;font-size:12px;}"
        )
        btn_browse = QPushButton("📁")
        btn_browse.setFixedSize(28, 28)
        btn_browse.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:5px;color:#8b949e;}"
            "QPushButton:hover{border-color:#58a6ff;color:#e6edf3;}"
        )
        btn_browse.clicked.connect(self._browse_save)

        tb_lay.addWidget(title)
        tb_lay.addStretch()
        tb_lay.addWidget(_make_label("Language:", "color:#8b949e;font-size:12px;"))
        tb_lay.addWidget(self._lang_combo)
        tb_lay.addSpacing(12)
        tb_lay.addWidget(_make_label("Model:", "color:#8b949e;font-size:12px;"))
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setFixedWidth(180)
        self._model_combo.setPlaceholderText("Use default model")
        self._model_combo.setStyleSheet(
            "QComboBox{background:#21262d;border:1px solid #30363d;"
            "border-radius:5px;padding:4px 8px;color:#e6edf3;font-size:12px;}"
            "QComboBox QAbstractItemView{background:#21262d;color:#e6edf3;"
            "selection-background-color:#1f6feb;}"
        )
        tb_lay.addWidget(self._model_combo)
        tb_lay.addSpacing(12)
        tb_lay.addWidget(save_lbl)
        tb_lay.addWidget(self._save_path)
        tb_lay.addWidget(btn_browse)

        # -- Task input area --
        task_widget = QWidget()
        task_widget.setStyleSheet("background:#0d1117;")
        task_lay = QVBoxLayout(task_widget)
        task_lay.setContentsMargins(16, 10, 16, 10)
        task_lay.setSpacing(8)

        task_header = QHBoxLayout()
        task_lbl = QLabel("📋  Task Description")
        task_lbl.setStyleSheet(
            "color:#e6edf3;font-size:13px;font-weight:600;"
        )

        self._btn_run = QPushButton("▶  Start Coding")
        self._btn_run.setFixedSize(120, 34)
        self._btn_run.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #1f6feb,stop:1 #7c3aed);"
            "border:none;border-radius:7px;color:white;"
            "font-size:13px;font-weight:700;}"
            "QPushButton:disabled{background:#21262d;color:#8b949e;}"
        )
        self._btn_run.clicked.connect(self._start)

        self._btn_stop = QPushButton("⏹  Stop")
        self._btn_stop.setFixedSize(80, 34)
        self._btn_stop.setEnabled(False)
        self._btn_stop.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:7px;color:#8b949e;font-size:12px;}"
            "QPushButton:enabled{border-color:#f85149;color:#f85149;}"
            "QPushButton:enabled:hover{background:rgba(248,81,73,.15);}"
        )
        self._btn_stop.clicked.connect(self._stop)

        task_header.addWidget(task_lbl)
        task_header.addStretch()
        task_header.addWidget(self._btn_stop)
        task_header.addWidget(self._btn_run)

        self._task_input = QLineEdit()
        self._task_input.setMinimumHeight(36)
        self._task_input.setPlaceholderText(
            "Example: Write a Snake game / Write a calculator / Write a batch file rename tool"
        )
        self._task_input.setStyleSheet(
            "QLineEdit{background:#21262d;border:1px solid #30363d;"
            "border-radius:8px;padding:8px 12px;color:#e6edf3;font-size:13px;}"
            "QLineEdit:focus{border-color:#58a6ff;}"
        )
        self._task_input.returnPressed.connect(self._start)

        task_lay.addLayout(task_header)
        task_lay.addWidget(self._task_input)

        # Reference code/context input
        ctx_header = QHBoxLayout()
        ctx_lbl = QLabel("📎  Reference Code / Table Data (optional)")
        ctx_lbl.setStyleSheet("color:#8b949e;font-size:11px;")
        self._ctx_toggle = QPushButton("Expand")
        self._ctx_toggle.setFixedSize(40, 20)
        self._ctx_toggle.setStyleSheet(
            "QPushButton{background:transparent;border:1px solid #30363d;"
            "border-radius:3px;color:#8b949e;font-size:10px;}"
            "QPushButton:hover{color:#58a6ff;border-color:#58a6ff;}"
        )
        self._ctx_toggle.clicked.connect(self._toggle_context)

        self._btn_upload_table = QPushButton("📤 Upload Table")
        self._btn_upload_table.setFixedSize(70, 20)
        self._btn_upload_table.setStyleSheet(
            "QPushButton{background:transparent;border:1px solid #30363d;"
            "border-radius:3px;color:#8b949e;font-size:10px;}"
            "QPushButton:hover{color:#f0883e;border-color:#f0883e;}"
        )
        self._btn_upload_table.clicked.connect(self._upload_table)

        ctx_header.addWidget(ctx_lbl)
        ctx_header.addStretch()
        ctx_header.addWidget(self._btn_upload_table)
        ctx_header.addWidget(self._ctx_toggle)

        self._context_input = QTextEdit()
        self._context_input.setPlaceholderText(
            "Paste reference code or file content, AI will use this as context…\n"
            "Examples: existing project code, API docs, data structures, etc."
        )
        self._context_input.setMaximumHeight(0)
        self._context_input.setStyleSheet(
            "QTextEdit{background:#21262d;border:1px solid #30363d;"
            "border-radius:8px;padding:8px 12px;color:#e6edf3;font-size:12px;"
            "font-family:Consolas,monospace;}"
            "QTextEdit:focus{border-color:#58a6ff;}"
        )
        self._ctx_visible = False

        task_lay.addLayout(ctx_header)
        task_lay.addWidget(self._context_input)

        # -- Main: Log + Code Preview --
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet(
            "QSplitter::handle{background:#30363d;width:1px;}"
        )

        # Left: execution log
        log_widget = QWidget()
        log_widget.setStyleSheet("background:#0d1117;")
        log_lay = QVBoxLayout(log_widget)
        log_lay.setContentsMargins(12, 10, 6, 12)
        log_lay.setSpacing(6)

        log_header = QHBoxLayout()
        log_header.addWidget(_make_label("Execution Log", "color:#e6edf3;font-size:13px;font-weight:600;"))
        log_header.addStretch()
        btn_clear_log = QPushButton("Clear")
        btn_clear_log.setFixedHeight(24)
        btn_clear_log.setStyleSheet(
            "QPushButton{background:transparent;border:none;"
            "color:#8b949e;font-size:11px;}"
            "QPushButton:hover{color:#e6edf3;}"
        )
        btn_clear_log.clicked.connect(lambda: self._log_view.clear())
        log_header.addWidget(btn_clear_log)

        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setStyleSheet(
            "QTextEdit{background:#0d1117;border:1px solid #30363d;"
            "border-radius:6px;color:#e6edf3;"
            "font-family:Consolas,'Courier New',monospace;font-size:12px;"
            "padding:8px;}"
        )

        # Status bar
        self._status_lbl = QLabel("Ready")
        self._status_lbl.setStyleSheet(
            "color:#8b949e;font-size:11px;padding-top:4px;"
        )

        log_lay.addLayout(log_header)
        log_lay.addWidget(self._log_view)
        log_lay.addWidget(self._status_lbl)

        # Right: code preview + actions
        code_widget = QWidget()
        code_widget.setStyleSheet("background:#0d1117;")
        code_lay = QVBoxLayout(code_widget)
        code_lay.setContentsMargins(6, 10, 12, 12)
        code_lay.setSpacing(6)

        code_header = QHBoxLayout()
        code_header.addWidget(_make_label("Code Preview", "color:#e6edf3;font-size:13px;font-weight:600;"))
        code_header.addStretch()

        self._file_combo = QComboBox()
        self._file_combo.setFixedWidth(160)
        self._file_combo.setStyleSheet(
            "QComboBox{background:#21262d;border:1px solid #30363d;"
            "border-radius:5px;padding:3px 6px;color:#e6edf3;font-size:11px;}"
            "QComboBox QAbstractItemView{background:#21262d;color:#e6edf3;"
            "selection-background-color:#1f6feb;}"
        )
        self._file_combo.currentTextChanged.connect(self._switch_file)
        code_header.addWidget(self._file_combo)

        self._code_view = QTextEdit()
        self._code_view.setReadOnly(True)
        self._code_view.setStyleSheet(
            "QTextEdit{background:#161b22;border:1px solid #30363d;"
            "border-radius:6px;color:#e6edf3;"
            "font-family:Consolas,'Courier New',monospace;font-size:12px;"
            "padding:10px;}"
        )

        # Action button row
        action_row = QHBoxLayout()
        self._btn_open_folder = QPushButton("📂  Open Output Directory")
        self._btn_open_folder.setEnabled(False)
        self._btn_open_folder.setFixedHeight(34)
        self._btn_open_folder.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:6px;color:#8b949e;font-size:12px;}"
            "QPushButton:enabled{color:#e6edf3;border-color:#30363d;}"
            "QPushButton:enabled:hover{border-color:#58a6ff;}"
        )
        self._btn_open_folder.clicked.connect(self._open_output)

        self._btn_run_preview = QPushButton("▶  Run Directly")
        self._btn_run_preview.setEnabled(False)
        self._btn_run_preview.setFixedHeight(34)
        self._btn_run_preview.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:6px;color:#8b949e;font-size:12px;}"
            "QPushButton:enabled{background:rgba(31,111,235,.2);"
            "color:#58a6ff;border-color:#1f6feb;}"
            "QPushButton:enabled:hover{background:rgba(31,111,235,.35);}"
        )
        self._btn_run_preview.clicked.connect(self._run_preview)

        self._iter_lbl = QLabel("")
        self._iter_lbl.setStyleSheet("color:#8b949e;font-size:11px;")

        action_row.addWidget(self._btn_open_folder)
        action_row.addWidget(self._btn_run_preview)
        action_row.addStretch()
        action_row.addWidget(self._iter_lbl)

        code_lay.addLayout(code_header)
        code_lay.addWidget(self._code_view)
        code_lay.addLayout(action_row)

        splitter.addWidget(log_widget)
        splitter.addWidget(code_widget)
        splitter.setSizes([480, 520])

        # Preset task quick buttons
        preset_bar = QWidget()
        preset_bar.setStyleSheet(
            "background:#161b22;border-top:1px solid #30363d;"
        )
        preset_lay = QHBoxLayout(preset_bar)
        preset_lay.setContentsMargins(16, 6, 16, 6)
        preset_lay.addWidget(_make_label("Quick Tasks:", "color:#8b949e;font-size:11px;"))
        presets = [
            ("🐍 Snake Game",   "python", "Write a Snake game with tkinter, with score display"),
            ("🧮 Calculator",   "python", "Write a GUI calculator supporting +,-,*,/ and parentheses"),
            ("📝 Notepad",      "python", "Write a simple notepad app that can open and save files"),
            ("⏰ Pomodoro",      "python", "Write a Pomodoro timer, 25min work 5min break"),
            ("🎮 Minesweeper",    "python", "Write a minesweeper game, 10x10 grid, 30 random mines"),
            ("📊 Table to Web", "html",   "Convert table data from reference code into a beautiful HTML dashboard with Chart.js, data tables, filtering, sorting, modern responsive layout"),
        ]
        for label, lang, task in presets:
            btn = QPushButton(label)
            btn.setFixedHeight(26)
            btn.setStyleSheet(
                "QPushButton{background:#21262d;border:1px solid #30363d;"
                "border-radius:12px;color:#8b949e;font-size:11px;padding:0 10px;}"
                "QPushButton:hover{border-color:#58a6ff;color:#e6edf3;}"
            )
            btn.clicked.connect(
                lambda checked, l=lang, t=task: self._set_preset(l, t)
            )
            preset_lay.addWidget(btn)
        preset_lay.addStretch()

        # PC tool quick buttons
        tool_bar = QWidget()
        tool_bar.setStyleSheet(
            "background:#161b22;border-top:1px solid #30363d;"
        )
        tool_lay = QHBoxLayout(tool_bar)
        tool_lay.setContentsMargins(16, 6, 16, 6)
        tool_lay.addWidget(_make_label("PC Tools:", "color:#8b949e;font-size:11px;"))
        tool_presets = [
            ("💻 PC Info",       "bat", "Write a Windows batch script using systeminfo, wmic to show CPU model, memory size, disk usage, OS version"),
            ("🌐 Network Test",  "bat", "Write a Windows batch script: ipconfig for IP, ping to test connectivity, netstat for connections"),
            ("🧹 Clean Temp",    "bat", "Write a Windows batch script: show temp folder size, clean %%TEMP%% on keypress"),
            ("📋 Process Mgr",   "bat", "Write a Windows batch script: tasklist top 20 by memory, support killing by name"),
            ("📁 Batch Rename",  "bat", "Write a Windows batch script: batch rename files with prefix, numbering, extension change"),
            ("🔒 File Encrypt",  "python", "Write a file encryption tool with AES, encrypt/decrypt with password"),
        ]
        for label, lang, task in tool_presets:
            btn = QPushButton(label)
            btn.setFixedHeight(26)
            btn.setStyleSheet(
                "QPushButton{background:#21262d;border:1px solid #30363d;"
                "border-radius:12px;color:#8b949e;font-size:11px;padding:0 10px;}"
                "QPushButton:hover{border-color:#f0883e;color:#e6edf3;}"
            )
            btn.clicked.connect(
                lambda checked, l=lang, t=task: self._set_preset(l, t)
            )
            tool_lay.addWidget(btn)
        tool_lay.addStretch()

        # Assemble
        layout.addWidget(toolbar)
        layout.addWidget(task_widget)
        layout.addWidget(splitter, stretch=1)
        layout.addWidget(preset_bar)
        layout.addWidget(tool_bar)

        # Internal state
        self._current_session = None
        self._output_path = ""
        self._current_files = {}

    # ---- Methods ----
    def _toggle_context(self):
        self._ctx_visible = not self._ctx_visible
        if self._ctx_visible:
            self._context_input.setMaximumHeight(150)
            self._ctx_toggle.setText("Collapse")
        else:
            self._context_input.setMaximumHeight(0)
            self._ctx_toggle.setText("Expand")

    def _upload_table(self):
        """Upload table file, parse and fill into reference code area"""
        from PyQt6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Table File",
            "", "Table Files (*.csv *.xlsx *.xls *.tsv);;All Files (*)"
        )
        if not path:
            return

        try:
            from engine.coder import parse_table_file
            result = parse_table_file(path)
            if not result.get("ok"):
                self._log_msg(f"❌ Table parse failed: {result.get('error', 'Unknown error')}", "error")
                return

            headers = result["headers"]
            col_types = result["col_types"]
            total = result["total_rows"]

            # Auto-expand reference code area
            if not self._ctx_visible:
                self._toggle_context()

            # Fill markdown table
            self._context_input.setPlainText(result["context_text"])

            self._log_msg(
                f"✅ Table loaded successfully: {Path(path).name}\n"
                f"   {len(headers)} cols × {total} rows | "
                + " | ".join(f"{h}({t})" for h, t in zip(headers, col_types)),
                "info"
            )

        except Exception as e:
            self._log_msg(f"❌ Table load failed: {e}", "error")

    def _set_preset(self, lang: str, task: str):
        idx = self._lang_combo.findText(lang)
        if idx >= 0:
            self._lang_combo.setCurrentIndex(idx)
        self._task_input.setText(task)
        self._task_input.setFocus()

    def _browse_save(self):
        from PyQt6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(self, "Select Save Directory")
        if d:
            self._save_path.setText(d)

    def _start(self):
        task = self._task_input.text().strip()
        if not task:
            self._task_input.setFocus()
            return
        if not self._agent_llm:
            self._log_msg("❌ LLM not initialized, please configure API Key or Ollama first", "error")
            return
        if self._worker and self._worker.isRunning():
            return

        lang    = self._lang_combo.currentText()
        save_to = self._save_path.text().strip() or str(_get_desktop())

        self._log_view.clear()
        self._code_view.clear()
        self._file_combo.clear()
        self._current_files = {}
        self._btn_run.setEnabled(False)
        self._btn_stop.setEnabled(True)
        self._btn_open_folder.setEnabled(False)
        self._btn_run_preview.setEnabled(False)
        self._iter_lbl.setText("")
        self._status_lbl.setText("🔄 Running…")

        self._worker = CoderWorker(
            self._agent_llm, task, lang, save_to,
            context=self._context_input.toPlainText().strip(),
            model=self._model_combo.currentData() or self._model_combo.currentText().strip()
        )
        self._worker.log.connect(self._log_msg)
        self._worker.done.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _stop(self):
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._log_msg("⏹  Stopped", "warn")
            self._reset_buttons()

    def _log_msg(self, msg: str, level: str = "info"):
        color = self.LOG_COLORS.get(level, "#e6edf3")
        timestamp = datetime.now().strftime("%H:%M:%S")

        # Special handling for code blocks
        if level in ("stdout", "stderr") and "\n" in msg:
            bg = "#1c1c1c" if level == "stderr" else "#0d1117"
            html = (
                f"<div style='margin:2px 0;'>"
                f"<span style='color:#8b949e;font-size:10px;'>{timestamp}</span> "
                f"<pre style='background:{bg};color:{color};padding:6px 8px;"
                f"border-radius:4px;margin:2px 0;font-size:11px;"
                f"white-space:pre-wrap;'>{msg}</pre></div>"
            )
        else:
            html = (
                f"<div style='margin:1px 0;'>"
                f"<span style='color:#8b949e;font-size:10px;'>{timestamp}</span> "
                f"<span style='color:{color};font-size:12px;'>{msg}</span></div>"
            )

        self._log_view.append(html)
        self._log_view.verticalScrollBar().setValue(
            self._log_view.verticalScrollBar().maximum()
        )

    def _on_done(self, session):
        self._current_session = session
        self._output_path = session.output_dir

        iters = len(session.iterations)
        status_text = (
            f"✅ Complete! {iters} iterations"
            if session.status == "passed"
            else f"⚠️ Max iterations reached ({iters} rounds), using last version"
        )
        self._status_lbl.setText(status_text)
        self._iter_lbl.setText(f"{iters} iterations")

        # Fill code preview
        self._current_files = {
            k: v for k, v in session.final_code.items()
            if not k.startswith("__")
        }
        self._file_combo.clear()
        for fname in self._current_files:
            self._file_combo.addItem(fname)
        if self._current_files:
            first = list(self._current_files.keys())[0]
            self._code_view.setPlainText(self._current_files[first])

        self._btn_open_folder.setEnabled(bool(self._output_path))
        self._btn_run_preview.setEnabled(bool(self._current_files))
        self._reset_buttons()

        # Success hint
        if session.status == "passed" and self._output_path:
            self._log_msg(
                f"📦 Project packaged: {self._output_path}", "done"
            )

    def _on_error(self, err: str):
        self._log_msg(f"❌ Agent exception: {err[:200]}", "error")
        self._status_lbl.setText("❌ Error occurred")
        self._reset_buttons()

    def _reset_buttons(self):
        self._btn_run.setEnabled(True)
        self._btn_stop.setEnabled(False)

    def _switch_file(self, fname: str):
        if fname and fname in self._current_files:
            self._code_view.setPlainText(self._current_files[fname])

    def _open_output(self):
        if not self._output_path:
            return
        p = Path(self._output_path)
        target = str(p.parent) if p.is_file() else str(p)
        import subprocess, sys
        if sys.platform == "win32":
            subprocess.Popen(["explorer", target])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])

    def _run_preview(self):
        """Run current code directly (no packaging, quick preview)"""
        if not self._current_files:
            return
        import tempfile, subprocess
        tmp = tempfile.mkdtemp(prefix="agi_preview_")
        for fname, content in self._current_files.items():
            (Path(tmp) / fname).write_text(content, encoding="utf-8")
        main_file = list(self._current_files.keys())[0]
        lang = self._lang_combo.currentText()

        self._log_msg(f"▶️  Running {main_file} directly…", "run")
        if lang == "python":
            subprocess.Popen(["python", main_file], cwd=tmp)
        elif lang == "javascript":
            subprocess.Popen(["node", main_file], cwd=tmp)
        elif lang == "html":
            import webbrowser
            webbrowser.open(str(Path(tmp) / main_file))


# ---- Face Recognition Page ----
class FaceWorker(QThread):
    """Run face operations in background (register/identify) to avoid UI freeze"""
    result = pyqtSignal(dict)
    error  = pyqtSignal(str)

    def __init__(self, task: str, db_file: str, image_data=None,
                 user_id: str = "", label: str = ""):
        super().__init__()
        self.task       = task        # "register" | "identify" | "capture"
        self.db_file    = db_file
        self.image_data = image_data  # numpy RGB array
        self.user_id    = user_id
        self.label      = label

    def run(self):
        try:
            from engine.face_recognition_engine import FaceDatabase, CameraThread
            db = FaceDatabase(self.db_file)

            if self.task == "capture":
                cam = CameraThread()
                frame = cam.get_frame_rgb()
                if frame is None:
                    self.error.emit("Cannot open camera. Please check: 1) Camera not in use by other apps 2) Camera permission granted 3) Restart app and retry")
                    return
                self.result.emit({"ok": True, "frame": frame})

            elif self.task == "register":
                if self.image_data is None:
                    self.error.emit("No image data")
                    return
                res = db.register(self.user_id, self.image_data,
                                  label=self.label)
                self.result.emit(res)

            elif self.task == "identify":
                if self.image_data is None:
                    self.error.emit("No image data")
                    return
                res = db.identify(self.image_data)
                self.result.emit(res)

            elif self.task == "list":
                users = db.list_users()
                self.result.emit({"ok": True, "users": users})

        except Exception as e:
            import traceback
            self.error.emit(f"{e}\n{traceback.format_exc()[:300]}")


class UserProfilePage(QWidget):
    """
    User Profile Page
    - Show confirmed / emerging personality traits with confidence
    - Anomaly behavior records
    - Refresh button (profile auto-updates in background during chat)
    """

    def __init__(self, db_file: str, auth_ref=None, parent=None):
        super().__init__(parent)
        self.db_file = db_file
        self._auth_ref = auth_ref   # callable, returns AuthManager or None
        self._mgr    = None   # UserProfileManager, lazy init
        self._setup_ui()

    def _get_mgr(self):
        if self._mgr is None:
            from engine.user_profile import UserProfileManager
            self._mgr = UserProfileManager(self.db_file)
        # Dynamic sync user_id with current auth state (Agent writes with same logic)
        if self._auth_ref:
            auth = self._auth_ref()
            if auth and auth.is_verified():
                self._mgr.user_id = auth.user_id
            else:
                self._mgr.user_id = "default"
        return self._mgr

    # ── UI ─────────────────────────────────────
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top bar
        header = QWidget()
        header.setFixedHeight(48)
        header.setStyleSheet("background:#161b22;border-bottom:1px solid #30363d;")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(16, 0, 16, 0)
        title = QLabel("👤  User Profile")
        title.setStyleSheet("color:#e6edf3;font-size:15px;font-weight:700;")
        self._stats_lbl = QLabel("")
        self._stats_lbl.setStyleSheet("color:#8b949e;font-size:12px;")
        btn_refresh = QPushButton("🔄  Refresh")
        btn_refresh.setFixedHeight(30)
        btn_refresh.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:6px;color:#c9d1d9;font-size:12px;padding:0 14px;}"
            "QPushButton:hover{border-color:#58a6ff;color:#58a6ff;}"
        )
        btn_refresh.clicked.connect(self.load)
        h_lay.addWidget(title)
        h_lay.addStretch()
        h_lay.addWidget(self._stats_lbl)
        h_lay.addSpacing(12)
        h_lay.addWidget(btn_refresh)
        layout.addWidget(header)

        # Main: left trait list + right details
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("QSplitter::handle{background:#30363d;width:1px;}")

        # -- Left: category nav --
        left = QWidget()
        left.setFixedWidth(180)
        left.setStyleSheet("background:#161b22;border-right:1px solid #30363d;")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 8, 0, 0)
        ll.setSpacing(0)
        cat_label = QLabel("  Categories")
        cat_label.setStyleSheet(
            "color:#8b949e;font-size:11px;font-weight:600;"
            "text-transform:uppercase;letter-spacing:1px;padding:4px 0;"
        )
        ll.addWidget(cat_label)
        self._cat_list = QListWidget()
        self._cat_list.setStyleSheet(
            "QListWidget{background:transparent;border:none;outline:none;}"
            "QListWidget::item{color:#c9d1d9;padding:8px 16px;font-size:13px;"
            "border-radius:6px;margin:1px 4px;}"
            "QListWidget::item:selected{background:#21262d;color:#58a6ff;}"
            "QListWidget::item:hover{background:#21262d;}"
        )
        self._cat_list.currentRowChanged.connect(self._on_cat_changed)
        ll.addWidget(self._cat_list)
        ll.addStretch()
        splitter.addWidget(left)

        # -- Right: trait cards area --
        right = QWidget()
        right.setStyleSheet("background:#0d1117;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 16, 20, 16)
        rl.setSpacing(12)

        # Confirmed zone
        confirmed_title = QLabel("✅  Confirmed Traits")
        confirmed_title.setStyleSheet(
            "color:#3fb950;font-size:13px;font-weight:700;"
        )
        rl.addWidget(confirmed_title)

        self._confirmed_area = QWidget()
        self._confirmed_layout = QVBoxLayout(self._confirmed_area)
        self._confirmed_layout.setContentsMargins(0, 0, 0, 0)
        self._confirmed_layout.setSpacing(6)
        rl.addWidget(self._confirmed_area)

        # Emerging zone
        emerging_title = QLabel("🔍  Emerging (Pending)")
        emerging_title.setStyleSheet(
            "color:#d29922;font-size:13px;font-weight:700;margin-top:8px;"
        )
        rl.addWidget(emerging_title)

        self._emerging_area = QWidget()
        self._emerging_layout = QVBoxLayout(self._emerging_area)
        self._emerging_layout.setContentsMargins(0, 0, 0, 0)
        self._emerging_layout.setSpacing(6)
        rl.addWidget(self._emerging_area)

        # Anomaly zone
        anomaly_title = QLabel("⚠️  Recent Anomalies")
        anomaly_title.setStyleSheet(
            "color:#f85149;font-size:13px;font-weight:700;margin-top:8px;"
        )
        rl.addWidget(anomaly_title)

        self._anomaly_area = QWidget()
        self._anomaly_layout = QVBoxLayout(self._anomaly_area)
        self._anomaly_layout.setContentsMargins(0, 0, 0, 0)
        self._anomaly_layout.setSpacing(6)
        rl.addWidget(self._anomaly_area)

        rl.addStretch()

        scroll = QScrollArea()
        scroll.setWidget(right)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea{border:none;background:#0d1117;}"
            "QScrollBar:vertical{background:#161b22;width:8px;border-radius:4px;}"
            "QScrollBar::handle:vertical{background:#30363d;border-radius:4px;}"
        )
        splitter.addWidget(scroll)
        splitter.setSizes([180, 600])

        layout.addWidget(splitter)
        self._setup_guest_section(layout)

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _make_trait_card(self, trait, confirmed: bool) -> QWidget:
        """Create single trait card (with progress bar)"""

        card = QWidget()
        card.setStyleSheet(
            "QWidget{background:#161b22;border:1px solid #30363d;"
            "border-radius:8px;padding:2px;}"
        )
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(6)

        # Top row: name + observation count
        top = QHBoxLayout()
        name_lbl = QLabel(trait.name)
        name_lbl.setStyleSheet(
            "color:#e6edf3;font-size:13px;font-weight:600;"
            "background:transparent;border:none;"
        )
        cat_badge = QLabel(trait.category)
        cat_badge.setStyleSheet(
            "color:#8b949e;font-size:11px;background:#21262d;"
            "border:1px solid #30363d;border-radius:4px;padding:1px 6px;"
        )
        count_lbl = QLabel(f"Observed {trait.evidence_count} times")
        count_lbl.setStyleSheet(
            "color:#8b949e;font-size:11px;background:transparent;border:none;"
        )
        top.addWidget(name_lbl)
        top.addWidget(cat_badge)
        top.addStretch()
        top.addWidget(count_lbl)
        cl.addLayout(top)

        # Confidence progress bar
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setValue(int(trait.confidence * 100))
        bar.setFixedHeight(6)
        bar.setTextVisible(False)
        color = "#3fb950" if confirmed else "#d29922"
        bar.setStyleSheet(
            f"QProgressBar{{background:#21262d;border-radius:3px;border:none;}}"
            f"QProgressBar::chunk{{background:{color};border-radius:3px;}}"
        )
        cl.addWidget(bar)

        # Confidence value + last seen time
        bottom = QHBoxLayout()
        conf_lbl = QLabel(f"Confidence {trait.confidence:.0%}")
        conf_lbl.setStyleSheet(
            f"color:{color};font-size:11px;background:transparent;border:none;"
        )
        date_str = trait.last_seen[:10] if trait.last_seen else ""
        date_lbl = QLabel(f"Last: {date_str}")
        date_lbl.setStyleSheet(
            "color:#8b949e;font-size:11px;background:transparent;border:none;"
        )
        bottom.addWidget(conf_lbl)
        bottom.addStretch()
        bottom.addWidget(date_lbl)
        cl.addLayout(bottom)

        # Examples (if any)
        if trait.examples:
            ex_lbl = QLabel(f"「{trait.examples[-1][:60]}」")
            ex_lbl.setStyleSheet(
                "color:#8b949e;font-size:11px;font-style:italic;"
                "background:transparent;border:none;"
            )
            ex_lbl.setWordWrap(True)
            cl.addWidget(ex_lbl)

        return card

    def _make_anomaly_card(self, anomaly) -> QWidget:
        card = QWidget()
        card.setStyleSheet(
            "QWidget{background:rgba(248,81,73,.08);border:1px solid rgba(248,81,73,.3);"
            "border-radius:8px;}"
        )
        cl = QVBoxLayout(card)
        cl.setContentsMargins(12, 10, 12, 10)
        cl.setSpacing(4)
        desc = QLabel(anomaly.description)
        desc.setStyleSheet(
            "color:#f85149;font-size:13px;font-weight:600;"
            "background:transparent;border:none;"
        )
        desc.setWordWrap(True)
        normal = QLabel(f"Normal pattern: {anomaly.normal_pattern}")
        normal.setStyleSheet(
            "color:#8b949e;font-size:11px;background:transparent;border:none;"
        )
        normal.setWordWrap(True)
        time_lbl = QLabel(anomaly.timestamp[:16])
        time_lbl.setStyleSheet(
            "color:#6e7681;font-size:11px;background:transparent;border:none;"
        )
        cl.addWidget(desc)
        cl.addWidget(normal)
        cl.addWidget(time_lbl)
        return card

    # ---- Data Loading ----
    def load(self):
        try:
            mgr = self._get_mgr()
            traits   = mgr.get_traits()
            anomalies = mgr.get_recent_anomalies(limit=5)

            confirmed = [t for t in traits if t.evidence_count >= mgr.CONFIRMED_THRESHOLD]
            emerging  = [t for t in traits if t.evidence_count <  mgr.CONFIRMED_THRESHOLD]

            # Update stats
            self._stats_lbl.setText(
                f"Confirmed {len(confirmed)}  ·  Observing {len(emerging)}"
            )

            # Update category list
            cats = sorted(set(t.category for t in traits))
            self._cat_list.clear()
            self._cat_list.addItem("All")
            for c in cats:
                self._cat_list.addItem(c)

            # Confirmed zone
            self._clear_layout(self._confirmed_layout)
            if confirmed:
                for t in confirmed:
                    self._confirmed_layout.addWidget(self._make_trait_card(t, True))
            else:
                lbl = QLabel("No confirmed traits yet. They will accumulate as you chat with AGI.")
                lbl.setStyleSheet("color:#8b949e;font-size:12px;")
                self._confirmed_layout.addWidget(lbl)

            # Emerging zone
            self._clear_layout(self._emerging_layout)
            if emerging:
                for t in emerging:
                    self._emerging_layout.addWidget(self._make_trait_card(t, False))
            else:
                lbl = QLabel("No emerging observations yet.")
                lbl.setStyleSheet("color:#8b949e;font-size:12px;")
                self._emerging_layout.addWidget(lbl)

            # Anomaly zone
            self._clear_layout(self._anomaly_layout)
            if anomalies:
                for a in anomalies:
                    self._anomaly_layout.addWidget(self._make_anomaly_card(a))
            else:
                lbl = QLabel("No anomaly records.")
                lbl.setStyleSheet("color:#8b949e;font-size:12px;")
                self._anomaly_layout.addWidget(lbl)

        except Exception as e:
            pass  # Silently ignore when DB not yet created

    def _on_cat_changed(self, row):
        pass

    def _setup_guest_section(self, layout):
        """Guest record block (collapsible, at page bottom)"""
        self._guest_section = QWidget()
        self._guest_section.setStyleSheet("background:#0d1117;")
        gl = QVBoxLayout(self._guest_section)
        gl.setContentsMargins(20, 8, 20, 16)
        gl.setSpacing(8)

        guest_header = QHBoxLayout()
        self._guest_title = QLabel("🕵️  Guest Records  ▶")
        self._guest_title.setStyleSheet(
            "color:#d29922;font-size:13px;font-weight:700;"
        )
        self._guest_title.mousePressEvent = lambda e: self._toggle_guest_panel()
        btn_clear_guest = QPushButton("Clear Records")
        btn_clear_guest.setFixedHeight(24)
        btn_clear_guest.setStyleSheet(
            "QPushButton{background:transparent;border:none;"
            "color:#6e7681;font-size:11px;}"
            "QPushButton:hover{color:#f85149;}"
        )
        btn_clear_guest.clicked.connect(self._clear_guest_sessions)
        guest_header.addWidget(self._guest_title)
        guest_header.addStretch()
        guest_header.addWidget(btn_clear_guest)
        gl.addLayout(guest_header)

        self._guest_panel = QWidget()
        self._guest_panel.setVisible(False)
        gpl = QVBoxLayout(self._guest_panel)
        gpl.setContentsMargins(0, 0, 0, 0)
        gpl.setSpacing(6)

        self._guest_list = QTextEdit()
        self._guest_list.setReadOnly(True)
        self._guest_list.setFixedHeight(180)
        self._guest_list.setStyleSheet(
            "QTextEdit{background:#161b22;border:1px solid #30363d;"
            "border-radius:8px;color:#c9d1d9;font-size:11px;"
            "font-family:Consolas,monospace;padding:8px;}"
        )
        gpl.addWidget(self._guest_list)
        gl.addWidget(self._guest_panel)
        layout.addWidget(self._guest_section)

    def _toggle_guest_panel(self):
        visible = self._guest_panel.isVisible()
        self._guest_panel.setVisible(not visible)
        self._guest_title.setText(
            "🕵️  Guest Records  ▼" if not visible else "🕵️  Guest Records  ▶"
        )
        if not visible:
            self._load_guest_sessions()

    def _load_guest_sessions(self):
        try:
            from engine.auth import AuthManager
            auth     = AuthManager(self.db_file)
            sessions = auth.get_guest_sessions(limit=10)
            if not sessions:
                self._guest_list.setPlainText("No guest records")
                return
            lines = []
            for s in sessions:
                time_str = s["started_at"][:16]
                end_str  = s["ended_at"][:16] if s["ended_at"] else "Active"
                photo    = "📷 Photo" if s["has_photo"] else "No photo"
                lines.append(f"{'='*40}")
                lines.append(f"🕐 {time_str} ~ {end_str}  {photo}  {s['msg_count']} messages")
                for msg in s["messages"][:5]:
                    lines.append(f"  [{msg['time']}] User: {msg['user'][:50]}")
                if s["msg_count"] > 5:
                    lines.append(f"  ... {s['msg_count']-5} more")
            self._guest_list.setPlainText("\n".join(lines))
        except Exception as e:
            self._guest_list.setPlainText(f"Load failed: {e}")

    def _clear_guest_sessions(self):
        try:
            from engine.auth import AuthManager
            AuthManager(self.db_file).clear_guest_sessions()
            self._guest_list.setPlainText("Cleared")
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
class MemoryGraphPage(QWidget):
    """
    Memory Graph Visualization Page
    - Read memories + memory_edges from SQLite
    - Generate vis.js graph HTML, write to temp file
    - Preview SVG summary with QTextBrowser, provide "Open in Browser" button
    """

    def __init__(self, db_file: str, parent=None):
        super().__init__(parent)
        self.db_file  = db_file
        self._html_path = ""
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top bar
        header = QWidget()
        header.setFixedHeight(48)
        header.setStyleSheet("background:#161b22;border-bottom:1px solid #30363d;")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(16, 0, 16, 0)
        title = QLabel("🕸️  Memory Graph")
        title.setStyleSheet("color:#e6edf3;font-size:15px;font-weight:700;")
        self._stats_lbl = QLabel("")
        self._stats_lbl.setStyleSheet("color:#8b949e;font-size:12px;")

        btn_refresh = QPushButton("🔄  Refresh")
        btn_refresh.setFixedHeight(30)
        btn_refresh.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:6px;color:#c9d1d9;font-size:12px;padding:0 14px;}"
            "QPushButton:hover{border-color:#58a6ff;color:#58a6ff;}"
        )
        btn_refresh.clicked.connect(self.load)

        btn_open = QPushButton("🌐  Open in Browser")
        btn_open.setFixedHeight(30)
        btn_open.setStyleSheet(
            "QPushButton{background:rgba(31,111,235,.15);border:1px solid #1f6feb;"
            "border-radius:6px;color:#58a6ff;font-size:12px;padding:0 14px;}"
            "QPushButton:hover{background:rgba(31,111,235,.3);}"
        )
        btn_open.clicked.connect(self._open_in_browser)

        h_lay.addWidget(title)
        h_lay.addStretch()
        h_lay.addWidget(self._stats_lbl)
        h_lay.addSpacing(12)
        h_lay.addWidget(btn_refresh)
        h_lay.addSpacing(6)
        h_lay.addWidget(btn_open)
        layout.addWidget(header)

        # Info + node type legend
        legend_bar = QWidget()
        legend_bar.setFixedHeight(36)
        legend_bar.setStyleSheet("background:#161b22;border-bottom:1px solid #21262d;")
        leg_lay = QHBoxLayout(legend_bar)
        leg_lay.setContentsMargins(16, 0, 16, 0)
        leg_lay.setSpacing(16)
        for color, label in [
            ("#58a6ff", "Semantic"),
            ("#3fb950", "Emotion"),
            ("#d29922", "Temporal"),
            ("#bc8cff", "Spatial"),
            ("#f0883e", "Person"),
        ]:
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{color};font-size:14px;")
            lbl = QLabel(label)
            lbl.setStyleSheet("color:#8b949e;font-size:11px;")
            leg_lay.addWidget(dot)
            leg_lay.addWidget(lbl)
        leg_lay.addStretch()
        lbl_hint = QLabel("Click \"Open in Browser\" for interactive drag")
        lbl_hint.setStyleSheet("color:#6e7681;font-size:11px;font-style:italic;")
        leg_lay.addWidget(lbl_hint)
        layout.addWidget(legend_bar)

        # Preview area (QTextBrowser renders SVG static preview)
        self._preview = QTextBrowser()
        self._preview.setStyleSheet(
            "QTextBrowser{background:#0d1117;border:none;}"
        )
        self._preview.setOpenLinks(False)
        layout.addWidget(self._preview)

    # ---- Data & Graph Generation ----
    def load(self):
        try:
            from engine.db_guard import guarded_connect
            with guarded_connect(self.db_file) as conn:
                nodes_raw = conn.execute(
                    "SELECT id, content, modality, level, importance, emotion_json "
                    "FROM memories ORDER BY importance DESC, last_accessed DESC LIMIT 120"
                ).fetchall()
                edges_raw = conn.execute(
                    "SELECT source_id, target_id, assoc_type, strength "
                    "FROM memory_edges ORDER BY strength DESC LIMIT 300"
                ).fetchall()

            node_ids = {r[0] for r in nodes_raw}
            # Only keep edges with both ends in node set
            edges_raw = [e for e in edges_raw
                         if e[0] in node_ids and e[1] in node_ids]

            self._stats_lbl.setText(
                f"Nodes {len(nodes_raw)}  ·  Edges {len(edges_raw)}"
            )

            # Generate interactive HTML (vis.js CDN)
            html = self._build_vis_html(nodes_raw, edges_raw)

            # Write temp file
            import tempfile, os
            tmp = tempfile.NamedTemporaryFile(
                delete=False, suffix=".html",
                prefix="agi_memory_graph_", mode="w", encoding="utf-8"
            )
            tmp.write(html)
            tmp.close()
            self._html_path = tmp.name

            # Static SVG preview (simple bubble chart)
            svg = self._build_svg_preview(nodes_raw, edges_raw)
            self._preview.setHtml(
                f"<body style='background:#0d1117;margin:0;'>{svg}"
                f"<p style='color:#6e7681;font-size:11px;text-align:center;"
                f"font-family:monospace;'>Static preview, click top button to view interactive version in browser</p>"
                f"</body>"
            )

        except Exception as e:
            self._preview.setHtml(
                f"<body style='background:#0d1117;color:#f85149;padding:20px;"
                f"font-family:monospace;'>"
                f"<p>Load failed: {e}</p>"
                f"<p style='color:#8b949e;'>Please chat first, wait for memory system to accumulate data.</p>"
                f"</body>"
            )

    def _build_vis_html(self, nodes_raw, edges_raw) -> str:
        import json as _json

        MODALITY_COLOR = {
            "semantic":      "#58a6ff",
            "emotional":     "#3fb950",
            "temporal":      "#d29922",
            "spatial":       "#bc8cff",
            "person":        "#f0883e",
            "visual":        "#79c0ff",
            "auditory":      "#56d364",
            "autobio":       "#ffa657",
            "procedural":    "#d2a8ff",
        }
        ASSOC_COLOR = {
            "semantic":   "#58a6ff",
            "emotional":  "#3fb950",
            "temporal":   "#d29922",
            "spatial":    "#bc8cff",
            "person":     "#f0883e",
            "sensory":    "#79c0ff",
            "causal":     "#ff7b72",
        }

        vis_nodes = []
        for r in nodes_raw:
            nid, content, modality, level, importance, emotion_json = r
            color = MODALITY_COLOR.get(modality, "#8b949e")
            size  = max(10, min(40, int(importance * 40)))
            label = content[:30].replace('"', "'") if content else nid[:8]
            level_label = {"detail": "Detail", "outline": "Outline", "summary": "Summary"}.get(level, level)
            try:
                emo = _json.loads(emotion_json or "{}")
                emo_str = emo.get("primary", "")
            except Exception:
                emo_str = ""
            title = f"{label}\nModality:{modality}  Level:{level_label}  Importance:{importance:.2f}"
            if emo_str:
                title += f"\nEmotion:{emo_str}"
            vis_nodes.append({
                "id": nid, "label": label, "title": title,
                "color": {"background": color, "border": color,
                          "highlight": {"background": "#ffffff", "border": color}},
                "size": size, "font": {"color": "#e6edf3", "size": 11}
            })

        vis_edges = []
        for r in edges_raw:
            src, tgt, atype, strength = r
            color = ASSOC_COLOR.get(atype, "#30363d")
            vis_edges.append({
                "from": src, "to": tgt,
                "width": max(1, strength * 4),
                "color": {"color": color, "opacity": max(0.3, strength)},
                "title": f"{atype}  Strength:{strength:.2f}",
                "arrows": {"to": {"enabled": True, "scaleFactor": 0.5}}
            })

        nodes_json = _json.dumps(vis_nodes, ensure_ascii=False)
        edges_json = _json.dumps(vis_edges, ensure_ascii=False)

        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>AGI Memory Graph</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/vis/4.21.0/vis.min.js"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/vis/4.21.0/vis.min.css">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#0d1117; font-family: 'Segoe UI', sans-serif; }}
  #graph {{ width:100vw; height:100vh; }}
  #legend {{
    position:fixed; top:16px; right:16px;
    background:rgba(22,27,34,.95); border:1px solid #30363d;
    border-radius:10px; padding:14px 16px; z-index:100;
    min-width:160px;
  }}
  #legend h3 {{ color:#e6edf3; font-size:13px; margin-bottom:10px; }}
  .leg-item {{ display:flex; align-items:center; gap:8px; margin:5px 0; }}
  .leg-dot {{ width:12px; height:12px; border-radius:50%; flex-shrink:0; }}
  .leg-label {{ color:#c9d1d9; font-size:12px; }}
  #info {{
    position:fixed; bottom:16px; left:16px;
    background:rgba(22,27,34,.9); border:1px solid #30363d;
    border-radius:8px; padding:10px 14px; color:#8b949e; font-size:12px;
  }}
</style>
</head>
<body>
<div id="graph"></div>
<div id="legend">
  <h3>🕸️ Memory Graph</h3>
  <div class="leg-item"><div class="leg-dot" style="background:#58a6ff"></div><span class="leg-label">Semantic</span></div>
  <div class="leg-item"><div class="leg-dot" style="background:#3fb950"></div><span class="leg-label">Emotion</span></div>
  <div class="leg-item"><div class="leg-dot" style="background:#d29922"></div><span class="leg-label">Temporal</span></div>
  <div class="leg-item"><div class="leg-dot" style="background:#bc8cff"></div><span class="leg-label">Spatial</span></div>
  <div class="leg-item"><div class="leg-dot" style="background:#f0883e"></div><span class="leg-label">Person</span></div>
  <div class="leg-item"><div class="leg-dot" style="background:#ff7b72"></div><span class="leg-label">Causal</span></div>
  <hr style="border-color:#30363d;margin:8px 0;">
  <div style="color:#6e7681;font-size:11px;">Node size = importance<br>Edge width = strength<br>Drag to move, scroll to zoom</div>
</div>
<div id="info">Nodes: {len(nodes_raw)}  Edges: {len(edges_raw)}</div>
<script>
var nodes = new vis.DataSet({nodes_json});
var edges = new vis.DataSet({edges_json});
var container = document.getElementById('graph');
var options = {{
  nodes: {{ shape:'dot', borderWidth:2 }},
  edges: {{ smooth:{{ type:'continuous' }} }},
  physics: {{
    stabilization: {{ iterations: 150 }},
    barnesHut: {{ gravitationalConstant:-3000, springLength:120, damping:0.15 }}
  }},
  interaction: {{ tooltipDelay:100, hideEdgesOnDrag:true }},
  background: '#0d1117'
}};
var network = new vis.Network(container, {{nodes:nodes, edges:edges}}, options);
network.on('click', function(params) {{
  if(params.nodes.length > 0) {{
    var n = nodes.get(params.nodes[0]);
    document.getElementById('info').textContent = n.title.replace(/\\n/g,' | ');
  }}
}});
</script>
</body>
</html>"""

    def _build_svg_preview(self, nodes_raw, edges_raw) -> str:
        """Generate simple SVG static bubble preview (no browser needed)"""
        import math, json as _json, html as _html

        W, H = 800, 480
        n = len(nodes_raw)
        if n == 0:
            return (f'<svg width="{W}" height="200" xmlns="http://www.w3.org/2000/svg">'
                    f'<text x="50%" y="100" text-anchor="middle" fill="#8b949e" font-size="14">'
                    f'No memory data</text></svg>')

        MODALITY_COLOR = {
            "semantic":"#58a6ff","emotional":"#3fb950","temporal":"#d29922",
            "spatial":"#bc8cff","person":"#f0883e","visual":"#79c0ff",
            "auditory":"#56d364","autobio":"#ffa657","procedural":"#d2a8ff",
        }

        # Circular layout (max 60 node preview)
        preview_nodes = nodes_raw[:60]
        positions = {}
        cx, cy, radius = W // 2, H // 2, min(W, H) // 2 - 50
        for i, r in enumerate(preview_nodes):
            angle = 2 * math.pi * i / len(preview_nodes)
            imp = r[4] or 0.5
            r2 = radius * (0.5 + 0.5 * imp)
            x = int(cx + r2 * math.cos(angle))
            y = int(cy + r2 * math.sin(angle))
            positions[r[0]] = (x, y)

        svg_parts = [
            f'<svg width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" '
            f'style="background:#0d1117;border-radius:8px;">'
        ]

        # Draw edges (max 100)
        edge_set = {r[0] for r in preview_nodes}
        for r in edges_raw[:100]:
            src, tgt, atype, strength = r
            if src in positions and tgt in positions:
                x1, y1 = positions[src]
                x2, y2 = positions[tgt]
                opacity = max(0.1, min(0.6, strength * 0.6))
                svg_parts.append(
                    f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                    f'stroke="#30363d" stroke-width="1" opacity="{opacity:.2f}"/>'
                )

        # Draw nodes
        for r in preview_nodes:
            nid, content, modality, level, importance, _ = r
            x, y = positions[nid]
            size = max(6, min(20, int((importance or 0.5) * 20)))
            color = MODALITY_COLOR.get(modality, "#8b949e")
            label = _html.escape((content or "")[:12])
            svg_parts.append(
                f'<circle cx="{x}" cy="{y}" r="{size}" '
                f'fill="{color}" opacity="0.85"/>'
            )
            if size >= 10:
                svg_parts.append(
                    f'<text x="{x}" y="{y+size+12}" text-anchor="middle" '
                    f'fill="#8b949e" font-size="9" font-family="monospace">{label}</text>'
                )

        svg_parts.append('</svg>')
        return "".join(svg_parts)

    def _open_in_browser(self):
        if not self._html_path:
            self.load()
        if self._html_path:
            import webbrowser
            webbrowser.open(f"file://{self._html_path}")


class FaceRecognitionPage(QWidget):
    """
    Face Recognition Management Page
    - View engine status and installation guide
    - Register user face (camera capture / import image)
    - Real-time recognition test
    - Manage registered users
    """

    def __init__(self, db_file: str, auth_ref=None, parent=None):
        super().__init__(parent)
        self.db_file     = db_file
        self._auth_ref   = auth_ref   # callable, returns AuthManager or None
        self._worker     = None
        self._current_frame = None   # Current preview frame (numpy RGB)
        self._setup_ui()
        self._check_engine()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # -- Top engine status bar --
        self._engine_bar = QWidget()
        self._engine_bar.setFixedHeight(44)
        self._engine_bar.setStyleSheet(
            "background:#161b22;border-bottom:1px solid #30363d;"
        )
        eb_lay = QHBoxLayout(self._engine_bar)
        eb_lay.setContentsMargins(16, 0, 16, 0)

        self._engine_lbl = QLabel("👁️  Face Engine: Detecting…")
        self._engine_lbl.setStyleSheet(
            "color:#8b949e;font-size:13px;font-weight:600;"
        )
        self._install_btn = QPushButton("📦  Install InsightFace")
        self._install_btn.setFixedHeight(30)
        self._install_btn.setVisible(False)
        self._install_btn.setStyleSheet(
            "QPushButton{background:rgba(210,153,34,.2);border:1px solid #d29922;"
            "border-radius:6px;color:#d29922;font-size:12px;padding:0 12px;}"
            "QPushButton:hover{background:rgba(210,153,34,.4);}"
        )
        self._install_btn.clicked.connect(self._install_engine)

        eb_lay.addWidget(self._engine_lbl)
        eb_lay.addStretch()
        eb_lay.addWidget(self._install_btn)

        # -- Main three columns --
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setStyleSheet(
            "QSplitter::handle{background:#30363d;width:1px;}"
        )

        # Left: user list
        left = QWidget()
        left.setFixedWidth(220)
        left.setStyleSheet("background:#161b22;border-right:1px solid #30363d;")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)

        lhdr = QLabel("  👤  Registered Users")
        lhdr.setFixedHeight(40)
        lhdr.setStyleSheet(
            "background:#1c2128;color:#58a6ff;font-weight:700;"
            "font-size:13px;border-bottom:1px solid #30363d;"
        )

        self._user_list = QListWidget()
        self._user_list.setStyleSheet(
            "QListWidget{background:#161b22;border:none;outline:none;}"
            "QListWidget::item{padding:10px 14px;border-bottom:1px solid #21262d;"
            "color:#e6edf3;font-size:12px;}"
            "QListWidget::item:selected{background:#1f3a5c;color:#58a6ff;}"
        )

        btn_del = QPushButton("🗑  Delete Selected User")
        btn_del.setFixedHeight(34)
        btn_del.setStyleSheet(
            "QPushButton{background:#1c2128;border:none;color:#8b949e;"
            "font-size:12px;border-top:1px solid #30363d;}"
            "QPushButton:hover{color:#f85149;}"
        )
        btn_del.clicked.connect(self._delete_user)

        ll.addWidget(lhdr)
        ll.addWidget(self._user_list, stretch=1)
        ll.addWidget(btn_del)

        # Center: camera preview + register
        mid = QWidget()
        mid.setStyleSheet("background:#0d1117;")
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(16, 14, 16, 14)
        ml.setSpacing(10)

        # Preview area
        self._preview = QLabel("Camera Preview")
        self._preview.setFixedHeight(240)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet(
            "QLabel{background:#161b22;border:2px dashed #30363d;"
            "border-radius:10px;color:#8b949e;font-size:13px;}"
        )

        # Action button row
        cam_row = QHBoxLayout()
        self._btn_capture = QPushButton("📷  Capture")
        self._btn_capture.setFixedHeight(36)
        self._btn_capture.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #1f6feb,stop:1 #7c3aed);"
            "border:none;border-radius:7px;color:white;"
            "font-size:13px;font-weight:700;}"
            "QPushButton:hover{opacity:.9;}"
        )
        self._btn_capture.clicked.connect(self._capture)

        self._btn_import = QPushButton("🖼  Import Image")
        self._btn_import.setFixedHeight(36)
        self._btn_import.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:7px;color:#e6edf3;font-size:12px;}"
            "QPushButton:hover{border-color:#58a6ff;}"
        )
        self._btn_import.clicked.connect(self._import_image)

        cam_row.addWidget(self._btn_capture)
        cam_row.addWidget(self._btn_import)

        # Registration info
        reg_box = QGroupBox("Face Registration")
        reg_box.setStyleSheet(
            "QGroupBox{border:1px solid #30363d;border-radius:8px;"
            "margin-top:8px;color:#58a6ff;font-weight:600;font-size:12px;}"
            "QGroupBox::title{subcontrol-origin:margin;left:10px;}"
        )
        reg_lay = QGridLayout(reg_box)

        # Existing account selection (for re-registering face)
        reg_lay.addWidget(QLabel("Existing Account:"), 0, 0)
        self._existing_user_combo = QComboBox()
        self._existing_user_combo.setStyleSheet(
            "QComboBox{background:#161b22;border:1px solid #30363d;"
            "border-radius:6px;color:#e6edf3;padding:5px 8px;font-size:12px;}"
            "QComboBox QAbstractItemView{background:#161b22;color:#e6edf3;}"
        )
        self._existing_user_combo.addItem("-- New User --", "")
        self._existing_user_combo.currentIndexChanged.connect(self._on_existing_user_changed)
        reg_lay.addWidget(self._existing_user_combo, 0, 1)

        reg_lay.addWidget(QLabel("User ID:"), 1, 0)
        self._reg_id = QLineEdit()
        self._reg_id.setPlaceholderText("Unique ID, e.g. user_001")
        reg_lay.addWidget(self._reg_id, 1, 1)
        reg_lay.addWidget(QLabel("Display Name:"), 2, 0)
        self._reg_name = QLineEdit()
        self._reg_name.setPlaceholderText("Nickname, e.g. John")
        reg_lay.addWidget(self._reg_name, 2, 1)

        self._btn_register = QPushButton("✅  Register Face")
        self._btn_register.setFixedHeight(36)
        self._btn_register.setEnabled(False)
        self._btn_register.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:7px;color:#8b949e;font-size:12px;}"
            "QPushButton:enabled{background:rgba(63,185,80,.2);"
            "border-color:#3fb950;color:#3fb950;}"
            "QPushButton:enabled:hover{background:rgba(63,185,80,.35);}"
        )
        self._btn_register.clicked.connect(self._register)

        reg_lay.addWidget(self._btn_register, 3, 0, 1, 2)
        ml.addWidget(self._preview)
        ml.addLayout(cam_row)
        ml.addWidget(reg_box)
        ml.addStretch()

        # Right: recognition test
        right = QWidget()
        right.setStyleSheet("background:#0d1117;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(16, 14, 16, 14)
        rl.setSpacing(10)

        rl.addWidget(QLabel(
            "🔍  Recognition Test",
            styleSheet="color:#e6edf3;font-size:13px;font-weight:700;"
        ) if False else self._make_lbl("🔍  Recognition Test",
                                        "color:#e6edf3;font-size:13px;font-weight:700;"))

        self._btn_identify = QPushButton("▶  Capture & Identify")
        self._btn_identify.setFixedHeight(40)
        self._btn_identify.setStyleSheet(
            "QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #1f6feb,stop:1 #7c3aed);"
            "border:none;border-radius:8px;color:white;"
            "font-size:13px;font-weight:700;}"
        )
        self._btn_identify.clicked.connect(self._identify)

        self._result_box = QTextEdit()
        self._result_box.setReadOnly(True)
        self._result_box.setStyleSheet(
            "QTextEdit{background:#161b22;border:1px solid #30363d;"
            "border-radius:8px;color:#e6edf3;font-size:13px;padding:10px;}"
        )

        # Installation guide
        install_guide = QTextEdit()
        install_guide.setReadOnly(True)
        install_guide.setFixedHeight(160)
        install_guide.setStyleSheet(
            "QTextEdit{background:#1c2128;border:1px solid #30363d;"
            "border-radius:8px;color:#8b949e;font-size:11px;"
            "font-family:Consolas,monospace;padding:10px;}"
        )
        install_guide.setPlainText(
            "# Install face recognition engine (choose one)\n\n"
            "# Recommended: InsightFace (highest accuracy, pip install)\n"
            "pip install insightface onnxruntime opencv-python\n\n"
            "# Alternative: face_recognition (Windows needs C++ env)\n"
            "# 1. Install CMake: https://cmake.org/download\n"
            "# 2. Install Visual Studio C++ tools\n"
            "# 3. pip install dlib face_recognition\n\n"
            "# Lightweight: OpenCV (detection only, no identity)\n"
            "pip install opencv-python"
        )

        rl.addWidget(self._btn_identify)
        rl.addWidget(self._result_box, stretch=1)
        rl.addWidget(self._make_lbl("Installation Guide:", "color:#8b949e;font-size:11px;"))
        rl.addWidget(install_guide)

        body.addWidget(left)
        body.addWidget(mid)
        body.addWidget(right)
        body.setSizes([220, 380, 400])

        layout.addWidget(self._engine_bar)
        layout.addWidget(body, stretch=1)

        # Load existing account list on init
        QTimer.singleShot(500, self._load_existing_accounts)

    def _make_lbl(self, text: str, style: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(style)
        return lbl

    # -- Existing Account Selection --
    def _load_existing_accounts(self):
        """Refresh existing account dropdown"""
        self._existing_user_combo.blockSignals(True)
        current = self._existing_user_combo.currentData()
        self._existing_user_combo.clear()
        self._existing_user_combo.addItem("-- New User --", "")
        if self._auth_ref:
            auth = self._auth_ref()
            if auth:
                users = auth.list_users()
                for u in users:
                    methods = ", ".join(u.auth_methods) if u.auth_methods else "No auth"
                    has_face = "👤" if "face" in u.auth_methods else ""
                    self._existing_user_combo.addItem(
                        f"{u.name} ({u.user_id}) {has_face} [{methods}]",
                        u.user_id
                    )
        # Restore previous selection
        for i in range(self._existing_user_combo.count()):
            if self._existing_user_combo.itemData(i) == current:
                self._existing_user_combo.setCurrentIndex(i)
                break
        self._existing_user_combo.blockSignals(False)

    def _on_existing_user_changed(self, idx):
        """When selecting existing account, auto-fill user_id and display name"""
        uid = self._existing_user_combo.currentData()
        if uid:
            self._reg_id.setText(uid)
            self._reg_id.setEnabled(False)
            if self._auth_ref:
                auth = self._auth_ref()
                if auth:
                    user = auth.get_user(uid)
                    if user:
                        self._reg_name.setText(user.name)
                        self._reg_name.setEnabled(False)
        else:
            self._reg_id.setText("")
            self._reg_id.setEnabled(True)
            self._reg_name.setText("")
            self._reg_name.setEnabled(True)

    # -- Engine Detection --
    def _check_engine(self):
        try:
            from engine.face_recognition_engine import get_engine_name, is_available
            name = get_engine_name()
        except Exception as e:
            self._engine_lbl.setText(f"👁️  Face Engine: Load failed ({e})")
            self._engine_lbl.setStyleSheet("color:#f85149;font-size:12px;font-weight:600;")
            self._install_btn.setVisible(True)
            return
        if name == "insightface":
            self._engine_lbl.setText("👁️  Face Engine: InsightFace ✅ (highest accuracy)")
            self._engine_lbl.setStyleSheet("color:#3fb950;font-size:13px;font-weight:600;")
        elif name == "face_recognition":
            self._engine_lbl.setText("👁️  Face Engine: face_recognition (dlib) ✅")
            self._engine_lbl.setStyleSheet("color:#3fb950;font-size:13px;font-weight:600;")
        elif name in ("opencv_dnn", "opencv_haar"):
            self._engine_lbl.setText(f"👁️  Face Engine: OpenCV ⚠️ (detection only, no identity)")
            self._engine_lbl.setStyleSheet("color:#d29922;font-size:13px;font-weight:600;")
            self._install_btn.setVisible(True)
        else:
            self._engine_lbl.setText("👁️  Face Engine: Not installed ❌")
            self._engine_lbl.setStyleSheet("color:#f85149;font-size:13px;font-weight:600;")
            self._install_btn.setVisible(True)

        self._load_users()

    def _install_engine(self):
        from engine.tools import execute_tool
        self._install_btn.setText("⏳ Installing…")
        self._install_btn.setEnabled(False)
        QApplication.processEvents()
        r = execute_tool("run_command", {
            "command": "pip install insightface onnxruntime opencv-python",
            "timeout": 180
        })
        if r.get("ok"):
            self._engine_lbl.setText("✅ Install complete, please restart app")
            self._engine_lbl.setStyleSheet("color:#3fb950;font-size:13px;font-weight:600;")
        else:
            self._install_btn.setText("📦  Retry Install")
            self._install_btn.setEnabled(True)
            QMessageBox.warning(self, "Install Failed", r.get("stderr","")[:400])

    # -- Camera / Image --
    def _capture(self):
        self._worker = FaceWorker("capture", self.db_file)
        self._worker.result.connect(self._on_captured)
        self._worker.error.connect(lambda e: self._show_result(f"❌ {e}", False))
        self._worker.start()

    def _on_captured(self, res: dict):
        if not res.get("ok"):
            return
        frame = res["frame"]
        self._current_frame = frame
        self._show_frame(frame)
        self._btn_register.setEnabled(True)

    def _import_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Face Photo", "",
            "Images (*.jpg *.jpeg *.png *.bmp)"
        )
        if not path:
            return
        try:
            import numpy as np
            from PIL import Image
            img = Image.open(path).convert("RGB")
            self._current_frame = np.array(img)
            self._show_frame(self._current_frame)
            self._btn_register.setEnabled(True)
        except Exception as e:
            QMessageBox.warning(self, "Import Failed", str(e))

    def _show_frame(self, frame):
        """Display numpy RGB array in preview area"""
        try:
            from PIL import Image
            from PyQt6.QtGui import QImage
            h, w, ch = frame.shape
            qi = QImage(frame.data, w, h, ch * w, QImage.Format.Format_RGB888)
            pix = QPixmap.fromImage(qi).scaled(
                360, 240,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self._preview.setPixmap(pix)
        except Exception:
            self._preview.setText("Image load failed")

    # -- Registration --
    def _register(self):
        if self._current_frame is None:
            QMessageBox.warning(self, "Notice", "Please capture or import an image first")
            return
        user_id = self._reg_id.text().strip()
        label   = self._reg_name.text().strip()
        if not user_id:
            QMessageBox.warning(self, "Notice", "Please fill in User ID")
            return

        self._btn_register.setEnabled(False)
        self._worker = FaceWorker("register", self.db_file,
                                  self._current_frame, user_id, label)
        self._worker.result.connect(self._on_registered)
        self._worker.error.connect(lambda e: (
            self._show_result(f"❌ {e}", False),
            self._btn_register.setEnabled(True)
        ))
        self._worker.start()

    def _on_registered(self, res: dict):
        self._btn_register.setEnabled(True)
        if res.get("ok"):
            uid = res.get("user_id", "")
            # Notify AuthManager this user has registered face
            if uid and self._auth_ref:
                auth = self._auth_ref()
                if auth and auth.get_user(uid):
                    auth.add_face_method(uid)
            self._show_result(
                f"✅ Registration successful!\n"
                f"User ID: {uid}\n"
                f"Engine: {res.get('engine')}\n"
                f"Confidence: {res.get('confidence', 0):.2%}",
                True
            )
            self._load_users()
            self._load_existing_accounts()
        else:
            self._show_result(f"❌ Registration failed: {res.get('error')}", False)

    # -- Recognition --
    def _identify(self):
        self._worker = FaceWorker("capture", self.db_file)
        self._worker.result.connect(self._on_capture_for_identify)
        self._worker.error.connect(
            lambda e: self._show_result(f"❌ Camera error: {e}", False)
        )
        self._worker.start()

    def _on_capture_for_identify(self, res: dict):
        if not res.get("ok"):
            return
        frame = res["frame"]
        self._current_frame = frame
        self._show_frame(frame)

        # Recognize
        self._worker = FaceWorker("identify", self.db_file, frame)
        self._worker.result.connect(self._on_identified)
        self._worker.error.connect(
            lambda e: self._show_result(f"❌ {e}", False)
        )
        self._worker.start()

    def _on_identified(self, res: dict):
        if not res.get("ok"):
            self._show_result(f"❌ {res.get('reason','Unknown error')}", False)
            return

        if res.get("identified"):
            self._show_result(
                f"✅ Recognition successful!\n\n"
                f"👤 User: {res.get('label')} ({res.get('user_id')})\n"
                f"📊 Confidence: {res.get('confidence', 0):.1%}\n"
                f"🔧 Engine: {res.get('engine')}",
                True
            )
        else:
            self._show_result(
                f"❓ No registered user identified\n\n"
                f"Reason: {res.get('reason','')}\n"
                f"Best match score: {res.get('best_score',0):.1%}\n\n"
                "(If this is you, please re-register your face)",
                False
            )

    def _show_result(self, text: str, success: bool):
        color = "#3fb950" if success else "#f85149"
        self._result_box.setStyleSheet(
            f"QTextEdit{{background:#161b22;border:2px solid {color};"
            "border-radius:8px;color:#e6edf3;font-size:13px;padding:10px;}"
        )
        self._result_box.setPlainText(text)

    # -- User Management --
    def _load_users(self):
        self._worker = FaceWorker("list", self.db_file)
        self._worker.result.connect(self._on_users_loaded)
        self._worker.start()

    def _on_users_loaded(self, res: dict):
        self._user_list.clear()
        for u in res.get("users", []):
            item = QListWidgetItem(
                f"👤 {u.get('label') or u.get('user_id')}\n"
                f"   ID: {u.get('user_id')}  Engine: {u.get('engine','?')}"
            )
            item.setData(Qt.ItemDataRole.UserRole, u.get("user_id"))
            self._user_list.addItem(item)

    def _delete_user(self):
        item = self._user_list.currentItem()
        if not item:
            return
        user_id = item.data(Qt.ItemDataRole.UserRole)
        confirm = QMessageBox.question(
            self, "Confirm Delete",
            f"Are you sure you want to delete face data for user {user_id}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if confirm == QMessageBox.StandardButton.Yes:
            from engine.face_recognition_engine import FaceDatabase
            FaceDatabase(self.db_file).delete_user(user_id)
            self._load_users()


# ---- SimLife Page (embedded / fallback) ----
class SimLifePage(QWidget):
    """SimLife page, embeds QWebEngineView loading http://127.0.0.1:8769"""

    SIMLIFE_URL = "http://127.0.0.1:8769"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._web = None
        self._loaded = False
        self._setup_ui()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Top bar
        header = QWidget()
        header.setFixedHeight(48)
        header.setStyleSheet("background:#161b22;border-bottom:1px solid #30363d;")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(16, 0, 16, 0)
        h_lay.addWidget(_make_label("🌱  SimLife",
            "color:#e6edf3;font-size:15px;font-weight:700;"))
        h_lay.addStretch()

        btn_refresh = QPushButton("🔄 Refresh")
        btn_refresh.setFixedSize(70, 32)
        btn_refresh.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:6px;color:#c9d1d9;font-size:12px;}"
            "QPushButton:hover{color:#58a6ff;border-color:#58a6ff;}"
        )
        btn_refresh.clicked.connect(self._refresh)
        h_lay.addWidget(btn_refresh)

        btn_external = QPushButton("🌐 Open in Browser")
        btn_external.setFixedSize(100, 32)
        btn_external.setStyleSheet(
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:6px;color:#c9d1d9;font-size:12px;}"
            "QPushButton:hover{color:#58a6ff;border-color:#58a6ff;}"
        )
        btn_external.clicked.connect(self._open_external)
        h_lay.addWidget(btn_external)

        outer.addWidget(header)

        # Content area (WebEngine or placeholder)
        self._container = QWidget()
        self._container.setStyleSheet("background:#0d1117;")
        container_lay = QVBoxLayout(self._container)
        container_lay.setContentsMargins(0, 0, 0, 0)

        # Placeholder (shown before WebEngine loads)
        self._placeholder = QLabel("🌱 SimLife\n\nLoading…")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(
            "color:#8b949e;font-size:16px;background:#0d1117;"
        )
        self._placeholder.setMinimumSize(300, 200)
        container_lay.addWidget(self._placeholder)

        outer.addWidget(self._container, stretch=1)

        # Try loading WebEngine after 1 second delay
        from PyQt6.QtCore import QTimer
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.timeout.connect(self._try_load)
        self._load_timer.start(1000)

    def _try_load(self):
        """Lazy load WebEngine to avoid affecting startup performance"""
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView
            lay = self._container.layout()

            self._web = QWebEngineView()
            self._web.setStyleSheet(
                "QWebEngineView{background:#0d1117;border:none;}"
            )
            self._web.setUrl(self.SIMLIFE_URL)

            lay.removeWidget(self._placeholder)
            self._placeholder.deleteLater()
            self._placeholder = None
            lay.addWidget(self._web)
            self._loaded = True
            print("[SimLife] Embedded page loaded")
        except ImportError:
            if self._placeholder:
                self._placeholder.setText(
                    "🌱 SimLife\n\n"
                    "PyQt6-WebEngine not installed\n"
                    "Click \"Open in Browser\" above\n"
                    "Or run: pip install PyQt6-WebEngine"
                )
            print("[SimLife] WebEngine not installed, fallback to external browser")
        except Exception as e:
            if self._placeholder:
                self._placeholder.setText(
                    f"🌱 SimLife\n\nWebEngine load failed:\n{e}\n\n"
                    "Click \"Open in Browser\" above"
                )
            print(f"[SimLife] WebEngine load failed: {e}")

    def _refresh(self):
        if self._web:
            self._web.reload()
        else:
            self._try_load()

    def _open_external(self):
        import webbrowser
        webbrowser.open(self.SIMLIFE_URL)

    def showEvent(self, event):
        super().showEvent(event)
        # Try loading when switching to this tab if not yet loaded
        if not self._loaded and self._placeholder and \
                "Loading" in self._placeholder.text():
            self._try_load()


# ---- Main Window ----
class MainWindow(QMainWindow):

    # Signal to notify UI after face recognition completes (runs in main thread)
    _auth_done = pyqtSignal()

    def __init__(self, agent, db_file: str):
        super().__init__()
        self.agent = agent
        self.db_file = db_file
        self._worker: AGIWorker | None = None
        self._thinking_lbl = None
        self._auth = None

        # Connect face recognition complete signal → main thread updates UI
        self._auth_done.connect(self._on_face_recognized)

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(900, 640)
        self.resize(1100, 720)
        self.setWindowIcon(make_tray_icon())
        self.setStyleSheet(DARK_QSS)

        self._setup_ui()
        self._setup_statusbar()

        # Check offline messages 2 seconds after startup
        QTimer.singleShot(2000, self._check_offline_messages)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # -- Left Navigation Bar --
        nav = QWidget()
        nav.setFixedWidth(56)
        nav.setStyleSheet("background:#161b22;border-right:1px solid #30363d;")
        nav_layout = QVBoxLayout(nav)
        nav_layout.setContentsMargins(6, 12, 6, 12)
        nav_layout.setSpacing(4)

        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.West)
        self._tabs.setStyleSheet(
            "QTabWidget::pane{border:none;}"
            "QTabBar::tab{width:44px;height:44px;font-size:20px;"
            "background:transparent;border:none;border-radius:8px;margin:2px;}"
            "QTabBar::tab:selected{background:#21262d;}"
            "QTabBar::tab:hover{background:#21262d;}"
        )

        # Chat page
        self.chat_page = ChatPage()
        self.chat_page.message_sent.connect(self._on_message)
        self.chat_page.simlife_toggled.connect(self._on_simlife_toggled)
        self._tabs.addTab(self.chat_page, "💬")
        self._tabs.setTabToolTip(0, "Chat")

        # Memory page
        self.memory_page = MemoryPage(self.db_file, auth_ref=lambda: getattr(self, '_auth', None))
        self._tabs.addTab(self.memory_page, "🗄️")
        self._tabs.setTabToolTip(1, "Memory")
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # Personality page (reuse web logic, simplified as JSON editor)
        self.personality_page = self._build_personality_page()
        self._tabs.addTab(self.personality_page, "🎭")
        self._tabs.setTabToolTip(2, "Personality")

        # Tool test page
        self.tool_test_page = ToolTestPage()
        self.tool_test_page.parent_ref = self
        self._tabs.addTab(self.tool_test_page, "🔬")
        self._tabs.setTabToolTip(3, "Tool Test Bench")

        # Coding agent page
        self.coder_page = CoderPage()
        self._tabs.addTab(self.coder_page, "💻")
        self._tabs.setTabToolTip(4, "Coding Agent")

        # Face recognition page (pass auth ref, sync auth_methods after registration)
        self.face_page = FaceRecognitionPage(db_file=self.db_file, auth_ref=lambda: getattr(self, '_auth', None))
        self._tabs.addTab(self.face_page, "👁️")
        self._tabs.setTabToolTip(5, "Face Recognition")

        # User Profile Page (pass auth ref, ensure user_id matches Agent writes)
        self.profile_page = UserProfilePage(db_file=self.db_file, auth_ref=lambda: self._auth)
        self._tabs.addTab(self.profile_page, "👤")
        self._tabs.setTabToolTip(6, "User Profile")

        # Memory graph page
        self.graph_page = MemoryGraphPage(db_file=self.db_file)
        self._tabs.addTab(self.graph_page, "🕸️")
        self._tabs.setTabToolTip(7, "Memory Graph")

        # Active learning page
        self.learner_page = LearnerPage(db_file=self.db_file)
        self.learner_page.learn_requested.connect(self._on_learn_requested)
        self._tabs.addTab(self.learner_page, "🎓")
        self._tabs.setTabToolTip(8, "Active Learning")

        # Settings page
        self.settings_page = SettingsPage()
        self.settings_page.settings_changed.connect(self._on_settings_changed)
        self._tabs.addTab(self.settings_page, "⚙️")
        self._tabs.setTabToolTip(9, "Settings")

        main_layout.addWidget(self._tabs)

        # Initial tab visibility (guests hide privacy tabs)
        self._update_tab_visibility()

    def _build_personality_page(self) -> QWidget:
        """Form-based personality settings page (with guide + core beliefs + sliders)"""
        from desktop.config import PERSONALITY_FILE

        # Read existing personality
        p_data = {}
        if Path(PERSONALITY_FILE).exists():
            try:
                p_data = json.loads(Path(PERSONALITY_FILE).read_text(encoding="utf-8"))
            except Exception:
                pass

        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Top bar
        header = QWidget()
        header.setFixedHeight(48)
        header.setStyleSheet("background:#161b22;border-bottom:1px solid #30363d;")
        h_lay = QHBoxLayout(header)
        h_lay.setContentsMargins(16, 0, 16, 0)
        h_lay.addWidget(_make_label("🎭  Personality Settings",
            "color:#e6edf3;font-size:15px;font-weight:700;"))
        h_lay.addStretch()
        # Auth status hint (shows lock icon when not logged in)
        self._p_auth_hint = QLabel("")
        self._p_auth_hint.setStyleSheet("color:#d29922;font-size:11px;")
        h_lay.addWidget(self._p_auth_hint)
        self._p_msg = QLabel("")
        self._p_msg.setStyleSheet("color:#3fb950;font-size:12px;")
        h_lay.addWidget(self._p_msg)
        h_lay.addSpacing(12)
        self._p_btn_save = QPushButton("💾  Save")
        self._p_btn_save.setFixedHeight(32)
        self._p_btn_save.setStyleSheet(
            "QPushButton{background:rgba(31,111,235,.2);border:1px solid #1f6feb;"
            "border-radius:6px;color:#58a6ff;font-size:12px;padding:0 16px;}"
            "QPushButton:hover{background:rgba(31,111,235,.4);}"
            "QPushButton:disabled{opacity:0.35;border-color:#30363d;color:#484f58;}"
        )
        self._p_btn_save.clicked.connect(self._save_personality)
        # Collect all form widgets (for lock/unlock readonly state)
        self._p_form_widgets = []
        h_lay.addWidget(self._p_btn_save)
        outer.addWidget(header)

        # Main: left form + right guide
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setStyleSheet("QSplitter::handle{background:#30363d;width:1px;}")

        # -- Left form (scrollable) --
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            "QScrollArea{border:none;background:#0d1117;}"
            "QScrollBar:vertical{background:#161b22;width:6px;}"
            "QScrollBar::handle:vertical{background:#30363d;border-radius:3px;}"
        )
        form_widget = QWidget()
        form_widget.setStyleSheet("background:#0d1117;")
        fl = QVBoxLayout(form_widget)
        fl.setContentsMargins(20, 16, 20, 20)
        fl.setSpacing(14)

        SECTION_STYLE = (
            "color:#e6edf3;font-size:13px;font-weight:700;"
            "border-bottom:1px solid #30363d;padding-bottom:4px;margin-top:8px;"
        )
        LABEL_STYLE = "color:#8b949e;font-size:11px;"
        INPUT_STYLE = (
            "QLineEdit,QTextEdit{background:#161b22;border:1px solid #30363d;"
            "border-radius:6px;color:#e6edf3;font-size:12px;padding:6px 10px;}"
            "QLineEdit:focus,QTextEdit:focus{border-color:#58a6ff;}"
        )

        def section(title):
            lbl = QLabel(title)
            lbl.setStyleSheet(SECTION_STYLE)
            fl.addWidget(lbl)

        def field(label, widget, hint=""):
            row = QVBoxLayout()
            row.setSpacing(3)
            lbl = QLabel(label)
            lbl.setStyleSheet(LABEL_STYLE)
            row.addWidget(lbl)
            row.addWidget(widget)
            if hint:
                h = QLabel(hint)
                h.setStyleSheet("color:#6e7681;font-size:10px;font-style:italic;")
                h.setWordWrap(True)
                row.addWidget(h)
            fl.addLayout(row)

        # Basic Info
        section("👤  Basic Info")
        row1 = QHBoxLayout()
        self._p_name = QLineEdit(p_data.get("name", ""))
        self._p_name.setPlaceholderText("AGI's name")
        self._p_name.setStyleSheet(INPUT_STYLE)
        self._p_age = QLineEdit(str(p_data.get("age", 28)))
        self._p_age.setPlaceholderText("Age")
        self._p_age.setFixedWidth(70)
        self._p_age.setStyleSheet(INPUT_STYLE)
        self._p_gender = QComboBox()
        self._p_gender.addItems(["Not set", "Male", "Female", "Other"])
        self._p_gender.setCurrentText(p_data.get("gender", "Not set"))
        self._p_gender.setStyleSheet(
            "QComboBox{background:#161b22;border:1px solid #30363d;"
            "border-radius:6px;color:#e6edf3;padding:5px 8px;font-size:12px;}"
            "QComboBox QAbstractItemView{background:#161b22;color:#e6edf3;}"
        )
        row1.addWidget(_make_label("Name", LABEL_STYLE))
        row1.addWidget(self._p_name)
        row1.addWidget(_make_label("Age", LABEL_STYLE))
        row1.addWidget(self._p_age)
        row1.addWidget(_make_label("Gender", LABEL_STYLE))
        row1.addWidget(self._p_gender)
        fl.addLayout(row1)

        # -- Core Beliefs (Deepest Values) --
        section("🌀  Core Beliefs (Deepest Values)")
        self._p_core_belief = QTextEdit()
        self._p_core_belief.setFixedHeight(90)
        self._p_core_belief.setPlaceholderText(
            "AGI's deepest beliefs, affect all reasoning and responses, highest priority.\n"
            "Example: \"The essence of knowledge is to free people, not make them smarter.\"\n"
            "\"My purpose is to truly understand people, not imitate them.\""
        )
        self._p_core_belief.setStyleSheet(INPUT_STYLE)
        self._p_core_belief.setPlainText(p_data.get("core_belief", ""))
        field("Core Beliefs", self._p_core_belief,
              "This is \"implanted thought\" — even if requested by the user, AGI will not violate it. Priority is above all other instructions.")

        # -- Personality Traits (sliders) --
        section("🎛️  Personality Traits (drag sliders to adjust)")
        traits = p_data.get("traits", {})
        TRAIT_INFO = [
            ("openness",         "Openness",  "Degree of accepting new ideas and experiences", "Traditional", "Exploratory"),
            ("conscientiousness","Conscientiousness", "Degree of being organized and planful", "Casual", "Disciplined"),
            ("extraversion",     "Extraversion", "Level of social interaction and self-expression", "Introverted", "Outgoing"),
            ("agreeableness",    "Agreeableness", "Degree of friendliness and cooperation", "Direct", "Agreeable"),
            ("neuroticism",      "Emotional Stability", "Amplitude of mood swings (lower = more stable)", "Sensitive", "Stable"),
            ("rationality",      "Rationality", "Tendency for logical analysis over intuition", "Intuitive", "Analytical"),
            ("empathy",          "Empathy",   "Ability to feel and understand others' emotions", "Detached", "Empathic"),
            ("curiosity",        "Curiosity", "Drive to actively explore the unknown", "Focused", "Exploratory"),
        ]
        self._trait_sliders = {}
        for key, name, desc, left_lbl, right_lbl in TRAIT_INFO:
            val = int(traits.get(key, 5))
            row = QHBoxLayout()
            name_lbl = QLabel(name)
            name_lbl.setFixedWidth(72)
            name_lbl.setStyleSheet("color:#c9d1d9;font-size:12px;font-weight:600;")
            left = QLabel(left_lbl)
            left.setFixedWidth(60)
            left.setStyleSheet("color:#6e7681;font-size:10px;text-align:right;")
            left.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 10)
            slider.setValue(val)
            slider.setStyleSheet(
                "QSlider::groove:horizontal{background:#21262d;height:4px;border-radius:2px;}"
                "QSlider::handle:horizontal{background:#58a6ff;width:14px;height:14px;"
                "border-radius:7px;margin:-5px 0;}"
                "QSlider::sub-page:horizontal{background:#58a6ff;border-radius:2px;}"
            )
            right = QLabel(right_lbl)
            right.setFixedWidth(60)
            right.setStyleSheet("color:#6e7681;font-size:10px;")
            val_lbl = QLabel(str(val))
            val_lbl.setFixedWidth(20)
            val_lbl.setStyleSheet("color:#58a6ff;font-size:12px;font-weight:700;")
            slider.valueChanged.connect(lambda v, l=val_lbl: l.setText(str(v)))
            self._trait_sliders[key] = slider
            row.addWidget(name_lbl)
            row.addWidget(left)
            row.addWidget(slider)
            row.addWidget(right)
            row.addWidget(val_lbl)
            fl.addLayout(row)
            hint_lbl = QLabel(desc)
            hint_lbl.setStyleSheet("color:#6e7681;font-size:10px;margin-left:78px;margin-bottom:2px;")
            fl.addWidget(hint_lbl)

        # -- Speech Style & Worldview --
        section("💬  Speech Style & Worldview")
        self._p_speech = QLineEdit(p_data.get("speech_style", "Natural, direct"))
        self._p_speech.setPlaceholderText("e.g. Humorous but deep, likes metaphors")
        self._p_speech.setStyleSheet(INPUT_STYLE)
        field("Speech Style", self._p_speech)

        self._p_worldview = QTextEdit()
        self._p_worldview.setFixedHeight(70)
        self._p_worldview.setPlaceholderText("AGI's basic view of the world and life")
        self._p_worldview.setStyleSheet(INPUT_STYLE)
        self._p_worldview.setPlainText(p_data.get("worldview", ""))
        field("Worldview", self._p_worldview)

        # -- Interests & Values (comma-separated) --
        section("🌟  Interests & Values")
        self._p_interests = QLineEdit(", ".join(p_data.get("interests", [])))
        self._p_interests.setPlaceholderText("e.g. Programming, AGI research, Philosophy, Music")
        self._p_interests.setStyleSheet(INPUT_STYLE)
        field("Interests", self._p_interests, "Comma-separated")

        self._p_values = QLineEdit(", ".join(p_data.get("values", [])))
        self._p_values.setPlaceholderText("e.g. Honesty, Freedom, Growth, Kindness")
        self._p_values.setStyleSheet(INPUT_STYLE)
        field("Core Values", self._p_values, "Comma-separated")

        self._p_taboos = QLineEdit(", ".join(p_data.get("taboos", [])))
        self._p_taboos.setPlaceholderText("e.g. Lying, harming others")
        self._p_taboos.setStyleSheet(INPUT_STYLE)
        field("Taboos (never do)", self._p_taboos, "Comma-separated")

        # -- Avatar Description (for image generation) --
        section("🖼️  Avatar Description (for image generation)")
        avatar_widget = QWidget()
        avatar_lay = QHBoxLayout(avatar_widget)
        avatar_lay.setContentsMargins(0, 0, 0, 0)
        self._p_avatar_prompt = QTextEdit()
        self._p_avatar_prompt.setFixedHeight(70)
        self._p_avatar_prompt.setPlaceholderText(
            "Describe the system character's appearance in English, for generating selfies and scenery images.\n"
            "Example: a young woman with long black hair, wearing a white dress, "
            "gentle smile, anime style, soft lighting"
        )
        self._p_avatar_prompt.setStyleSheet(INPUT_STYLE)
        self._p_avatar_prompt.setPlainText(p_data.get("avatar_prompt", ""))
        avatar_lay.addWidget(self._p_avatar_prompt)

        # AI generate button
        self._btn_gen_avatar = QPushButton("✨ AI Generate")
        self._btn_gen_avatar.setFixedSize(80, 70)
        self._btn_gen_avatar.setStyleSheet(
            "QPushButton{background:#1f6feb;border:none;border-radius:6px;"
            "color:white;font-size:12px;font-weight:700;}"
            "QPushButton:hover{background:#388bfd;}"
            "QPushButton:disabled{background:#21262d;color:#8b949e;}"
        )
        self._btn_gen_avatar.clicked.connect(self._ai_generate_avatar)
        avatar_lay.addWidget(self._btn_gen_avatar)
        field("Avatar (English)", avatar_widget,
              "Click \"AI Generate\" to auto-generate from personality, or edit manually")

        self._p_avatar_hint = QLabel("")
        self._p_avatar_hint.setStyleSheet("color:#8b949e;font-size:10px;padding-left:4px;")
        fl.addWidget(self._p_avatar_hint)

        fl.addStretch()
        scroll.setWidget(form_widget)
        splitter.addWidget(scroll)

        # -- Right guide panel --
        help_widget = QWidget()
        help_widget.setFixedWidth(280)
        help_widget.setStyleSheet("background:#161b22;")
        hl = QVBoxLayout(help_widget)
        hl.setContentsMargins(16, 16, 16, 16)
        hl.setSpacing(12)

        hl.addWidget(_make_label("📖  Guide",
            "color:#e6edf3;font-size:13px;font-weight:700;border-bottom:1px solid #30363d;padding-bottom:6px;"))

        HELP_ITEMS = [
            ("🌀 Core Beliefs", "Highest priority core beliefs. Like Inception's \"idea implantation\" — AGI's reasoning will never violate it. Suitable for AGI's purpose, moral bottom lines, etc."),
            ("🎛️ Traits", "8 dimensions control AGI's behavioral style. Higher values lean right. Set before chatting, values will drift naturally over time."),
            ("💬 Speech Style", "Directly affects AGI's tone and wording. Can be specific, e.g. \"concise and direct, likes rhetorical questions, occasional dark humor\"."),
            ("🌟 Interests", "AGI will actively mention these interests and engage more on related topics."),
            ("🧠 Experience", "[Not manually editable] Formed automatically by AGI through learning and conversation, view in Active Learning page. Only clear all memories can reset."),
            ("💾 Save Timing", "Click save after settings, takes effect from next message. Traits drift naturally over time (fine-tuned every 20 conversation rounds)."),
        ]
        for title, content in HELP_ITEMS:
            card = QWidget()
            card.setStyleSheet(
                "QWidget{background:#0d1117;border:1px solid #21262d;"
                "border-radius:8px;padding:2px;}"
            )
            cl = QVBoxLayout(card)
            cl.setContentsMargins(10, 8, 10, 8)
            cl.setSpacing(4)
            t = QLabel(title)
            t.setStyleSheet("color:#58a6ff;font-size:12px;font-weight:700;background:transparent;border:none;")
            c = QLabel(content)
            c.setStyleSheet("color:#8b949e;font-size:11px;background:transparent;border:none;")
            c.setWordWrap(True)
            cl.addWidget(t)
            cl.addWidget(c)
            hl.addWidget(card)

        hl.addStretch()
        splitter.addWidget(help_widget)
        splitter.setSizes([600, 280])

        outer.addWidget(splitter)

        # Collect all form widgets for batch enable/disable on auth state change
        self._p_form_widgets = [
            self._p_name, self._p_age, self._p_gender,
            self._p_core_belief, self._p_speech, self._p_worldview,
            self._p_interests, self._p_values, self._p_taboos,
            self._p_avatar_prompt,
        ]
        # Initialize based on auth state
        self._update_personality_auth()

        return page

    def _ai_generate_avatar(self):
        """Auto-generate avatar description from current personality settings using AI"""
        if not self.agent or not hasattr(self.agent, 'b'):
            self._p_avatar_hint.setText("⚠️ Engine not ready, please try again later")
            self._p_avatar_hint.setStyleSheet("color:#f85149;font-size:10px;padding-left:4px;")
            return

        self._btn_gen_avatar.setEnabled(False)
        self._p_avatar_hint.setText("⏳ AI is generating avatar description…")
        self._p_avatar_hint.setStyleSheet("color:#58a6ff;font-size:10px;padding-left:4px;")

        # Collect current personality info
        name = self._p_name.text().strip()
        age = self._p_age.text().strip()
        gender = self._p_gender.currentText()
        speech = self._p_speech.text().strip()
        worldview = self._p_worldview.toPlainText().strip()
        interests = self._p_interests.text().strip()
        values = self._p_values.text().strip()
        core_belief = self._p_core_belief.toPlainText().strip()

        traits_desc = ""
        if hasattr(self, '_trait_sliders') and self._trait_sliders:
            trait_map = {
                "openness": "Openness", "conscientiousness": "Conscientiousness",
                "extraversion": "Extraversion", "agreeableness": "Agreeableness",
                "neuroticism": "Emotional Stability", "rationality": "Rationality",
                "empathy": "Empathy", "curiosity": "Curiosity"
            }
            trait_parts = []
            for k, slider in self._trait_sliders.items():
                v = slider.value()
                label = trait_map.get(k, k)
                if v >= 7:
                    trait_parts.append(f"{label}Very High({v})")
                elif v <= 4:
                    trait_parts.append(f"{label}Low({v})")
            if trait_parts:
                traits_desc = "Personality Traits: " + ", ".join(trait_parts[:5])

        sys_prompt = (
            "You are a character designer. Based on the character info below, "
            "generate a concise English description of the character's visual appearance "
            "for AI image generation (anime art style). "
            "Focus ONLY on visual appearance: hair, eyes, face, outfit, build, expression, aura. "
            "Keep it under 40 words. No explanations, just the description.\n\n"
            f"Name: {name}\n"
            f"Age: {age}\n"
            f"Gender: {gender}\n"
            f"Speech style: {speech}\n"
            f"Worldview: {worldview}\n"
            f"Interests: {interests}\n"
            f"Values: {values}\n"
            f"Core belief: {core_belief}\n"
            f"{traits_desc}"
        )

        class _AvatarWorker(QThread):
            done = pyqtSignal(str)
            fail = pyqtSignal(str)

            def __init__(self, llm, prompt):
                super().__init__()
                self.llm = llm
                self.prompt = prompt

            def run(self):
                try:
                    resp = self.llm.generate(
                        prompt="Generate the visual appearance description now.",
                        system=sys_prompt,
                        temperature=0.8,
                    )
                    # LLM may return quoted content, strip quotes
                    result = resp.strip().strip('"').strip("'")
                    self.done.emit(result)
                except Exception as e:
                    self.fail.emit(str(e))

        self._avatar_worker = _AvatarWorker(self.agent.b.llm, sys_prompt)
        self._avatar_worker.done.connect(self._on_avatar_generated)
        self._avatar_worker.fail.connect(self._on_avatar_gen_failed)
        self._avatar_worker.start()

    def _on_avatar_generated(self, text: str):
        self._p_avatar_prompt.setPlainText(text)
        self._btn_gen_avatar.setEnabled(True)
        self._p_avatar_hint.setText("✅ Generated, can edit manually")
        self._p_avatar_hint.setStyleSheet("color:#3fb950;font-size:10px;padding-left:4px;")

    def _on_avatar_gen_failed(self, err: str):
        self._btn_gen_avatar.setEnabled(True)
        self._p_avatar_hint.setText(f"❌ Generation failed: {err[:30]}")
        self._p_avatar_hint.setStyleSheet("color:#f85149;font-size:10px;padding-left:4px;")

    def _save_personality(self):
        # -- Auth check --
        if hasattr(self, '_auth') and self._auth and not self._auth.is_verified():
            reply = QMessageBox.question(
                self, "Authentication Required",
                "Changing personality settings requires login.\n\nLogin now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._show_unlock_dialog()
            return

        from desktop.config import PERSONALITY_FILE

        # Confirmation dialog, prevent misoperation
        reply = QMessageBox.question(
            self, "Confirm Save Personality",
            "Saving will affect AGI's personality and behavior, effective from next message.\n\nConfirm save?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No   # Default No, prevent misclick
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            def parse_list(s):
                return [x.strip() for x in s.split(",") if x.strip()]

            data = {
                "name":         self._p_name.text().strip() or "Unnamed",
                "age":          int(self._p_age.text().strip() or 28),
                "gender":       self._p_gender.currentText(),
                "core_belief":  self._p_core_belief.toPlainText().strip(),
                "speech_style": self._p_speech.text().strip() or "Natural, direct",
                "worldview":    self._p_worldview.toPlainText().strip(),
                "interests":    parse_list(self._p_interests.text()),
                "values":       parse_list(self._p_values.text()),
                "taboos":       parse_list(self._p_taboos.text()),
                "sensitivities": [],
                "key_experiences": [],
                "avatar_prompt": self._p_avatar_prompt.toPlainText().strip(),
                "traits": {
                    key: slider.value()
                    for key, slider in self._trait_sliders.items()
                }
            }
            Path(PERSONALITY_FILE).write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            self._p_msg.setText("✅ Saved, effective next conversation")
            self._p_msg.setStyleSheet("color:#3fb950;font-size:12px;")
            QTimer.singleShot(3000, lambda: self._p_msg.setText(""))
        except Exception as e:
            self._p_msg.setText(f"❌ Save failed: {e}")
            self._p_msg.setStyleSheet("color:#f85149;font-size:12px;")

    def _update_personality_auth(self):
        """Update personality page save button and form editability based on current auth state"""
        verified = bool(hasattr(self, '_auth') and self._auth and self._auth.is_verified())
        if not hasattr(self, '_p_btn_save'):
            return
        self._p_btn_save.setEnabled(verified)
        if verified:
            self._p_auth_hint.setText("")
            # Unlock all form widgets
            for w in self._p_form_widgets:
                w.setEnabled(True)
            for slider in self._trait_sliders.values():
                slider.setEnabled(True)
        else:
            no_face = bool(hasattr(self, '_auth') and self._auth and self._auth.is_no_face())
            if no_face:
                self._p_auth_hint.setText("🔒 Please register an account first")
            else:
                self._p_auth_hint.setText("🔒 Please login first")
            # Lock all form widgets to readonly
            for w in self._p_form_widgets:
                w.setEnabled(False)
            for slider in self._trait_sliders.values():
                slider.setEnabled(False)

    def _setup_statusbar(self):
        sb = self.statusBar()
        sb.setStyleSheet(
            "QStatusBar{background:#161b22;border-top:1px solid #30363d;"
            "color:#8b949e;font-size:11px;}"
        )
        self._status_emotion = QLabel("Emotion: —")
        self._status_mem     = QLabel("Memory: —")
        self._status_mode    = QLabel("Ready")
        # Identity status (click to unlock)
        self._status_auth    = QLabel("🟡 Verifying identity…")
        self._status_auth.setStyleSheet(
            "color:#d29922;font-size:11px;"
            "text-decoration:underline;"
        )
        self._status_auth.mousePressEvent = lambda e: self._on_auth_click()
        sb.addPermanentWidget(self._status_auth)
        sb.addPermanentWidget(QLabel(" | "))
        sb.addPermanentWidget(self._status_emotion)
        sb.addPermanentWidget(QLabel(" | "))
        sb.addPermanentWidget(self._status_mem)
        sb.addPermanentWidget(QLabel(" | "))
        sb.addWidget(self._status_mode)

    # ---- Authentication ----
    def start_auth_verification(self, auth_manager):
        """Run face recognition in background at startup, auto-login after identification"""
        import threading
        self._auth = auth_manager

        def _recognize():
            try:
                # Face recognition only identifies who it is, verify_face auto-sets state
                auth_manager.verify_face()
            except Exception as e:
                print(f"[Face Recognition] Exception: {e}")
            # Regardless of success/failure, signal main thread to refresh UI
            self._auth_done.emit()

        threading.Thread(target=_recognize, daemon=True).start()

    def _on_face_recognized(self):
        """Signal callback (main thread): read auth state, update UI + permissions"""
        if not self._auth:
            return
        from engine.auth import AuthState

        state = self._auth.state
        # Normalize to string
        state_str = state.value if hasattr(state, 'value') else str(state)

        if state_str == "verified":
            name = self._auth.current_name or "Authenticated User"
            self._status_auth.setText(f"🟢 {name}")
            self._status_auth.setStyleSheet("color:#3fb950;font-size:11px;")
            self._status_auth.setCursor(Qt.CursorShape.ArrowCursor)
            self.chat_page.add_ai_message(f"✅ Welcome back, {name}")
        elif state_str == "no_face":
            self._status_auth.setText("🟡 Unregistered User (click to register)")
            self._status_auth.setStyleSheet(
                "color:#d29922;font-size:11px;text-decoration:underline;")
            self.chat_page.add_ai_message(
                "👋 Welcome! No user accounts registered yet.\n"
                "Click \"Unregistered User\" at bottom to register now. Memory and profile will be bound to your account.\n"
                "Currently running with full permissions."
            )
        else:
            # guest or identification failed → guest mode
            self._status_auth.setText("🔴 Guest Mode (click to unlock)")
            self._status_auth.setStyleSheet(
                "color:#f85149;font-size:11px;text-decoration:underline;")
            self.chat_page.add_ai_message(
                "🔒 No registered user identified, running in **Guest Mode**.\n"
                "Private memories and user profile are protected.\n"
                "Click \"Guest Mode\" at bottom to login or register."
            )
        # Refresh personality page auth state (permission toggle)
        self._update_personality_auth()

    def _on_auth_result(self, result: dict):
        """Quick entry for post-face-login in chat dialog"""
        from engine.auth import AuthState
        state = result.get("state", AuthState.GUEST)
        state_str = state.value if hasattr(state, 'value') else str(state)

        if state_str == "verified":
            name = self._auth.current_name or "Authenticated User"
            self._status_auth.setText(f"🟢 {name}")
            self._status_auth.setStyleSheet("color:#3fb950;font-size:11px;")
            self._status_auth.setCursor(Qt.CursorShape.ArrowCursor)
            self.chat_page.add_ai_message(f"✅ Welcome back, {name}")
        self._update_personality_auth()
        self._update_tab_visibility()

    def _update_tab_visibility(self):
        """Control visibility of privacy-related tabs based on auth state"""
        from engine.auth import AuthState
        verified = bool(
            hasattr(self, '_auth') and self._auth and self._auth.is_verified()
        )
        # Memory graph(7), Active learning(8) require login to view
        if hasattr(self, '_tabs'):
            self._tabs.setTabVisible(7, verified)
            self._tabs.setTabVisible(8, verified)

    def _on_auth_click(self):
        if not hasattr(self, "_auth") or self._auth is None:
            return
        from engine.auth import AuthState
        state = self._auth.state
        if state == AuthState.VERIFIED:
            name = self._auth.current_name or self._auth.user_id or "Current User"
            reply = QMessageBox.question(
                self, "Account Management",
                f"Currently logged in: {name}\n\nLock and switch user?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._auth.lock()
                self._status_auth.setText("🔴 Guest Mode (click to unlock)")
                self._status_auth.setStyleSheet(
                    "color:#f85149;font-size:11px;text-decoration:underline;")
                self.chat_page.add_ai_message("🔒 Locked, switched to guest mode.")
                self._update_personality_auth()
                self._update_tab_visibility()
            return
        self._show_unlock_dialog()

    def _show_unlock_dialog(self):
        """Login / Register dialog"""
        from engine.auth import AuthState
        dialog = QWidget(self, Qt.WindowType.Dialog)
        dialog.setWindowTitle("Login / Register")
        dialog.setFixedSize(420, 500)
        dialog.setStyleSheet(
            "QWidget{background:#161b22;color:#e6edf3;}"
            "QLineEdit{background:#0d1117;border:1px solid #30363d;"
            "border-radius:6px;padding:8px;color:#e6edf3;font-size:13px;}"
            "QPushButton{background:#21262d;border:1px solid #30363d;"
            "border-radius:6px;color:#c9d1d9;padding:8px 16px;font-size:12px;}"
            "QPushButton:hover{border-color:#58a6ff;color:#58a6ff;}"
        )
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        # Tab switch
        tab_row = QHBoxLayout()
        btn_login    = QPushButton("🔑  Login")
        btn_register = QPushButton("✨  Register New User")
        for b in [btn_login, btn_register]:
            b.setCheckable(True)
            b.setFixedHeight(34)
        btn_login.setChecked(True)
        tab_row.addWidget(btn_login)
        tab_row.addWidget(btn_register)
        layout.addLayout(tab_row)

        sep0 = QFrame(); sep0.setFrameShape(QFrame.Shape.HLine)
        sep0.setStyleSheet("color:#30363d;"); layout.addWidget(sep0)

        # -- Login area (password only) --
        login_widget = QWidget()
        lw = QVBoxLayout(login_widget); lw.setContentsMargins(0,0,0,0); lw.setSpacing(8)
        lw.addWidget(_make_label("Passphrase Login", "color:#8b949e;font-size:12px;"))
        pw_input = QLineEdit(); pw_input.setPlaceholderText("Enter passphrase…")
        pw_input.setEchoMode(QLineEdit.EchoMode.Password)
        pw_msg = _make_label("", "color:#f85149;font-size:11px;")
        btn_pw_login = QPushButton("Login")
        lw.addWidget(pw_input); lw.addWidget(pw_msg); lw.addWidget(btn_pw_login)
        lw.addStretch()
        layout.addWidget(login_widget)

        # -- Registration area --
        reg_widget = QWidget(); reg_widget.setVisible(False)
        rw = QVBoxLayout(reg_widget); rw.setContentsMargins(0,0,0,0); rw.setSpacing(8)
        rw.addWidget(_make_label("Display Name *", "color:#8b949e;font-size:12px;"))
        name_input = QLineEdit(); name_input.setPlaceholderText("Your name (e.g. John)")
        rw.addWidget(name_input)
        rw.addWidget(_make_label("Auth Method", "color:#8b949e;font-size:12px;"))
        chk_face = QCheckBox("📷  Face Recognition (go to Face Recognition page after registration)")
        chk_face.setStyleSheet("color:#c9d1d9;font-size:12px;")
        chk_pw = QCheckBox("🔑  Passphrase (no camera needed, more convenient)")
        chk_pw.setStyleSheet("color:#c9d1d9;font-size:12px;")
        chk_pw.setChecked(True)
        rw.addWidget(chk_face); rw.addWidget(chk_pw)
        pw2_widget = QWidget()
        pw2l = QVBoxLayout(pw2_widget); pw2l.setContentsMargins(0,0,0,0); pw2l.setSpacing(4)
        pw2l.addWidget(_make_label("Set Passphrase", "color:#8b949e;font-size:11px;"))
        pw2_input   = QLineEdit(); pw2_input.setPlaceholderText("Suggest a phrase, memorable and unique")
        pw2_input.setEchoMode(QLineEdit.EchoMode.Password)
        pw2_confirm = QLineEdit(); pw2_confirm.setPlaceholderText("Confirm passphrase")
        pw2_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        pw2l.addWidget(pw2_input); pw2l.addWidget(pw2_confirm)
        rw.addWidget(pw2_widget)
        chk_pw.toggled.connect(pw2_widget.setVisible)
        reg_msg = _make_label("", "color:#f85149;font-size:11px;")
        btn_do_reg = QPushButton("✨  Create Account")
        btn_do_reg.setStyleSheet(
            "QPushButton{background:rgba(63,185,80,.15);border:1px solid #3fb950;"
            "border-radius:8px;color:#3fb950;padding:10px;font-size:13px;}"
            "QPushButton:hover{background:rgba(63,185,80,.3);}"
        )
        rw.addWidget(reg_msg); rw.addWidget(btn_do_reg); rw.addStretch()
        layout.addWidget(reg_widget)

        btn_cancel = QPushButton("Cancel"); layout.addWidget(btn_cancel)

        # Switch logic
        def _show_login():
            btn_login.setChecked(True); btn_register.setChecked(False)
            login_widget.setVisible(True); reg_widget.setVisible(False)
        def _show_reg():
            btn_login.setChecked(False); btn_register.setChecked(True)
            login_widget.setVisible(False); reg_widget.setVisible(True)
        btn_login.clicked.connect(_show_login)
        btn_register.clicked.connect(_show_reg)
        if not self._auth.has_any_user():
            _show_reg()

        # Password login
        def _do_pw_login():
            pw = pw_input.text().strip()
            if not pw: return
            user = self._auth.verify_passphrase(pw)
            if user:
                self._on_auth_result({"state": AuthState.VERIFIED,
                                      "reason": f"Welcome back, {user.name}"})
                dialog.close()
            else:
                pw_msg.setText("Wrong password, or this password is not bound to any account")
        btn_pw_login.clicked.connect(_do_pw_login)
        pw_input.returnPressed.connect(_do_pw_login)

        # Register
        def _do_register():
            name = name_input.text().strip()
            if not name:
                reg_msg.setText("Please enter a name"); return
            pw  = pw2_input.text().strip()   if chk_pw.isChecked() else ""
            pw2 = pw2_confirm.text().strip() if chk_pw.isChecked() else ""
            if chk_pw.isChecked() and not pw:
                reg_msg.setText("Please set a passphrase"); return
            if chk_pw.isChecked() and pw != pw2:
                reg_msg.setText("Passwords do not match"); return
            if not chk_face.isChecked() and not chk_pw.isChecked():
                reg_msg.setText("Please select at least one auth method"); return
            user = self._auth.create_user(name=name, passphrase=pw)
            if chk_face.isChecked():
                self._auth.add_face_method(user.user_id)
            self._auth.login(user)
            self._on_auth_result({"state": AuthState.VERIFIED,
                                  "reason": f"Account created successfully, welcome {name}!"})
            if chk_face.isChecked():
                self.chat_page.add_ai_message(
                    f"📷 Please go to 👁️ Face Recognition page, click \"Register Face\","
                    f"fill User ID {user.user_id} to complete registration."
                )
            dialog.close()
        btn_do_reg.clicked.connect(_do_register)
        btn_cancel.clicked.connect(dialog.close)
        dialog.show()


    # ---- Message Handling ----
    def _on_simlife_toggled(self, enabled: bool):
        """SimLife scene mode switch: sync to agent"""
        if self.agent:
            self.agent.simlife_mode = enabled
        self.statusBar().showMessage(
            "🌱 Entered SimLife scene mode" if enabled
            else "Exited SimLife scene mode", 3000
        )

    def _on_message(self, text: str):
        if self._worker and self._worker.isRunning():
            return

        self.chat_page.add_user_message(text)
        self._thinking_lbl = self.chat_page.add_thinking_indicator()
        self._status_mode.setText("🔄 Processing…")

        # VRM: user sends message → curious expression + start talking animation
        vrm = getattr(self.chat_page, "vrm_widget", None)
        if vrm:
            try:
                from vrm_module.emotion_bridge import translate
                name, val = translate("curious", 0.5)
                vrm.set_emotion(name, val)
                vrm.set_speaking(True)
            except Exception:
                pass

        self._worker = AGIWorker(self.agent, text)
        self._worker.finished.connect(self._on_result)
        self._worker.error.connect(self._on_error)
        self._worker.confirm_requested.connect(self._on_confirm_requested)
        self._worker.start()

    def _on_confirm_requested(self, tool_name: str, params: dict):
        """Main thread slot: pop confirmation dialog and return result to worker thread"""
        box = QMessageBox()
        box.setWindowTitle("⚠️  High-Risk Operation Confirmation")
        box.setText(
            f"<b>B-layer requested high-risk tool execution</b><br><br>"
            f"Tool: <code>{tool_name}</code><br><br>"
            f"Params: <pre>{json.dumps(params, ensure_ascii=False, indent=2)[:400]}</pre>"
        )
        box.setInformativeText("This operation may be irreversible. Allow execution?")
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setStyleSheet(DARK_QSS)
        result = (box.exec() == QMessageBox.StandardButton.Yes)
        self._worker.set_confirm_result(result)

    def _on_result(self, result: dict):
        self.chat_page.remove_thinking_indicator()
        response_text = result.get("response", "")

        # Plan D: add emoji prefix based on emotion
        _emoji_map = {
            "joy": "😊", "happy": "😊", "sadness": "😔", "sad": "😔",
            "anger": "😤", "angry": "😤", "fear": "😨", "scared": "😨",
            "surprise": "😲", "surprised": "😲", "curious": "🤔",
            "nostalgic": "😌", "trust": "🤝", "neutral": "",
            "calm": "😌", "excited": "🤩", "confused": "😕",
            "bored": "😑", "anxious": "😰", "love": "🥰",
            "gratitude": "🙏", "pride": "😄", "shame": "😳",
        }
        e_dict = result.get("emotion") or {}
        primary = e_dict.get("primary", "neutral")
        intensity = e_dict.get("intensity", 0)
        emoji = _emoji_map.get(primary, "")
        if emoji and intensity < 0.3:
            emoji = ""
        if emoji:
            response_text = f"{emoji} {response_text}"

        self.chat_page.add_ai_message(
            response_text,
            meta={
                "emotion":    result.get("emotion"),
                "task_type":  result.get("task_type"),
                "tools_used": result.get("tools_used", []),
                "tool_steps": result.get("tool_steps", []),
                "stored":     bool(result.get("stored_ids"))
            }
        )

        # Detect image results in tool calls, auto-show image bubbles
        import os
        for step in result.get("tool_steps", []):
            tool_name = step.get("tool", "")
            step_result = step.get("result") or {}
            if tool_name in ("generate_image", "generate_image_comfy") and step_result.get("ok"):
                img_path = step_result.get("image_path", "")
                if img_path and os.path.isfile(img_path):
                    self.chat_page._show_image_bubble(img_path, is_user=False)
        # Update status bar
        if result.get("emotion"):
            e = result["emotion"]
            self._status_emotion.setText(
                f"Emotion: {e.get('primary','?')} "
                f"({int(e.get('intensity',0)*10)}/10)"
            )
            # VRM: update expression
            vrm = getattr(self.chat_page, "vrm_widget", None)
            if vrm:
                try:
                    from vrm_module.emotion_bridge import translate
                    name, val = translate(
                        e.get("primary", "neutral"),
                        e.get("intensity", 0)
                    )
                    vrm.set_emotion(name, val)
                    vrm.set_speaking(False)
                except Exception:
                    pass
        else:
            # VRM: back to idle when no emotion
            vrm = getattr(self.chat_page, "vrm_widget", None)
            if vrm:
                try:
                    vrm.set_speaking(False)
                except Exception:
                    pass
        self._status_mode.setText("Ready")
        self._update_memory_count()

        # TTS auto read
        try:
            cfg = load_config()
            if cfg.get("tts_enabled", False) and response_text:
                from engine.tts_engine import get_tts
                tts = get_tts()
                tts.set_voice(cfg.get("tts_voice", "zh-CN-XiaoxiaoNeural"))
                tts.set_rate(cfg.get("tts_rate", 0))
                tts.speak(response_text)
        except Exception as e:
            print(f"[TTS] Auto read exception: {e}")

    def _on_error(self, err: str):
        self.chat_page.remove_thinking_indicator()
        self.chat_page.add_ai_message(f"❌ Error: {err}")
        self._status_mode.setText("Ready")
        # VRM: restore idle on error
        vrm = getattr(self.chat_page, "vrm_widget", None)
        if vrm:
            try:
                vrm.set_speaking(False)
                vrm.set_emotion("neutral", 0.5)
            except Exception:
                pass

    def _check_offline_messages(self):
        """Check and display offline messages at startup"""
        try:
            from simlife.offline_messages import on_startup
            messages = on_startup()
            if not messages:
                return

            self._status_mode.setText("Loading offline messages...")
            self._offline_msg_queue = list(messages)
            self._offline_msg_idx = 0
            QTimer.singleShot(800, self._show_next_offline_message)
        except Exception as e:
            print(f"[Offline] Message load skipped: {e}")

    def _show_next_offline_message(self):
        """Display offline messages one by one"""
        if not hasattr(self, "_offline_msg_queue"):
            return
        if self._offline_msg_idx >= len(self._offline_msg_queue):
            self._status_mode.setText("Ready")
            return

        msg = self._offline_msg_queue[self._offline_msg_idx]
        self._offline_msg_idx += 1

        label = f"[Offline · {msg['timestamp']}]"
        self.chat_page.add_ai_message(f"{label}\n{msg['text']}")

        # Next message at 1.5-3 second interval
        if self._offline_msg_idx < len(self._offline_msg_queue):
            delay = random.randint(1500, 3000)
            QTimer.singleShot(delay, self._show_next_offline_message)
        else:
            self._status_mode.setText("Ready")

    def _on_tab_changed(self, idx: int):
        if idx == 1:   # Memory
            self.memory_page.load()
        elif idx == 5: # Face Recognition
            self.face_page._load_existing_accounts()
        elif idx == 6: # User Profile
            self.profile_page.load()
        elif idx == 7: # Memory Graph
            self.graph_page.load()
        elif idx == 8: # Active Learning
            self.learner_page._load_cognitions()

    def _on_learn_requested(self, topics: list):
        """Active learning button trigger, run in background thread"""
        import threading
        def _run():
            try:
                if self.agent and hasattr(self.agent, "growth") and self.agent.growth:
                    growth = self.agent.growth
                    def _log(msg):
                        # Safely update UI through signal
                        QTimer.singleShot(0, lambda m=msg: self.learner_page.on_learn_log(m))
                    growth.learn_from_web(topics=topics, log_callback=_log)
                else:
                    QTimer.singleShot(0, lambda: self.learner_page.on_learn_log(
                        "⚠️ AGI not ready, please complete initialization first"))
            except Exception as e:
                QTimer.singleShot(0, lambda: self.learner_page.on_learn_log(f"❌ Error: {e}"))
            finally:
                QTimer.singleShot(0, self.learner_page.on_learn_done)
        threading.Thread(target=_run, daemon=True).start()

    def _on_settings_changed(self, cfg: dict):
        # Rebuild agent to apply new API key
        pass  # Handled by main program

    def _update_memory_count(self):
        try:
            from engine.db_guard import guarded_connect
            with guarded_connect(self.db_file) as conn:
                n = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            self._status_mem.setText(f"Memory: {n}")
        except Exception:
            pass

    # ---- Receive Screenshot Results ----
    def receive_screenshot_text(self, text: str):
        """Inject OCR result into input box"""
        self.chat_page.fill_input(f"[Screenshot OCR]\n{text}")
        self.activateWindow()
        self._tabs.setCurrentIndex(0)

    # ---- Close Behavior ----
    def closeEvent(self, event):
        cfg = load_config()
        if cfg.get("tray_minimize", True):
            event.ignore()
            self.hide()
        else:
            event.accept()
