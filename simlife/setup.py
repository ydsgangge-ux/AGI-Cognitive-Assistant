"""
SimLife 首次启动引导
"""
import json
import sys
import webbrowser
from pathlib import Path

SIMLIFE_DIR = Path(__file__).parent
DATA_DIR = SIMLIFE_DIR / "data"
FRONTEND_DIR = SIMLIFE_DIR / "frontend"
CONFIG_PATH = DATA_DIR / "simlife_config.json"
CHARACTER_PATH = DATA_DIR / "character_card.json"


def ensure_dirs():
    """确保目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "events").mkdir(parents=True, exist_ok=True)


def ensure_config():
    """确保配置文件存在"""
    if not CONFIG_PATH.exists():
        config = {
            "agidpa_data_path": "../",
            "backend_port": 87659,
            "tick_interval_seconds": 300,
            "llm_provider": "",
            "llm_api_key": "",
            "llm_model": None,
            "weather_api_key": "",
            "weather_enabled": False,
            "language": "zh-CN",
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        print("[Setup] Default config created: simlife_config.json")
    else:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    return config


def ensure_data_files():
    """确保数据文件存在"""
    from .backend.event_engine import load_event_library
    lib = load_event_library()
    if not lib:
        print("[Setup] Event library empty，please verify event_library.json exists")


def check_frontend():
    """检查前端文件"""
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        print("[Setup] Frontend files missing, need generation")
        return False
    return True


def main():
    print("=" * 40)
    print("  SimLife First-Time Setup")
    print("=" * 40)
    print()

    ensure_dirs()
    config = ensure_config()
    ensure_data_files()

    if CHARACTER_PATH.exists():
        print("[Setup] Character card exists, starting world directly")
    else:
        print("[Setup] Character card not created, complete setup on settings page after launch")

    port = config.get("backend_port", 8769)
    print(f"\n[Setup] Starting backend service (端口 {port})...")
    print(f"[Setup] Open browser at http://127.0.0.1:{port}")

    # 启动后端
    sys.path.insert(0, str(SIMLIFE_DIR.parent))
    from simlife.backend.main import run_server
    run_server(port=port, open_browser=True)


if __name__ == "__main__":
    main()
