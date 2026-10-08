import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from the current directory
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID", "").strip()

# Antigravity Paths
ANTIGRAVITY_APP_DATA = Path.home() / ".gemini" / "antigravity"
CONVERSATION_DB = ANTIGRAVITY_APP_DATA / "conversation_summaries.db"
BRAIN_DIR = ANTIGRAVITY_APP_DATA / "brain"
AGENTAPI_BAT = ANTIGRAVITY_APP_DATA / "bin" / "agentapi.bat"

# Local mapping database file
MAPPING_FILE = Path(__file__).parent / "thread_mappings.json"
