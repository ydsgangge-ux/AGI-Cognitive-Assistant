"""
emotion_bridge.py - Emotion state mapping layer

Map AGI-DPA internal emotion states (string/value) to VRM BlendShape parameters.
Main program simply calls translate(emotion_key) to get (expression_name, intensity).
"""

EMOTION_MAP: dict[str, tuple[str, float]] = {
    # Internal state -> (VRM expression name, intensity 0~1)
    "happy":        ("happy",     1.0),
    "excited":      ("happy",     0.8),
    "curious":      ("surprised", 0.5),
    "thinking":     ("neutral",   0.3),
    "sad":          ("sad",       0.7),
    "angry":        ("angry",     0.6),
    "surprised":    ("surprised", 1.0),
    "surprise":     ("surprised", 1.0),
    "neutral":      ("neutral",   1.0),
    "calm":         ("neutral",   0.8),
    "anticipation": ("happy",     0.4),
    "love":         ("happy",     0.9),
    "gratitude":    ("happy",     0.7),
    "pride":        ("happy",     0.6),
    "confused":     ("surprised", 0.4),
    "anxious":      ("sad",       0.5),
    "bored":        ("neutral",   0.5),
    "nostalgic":    ("sad",       0.3),
    "trust":        ("neutral",   0.7),
    "shame":        ("sad",       0.6),
}


def translate(emotion_key: str, intensity: float = 1.0) -> tuple[str, float]:
    """
    Map AGI-DPA emotion key to VRM expression parameters.

    Args:
        emotion_key: emotion string, e.g. "happy", "sad"
        intensity: raw emotion intensity 0~1

    Returns:
        (vrm_expression_name, vrm_intensity) tuple
    """
    name, base = EMOTION_MAP.get(emotion_key.lower(), ("neutral", 1.0))
    return name, base * intensity
