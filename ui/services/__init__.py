"""Compatibility facade for the canonical layered service packages.

The implementation moved to ``core`` during the architecture migration. UI
callers can keep importing from ``ui.services`` while new code uses the
canonical packages directly. The screen-session service remains UI-specific
because it owns the external scrcpy window lifecycle.
"""

from core.domain.models import *  # noqa: F401,F403
from core.domain.rules import *  # noqa: F401,F403
from core.infrastructure import *  # noqa: F401,F403
from core.services import *  # noqa: F401,F403

from .screen_session_service import (
    ScreenPreset,
    ScreenPresetConfig,
    ScreenSession,
    ScreenSessionError,
    ScreenSessionService,
    ScreenSessionState,
)

__all__ = [
    name for name in globals()
    if not name.startswith("_")
]
