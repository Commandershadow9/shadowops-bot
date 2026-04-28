import sys
from unittest.mock import MagicMock
sys.modules['discord'] = MagicMock()
sys.modules['discord.ext'] = MagicMock()
sys.modules['discord.ext.commands'] = MagicMock()
sys.modules['discord.app_commands'] = MagicMock()
from src.cogs.inspector import InspectorCog
print("Success")
