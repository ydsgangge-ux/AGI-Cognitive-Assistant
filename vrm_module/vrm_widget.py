"""
vrm_widget.py - PyQt6 QWebEngineView embed component

Display VRM avatar above right tool panel, fixed size, does not affect tool list below.
"""

import os
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtGui import QColor


class VRMWidget(QWidget):
    """
    VRM rendering panel, embed QWebEngineView to load Three.js page.
    Size configured via config, default 220x220.
    """

    WIDTH  = 220
    HEIGHT = 220

    def __init__(self, parent=None, width=220, height=220):
        super().__init__(parent)
        self.WIDTH = width
        self.HEIGHT = height
        self.setFixedWidth(self.WIDTH)
        self.setFixedHeight(self.HEIGHT)
        self.setStyleSheet("background:#0d1117;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Placeholder label (shown before WebEngine loads)
        self._placeholder = QLabel("")
        self._placeholder.setStyleSheet(
            "background:#0d1117;border:1px dashed #30363d;"
            "color:#484f58;font-size:11px;"
        )
        self._placeholder.setAlignment(
            __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.AlignmentFlag.AlignCenter
        )
        layout.addWidget(self._placeholder)

        self._web = None
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.timeout.connect(self._try_load_webengine)

        # Delay 500ms to attempt loading, avoid blocking startup
        self._load_timer.start(500)

    def _try_load_webengine(self):
        """Lazy load WebEngine, avoid affecting startup performance"""
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView

            layout = self.layout()
            layout.removeWidget(self._placeholder)
            self._placeholder.deleteLater()

            self._web = QWebEngineView()
            self._web.setStyleSheet(
                "QWebEngineView{background:transparent;border:none;}"
            )

            # Disable right-click menu
            self._web.setContextMenuPolicy(
                __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.ContextMenuPolicy.NoContextMenu
            )

            html_path = os.path.join(
                os.path.dirname(__file__), "static", "vrm_viewer.html"
            )
            if os.path.isfile(html_path):
                self._web.load(QUrl.fromLocalFile(
                    html_path.replace("\\", "/")
                ))
            else:
                print(f"[VRM] Render page not found: {html_path}")

            layout.addWidget(self._web)

        except ImportError:
            self._placeholder.setText("VRM: WebEngine\nNot installed")
            print("[VRM] PyQt6-WebEngine not installed, run: pip install PyQt6-WebEngine")
        except Exception as e:
            self._placeholder.setText("VRM: Load failed")
            print(f"[VRM] WebEngine load failed: {e}")

    def set_emotion(self, emotion: str, intensity: float = 1.0):
        """Drive VRM expression (called after emotion_bridge.translate generates params)"""
        if not self._web:
            return
        js = f"setEmotion('{emotion}', {intensity:.2f})"
        self._web.page().runJavaScript(js)

    def set_speaking(self, is_speaking: bool):
        """Trigger/stop talking animation"""
        if not self._web:
            return
        js = f"setSpeaking({str(is_speaking).lower()})"
        self._web.page().runJavaScript(js)
