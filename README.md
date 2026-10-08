# AGJarvis 🤖✨

> **Antigravity Discord Bridge & Autonomous Windows Desktop Jarvis Helper**
> Remote-control your PC, dictate tasks via Discord voice notes, and pair-program with Google Antigravity from anywhere.

---

## 🌟 Overview

**AGJarvis** connects **Google Antigravity** to **Discord** while turning your Windows PC into an autonomous desktop assistant ("Jarvis"). 

Whether you are away from your workstation or sitting at your desk, AGJarvis lets you:
- **Control Antigravity Remotely via Discord:** Create new conversation sessions, switch workspaces, select models, monitor step-by-step progress, and reply to agent clarification questions via interactive Discord UI buttons and modals.
- **Voice-to-Task Transcription:** Send voice notes directly from the Discord mobile app. The bridge uses **Google Gemini Multimodal STT** (`gemini-3.5-flash-lite` / `gemini-3.8-flash`) to accurately transcribe code, technical terms, and file paths.
- **Autonomous Windows Desktop Control (MCP Server):** Antigravity gains 35+ first-class desktop tools powered by FastMCP — including hardware-accelerated Windows OCR, template matching, coordinate navigation with grid overlays, mouse/keyboard simulation, and Excel automation.
- **Live Non-Intrusive HUD Overlay:** A sleek, transparent floating HUD at the top-right of your monitor shows you real-time agent intent, active tools, and heartbeat animations without stealing focus or appearing in AI screenshots.

---

## 🏛️ System Architecture

```text
       ┌───────────────────────────────┐
       │   Discord (Mobile / Desktop)  │
       │   - Voice Notes               │
       │   - Slash Commands            │
       │   - Interactive Modals/Buttons│
       └──────────────┬────────────────┘
                      │ (Discord Gateway API)
                      ▼
       ┌───────────────────────────────┐
       │    discord_bridge / bot.py    │
       │  - Gemini Multimodal STT      │
       │  - Thread-to-Session Mapping  │
       └──────┬─────────────────▲──────┘
              │                 │
 (agentapi CLI / LS)    (transcript.jsonl polling)
              │                 │
              ▼                 │
   ┌─────────────────────────────────────┐
   │          Google Antigravity         │
   │      (Language Server / Engine)     │
   └──────────────────┬──────────────────┘
                      │ (Model Context Protocol - STDIO)
                      ▼
   ┌─────────────────────────────────────┐
   │     jarvis-desktop MCP Server       │
   │  - Hardware-Accelerated OCR         │
   │  - Win32 & UIAutomation Input       │
   │  - OpenCV Template Matching         │
   │  - Excel Border Formatting          │
   └──────────────────┬──────────────────┘
                      │ (Status IPC)
                      ▼
   ┌─────────────────────────────────────┐
   │   Jarvis Floating HUD Overlay       │
   │  - WS_EX_TRANSPARENT (Click-thru)   │
   │  - WDA_EXCLUDEFROMCAPTURE           │
   └─────────────────────────────────────┘
```

---

## 🚀 Key Features

### 1. Discord Bridge (`discord_bridge/bot.py`)
- **Remote Antigravity Orchestration:** Start sessions (`/new`), list projects (`/projects`), browse conversations (`/convos`), and bind threads (`/bind`).
- **Interactive Clarification Modals:** When the agent calls `ask_question`, the bridge renders Discord interactive buttons and a write-in modal (`CustomAnswerModal`) so you can guide the agent seamlessly from mobile.
- **Multimodal Voice Notes:** Speak your commands into Discord. Powered by `google-genai` with fallback candidate models (`gemini-3.5-flash-lite` -> `gemini-3.8-flash`), delivering fast transcription.
- **Token & Resource Tracking:** Run `/usage` to view total tokens, transcript size, step counts, and a breakdown of executed tools.
- **Execution Policy & Model Selection:** Toggle between `auto` (autonomous execution) and `ask` policies, and switch models on the fly (`/model`).

### 2. Desktop Jarvis MCP Server (`discord_bridge/desktop_mcp.py`)
- **Hardware-Accelerated OCR (`winsdk.windows.media.ocr`):** `find_text_on_screen` and `click_text` locate text buttons, web links, or labels in ~0.2s without manual coordinate calculations.
- **Ruler Grid Screenshots (`take_screenshot_with_grid`):** Captures the screen with an overlay of 100px coordinate rulers, eliminating guesswork when clicking icons.
- **Reliable Text Entry (`paste_text`):** Pushes text into the Windows clipboard and performs an instantaneous `Ctrl+V` paste, immune to typing latency, dropped keystrokes, and keyboard layout mismatch.
- **Window & Modal Dialogs:** Deterministically close windows, focus applications, or click standard `#32770` dialog buttons (`OK`, `Cancel`, `Yes`, `No`).
- **Excel Border Formatting (`excel_format_borders`):** Programmatically applies pixel-perfect borders to spreadsheets using `openpyxl`.
- **System Vitals & Audio:** Inspect CPU, RAM, disk, active window, master volume levels, and Windows toast notifications.

### 3. Floating HUD Overlay (`discord_bridge/hud_overlay.py`)
- **Click-Through & Focus-Free:** Built with `WS_EX_TRANSPARENT` and `WS_EX_NOACTIVATE`. You can click underlying applications directly without interference.
- **AI-Invisible:** Protected with `WDA_EXCLUDEFROMCAPTURE`. The HUD is invisible to screenshots and OCR tools, preventing visual recursion loops.
- **Dynamic Status Badges:** Color-coded states for `RUN`, `THINK`, `VISION`, `INPUT`, `EXCEL`, `WAIT`, `DONE`, and `ERROR`.

---

## 📋 Prerequisites

- **Operating System:** Windows 10 or Windows 11 (64-bit)
- **Python:** 3.10, 3.11, or 3.12
- **Google Antigravity:** Installed and running on your machine
- **Discord Bot Token:** Created from the [Discord Developer Portal](https://discord.com/developers/applications)
- **Google Gemini API Key:** From [Google AI Studio](https://aistudio.google.com/apikey)

---

## 🛠️ Installation & Setup

### Step 1: Clone the Repository
```bash
git clone https://github.com/GladstoneOG/AGJarvis.git
cd AGJarvis
```

### Step 2: Install Python Dependencies
It is recommended to use a virtual environment or your standard Python environment:
```bash
pip install -r requirements.txt
```

> **Note for Windows SDK:** `winsdk` provides native Windows Media OCR. If you encounter any installation issues, ensure you are on Python 3.10–3.12 on 64-bit Windows.

---

### Step 3: Discord Bot Setup

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**.
2. Go to the **Bot** tab on the left menu:
   - Click **Add Bot**.
   - Under **Privileged Gateway Intents**, enable:
     - ✅ **Message Content Intent** (Required for reading instructions and voice notes)
     - ✅ **Server Members Intent**
   - Click **Reset Token** and copy your **Bot Token**.
3. Go to the **OAuth2 -> URL Generator** tab:
   - Under **Scopes**, select `bot` and `applications.commands`.
   - Under **Bot Permissions**, select `Administrator` (or *Send Messages, Create Public Threads, Attach Files, Embed Links, Read Message History*).
   - Copy the generated URL and open it in your browser to invite the bot to your Discord server.
4. *(Optional)* Copy your **Discord Server (Guild) ID**:
   - Enable Developer Mode in Discord settings (`Settings -> Advanced -> Developer Mode`).
   - Right-click your server icon and click **Copy Server ID**. Providing this in `.env` registers slash commands instantly without waiting for Discord's global cache.

---

### Step 4: Configure Environment Variables

In the `discord_bridge` directory, create a `.env` file based on `.env.example`:

```bash
copy discord_bridge\.env.example discord_bridge\.env
```

Open `discord_bridge/.env` in any text editor and fill in your keys:

```ini
# Discord Bot Token (from Discord Developer Portal)
DISCORD_BOT_TOKEN=your_discord_bot_token_here

# Google Gemini API Key (from https://aistudio.google.com/apikey)
GEMINI_API_KEY=your_gemini_api_key_here

# (Optional) Your Discord Server (Guild) ID for instant slash command synchronization
DISCORD_GUILD_ID=your_discord_server_id_here
```

> 🔒 **Security Notice:** The `.env` file contains sensitive private API tokens and is strictly ignored by `.gitignore`. Never share or commit this file!

---

### Step 5: Register the MCP Server in Antigravity

Antigravity discovers custom MCP tools through `mcp_config.json`.

1. Open your Antigravity global configuration file located at:
   `%USERPROFILE%\.gemini\config\mcp_config.json`
   *(e.g., `C:\Users\<YourUsername>\.gemini\config\mcp_config.json`)*
2. Add the `jarvis-desktop` entry to `mcpServers`. Replace the path with the absolute path to your cloned `desktop_mcp.py`:

```json
{
  "mcpServers": {
    "jarvis-desktop": {
      "command": "python",
      "args": [
        "C:\\path\\to\\AGJarvis\\discord_bridge\\desktop_mcp.py"
      ]
    }
  }
}
```

3. Restart Antigravity or reopen the workspace. The `jarvis-desktop` tools will now be available to the agent!

---

### Step 6: Agent Skill Setup

The agent documentation is located in `.agents/skills/jarvis-desktop/SKILL.md`.

When you open this repository (or any project workspace) in Antigravity:
- Antigravity automatically detects workspace skills located inside `.agents/skills/`.
- The agent will reference `jarvis-desktop` workflows, coordinate guidelines, OCR rules, and safe printing policies dynamically.

---

### Step 7: Run the Bridge

You can start the bridge using the provided batch launcher:
```bash
discord_bridge\run_bridge.bat
```
Or directly via Python:
```bash
python discord_bridge/bot.py
```

The script will:
1. Verify whether the Antigravity desktop application is running (and launch it if necessary).
2. Auto-discover the local Antigravity Language Server port and CSRF token.
3. Sync Discord slash commands to your guild.
4. Announce connection in your terminal: `Logged in as <BotName>! Ready to bridge with Antigravity.`

---

## 🎮 Discord Slash Commands

| Command | Description |
| :--- | :--- |
| `/new [project] [prompt]` | Starts a new Antigravity session in the specified workspace and creates a dedicated Discord thread. |
| `/projects` | Lists all detected Antigravity projects and workspaces. |
| `/convos [project]` | Displays recent conversations for a project. |
| `/bind <conversation_id>` | Binds the current Discord thread to an existing Antigravity conversation ID. |
| `/unbind` | Unbinds the current Discord thread from its Antigravity conversation. |
| `/model [model_name]` | Changes the active AI model for this session (e.g. Gemini 3.8 Flash, Pro, Claude). |
| `/policy [policy]` | Sets execution policy: `auto` (auto-run commands) or `ask` (confirm with user). |
| `/usage` | Displays session token footprint, step count, and tool breakdown. |
| `/screenshot` | Takes a real-time screenshot of your desktop and posts it into Discord. |
| `/hud [visible]` | Toggles or resets the floating desktop HUD overlay. |
| `/hotkey <keys>` | Sends a keyboard combination directly to the active Windows window (e.g., `win+d`, `alt+tab`). |
| `/vol [level]` | Checks or sets the Windows master audio volume (0–100%). |
| `/mute [state]` | Mutes or unmutes Windows master audio. |
| `/vitals` | Reports CPU usage, RAM utilization, active window title, and uptime. |
| `/ping` | Verifies bot connectivity and latency. |

---

## 🛠️ MCP Tools Reference (`jarvis-desktop`)

| Category | Tools | Description |
| :--- | :--- | :--- |
| **Vision & Screen** | `take_screenshot`, `take_screenshot_with_grid`, `screenshot_window`, `get_cursor_position` | Full desktop inspection, window isolation, coordinate rulers. |
| **OCR & Vision** | `find_text_on_screen`, `click_text` | Native Windows Media OCR for instantaneous button and label clicking. |
| **Template Matching** | `find_image_on_screen`, `click_image` | OpenCV matching for textless graphical icons. |
| **Mouse & Touch** | `mouse_click`, `mouse_move`, `mouse_drag`, `mouse_scroll` | Granular pointer movement and scrolling. |
| **Keyboard & Input** | `paste_text`, `type_text`, `press_key`, `hotkey` | Keystrokes, clipboard pasting, and shortcuts. |
| **Windows & Dialogs**| `list_windows`, `focus_window`, `close_window`, `click_dialog_button` | Window management and deterministic modal dialog clicks. |
| **Apps & Files** | `open_app_or_file`, `find_files` | Launch apps, open files in default handlers, filesystem search. |
| **Office Automation** | `get_open_excel_workbooks`, `excel_format_borders` | Open workbook inspection and programmatic cell borders. |
| **UI Automation** | `get_window_controls`, `click_ui_control` | Accessibility tree inspection and deterministic control interaction. |
| **System & Media** | `get_system_vitals`, `get_audio_volume`, `set_audio_volume`, `mute_audio`, `get_recent_notifications` | System diagnostics and audio control. |
| **HUD Control** | `set_hud_status`, `show_hud` | Updates the persistent top-right transparent activity overlay. |

---

## 📁 Repository Structure

```text
AGJarvis/
├── .agents/
│   └── skills/
│       └── jarvis-desktop/
│           └── SKILL.md            # Agent Skill documentation & operational guidelines
├── discord_bridge/
│   ├── antigravity_client.py       # Client for Antigravity Language Server & agentapi
│   ├── bot.py                      # Discord Bot with thread isolation, UI modals & slash commands
│   ├── config.py                   # Configuration & environment loading
│   ├── db.py                       # SQLite & JSON thread-to-conversation storage
│   ├── desktop_mcp.py              # FastMCP Desktop Automation Server (35+ tools)
│   ├── hud_overlay.py              # Transparent, click-through, capture-immune HUD overlay
│   ├── run_bridge.bat              # One-click Windows startup script
│   ├── transcriber.py              # Gemini Multimodal speech-to-text transcriber
│   ├── test_audio.py               # Audio transcription diagnostic script
│   ├── test_line_by_line.py        # Gemini API diagnostic script
│   ├── .env.example                # Example environment variables template
│   └── thread_mappings.json.example# Example thread mappings structure
├── mcp_schemas/
│   └── jarvis-desktop/             # JSON schemas for all 35 MCP tools
├── mcp_config.example.json         # Example Antigravity MCP server configuration
├── requirements.txt                # Python package dependencies
├── .gitignore                      # Git ignore rules protecting .env and logs
└── README.md                       # Comprehensive setup guide
```

---

## 🔒 Security Best Practices

1. **Keep `.env` Private:** Your Discord bot token and Gemini API key have access to your bot and Google Cloud quota. Never commit `.env`.
2. **Safe Desktop Execution:** Because `jarvis-desktop` provides native OS-level mouse and keyboard emulation, only run the Discord bot in private servers where you trust authorized users.
3. **Physical Printing Policy:** The agent is configured by default not to send print jobs to physical printers unless explicitly requested.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
