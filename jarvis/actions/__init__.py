"""Import every action module so tools register themselves.

``jarvis.actions`` is imported once during startup.  Each module calls the
``@tool`` decorator at import time, which fills the global tool registry used by
the AI agent and the slash commands.
"""

from __future__ import annotations

# Import order only affects help output, not behaviour.
from . import member  # noqa: F401
from . import structure  # noqa: F401
from . import roles  # noqa: F401
from . import messages  # noqa: F401
from . import server  # noqa: F401
from . import storage  # noqa: F401
from . import community  # noqa: F401
from . import tickets  # noqa: F401
from . import leveling  # noqa: F401
from . import economy  # noqa: F401
from . import giveaways  # noqa: F401
from . import reminders  # noqa: F401
from . import utility  # noqa: F401
from . import voice  # noqa: F401
from . import games  # noqa: F401

__all__ = [
    "member",
    "structure",
    "roles",
    "messages",
    "server",
    "storage",
    "community",
    "tickets",
    "leveling",
    "economy",
    "giveaways",
    "reminders",
    "utility",
    "voice",
    "games",
]
