"""
Agent package.

Keep this module side-effect free: avoid importing heavy agents (browser/multimodal)
at import time, because they may require optional dependencies or prompts.
"""

from __future__ import annotations

import importlib
from typing import Any

from app.agent.base import BaseAgent


__all__ = [
    "BaseAgent",
    "BrowserAgent",
    "ReActAgent",
    "SWEAgent",
    "ToolCallAgent",
    "MCPAgent",
    "ImageGenerationAgent",
    "AudioGenerationAgent",
]


_LAZY_IMPORTS = {
    "BrowserAgent": "app.agent.browser",
    "MCPAgent": "app.agent.mcp",
    "ReActAgent": "app.agent.react",
    "SWEAgent": "app.agent.swe",
    "ToolCallAgent": "app.agent.toolcall",
    "ImageGenerationAgent": "app.agent.image_generation",
    "AudioGenerationAgent": "app.agent.audio_generation",
}


def __getattr__(name: str) -> Any:
    mod_path = _LAZY_IMPORTS.get(name)
    if not mod_path:
        raise AttributeError(name)
    mod = importlib.import_module(mod_path)
    return getattr(mod, name)
