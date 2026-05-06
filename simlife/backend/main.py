"""
SimLife FastAPI 后端入口
端口 8769
"""
import json
import sys
import os
import webbrowser
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# ── 路径 ──────────────────────────────────────────────
SIMLIFE_DIR = Path(__file__).parent.parent
DATA_DIR = SIMLIFE_DIR / "data"
FRONTEND_DIR = SIMLIFE_DIR / "frontend"

sys.path.insert(0, str(SIMLIFE_DIR.parent))

from simlife.backend.character import (
    CharacterCard, WorldState, LogEntry, SceneEnum, SCENE_LABELS
)
from simlife.backend.world_engine import (
    get_current_scene, get_day_seed, get_time_period_label, catchup_world_state,
    _get_current_travel_destination,
)
from simlife.backend.event_engine import (
    load_event_library, load_scheduled_events, save_scheduled_events,
    load_event_history, record_triggered_event,
    check_daily_micro_events, check_random_events, check_scheduled_events,
    apply_event_consequences, add_scheduled_events
)
from simlife.backend.mood_engine import calculate_mood, get_mood_tone
from simlife.backend.npc_engine import load_npc_cards, get_active_npcs
from simlife.backend.agidpa_reader import AGIDPAReader
from simlife.backend.weather import WeatherService
from simlife.backend.world_engine import get_holiday_info, get_festive_log_entry
from simlife.backend.birthday_engine import (
    check_birthdays_today, get_birthday_mood,
)

# ── 全局状态 ───────────────────────────────────────────
character_card: Optional[CharacterCard] = None
world_state: Optional[WorldState] = None
agidpa_reader: Optional[AGIDPAReader] = None
weather_service: Optional[WeatherService] = None
last_tick_scene: Optional[str] = None

# ── App ───────────────────────────────────────────────
app = FastAPI(title="SimLife", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件（前端）
FRONTEND_DIR.mkdir(parents=True, exist_ok=True)


def _load_config() -> dict:
    config_path = DATA_DIR / "simlife_config.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _load_character_card() -> Optional[CharacterCard]:
    path = DATA_DIR / "character_card.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return CharacterCard(**data)
    return None


def _save_character_card(card: CharacterCard):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "character_card.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(card.model_dump(), f, ensure_ascii=False, indent=2)


def _load_world_state() -> WorldState:
    path = DATA_DIR / "world_state.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return WorldState(**data)
    return WorldState()


def _save_world_state(state: WorldState):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "world_state.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state.model_dump(), f, ensure_ascii=False, indent=2)


def _get_work_style_safe() -> str:
    """安全获取工作模式字符串"""
    if not character_card:
        return "office"
    ws = getattr(character_card.basic, "work_style", "office") or "office"
    return ws


def _tick():
    """核心时钟：计算当前场景、检查事件、更新状态"""
    global character_card, world_state, agidpa_reader, last_tick_scene

    if not character_card or not world_state:
        return

    # ── 检查用户是否在场景中（冻结场景推进） ──
    user_in_scene = False
    try:
        profile = _load_user_profile()
        if profile.get("entered"):
            user_in_scene = True
    except Exception:
        pass

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    char_bd = character_card.basic.birth_date if character_card.basic.birth_date else ""

    # 新的一天，重置
    if world_state.today_date != today:
        world_state.today_date = today
        world_state.today_log = []
        world_state.today_events_triggered = []
        # 继承前一天加班的疲劳
        if world_state.current_scene == "OVERTIME":
            world_state.sleep_mood_penalty = -5
        else:
            world_state.sleep_mood_penalty = 0

        # 新的一天注入节日日志（第一条）
        festive_log = get_festive_log_entry(now)
        if festive_log:
            world_state.today_log.append(LogEntry(
                time="09:00", event=festive_log
            ))

        # 新的一天检查生日
        birthday_results = check_birthdays_today(char_bd, load_npc_cards())
        for br in birthday_results:
            world_state.today_log.append(LogEntry(
                time="09:00", event=br["log"]
            ))

    # 离线补算（用户在场景中跳过，避免角色凭空移动）
    if not user_in_scene:
        last_updated = datetime.fromisoformat(world_state.last_updated) if world_state.last_updated else None
        if last_updated and (now - last_updated).total_seconds() > 300:
            world_state, catchup_logs = catchup_world_state(world_state, character_card, now)
            last_tick_scene = world_state.current_scene

    # ── 用户在场景中：冻结场景切换和事件触发，只更新心情 ──
    if user_in_scene:
        scene = SceneEnum(world_state.current_scene)
        label = SCENE_LABELS.get(scene, world_state.current_scene)

        # 心情计算（仍然响应天气、节假日、用户交互等）
        is_weekend = now.weekday() >= 5
        mood_deltas = []
        for eid in world_state.today_events_triggered:
            hist = load_event_history()
            for h in hist:
                if h.get("id") == eid:
                    mood_deltas.append(h.get("mood_delta", 0))
                    break

        interaction_hours = None
        task_len = 0
        if agidpa_reader and agidpa_reader.is_available():
            if agidpa_reader.recent_interaction_within_hours(3):
                interaction_hours = 0.1
            else:
                interaction_hours = None
            task_len = agidpa_reader.get_task_queue_length()

        weather_mood_delta = 0
        if weather_service:
            weather_mood_delta = weather_service.get_mood_delta()

        holiday_mood_delta = 0
        from simlife.backend.holiday_calendar import get_holiday_mood_delta
        holiday_mood_delta = get_holiday_mood_delta(now.date())

        birthday_mood_delta = get_birthday_mood(char_bd) if char_bd else 0
        if birthday_mood_delta == 0:
            for npc in load_npc_cards():
                npc_bd = npc.get("birth_date", "")
                npc_mood = get_birthday_mood(npc_bd)
                if npc_mood > 0:
                    birthday_mood_delta += npc_mood // 3

        mood_deltas.append(weather_mood_delta)
        mood_deltas.append(holiday_mood_delta)
        mood_deltas.append(birthday_mood_delta)

        world_state.mood = calculate_mood(
            scene=scene.value,
            current_hour=now.hour,
            is_weekend=is_weekend,
            today_events_mood_delta=mood_deltas,
            recent_interaction_hours=interaction_hours,
            task_queue_length=task_len,
            sleep_penalty=world_state.sleep_mood_penalty,
        )

        # 激活 NPC（当前场景）
        active = get_active_npcs(scene.value, world_state.today_events_triggered)
        world_state.active_npcs = [n.get("id", "") for n in active]

        if len(world_state.today_log) > 50:
            world_state.today_log = world_state.today_log[-50:]

        world_state.last_updated = now.isoformat()
        _save_world_state(world_state)
        return

    # ── 正常推进（用户不在场景中） ──

    # 事件覆盖（今日已触发事件的后果）
    event_overrides = {}
    for evt_id in world_state.today_events_triggered:
        consequence = apply_event_consequences(evt_id, 0)
        event_overrides.update(consequence.get("schedule_overrides", {}))

    # 计算场景（传入天气服务）
    day_seed = get_day_seed(now)
    scene, label = get_current_scene(
        character_card, now, day_seed,
        event_overrides or None,
        weather_service=weather_service,
    )

    # 场景变化
    scene_changed = scene.value != world_state.current_scene
    if scene_changed:
        world_state.current_scene = scene.value
        time_str = now.strftime("%H:%M")
        if last_tick_scene:
            old_label = SCENE_LABELS.get(SceneEnum(last_tick_scene), last_tick_scene)
            world_state.today_log.append(LogEntry(
                time=time_str, event=f"→ {label}"
            ))

        # 生成 activity 描述
        try:
            from simlife.backend.generator import generate_activity_description
            events_summary = "; ".join([l.event for l in world_state.today_log[-5:]])
            activity = generate_activity_description(
                character_card.model_dump(),
                scene.value, label,
                events_summary,
                world_state.mood
            )
            world_state.current_activity = activity
        except Exception as e:
            print(f"[SimLife] Activity生成失败: {e}")
            world_state.current_activity = f"在{label}"

        last_tick_scene = scene.value

    # 检查微事件（每 5 分钟检查一次，不再仅限场景变化或整15分钟）
    if scene_changed or (now.minute % 5 == 0):
        micro = check_daily_micro_events(
            character_card.model_dump(),
            scene.value,
            day_seed,
            world_state.today_events_triggered
        )
        if micro and micro["id"] not in world_state.today_events_triggered:
            world_state.today_events_triggered.append(micro["id"])
            record_triggered_event(micro)
            world_state.today_log.append(LogEntry(
                time=now.strftime("%H:%M"),
                event=micro["label"]
            ))

    # 检查随机事件
    rand_evt = check_random_events(
        character_card.model_dump(),
        scene.value,
        day_seed,
        world_state.today_events_triggered,
        now,
    )
    if rand_evt and rand_evt["id"] not in world_state.today_events_triggered:
        world_state.today_events_triggered.append(rand_evt["id"])
        record_triggered_event(rand_evt)
        world_state.today_log.append(LogEntry(
            time=now.strftime("%H:%M"),
            event=rand_evt["label"]
        ))

    # 检查排期事件
    scheduled = load_scheduled_events()
    triggered, remaining = check_scheduled_events(scheduled, now)
    for evt in triggered:
        if evt["id"] not in world_state.today_events_triggered:
            world_state.today_events_triggered.append(evt["id"])
            record_triggered_event(evt)
            world_state.today_log.append(LogEntry(
                time=now.strftime("%H:%M"),
                event=evt["label"]
            ))
    if triggered:
        save_scheduled_events(remaining)

    # 计算心情（加入天气 + 节假日修正）
    is_weekend = now.weekday() >= 5
    mood_deltas = []
    for eid in world_state.today_events_triggered:
        hist = load_event_history()
        for h in hist:
            if h.get("id") == eid:
                mood_deltas.append(h.get("mood_delta", 0))
                break

    # 旅行心情加成
    from simlife.backend.character import WorkStyle
    travel_dest = None
    _ws = _get_work_style_safe()
    if _ws == "travel" and character_card:
        travel_dest = _get_current_travel_destination(character_card, now.date())
        if travel_dest:
            mood_deltas.append(travel_dest.get("mood_bonus", 15))

    interaction_hours = None
    task_len = 0
    if agidpa_reader and agidpa_reader.is_available():
        if agidpa_reader.recent_interaction_within_hours(3):
            interaction_hours = 0.1
        else:
            interaction_hours = None
        task_len = agidpa_reader.get_task_queue_length()

    # 天气心情修正
    weather_mood_delta = 0
    if weather_service:
        weather_mood_delta = weather_service.get_mood_delta()

    # 节假日心情修正
    holiday_mood_delta = 0
    from simlife.backend.holiday_calendar import get_holiday_mood_delta
    holiday_mood_delta = get_holiday_mood_delta(now.date())

    # 生日心情修正
    birthday_mood_delta = get_birthday_mood(char_bd) if char_bd else 0
    if birthday_mood_delta == 0:
        # 检查NPC生日
        for npc in load_npc_cards():
            npc_bd = npc.get("birth_date", "")
            npc_mood = get_birthday_mood(npc_bd)
            if npc_mood > 0:
                birthday_mood_delta += npc_mood // 3  # NPC生日对主角心情影响较小

    mood_deltas.append(weather_mood_delta)
    mood_deltas.append(holiday_mood_delta)
    mood_deltas.append(birthday_mood_delta)

    world_state.mood = calculate_mood(
        scene=scene.value,
        current_hour=now.hour,
        is_weekend=is_weekend,
        today_events_mood_delta=mood_deltas,
        recent_interaction_hours=interaction_hours,
        task_queue_length=task_len,
        sleep_penalty=world_state.sleep_mood_penalty,
    )

    # 激活 NPC
    active = get_active_npcs(scene.value, world_state.today_events_triggered)
    world_state.active_npcs = [n.get("id", "") for n in active]

    # 限制日志数量
    if len(world_state.today_log) > 50:
        world_state.today_log = world_state.today_log[-50:]

    # 保存
    world_state.last_updated = now.isoformat()
    _save_world_state(world_state)


# ── API 路由 ──────────────────────────────────────────

@app.get("/api/world/state")
def api_world_state():
    _tick()
    if not world_state:
        return {"error": "世界未初始化"}

    # 天气信息
    weather_data = {"label": "多云", "emoji": "⛅", "temp": ""}
    if weather_service:
        w = weather_service.get_weather()
        weather_data = {
            "label": w.get("label", "多云"),
            "emoji": w.get("emoji", "⛅"),
            "temp": w.get("temp", ""),
            "text": w.get("text", ""),
        }

    # 节假日信息
    holiday_info = get_holiday_info()

    # 生日信息
    birthday_info = None
    char_bd = character_card.basic.birth_date if character_card.basic.birth_date else ""
    if char_bd:
        from simlife.backend.birthday_engine import get_birthday_mood
        if get_birthday_mood(char_bd) > 0:
            birthday_info = {
                "is_self": True,
                "zodiac": character_card.basic.zodiac or "",
            }
    # 即将到来的生日
    from simlife.backend.birthday_engine import get_upcoming_birthdays
    upcoming_birthdays = get_upcoming_birthdays(char_bd, load_npc_cards(), days=14)

    # 旅行信息
    travel_info = None
    if character_card and _get_work_style_safe() == "travel":
        travel_dest = _get_current_travel_destination(character_card, now.date())
        if travel_dest:
            travel_info = travel_dest

    # 用户入驻状态
    user_profile = _load_user_profile()

    return {
        "scene": world_state.current_scene,
        "scene_label": SCENE_LABELS.get(
            SceneEnum(world_state.current_scene), world_state.current_scene
        ),
        "activity": world_state.current_activity,
        "mood": world_state.mood,
        "active_npcs": world_state.active_npcs,
        "today_date": world_state.today_date,
        "time_label": get_time_period_label(),
        "latest_log": [
            {"time": l.time, "event": l.event}
            for l in world_state.today_log[-20:]
        ],
        "weather": weather_data,
        "holiday": holiday_info,
        "birthday": birthday_info,
        "upcoming_birthdays": upcoming_birthdays,
        "travel": travel_info,
        "user": {
            "entered": user_profile.get("entered", False),
            "name": user_profile.get("name", ""),
            "relation": user_profile.get("relation", ""),
        },
    }


@app.get("/api/character")
def api_get_character():
    if not character_card:
        return {"initialized": False}
    return {"initialized": True, "card": character_card.model_dump()}


@app.post("/api/character")
def api_set_character(data: dict):
    global character_card
    try:
        character_card = CharacterCard(**data)
        _save_character_card(character_card)
        # 初始化世界状态
        global world_state
        world_state = WorldState(
            last_updated=datetime.now().isoformat(),
            today_date=datetime.now().strftime("%Y-%m-%d"),
            current_scene="HOME_EVENING",
            current_activity="刚设置好，在看看新家",
        )
        _save_world_state(world_state)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/setup/generate")
def api_setup_generate(data: dict):
    """首次设置：根据锚点生成人物卡"""
    global character_card

    try:
        from simlife.backend.generator import generate_character_card, generate_npc_cards

        anchor = data.get("anchor", {})
        card_data = generate_character_card(anchor)
        if not card_data:
            raise HTTPException(500, "人物卡生成失败")

        character_card = CharacterCard(**card_data)
        _save_character_card(character_card)

        # 生成 NPC
        npc_data = generate_npc_cards(card_data)
        if npc_data:
            from simlife.backend.npc_engine import save_npc_cards
            save_npc_cards(npc_data)

        # 初始化世界状态
        global world_state
        now = datetime.now()
        scene, label = get_current_scene(character_card, now)
        world_state = WorldState(
            last_updated=now.isoformat(),
            current_scene=scene.value,
            current_activity=f"世界开始了，{label}",
            today_date=now.strftime("%Y-%m-%d"),
        )
        _save_world_state(world_state)

        return {"status": "ok", "card": character_card.model_dump()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"生成失败: {e}")


@app.get("/api/npcs")
def api_get_npcs():
    return {"npcs": load_npc_cards()}


@app.get("/api/events/history")
def api_event_history():
    return {"history": load_event_history()[-30:]}


@app.get("/api/events/scheduled")
def api_scheduled_events():
    return {"scheduled": load_scheduled_events()}


@app.post("/api/reset")
def api_reset():
    """重置 SimLife：删除角色卡、世界状态和用户档案，重新初始化"""
    global character_card, world_state
    try:
        # 删除数据文件
        for f in ["character_card.json", "world_state.json", "user_profile.json"]:
            p = DATA_DIR / f
            if p.exists():
                p.unlink()

        character_card = None
        world_state = None

        return {"status": "ok", "message": "已重置，请刷新页面重新设置"}
    except Exception as e:
        raise HTTPException(500, f"重置失败: {e}")


@app.get("/api/status")
def api_status():
    return {
        "initialized": character_card is not None,
        "version": "1.0.0",
    }


# ── 世界观管理 API ─────────────────────────────────

@app.get("/api/worlds")
def api_list_worlds():
    """列出所有可用世界观"""
    from simlife.worlds.world_manager import list_available_worlds, get_current_world_id
    return {
        "worlds": list_available_worlds(),
        "current": get_current_world_id(),
    }


@app.get("/api/worlds/current")
def api_get_current_world():
    """获取当前世界观的完整设定"""
    from simlife.worlds.world_manager import (
        load_world_setting, build_world_context,
        get_current_world_id,
    )
    world_id = get_current_world_id()
    setting = load_world_setting(world_id)
    context = build_world_context(setting) if setting else ""
    return {"world_id": world_id, "setting": setting, "context": context}


@app.post("/api/worlds/switch")
def api_switch_world(data: dict):
    """切换世界观（仅未初始化时可用）"""
    if character_card is not None:
        raise HTTPException(400, "已初始化角色，无法切换世界观")
    world_id = data.get("world_id", "modern")
    from simlife.worlds.world_manager import set_current_world, list_available_worlds
    valid_ids = [w["world_id"] for w in list_available_worlds()]
    if world_id not in valid_ids:
        raise HTTPException(400, f"无效的世界观 ID: {world_id}")
    set_current_world(world_id)
    return {"status": "ok", "world_id": world_id}


@app.post("/api/worlds/import")
def api_import_world(data: dict):
    """导入自定义世界观设定"""
    setting = data.get("setting")
    if not setting or not isinstance(setting, dict):
        raise HTTPException(400, "缺少 setting 字段")
    world_id = setting.get("world_id", "custom")
    if not world_id or world_id == "modern":
        raise HTTPException(400, "世界观 ID 无效（不能使用 'modern'）")
    from simlife.worlds.world_manager import save_world_setting
    save_world_setting(world_id, setting)
    return {"status": "ok", "world_id": world_id, "world_name": setting.get("world_name", "")}


@app.post("/api/worlds/generate")
def api_generate_world(data: dict):
    """用 AI 生成一个自定义世界观设定"""
    from simlife.backend.generator import generate_world_setting
    from simlife.worlds.world_manager import save_world_setting, set_current_world

    world_type = data.get("world_type", "fantasy")
    core_theme = data.get("core_theme", "")
    character_role = data.get("character_role_hint", "")

    if not core_theme:
        raise HTTPException(400, "请填写核心主题")

    try:
        setting = generate_world_setting(
            world_type=world_type,
            core_theme=core_theme,
            character_role=character_role,
        )
        if not setting:
            raise HTTPException(500, "AI 生成世界观失败，请检查 API Key 是否配置")

        world_id = setting.get("world_id", "custom")
        save_world_setting(world_id, setting)
        set_current_world(world_id)
        return {
            "status": "ok",
            "world_id": world_id,
            "world_name": setting.get("world_name", ""),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"生成失败: {e}")


@app.get("/api/worlds/template")
def api_get_world_template():
    """获取世界观设定模板（用户用 LLM 生成后导入）"""
    from simlife.worlds.world_manager import WORLD_TEMPLATE
    if WORLD_TEMPLATE.exists():
        with open(WORLD_TEMPLATE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# ── 用户入驻管理 API ─────────────────────────────────

USER_PROFILE_PATH = DATA_DIR / "user_profile.json"


def _load_user_profile() -> dict:
    """加载用户在世界中的身份信息"""
    if USER_PROFILE_PATH.exists():
        with open(USER_PROFILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"entered": False}


def _save_user_profile(profile: dict):
    """保存用户身份信息"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(USER_PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)


@app.get("/api/user/profile")
def api_get_user_profile():
    """获取用户当前入驻状态和身份"""
    return _load_user_profile()


@app.post("/api/user/profile")
def api_set_user_profile(data: dict):
    """设置用户在世界中的身份信息"""
    profile = _load_user_profile()
    profile["name"] = data.get("name", "") or profile.get("name", "")
    profile["relation"] = data.get("relation", "")
    profile["world_role"] = data.get("world_role", "")
    if profile["relation"]:
        _save_user_profile(profile)
    return {"status": "ok", "profile": profile}


@app.post("/api/user/enter")
def api_user_enter():
    """用户进入 SimLife 世界"""
    profile = _load_user_profile()
    if not profile.get("relation"):
        raise HTTPException(400, "请先设置你与角色的关系")
    profile["entered"] = True
    profile["entered_at"] = datetime.now().isoformat()
    _save_user_profile(profile)
    # 记录到世界日志
    if world_state:
        user_name = profile.get("name", "用户")
        relation = profile.get("relation", "")
        world_state.today_log.append(LogEntry(
            time=datetime.now().strftime("%H:%M"),
            event=f"🎂 {user_name}（{relation}）来到了"
        ))
        _save_world_state(world_state)
    return {"status": "ok", "entered": True}


@app.post("/api/user/leave")
def api_user_leave():
    """用户离开 SimLife 世界"""
    profile = _load_user_profile()
    profile["entered"] = False
    profile["entered_at"] = None
    _save_user_profile(profile)
    # 记录到世界日志
    if world_state:
        user_name = profile.get("name", "用户")
        world_state.today_log.append(LogEntry(
            time=datetime.now().strftime("%H:%M"),
            event=f"👋 {user_name}离开了"
        ))
        _save_world_state(world_state)
    return {"status": "ok", "entered": False}


# ── 前端静态文件 ─────────────────────────────────────

@app.get("/")
def serve_index():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return "<h1>SimLife</h1><p>前端文件未找到，请运行 setup.py</p>"


# 挂载前端静态文件（JS/CSS/图片）
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.on_event("startup")
def on_startup():
    global character_card, world_state, agidpa_reader, weather_service

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 加载人物卡
    character_card = _load_character_card()

    # 加载世界状态
    if character_card:
        world_state = _load_world_state()

    # AGI-DPA 读取器
    config = _load_config()
    agidpa_path = config.get("agidpa_data_path", "")
    agidpa_reader = AGIDPAReader(agidpa_path)

    # 天气服务（Open-Meteo 免费 API，无需配置 Key，根据人物卡城市自动定位）
    city = character_card.basic.city if character_card else "上海"
    weather_service = WeatherService(city=city)
    geo = weather_service._geo
    if geo:
        print(f"[SimLife] 天气服务已启用（{city}，{geo[0]:.2f}°N {geo[1]:.2f}°E）")
    else:
        print(f"[SimLife] 天气服务：城市「{city}」未找到坐标，使用季节推断")

    print("[SimLife] 后端启动")
    if character_card:
        print(f"[SimLife] 角色: {character_card.basic.name}")
        h = get_holiday_info()
        if h:
            print(f"[SimLife] 今天: {h['label']}（{h['type']}）")
        _tick()
    else:
        print("[SimLife] 未初始化，请访问设置页面")

    # ── 后台定时 tick 线程（不依赖前端轮询，每 3 分钟自动推进一次）──
    def _background_tick_loop():
        while True:
            try:
                import time
                time.sleep(180)  # 每 3 分钟
                _tick()
            except Exception as e:
                print(f"[SimLife] 后台tick出错: {e}")

    _bg_thread = threading.Thread(target=_background_tick_loop, daemon=True)
    _bg_thread.start()
    print("[SimLife] 后台定时 tick 已启动（每 3 分钟）")


def run_server(port: int = 87659, open_browser: bool = True):
    """启动服务器"""
    import uvicorn

    def _open():
        import time
        time.sleep(1.5)
        webbrowser.open(f"http://127.0.0.1:{port}")

    if open_browser:
        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SimLife 后端")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    run_server(port=args.port, open_browser=not args.no_browser)
