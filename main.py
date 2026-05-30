"""
AGI Desktop Application - Unified Entry Point
All modules are self-contained, no external project dependencies
"""

import sys
import os
import json
import random
import time
import threading
from pathlib import Path
from datetime import datetime

# Add project root to path (ensure engine / ui / desktop can be found)
APP_DIR = Path(__file__).parent
sys.path.insert(0, str(APP_DIR))


def _default_font():
    """Pick a reasonable default font based on platform"""
    if sys.platform == "win32":
        return "Microsoft YaHei UI"
    elif sys.platform == "darwin":
        return "PingFang SC"
    else:
        return "Noto Sans CJK SC"

# Import core Qt modules (error here means PyQt6 is not installed)
try:
    from PyQt6.QtCore    import Qt, QObject, QThread, pyqtSignal, QTimer
    from PyQt6.QtWidgets import QApplication, QMessageBox, QSplashScreen
    from PyQt6.QtGui     import QFont, QPixmap, QPainter, QColor
except ImportError as e:
    print(f"[FATAL] PyQt6 not installed: {e}")
    print("Please run: pip install PyQt6")
    input("Press Enter to exit...")
    sys.exit(1)

# Import app modules (error here means project files have issues)
try:
    from desktop.config  import APP_NAME, APP_VERSION, load_config, save_config, DARK_QSS, DB_FILE
    from desktop.system  import SystemTray, GlobalHotkey, AutoStart
    from desktop.screenshot import ScreenshotSelector, OCRThread
    from ui.main_window  import MainWindow, AGIWorker
    from ui.float_window import FloatingWindow
except Exception as e:
    import traceback
    err = traceback.format_exc()
    print(f"[STARTUP ERROR] Module loading failed:\n{err}")
    # Try to show popup
    try:
        _app = QApplication(sys.argv)
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setWindowTitle("Module Load Failed")
        msg.setText(f"<b>Startup failed:</b><br>{str(e)}")
        msg.setDetailedText(err)
        msg.exec()
    except Exception:
        input("Press Enter to exit...")
    sys.exit(1)


# -- Emotion -> emoji prefix mapping --
EMOJI_MAP = {
    "joy":       "😊", "happy":     "😊",
    "sadness":   "😔", "sad":       "😔",
    "anger":     "😤", "angry":     "😤",
    "fear":      "😨", "scared":    "😨",
    "surprise":  "😲", "surprised": "😲",
    "curious":   "🤔", "nostalgic": "😌",
    "trust":     "🤝", "neutral":   "",
    "calm":      "😌", "excited":   "🤩",
    "confused":  "😕", "bored":     "😑",
    "anxious":   "😰", "love":      "🥰",
    "gratitude": "🙏", "pride":     "😄",
    "shame":     "😳", "disgust":   "🤢",
}


def _emotion_emoji(emotion: dict) -> str:
    """Return emoji prefix based on emotion dict"""
    if not emotion:
        return ""
    primary = emotion.get("primary", "neutral")
    intensity = emotion.get("intensity", 0)
    emoji = EMOJI_MAP.get(primary, "")
    if emoji and intensity < 0.3:
        return ""
    return emoji


# -- Startup splash --
def make_splash() -> QSplashScreen:
    px = QPixmap(480, 280)
    px.fill(QColor("#0d1117"))
    p = QPainter(px)
    p.setPen(QColor("#58a6ff"))
    p.setFont(QFont(_default_font(), 36, QFont.Weight.Bold))
    p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "🧠 AGI")
    p.setPen(QColor("#8b949e"))
    p.setFont(QFont(_default_font(), 13))
    from PyQt6.QtCore import QRect
    p.drawText(
        QRect(0, 170, 480, 40),
        Qt.AlignmentFlag.AlignCenter,
        "Cognitive Simulation System - Initializing..."
    )
    p.setPen(QColor("#30363d"))
    p.drawRect(0, 0, 479, 279)
    p.end()
    splash = QSplashScreen(px)
    splash.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    return splash


# -- AGI Core async loader thread --
class EngineLoader(QThread):
    """
    Initialize AGI core in background thread
    UI shows first, core activates after loading
    """
    ready  = pyqtSignal(object)   # Load success, return agent
    failed = pyqtSignal(str)      # Load failed, return error message

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg

    def run(self):
        try:
            # Database protection: integrity check + backup + WAL + migration
            from engine.db_guard import init_guard
            init_guard(DB_FILE)

            from engine.models         import PersonalityCore
            from engine.memory         import MemoryStore
            from engine.memory_manager import HierarchicalMemoryManager
            from engine.association    import MemoryAssociationNetwork
            from engine.llm_client     import create_client
            from engine.executor       import BLayerExecutor
            from engine.agent          import ConsciousnessAgent
            from engine.user_profile   import UserProfileManager
            from engine.learner        import GrowthEngine, FormedCognitionStore
            from engine.auth           import AuthManager
            from desktop.config        import PERSONALITY_FILE

            # Personality
            if Path(PERSONALITY_FILE).exists():
                with open(PERSONALITY_FILE, encoding="utf-8") as f:
                    personality = PersonalityCore.from_dict(json.load(f))
            else:
                personality = PersonalityCore(
                    name="AGI Assistant", worldview="Stay curious, live seriously"
                )

            # LLM client
            provider = self.cfg.get("api_provider", "deepseek")
            llm = create_client(
                api_key      = self.cfg.get("api_key", "") or os.environ.get("DEEPSEEK_API_KEY", ""),
                provider     = provider,
                model        = self.cfg.get("llm_model", None),
                ollama_model = self.cfg.get("ollama_model", "qwen2.5:7b"),
                ollama_url   = self.cfg.get("ollama_url", "http://localhost:11434"),
            )

            # Language settings
            try:
                from engine.i18n import set_language
                set_language(self.cfg.get("language", "zh"))
            except Exception:
                pass

            # Memory + association network
            store  = MemoryStore(DB_FILE)
            net    = MemoryAssociationNetwork(DB_FILE)
            memory = HierarchicalMemoryManager(store, net, llm_client=llm)

            executor = BLayerExecutor(
                llm_client=llm,
                confirm_callback=None,  # Injected by main thread at runtime
                max_tool_steps=8,
                verbose=True
            )

            # User profile (shares same file as memory DB)
            user_profile = UserProfileManager(DB_FILE)

            # Growth engine (experience cognition + personality drift + active learning)
            growth = GrowthEngine(
                db_path=DB_FILE,
                personality_file=str(PERSONALITY_FILE),
                llm_client=llm
            )
            cognition = FormedCognitionStore(DB_FILE)

            # Auth manager
            auth = AuthManager(DB_FILE)

            # SimLife client (optional, SimLife not initialized does not affect main system)
            simlife_client = None
            try:
                from engine.simlife_client import SimLifeClient
                _sl = SimLifeClient()
                if _sl.is_available():
                    simlife_client = _sl
                    print("[SimLife] Life status module connected")
            except Exception as e:
                print(f"[SimLife] Not enabled ({e})")

            # SimLife backend auto-start (background thread, no need for frontend)
            try:
                from simlife.backend.main import app as simlife_app
                import uvicorn as _uvicorn
                def _run_simlife():
                    _uvicorn.run(simlife_app, host="127.0.0.1", port=8769,
                                  log_level="warning", access_log=False)
                _simlife_thread = threading.Thread(target=_run_simlife, daemon=True)
                _simlife_thread.start()
                print("[SimLife] Backend service started in background (port 8769)")
            except Exception as e:
                print(f"[SimLife] Backend auto-start failed ({e}), falling back to file read mode")

            # SimLife new user guide: detect if initialized
            if not simlife_client or not simlife_client.is_available():
                print()
                print("=" * 56)
                print("  [SimLife] Life simulation module not yet initialized")
                print("  Please open http://127.0.0.1:8769 in your browser")
                print('  Fill in basic info and click "Start Generation"')
                print("  Restart the program after initialization to take effect")
                print("=" * 56)
                print()

            agent = ConsciousnessAgent(
                personality=personality,
                memory_manager=memory,
                b_layer_executor=executor,
                user_profile=user_profile,
                growth_engine=growth,
                cognition_store=cognition,
                auth_manager=auth,
                simlife_client=simlife_client,
                verbose=True
            )
            self.ready.emit(agent)

        except Exception as e:
            import traceback
            self.failed.emit(f"{e}\n\n{traceback.format_exc()}")


# -- Cross-thread scheduled message bridge --
class TimedMsgBridge(QObject):
    """Receive scheduled task signals from any thread, execute UI updates in main thread"""
    signal = pyqtSignal(str)


# -- Main controller --
class AGIApp:

    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setApplicationName(APP_NAME)
        self.app.setApplicationVersion(APP_VERSION)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setStyleSheet(DARK_QSS)
        self.app.setFont(QFont(_default_font(), 9))

        self.cfg    = load_config()
        self.agent  = None
        self._float_worker = None
        self._ocr_thread   = None
        self._screenshot_selector = None

        # -- Splash --
        self.splash = make_splash()
        self.splash.show()
        self.app.processEvents()

        # -- UI (build first, core loads in background) --
        self.main_win  = MainWindow(agent=None, db_file=DB_FILE)
        self.float_win = FloatingWindow(
            opacity=self.cfg.get("float_opacity", 0.95)
        )
        self.tray   = SystemTray()
        self.hotkey = GlobalHotkey()

        # -- Proactive speech state (global, shared across windows) --
        self._last_chat_time = time.time()
        self._proactive_wait_minutes = None
        self._proactive_scheduled_at = None
        self._proactive_count_today = 0
        self._proactive_date = datetime.now().date()
        self._pending_proactive_msg = None   # Latest pending proactive message

        # -- Scheduled task message bridge --
        self._timed_bridge = TimedMsgBridge()
        self._timed_bridge.signal.connect(self._show_timed_in_ui)

        # -- Image generation state (~every 3h) --
        self._last_image_time = time.time()      # Last image generation time
        self._image_gen_interval = None           # Random interval (2.5~3.5 hours)
        self._image_gen_count_today = 0
        self._image_gen_date = datetime.now().date()

        self._connect_signals()
        self._register_hotkeys()

        # -- Idle detection timer (global, independent of float window) --
        self._idle_timer = QTimer()
        self._idle_timer.timeout.connect(self._check_proactive)
        self._idle_timer.start(60_000)  # Check every 60s

        # -- Image generation timer (check every 5 min) --
        self._image_timer = QTimer()
        self._image_timer.timeout.connect(self._check_image_gen)
        self._image_timer.start(5 * 60_000)

        # -- Background engine load --
        self.splash.showMessage(
            "  Initializing AGI core...",
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft,
            QColor("#58a6ff")
        )
        self._loader = EngineLoader(self.cfg)
        self._loader.ready.connect(self._on_engine_ready)
        self._loader.failed.connect(self._on_engine_failed)
        self._loader.start()

    # -- Engine load callbacks --
    def _on_engine_ready(self, agent):
        self.agent = agent
        self.main_win.agent = agent
        self.float_win.agent = agent  # Inject agent for float window proactive speech

        # Inject simlife_client to float window
        if hasattr(agent, 'simlife') and agent.simlife:
            self.float_win.simlife_client = agent.simlife
            # SimLife status refresh timer (every 30s)
            self._simlife_timer = QTimer()
            self._simlife_timer.timeout.connect(self._refresh_simlife_float)
            self._simlife_timer.start(30_000)
            print("[SimLife] Float window status panel enabled")

        # Share LLM client with coder page
        self.main_win.coder_page.set_llm(agent.b.llm)

        # Start memory decay timer (every 2 hours)
        self._decay_timer = QTimer()
        self._decay_timer.timeout.connect(self._apply_memory_decay)
        self._decay_timer.start(2 * 60 * 60 * 1000)   # 2 hours

        self.splash.finish(self.main_win)
        self.tray.notify(APP_NAME, "AGI Core Ready ✅")

        from engine.memory import MemoryStore, _embedding_mode
        stats = MemoryStore(DB_FILE).get_stats()
        print(f"[Ready] Character: {agent.personality.name}")
        print(f"[Ready] Memory: {stats['total']} entries")
        print(f"[Ready] Vector mode: {_embedding_mode}")

        # Start auth verification (background thread, non-blocking)
        if agent.auth:
            self.main_win.start_auth_verification(agent.auth)

        # Start mobile Web service (shared agent instance)
        try:
            from server import start_server
            start_server(agent=agent, auth_manager=agent.auth)
        except ImportError as e:
            print(f"[Mobile] Web service not started (missing deps): {e}")

        # Start scheduled task scheduler
        try:
            from engine.task_scheduler import init_scheduler
            self._task_scheduler = init_scheduler(on_trigger=self._on_timed_task_trigger)
            result = self._task_scheduler.catchup_overdue()
            if result["catchup"] > 0 or result["expired"] > 0:
                print(f"[Scheduled Task] Catch-up on boot: {result['catchup']}, expired: {result['expired']}")
            self._task_scheduler.start()
            print("[Scheduled Task] Scheduler started (smart wait mode)")
        except Exception as e:
            print(f"[Scheduled Task] Scheduler start failed: {e}")

        # Start web chat service (Claude style, port 18766)
        try:
            from web_server import start_web_chat
            start_web_chat(
                agent=agent,
                auth_manager=agent.auth,
                scheduler=getattr(self, '_task_scheduler', None),
            )
        except ImportError as e:
            print(f"[Web Chat] Web service not started (missing deps): {e}")

    def _apply_memory_decay(self):
        """Scheduled memory decay"""
        try:
            from engine.memory import MemoryStore
            store = MemoryStore(DB_FILE)
            store.apply_decay(decay_rate=0.995)
            print("[Memory Decay] Executed")
        except Exception as e:
            print(f"[Memory Decay] Failed: {e}")

    def _on_timed_task_trigger(self, task: dict):
        """Scheduled task callback -- called in background thread, updates UI via signal bridge"""
        action = task.get("action", "speak")
        content = task.get("content", "")
        params = task.get("action_params", {})

        if action == "speak":
            message = params.get("message", content)
            print(f"[Scheduled Task] Speak: {message[:50]}")
            self._deliver_timed_message(message, content)
        elif action == "tool":
            tool_name = params.get("tool_name", "")
            tool_params = params.get("tool_params", {})
            print(f"[Scheduled Task] Execute tool: {tool_name}")
            try:
                from engine.tools import execute_tool
                result = execute_tool(tool_name, tool_params)
                print(f"[Scheduled Task] Tool result: {result}")
                if result.get("ok"):
                    msg = result.get("message", f"{tool_name} executed successfully")
                    self._deliver_timed_message(f"(Scheduled) {msg}", content)
            except Exception as e:
                print(f"[Scheduled Task] Tool execution failed: {e}")

    def _deliver_timed_message(self, message: str, task_content: str):
        """Push scheduled task message to UI (thread-safe via pyqtSignal)"""
        self._proactive_count_today += 1
        self._last_chat_time = time.time()
        self._pending_proactive_msg = message

        if self.agent:
            self.agent.conversation_history.append(
                {"role": "assistant", "content": f"[Scheduled Reminder]{message}"}
            )
            self.agent._proactive_context = f"Scheduled task triggered: {task_content}"

        self._timed_bridge.signal.emit(message)

    def _show_timed_in_ui(self, message: str):
        """Display scheduled message to visible windows in main thread"""
        print(f"[Scheduled Reminder] {message}")
        if self.float_win.isVisible():
            self.float_win.add_message(message, is_user=False, is_proactive=True)
            if not self.float_win._expanded:
                self.float_win._expand()
        elif self.main_win.isVisible():
            self.main_win.chat_page.add_ai_message(
                message, meta={"proactive": True}
            )
        else:
            self.tray.notify("⏰ Scheduled Reminder", message)

    def _refresh_simlife_float(self):
        """Periodically refresh float window SimLife status panel"""
        if self.float_win.isVisible():
            self.float_win.refresh_simlife_state()

    # -- Global proactive speech --
    def _is_simlife_sleeping(self) -> bool:
        """Check if SimLife character is sleeping (late night or SLEEPING scene)"""
        # Midnight 0:00-6:59 treated as night, no proactive interruption
        hour = datetime.now().hour
        if hour < 7:
            return True
        # Check SimLife current scene
        if self.agent and self.agent.simlife:
            try:
                state = self.agent.simlife.get_state(use_api=True) or self.agent.simlife._read_file_state()
                if state:
                    scene = state.get("current_scene", "") or state.get("scene", "")
                    if "SLEEPING" in scene:
                        return True
            except Exception:
                pass
        return False

    def _update_chat_time(self):
        """Called when user sends message in any window, reset idle timer"""
        self._last_chat_time = time.time()
        self._proactive_wait_minutes = None

    def _on_main_message(self, text: str):
        """Main window message: reset idle timer + inject proactive speech context"""
        self._update_chat_time()
        if self._pending_proactive_msg and self.agent:
            self.agent._proactive_context = self._pending_proactive_msg
            self._pending_proactive_msg = None
        # Update user last active time (for offline message calculation)
        try:
            from simlife.offline_messages import _save_last_online
            _save_last_online()
        except Exception:
            pass

    def _check_proactive(self):
        """Check every minute whether to speak proactively"""
        if not self.agent:
            return
        # At least one of main or float window must be visible
        if not self.main_win.isVisible() and not self.float_win.isVisible():
            return

        # Do not speak proactively when SimLife is sleeping
        if self._is_simlife_sleeping():
            return

        # Daily limit: 5
        today = datetime.now().date()
        if today != self._proactive_date:
            self._proactive_date = today
            self._proactive_count_today = 0
        if self._proactive_count_today >= 5:
            return

        idle_minutes = (time.time() - self._last_chat_time) / 60

        # Idle less than 30 min, reset plan
        if idle_minutes < 30:
            self._proactive_wait_minutes = None
            return

        # When idle just reached 30 min, randomly schedule a wait time
        if self._proactive_wait_minutes is None:
            self._proactive_wait_minutes = random.randint(1, 30)
            self._proactive_scheduled_at = time.time()
            return

        # Check if scheduled time has arrived
        waited = (time.time() - self._proactive_scheduled_at) / 60
        if waited < self._proactive_wait_minutes:
            return

        # Trigger proactive speech
        self._proactive_wait_minutes = None
        self._do_proactive_speak()

    def _do_proactive_speak(self):
        """Generate proactive message in sub-thread"""
        if not self.agent:
            return

        class _ProactiveWorker(QThread):
            done = pyqtSignal(str)
            def __init__(self, agent):
                super().__init__()
                self.agent = agent
            def run(self):
                try:
                    msg = self.agent.proactive_message()
                    if msg:
                        self.done.emit(msg)
                    else:
                        print("[Proactive Speech] LLM returned empty, skipped")
                except Exception as e:
                    print(f"[Proactive Speech] Generation failed: {e}")

        self._proactive_worker = _ProactiveWorker(self.agent)
        self._proactive_worker.done.connect(self._on_proactive_message)
        self._proactive_worker.start()

    def _on_proactive_message(self, message: str):
        """Display proactive message to visible windows (with reply checkbox)"""
        self._proactive_count_today += 1
        self._last_chat_time = time.time()
        print(f"[Proactive Speech] #{self._proactive_count_today} today: {message[:50]}")

        # Record pending, next reply from any window will auto-associate
        self._pending_proactive_msg = message

        # Append to conversation history, so process() sees context when user replies and stores to memory
        if self.agent:
            self.agent.conversation_history.append(
                {"role": "assistant", "content": f"[Proactive message]{message}"}
            )

        # Show on float window first (if visible), else on main window
        if self.float_win.isVisible():
            self.float_win.add_message(message, is_user=False, is_proactive=True)
            if not self.float_win._expanded:
                self.float_win._expand()
        elif self.main_win.isVisible():
            self.main_win.chat_page.add_ai_message(
                message, meta={"proactive": True}
            )
        else:
            # Both windows hidden, use tray notification
            self.tray.notify("AGI Proactive Message", message)

    def _on_proactive_replied(self, proactive_msg: str, user_reply: str):
        """Proactive message replied: set flag so process() prepends context when storing"""
        if user_reply and self.agent:
            self.agent._proactive_context = proactive_msg

    # -- Proactive image generation (~every 3h) --
    def _check_image_gen(self):
        """Check periodically whether to generate image"""
        if not self.agent:
            return

        # Do not generate image when SimLife is sleeping
        if self._is_simlife_sleeping():
            return

        # Daily limit: 3 images
        today = datetime.now().date()
        if today != self._image_gen_date:
            self._image_gen_date = today
            self._image_gen_count_today = 0
        if self._image_gen_count_today >= 3:
            return

        # Randomize interval on first run (2.5~3.5 hours)
        if self._image_gen_interval is None:
            self._image_gen_interval = random.randint(150, 210) * 60  # seconds

        elapsed = time.time() - self._last_image_time
        if elapsed < self._image_gen_interval:
            return

        # Trigger image generation
        self._image_gen_interval = random.randint(150, 210) * 60
        self._last_image_time = time.time()
        self._do_generate_image()

    def _do_generate_image(self):
        """Generate image in sub-thread"""
        if not self.agent:
            return
        personality_dict = self.agent.personality.to_dict()

        # Get SimLife current state, pass to image generator
        simlife_context = ""
        if self.agent.simlife:
            try:
                simlife_context = self.agent.simlife.format_for_prompt()
            except Exception:
                pass

        class _ImageGenWorker(QThread):
            done = pyqtSignal(str, str)   # (image_path, caption)
            fail = pyqtSignal(str)

            def __init__(self, personality_dict, simlife_ctx):
                super().__init__()
                self.personality_dict = personality_dict
                self.simlife_ctx = simlife_ctx

            def run(self):
                try:
                    from engine.image_gen import generate_and_download
                    result = generate_and_download(self.personality_dict, simlife_context=self.simlife_ctx)
                    if result:
                        prompt, image_path, image_type = result
                        caption = self._make_caption(image_type)
                        self.done.emit(image_path, caption)
                    else:
                        self.fail.emit("Image generation returned empty result")
                except Exception as e:
                    self.fail.emit(str(e))

            @staticmethod
            def _make_caption(image_type: str) -> str:
                """Generate caption based on image type"""
                import random
                if image_type == "selfie":
                    captions = [
                        "Took a photo, let me show you~",
                        "Feeling good today, sharing a selfie ✨",
                        "Just took this, looks pretty good~",
                        "Couldn't resist taking a photo of this scene",
                        "Here is what I look like now~",
                    ]
                else:
                    captions = [
                        "This scenery is so beautiful, sharing with you ✨",
                        "I saw such a beautiful view~",
                        "The sky looks especially beautiful today",
                        "This scene is so healing, sharing with you",
                        "The scenery I captured, wanted to share with you",
                    ]
                return random.choice(captions)

        self._image_worker = _ImageGenWorker(personality_dict, simlife_context)
        self._image_worker.done.connect(self._on_image_generated)
        self._image_worker.fail.connect(lambda e: print(f"[Image Gen] Failed: {e}"))
        self._image_worker.start()

    def _on_image_generated(self, image_path: str, caption: str):
        """Image generation complete, display to chat area"""
        self._image_gen_count_today += 1
        print(f"[Image Gen] #{self._image_gen_count_today} today")

        # Append to conversation history
        if self.agent:
            self.agent.conversation_history.append(
                {"role": "assistant", "content": f"[Shared image]{caption}"}
            )

        # Display to visible windows
        if self.float_win.isVisible():
            self.float_win.add_message(caption, is_user=False, is_proactive=True)
            if not self.float_win._expanded:
                self.float_win._expand()
        elif self.main_win.isVisible():
            chat = self.main_win.chat_page
            # Show text message first
            chat.add_ai_message(caption, meta={"proactive": True})
            # Then show image bubble (AI generated, left aligned)
            chat._show_image_bubble(image_path, is_user=False)
        else:
            # No visible window, use tray notification
            self.tray.notify("AGI shared an image with you", caption)

    def _on_engine_failed(self, err: str):
        self.splash.finish(self.main_win)
        print(f"[WARNING] AGI core load failed, entering degraded mode\n{err[:300]}")
        # Do not crash, just feature degradation -- UI opens normally
        self.tray.notify(
            APP_NAME,
            "AGI core not ready, please check API Key in settings",
            duration=5000
        )



    # -- Signal connections --
    def _connect_signals(self):
        self.tray.show_main.connect(self._show_main)
        self.tray.show_float.connect(self._show_float)
        self.tray.take_screenshot.connect(self._start_screenshot)
        self.tray.quit_app.connect(self._quit)

        self.float_win.message_sent.connect(self._float_message)
        self.float_win.screenshot_requested.connect(self._start_screenshot)
        self.float_win.proactive_replied.connect(self._on_proactive_replied)

        self.hotkey.triggered.connect(self._on_hotkey)
        self.main_win.settings_page.settings_changed.connect(self._on_settings)

        # Reset idle timer + inject proactive context on main window message
        self.main_win.chat_page.message_sent.connect(self._on_main_message)

    def _register_hotkeys(self):
        self.hotkey.register(
            "activate",   self.cfg.get("hotkey_activate",   "ctrl+shift+space"))
        self.hotkey.register(
            "screenshot", self.cfg.get("hotkey_screenshot",  "ctrl+shift+s"))

    # -- Window control --
    def _show_main(self):
        self.main_win.show()
        self.main_win.raise_()
        self.main_win.activateWindow()

    def _show_float(self):
        self.float_win.show()
        self.float_win.raise_()

    def _toggle_float(self):
        if self.float_win.isVisible():
            self.float_win.hide()
        else:
            self._show_float()

    def _on_hotkey(self, hid: str):
        if hid == "activate":
            self._toggle_float()
        elif hid == "screenshot":
            self._start_screenshot()

    # -- Screenshot + OCR --
    def _start_screenshot(self):
        self._screenshot_selector = ScreenshotSelector()
        self._screenshot_selector.captured.connect(self._on_captured)

    def _on_captured(self, pixmap, rect):
        self.tray.notify("Screenshot OCR", "Recognizing text...", 2000)
        if self.float_win.isVisible():
            self.float_win.add_message("[Screenshot captured, recognizing...]")
        self._ocr_thread = OCRThread(
            pixmap, self.cfg.get("ocr_language", "chi_sim+eng")
        )
        self._ocr_thread.finished.connect(self._on_ocr_done)
        self._ocr_thread.error.connect(
            lambda e: self.tray.notify("OCR Failed", e)
        )
        self._ocr_thread.start()

    def _on_ocr_done(self, text: str):
        self.main_win.receive_screenshot_text(text)
        if self.float_win.isVisible():
            self.float_win.add_message(f"Recognized: {text[:60]}...")
        self.tray.notify("Recognition done", f"{len(text)} chars")

    # -- Float window messages --
    def _float_message(self, text: str):
        if not self.agent:
            self.float_win.add_message(
                "AGI core not ready yet, please wait or check settings"
            )
            return

        self._update_chat_time()
        # Inject proactive speech context (if any)
        if self._pending_proactive_msg:
            self.agent._proactive_context = self._pending_proactive_msg
            self._pending_proactive_msg = None

        self.float_win.set_thinking(True)
        worker = AGIWorker(self.agent, text)

        def on_done(r):
            self.float_win.set_thinking(False)
            e = r.get("emotion", {})
            self.float_win.update_emotion(
                e.get("primary", "neutral"),
                e.get("intensity", 0.3)
            )
            emoji = _emotion_emoji(e)
            resp = r.get("response", "…")
            self.float_win.add_message(f"{emoji} {resp}" if emoji else resp)

        def on_err(err):
            self.float_win.set_thinking(False)
            self.float_win.add_message(f"❌ {err}")

        worker.finished.connect(on_done)
        worker.error.connect(on_err)
        worker.start()
        self._float_worker = worker   # Anti-GC

    # -- Settings changes --───────────────────────────────
    def _on_settings(self, cfg: dict):
        self.cfg = cfg
        save_config(cfg)
        # Re-register hotkeys
        self.hotkey.unregister_all()
        self._register_hotkeys()
        # Reload engine (new API Key)
        self._loader = EngineLoader(cfg)
        self._loader.ready.connect(self._on_engine_ready)
        self._loader.failed.connect(self._on_engine_failed)
        self._loader.start()
        self.tray.notify("Settings updated", "Reconnecting to API...")

    def _quit(self):
        self.hotkey.unregister_all()
        # Save last active time on exit (for offline message calculation)
        try:
            from simlife.offline_messages import _save_last_online
            _save_last_online()
        except Exception:
            pass
        self.app.quit()

    # -- Run --───────────────────────────────────
    def run(self) -> int:
        start_minimized = "--minimized" in sys.argv
        if not start_minimized:
            # Show main window after engine loads (max 15 sec wait)
            QTimer.singleShot(300, self._show_main)
        else:
            self.tray.notify(APP_NAME, "Running in background")

        print(f"[{APP_NAME} v{APP_VERSION}] Started")
        print(f"  Wake hotkey: {self.cfg.get('hotkey_activate')}")
        print(f"  Screenshot hotkey: {self.cfg.get('hotkey_screenshot')}")
        return self.app.exec()


def main():
    # Top-level exception catch: show popup for any startup error, no silent crash
    try:
        app = AGIApp()
        sys.exit(app.run())
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[STARTUP FAILED]\n{tb}")
        # Try to show error popup with Qt
        try:
            if not QApplication.instance():
                _app = QApplication(sys.argv)
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Icon.Critical)
            msg.setWindowTitle("Startup Failed")
            msg.setText(f"<b>Error occurred during AGI startup:</b><br><br>{str(e)}")
            msg.setDetailedText(tb)
            msg.setStandardButtons(QMessageBox.StandardButton.Ok)
            msg.exec()
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
