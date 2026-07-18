"""Extension registry + builder (R25).

Ports the registry half of grok-build's ``xai-agent-lifecycle`` crate.
The builder collects contributors of four families; :meth:`build`
freezes them into an :class:`ExtensionRegistry` the host reads for the
rest of the process.

Command-name policy
-------------------
Registration order wins. The first contributor to advertise a given
command name owns it; a later contributor trying to claim the same
name is logged and that *spec* is dropped (grok panics in debug builds,
logs in release — MiniMax keeps only the release behaviour so a
misbehaving extension can't crash the host). The duplicate contributor
itself stays registered so its other names still resolve.
:meth:`all_advertised_commands` returns the de-duped spec list (first
spec per name wins), so it agrees with :meth:`command_owner` — the
help UI never shows a command twice.

Design
------
"Install-time capability injection, never loop takeover." The registry
is built once, frozen, and queried by the host's run loop. Contributors
carry data in; the loop never hands control flow over to them.
"""

from __future__ import annotations

import logging

from .contributors import (
    CommandContributor,
    SessionLifecycleContributor,
    TurnInputContributor,
    TurnLifecycleContributor,
)
from .types import CommandSpec

logger = logging.getLogger(__name__)

__all__ = ["ExtensionRegistry", "ExtensionRegistryBuilder"]


class ExtensionRegistry:
    """Frozen view over every registered contributor.

    Built once by :class:`ExtensionRegistryBuilder`; the host holds the
    instance for the process lifetime. The four family tuples are
    immutable, so the set of contributors cannot change mid-turn.
    """

    __slots__ = (
        "_turn_lifecycle",
        "_session_lifecycle",
        "_turn_input",
        "_commands",
        "_command_owners",
        "_command_specs",
    )

    def __init__(
        self,
        *,
        turn_lifecycle: tuple[TurnLifecycleContributor, ...],
        session_lifecycle: tuple[SessionLifecycleContributor, ...],
        turn_input: tuple[TurnInputContributor, ...],
        commands: tuple[CommandContributor, ...],
        command_owners: dict[str, CommandContributor],
        command_specs: list[CommandSpec],
    ) -> None:
        self._turn_lifecycle = turn_lifecycle
        self._session_lifecycle = session_lifecycle
        self._turn_input = turn_input
        self._commands = commands
        self._command_owners = command_owners
        self._command_specs = command_specs

    @property
    def turn_lifecycle(self) -> tuple[TurnLifecycleContributor, ...]:
        return self._turn_lifecycle

    @property
    def session_lifecycle(self) -> tuple[SessionLifecycleContributor, ...]:
        return self._session_lifecycle

    @property
    def turn_input(self) -> tuple[TurnInputContributor, ...]:
        return self._turn_input

    @property
    def commands(self) -> tuple[CommandContributor, ...]:
        return self._commands

    def command_owner(self, name: str) -> CommandContributor | None:
        """The contributor that owns ``name``, or ``None`` if unclaimed."""
        return self._command_owners.get(name)

    def all_advertised_commands(self) -> list[CommandSpec]:
        """Every advertised spec, de-duped (first registrant per name wins).

        Mirrors :meth:`command_owner` — a name claimed by two contributors
        appears exactly once here, owned by whoever registered first. The
        help UI thus never shows a duplicate entry.
        """
        return list(self._command_specs)


class ExtensionRegistryBuilder:
    """Collect contributors, then freeze into an :class:`ExtensionRegistry`."""

    def __init__(self) -> None:
        self._turn_lifecycle: list[TurnLifecycleContributor] = []
        self._session_lifecycle: list[SessionLifecycleContributor] = []
        self._turn_input: list[TurnInputContributor] = []
        self._commands: list[CommandContributor] = []
        self._command_owners: dict[str, CommandContributor] = {}
        self._command_specs: list[CommandSpec] = []

    def add_turn_lifecycle(
        self, c: TurnLifecycleContributor
    ) -> ExtensionRegistryBuilder:
        self._turn_lifecycle.append(c)
        return self

    def add_session_lifecycle(
        self, c: SessionLifecycleContributor
    ) -> ExtensionRegistryBuilder:
        self._session_lifecycle.append(c)
        return self

    def add_turn_input(
        self, c: TurnInputContributor
    ) -> ExtensionRegistryBuilder:
        self._turn_input.append(c)
        return self

    def add_command(self, c: CommandContributor) -> ExtensionRegistryBuilder:
        """Register a command contributor.

        Advertised names are claimed in registration order; a duplicate
        claim is logged and that spec dropped. The contributor itself
        stays so its other names still resolve.
        """
        self._commands.append(c)
        for spec in c.advertised_commands():
            if spec.name in self._command_owners:
                logger.warning(
                    "command %r already owned by %s; %s duplicate advert dropped",
                    spec.name,
                    type(self._command_owners[spec.name]).__name__,
                    type(c).__name__,
                )
                continue
            self._command_owners[spec.name] = c
            self._command_specs.append(spec)
        return self

    def build(self) -> ExtensionRegistry:
        return ExtensionRegistry(
            turn_lifecycle=tuple(self._turn_lifecycle),
            session_lifecycle=tuple(self._session_lifecycle),
            turn_input=tuple(self._turn_input),
            commands=tuple(self._commands),
            command_owners=dict(self._command_owners),
            command_specs=list(self._command_specs),
        )
