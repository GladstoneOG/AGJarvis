import sqlite3
import json
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional
from config import CONVERSATION_DB, MAPPING_FILE

def get_projects() -> List[Dict]:
    """
    Returns a list of all distinct projects found in Antigravity's database.
    """
    if not CONVERSATION_DB.exists():
        return []

    conn = sqlite3.connect(str(CONVERSATION_DB))
    cursor = conn.cursor()
    rows = cursor.execute("""
        SELECT project_id, workspace_uris, title, last_modified_time
        FROM conversation_summaries
        WHERE workspace_uris IS NOT NULL AND workspace_uris != '[]'
        ORDER BY last_modified_time DESC
    """).fetchall()
    conn.close()

    projects_dict = {}
    for pid, uris_raw, title, last_mod in rows:
        try:
            uris = json.loads(uris_raw)
            for u in uris:
                clean_path = urllib.parse.unquote(u.replace("file:///", "").replace("file://", ""))
                name = Path(clean_path).name or clean_path
                if name not in projects_dict:
                    projects_dict[name] = {
                        "name": name,
                        "project_id": pid,
                        "path": clean_path,
                        "conv_count": 1,
                        "last_modified": last_mod
                    }
                else:
                    projects_dict[name]["conv_count"] += 1
        except Exception:
            pass

    return sorted(projects_dict.values(), key=lambda p: p["name"].lower())

def get_conversations_for_project(project_name: str, limit: int = 25) -> List[Dict]:
    """
    Retrieves conversations for a project. Falls back to preview if title is empty.
    """
    if not CONVERSATION_DB.exists():
        return []

    conn = sqlite3.connect(str(CONVERSATION_DB))
    cursor = conn.cursor()
    rows = cursor.execute("""
        SELECT conversation_id, title, preview, workspace_uris, project_id, last_modified_time
        FROM conversation_summaries
        WHERE workspace_uris IS NOT NULL
        ORDER BY last_modified_time DESC
    """).fetchall()
    conn.close()

    matched = []
    for cid, raw_title, preview, uris_raw, pid, last_mod in rows:
        try:
            uris = json.loads(uris_raw)
            for u in uris:
                clean_path = urllib.parse.unquote(u.replace("file:///", "").replace("file://", ""))
                name = Path(clean_path).name or clean_path
                if name.lower() == project_name.lower():
                    # Fallback to preview or untitled if title is empty
                    clean_title = (raw_title or "").strip()
                    if not clean_title:
                        clean_title = (preview or "").strip()
                    if not clean_title:
                        clean_title = f"Conversation {cid[:8]}"

                    matched.append({
                        "conversation_id": cid,
                        "title": clean_title,
                        "project_id": pid,
                        "path": clean_path,
                        "last_modified": last_mod
                    })
                    break
        except Exception:
            pass

        if len(matched) >= limit:
            break

    return matched

def get_conversation_info(conversation_id: str) -> Optional[Dict]:
    """Retrieves metadata for a specific conversation by ID."""
    if not CONVERSATION_DB.exists():
        return None
    try:
        conn = sqlite3.connect(str(CONVERSATION_DB))
        cursor = conn.cursor()
        row = cursor.execute("""
            SELECT conversation_id, title, preview, workspace_uris, project_id
            FROM conversation_summaries
            WHERE conversation_id = ?
        """, (conversation_id,)).fetchone()
        conn.close()
        if row:
            cid, raw_title, preview, uris_raw, pid = row
            clean_title = (raw_title or "").strip() or (preview or "").strip() or f"Conversation {cid[:8]}"
            clean_path = ""
            try:
                uris = json.loads(uris_raw)
                if uris:
                    clean_path = urllib.parse.unquote(uris[0].replace("file:///", "").replace("file://", ""))
            except Exception:
                pass
            return {
                "conversation_id": cid,
                "title": clean_title,
                "project_id": pid,
                "workspace_path": clean_path
            }
    except Exception:
        pass
    return None

# --- Thread Mapping Storage ---

def load_mappings() -> Dict[str, Dict]:
    if not MAPPING_FILE.exists():
        return {}
    try:
        with open(MAPPING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_mappings(mappings: Dict[str, Dict]):
    with open(MAPPING_FILE, "w", encoding="utf-8") as f:
        json.dump(mappings, f, indent=2)

def set_thread_binding(
    thread_id: int | str,
    conversation_id: str,
    project_name: str,
    workspace_path: str,
    title: str,
    project_id: Optional[str] = None,
    last_synced_line: int = 0,
    model: str = "Gemini 3.8 Flash (High)",
    model_id: Optional[str] = None,
    policy: str = "auto"
):
    mappings = load_mappings()
    if not project_id:
        info = get_conversation_info(conversation_id)
        if info:
            project_id = info.get("project_id")

    # Remove any other thread binding with this conversation_id to enforce strict 1:1 mapping
    for old_tid, info in list(mappings.items()):
        if str(old_tid) != str(thread_id) and info.get("conversation_id") == conversation_id:
            del mappings[old_tid]

    mappings[str(thread_id)] = {
        "conversation_id": conversation_id,
        "project_name": project_name,
        "workspace_path": workspace_path,
        "title": title,
        "project_id": project_id or "",
        "last_synced_line": last_synced_line,
        "model": model,
        "model_id": model_id or "",
        "policy": policy
    }
    save_mappings(mappings)

def get_thread_binding(thread_id: int | str) -> Optional[Dict]:
    mappings = load_mappings()
    return mappings.get(str(thread_id))

def find_thread_for_conversation(conversation_id: str) -> Optional[str]:
    """Checks if a conversation is already bound to a Discord thread ID."""
    mappings = load_mappings()
    for tid, info in mappings.items():
        if info.get("conversation_id") == conversation_id:
            return tid
    return None

def update_thread_synced_line(thread_id: int | str, line_count: int):
    mappings = load_mappings()
    tid = str(thread_id)
    if tid in mappings:
        mappings[tid]["last_synced_line"] = line_count
        save_mappings(mappings)

def update_thread_model(thread_id: int | str, model: str, model_id: Optional[str] = None):
    mappings = load_mappings()
    tid = str(thread_id)
    if tid in mappings:
        mappings[tid]["model"] = model
        if model_id:
            mappings[tid]["model_id"] = model_id
        save_mappings(mappings)

def update_thread_policy(thread_id: int | str, policy: str):
    mappings = load_mappings()
    tid = str(thread_id)
    if tid in mappings:
        mappings[tid]["policy"] = policy
        save_mappings(mappings)

def delete_thread_binding(thread_id: int | str):
    mappings = load_mappings()
    tid = str(thread_id)
    if tid in mappings:
        del mappings[tid]
        save_mappings(mappings)

def get_conversation_usage(conversation_id: str) -> Dict:
    """
    Computes comprehensive usage statistics for a conversation.
    """
    from collections import Counter
    from config import BRAIN_DIR

    stats = {
        "step_count": 0,
        "user_prompts": 0,
        "agent_turns": 0,
        "tool_breakdown": {},
        "transcript_size_kb": 0.0,
        "estimated_tokens": 0,
        "last_modified": "Unknown",
        "status": "Unknown"
    }

    # 1. Database summary info
    if CONVERSATION_DB.exists():
        try:
            conn = sqlite3.connect(str(CONVERSATION_DB))
            row = conn.execute("""
                SELECT step_count, last_modified_time, status
                FROM conversation_summaries
                WHERE conversation_id = ?
            """, (conversation_id,)).fetchone()
            conn.close()
            if row:
                stats["step_count"] = row[0] or 0
                stats["last_modified"] = row[1] or "Unknown"
                stats["status"] = row[2] or "Unknown"
        except Exception:
            pass

    # 2. Transcript analysis
    tpath = BRAIN_DIR / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"
    if tpath.exists():
        try:
            tools = Counter()
            user_count = 0
            agent_count = 0
            char_count = 0

            with open(tpath, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    char_count += len(line)
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        data = json.loads(line_str)
                        stype = data.get("type")
                        if stype == "USER_INPUT":
                            user_count += 1
                        elif stype == "PLANNER_RESPONSE":
                            agent_count += 1
                            for tc in data.get("tool_calls", []):
                                tools[tc.get("name")] += 1
                    except Exception:
                        pass

            stats["user_prompts"] = user_count
            stats["agent_turns"] = agent_count
            stats["tool_breakdown"] = dict(tools)
            stats["transcript_size_kb"] = round(char_count / 1024, 1)
            # Estimate ~4 characters per token
            stats["estimated_tokens"] = round(char_count / 4)
        except Exception:
            pass

    return stats

