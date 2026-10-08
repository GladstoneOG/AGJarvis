import asyncio
import json
import logging
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional, Set
from config import ANTIGRAVITY_APP_DATA, BRAIN_DIR, CONVERSATION_DB, MAPPING_FILE
from db import get_conversation_info

logger = logging.getLogger(__name__)

def _resolve_language_server_path() -> Path:
    env_path = os.environ.get("ANTIGRAVITY_LS_PATH")
    if env_path and Path(env_path).exists():
        return Path(env_path)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        p = Path(local_app_data) / "Programs" / "Antigravity" / "resources" / "bin" / "language_server.exe"
        if p.exists():
            return p
    home_p = Path.home() / "AppData" / "Local" / "Programs" / "Antigravity" / "resources" / "bin" / "language_server.exe"
    if home_p.exists():
        return home_p
    return Path(local_app_data or str(Path.home())) / "Programs" / "Antigravity" / "resources" / "bin" / "language_server.exe"

LANGUAGE_SERVER_EXE = _resolve_language_server_path()

_CACHED_LS_INFO: Optional[Dict] = None

def discover_language_server(force_refresh: bool = False) -> Optional[Dict]:
    """
    Auto-discovers the active language_server.exe process port and CSRF token.
    This enables standalone bots outside the terminal to connect reliably.
    """
    global _CACHED_LS_INFO
    if _CACHED_LS_INFO and not force_refresh:
        return _CACHED_LS_INFO

    ps_cmd = (
        "$proc = Get-CimInstance Win32_Process -Filter \"name = 'language_server.exe'\" | Select-Object -First 1 ProcessId, CommandLine; "
        "if ($proc) { "
        "$conn = Get-NetTCPConnection -OwningProcess $proc.ProcessId -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalAddress -eq '127.0.0.1' } | Select-Object -First 1 LocalPort; "
        "Write-Output \"PID:$($proc.ProcessId)|PORT:$($conn.LocalPort)|CMD:$($proc.CommandLine)\" "
        "}"
    )
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True, check=False)
        out = res.stdout.strip()
        if not out or "|" not in out:
            return None

        parts = out.split("|", 2)
        pid_str = parts[0].replace("PID:", "")
        port_str = parts[1].replace("PORT:", "")
        cmd = parts[2] if len(parts) > 2 else ""

        csrf_match = re.search(r"--csrf_token\s+([a-zA-Z0-9\-]+)", cmd)
        csrf_token = csrf_match.group(1) if csrf_match else ""

        if port_str and csrf_token:
            _CACHED_LS_INFO = {
                "pid": pid_str,
                "address": f"localhost:{port_str}",
                "csrf_token": csrf_token
            }
            logger.info(f"Discovered Antigravity Language Server: {_CACHED_LS_INFO}")
            return _CACHED_LS_INFO
    except Exception as e:
        logger.error(f"Failed to discover language server: {e}")

    return None

def build_agentapi_env(project_id: Optional[str] = None) -> Dict[str, str]:
    """Builds a clean environment with active LS address, token, and matching project_id."""
    ls_info = discover_language_server()
    if not ls_info:
        # Retry with force_refresh
        ls_info = discover_language_server(force_refresh=True)

    if not ls_info:
        raise RuntimeError("Antigravity is not currently running. Please launch the Antigravity desktop application.")

    # Strip existing ANTIGRAVITY_* vars from parent to avoid cross-project conflicts
    clean_env = {k: v for k, v in os.environ.items() if not k.startswith("ANTIGRAVITY_")}
    clean_env["ANTIGRAVITY_LS_ADDRESS"] = ls_info["address"]
    clean_env["ANTIGRAVITY_CSRF_TOKEN"] = ls_info["csrf_token"]
    if project_id:
        clean_env["ANTIGRAVITY_PROJECT_ID"] = project_id

    return clean_env

def get_transcript_path(conversation_id: str) -> Path:
    return BRAIN_DIR / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"

def get_user_uploaded_dir(conversation_id: str) -> Path:
    target = BRAIN_DIR / conversation_id / ".user_uploaded"
    target.mkdir(parents=True, exist_ok=True)
    return target

def get_latest_conversation_id(workspace_path: Optional[str] = None, exclude_ids: Optional[Set[str]] = None) -> Optional[str]:
    """Retrieves the most recently created conversation_id from the database, excluding already bound conversations."""
    import sqlite3
    if not CONVERSATION_DB.exists():
        return None
    try:
        conn = sqlite3.connect(str(CONVERSATION_DB))
        cursor = conn.cursor()
        excluded = exclude_ids or set()
        if workspace_path:
            rows = cursor.execute("""
                SELECT conversation_id, workspace_uris FROM conversation_summaries
                ORDER BY last_modified_time DESC LIMIT 20
            """).fetchall()
            conn.close()
            w_name = Path(workspace_path).name.lower()
            for cid, uris_raw in rows:
                if cid not in excluded and uris_raw and w_name in uris_raw.lower():
                    return cid
        else:
            rows = cursor.execute("""
                SELECT conversation_id FROM conversation_summaries
                ORDER BY last_modified_time DESC LIMIT 20
            """).fetchall()
            conn.close()
            for row in rows:
                if row[0] not in excluded:
                    return row[0]
    except Exception as e:
        logger.error(f"Error querying latest conversation: {e}")
    return None

def start_new_conversation(
    workspace_path: str,
    title: str,
    initial_prompt: str,
    project_id: Optional[str] = None,
    model: Optional[str] = None,
    model_id: Optional[str] = None
) -> str:
    """
    Starts a new Antigravity conversation bound to workspace_path using agentapi.
    Returns the unique new conversation_id.
    """
    if model_id:
        set_active_antigravity_model(model_id)

    env = build_agentapi_env(project_id)
    cmd = [
        str(LANGUAGE_SERVER_EXE),
        "agentapi",
        "new-conversation",
    ]
    if model and model in ("flash", "pro", "flash_lite") and not model_id:
        cmd.append(f"--model={model}")

    cmd.extend([
        f"--title={title}",
        initial_prompt
    ])

    cwd = workspace_path if Path(workspace_path).exists() else None
    logger.info(f"Launching new conversation in cwd={cwd}")

    res = subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True, check=False)
    output = res.stdout.strip()
    logger.info(f"new-conversation result: {output}")

    if res.returncode != 0:
        err_msg = output or res.stderr.strip()
        try:
            err_data = json.loads(output)
            err_msg = err_data.get("error", err_msg)
        except Exception:
            pass
        raise RuntimeError(f"Failed to start conversation: {err_msg}")

    # Parse conversation_id from output - check newConversation object first!
    try:
        data = json.loads(output)
        resp = data.get("response", {})
        cid = (
            resp.get("newConversation", {}).get("conversationId")
            or resp.get("conversationId")
            or data.get("conversation_id")
        )
        if cid:
            logger.info(f"Successfully extracted new conversation ID: {cid}")
            return cid
    except Exception as e:
        logger.error(f"Error parsing conversationId from output: {e}")

    # Fallback to database lookup, strictly excluding already bound threads
    time.sleep(1.2)
    bound_cids: Set[str] = set()
    if MAPPING_FILE.exists():
        try:
            with open(MAPPING_FILE, "r", encoding="utf-8") as f:
                mappings = json.load(f)
                bound_cids = {info.get("conversation_id") for info in mappings.values() if info.get("conversation_id")}
        except Exception:
            pass

    cid = get_latest_conversation_id(workspace_path, exclude_ids=bound_cids)
    if cid:
        logger.info(f"Found new conversation ID via database fallback: {cid}")
        return cid

    raise RuntimeError(f"Could not retrieve new conversation ID: {output}")

def send_message_to_conversation(conversation_id: str, prompt: str, project_id: Optional[str] = None) -> None:
    """
    Sends a message to an existing conversation session via agentapi.
    """
    # If project_id not provided, look it up in DB
    if not project_id:
        info = get_conversation_info(conversation_id)
        if info:
            project_id = info.get("project_id")

    env = build_agentapi_env(project_id)
    cmd = [
        str(LANGUAGE_SERVER_EXE),
        "agentapi",
        "send-message",
        conversation_id,
        prompt
    ]

    res = subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)
    output = res.stdout.strip()

    if res.returncode != 0:
        err_msg = output or res.stderr.strip()
        try:
            err_data = json.loads(output)
            err_msg = err_data.get("error", err_msg)
        except Exception:
            pass
        logger.error(f"send-message error: {err_msg}")
        raise RuntimeError(err_msg)

def count_transcript_lines(conversation_id: str) -> int:
    tpath = get_transcript_path(conversation_id)
    if not tpath.exists():
        return 0
    try:
        with open(tpath, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0

async def monitor_response(
    conversation_id: str,
    start_line_count: int,
    timeout_seconds: int = 3600
) -> AsyncGenerator[Dict, None]:
    """
    Tails transcript.jsonl starting after start_line_count.
    Defaults to 1-hour timeout (3600s), and automatically resets activity timer
    whenever the agent executes a tool, produces thoughts, or updates logs.
    Set timeout_seconds <= 0 for unlimited.
    """
    tpath = get_transcript_path(conversation_id)
    start_time = time.time()
    last_activity_time = time.time()
    last_processed_line = start_line_count
    final_content = None

    while (timeout_seconds <= 0) or (time.time() - last_activity_time < timeout_seconds):
        if tpath.exists():
            try:
                with open(tpath, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()

                if len(lines) > last_processed_line:
                    last_activity_time = time.time()
                    for i in range(last_processed_line, len(lines)):
                        line_str = lines[i].strip()
                        if not line_str:
                            continue
                        try:
                            step = json.loads(line_str)
                            stype = step.get("type")
                            source = step.get("source")

                            # If model produces thoughts or tool calls
                            if source == "MODEL" and stype == "PLANNER_RESPONSE":
                                tool_calls = step.get("tool_calls", [])
                                thinking = step.get("thinking", "")
                                content = step.get("content", "")

                                if tool_calls:
                                    t_desc = []
                                    for tc in tool_calls:
                                        name = tc.get("name", "")
                                        args = tc.get("args", {})
                                        if name == "ask_question":
                                            questions = args.get("questions", [])
                                            yield {"type": "question", "questions": questions}
                                        elif name == "run_command":
                                            cmd = " ".join(args.get("CommandLine", "").split())
                                            t_desc.append(f"⚙️ Running `{cmd[:60]}`")
                                        elif name == "view_file":
                                            fpath = Path(args.get("AbsolutePath", "")).name
                                            t_desc.append(f"📖 Reading `{fpath}`")
                                        elif name in ("write_to_file", "replace_file_content"):
                                            fpath = Path(args.get("TargetFile", "")).name
                                            t_desc.append(f"✍️ Editing `{fpath}`")
                                        elif name == "call_mcp_tool":
                                            sname = args.get("ServerName", "mcp")
                                            tname = args.get("ToolName", "tool")
                                            t_desc.append(f"🖥️ `{sname}`: `{tname}`")
                                        elif name.startswith("mcp_"):
                                            t_desc.append(f"🖥️ `{name[4:]}`")
                                        elif name == "take_screenshot":
                                            t_desc.append("📸 Capturing screenshot")
                                        elif name in ("mouse_click", "click_text"):
                                            target = args.get("text", f"({args.get('x')}, {args.get('y')})")
                                            t_desc.append(f"🖱️ Clicking `{target}`")
                                        elif name in ("type_text", "paste_text"):
                                            t_desc.append("⌨️ Entering text")
                                        elif name == "hotkey":
                                            combo = "+".join(args.get("keys", []))
                                            t_desc.append(f"⌨️ Hotkey `{combo}`")
                                        elif name == "focus_window":
                                            t_desc.append(f"🪟 Focusing `{args.get('query', '')[:30]}`")
                                        elif name == "search_web":
                                            t_desc.append(f"🔍 Web search: `{args.get('query', '')[:40]}`")
                                        else:
                                            t_desc.append(f"🛠️ `{name}`")
                                    if t_desc:
                                        yield {"type": "status", "text": "\n".join(t_desc)}
                                elif thinking and not content:
                                    clean_think = thinking.strip().split("\n")[0][:120]
                                    yield {"type": "status", "text": f"🧠 *Thinking:* {clean_think}..."}
                                elif content:
                                    final_content = content
                        except Exception:
                            pass
                    last_processed_line = len(lines)

                    # If we received final content and the model is done
                    if final_content:
                        artifacts = []
                        brain_dir = BRAIN_DIR / conversation_id
                        if brain_dir.exists():
                            for p in brain_dir.glob("*.md"):
                                if p.stat().st_mtime >= start_time:
                                    artifacts.append(str(p))
                        yield {"type": "final", "content": final_content, "artifacts": artifacts}
                        return
            except Exception as e:
                logger.error(f"Error reading transcript: {e}")

        await asyncio.sleep(1.2)

    if final_content:
        yield {"type": "final", "content": final_content, "artifacts": []}
    else:
        yield {"type": "status", "text": "⚠️ Operation timed out or agent is taking longer than expected."}


# --- Dynamic Model Discovery & Switching ---

FALLBACK_CASCADE_MODELS = [
    {"label": "Gemini 3.8 Flash (High)", "model_id": "MODEL_PLACEHOLDER_M318", "quota_percent": 65.0, "family": "Gemini Flash", "emoji": "⚡"},
    {"label": "Gemini 3.8 Flash (Medium)", "model_id": "MODEL_PLACEHOLDER_M319", "quota_percent": 65.0, "family": "Gemini Flash", "emoji": "⚡"},
    {"label": "Gemini 3.8 Flash (Low)", "model_id": "MODEL_PLACEHOLDER_M320", "quota_percent": 65.0, "family": "Gemini Flash", "emoji": "⚡"},
    {"label": "Gemini 3.7 Flash (High)", "model_id": "MODEL_PLACEHOLDER_M298", "quota_percent": 65.0, "family": "Gemini Flash", "emoji": "⚡"},
    {"label": "Gemini 3.7 Flash (Medium)", "model_id": "MODEL_PLACEHOLDER_M299", "quota_percent": 65.0, "family": "Gemini Flash", "emoji": "⚡"},
    {"label": "Gemini 3.7 Flash (Low)", "model_id": "MODEL_PLACEHOLDER_M300", "quota_percent": 65.0, "family": "Gemini Flash", "emoji": "⚡"},
    {"label": "Gemini 3.6 Flash (High)", "model_id": "MODEL_PLACEHOLDER_M71", "quota_percent": 65.0, "family": "Gemini Flash", "emoji": "⚡"},
    {"label": "Gemini 3.6 Flash (Medium)", "model_id": "MODEL_PLACEHOLDER_M72", "quota_percent": 65.0, "family": "Gemini Flash", "emoji": "⚡"},
    {"label": "Gemini 3.6 Flash (Low)", "model_id": "MODEL_PLACEHOLDER_M73", "quota_percent": 65.0, "family": "Gemini Flash", "emoji": "⚡"},
    {"label": "Gemini 3.1 Pro (High)", "model_id": "MODEL_PLACEHOLDER_M16", "quota_percent": 65.0, "family": "Gemini Pro", "emoji": "🧠"},
    {"label": "Gemini 3.1 Pro (Low)", "model_id": "MODEL_PLACEHOLDER_M36", "quota_percent": 65.0, "family": "Gemini Pro", "emoji": "🧠"},
    {"label": "Claude Opus 5.5 (High)", "model_id": "MODEL_PLACEHOLDER_M402", "quota_percent": 100.0, "family": "Claude", "emoji": "🎭"},
    {"label": "Claude Opus 5.5 (Medium)", "model_id": "MODEL_PLACEHOLDER_M401", "quota_percent": 100.0, "family": "Claude", "emoji": "🎭"},
    {"label": "Claude Opus 5.5 (Low)", "model_id": "MODEL_PLACEHOLDER_M400", "quota_percent": 100.0, "family": "Claude", "emoji": "🎭"},
    {"label": "Claude Sonnet 5.5 (High)", "model_id": "MODEL_PLACEHOLDER_M405", "quota_percent": 100.0, "family": "Claude", "emoji": "🎭"},
    {"label": "Claude Sonnet 5.5 (Medium)", "model_id": "MODEL_PLACEHOLDER_M404", "quota_percent": 100.0, "family": "Claude", "emoji": "🎭"},
    {"label": "Claude Sonnet 5.5 (Low)", "model_id": "MODEL_PLACEHOLDER_M403", "quota_percent": 100.0, "family": "Claude", "emoji": "🎭"},
    {"label": "GPT-OSS 120B (Medium)", "model_id": "MODEL_OPENAI_GPT_OSS_120B_MEDIUM", "quota_percent": 100.0, "family": "OpenAI", "emoji": "🌐"},
]

def get_cascade_models() -> List[Dict]:
    """
    Dynamically queries Antigravity Language Server for the real list of available models,
    including reasoning budgets and real-time remaining quota fractions.
    """
    info = discover_language_server()
    if not info:
        return FALLBACK_CASCADE_MODELS

    port = info["address"].split(":")[1]
    token = info["csrf_token"]
    url = f"http://127.0.0.1:{port}/exa.language_server_pb.LanguageServerService/GetCascadeModelConfigData"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "x-codeium-csrf-token": token
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = []
            for m in data.get("clientModelConfigs", []):
                quota = m.get("quotaInfo", {})
                rem = round(quota.get("remainingFraction", 1.0) * 100, 1)
                lbl = m.get("label", "Unknown")
                mid = m.get("modelOrAlias", {}).get("model", "")

                emoji = "🤖"
                family = "Other"
                if "Gemini 3.1 Pro" in lbl:
                    emoji = "🧠"
                    family = "Gemini Pro"
                elif "Gemini" in lbl:
                    emoji = "⚡"
                    family = "Gemini Flash"
                elif "Claude" in lbl:
                    emoji = "🎭"
                    family = "Claude"
                elif "GPT" in lbl:
                    emoji = "🌐"
                    family = "OpenAI"

                models.append({
                    "label": lbl,
                    "model_id": mid,
                    "quota_percent": rem,
                    "reset_time": quota.get("resetTime", ""),
                    "family": family,
                    "emoji": emoji,
                    "is_recommended": m.get("isRecommended", False)
                })

            def sort_key(m):
                lbl = m.get("label", "")
                prio = 99
                if "Gemini 3.8 Flash" in lbl: prio = 10
                elif "Gemini 3.7 Flash" in lbl: prio = 20
                elif "Gemini 3.6 Flash" in lbl: prio = 30
                elif "Gemini 3.1 Pro" in lbl: prio = 40
                elif "Claude Opus 5.5" in lbl: prio = 50
                elif "Claude Sonnet 5.5" in lbl: prio = 60
                elif "GPT-OSS" in lbl: prio = 70

                effort = 2
                if "(High)" in lbl: effort = 1
                elif "(Medium)" in lbl: effort = 2
                elif "(Low)" in lbl: effort = 3
                return (prio, effort)

            models.sort(key=sort_key)
            return models if models else FALLBACK_CASCADE_MODELS
    except Exception as e:
        logger.warning(f"Failed to fetch live model configs: {e}")
        return FALLBACK_CASCADE_MODELS

def get_active_antigravity_model() -> str:
    """Reads the current active model from antigravity_state.pbtxt."""
    pbtxt_path = ANTIGRAVITY_APP_DATA / "antigravity_state.pbtxt"
    if pbtxt_path.exists():
        try:
            with open(pbtxt_path, "r", encoding="utf-8") as f:
                content = f.read()
            m = re.search(r"last_selected_agent_model:\s*([A-Za-z0-9_]+)", content)
            if m:
                return m.group(1).strip()
        except Exception:
            pass
    return "MODEL_PLACEHOLDER_M318"

def set_active_antigravity_model(model_id: str) -> bool:
    """Updates the active model in antigravity_state.pbtxt."""
    pbtxt_path = ANTIGRAVITY_APP_DATA / "antigravity_state.pbtxt"
    if not pbtxt_path.exists():
        return False
    try:
        with open(pbtxt_path, "r", encoding="utf-8") as f:
            content = f.read()
        if "last_selected_agent_model:" in content:
            new_content = re.sub(
                r"last_selected_agent_model:\s*[A-Za-z0-9_]+",
                f"last_selected_agent_model:  {model_id}",
                content
            )
        else:
            new_content = content + f"\nlast_selected_agent_model:  {model_id}\n"
        with open(pbtxt_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        logger.info(f"Updated antigravity_state.pbtxt last_selected_agent_model to {model_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to set active model in antigravity_state.pbtxt: {e}")
        return False

