"""Debug: check if skills_root resolution and loader work."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, "D:/工作/城建院/2606/agent")

from minimax_code.agent.skills import bootstrap
from minimax_code.agent.skills.registry import _default_skills_root

print("default skills_root =", _default_skills_root())
print("exists =", _default_skills_root().exists())
print("contents =", list(_default_skills_root().iterdir()) if _default_skills_root().exists() else "N/A")

async def go():
    rt = await bootstrap()
    print("registry skills =", rt.registry.list())
    print("registry.list() count =", len(rt.registry.list()))

asyncio.run(go())
