"""
AI Generator - Character Cards + NPC Cards + Activity Descriptions + Event Queue
Supports: office worker / freelancer / student / travel blogger
Supports multi-world: modern (default) + custom worlds (fantasy/scifi/...)
"""
import json
import random
import sys
from pathlib import Path

# Reuse the main project LLM client
_PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from engine.llm_client import create_client


def _get_world_context() -> str:
    """Get current worldview context text; returns empty string for modern world"""
    try:
        from simlife.worlds.world_manager import load_world_setting, build_world_context
        ws = load_world_setting()
        if ws:
            return build_world_context(ws)
    except Exception:
        pass
    return ""


def _get_story_influences() -> str:
    """Read story influence info from user chat"""
    try:
        from engine.simlife_client import SimLifeClient
        sl = SimLifeClient()
        return sl.get_story_influences()
    except Exception:
        return ""


def _get_world_guide(guide_type: str = "character") -> str:
    """Get worldview generation guide (character/activity/event)"""
    try:
        from simlife.worlds.world_manager import load_world_setting
        ws = load_world_setting()
        if ws:
            if guide_type == "character":
                from simlife.worlds.world_manager import build_character_guide
                return build_character_guide(ws)
            elif guide_type == "activity":
                from simlife.worlds.world_manager import build_activity_guide
                return build_activity_guide(ws)
            elif guide_type == "event":
                from simlife.worlds.world_manager import build_event_guide
                return build_event_guide(ws)
    except Exception:
        pass
    return ""


def generate_world_setting(
    world_type: str = "fantasy",
    core_theme: str = "",
    character_role: str = "",
) -> dict:
    """
    Generate a complete world setting JSON using LLM.
    Returns world_setting dict or None.
    """
    import re

    llm = get_llm_client()

    type_names = {
        "fantasy": "Fantasy Magic",
        "scifi": "Sci-Fi Future",
        "xianxia": "Cultivation",
        "post_apocalyptic": "Post-Apocalyptic",
        "custom": "Custom",
    }
    type_label = type_names.get(world_type, world_type)

    prompt = f"""You are a professional world-building designer. Create a {type_label} world setting.

Core theme: {core_theme}
Character's identity in this world: {character_role or "(not specified)"}

Design requirements:
1. Self-consistent world: geography, races, power system, factions must have logical causal relationships
2. Rich details: each region, race, faction must have uniqueness
3. Story potential: leave conflict points and suspense
4. Appropriate quantity: 4-8 regions, 3-6 races, 3-5 factions, 3-5 dungeons
5. All names should have stylistic unity

Return complete JSON with these top-level fields:
world_id (lowercase id), world_name, world_type, era, communication (device/device_description/narrative_style), geography (overview/regions array), races array, power_system, factions array, history, daily_life, dangers (monster_types/dungeons array), character_generation_guide, activity_generation_guide, event_generation_guide

Return only JSON, no other text. Ensure JSON can be parsed directly."""

    try:
        response = llm.generate(prompt, max_tokens=8000, temperature=0.8)
        response = response.strip()
        # Extract JSON (may be wrapped in markdown code block)
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            response = json_match.group(0)

        setting = json.loads(response)

        # Ensure world_id is valid
        if not setting.get("world_id") or setting["world_id"] == "modern":
            import hashlib
            setting["world_id"] = "world_" + hashlib.md5(core_theme.encode()).hexdigest()[:8]

        # Ensure world_type
        if not setting.get("world_type"):
            setting["world_type"] = world_type

        return setting
    except Exception as e:
        print(f"[SimLife] World setting generation failed: {e}")
        return None


def get_llm_client(config: dict = None):
    """Get LLM client instance (from SimLife config or main project config)"""
    if config is None:
        config_path = Path(__file__).parent.parent / "data" / "simlife_config.json"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        else:
            config = {}

    import os
    if sys.platform == "win32":
        _cfg_dir = Path(os.environ.get("APPDATA", str(Path.home()))) / "AGI-Desktop"
    else:
        _cfg_dir = Path.home() / ".agi-desktop"
    main_config_path = _cfg_dir / "config.json"
    main_cfg = {}
    if main_config_path.exists():
        with open(main_config_path, "r", encoding="utf-8") as f:
            main_cfg = json.load(f)

    provider = config.get("llm_provider", "") or main_cfg.get("api_provider", "deepseek")
    api_key = config.get("llm_api_key", "") or main_cfg.get("api_key", "")
    model = config.get("llm_model", None) or main_cfg.get("llm_model", None)

    return create_client(api_key=api_key, provider=provider, model=model)


def _detect_work_style(occupation: str) -> str:
    """Infer work style from occupation description"""
    from .character import detect_work_style
    return detect_work_style(occupation).value


def generate_character_card(anchor: dict, agidpa_personality: dict = None) -> dict:
    """
    Generate complete character card from anchor and personality data.
    Auto-selects generation template based on occupation type.
    Returns CharacterCard dict (without basic.name, to be filled later).
    """
    llm = get_llm_client()

    name = anchor.get("character_name", "AI")
    city = anchor.get("city", "New York")
    occupation = anchor.get("occupation_hint", "UI Designer")
    age = anchor.get("age", 24)
    personality = anchor.get("personality_word", "gentle")

    extra_context = ""
    if agidpa_personality:
        traits = agidpa_personality.get("personality_traits", [])
        style = agidpa_personality.get("speaking_style", "")
        bg = agidpa_personality.get("background_story", "")
        if traits:
            extra_context += f"\npersonality_traits: {', '.join(traits)}"
        if style:
            extra_context += f"\nSpeaking style: {style}"
        if bg:
            extra_context += f"\nBackground story: {bg[:100]}"

    work_style = _detect_work_style(occupation)

    if work_style == "freelance":
        prompt = _build_freelance_prompt(name, age, city, occupation, personality, extra_context)
    elif work_style == "student":
        prompt = _build_student_prompt(name, age, city, occupation, personality, extra_context)
    elif work_style == "travel":
        prompt = _build_travel_prompt(name, age, city, occupation, personality, extra_context)
    else:
        prompt = _build_office_prompt(name, age, city, occupation, personality, extra_context)

    # Inject worldview setting (for non-modern world)
    world_ctx = _get_world_context()
    if world_ctx:
        prompt = world_ctx + _get_world_guide("character") + "\n\n" + prompt

    try:
        response = llm.generate(prompt, max_tokens=2500, temperature=0.8)
        response = response.strip()
        if response.startswith("```"):
            lines = response.split("\n")
            response = "\n".join(lines[1:])
            if response.endswith("```"):
                response = response[:-3]
            response = response.strip()

        card = json.loads(response)
        card["basic"]["name"] = name
        # Ensure work_style exists
        if "work_style" not in card.get("basic", {}):
            card["basic"]["work_style"] = work_style
        else:
            work_style = card["basic"]["work_style"]
        # Ensure work_location_weights exists
        if work_style == "freelance" and "work_location_weights" not in card.get("basic", {}):
            card["basic"]["work_location_weights"] = {"home": 50, "cafe": 25, "outdoor": 15, "studio": 10}
        # Ensure life_goals exists
        if "life_goals" not in card:
            card["life_goals"] = []
        # Ensure work_start/work_end
        if "work_start" not in card.get("daily_schedule", {}):
            card["daily_schedule"]["work_start"] = card["daily_schedule"].get("arrive_work", "10:00")
        if "work_end" not in card.get("daily_schedule", {}):
            card["daily_schedule"]["work_end"] = card["daily_schedule"].get("leave_work", "18:00")
        # Backward compat: commute info
        if work_style in ("freelance", "remote", "travel") and "commute" not in card:
            card["commute"] = {"method": "", "line": "", "duration_minutes": 0}
        # Travel blogger: ensure travel_plan
        if work_style == "travel" and "travel_plan" not in card:
            card["travel_plan"] = {"enabled": True, "destinations": []}
        # Backward compat: wardrobe missing travel field
        if "travel" not in card.get("wardrobe", {}):
            card.setdefault("wardrobe", {})["travel"] = "lightweight travel outfit"
            card.setdefault("wardrobe", {})["travel_en"] = "lightweight travel outfit with backpack and camera"
        # -- Auto generate birthday: personality -> zodiac -> random date --
        if "birth_date" not in card.get("basic", {}) or not card["basic"].get("birth_date"):
            from .birthday_engine import auto_generate_birthday
            bd_info = auto_generate_birthday(personality, age)
            card["basic"]["birth_date"] = bd_info["birth_date"]
            card["basic"]["zodiac"] = bd_info["zodiac"]
        return card
    except Exception as e:
        print(f"[SimLife] Character card generation failed: {e}")
        return None


def _build_office_prompt(name, age, city, occupation, personality, extra_context):
    """Office worker generation template"""
    return f"""Generate a detailed character card for a virtual character named "{name}".

Basic info:
- Age: {age}
- City: {city}
- Occupation: {occupation} (office worker, fixed location)
- Personality keywords: {personality}{extra_context}

Generate the following information, return in JSON format:
{{
  "basic": {{
    "age": {age},
    "city": "{city}",
    "district": "a real district in {city}",
    "occupation": "{occupation}",
    "work_style": "office",
    "company_name": "a plausible company name",
    "company_area": "a plausible business district name",
    "work_location_weights": {{"home": 0, "cafe": 0, "outdoor": 0, "studio": 0}},
    "nationality": "Nationality/ethnicity (e.g. american, british, japanese, korean, mixed)",
    "hair_color": "Hair color (e.g. black, brown, dark brown, blonde)",
    "eye_color": "Eye color (e.g. brown, dark brown, black, blue)",
    "body_type": "Body type (e.g. tall and slender, petite, average height, athletic)"
  }},
  "home": {{
    "type": "plausible home type",
    "description": "Home description under 30 words with life details",
    "has_roommate": false,
    "pets": "Pet names or empty string if none"
  }},
  "family": {{
    "parents_location": "a plausible city",
    "contact_frequency": "a plausible contact frequency",
    "notes": "a small family detail"
  }},
  "daily_schedule": {{
    "wake_up": "07:30",
    "leave_home": "08:45",
    "arrive_work": "09:30",
    "lunch_break_start": "12:00",
    "lunch_break_end": "13:00",
    "leave_work": "18:30",
    "arrive_home": "19:15",
    "sleep": "23:30",
    "work_start": "09:30",
    "work_end": "18:30"
  }},
  "commute": {{
    "method": "subway/bus/bike",
    "line": "specific route line",
    "duration_minutes": "commute duration in minutes"
  }},
  "locations": {{
    "home_address_hint": "a real street near {city}",
    "company_landmark": "a real landmark in {city}",
    "favorite_cafe": "a real cafe name",
    "supermarket": "a real supermarket name",
    "park": "a real park name",
    "weekend_hangout": "a real shopping/street name",
    "frequent_outdoor_spots": "frequently visited outdoor spots"
  }},
  "habits": {{
    "morning_drink": "morning drink preference",
    "lunch_style": "lunch habits",
    "evening_routine": "evening routine",
    "weekend_morning": "weekend morning routine"
  }},
  "current_context": "What they are busy with lately, under 30 words",
  "pixel_appearance": {{
    "hair_color": "#hex color",
    "hair_style": "hairstyle",
    "default_outfit_color": "#hex color"
  }},
  "life_goals": [
    {{"category": "Career", "description": "a short-term career-related goal", "target_date": "", "progress": 0, "priority": 1}},
    {{"category": "Life", "description": "a lifestyle goal (e.g. get drivers license, learn swimming, workout, learn painting, gardening, cooking)", "target_date": "", "progress": 0, "priority": 2}},
    {{"category": "Learning", "description": "a learning/growth goal", "target_date": "", "progress": 0, "priority": 3}}
  ],
  "wardrobe": {{
    "home": "Comfortable home clothes (short English description)",
    "work": "Office or business casual outfit (short English description)",
    "casual": "Casual everyday outfit (short English description)",
    "outdoor": "Outdoor activity outfit (short English description)",
    "formal": "Formal occasion attire (short English description)",
    "sport": "Sports/workout clothes (short English description)",
    "sleep": "Sleepwear/pajamas (short English description)",
    "home_en": "English description of home outfit for image generation",
    "work_en": "English description of work outfit",
    "casual_en": "English description of casual outfit",
    "outdoor_en": "English description of outdoor outfit",
    "formal_en": "English description of formal outfit",
    "sport_en": "English description of sport outfit",
    "sleep_en": "English description of sleepwear"
  }}
}}

Return only JSON, no other content. All locations must be real places in {city}. Life goals should be specific and interesting. Wardrobe should match the character's gender, age, and style preferences. If the character is athletic, outdoor and sport outfits should be more detailed."""


def _build_freelance_prompt(name, age, city, occupation, personality, extra_context):
    """Freelance generation template"""
    return f"""Generate a detailed character card for a virtual character named "{name}".

Basic info:
- Age: {age}
- City: {city}
- Occupation: {occupation} (freelancer/independent worker, flexible schedule)
- Personality keywords: {personality}{extra_context}

Important: This is a freelancer with no fixed company, no daily commute. Generate a reasonable life rhythm based on their specific occupation.

Generate the following information, return in JSON format:
{{
  "basic": {{
    "age": {age},
    "city": "{city}",
    "district": "a real district in {city}",
    "occupation": "{occupation}",
    "work_style": "freelance",
    "company_name": "",
    "company_area": "",
    "work_location_weights": {{
      "home": "frequency weight for working at home (integer 0-100)",
      "cafe": "frequency weight for working at cafes (integer 0-100)",
      "outdoor": "frequency weight for outdoor work (shooting/interviews etc) (integer 0-100)",
      "studio": "frequency weight for working at a studio (integer 0-100)"
    }},
    "nationality": "Nationality/ethnicity (e.g. american, british, japanese, korean, mixed)",
    "hair_color": "Hair color (e.g. black, brown, dark brown, blonde)",
    "eye_color": "Eye color (e.g. brown, dark brown, black, blue)",
    "body_type": "Body type (e.g. tall and slender, petite, average height, athletic)"
  }},
  "home": {{
    "type": "plausible home type (freelancers may have a study or workspace)",
    "description": "Home description under 30 words reflecting freelance lifestyle",
    "has_roommate": false,
    "pets": "Having a pet adds life flavor, empty string if none"
  }},
  "family": {{
    "parents_location": "a plausible city",
    "contact_frequency": "a plausible contact frequency",
    "notes": "Family's attitude toward this career, a small detail"
  }},
  "daily_schedule": {{
    "wake_up": "reasonable wake-up time (freelancers usually wake later than office workers)",
    "leave_home": "10:00",
    "arrive_work": "10:30",
    "lunch_break_start": "12:30",
    "lunch_break_end": "14:00",
    "leave_work": "19:00",
    "arrive_home": "19:00",
    "sleep": "reasonable bedtime (may be later than office workers)",
    "work_start": "actual work start time",
    "work_end": "actual work end time"
  }},
  "commute": {{
    "method": "",
    "line": "",
    "duration_minutes": 0
  }},
  "locations": {{
    "home_address_hint": "a real street near {city}",
    "company_landmark": "",
    "favorite_cafe": "favorite cafe to work from",
    "supermarket": "a real supermarket name",
    "park": "a real park name (place to relax/find inspiration)",
    "weekend_hangout": "a real shopping district/street name",
    "frequent_outdoor_spots": "frequently visited work-related outdoor locations (shooting sites, interview spots, etc.)"
  }},
  "habits": {{
    "morning_drink": "morning drink preference",
    "lunch_style": "lunch habits (cook at home, takeout, or nearby eateries)",
    "evening_routine": "evening relaxation routine",
    "weekend_morning": "weekend morning habit"
  }},
  "current_context": "What project/creation they are busy with lately, under 30 words",
  "pixel_appearance": {{
    "hair_color": "#hex color",
    "hair_style": "hairstyle",
    "default_outfit_color": "#hex color"
  }},
  "life_goals": [
    {{"category": "Career", "description": "a goal directly related to {occupation} (e.g. follower count, client volume, portfolio pieces)", "target_date": "", "progress": 0, "priority": 1}},
    {{"category": "Life", "description": "a personal life goal (e.g. get drivers license, learn swimming, workout, learn painting, gardening, cooking, get a pet, travel plan, learn guitar, learn to dance, get a certificate)", "target_date": "", "progress": 0, "priority": 2}},
    {{"category": "Health", "description": "a health-related goal (e.g. running, fitness, sleep earlier, less takeout)", "target_date": "", "progress": 0, "priority": 3}},
    {{"category": "Finance", "description": "a financial goal (e.g. save for equipment, reach monthly income target)", "target_date": "", "progress": 0, "priority": 4}}
  ],
  "wardrobe": {{
    "home": "Comfortable home clothes (freelancers may wear loungewear all day, short English description)",
    "work": "Outfit for meeting clients or formal work (freelancers may not wear suits, match occupation style)",
    "casual": "Going out to cafes or hanging out outfit",
    "outdoor": "Outdoor shooting/interview/sports outfit (adjust based on occupation)",
    "formal": "Formal occasion or date outfit",
    "sport": "Sports/workout clothes",
    "sleep": "Sleepwear",
    "home_en": "English description for image generation",
    "work_en": "English work outfit description",
    "casual_en": "English casual outfit",
    "outdoor_en": "English outdoor outfit",
    "formal_en": "English formal outfit",
    "sport_en": "English sport outfit",
    "sleep_en": "English sleepwear"
  }}
}}

Return only JSON, no other content. All locations must be real places in {city}. Schedule should match a freelancer's real rhythm, not copy the office worker template. Life goals should be specific and interesting, fitting {occupation} as an occupation. Wardrobe should match the character's gender, age and occupation style."""


def _build_student_prompt(name, age, city, occupation, personality, extra_context):
    """Student generation template"""
    return f"""Generate a detailed character card for a virtual character named "{name}".

Basic info:
- Age: {age}
- City: {city}
- Occupation: {occupation} (student)
- Personality keywords: {personality}{extra_context}

Generate the following information, return in JSON format:
{{
  "basic": {{
    "age": {age},
    "city": "{city}",
    "district": "a real district in {city} (near university area)",
    "occupation": "{occupation}",
    "work_style": "student",
    "company_name": "school/university name",
    "company_area": "school location area",
    "work_location_weights": {{"home": 40, "cafe": 25, "outdoor": 5, "studio": 0}},
    "nationality": "Nationality/ethnicity (e.g. american, british, japanese, korean, mixed)",
    "hair_color": "Hair color (e.g. black, brown, dark brown, blonde)",
    "eye_color": "Eye color (e.g. brown, dark brown, black, blue)",
    "body_type": "Body type (e.g. tall and slender, petite, average height, athletic)"
  }},
  "home": {{
    "type": "dorm/rental apartment",
    "description": "Home description under 30 words",
    "has_roommate": true,
    "pets": ""
  }},
  "family": {{
    "parents_location": "a plausible city",
    "contact_frequency": "a plausible contact frequency",
    "notes": "a small family detail"
  }},
  "daily_schedule": {{
    "wake_up": "reasonable wake-up time",
    "leave_home": "time to head to class",
    "arrive_work": "time to arrive at classroom/library",
    "lunch_break_start": "12:00",
    "lunch_break_end": "13:00",
    "leave_work": "time class ends",
    "arrive_home": "time back to dorm/home",
    "sleep": "reasonable bedtime",
    "work_start": "self-study start time",
    "work_end": "self-study end time"
  }},
  "commute": {{
    "method": "walking/biking/subway",
    "line": "specific route (if applicable)",
    "duration_minutes": 15
  }},
  "locations": {{
    "home_address_hint": "a real street near {city}",
    "company_landmark": "school/university name",
    "favorite_cafe": "favorite cafe name",
    "supermarket": "a real supermarket name",
    "park": "a real park name",
    "weekend_hangout": "a real shopping district/street name",
    "frequent_outdoor_spots": ""
  }},
  "habits": {{
    "morning_drink": "morning drink preference",
    "lunch_style": "cafeteria/takeout/off-campus eateries",
    "evening_routine": "evening relaxation routine",
    "weekend_morning": "weekend morning routine"
  }},
  "current_context": "What they are busy with lately (exams, thesis, clubs etc.), under 30 words",
  "pixel_appearance": {{
    "hair_color": "#hex color",
    "hair_style": "hairstyle",
    "default_outfit_color": "#hex color"
  }},
  "life_goals": [
    {{"category": "Academics", "description": "an academic goal (e.g. grad school, certifications, GPA)", "target_date": "", "progress": 0, "priority": 1}},
    {{"category": "Life", "description": "a life goal (e.g. learn swimming, get drivers license, travel, learn an instrument)", "target_date": "", "progress": 0, "priority": 2}},
    {{"category": "Social", "description": "a social goal (e.g. join a club, make new friends)", "target_date": "", "progress": 0, "priority": 3}}
  ],
  "wardrobe": {{
    "home": "Comfortable clothes for dorm/apartment (short English description)",
    "work": "Daily outfit for class (student style, no need for formal wear)",
    "casual": "Weekend casual outfit",
    "outdoor": "Outdoor sports/activity outfit",
    "formal": "Outfit for events/interviews/formal occasions",
    "sport": "Sports/workout clothes",
    "sleep": "Sleepwear",
    "home_en": "English description for image generation",
    "work_en": "English daily outfit for class",
    "casual_en": "English casual outfit",
    "outdoor_en": "English outdoor outfit",
    "formal_en": "English formal outfit",
    "sport_en": "English sport outfit",
    "sleep_en": "English sleepwear"
  }}
}}

Return only JSON, no other content. All locations must be real places in {city}. Wardrobe should match the student's gender and style, avoid overly mature business attire."""


def _build_travel_prompt(name, age, city, occupation, personality, extra_context):
    """Travel blogger generation template"""
    return f"""Generate a detailed character card for a virtual character named "{name}".

Basic info:
- Age: {age}
- Base city: {city} (home base and travel departure point)
- Occupation: {occupation} (travel blogger/travel influencer, travels worldwide filming videos)
- Personality keywords: {personality}{extra_context}

Important: This is a travel blogger with an irregular schedule, frequently moving between cities and countries.
No fixed company; work time equals travel and filming time. {city} is their home base where they stay when not traveling.

Generate the following information, return in JSON format:
{{
  "basic": {{
    "age": {age},
    "city": "{city}",
    "district": "a real district in {city}",
    "occupation": "{occupation}",
    "work_style": "travel",
    "company_name": "",
    "company_area": "",
    "work_location_weights": {{"home": 20, "cafe": 10, "outdoor": 60, "studio": 10}},
    "nationality": "Nationality/ethnicity (e.g. american, british, japanese, korean, mixed)",
    "hair_color": "Hair color (e.g. black, brown, dark brown, blonde)",
    "eye_color": "Eye color (e.g. brown, dark brown, black, blue)",
    "body_type": "Body type (e.g. tall and slender, petite, average height, athletic)"
  }},
  "home": {{
    "type": "plausible home type (may be small since they travel most of the time)",
    "description": "Home description under 30 words, can be slightly messy with a lived-in feel",
    "has_roommate": false,
    "pets": "Having one would be more interesting, empty string if none"
  }},
  "family": {{
    "parents_location": "a plausible city",
    "contact_frequency": "a plausible contact frequency",
    "notes": "Family's attitude toward the constant-travel career, a small detail"
  }},
  "daily_schedule": {{
    "wake_up": "reasonable wake-up time (may wake later or earlier for travel schedule)",
    "leave_home": "09:00",
    "arrive_work": "10:00",
    "lunch_break_start": "12:00",
    "lunch_break_end": "13:30",
    "leave_work": "18:00",
    "arrive_home": "19:00",
    "sleep": "reasonable bedtime",
    "work_start": "10:00",
    "work_end": "18:00"
  }},
  "commute": {{
    "method": "",
    "line": "",
    "duration_minutes": 0
  }},
  "locations": {{
    "home_address_hint": "a real street near {city}",
    "company_landmark": "",
    "favorite_cafe": "favorite cafe name",
    "supermarket": "a real supermarket name",
    "park": "a real park name",
    "weekend_hangout": "a real shopping district/street name",
    "frequent_outdoor_spots": "frequent filming/shooting locations"
  }},
  "habits": {{
    "morning_drink": "morning drink (local specialty coffee or tea while traveling)",
    "lunch_style": "lunch habits (loves trying local cuisine while traveling)",
    "evening_routine": "evening routine (sorting footage, editing videos)",
    "weekend_morning": "weekend morning routine when not traveling"
  }},
  "current_context": "What travel project they are busy with lately, under 30 words",
  "pixel_appearance": {{
    "hair_color": "#hex color",
    "hair_style": "hairstyle",
    "default_outfit_color": "#hex color"
  }},
  "life_goals": [
    {{"category": "Career", "description": "a goal directly related to {occupation} (e.g. follower count, countries visited, brand collaborations)", "target_date": "", "progress": 0, "priority": 1}},
    {{"category": "Life", "description": "a personal life goal (e.g. learn a new language, get scuba certified, learn surfing)", "target_date": "", "progress": 0, "priority": 2}},
    {{"category": "Health", "description": "a health goal (travel bloggers often have irregular sleep, could be fixing sleep schedule)", "target_date": "", "progress": 0, "priority": 3}},
    {{"category": "Travel", "description": "a travel goal (e.g. visit Antarctica, complete the Silk Road, road trip around a continent)", "target_date": "", "progress": 0, "priority": 4}}
  ],
  "travel_plan": {{
    "enabled": true,
    "destinations": [
      {{
        "city": "a real travel destination city",
        "city_en": "English city name",
        "country": "country name",
        "start_date": "a date starting tomorrow, format YYYY-MM-DD",
        "end_date": "a date 4-7 days later, format YYYY-MM-DD",
        "spots": ["3-5 real landmark names in that city"],
        "purpose": "purpose of this trip (vlog filming, food exploration, cultural experience, etc.)",
        "mood_bonus": 15
      }},
      {{
        "city": "another different city in another country",
        "city_en": "English city name",
        "country": "country name",
        "start_date": "a date 10-15 days from now",
        "end_date": "a date 14-18 days from now",
        "spots": ["3-5 real landmark names in that city"],
        "purpose": "travel purpose",
        "mood_bonus": 18
      }},
      {{
        "city": "a third destination",
        "city_en": "English city name",
        "country": "country name",
        "start_date": "a date 20-25 days from now",
        "end_date": "a date 24-30 days from now",
        "spots": ["3-5 real landmark names in that city"],
        "purpose": "travel purpose",
        "mood_bonus": 20
      }}
    ]
  }},
  "wardrobe": {{
    "home": "Comfortable home clothes at base city (short English description)",
    "work": "Outfit for meeting brands or formal work",
    "casual": "Casual going-out outfit",
    "outdoor": "Travel filming outfit (sun protection, comfortable, easy to move in)",
    "formal": "Brand event or formal occasion outfit",
    "sport": "Sports/workout clothes",
    "sleep": "Sleepwear",
    "travel": "Signature travel look (e.g. photographer style: vest + cargo pants + sneakers)",
    "home_en": "English description for image generation",
    "work_en": "English work outfit description",
    "casual_en": "English casual outfit",
    "outdoor_en": "English travel photography outfit with utility vest and cargo pants",
    "formal_en": "English formal outfit",
    "sport_en": "English sport outfit",
    "sleep_en": "English sleepwear",
    "travel_en": "English travel outfit with camera bag, utility vest, comfortable sneakers and sunglasses"
  }}
}}

Return only JSON, no other content. 
- Base city {city} locations must be real.
- Destination cities and landmarks in travel_plan must be real.
- Dates are sequential starting from tomorrow, each trip 4-7 days, with 3-5 day gaps between.
- Wardrobe travel outfit should reflect travel blogger style (practical, filming-friendly, distinctive).
- Life goals should be specific and interesting, fitting the travel blogger occupation."""


def generate_npc_cards(character_card: dict) -> list:
    """Generate NPC network based on character card (adapts to work style)"""
    llm = get_llm_client()

    name = character_card.get("basic", {}).get("name", "")
    age = character_card.get("basic", {}).get("age", 24)
    occupation = character_card.get("basic", {}).get("occupation", "")
    city = character_card.get("basic", {}).get("city", "New York")
    district = character_card.get("basic", {}).get("district", "")
    work_style = character_card.get("basic", {}).get("work_style", "office")

    if work_style == "freelance":
        prompt = f"""Generate a rich, realistic social circle for the protagonist "{name}".

Protagonist info: {age} years old, {occupation} (freelancer), living in {city}{district}.
Freelancers' social circles differ from office workers - they usually have clients, collaborators, fellow freelancers, etc.

Generate the following NPCs, return a JSON array (must include all characters):
[
  {{
    "id": "npc_bestfriend",
    "relation": "Best friend",
    "name": "a common name in {city}",
    "age": 25,
    "occupation": "a plausible occupation (could be another freelancer)",
    "personality_word": "personality word (e.g. cheerful, thoughtful, etc.)",
    "contact_frequency": "how often they meet",
    "appear_scenes": ["CAFE", "STREET_WANDERING", "PARK", "FRIEND_HANGOUT", "CAFE_WORKING"],
    "event_pool": ["invite_hangout", "share_good_news"],
    "pixel_variant": "npc_f_01"
  }},
  {{
    "id": "npc_client",
    "relation": "Client",
    "name": "a common name",
    "age": 30,
    "occupation": "a plausible industry",
    "personality_word": "personality word",
    "contact_frequency": "frequent during projects",
    "appear_scenes": ["CAFE_WORKING", "CAFE"],
    "event_pool": ["new_project", "payment_delay"],
    "pixel_variant": "npc_f_02"
  }},
  {{
    "id": "npc_collaborator",
    "relation": "Collaborator",
    "name": "a common name",
    "age": 27,
    "occupation": "a freelancer in a related field",
    "personality_word": "personality word",
    "contact_frequency": "occasional collaboration",
    "appear_scenes": ["CAFE_WORKING", "CAFE", "HOME_WORKING"],
    "event_pool": ["collaboration_opportunity", "share_resource"],
    "pixel_variant": "npc_m_01"
  }},
  {{
    "id": "npc_mom",
    "relation": "Mother",
    "name": "N/A",
    "age": {age + random.randint(25, 32)},
    "occupation": "",
    "personality_word": "caring",
    "contact_frequency": "weekly video call",
    "appear_scenes": [],
    "event_pool": ["video_call", "send_recipe"],
    "pixel_variant": null
  }},
  {{
    "id": "npc_dad",
    "relation": "Father",
    "name": "N/A",
    "age": {age + random.randint(27, 34)},
    "occupation": "",
    "personality_word": "reserved and steady",
    "contact_frequency": "occasional video call",
    "appear_scenes": [],
    "event_pool": ["video_call", "send_money"],
    "pixel_variant": null
  }},
  {{
    "id": "npc_roommate",
    "relation": "College roommate",
    "name": "a common name in {city}",
    "age": {age},
    "occupation": "a plausible occupation",
    "personality_word": "lively and quirky",
    "contact_frequency": "monthly meetup",
    "appear_scenes": ["CAFE", "FRIEND_HANGOUT", "STREET_WANDERING"],
    "event_pool": ["invite_hangout", "share_good_news", "catch_up"],
    "pixel_variant": "npc_f_03"
  }},
  {{
    "id": "npc_neighbor",
    "relation": "Neighbor",
    "name": "a common name",
    "age": {age + random.randint(0, 3)},
    "occupation": "a plausible occupation",
    "personality_word": "easygoing and chill",
    "contact_frequency": "occasionally run into each other",
    "appear_scenes": ["HOME_MORNING", "HOME_EVENING", "STREET_WANDERING"],
    "event_pool": ["borrow_thing", "share_good_news"],
    "pixel_variant": "npc_f_04"
  }}
]

Return only JSON array, no other content. Names should use {city} common name styles. Age can be fine-tuned (±2 years)."""
    else:
        prompt = f"""Generate a rich, realistic social circle for the protagonist "{name}".

Protagonist info: {age} years old, {occupation}, living in {city}{district}.

Generate the following NPCs, return a JSON array (must include all characters):
[
  {{
    "id": "npc_bestfriend",
    "relation": "Best friend",
    "name": "a common name in {city}",
    "age": {age + random.randint(1, 5)},
    "occupation": "a plausible occupation",
    "personality_word": "personality word (e.g. cheerful, thoughtful, etc.)",
    "contact_frequency": "how often they meet",
    "appear_scenes": ["CAFE", "STREET_WANDERING", "PARK", "FRIEND_HANGOUT"],
    "event_pool": ["invite_hangout", "share_good_news"],
    "pixel_variant": "npc_f_01"
  }},
  {{
    "id": "npc_colleague_a",
    "relation": "Colleague",
    "name": "a common name",
    "age": {age + random.randint(2, 6)},
    "occupation": "same company",
    "personality_word": "personality word",
    "contact_frequency": "daily",
    "appear_scenes": ["OFFICE_WORKING", "OFFICE_LUNCH"],
    "event_pool": ["lunch_together", "complain_about_work"],
    "pixel_variant": "npc_f_02"
  }},
  {{
    "id": "npc_colleague_b",
    "relation": "Colleague",
    "name": "a common name",
    "age": {age + random.randint(3, 8)},
    "occupation": "same company",
    "personality_word": "personality word",
    "contact_frequency": "daily",
    "appear_scenes": ["OFFICE_WORKING"],
    "event_pool": ["extra_task_from_boss"],
    "pixel_variant": "npc_m_01"
  }},
  {{
    "id": "npc_mom",
    "relation": "Mother",
    "name": "N/A",
    "age": {age + random.randint(25, 32)},
    "occupation": "",
    "personality_word": "caring",
    "contact_frequency": "weekly video call",
    "appear_scenes": [],
    "event_pool": ["video_call", "send_recipe"],
    "pixel_variant": null
  }},
  {{
    "id": "npc_dad",
    "relation": "Father",
    "name": "N/A",
    "age": {age + random.randint(27, 34)},
    "occupation": "",
    "personality_word": "reserved and steady",
    "contact_frequency": "occasional video call",
    "appear_scenes": [],
    "event_pool": ["video_call", "send_money"],
    "pixel_variant": null
  }},
  {{
    "id": "npc_roommate",
    "relation": "College roommate",
    "name": "a common name in {city}",
    "age": {age},
    "occupation": "a plausible occupation",
    "personality_word": "lively and quirky",
    "contact_frequency": "monthly meetup",
    "appear_scenes": ["CAFE", "FRIEND_HANGOUT", "STREET_WANDERING"],
    "event_pool": ["invite_hangout", "share_good_news", "catch_up"],
    "pixel_variant": "npc_f_03"
  }},
  {{
    "id": "npc_boss",
    "relation": "Direct supervisor",
    "name": "a common name",
    "age": {age + random.randint(8, 14)},
    "occupation": "a plausible position",
    "personality_word": "competent and strict",
    "contact_frequency": "daily",
    "appear_scenes": ["OFFICE_WORKING", "OFFICE_MEETING"],
    "event_pool": ["extra_task_from_boss", "praise_from_boss"],
    "pixel_variant": "npc_m_02"
  }},
  {{
    "id": "npc_neighbor",
    "relation": "Neighbor",
    "name": "a common name",
    "age": {age + random.randint(0, 3)},
    "occupation": "a plausible occupation",
    "personality_word": "easygoing and chill",
    "contact_frequency": "occasionally run into each other",
    "appear_scenes": ["HOME_MORNING", "HOME_EVENING", "STREET_WANDERING"],
    "event_pool": ["borrow_thing", "share_good_news"],
    "pixel_variant": "npc_f_04"
  }}
]

Return only JSON array, no other content. Names should use {city} common name styles. Age can be fine-tuned (±2 years)."""

    try:
        response = llm.generate(prompt, max_tokens=1500, temperature=0.8)
        response = response.strip()
        if response.startswith("```"):
            lines = response.split("\n")
            response = "\n".join(lines[1:])
            if response.endswith("```"):
                response = response[:-3]
            response = response.strip()
        npcs = json.loads(response)
        from .birthday_engine import auto_generate_birthday
        for npc in npcs:
            if not npc.get("birth_date"):
                personality = npc.get("personality_word", "")
                npc_age = npc.get("age", age + 2)
                bd_info = auto_generate_birthday(personality, npc_age)
                npc["birth_date"] = bd_info["birth_date"]
        return npcs
    except Exception as e:
        print(f"[SimLife] NPC generation failed: {e}")
        return None


def generate_activity_description(
    character_card: dict,
    scene: str,
    scene_label: str,
    today_events_summary: str = "",
    mood: int = 70,
) -> str:
    """Generate a conversational activity description"""
    llm = get_llm_client()

    from datetime import datetime
    now = datetime.now()
    weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    name = character_card.get("basic", {}).get("name", "")
    occupation = character_card.get("basic", {}).get("occupation", "")

    if mood > 80:
        tone = "Light, upbeat tone with a small delightful detail"
    elif mood >= 60:
        tone = "Neutral tone, mundane but textured"
    elif mood >= 40:
        tone = "Slightly weary tone"
    else:
        tone = "Downbeat tone, but not exaggerated"

    prompt = f"""The character's name is "{name}", occupation: {occupation}, currently {weekday_names[now.weekday()]} {now.strftime('%H:%M')}.
They just entered "{scene_label}" mode.
What happened today: {today_events_summary or 'nothing in particular'}.
{tone}.
Write one sentence in third person describing this moment, conversational, with detail, no more than 20 words, no exclamation marks.
Return only the description text, no quotes or other content."""

    world_guide = _get_world_guide("activity")
    if world_guide:
        prompt = world_guide + "\n\n" + prompt

    try:
        response = llm.generate(prompt, max_tokens=100, temperature=0.9)
        return response.strip().strip('"').strip('"').strip("'").strip()
    except Exception:
        defaults = {
            "HOME_MORNING": "woke up and making coffee in the kitchen",
            "COMMUTE_TO_WORK": "on the way to work",
            "OFFICE_WORKING": "working at the desk",
            "OFFICE_MEETING": "in a meeting room",
            "OFFICE_LUNCH": "out grabbing lunch",
            "COMMUTE_TO_HOME": "on the way home from work",
            "HOME_EVENING": "relaxing at home",
            "CAFE": "sitting in a cafe for a while",
            "PARK": "taking a walk in the park",
            "HOME_SLEEPING": "fast asleep",
            "HOME_WEEKEND_LAZY": "lazing in bed, not wanting to get up",
            "HOME_WORKING": "working on the computer at home",
            "CAFE_WORKING": "opened the laptop at a cafe",
            "OUTDOOR_WORKING": "busy with work outdoors",
            "STUDIO_WORKING": "busy in the studio",
            "OVERTIME": "still working overtime",
            "AIRPORT": "waiting at the airport",
            "TOURING": "shooting content at a scenic spot",
            "HOTEL": "organizing photos at the hotel",
            "LOCAL_FOOD": "trying local cuisine",
            "TRAIN_STATION": "waiting at the train station",
            "SCENIC_DRIVE": "sitting in the car, photographing the scenery outside",
            "RESTAURANT_LOCAL": "eating at a local restaurant",
        }
        return defaults.get(scene, "busy with their own thing")


def generate_life_arc(character_card: dict, previous_arc: dict = None) -> dict:
    """
    Based on world setting + character info, LLM calculates a monthly-level life arc.
    Optionally pass previous_arc as the summary of the previous arc for story continuity.
    Returns a dict that can be used directly to create a LifeArc object.
    """
    llm = get_llm_client()

    name = character_card.get("basic", {}).get("name", "")
    occupation = character_card.get("basic", {}).get("occupation", "")
    personality = character_card.get("basic", {}).get("personality_traits", [])
    traits_str = ", ".join(personality[:3]) if personality else "not set"
    age = character_card.get("basic", {}).get("age", "")

    prev_hint = ""
    if previous_arc:
        prev_title = previous_arc.get("title", "")
        prev_desc = previous_arc.get("description", "")
        stages = previous_arc.get("stages", [])
        final_stage = stages[-1] if stages else {}
        final_events = "; ".join(final_stage.get("key_events", [])[:3])
        if final_stage.get("description"):
            final_events = final_stage["description"] + ". " + final_events
        prev_hint = f"""

[Previous Arc Summary]
Previous arc: "{prev_title}"
Overview: {prev_desc}
Ending: {final_events}
"""
        try:
            hist_path = Path(__file__).parent.parent / "data" / "life_arc_history.json"
            if hist_path.exists():
                with open(hist_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
                if history:
                    arc_titles = " -> ".join([h.get("title", "?") for h in history[-5:]])
                    prev_hint += f"All previous arcs the character has experienced: {arc_titles}\n"
        except Exception:
            pass

    prompt = f"""You are the narrative system of a life simulator. Please plan a life arc quest spanning about 30 days for the character "{name}" ({occupation}, age {age}, personality: {traits_str}).{prev_hint}

Requirements:
1. The arc should have a beginning, development, turn, and conclusion that matches the character's identity and personality
2. Divide into 4-7 stages, each lasting 3-10 days
3. Stages should have logical progression (e.g.: preparation -> departure -> exploration -> climax -> resolution)
4. Each stage should have 2-4 possible key events
5. Total duration should be 25-40 days
6. Content should fit the world setting, adventurous but not absurd
7. Title should be 10-20 characters long
8. If there is a [Previous Arc Summary], the new arc should naturally continue from it, with inherited character state and relationships

Return JSON only, no other content:
{{
  "title": "Arc title",
  "description": "Arc overview (50-100 chars)",
  "duration_days": 30,
  "stages": [
    {{
      "name": "Stage name (5-10 chars)",
      "description": "Stage description (20-50 chars)",
      "duration_days": 5,
      "key_events": ["Event 1", "Event 2", "Event 3"]
    }}
  ]
}}"""

    world_context = _get_world_context()
    if world_context:
        prompt = world_context + "\n\n" + prompt
    story_influence = _get_story_influences()
    if story_influence:
        prompt = prompt + "\n\n" + story_influence

    try:
        response = llm.generate(prompt, max_tokens=1000, temperature=0.85)
        response = response.strip()
        if response.startswith("```"):
            lines = response.split("\n")
            response = "\n".join(lines[1:])
            if response.endswith("```"):
                response = response[:-3]
            response = response.strip()
        result = json.loads(response)

        stages_raw = result.get("stages", [])
        total_days = 0
        stages = []
        for s in stages_raw:
            if not isinstance(s, dict):
                continue
            dur = int(s.get("duration_days", 5))
            dur = max(2, min(15, dur))
            total_days += dur
            stages.append({
                "name": str(s.get("name", "Stage")),
                "description": str(s.get("description", "")),
                "duration_days": dur,
                "status": "pending",
                "key_events": [str(e) for e in s.get("key_events", [])[:5]],
            })

        if not stages:
            return _default_life_arc(name)

        stages[0]["status"] = "active"

        return {
            "title": str(result.get("title", "Daily Adventure")),
            "description": str(result.get("description", "")),
            "duration_days": total_days,
            "stages": stages,
        }

    except Exception as e:
        print(f"[SimLife] Life arc generation failed: {e}")
        return _default_life_arc(name)


def _default_life_arc(name: str = "Character") -> dict:
    """Fallback when life arc generation fails"""
    return {
        "title": "Daily Training and Exploration",
        "description": f"{name} begins an ordinary but fulfilling daily life",
        "duration_days": 30,
        "stages": [
            {"name": "Daily Training", "description": "Training fundamentals near home", "duration_days": 7, "status": "active", "key_events": ["Morning exercise", "Studying texts", "Basic training"]},
            {"name": "Outward Exploration", "description": "Exploring the surrounding area", "duration_days": 7, "status": "pending", "key_events": ["Visiting the market", "Gathering intel", "Exploring ruins"]},
            {"name": "Mission Execution", "description": "Accepting and completing missions", "duration_days": 10, "status": "pending", "key_events": ["Accepting commissions", "Combat training", "Collecting spoils"]},
            {"name": "Rest and Reflection", "description": "Rest and plan the next step", "duration_days": 6, "status": "pending", "key_events": ["Organizing gains", "Repairing equipment", "Recording insights"]},
        ],
    }


def generate_day_plan(
    character_card: dict,
    mood: int = 70,
    yesterday_summary: str = "",
    arc_hint: str = "",
    cast: list = None,
    recent_story_context: str = "",
) -> list:
    """
    Generate a daily outline plan for non-modern worlds (LLM single call, full day schedule).
    Returns list: [{"time":"07:00","scene":"Room","label":"Wake up","activity":"...","mood_delta":0,"npc":"npc_id or empty"}, ...]
    Typically 6-10 entries covering a full day.
    """
    from datetime import datetime

    llm = get_llm_client()
    now = datetime.now()
    weekday_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    name = character_card.get("basic", {}).get("name", "")
    occupation = character_card.get("basic", {}).get("occupation", "")
    personality = character_card.get("basic", {}).get("personality_traits", [])
    traits_str = ", ".join(personality[:3]) if personality else "not set"

    summary_hint = f"\nYesterday: {yesterday_summary}" if yesterday_summary else ""
    arc_hint_text = f"\n\n{arc_hint}" if arc_hint else ""

    cast_hint = ""
    if cast:
        npc_brief = "\n".join([f"- {c['name']} ({c['role']}, {c['personality']})" for c in cast])
        cast_hint = f"\n\nAvailable NPC cast:\n{npc_brief}"

    prompt = f"""You are a life simulator. Plan a full daily outline for character "{name}" ({occupation}, personality: {traits_str}).

Today is {weekday_names[now.weekday()]}, current mood: {mood}/100.{summary_hint}{arc_hint_text}{cast_hint}

Requirements:
1. Generate 8-10 time entries, evenly distributed from waking up to going to sleep
2. Each entry contains: time(HH:MM), scene(2-4 word scene name), label(4-8 word label), activity(15-30 word brief description), mood_delta(-5 to +5), npc(optional, NPC id or empty string)
3. Activities should fit the world setting and advance the arc
4. No exclamation marks
5. Activity should be concise, details will be expanded on demand later
6. At least 1-2 entries should involve NPC interaction

Return JSON array only, no other content:
[{{"time":"07:00","scene":"Room","label":"Morning","activity":"{name} wakes up and freshens up","mood_delta":1,"npc":""}}, ...]"""

    world_context = _get_world_context()
    if world_context:
        prompt = world_context + "\n\n" + prompt
    story_influence = _get_story_influences()
    if story_influence:
        prompt = prompt + "\n\n" + story_influence
    if recent_story_context:
        prompt = prompt + "\n\n" + recent_story_context

    try:
        response = llm.generate(prompt, max_tokens=800, temperature=0.85,
                                 response_format={"type": "json_object"})
        response = response.strip()
        if response.startswith("```"):
            lines = response.split("\n")
            response = "\n".join(lines[1:])
            if response.endswith("```"):
                response = response[:-3]
            response = response.strip()

        plan = json.loads(response)
        if not isinstance(plan, list) or len(plan) == 0:
            raise ValueError("Empty list")

        valid_plan = []
        for item in plan:
            if not isinstance(item, dict):
                continue
            valid_plan.append({
                "time": str(item.get("time", "08:00")),
                "scene": str(item.get("scene", "Daily")),
                "label": str(item.get("label", "")),
                "activity": str(item.get("activity", "")),
                "mood_delta": int(item.get("mood_delta", 0)),
                "npc": str(item.get("npc", "")),
                "expanded": None,
            })
        return valid_plan if valid_plan else _default_day_plan(name)

    except Exception as e:
        print(f"[SimLife] Daily plan JSON parsing failed, attempting repair: {e}")
        try:
            import re as _re
            fixed = response

            array_match = _re.search(r'\[[\s\S]*\]', fixed)
            if array_match:
                fixed = array_match.group(0)
            fixed = _re.sub(r',\s*$', '', fixed.strip())
            in_string = False
            escape_next = False
            for ch in fixed:
                if escape_next:
                    escape_next = False
                    continue
                if ch == '\\':
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
            if in_string:
                fixed += '"'
            open_brackets = fixed.count('[') + fixed.count('{')
            close_brackets = fixed.count(']') + fixed.count('}')
            fixed += ']' * (open_brackets - close_brackets)
            try:
                import rjson
                plan = rjson.loads(fixed)
            except ImportError:
                plan = json.loads(fixed)
            if isinstance(plan, list) and len(plan) > 0:
                valid_plan = []
                for item in plan:
                    if not isinstance(item, dict):
                        continue
                    valid_plan.append({
                        "time": str(item.get("time", "08:00")),
                        "scene": str(item.get("scene", "Daily")),
                        "label": str(item.get("label", "")),
                        "activity": str(item.get("activity", "")),
                        "mood_delta": int(item.get("mood_delta", 0)),
                        "npc": str(item.get("npc", "")),
                        "expanded": None,
                    })
                if valid_plan:
                    print(f"[SimLife] JSON repair succeeded, got {len(valid_plan)} entries")
                    return valid_plan
        except Exception as e2:
            print(f"[SimLife] JSON repair also failed: {e2}")

        try:
            import re as _re
            objects = _re.findall(r'\{[^{}]*\}', response)
            if objects:
                valid_plan = []
                for obj_str in objects:
                    try:
                        key_values = {}
                        m = _re.search(r'"mood_delta"\s*:\s*(-?\d+)', obj_str)
                        if m:
                            key_values["mood_delta"] = int(m.group(1))
                        for key in ("time", "scene", "label", "activity", "npc"):
                            m = _re.search(rf'"{key}"\s*:\s*"([^"]*)"', obj_str)
                            if m:
                                key_values[key] = m.group(1)
                        if "time" in key_values and "scene" in key_values:
                            valid_plan.append({
                                "time": key_values.get("time", "08:00"),
                                "scene": key_values.get("scene", "Daily"),
                                "label": key_values.get("label", ""),
                                "activity": key_values.get("activity", ""),
                                "mood_delta": int(key_values.get("mood_delta", 0)),
                                "npc": key_values.get("npc", ""),
                                "expanded": None,
                            })
                    except Exception:
                        continue
                if valid_plan:
                    print(f"[SimLife] Regex extraction repair succeeded, got {len(valid_plan)} entries")
                    return valid_plan
        except Exception:
            pass

        print(f"[SimLife] Daily plan generation failed, using default plan")
        return _default_day_plan(name)


def _default_day_plan(name: str = "Character") -> list:
    """Fallback plan when generation fails"""
    return [
        {"time": "07:00", "scene": "Room", "label": "Wake up", "activity": f"{name} wakes up from sleep", "mood_delta": 1},
        {"time": "08:00", "scene": "Daily", "label": "Breakfast", "activity": f"{name} has a simple breakfast", "mood_delta": 2},
        {"time": "09:00", "scene": "Work", "label": "Start work", "activity": f"{name} starts the day's work", "mood_delta": 0},
        {"time": "12:00", "scene": "Daily", "label": "Lunch", "activity": f"{name} finds a place to eat and rest", "mood_delta": 2},
        {"time": "14:00", "scene": "Work", "label": "Afternoon work", "activity": f"{name} continues working", "mood_delta": -1},
        {"time": "18:00", "scene": "Daily", "label": "Dinner", "activity": f"{name} has dinner and relaxes", "mood_delta": 3},
        {"time": "20:00", "scene": "Leisure", "label": "Evening leisure", "activity": f"{name} enjoys some personal time", "mood_delta": 2},
        {"time": "22:00", "scene": "Room", "label": "Sleep", "activity": f"{name} gets ready for bed", "mood_delta": 1},
    ]


def generate_story_cast(character_card: dict) -> list:
    """
    Generate story NPC cast for non-modern worlds (3-5 characters).
    Each NPC has name, identity, personality, secret, and speaking style.
    Automatically adapts content based on world setting.
    """
    llm = get_llm_client()

    name = character_card.get("basic", {}).get("name", "")
    occupation = character_card.get("basic", {}).get("occupation", "")
    age = character_card.get("basic", {}).get("age", 24)
    personality = character_card.get("basic", {}).get("personality_traits", [])
    traits_str = ", ".join(personality[:3]) if personality else "not set"

    prompt = f"""You are the narrative system of a life simulator. Generate a cast of story NPCs for character "{name}" ({occupation}, age {age}, personality: {traits_str}).

Requirements:
1. Generate 3-5 NPCs who will appear repeatedly in the story
2. NPC types should be varied: companion, rival, mentor, mysterious figure, trading partner, etc.
3. Each NPC should have a unique personality and speaking style for recognizable dialogue
4. Each NPC should have a secret or hidden identity to set up future plot points
5. NPCs must fully fit the world setting, no modern elements

Return JSON array only, no other content:
[
  {{
    "id": "npc_character_english_id",
    "name": "Character name",
    "role": "Role in the story (e.g. adventure companion, librarian, rival, mentor's old friend, etc.)",
    "personality": "Personality description (within 30 words)",
    "appearance": "Appearance description (within 30 words)",
    "secret": "A secret or hidden identity (within 20 words)",
    "voice_style": "Speaking style (within 15 words, e.g. uses rhetorical questions, speaks slowly, catchphrase, etc.)",
    "first_encounter": "Scene of first meeting with the protagonist (within 30 words)"
  }}
]"""

    world_context = _get_world_context()
    if world_context:
        prompt = world_context + "\n\n" + prompt
    story_influence = _get_story_influences()
    if story_influence:
        prompt = prompt + "\n\n" + story_influence

    try:
        response = llm.generate(prompt, max_tokens=1500, temperature=0.85)
        response = response.strip()
        if response.startswith("```"):
            lines = response.split("\n")
            response = "\n".join(lines[1:])
            if response.endswith("```"):
                response = response[:-3]
            response = response.strip()

        cast = json.loads(response)
        if not isinstance(cast, list) or len(cast) == 0:
            return _default_story_cast(name)

        valid_cast = []
        for item in cast:
            if not isinstance(item, dict):
                continue
            valid_cast.append({
                "id": str(item.get("id", "")),
                "name": str(item.get("name", "")),
                "role": str(item.get("role", "")),
                "personality": str(item.get("personality", "")),
                "appearance": str(item.get("appearance", "")),
                "secret": str(item.get("secret", "")),
                "voice_style": str(item.get("voice_style", "")),
                "first_encounter": str(item.get("first_encounter", "")),
                "trust": 50,
                "encountered": False,
            })
        return valid_cast if valid_cast else _default_story_cast(name)
    except Exception as e:
        print(f"[SimLife] NPC cast generation failed: {e}")
        return _default_story_cast(name)


def _default_story_cast(name: str = "Character") -> list:
    """Fallback when cast generation fails"""
    return [
        {"id": "npc_companion", "name": "Traveler", "role": "A fellow traveler met by chance",
         "personality": "Talkative but kind-hearted", "appearance": "Wearing a cloak, face hidden",
         "secret": "Actually on the run", "voice_style": "Loves exaggerated metaphors",
         "first_encounter": "Approached while resting by the roadside", "trust": 50, "encountered": False},
        {"id": "npc_mentor", "name": "Elder", "role": "A mysterious guide",
         "personality": "Silent but gives crucial advice at key moments", "appearance": "White-haired with deep, penetrating eyes",
         "secret": "Has old ties with the protagonist's mentor", "voice_style": "Speaks succinctly and powerfully",
         "first_encounter": "Met by chance in a library corner", "trust": 50, "encountered": False},
        {"id": "npc_rival", "name": "Rival", "role": "A competitor with the same goal",
         "personality": "Friendly on the surface, scheming underneath", "appearance": "Neatly dressed with a smile",
         "secret": "Works for a certain organization", "voice_style": "Gentle tone but with hidden edge",
         "first_encounter": "Competing for the same commission at the quest board", "trust": 30, "encountered": False},
    ]


def expand_node(character_card: dict, node: dict, cast: list = None,
                arc_context: str = "", prev_nodes: list = None) -> str:
    """
    Expand a day_plan node into a 200-500 word narrative paragraph.
    Includes scene description, action details, internal monologue, NPC dialogue.
    """
    llm = get_llm_client()

    name = character_card.get("basic", {}).get("name", "")
    occupation = character_card.get("basic", {}).get("occupation", "")

    cast_info = ""
    if cast and node.get("npc"):
        npc_id = node.get("npc", "")
        for c in cast:
            if c.get("id") == npc_id:
                cast_info = (
                    f"\nInteracting NPC: {c['name']} ({c['role']})\n"
                    f"Personality: {c['personality']}\n"
                    f"Speaking style: {c['voice_style']}\n"
                    f"Secret: {c['secret']}"
                )
                break
        if not cast_info and cast:
            brief = "; ".join([f"{c['name']}({c['role']})" for c in cast[:4]])
            cast_info = f"\nAvailable NPCs: {brief}"

    prev_context = ""
    if prev_nodes and len(prev_nodes) > 0:
        last = prev_nodes[-1]
        prev_context = f"\nPrevious entry: {last.get('time', '')} {last.get('label', '')} - {last.get('activity', '')}"

    arc_hint = f"\n\n{arc_context}" if arc_context else ""

    prompt = f"""You are the narrative system of a life simulator. Expand the following schedule entry into a vivid narrative paragraph.

Character: {name} ({occupation})
Current entry: {node.get('time', '')} {node.get('label', '')} - {node.get('scene', '')}
Activity summary: {node.get('activity', '')}{cast_info}{prev_context}{arc_hint}

Writing requirements:
1. 200-500 words
2. Include scene description (environment, atmosphere, senses)
3. Include action details (micro-expressions, small gestures)
4. If there is an interacting NPC, must include dialogue (with character-distinctive voice)
5. May include character internal monologue
6. Third-person narrative, natural and smooth tone
7. No exclamation marks
8. Strictly fit the world setting

Return only the narrative text, no other content."""

    world_context = _get_world_context()
    if world_context:
        prompt = world_context + "\n\n" + prompt
    story_influence = _get_story_influences()
    if story_influence:
        prompt = prompt + "\n\n" + story_influence

    try:
        response = llm.generate(prompt, max_tokens=600, temperature=0.9)
        return response.strip()
    except Exception as e:
        print(f"[SimLife] Node expansion failed: {e}")
        return node.get("activity", "")


def generate_future_events(
    character_card: dict,
    recent_events: list,
    days: int = 3,
) -> list:
    """Generate a queue of random events for the next N days"""
    llm = get_llm_client()

    name = character_card.get("basic", {}).get("name", "")
    occupation = character_card.get("basic", {}).get("occupation", "")
    work_style = character_card.get("basic", {}).get("work_style", "office")
    recent = ", ".join([e.get("label", "") for e in recent_events[-5:]]) if recent_events else "none"

    style_hint = ""
    if work_style == "freelance":
        style_hint = "They are a freelancer. Events may involve seeking inspiration, client communication, creative work, self-improvement, etc."
    elif work_style == "student":
        style_hint = "They are a student. Events may involve exams, clubs, assignments, social life, etc."
    elif work_style == "travel":
        style_hint = "They are a travel blogger. Events may involve flight changes, shooting content, local experiences, brand collaborations, fan interaction, etc."
    else:
        style_hint = "They are an office worker. Events may involve work projects, colleague relationships, overtime, commuting, etc."

    prompt = f"""Character "{name}", {occupation}. Recent events: {recent}.
{style_hint}
Generate life events for the next {days} days that may happen,
0-2 per day, with time range (e.g. "19:00-20:00") and mood impact (-30 to +30).
Return JSON array format:
[
  {{"event_id": "custom_english_id", "label": "Event description", "scheduled_date": "YYYY-MM-DD", "scheduled_time_range": "HH:MM-HH:MM", "mood_delta": 10, "source": "llm_generated"}}
]
Starting from tomorrow. Return only JSON array."""

    world_guide = _get_world_guide("event")
    if world_guide:
        prompt = world_guide + "\n\n" + prompt

    try:
        from datetime import datetime, timedelta
        response = llm.generate(prompt, max_tokens=1000, temperature=0.8)
        response = response.strip()
        if response.startswith("```"):
            lines = response.split("\n")
            response = "\n".join(lines[1:])
            if response.endswith("```"):
                response = response[:-3]
            response = response.strip()
        events = json.loads(response)

        tomorrow = (datetime.now() + timedelta(days=1)).date()
        for i, evt in enumerate(events):
            date_str = evt.get("scheduled_date", "")
            try:
                d = __import__("datetime").date.fromisoformat(date_str)
            except Exception:
                d = tomorrow + timedelta(days=i // 2)

        return events
    except Exception as e:
        print(f"[SimLife] Future events generation failed: {e}")
        return []
