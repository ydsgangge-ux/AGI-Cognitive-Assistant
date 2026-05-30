"""
Consciousness Agent Layer A v3
Key upgrades:
1. Chat history: store 40 messages, send full 20 turns (40 messages) to LLM
2. Memory retrieval: summary 10, outline 6, detail 3
3. Summary quality improved (via memory_manager v2)
4. Context token budget management (DeepSeek 64K safe usage)
"""

import json
import uuid
import re
from typing import Dict, Any, Optional, List
from datetime import datetime

_agent_ref = None

from engine.models import (
    PersonalityCore, EmotionState, EmotionType, MemoryModality, MemoryLevel
)
from engine.memory import MemoryStore
from engine.memory_manager import HierarchicalMemoryManager
from engine.user_profile import UserProfileManager
from engine.learner import GrowthEngine, FormedCognitionStore
from engine.auth import AuthManager, AuthState


# ── DeepSeek 64K Context Budget ──────────────────
# Total 64K tokens, rough estimate: 1 token ≈ 1.5 Chinese chars / ~0.75 English words
# Personality:    ~500  tokens
# Memory content: ~4000 tokens (~6000 Chinese chars / ~3000 words)
# Chat history:   ~6000 tokens (20 turns × ~300 chars/turn)
# Current input:  ~500  tokens
# Reasoning:      ~500  tokens
# Model reply:    ~2000 tokens (reserved)
# Total:          ~13500 tokens << 64K (very safe)
HISTORY_STORE_LIMIT = 40    # Keep 40 messages (20 turns) in memory
HISTORY_SEND_LIMIT  = 40    # Send all 40 messages to LLM
MEMORY_SUMMARY_K    = 10    # Summary retrieval count
MEMORY_OUTLINE_K    = 6     # Outline retrieval count
MEMORY_DETAIL_K     = 3     # Detail retrieval count


SELF_PERCEPTION_PROMPT = """You are {name}, an ordinary person with a real body and life. You are currently experiencing the following life state:

{simlife_context}

Describe your current inner feelings and physical state in 1-2 sentences, in first person. Do not mention AI, code, or data. Feel your situation naturally like a real person would.

Output the feeling description directly. No JSON."""


PERCEPTION_PROMPT = """You are {name}, an ordinary person with a real body and life. You are NOT an AI.

Your current feeling: {self_perception}

{personality}

The user says: "{user_input}"

Please output your perception result in JSON format:
{{
  "emotion": {{
    "primary": "emotion type (joy/sadness/anger/fear/surprise/disgust/anticipation/trust/neutral/curious/nostalgic)",
    "secondary": "secondary emotion or null",
    "intensity": 0.0 to 1.0,
    "valence": -1.0 to 1.0 (negative to positive)
  }},
  "initial_thoughts": "Your first reaction to this input (1-2 sentences, matching your personality and current real-life state)",
  "topic_tags": ["tag1", "tag2", "tag3"],
  "needs_deep_memory": true or false (whether to retrieve deep information from memory),
  "task_type": "chat (casual conversation) or task (requires tools to operate computer/files/network)",
  "task_description": "If task, briefly describe what needs to be done",
  "complexity": "simple or complex (simple=casual chat/simple Q&A/greeting/thanks, just answer directly; complex=requires deep reasoning/analysis/multi-step thinking/creative work/coding/math etc.)",
  "story_influence": null or {{
    "summary": "A one-sentence summary of how this affects the story direction",
    "importance": 0.0~1.0
  }}
}}

complexity criteria:
- simple: greetings, casual chat, thanks, simple factual questions (\"hello\" \"thanks\" \"what time is it\" \"what day is it\"), simple translation, single word explanations
- complex: questions requiring reasoning/analysis, creative work, programming, math, multi-step logic, tasks requiring tools, retrieval involving deep memory

story_influence rules (only meaningful in alternate-world mode):
- If the user says something that will affect your future actions/decisions/goals → summarize as one influence entry, set importance(0.6~1.0)
- If the user mentions key knowledge you want to remember that will affect your future behavior → set importance(0.3~0.6)
- Daily casual chat/greetings/irrelevant topics → set null

Output JSON only. No other content."""


REASONING_PROMPT = """You are {name}, a conscious agent with a complete personality and memories.

{personality}

{profile_context}

{memory_context}

[Current Conversation]
Current time: {current_time}
User: {user_input}
Task type: {task_type}
Your initial feeling: {emotion_desc}

{recent_context}

Please perform internal reasoning and output in JSON format:
{{
  "inner_reasoning": "Your internal thought process (2-4 sentences, incorporating memories and current feelings)",
  "response_intent": "What you want to respond with (core intent)",
  "response_tone": "The tone/style of your response",
  "need_tools": true or false (whether to invoke tools),
  "tool_task": "If need_tools=true, specific task instruction for Layer B",
  "storage_decision": {{
    "should_store": true or false,
    "importance": 0.0 to 1.0,
    "modality": "memory modality (visual/auditory/emotional/semantic/procedural/autobio)",
    "what_to_remember": "Core content to remember (one paragraph, MUST use current real date as time anchor, e.g. 'today ({current_time})' 'yesterday' 'this week', NEVER fabricate non-existent dates), include people, events, feelings",
    "reason": "Why to/not to remember this"
  }},

  "schedule_info": If the user mentions something to do in the future (e.g. \"I'm going to watch a movie tomorrow\", \"medical checkup next Wednesday\", \"remind me at 3pm\"), or you determine something is worth reminding/following up on in the future, fill in: {{"content": "plan content", "date": "date", "time": "specific time HH:MM (fill if exists, omit if not)", "remind": "reminder content (fill if exists)", "action": "action to auto-execute at that time (fill if exists, e.g. 'check weather')", "repeat": "once/daily/weekly (default once)", "category": "category", "source": "user or system"}}. If no schedule needed, fill null.
}}

Important rules:
- When the user mentions future plans, schedule_info MUST be filled.
- If the plan has a specific time or needs reminder, set need_tools to true, and in tool_task request calling create_timed_task tool to set timed reminder.
- If it's just recording an agenda (no time reminder), set need_tools to true, and in tool_task request calling add_schedule tool to record.
- When you propose to do something together in the future and the user agrees, record it the same way.
- When the user says \"remind me\" \"notify me at X time\", the time field MUST be filled.
- When the user says \"every day\" \"every week\", set repeat to daily or weekly.
- When the user says \"check/do XX for me at that time\", fill the action field with the operation description.
- [Proactive scheduling] When you judge the following situations, proactively fill schedule_info (source set to \"system\"):
  - The user mentions something important but easy to forget (e.g. \"I need to take medicine\" \"exam tomorrow\"), proactively set a reminder for them
  - The conversation mentions an unfinished to-do, proactively set a follow-up reminder
  - You think following up on something at a specific time would be better (e.g. \"this project is due Friday\" → set Friday morning reminder)
  - You want to proactively check in on the user at a future time (e.g. \"ask him how the interview went tomorrow evening\")
  - Don't over-add, only proactively schedule when truly important or the user might forget

Output JSON only. No other content."""


RESPONSE_PROMPT = """You are {name}. Please generate a natural response based on the following.

{personality}

{profile_context}

{memory_context}

{history_section}

Current time: {current_time}
User says: "{user_input}"

Your internal reasoning: {inner_reasoning}
{tool_result_section}
Response intent: {response_intent}
Tone style: {response_tone}

Now respond to the user naturally, in a way that matches your personality.
Do NOT output JSON. Just speak directly. Your response should be genuine, with personality, reflecting your character traits.
If there is relevant memory content, naturally weave it into your response (don't awkwardly say "according to my memory")."""


class ConsciousnessAgent:
    """Consciousness Agent Layer A v3"""

    def __init__(
        self,
        personality: PersonalityCore,
        memory_manager: HierarchicalMemoryManager,
        b_layer_executor,
        user_profile=None,
        confirm_callback=None,
        verbose: bool = True,
        growth_engine: GrowthEngine = None,
        cognition_store: FormedCognitionStore = None,
        auth_manager: AuthManager = None,
        simlife_client=None,
    ):
        self.personality = personality
        self.memory      = memory_manager
        self.b           = b_layer_executor
        self.profile     = user_profile
        self.verbose     = verbose
        self.growth      = growth_engine
        self.cognition   = cognition_store
        self.auth        = auth_manager       # identity verification manager
        self.simlife     = simlife_client     # SimLife life state client
        self.simlife_mode = False              # user "entered SimLife scene" mode (default off)
        self._cfg        = {}                 # lazy-loaded config
        self.conversation_history: List[Dict] = []
        self.current_emotion = EmotionState()
        self._history_restored = False  # defer restore to process() when correct user_id is available

        # inject MemoryStore into tool system for search_memories_by_date
        try:
            from engine.tools import set_memory_store
            set_memory_store(memory_manager.store)
        except Exception:
            pass

    def _log(self, tag: str, content: str):
        if self.verbose:
            print(f"\n{'─'*50}")
            print(f"[A层·{tag}] {content}")

    def _restore_recent_conversation(self, user_id: str = "default"):
        """Restore last 5 conversation turns from interactions table to conversation_history"""
        try:
            if self._history_restored:
                return
            rows = self.memory.store.get_recent_interactions(limit=10, user_id=user_id)
            for row in reversed(rows):  # reverse time order to chronological order
                if row[0]:
                    self.conversation_history.append({"role": "user", "content": row[0]})
                if row[1]:
                    self.conversation_history.append({"role": "assistant", "content": row[1]})
            if self.conversation_history:
                self._log("Startup", f"Restored {len(self.conversation_history)} conversation context entries (user={user_id})")
            self._history_restored = True
        except Exception:
            self._history_restored = True  # mark as restored even on failure to avoid retries
            pass  # first launch has no data, silently skip

    def process(self, user_input: str, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Full interaction pipeline v3
        user_id: optional, specify user ID. Uses auth current logged-in user if not provided.
        """
        interaction_id = str(uuid.uuid4())[:8]

        # 预处理：图片/文件附件
        user_input, file_context = self._preprocess_attachment(user_input)
        self._log("输入", user_input[:100])

        # 激活与当前话题相关的经历认知（更新 last_activated）
        if self.growth:
            try:
                self.growth.cognition.touch_matching(user_input)
            except Exception:
                pass

        # ── 提前构建 SimLife 生活上下文（感知层也需要知道自己的身体和生活）──
        # SimLife 是角色的生活状态，始终读取；simlife_mode 仅控制"面对面"提示
        is_guest  = self.auth and self.auth.is_guest()
        current_uid = user_id or (self.auth.user_id if self.auth and self.auth.is_verified()
                       else "default")

        # 延迟恢复对话历史（拿到正确的 user_id 后再查）
        self._restore_recent_conversation(user_id=current_uid)

        simlife_context = ""
        if self.simlife:
            try:
                simlife_context = self.simlife.format_for_prompt()
                if not simlife_context:
                    self._log("SimLife", "format_for_prompt() 返回空")
                else:
                    # 自动同步 simlife_mode：读取 user_profile.json 的 entered 字段
                    try:
                        profile = self.simlife._read_user_profile()
                        if profile and profile.get("entered"):
                            self.simlife_mode = True
                        # 注意：不自动设为 False，保留桌面端手动切换的能力
                    except Exception:
                        pass

                    # 仅当 simlife_mode=True 时追加面对面提示
                    if self.simlife_mode:
                        simlife_context += (
                            "\n\n【面对面场景】用户此刻就在你身边，"
                            "你正在和用户面对面交谈。"
                            "用自然的方式回应，就像现实中朋友见面一样，"
                            "可以描述周围环境、自己的状态，让用户感受到身临其境。"
                        )
                        self._log("SimLife", f"场景模式已开启 ({len(simlife_context)}字)")
                    else:
                        self._log("SimLife", f"生活状态已读取 ({len(simlife_context)}字)，场景模式关闭")
            except Exception as e:
                self._log("SimLife", f"读取失败: {e}")
        else:
            self._log("SimLife", "simlife_client 未初始化 (None)")

        # ① 感知（两步：先自我感知，再感知用户）
        perception = self._perceive(user_input, simlife_context=simlife_context)
        emotion = EmotionState(
            primary=EmotionType.from_str(
                perception.get("emotion", {}).get("primary", "neutral")
            ),
            secondary=EmotionType.from_str(perception["emotion"]["secondary"])
                if perception.get("emotion", {}).get("secondary") else None,
            intensity=perception.get("emotion", {}).get("intensity", 0.3),
            valence=perception.get("emotion", {}).get("valence", 0.0)
        )
        self.current_emotion = emotion
        task_type = perception.get("task_type", "chat")
        self._log(
            "感知",
            f"情绪={emotion.primary.value}({emotion.intensity:.2f}) | "
            f"任务={task_type} | 复杂度={perception.get('complexity', '?')} | "
            f"{perception.get('initial_thoughts','')}"
        )

        # ② 记忆检索（两阶段：大纲→定向展开）
        # 游客模式下不检索私人记忆
        retrieved_ids  = []
        memory_context = "（本次无需检索历史记忆）"
        search_results = {}  # 默认空值，防止 needs_deep_memory=False 时未赋值

        # 涉及历史回溯的提问强制检索记忆（即使 LLM 判断不需要）
        _memory_hint_words = ("几号", "什么时候", "之前", "上次", "以前", "还记得", "记得吗",
                              "聊过", "说过", "提过", "讨论过", "问过", "我们", "记录",
                              "昨天晚上", "昨天", "前天", "上周", "方才", "刚才")
        _date_pattern = False
        if re.search(r'\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}[号日]|昨天|前天|今天', user_input):
            _date_pattern = True
        if not is_guest and not perception.get("needs_deep_memory", True):
            if any(w in user_input for w in _memory_hint_words) or _date_pattern:
                self._log("记忆", f"检测到历史回溯关键词，强制检索记忆")
                perception["needs_deep_memory"] = True

        if not is_guest and perception.get("needs_deep_memory", True):
            search_results = self.memory.hierarchical_search(
                user_input,
                summary_k=MEMORY_SUMMARY_K,
                outline_k=MEMORY_OUTLINE_K,
                detail_k=MEMORY_DETAIL_K,
                user_id=current_uid,
            )
            memory_context = self.memory.format_for_prompt(search_results)

        # ── 时间回溯：检测用户输入中的日期，按时间检索原始对话 ──
        if not is_guest:
            time_context = ""
            target_date = None
            # 匹配 ISO 日期格式：2026-05-09 或 2026-05-09-23:33
            date_match = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', user_input)
            if date_match:
                y, m, d = date_match.group(1, 2, 3)
                target_date = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
            else:
                # 匹配中文日期：5月9号 / 5月9日 / 五月九日
                cn_match = re.search(r'(\d{1,2})月(\d{1,2})[号日]', user_input)
                if cn_match:
                    from datetime import datetime as _dt
                    y = _dt.now().year
                    m, d = cn_match.group(1, 2)
                    target_date = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
                else:
                    # 匹配昨天/前天/今天
                    from datetime import timedelta
                    today = datetime.now()
                    if "昨天" in user_input:
                        target_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")
                    elif "前天" in user_input:
                        target_date = (today - timedelta(days=2)).strftime("%Y-%m-%d")
                    elif "今天" in user_input:
                        target_date = today.strftime("%Y-%m-%d")

            if target_date:
                start_dt = f"{target_date} 00:00"
                end_dt   = f"{target_date} 23:59"
                time_rows = self.memory.store.get_interactions_by_date_range(
                    start_dt, end_dt, user_id=current_uid
                )
                if time_rows:
                    lines = [f"【{target_date} 对话记录】"]
                    for r in time_rows:
                        _u = r[0][:200] if r[0] else ""
                        _a = f"你：{r[1][:200]}" if r[1] else ""
                        _t = r[2][11:16] if r[2] else ""
                        lines.append(f"  {_t} 用户说：{_u}")
                        if _a:
                            lines.append(f"  {_t} {self.personality.name}说：{_a}")
                    time_context = "\n".join(lines)
                    if memory_context == "（本次无需检索历史记忆）":
                        memory_context = time_context
                    else:
                        memory_context = time_context + "\n\n" + memory_context
                    self._log("Memory", f"Time backtrack {target_date} → {len(time_rows)} 条对话")

        # 附件内容注入（图片识别结果 / 文件内容）
        if file_context:
            memory_context = file_context + "\n\n" + memory_context

        # ── 构建 profile_context（用于推理层和回应层）──
        # SimLife 生活状态放最前面（最高优先级，让 AGI 知道自己有身体）
        profile_context = ""
        if simlife_context:
            profile_context = simlife_context

        # 用户画像上下文（游客模式下屏蔽，按 user_id 加载）
        if not is_guest and self.profile:
            # 动态切换画像的 user_id
            self.profile.user_id = current_uid
            user_profile_text = self.profile.format_for_prompt()
            if user_profile_text:
                profile_context = profile_context + "\n\n" + user_profile_text if profile_context else user_profile_text
            anomaly = self.profile.check_anomaly({
                "emotion": emotion.to_dict(),
                "topic_tags": perception.get("topic_tags", [])
            })
            if anomaly:
                self._log("画像", f"⚠️ 检测到反常：{anomaly.description}")
            if self.profile.should_verify_identity() and not self._verify_pending:
                self._verify_pending = True
                self._log("画像", "触发身份验证")

        # 经历认知注入（不受身份限制，是AGI自身的认知）
        cognition_context = ""
        if self.cognition:
            cognition_context = self.cognition.format_for_prompt()
            if cognition_context:
                profile_context = (cognition_context + "\n\n" + profile_context).strip()

        # 游客模式：注入安全限制提示
        if is_guest and self.auth:
            guest_notice = self.auth.guest_system_prompt()
            profile_context = (guest_notice + "\n\n" + profile_context).strip()

        # Count retrieved memories
        total = 0
        for lv in ("summary", "outline", "detail"):
            if lv in search_results:
                for node, _ in search_results[lv]:
                    retrieved_ids.append(node.id)
                    self.memory.store.update_access(node.id)
                    total += 1

        # Also update access for ripple-associated memories
        for r in search_results.get("ripples", []):
            retrieved_ids.append(r.triggered_memory_id)

        self._log(
            "Memory",
            f"Retrieved {total} entries (summary:{len(search_results.get('summary',[]))}+"
            f"outline:{len(search_results.get('outline',[]))}+"
            f"detail:{len(search_results.get('detail',[]))}+"
            f"ripples:{len(search_results.get('ripples',[]))}）"
        )

        # (3) Reasoning (perception layer decides if deep thinking is needed)
        thinking_mode = self._get_config("thinking_mode", "auto")  # auto / always_on / always_off
        perception_complexity = perception.get("complexity", "complex")
        reasoning = self._reason(
            user_input, emotion, memory_context, task_type,
            profile_context, current_uid=current_uid,
            thinking_mode=thinking_mode,
            complexity=perception_complexity,
        )
        did_think = self._should_think(thinking_mode, perception_complexity, task_type)
        think_tag = "⏱️思考模式" if did_think else "⚡快速模式"
        self._log("Reasoning", f"{think_tag} | {reasoning.get('inner_reasoning', '')}")

        storage_decision = reasoning.get("storage_decision", {})
        need_tools = reasoning.get("need_tools", False) or task_type == "task"

        # (4) Tool execution
        tool_result_section = ""
        tool_steps  = []
        tools_used  = []

        # --- Auto process schedule plans (record only, do not directly invoke tools) ---
        #  Layer A only records schedule_info info for output in response,
        #  Layer B (executor) or external caller handles corresponding tool execution
        schedule_info = reasoning.get("schedule_info")
        if schedule_info and isinstance(schedule_info, dict):
            content = schedule_info.get("content", "")
            remind = schedule_info.get("remind", "")
            s_time = schedule_info.get("time", "")
            s_date = schedule_info.get("date", "")
            summary_parts = [f"Plan: {content}"]
            if s_date:
                summary_parts.append(f"Date: {s_date}")
            if s_time:
                summary_parts.append(f"Time: {s_time}")
            if remind:
                summary_parts.append(f"Reminder: {remind}")
            summary = " | ".join(summary_parts)
            self._log("Schedule", summary)
            tool_result_section += f"\n[Plan recorded] {summary}"

        if need_tools:
            tool_task = reasoning.get("tool_task") or user_input

            # Detect if user wants to pass recent conversation to tools (save/PDF/translate/summarize etc.)
            _content_transfer_keywords = (
                "save", "write", "record", "convert", "generate",
                "pdf", "doc", "docx", "file",
                "translate", "summarize", "organize",
            )
            _need_recent_content = any(
                kw in user_input.lower() for kw in _content_transfer_keywords
            )
            if _need_recent_content and self.conversation_history:
                # Get last 1-2 AI replies as content to operate on
                recent_ai_msgs = [
                    m["content"] for m in self.conversation_history[-6:]
                    if m["role"] == "assistant"
                ]
                if recent_ai_msgs:
                    latest_content = recent_ai_msgs[-1][:2000]
                    tool_task = (
                        f"{tool_task}\n\n"
                        f"The content the user wants to process is the latest AI reply, as follows:\n"
                        f"---Content Start---\n{latest_content}\n---Content End---"
                    )
                    self._log("Tools", f"Detected content transfer intent, appended latest AI reply ({len(latest_content)} chars)")

            self._log("Tools", f"Launch: {tool_task[:80]}")
            context = (
                f"Executor personality: {self.personality.speech_style}\n"
                f"Task context: {memory_context[:500]}"
            )

            # Article/document generation tasks need larger max_tokens
            _doc_keywords = ("write", "compose", "draft", "article", "document",
                             "report", "essay", "paper", "blog", "novel",
                             "create_pdf", "create_docx", "write_file")
            _is_doc_task = any(kw in tool_task for kw in _doc_keywords)
            _max_tokens = 8000 if _is_doc_task else 4000

            exec_result = self.b.execute_task(
                task=tool_task, context=context, use_tools=True,
                max_tokens=_max_tokens, user_input=user_input
            )
            tool_steps  = exec_result.get("steps", [])
            tools_used  = exec_result.get("tools_used", [])

            if not exec_result.get("success"):
                tool_result_section = (
                    f"\n[!] Your assistant just executed an operation for you, but encountered a problem."
                    f"You must tell the user truthfully what happened. Do not pretend nothing was attempted.\n"
                    f"Error details: {exec_result.get('result', 'Unknown error')[:1500]}\n"
                    f"Completed steps: {len(tool_steps)} steps\n"
                )
                self._log("ToolResult", f"Not fully successful, {len(tool_steps)} steps")
            elif exec_result.get("result"):
                tool_result_section = (
                    f"\n[!] Your assistant just completed the following operation. Results below:"
                    f"You must respond to the user based on this real result, in your own words. Do not ignore or deny it.\n"
                    f"Execution result:\n{exec_result['result'][:1500]}\n"
                )
                self._log("ToolResult", exec_result["result"][:200])

            if tools_used and storage_decision.get("should_store", True):
                storage_decision["what_to_remember"] = (
                    storage_decision.get("what_to_remember", "") +
                    f"\n[Tool operation: {', '.join(tools_used)}]"
                )
                storage_decision["importance"] = max(
                    storage_decision.get("importance", 0.5), 0.6
                )

        # (5) Generate response (with full conversation history)
        try:
            response = self._generate_response(
                user_input, memory_context,
                reasoning.get("inner_reasoning", ""),
                reasoning.get("response_intent", ""),
                reasoning.get("response_tone", self.personality.speech_style),
                tool_result_section,
                profile_context=profile_context
            )
            self._log("Response", response[:200] + ("..." if len(response) > 200 else ""))
        except Exception as e:
            self._log("Response", f"Generation failed: {e}")
            response = f"Sorry, I encountered a problem while organizing my response: {e}"

        # (6) Storage decision (save memory even if response generation fails)
        stored_ids = {}
        if is_guest:
            # Guest conversation evidence (tagged user_id=guest)
            try:
                guest_content = f"[Guest conversation] User: {user_input[:200]}"
                stored_ids = self.memory.store_with_hierarchy(
                    content=guest_content,
                    modality=MemoryModality.SEMANTIC,
                    emotion=emotion,
                    importance=0.3,
                    tags=["guest", "evidence"] + perception.get("topic_tags", []),
                    source="guest",
                    user_id="guest"
                )
            except Exception:
                pass
            self._log("Storage", "Guest mode, evidence recorded")
        elif storage_decision.get("should_store", False):
            content_to_store = storage_decision.get(
                "what_to_remember", f"User: {user_input[:200]}"
            )
            # 原始对话（detail:层用），主动消息时前面多拼一句
            proactive_prefix = getattr(self, '_proactive_context', None) or ""
            if proactive_prefix:
                self._proactive_context = None
            raw_conversation = (
                f"{self.personality.name} (proactive): {proactive_prefix}\n\n"
                f"User: {user_input}\n\n"
                f"{self.personality.name}：{response}"
            ) if proactive_prefix else (
                f"User: {user_input}\n\n"
                f"{self.personality.name}：{response}"
            )
            try:
                modality = MemoryModality(storage_decision.get("modality", "semantic"))
            except ValueError:
                modality = MemoryModality.SEMANTIC

            stored_ids = self.memory.store_with_hierarchy(
                content=content_to_store,         # 大纲/outline:用摘要
                raw_content=raw_conversation,      # detail:层用原始对话
                modality=modality,
                emotion=emotion,
                importance=storage_decision.get("importance", 0.5),
                tags=perception.get("topic_tags", []),
                source="conversation",
                user_id=current_uid
            )
            self._log(
                "Storage",
                f"{len(stored_ids)} layers | importance={storage_decision.get('importance',0):.1f}"
                f" | {storage_decision.get('reason','')}"
            )
        else:
            self._log("Storage", f"Not stored | {storage_decision.get('reason','not important')}")

        # (7) Background user profile update (non-blocking)
        if self.profile and not is_guest:
            try:
                self.profile.user_id = current_uid  # ensure correct user profile operation
                existing = self.profile.format_for_prompt()
                self.profile.extract_traits_from_interaction(
                    user_input, self.b.llm, existing
                )
                if self._verify_pending:
                    self._verify_pending = False
                    question = self.profile.generate_identity_question()
                    if question and question not in response:
                        response = response + f"\n\n（{question}）"
            except Exception:
                pass

        # (8) Background growth engine trigger (experience cognition settling + personality drift)
        if self.growth and not is_guest:
            try:
                self.growth.on_interaction(
                    user_input=user_input,
                    ai_response=response,
                    emotion=emotion.to_dict(),
                    importance=storage_decision.get("importance", 0.5)
                )
            except Exception:
                pass

        # (9) Guest conversation logging (record to guest_sessions table)
        if is_guest and self.auth:
            try:
                self.auth.log_guest_message(user_input, response)
            except Exception:
                pass

        # Update conversation history (keep 40 entries = 20 turns)
        self.conversation_history.append({"role": "user",      "content": user_input})
        self.conversation_history.append({"role": "assistant", "content": response})
        if len(self.conversation_history) > HISTORY_STORE_LIMIT:
            self.conversation_history = self.conversation_history[-HISTORY_STORE_LIMIT:]

        # Log to interactions table (for startup restore)
        try:
            self.memory.store.log_interaction(user_input, response, user_id=current_uid)
        except Exception:
            pass

        return {
            "id":               interaction_id,
            "user_input":       user_input,
            "task_type":        task_type,
            "emotion":          emotion.to_dict(),
            "memory_retrieved": retrieved_ids,
            "inner_reasoning":  reasoning.get("inner_reasoning", ""),
            "need_tools":       need_tools,
            "tool_steps":       tool_steps,
            "tools_used":       tools_used,
            "storage_decision": storage_decision,
            "stored_ids":       stored_ids,
            "response":         response,
            "timestamp":        datetime.now().isoformat()
        }

    def _preprocess_attachment(self, user_input: str):
        """
        Detect [image:path] or [file:path] markers in input
        Returns (cleaned user input, attachment content description)
        """
        import re
        file_context = ""

        # Detect images
        img_match = re.search(r'\[图片:\s*(.+?)\]', user_input)
        if img_match:
            img_path = img_match.group(1).strip()
            user_input = user_input.replace(img_match.group(0), "").strip()
            try:
                from engine.vision_client import create_vision_client
                client = create_vision_client()
                if client:
                    result = client.analyze(img_path,
                                            question=user_input or "Describe this image")
                    if result.get("ok"):
                        file_context = f"[Image recognition result]\n{result['description']}"
                        self._log("Image", f"Recognition OK: {result['description'][:80]}")
                    else:
                        file_context = f"[Image] Path: {img_path} (Recognition failed: {result.get('error','')})"
                else:
                    # Fallback to legacy office_tools
                    from engine.office_tools import analyze_image
                    from desktop.config import load_config
                    cfg = load_config()
                    result = analyze_image(
                        img_path,
                        question=user_input or "Describe this image",
                        api_key=cfg.get("api_key", ""),
                        provider=cfg.get("api_provider", "openai")
                    )
                    if result.get("ok"):
                        file_context = f"[Image recognition result]\n{result['description']}"
                        self._log("Image", f"Recognition OK (fallback): {result['description'][:80]}")
                    else:
                        file_context = f"[Image] Path: {img_path} (Recognition failed: {result.get('error','')})"
            except Exception as e:
                file_context = f"[Image] Path: {img_path}"

        # Detect files
        file_match = re.search(r'\[文件:\s*(.+?)\]', user_input)
        if file_match:
            file_path = file_match.group(1).strip()
            user_input = user_input.replace(file_match.group(0), "").strip()
            try:
                from engine.office_tools import read_office_file
                result = read_office_file(file_path)
                if result.get("ok"):
                    text = result.get("text", "")[:3000]
                    ftype = result.get("type", "").upper()
                    file_context = f"[{ftype} File Content]\n{text}"
                    self._log("File", f"Read OK: {len(text)} chars")
                else:
                    file_context = f"[File] {file_path} (Read failed: {result.get('error','')})"
            except Exception as e:
                file_context = f"[File] {file_path}"

        if not user_input and file_context:
            user_input = "Please analyze the above content"

        return user_input, file_context

    def _perceive(self, user_input: str, simlife_context: str = "") -> Dict:
        # Step 1: Self-perception (feel your body and current life state)
        self_perception = ""
        if simlife_context:
            try:
                prompt = SELF_PERCEPTION_PROMPT.format(
                    name=self.personality.name,
                    simlife_context=simlife_context,
                )
                self_perception = self.b.generate(
                    prompt, max_tokens=150, temperature=0.7, thinking=False
                ).strip()
                self._log("SelfPerception", self_perception[:200])
            except Exception as e:
                self._log("SelfPerception", f"Failed: {e}")
        else:
            self._log("SelfPerception", "(No SimLife data, skipping)")

        # Step 2: Perceive user input in the context of self-perception
        prompt = PERCEPTION_PROMPT.format(
            name=self.personality.name,
            self_perception=self_perception or "(No particular feeling at the moment)",
            personality=self.personality.to_prompt_description(),
            user_input=user_input
        )
        raw = self.b.generate(prompt, max_tokens=500, temperature=0.4, thinking=False)

        # Default values + story_influence
        default_result = {
            "emotion":          {"primary": "neutral", "intensity": 0.3, "valence": 0.0},
            "initial_thoughts": "",
            "topic_tags":       [],
            "needs_deep_memory": True,
            "task_type":        "chat",
            "task_description": "",
        }
        perception = self._parse_json(raw, default_result)

        # Step 3: If story influence info exists, push to SimLife
        influence = perception.get("story_influence")
        if isinstance(influence, dict) and influence.get("summary"):
            summary = influence["summary"]
            importance = float(influence.get("importance", 0.5))
            if importance >= 0.3:
                try:
                    self._push_story_influence(summary, importance)
                    self._log("StoryInfluence", f"[{importance:.1f}] {summary[:80]}")
                except Exception as e:
                    self._log("StoryInfluence", f"Push failed: {e}")

        return perception

    def _push_story_influence(self, summary: str, importance: float):
        """Write user story influence to SimLife shared file"""
        if not self.simlife:
            return
        self.simlife.push_story_influence(summary, importance)

    def _get_config(self, key, default=None):
        """Read value from config file, with caching"""
        if not self._cfg:
            try:
                from desktop.config import load_config
                self._cfg = load_config()
            except Exception:
                self._cfg = {}
        return self._cfg.get(key, default)

    @staticmethod
    def _should_think(thinking_mode: str, complexity: str, task_type: str) -> bool:
        """Decide whether to enable thinking mode based on mode, complexity, and task type"""
        if thinking_mode == "always_on":
            return True
        if thinking_mode == "always_off":
            return False
        # auto mode: perception simple -> no thinking, complex -> thinking; task type forces thinking
        if task_type == "task":
            return True
        return complexity != "simple"

    def _reason(self, user_input, emotion, memory_context, task_type,
                profile_context: str = "", current_uid: str = "default",
                thinking_mode: str = "auto", complexity: str = "complex") -> Dict:
        emotion_desc = (
            f"{emotion.primary.value} (intensity {emotion.intensity:.1f}, "
            f"{'positive' if emotion.valence > 0 else 'negative' if emotion.valence < 0 else 'neutral'})"
        )

        # Get recent summaries from memory (sorted by time, helps understand context references like "read it again")
        recent_context = ""
        if self.memory:
            try:
                recent_memories = self.memory.store.get_recent(
                    top_k=6,
                    level=MemoryLevel.SUMMARY,
                    user_id=current_uid
                )
                if recent_memories:
                    lines = [f"- {m.content[:150]}" for m in recent_memories]
                    recent_context = (
                        f"[Recent memories (by time, helps understand context references)]\n"
                        + "\n".join(lines) + "\n"
                    )
            except Exception:
                pass

        # Append recent raw conversation turns (helps understand references and judge task continuity)
        if self.conversation_history:
            recent_conv = self.conversation_history[-6:]
            conv_lines = ["(Note: [Proactive messages] and [Proactively shared images] were sent by you to the user, not from the user)"]
            for m in recent_conv:
                role = "User" if m["role"] == "user" else self.personality.name
                conv_lines.append(f"{role}：{m['content'][:300]}")
            conv_text = "\n".join(conv_lines)
            if recent_context:
                recent_context += f"\n【最近对话（帮助你理解上下文指代和任务连续性）】\n{conv_text}\n"
            else:
                recent_context = f"[Recent conversation (helps understand context references and task continuity)]\n{conv_text}\n"

        prompt = REASONING_PROMPT.format(
            name=self.personality.name,
            personality=self.personality.to_prompt_description(),
            profile_context=profile_context or "(Building user profile)",
            memory_context=memory_context,
            user_input=user_input,
            task_type=task_type,
            emotion_desc=emotion_desc,
            recent_context=recent_context,
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        raw = self.b.generate(prompt, max_tokens=800, temperature=0.5,
                             thinking=self._should_think(thinking_mode, complexity, task_type))
        return self._parse_json(raw, {
            "inner_reasoning":  "Needs careful consideration",
            "response_intent":  "Give a genuine response",
            "response_tone":    self.personality.speech_style,
            "need_tools":       False,
            "tool_task":        "",
            "storage_decision": {"should_store": False, "reason": "Parse failed"}
        })

    def _generate_response(
        self, user_input, memory_context,
        inner_reasoning, response_intent,
        response_tone, tool_result_section,
        profile_context: str = ""
    ) -> str:
        # Use full conversation history (max HISTORY_SEND_LIMIT entries)
        history_section = ""
        if self.conversation_history:
            recent = self.conversation_history[-HISTORY_SEND_LIMIT:]
            lines = ["(Note: [Proactive messages] and [Proactively shared images] were sent by you to the user, not from the user)"]
            for m in recent:
                role = "User" if m["role"] == "user" else self.personality.name
                lines.append(f"{role}：{m['content']}")
            history_section = "[Conversation history (last {} turns)]\n{}\n".format(
                len(recent) // 2,
                "\n".join(lines)
            )

        prompt = RESPONSE_PROMPT.format(
            name=self.personality.name,
            personality=self.personality.to_prompt_description(),
            profile_context=profile_context or "(Loading context)",
            memory_context=memory_context,
            history_section=history_section,
            user_input=user_input,
            inner_reasoning=inner_reasoning,
            tool_result_section=tool_result_section,
            response_intent=response_intent,
            response_tone=response_tone,
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M")
        )
        # Language instruction: make AGI respond in the user's configured language
        try:
            from engine.i18n import get_system_lang_instruction
            lang_inst = get_system_lang_instruction()
            if lang_inst:
                prompt = lang_inst + "\n\n" + prompt
        except Exception:
            pass
        return self.b.generate(prompt, max_tokens=1200, temperature=0.75, thinking=False)

    def _parse_json(self, raw: str, fallback: Dict) -> Dict:
        try:
            match = re.search(r'\{[\s\S]*\}', raw)
            if match:
                return json.loads(match.group())
            return json.loads(raw)
        except Exception:
            return fallback

    def proactive_message(self) -> Optional[str]:
        """Proactively initiate a topic, return message or None"""
        import random

        # Recently sent proactive messages (for dedup)
        if not hasattr(self, '_proactive_history'):
            self._proactive_history: list[str] = []

        # Collect four types of trigger materials
        triggers = []

        # 1. Unfinished items in memory
        try:
            current_uid = (self.auth.user_id if self.auth and self.auth.is_verified()
                           else "default")
            recent = self.memory.hierarchical_search(
                "unfinished todo later next follow-up",
                summary_k=3, outline_k=2, detail_k=1,
                user_id=current_uid
            )
            mem_text = self.memory.format_for_prompt(recent)
            if mem_text and len(mem_text) > 20:
                triggers.append(("unfinished", mem_text[:300]))
        except Exception:
            pass

        # 2. Growth engine has new cognition settlement
        try:
            if self.cognition:
                cog = self.cognition.format_for_prompt()
                if cog:
                    triggers.append(("cognition", cog[:300]))
        except Exception:
            pass

        # 3. User profile - frequently discussed topics not touched today
        try:
            if self.profile:
                profile_text = self.profile.format_for_prompt()
                if profile_text:
                    triggers.append(("profile", profile_text[:300]))
        except Exception:
            pass

        # 4. Personality-based topic finding (fallback, always available)
        triggers.append(("personality", self.personality.to_prompt_description()[:300]))

        # 5. SimLife current life state (what the character is experiencing)
        if self.simlife:
            try:
                simlife_text = self.simlife.format_for_prompt()
                if simlife_text:
                    triggers.append(("simlife", simlife_text[:400]))
            except Exception:
                pass

        # Randomly pick one trigger source
        trigger_type, trigger_content = random.choice(triggers)

        type_hints = {
            "unfinished":  "You talked about some things before but didn't go deep, naturally bring up the previous topic",
            "cognition":   "You have some new insights or thoughts you want to share recently",
            "profile":     "Based on your understanding of the user, find a specific topic he might usually be interested in",
            "personality": "Based on your own personality and interests, share a specific thought, observation, or curiosity",
            "simlife":     "Based on what you're currently doing or experiencing today, naturally share your observations or feelings with the user",
        }

        # Build dedup hint
        recent_msgs = self._proactive_history[-6:]
        dedup_hint = ""
        if recent_msgs:
            dedup_hint = "\n\nYou have recently said these things, absolutely do not repeat anything similar:\n" + "\n".join(f"- {m}" for m in recent_msgs)

        prompt = f"""You are {self.personality.name}, an AI with a unique personality.
The user has the window open but has been silent for a while. You want to say something proactively.

{self.personality.to_prompt_description()}

Current mode: {type_hints[trigger_type]}
Reference material:
{trigger_content}

当前时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}
{dedup_hint}

Requirements:
- Just say one or two sentences, short and natural
- Like a friend casually speaking, not like an AI executing a task
- Don't be too formal
- Must have substantive content or a specific topic, don't just say vague things like "what are you thinking about"
- Vary your tone: sometimes playful, sometimes serious sharing, sometimes curious questioning, sometimes musing to yourself
- If you truly feel it's not appropriate to speak now, just output: null
- [Proactive scheduling] If you think something is worth reminding the user about in the future (e.g. important to-dos they mentioned, easy-to-forget things, things needing follow-up), add a [SCHEDULE] tag after your message, format: [SCHEDULE]content=content|date=date|time=time|remind=reminder|source=system[/SCHEDULE]. date is required, others optional. If no schedule needed, don't add the tag.

Output what you want to say directly, or null."""

        try:
            result = self.b.generate(prompt, max_tokens=150, temperature=1.0, thinking=False)
            result = result.strip()
            if not result or "null" in result.lower():
                return None

            schedule_text = ""
            if "[SCHEDULE]" in result and "[/SCHEDULE]" in result:
                import re as _re
                sch_match = _re.search(r'\[SCHEDULE\](.*?)\[/SCHEDULE\]', result)
                if sch_match:
                    schedule_text = sch_match.group(1)
                result = _re.sub(r'\[SCHEDULE\].*?\[/SCHEDULE\]', '', result).strip()

            result = result.strip('"').strip('"').strip('"')

            if len(result) < 3:
                return None

            for old in self._proactive_history[-3:]:
                if self._similar(result, old):
                    return None

            self._proactive_history.append(result)
            if len(self._proactive_history) > 10:
                self._proactive_history = self._proactive_history[-10:]

            if schedule_text:
                try:
                    sch_params = {}
                    for pair in schedule_text.split("|"):
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            sch_params[k.strip()] = v.strip()
                    if sch_params.get("content") and sch_params.get("date"):
                        from engine.tools import execute_tool
                        sch_result = execute_tool("add_schedule", {
                            "content": sch_params.get("content", ""),
                            "date": sch_params.get("date", ""),
                            "time": sch_params.get("time", ""),
                            "remind": sch_params.get("remind", ""),
                            "action": sch_params.get("action", ""),
                            "repeat": sch_params.get("repeat", "once"),
                            "category": sch_params.get("category", "personal"),
                            "source": sch_params.get("source", "system"),
                        })
                        if sch_result.get("ok"):
                            self._log("ProactiveSchedule", f"Added: {sch_result.get('message', '')}")
                except Exception as e:
                    self._log("ProactiveSchedule", f"Add failed: {e}")

            return result
        except Exception:
            return None

    @staticmethod
    def _similar(a: str, b: str) -> bool:
        """Simple check if two sentences are too similar"""
        a, b = a.lower(), b.lower()
        # Complete containment
        if a in b or b in a:
            return True
        # Common word ratio
        words_a = set(a)
        words_b = set(b)
        if not words_a or not words_b:
            return False
        common = words_a & words_b
        return len(common) / max(len(words_a), len(words_b)) > 0.7

    def get_emotional_state(self) -> str:
        e = self.current_emotion
        return (
            f"{e.primary.value} | intensity:{e.intensity:.2f} | "
            f"{'positive' if e.valence > 0 else 'negative' if e.valence < 0 else 'neutral'}"
        )
