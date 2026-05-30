# AGI Cognitive Assistant

<p align="center">
  <b>A desktop AI assistant that simulates human cognitive architecture</b><br>
  Hierarchical memory · Emotional weighting · Associative retrieval · Personality growth
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/PyQt6-Desktop_UI-green?logo=qt" alt="PyQt6">
  <img src="https://img.shields.io/badge/License-Apache_2.0-yellow" alt="License">
  <img src="https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey" alt="Platform">
</p>

<p align="center">
  English
</p>

<p align="center">
  <b>Repository</b>: <a href="https://github.com/ydsgangge-ux/AGI-Cognitive-Assistant">github.com/ydsgangge-ux/AGI-Cognitive-Assistant</a>
  &nbsp;|&nbsp;
  <b>Email</b>: <a href="mailto:ydsgangge@gmail.com">ydsgangge@gmail.com</a>
</p>

---

## Features

- **A/B Dual Architecture** — Layer A (consciousness) has personality, emotions, and judgment; Layer B (executor) calls LLM + tools
- **Dynamic Thinking Mode** — Perception layer auto-judges question complexity; simple questions get fast responses (saves tokens), complex questions enable deep reasoning (high quality). Three switchable modes (Auto / Always On / Always Off). Decision path observable in server logs
- **Hierarchical Memory** — SQLite + vector retrieval, three-tier storage (summary / outline / detail) + associative network + two-phase retrieval
- **User Profile** — Gradually accumulates personality traits, detects anomalous behavior, identity verification
- **28 Built-in Tools** — File operations, system control, web search, browser automation, OCR, coding agent, Office files, stocks, news, **AI image generation**
- **AI Image Generation** — Dual backend: ① pollinations.ai (free, no API Key) auto-generation; ② **ComfyUI local generation** (SDXL/NoobAI etc., supports LoRA, 4-step ultra-fast output). Supports dynamic outfit injection (SimLife wardrobe), travel scene injection, style switching (anime/realistic). Auto-config tool `setup_comfyui.py` — one-click path/port/model detection
- **SimLife Virtual Life** — Real-time scene engine (work / home / commute / outdoor / travel), daily events, mood system, NPC interaction, weather integration (Open-Meteo, free), holiday calendar, schedule management. Auto-starts with main app. First-time setup via built-in Web UI (`http://127.0.0.1:87659`)
- **SimLife World System** — Isekai-style communication! Beyond the default modern world, import custom worlds (fantasy / sci-fi / isekai). Generate setting packs via external LLMs (JSON), auto-injected into character generation, activity descriptions, and event systems. Built-in world template and generation prompts, one-click switching
- **Growth Engine** — Personality drift + active learning + experiential cognition (dedup merge + activity decay) — the AGI evolves through conversation
- **Mobile Web Client** — Built-in web server (FastAPI), chat from any phone browser, shares the same agent instance and memory as desktop
- **Proactive Conversation** — AGI initiates topics autonomously; full memory chain storage on user reply (system → user → AI). Proactive messages carry identity tags so AI correctly distinguishes its own output from user input
- **12 LLM Providers** — DeepSeek / OpenAI / Groq / Claude / Gemini / Qwen / Zhipu GLM / Doubao / Kimi / Ernie Bot / iFlytek Spark / Ollama (100% local)
- **Multi-language** — Chinese / English / Japanese / Korean / Spanish / Arabic
- **Voice Synthesis** — Microsoft Edge TTS, multiple voices
- **Face Recognition** — Multi-engine (InsightFace / face_recognition / OpenCV), multi-user identity
- **Desktop Integration** — System tray, global hotkeys, floating window, screenshot OCR, auto-start
- **VRM Virtual Avatar** — Embedded VRM 3D character panel with emotion sync (20 emotion mappings), speaking animation, breathing/blink lifelike animations, holographic visual style. Supports VRM 0.x / 1.0 models. Modular loading, graceful degradation when absent

---

## Quick Start

### Windows (recommended)

1. **Install Python 3.10+** from https://www.python.org/downloads/
   - **Must check** `Add Python to PATH`
2. **Double-click `install.bat`** — installs all dependencies
3. **Double-click `launch.bat`** — starts the app

Just two double-clicks after installing Python!

### Linux / macOS

```bash
# 1. Ensure Python 3.10+ and pip are installed
# Ubuntu: sudo apt install python3 python3-pip
# macOS:   brew install python3

# 2. Install dependencies
chmod +x install.sh launch.sh
./install.sh

# 3. Launch
./launch.sh
```

---

## Screenshots

| Main Chat | Tool Panel | Settings |
|:---------:|:----------:|:--------:|
| ![Main Chat](docs/screenshots/zhuduihua.png) | ![Tool Panel](docs/screenshots/ceshitai.jpg) | ![Settings](docs/screenshots/shezhi.jpg) |

---

## Project Structure

```
agi_app/
├── main.py                  # Entry point (PyQt6 desktop app)
├── server.py                # Mobile web server (FastAPI, shared Agent instance)
├── install.bat / install.sh # One-click install script
├── launch.bat / launch.sh   # Launch script
├── setup_comfyui.py         # ComfyUI auto-detection & config tool
├── workflow_api.json        # ComfyUI workflow (model/LoRA/sampling params)
├── build.py                 # PyInstaller packaging script
├── requirements.txt         # Python dependencies
│
├── engine/                  # AGI core engine
│   ├── models.py            # Data models (personality/memory/emotion/modality)
│   ├── memory.py            # SQLite vector memory store (CRUD + decay)
│   ├── memory_manager.py    # Hierarchical retrieval (two-phase retrieval)
│   ├── association.py       # Memory association network (directed weighted graph)
│   ├── agent.py             # A-layer consciousness (perception → memory → reasoning → tools → generation)
│   ├── executor.py          # B-layer tool execution loop (ReAct, max 8 steps)
│   ├── tools.py             # 28 tool functions
│   ├── image_gen.py         # AI image generation (pollinations.ai, selfies & scenery)
│   ├── coder.py             # Autonomous coding agent (write → run → fix loop)
│   ├── office_tools.py      # Office file tools (docx/xlsx/pptx/pdf)
│   ├── user_profile.py      # User profile (trait accumulation + anomaly detection)
│   ├── learner.py           # Growth engine (personality drift + active learning + cognitive dedup/decay)
│   ├── auth.py              # Multi-user authentication
│   ├── face_recognition_engine.py  # Face recognition (3-engine lazy loading)
│   ├── llm_client.py        # LLM client (DeepSeek/OpenAI/Groq/Claude/Gemini/Ollama)
│   ├── tts_engine.py        # Voice synthesis (Edge TTS / pyttsx3)
│   └── i18n.py              # Internationalization (6 languages)
│
├── desktop/                 # Desktop system layer
│   ├── config.py            # Config management, paths, QSS dark theme
│   ├── system.py            # System tray, global hotkeys, auto-start
│   └── screenshot.py        # Screenshot selector + OCR background thread
│
├── simlife/                 # SimLife virtual life simulation
│   ├── backend/             # FastAPI backend (auto-starts with main app)
│   │   ├── main.py          # Service entry + API routes (port 8769)
│   │   ├── world_engine.py  # Scene engine (schedule + weather + holidays)
│   │   ├── event_engine.py  # Daily/random/scheduled event system
│   │   ├── mood_engine.py   # Mood calculation (scene + events + weather)
│   │   ├── npc_engine.py    # NPC activation & interaction
│   │   ├── weather.py       # Open-Meteo weather (free, no API key)
│   │   ├── generator.py     # LLM-generated character/NPC cards (auto-injects world setting)
│   │   └── holiday_calendar.py  # Holiday + festival calendar
│   ├── frontend/            # Initialization Web UI (first-time character creation)
│   ├── data/                # Runtime data (character cards, world state, event library)
│   ├── worlds/              # World system
│   │   ├── world_manager.py       # World load/switch/inject manager
│   │   ├── world_setting_template.json  # 13-dimension world template
│   │   └── generate_world_prompt.md     # Prompt template for user-generated worlds
│   └── setup.py             # Standalone launcher
│
├── vrm_module/              # VRM avatar module (optional)
│   ├── __init__.py          # Safe loading entry (all exceptions caught)
│   ├── vrm_widget.py        # PyQt6 QWebEngineView component
│   ├── emotion_bridge.py    # Emotion mapping (AGI emotions → VRM BlendShape)
│   ├── static/              # Three.js rendering assets
│   │   ├── vrm_viewer.html  # Three.js + three-vrm render page
│   │   ├── three.module.js  # Three.js ES Module (offline)
│   │   ├── three-vrm.module.js  # three-vrm ES Module (offline)
│   │   └── model.vrm        # VRM model file (user-provided)
│   └── test_server.py       # Browser test server
│
└── ui/                      # UI layer (PyQt6)
    ├── main_window.py       # Main window (7 functional tabs)
    └── float_window.py      # Floating window (always-on-top, draggable, animated, proactive reply)
```

---

## First-Time Setup

After launching, go to the **Settings** tab to configure:

| Setting | Description |
|---------|-------------|
| **LLM Provider** | DeepSeek / OpenAI / Groq / Claude / Gemini / Ollama |
| **API Key** | Obtain from the provider's website (not needed for Ollama) |
| **Hotkeys** | Customize wake and screenshot shortcuts |
| **Language** | Chinese / English / Japanese / Korean / Spanish / Arabic |

### Supported LLM Providers

| Provider | API Key Source | Notes |
|----------|---------------|-------|
| **DeepSeek** | https://platform.deepseek.com | Recommended, affordable |
| **OpenAI** | https://platform.openai.com | GPT-4o-mini etc. |
| **Groq** | https://console.groq.com | Free tier, fast |
| **Claude** | https://console.anthropic.com | Anthropic |
| **Gemini** | https://aistudio.google.com | Google |
| **Ollama** | https://ollama.ai | 100% local, no key needed |

> **Tool calling**: DeepSeek / OpenAI / Groq / Qwen / Zhipu GLM / Doubao / Kimi / Ernie Bot / iFlytek Spark use native function calling. Claude / Gemini / Ollama use ReAct prompt parsing (tool descriptions embedded in prompts, JSON output). All providers support real tool execution.

---

## Architecture Overview

```
User Input
    │
    ▼
① Perception (LLM) → Emotion / Task type / Topic tags / Complexity (simple/complex)
    │
    ▼
② Two-Phase Memory Retrieval
   Phase 1: Vector search for outlines + associative ripple spread
   Phase 2: Fetch details by outline direction
   + User Profile (always injected)
    │
    ▼
③ Reasoning (LLM) → Decide tool usage, storage strategy
   └ Complex problems enable deep thinking, simple ones get fast responses
    │
    ├── Needs tools ──→ ④ B-Layer tool loop (ReAct, max 8 steps)
    │
    ▼
⑤ Generate response (LLM) → Personality-driven output
    │
    ▼
⑥ Storage → Hierarchical memory by importance/emotion
    │
    ▼
⑦ Background → User profile / Growth engine / Experiential cognition
```

---

## Tool List (28 tools)

| Category | Tools |
|----------|-------|
| **File System** | `read_file` · `write_file` · `list_directory` · `search_files` · `delete_file` |
| **Execution** | `run_command` · `run_python` |
| **Web** | `web_search` (DuckDuckGo + Bing) · `fetch_url` · `read_article` (newspaper3k) |
| **System** | `screenshot` · `mouse_click` · `keyboard_type` · `open_application` · `get_system_info` · `read_clipboard` · `write_clipboard` |
| **Browser** | `browser_action` (Playwright) |
| **Office** | `create_word` · `create_excel` · `create_pptx` · `create_pdf` · `read_office_file` |
| **Finance** | `get_stock_info` · `search_stock` |
| **News** | `get_news` · `get_news_sources` |
| **Image** | `generate_image` (pollinations.ai, free) · `generate_image_comfy` (ComfyUI local) |

All high-risk tools (`run_command`, `run_python`) require explicit user confirmation before execution.

---

## ComfyUI Local Image Generation

Generate high-quality images via local ComfyUI, suitable for character selfies, scene images, etc.

### Quick Setup

```bash
# 1. Install and start ComfyUI (https://github.com/comfyanonymous/ComfyUI)
# 2. Download models to ComfyUI/models/checkpoints/
# 3. Double-click to run auto-config tool
python setup_comfyui.py
```

`setup_comfyui.py` auto-detects: ComfyUI installation path, running port, available models, workflow matching, and lets you select generation style (anime/realistic/none).

### Supported Models

No specific model restrictions — `workflow_api.json` is standard ComfyUI export format. You can build your own workflow in ComfyUI and export to replace it.

| Recommended | Description |
|-------------|-------------|
| NoobAI + sdxl_lightning LoRA | Anime style, 4-step generation |
| SDXL Turbo | Realistic style, 4-step generation |
| Flux / SD 1.5 / others | Modify workflow_api.json accordingly |

### Smart Injection

The following are automatically appended during generation:
1. **Style prefix** — based on `comfyui_style` in config (anime: `illustration, anime style, pixiv` / realistic: `photorealistic, 8k uhd`)
2. **Appearance traits** — avatar_prompt (features, hairstyle, body type)
3. **Dynamic outfit** — SimLife wardrobe auto-matched by current scene/time
4. **Travel scene** — Auto-injects current city for travel blogger mode

---

## SimLife World System

SimLife supports custom world settings, enabling an isekai-style communication experience — AGI can roleplay characters from fantasy/sci-fi worlds.

### How It Works

- **Modern world** (default): Non-deletable, uses original reality-based logic
- **Custom worlds**: Users generate world settings via external LLMs (JSON), auto-injected into character generation, activity descriptions, and event generation
- **LLM config inheritance**: SimLife automatically uses the main system's LLM configuration, no separate setup needed

### Usage Steps

1. Open `simlife/worlds/generate_world_prompt.md`, copy the prompt template
2. Paste into any LLM you like (DeepSeek, ChatGPT, etc.), customize as needed
3. Save the generated JSON as `world_setting.json`
4. Import via SimLife API: `POST http://127.0.0.1:8769/api/worlds/import`
5. Switch world: `POST http://127.0.0.1:8769/api/worlds/switch`, body: `{"world_id": "your-world-id"}`

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/worlds` | GET | List all available worlds |
| `/api/worlds/current` | GET | Get current world |
| `/api/worlds/switch` | POST | Switch to a world |
| `/api/worlds/import` | POST | Import a world setting |
| `/api/worlds/template` | GET | Get world template |

### World Template Dimensions

`world_setting_template.json` contains 13 dimensions: world name, geography, races, power system, factions, history, daily life, dangers/dungeons, items, communication devices, time system, special rules, custom fields. Includes prompt examples for Genshin-style, SAO-style, and original fantasy worlds.

---

## Hotkeys

| Hotkey | Function |
|--------|----------|
| `Ctrl+Shift+Space` | Show/hide floating window |
| `Ctrl+Shift+S` | Region screenshot + OCR |

> Both hotkeys are customizable in Settings.

---

## Optional Enhancements

The following components are auto-installed by `install.bat`/`install.sh` (when possible):

```bash
# Voice synthesis (Microsoft Edge TTS, free)
pip install edge-tts

# Mobile web server (phone browser chat)
pip install fastapi uvicorn PyJWT

# Office file I/O (Word/Excel/PPT/PDF)
pip install python-docx openpyxl python-pptx reportlab pdfplumber

# Semantic vectors (improves memory retrieval quality, ~500MB)
pip install sentence-transformers

# Face recognition (InsightFace engine, recommended)
pip install insightface onnxruntime opencv-python

# Browser automation
pip install playwright && playwright install chromium

# Article extraction (intelligent news/article parser)
pip install newspaper3k

# Finance tools (stock info & search)
pip install yfinance

# News tools (requires newsapi.org API key)
pip install newsapi-python

# VRM avatar (PyQt6 WebEngine, optional, requires Python 3.12/3.13)
pip install PyQt6-WebEngine
```

Graceful degradation when optional dependencies are missing — core functionality is unaffected.

---

## VRM Virtual Avatar

Displays a 3D virtual character on the right side of the chat interface, with real-time emotion changes during conversation.

### Requirements

- Python 3.12 or 3.13 (3.14 not yet compatible with PyQt6-WebEngine)
- `pip install PyQt6-WebEngine`
- Place a `.vrm` model file at `vrm_module/static/model.vrm`

### Model Sources

- [VRoid Studio](https://vroid.com/studio) (free character creation tool)
- [VRoid Hub](https://hub.vroid.com) (free, commercial-use models)

### Testing

```bash
python vrm_module/test_server.py
# Open http://localhost:8899 in browser
# Console test: setEmotion("happy", 0.9) / setSpeaking(true)
```

---

## Building Standalone Executables

```bash
pip install pyinstaller
python build.py windows   # → dist/AGI-Desktop.exe
python build.py linux     # → dist/AGI-Desktop
```

---

## Data Storage

All data is stored in the user directory — project folder stays clean:

| Platform | Data Directory |
|----------|---------------|
| Windows | `%APPDATA%\AGI-Assistant\` |
| Linux/macOS | `~/.agi-assistant/` |

Core files:
- `config.json` — User settings (API Key, hotkeys, ComfyUI config, etc.)
- `personality.json` — Personality config (includes avatar_prompt appearance settings)
- `memory.db` — SQLite database (memory/association/user profile/face/growth)

---

## FAQ

### "Python not found" or "'python' is not recognized"

1. Go to https://www.python.org/downloads/ and download Python
2. **Check "Add Python to PATH"** during installation (critical!)
3. Reopen command prompt and run `install.bat` again

### "No module named PyQt6"

Re-run `install.bat`, or manually run: `pip install -r requirements.txt`

### Console shows garbled text

Right-click console title bar → Properties → Font → Select a font that supports your language.

### Memory retrieval quality is poor

Install semantic vectors: `pip install sentence-transformers`

### Ollama tool calling not working

Ollama does not natively support function calling. Use DeepSeek API for full tool support.

### VRM panel shows "WebEngine Not installed"

Python 3.14 is not yet compatible with PyQt6-WebEngine; use Python 3.12 or 3.13. The VRM module is optional and does not affect main program operation.

---

## Tech Stack

- **UI**: PyQt6 (dark theme)
- **LLM**: DeepSeek / OpenAI / Groq / Claude / Gemini / Ollama
- **Memory**: SQLite + sentence-transformers (optional)
- **Mobile**: FastAPI + Uvicorn + PyJWT
- **Voice**: Edge TTS / pyttsx3
- **Face**: InsightFace / face_recognition / OpenCV
- **Office**: python-docx / openpyxl / python-pptx / reportlab / pdfplumber
- **Browser**: Playwright (optional)
- **Finance**: yfinance
- **Articles**: newspaper3k
- **Images**: pollinations.ai (free) / ComfyUI (local, optional)
- **Avatar**: Three.js + three-vrm + PyQt6-WebEngine (optional)

---

## License

[Apache-2.0](LICENSE)