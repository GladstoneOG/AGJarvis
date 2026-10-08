"""
Jarvis Desktop Minimalist HUD & Activity Overlay
A sleek, non-intrusive, click-through floating activity window.

Key Features:
- WS_EX_TRANSPARENT: Clicks pass directly to apps underneath.
- WS_EX_NOACTIVATE: Never steals window focus.
- WS_EX_TOPMOST: Stays on top of full-screen windows.
- WS_EX_TOOLWINDOW: Hidden from taskbar and Alt+Tab.
- WDA_EXCLUDEFROMCAPTURE: Invisible to AI screenshots / OCR.
- Inactivity Auto-Close: Closes automatically if idle for >10s or after completion.
- Ultra-Minimalist: Crisp, concise status indicators without verbose clutter.
"""

import os
import sys
import time
import json
import ctypes
from datetime import datetime
import psutil
import tkinter as tk

user32 = ctypes.windll.user32

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WDA_EXCLUDEFROMCAPTURE = 0x00000011

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "desktop_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
STATUS_FILE = os.path.join(CACHE_DIR, "hud_status.json")
PID_FILE = os.path.join(CACHE_DIR, "hud.pid")

INACTIVITY_TIMEOUT = 10.0  # Seconds of silence before auto-closing

def check_single_instance():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            if old_pid != os.getpid() and psutil.pid_exists(old_pid):
                p = psutil.Process(old_pid)
                if any("hud_overlay" in arg.lower() for arg in p.cmdline()):
                    # Another instance is running, exit this one
                    sys.exit(0)
        except Exception:
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

THEMES = {
    "busy":     {"badge": "RUN",    "color": "#a6e3a1", "icon": "●"},
    "thinking": {"badge": "THINK",  "color": "#f9e2af", "icon": "⠋"},
    "vision":   {"badge": "VISION", "color": "#89b4fa", "icon": "◉"},
    "input":    {"badge": "INPUT",  "color": "#fab387", "icon": "⌨"},
    "excel":    {"badge": "EXCEL",  "color": "#cba6f7", "icon": "▦"},
    "waiting":  {"badge": "WAIT",   "color": "#f5c2e7", "icon": "◌"},
    "success":  {"badge": "DONE",   "color": "#94e2d5", "icon": "✓"},
    "error":    {"badge": "ERROR",  "color": "#f38ba8", "icon": "✕"},
    "idle":     {"badge": "IDLE",   "color": "#6c7086", "icon": "·"}
}

SPINNERS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class JarvisMinimalHUD:
    def __init__(self):
        check_single_instance()
        
        try:
            import win32service, win32con
            h_desk = win32service.OpenDesktop("Default", 0, False, win32con.GENERIC_ALL)
            user32.SetThreadDesktop(int(h_desk))
        except Exception:
            pass

        self.root = tk.Tk()
        self.root.title("JarvisHUD_Overlay")
        
        # Ultra-compact dimensions (280x120) docked top-right
        self.width = 280
        self.height = 120
        screen_w = self.root.winfo_screenwidth()
        pos_x = max(10, screen_w - self.width - 20)
        pos_y = 15
        self.root.geometry(f"{self.width}x{self.height}+{pos_x}+{pos_y}")
        
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.90)
        self.root.configure(bg="#11111b")
        
        # Outer Border
        self.border = tk.Frame(self.root, bg="#313244", padx=1, pady=1)
        self.border.pack(fill="both", expand=True)
        
        # Body Container
        self.body = tk.Frame(self.border, bg="#11111b", padx=8, pady=6)
        self.body.pack(fill="both", expand=True)
        
        # --- Top Header ---
        self.header = tk.Frame(self.body, bg="#11111b")
        self.header.pack(fill="x")
        
        self.title_lbl = tk.Label(
            self.header,
            text="JARVIS",
            font=("Segoe UI", 8, "bold"),
            fg="#cdd6f4",
            bg="#11111b"
        )
        self.title_lbl.pack(side="left")
        
        self.badge_lbl = tk.Label(
            self.header,
            text="IDLE",
            font=("Segoe UI", 7, "bold"),
            fg="#6c7086",
            bg="#181825",
            padx=4,
            pady=0
        )
        self.badge_lbl.pack(side="right")
        
        self.timer_lbl = tk.Label(
            self.header,
            text="",
            font=("Segoe UI", 7),
            fg="#7f849c",
            bg="#11111b",
            padx=3
        )
        self.timer_lbl.pack(side="right")

        # --- Active Action Card ---
        self.card = tk.Frame(self.body, bg="#181825", padx=6, pady=4)
        self.card.pack(fill="x", pady=(4, 3))
        
        self.action_lbl = tk.Label(
            self.card,
            text="Standby",
            font=("Segoe UI", 8, "bold"),
            fg="#a6e3a1",
            bg="#181825",
            anchor="w"
        )
        self.action_lbl.pack(fill="x")
        
        self.detail_lbl = tk.Label(
            self.card,
            text="",
            font=("Segoe UI", 7),
            fg="#7f849c",
            bg="#181825",
            anchor="w"
        )
        self.detail_lbl.pack(fill="x")

        # --- Compact Recent Log (2 items) ---
        self.log_container = tk.Frame(self.body, bg="#11111b")
        self.log_container.pack(fill="both", expand=True, pady=(1, 0))
        
        self.rows = []
        for _ in range(2):
            rf = tk.Frame(self.log_container, bg="#11111b")
            rf.pack(fill="x", pady=0)
            
            t = tk.Label(rf, text="", font=("Consolas", 7), fg="#585b70", bg="#11111b", width=6, anchor="w")
            t.pack(side="left")
            
            m = tk.Label(rf, text="", font=("Segoe UI", 7), fg="#a6adc8", bg="#11111b", anchor="w")
            m.pack(side="left", fill="x", expand=True)
            
            self.rows.append((t, m))
            
        # State tracking
        self.last_mtime = 0
        self.start_time = time.time()
        self.last_activity_time = time.time()
        self.current_state = "idle"
        self.current_action = ""
        self.history = []
        self.spinner_i = 0
        self.closing_scheduled = False
        
        # Apply styles
        self.root.update_idletasks()
        wid = self.root.winfo_id()
        self.hwnd = user32.GetParent(wid) or wid
        user32.SetWindowTextW(self.hwnd, "JarvisHUD_Overlay")
        self.apply_styles()
        
        # Loops
        self.root.after(100, self.poll)
        self.root.after(120, self.heartbeat)

    def apply_styles(self):
        if hasattr(self, 'hwnd') and self.hwnd:
            style = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                self.hwnd,
                GWL_EXSTYLE,
                style | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            )
            user32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE)

    def close_app(self):
        try:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        sys.exit(0)

    def add_history(self, t_str: str, text: str, state: str):
        if not text:
            return
        if self.history and self.history[-1]["text"] == text:
            return
        self.history.append({"time": t_str, "text": text, "state": state})
        if len(self.history) > 6:
            self.history.pop(0)
        
        recent = self.history[-2:]
        for i in range(2):
            t_lbl, m_lbl = self.rows[i]
            if i < len(recent):
                entry = recent[i]
                theme = THEMES.get(entry["state"], THEMES["busy"])
                t_lbl.config(text=entry["time"])
                m_lbl.config(text=f"{theme['icon']} {entry['text'][:28]}", fg=theme["color"])
            else:
                t_lbl.config(text="")
                m_lbl.config(text="")

    def heartbeat(self):
        self.spinner_i = (self.spinner_i + 1) % len(SPINNERS)
        elapsed = int(time.time() - self.start_time)
        
        if self.current_state not in ["idle", "success"]:
            self.timer_lbl.config(text=f"{SPINNERS[self.spinner_i]} {elapsed}s")
        elif self.current_state == "success":
            self.timer_lbl.config(text="✓")
        else:
            self.timer_lbl.config(text="")
            
        # Inactivity watchdog: Auto-close if no updates for INACTIVITY_TIMEOUT seconds
        time_since_activity = time.time() - self.last_activity_time
        if time_since_activity > INACTIVITY_TIMEOUT:
            self.close_app()
            return
            
        self.root.after(150, self.heartbeat)

    def poll(self):
        try:
            if os.path.exists(STATUS_FILE):
                mtime = os.path.getmtime(STATUS_FILE)
                if mtime != self.last_mtime:
                    self.last_mtime = mtime
                    self.last_activity_time = time.time()
                    
                    with open(STATUS_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    # Explicit close requested
                    if data.get("close") is True:
                        self.close_app()
                        return
                    
                    action = data.get("action", "").strip()
                    detail = data.get("detail", "").strip()
                    state = data.get("state", "busy")
                    
                    if data.get("clear"):
                        self.history.clear()
                    
                    now = datetime.now().strftime("%H:%M:%S")
                    
                    if action and action != self.current_action:
                        if self.current_action:
                            self.add_history(now, self.current_action, self.current_state)
                        self.start_time = time.time()
                    
                    self.current_action = action
                    self.current_state = state
                    
                    theme = THEMES.get(state, THEMES["busy"])
                    self.badge_lbl.config(text=theme["badge"], fg=theme["color"])
                    self.border.config(bg=theme["color"])
                    
                    self.action_lbl.config(text=action or "Active", fg=theme["color"])
                    if detail:
                        self.detail_lbl.config(text=detail[:35])
                        self.detail_lbl.pack(fill="x")
                    else:
                        self.detail_lbl.pack_forget()
                    
                    # Auto-close on task completion
                    if state in ["success", "done"]:
                        if not self.closing_scheduled:
                            self.closing_scheduled = True
                            self.root.after(2500, self.close_app)
                    else:
                        self.closing_scheduled = False

        except Exception:
            pass
            
        self.root.after(100, self.poll)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    try:
        app = JarvisMinimalHUD()
        app.run()
    except Exception as e:
        import traceback
        with open(os.path.join(CACHE_DIR, "hud_error.log"), "w", encoding="utf-8") as f:
            traceback.print_exc(file=f)
