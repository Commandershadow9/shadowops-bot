"""
State Manager for ShadowOps Bot.
Handles dynamic, runtime-specific data like created channel IDs.
This separates state from static configuration.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

logger = logging.getLogger('shadowops')

class StateManager:
    """
    Manages the bot's dynamic state in a JSON file, with multi-tenancy support.
    """

    def __init__(self, state_path: str = "data/state.json"):
        self.state_path = Path(state_path)
        self._state: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Loads state from the JSON file."""
        try:
            if self.state_path.exists():
                with open(self.state_path, 'r', encoding='utf-8') as f:
                    self._state = json.load(f)
                logger.info(f"✅ State loaded from {self.state_path}")
            else:
                logger.info("No state file found, starting with a fresh state.")
                self._state = {}
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"❌ Could not load state file at {self.state_path}: {e}. Starting fresh.", exc_info=True)
            self._state = {}
    
    def _save(self) -> None:
        """Saves the current state to the JSON file."""
        try:
            # Ensure the directory exists
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_path, 'w', encoding='utf-8') as f:
                json.dump(self._state, f, indent=4)
        except IOError as e:
            logger.error(f"❌ Could not save state file to {self.state_path}: {e}", exc_info=True)

    def get_guild_state(self, guild_id: int) -> Dict[str, Any]:
        """Gets the entire state dictionary for a specific guild."""
        return self._state.get(str(guild_id), {})

    def set_guild_state(self, guild_id: int, data: Dict[str, Any]) -> None:
        """Sets the entire state for a guild and saves."""
        self._state[str(guild_id)] = data
        self._save()

    def get_value(self, guild_id: int, key: str, default: Optional[Any] = None) -> Any:
        """Gets a specific value from a guild's state."""
        guild_state = self.get_guild_state(guild_id)
        return guild_state.get(key, default)

    def set_value(self, guild_id: int, key: str, value: Any) -> None:
        """Sets a specific value in a guild's state and saves."""
        if str(guild_id) not in self._state:
            self._state[str(guild_id)] = {}
            
        guild_state = self.get_guild_state(guild_id)
        guild_state[key] = value
        self.set_guild_state(guild_id, guild_state)

    # --- Convenience methods for channel IDs ---

    def get_channel_id(self, guild_id: int, channel_name: str) -> Optional[int]:
        """Gets a specific channel ID for a guild."""
        channels = self.get_value(guild_id, 'channels', {})
        channel_id = channels.get(channel_name)
        return int(channel_id) if channel_id else None

    def set_channel_id(self, guild_id: int, channel_name: str, channel_id: int) -> None:
        """Sets a channel ID for a guild and saves."""
        channels = self.get_value(guild_id, 'channels', {})
        channels[channel_name] = channel_id
        self.set_value(guild_id, 'channels', channels)


# Global singleton helper
state_manager: Optional[StateManager] = None


def get_state_manager() -> StateManager:
    global state_manager
    if state_manager is None:
        state_manager = StateManager()
    return state_manager
