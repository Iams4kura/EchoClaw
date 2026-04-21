"""Soul — mini_claw 的人格系统。"""

from .models import PersonalityTraits, MoodState, SoulConfig
from .manager import SoulManager

__all__ = ["PersonalityTraits", "MoodState", "SoulConfig", "SoulManager"]
