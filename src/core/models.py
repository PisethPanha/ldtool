"""Data models and application state.

Defines dataclasses for representing LDPlayer instances and app profiles,
along with a singleton-like AppState object that may be referenced by the
UI to access shared application state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class LDInstance:
    """Represents a single LDPlayer emulator instance."""

    index: int
    """Numeric index of the instance."""
    name: str
    """Display name of the instance."""
    is_running: bool = False
    """True if the instance is currently running."""
    adb_serial: Optional[str] = None
    """ADB serial string like '127.0.0.1:5555', or None if not connected."""
    status: str = "Offline"
    """Current status as a string (e.g., 'running', 'stopped', 'starting')."""


@dataclass
class AppProfile:
    """Describes a mobile application to launch or automate."""

    name: str
    """Human-readable name of the app."""
    package: str
    """Android package name (e.g., 'com.example.app')."""
    activity: Optional[str] = None
    """Optional main activity name; if absent, uses app default."""


class AppState:
    """Shared application state accessible across tabs and components.

    Holds the current list of known instances and which ones the user
    has selected.  A single global object is provided via ``instance()``.
    """

    _instance: Optional[AppState] = None

    def __init__(self):
        # full list of instances maintained by index order
        self.instances: List[LDInstance] = []
        # mapping from index -> instance for quick lookup
        self.instances_by_index: Dict[int, LDInstance] = {}
        # set of indexes that are currently selected
        self.selected_indexes: set[int] = set()
        # available app profiles
        self.app_profiles: List[AppProfile] = []

    @classmethod
    def instance(cls) -> AppState:
        """Get the singleton instance of AppState."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_instances(self, instances: List[LDInstance] | List[Dict]) -> None:
        """Replace the current instances.

        * Accepts either a list of :class:`LDInstance` objects or a
          list of dictionaries produced by :meth:`LDPlayerController.list_instances`.
        * Rebuilds ``instances`` and ``instances_by_index``.
        * Selected indexes are retained only if they still exist.
        """
        new_list: List[LDInstance] = []
        new_map: Dict[int, LDInstance] = {}
        for entry in instances:
            if isinstance(entry, dict):
                idx = int(entry.get("index", -1))
                inst = LDInstance(
                    index=idx,
                    name=str(entry.get("name", "")),
                    is_running=bool(entry.get("is_running", False)),
                )
            elif isinstance(entry, LDInstance):
                inst = entry
                idx = inst.index
            else:
                continue
            new_list.append(inst)
            new_map[idx] = inst
        self.instances = new_list
        self.instances_by_index = new_map
        self.selected_indexes &= set(new_map.keys())

    def set_selected(self, index: int, selected: bool) -> None:
        """Mark an instance index as selected or deselected.

        If the index does not exist in ``instances_by_index`` the call is
        silently ignored.
        """
        if index not in self.instances_by_index:
            return
        if selected:
            self.selected_indexes.add(index)
        else:
            self.selected_indexes.discard(index)

    def get_selected_instances(self) -> List[LDInstance]:
        """Return list of selected instances.

        The caller may filter further (for example, only those with an
        ``adb_serial``) but this method simply returns whatever instances
        correspond to ``selected_indexes``.
        """
        return [self.instances_by_index[i] for i in self.selected_indexes if i in self.instances_by_index]

    def clear_selected(self) -> None:
        """Clear all selections."""
        self.selected_indexes.clear()

    def upsert_instance(self, inst: LDInstance) -> None:
        """Add or update a single instance in the state.

        The instance is inserted into both the list and the index map.
        """
        self.instances_by_index[inst.index] = inst
        # rebuild ordered list to keep it in sync
        self.instances = sorted(self.instances_by_index.values(), key=lambda x: x.index)
