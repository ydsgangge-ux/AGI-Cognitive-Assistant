"""
定时执行引擎 v1
- 事件驱动：有计划才设 QTimer，没计划零负担
- 持久化：计划存 JSON，关机不丢，开机自动恢复
- 支持提醒、执行工具、重复计划

数据文件：simlife/data/scheduled_events.json（与 add_schedule 工具共用）
"""

import json
import uuid
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional, Callable, List, Dict, Any

try:
    from PyQt5.QtCore import QTimer
except ImportError:
    QTimer = None

SCHEDULE_PATH = Path(__file__).resolve().parent.parent / "simlife" / "data" / "scheduled_events.json"


def _load_events() -> List[dict]:
    if not SCHEDULE_PATH.exists():
        return []
    try:
        with open(SCHEDULE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_events(events: List[dict]):
    SCHEDULE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SCHEDULE_PATH, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)


def _parse_datetime(evt: dict) -> Optional[datetime]:
    d = evt.get("scheduled_date", "")
    t = evt.get("scheduled_time", "")
    if not d:
        return None
    try:
        if t:
            return datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M")
        return datetime.strptime(d, "%Y-%m-%d").replace(hour=9, minute=0)
    except ValueError:
        return None


class Scheduler:
    def __init__(self, on_fire: Optional[Callable] = None):
        self._on_fire = on_fire
        self._timers: Dict[str, Any] = {}
        self._agent = None
        self._running = False

    def set_agent(self, agent):
        self._agent = agent

    def start(self):
        self._running = True
        self._restore()

    def stop(self):
        self._running = False
        for timer in self._timers.values():
            if timer:
                timer.stop()
        self._timers.clear()

    def _restore(self):
        events = _load_events()
        now = datetime.now()
        for evt in events:
            if evt.get("status") == "done":
                continue
            target = _parse_datetime(evt)
            if target is None:
                continue
            if target <= now:
                self._fire_event(evt)
            else:
                self._set_timer(evt, target)

    def add_event(self, evt: dict):
        if "id" not in evt:
            evt["id"] = f"sch_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:4]}"
        evt.setdefault("status", "pending")
        evt.setdefault("created_at", datetime.now().isoformat())

        events = _load_events()
        events.append(evt)
        _save_events(events)

        target = _parse_datetime(evt)
        now = datetime.now()
        if target and target <= now:
            self._fire_event(evt)
        elif target:
            self._set_timer(evt, target)

    def watch_event(self, evt: dict):
        """对已持久化的事件只设定时器，不重复写 JSON"""
        target = _parse_datetime(evt)
        now = datetime.now()
        if target and target <= now:
            self._fire_event(evt)
        elif target:
            self._set_timer(evt, target)

    def _set_timer(self, evt: dict, target: datetime):
        if QTimer is None:
            return
        evt_id = evt.get("id", "")
        if evt_id in self._timers and self._timers[evt_id]:
            self._timers[evt_id].stop()

        delay_ms = int((target - datetime.now()).total_seconds() * 1000)
        if delay_ms <= 0:
            self._fire_event(evt)
            return

        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._fire_event(evt))
        timer.start(delay_ms)
        self._timers[evt_id] = timer

    def _fire_event(self, evt: dict):
        evt_id = evt.get("id", "")
        content = evt.get("content", "")
        remind = evt.get("remind", content)
        action = evt.get("action", "")
        repeat = evt.get("repeat", "once")
        user_id = evt.get("user_id", "default")

        if self._on_fire:
            self._on_fire({
                "id": evt_id,
                "remind": remind,
                "action": action,
                "content": content,
                "user_id": user_id,
            })

        if action and self._agent:
            try:
                self._agent.process(action, user_id=user_id)
            except Exception:
                pass

        if repeat in ("daily", "weekly"):
            self._reschedule(evt, repeat)
        else:
            self._mark_done(evt_id)

        if evt_id in self._timers:
            self._timers.pop(evt_id, None)

    def _mark_done(self, evt_id: str):
        events = _load_events()
        for evt in events:
            if evt.get("id") == evt_id:
                evt["status"] = "done"
                evt["fired_at"] = datetime.now().isoformat()
                break
        _save_events(events)

    def _reschedule(self, evt: dict, repeat: str):
        target = _parse_datetime(evt)
        if not target:
            self._mark_done(evt.get("id", ""))
            return

        if repeat == "daily":
            next_target = target + timedelta(days=1)
        elif repeat == "weekly":
            next_target = target + timedelta(weeks=1)
        else:
            self._mark_done(evt.get("id", ""))
            return

        new_evt = dict(evt)
        new_evt["id"] = f"sch_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:4]}"
        new_evt["scheduled_date"] = next_target.strftime("%Y-%m-%d")
        if "scheduled_time" in evt:
            new_evt["scheduled_time"] = evt["scheduled_time"]
        new_evt["status"] = "pending"
        new_evt["created_at"] = datetime.now().isoformat()
        if "fired_at" in new_evt:
            del new_evt["fired_at"]

        self._mark_done(evt.get("id", ""))
        self.add_event(new_evt)

    def get_pending(self) -> List[dict]:
        events = _load_events()
        return [e for e in events if e.get("status") != "done"]
