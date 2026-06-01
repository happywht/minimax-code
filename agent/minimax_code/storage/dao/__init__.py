"""Storage DAOs — one per entity.

The DAO layer is the only surface area the rest of the agent should
touch for persistent state. Each DAO exposes typed CRUD plus the
list/filter operations that the IPC layer needs.

Conventions
-----------
* Each entity lives in its own submodule
  (:mod:`.sessions`, :mod:`.messages`, :mod:`.tasks`, …).
* Every DAO has an *async class* (``SessionsDAO`` etc.) that takes
  an :class:`~minimax_code.storage.db.AsyncDatabase`, and a set of
  *module-level sync helpers* (``create_sync``, ``get_sync``,
  ``list_sync``) that take a :class:`~minimax_code.storage.db.Database`.
* The async class is the primary API for the agent loop; the sync
  helpers are used by the background scheduler and by ad-hoc CLI
  tools / tests.
"""

from __future__ import annotations

from . import agents, messages, mobile_devices, permissions, scheduled_jobs, sessions, skills, tasks

# Public re-exports — keep these stable; the agent loop imports
# ``SessionsDAO`` from this module, not from the submodule.
from .agents import AgentsDAO
from .agents import create_sync as create_agent_sync
from .messages import MessagesDAO
from .messages import create_sync as create_message_sync
from .mobile_devices import MobileDevicesDAO
from .mobile_devices import upsert_sync as upsert_device_sync
from .permissions import PermissionRuleDAO
from .permissions import PermissionRulesDAO
from .permissions import create_sync as create_permission_sync
from .scheduled_jobs import ScheduledJobsDAO
from .scheduled_jobs import create_sync as create_job_sync
from .sessions import SessionsDAO
from .sessions import create_sync as create_session_sync
from .skills import SkillsDAO
from .skills import upsert_sync as upsert_skill_sync
from .tasks import TaskDAO
from .tasks import TasksDAO
from .tasks import create_sync as create_task_sync

__all__ = [
    # Async DAOs
    "AgentsDAO",
    "MessagesDAO",
    "MobileDevicesDAO",
    "PermissionRuleDAO",
    "PermissionRulesDAO",
    "ScheduledJobsDAO",
    "SessionsDAO",
    "SkillsDAO",
    "TaskDAO",
    "TasksDAO",
    # Sync helpers
    "create_agent_sync",
    "create_job_sync",
    "create_message_sync",
    "create_permission_sync",
    "create_session_sync",
    "create_task_sync",
    "upsert_device_sync",
    "upsert_skill_sync",
    # Submodules
    "agents",
    "messages",
    "mobile_devices",
    "permissions",
    "scheduled_jobs",
    "sessions",
    "skills",
    "tasks",
]
