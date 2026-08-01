"""Lightweight, deterministic fact extractor for long-term memory."""

from __future__ import annotations

import re
from typing import Any


class MemoryExtractor:
    """Extract atomic facts from free-form text without calling an LLM."""

    _SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

    def extract_facts(self, text: str) -> list[dict[str, Any]]:
        """Split ``text`` into sentences and return each as a memory fact.

        Each fact is shaped as::

            {"content": <sentence>, "category": "fact", "confidence": 0.8}
        """
        if not isinstance(text, str) or not text.strip():
            return []

        facts: list[dict[str, Any]] = []
        for raw in self._SENTENCE_SPLIT.split(text.strip()):
            sentence = raw.strip()
            if not sentence:
                continue
            facts.append(
                {
                    "content": sentence,
                    "category": "fact",
                    "confidence": 0.8,
                }
            )
        return facts
