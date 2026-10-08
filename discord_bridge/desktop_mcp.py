"""
Jarvis Desktop Control MCP Server & PC Automation Suite
Provides first-class computer use capabilities for Antigravity & Discord Bridge:
- Screen Capture & Vision
- Mouse & Keyboard Input Emulation
- Window Management & Focusing
- UI Automation (Semantic Accessibility Tree)
- Application & File Execution
- System Vitals & Audio Volume
- Clipboard & Notification Management
"""

import os
import sys
import time
import json
import ctypes
import threading
import logging
from typing import List, Optional, Dict, Any

# Configure stderr logging (NEVER log to stdout in MCP servers)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger("jarvis_desktop")

import win32gui
import win32process
import win32con
import win32service
import win32api
from PIL import ImageGrab, Image, ImageDraw, ImageFont
import pyautogui
import psutil
import pyperclip
import asyncio
import cv2
import numpy as np
import openpyxl
from openpyxl.styles import Border, Side
import winsdk.windows.media.ocr as win_ocr
import winsdk.windows.graphics.imaging as win_imaging
import winsdk.windows.storage as win_storage
from pycaw.pycaw import AudioUtilities
import uiautomation as auto

# FastMCP
from fastmcp import FastMCP

# Safety settings for pyautogui
pyautogui.FAILSAFE = False

mcp = FastMCP("jarvis-desktop")

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity", "desktop_cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def run_on_default_desktop(func, *args, **kwargs):
    """
    Executes a callable inside a dedicated worker thread attached to the
    interactive 'Default' Windows desktop on WinSta0.
    Guarantees isolation from sandbox sidecar desktops and avoids Win32 ERROR_BUSY (170).
    """
    res = []
    err = []
    
    def worker():
        co_init = False
        try:
            ctypes.windll.ole32.CoInitialize(None)
            co_init = True
            h_desk = win32service.OpenDesktop("Default", 0, False, win32con.GENERIC_ALL)
            ctypes.windll.user32.SetThreadDesktop(int(h_desk))
            out = func(*args, **kwargs)
            res.append(out)
        except Exception as e:
            err.append(e)
        finally:
            if co_init:
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass
            
    t = threading.Thread(target=worker)
    t.start()
    t.join()
    
    if err:
        raise err[0]
    return res[0] if res else None


# ==========================================
# 0. LIVE HUD OVERLAY SUBSYSTEM
# ==========================================

HUD_STATUS_FILE = os.path.join(CACHE_DIR, "hud_status.json")
HUD_PID_FILE = os.path.join(CACHE_DIR, "hud.pid")
HUD_SCRIPT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hud_overlay.py")

def is_hud_running() -> bool:
    """Checks whether the HUD daemon process is currently running."""
    if os.path.exists(HUD_PID_FILE):
        try:
            with open(HUD_PID_FILE, "r") as f:
                pid = int(f.read().strip())
            if psutil.pid_exists(pid):
                p = psutil.Process(pid)
                if any("hud_overlay" in arg.lower() for arg in p.cmdline()):
                    return True
            else:
                try:
                    os.remove(HUD_PID_FILE)
                except Exception:
                    pass
        except Exception:
            pass
    return False

def ensure_hud_running():
    """Launches the persistent floating HUD daemon silently as a detached process."""
    try:
        if not is_hud_running():
            pythonw_candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            pythonw = pythonw_candidate if os.path.exists(pythonw_candidate) else sys.executable
            import subprocess
            cmd = f'wmic process call create "{pythonw} {HUD_SCRIPT_PATH}"'
            subprocess.run(cmd, shell=True, capture_output=True, timeout=5)
            time.sleep(0.3)
    except Exception as e:
        logger.warning(f"Could not start HUD overlay: {e}")

def update_hud(action: str, detail: str = "", state: str = "busy", clear: bool = False):
    """Updates the floating HUD Log Window with real-time intent, history, and status."""
    try:
        ensure_hud_running()
        payload = {
            "action": action,
            "detail": detail,
            "state": state,
            "clear": clear,
            "timestamp": time.time()
        }
        with open(HUD_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception as e:
        logger.warning(f"Failed to update HUD status: {e}")

# _orig_mcp_tool = mcp.tool
# mcp.tool = _hud_tool_decorator


@mcp.tool()
def set_hud_status(action: str, detail: str = "", state: str = "busy") -> Dict[str, Any]:
    """
    Sets a custom intent/status message on the user's floating HUD Log Window.
    States: 'busy' (green), 'thinking' (yellow), 'vision' (blue), 'input' (orange),
            'excel' (purple), 'waiting' (pink), 'success' (teal), 'error' (red), 'idle' (gray).
    Use this to announce high-level goals or multi-step progress directly to the user.
    """
    clear = (action.strip().lower() in ("clear", "reset"))
    update_hud(action, detail, state, clear=clear)
    return {"status": "ok", "action": action, "detail": detail, "state": state}

@mcp.tool()
def show_hud(visible: bool = True) -> Dict[str, Any]:
    """Toggles or resets the floating HUD Log Window overlay."""
    if visible:
        ensure_hud_running()
        update_hud("Jarvis Desktop Ready", "Floating HUD active", "idle")
        return {"status": "ok", "message": "HUD overlay enabled"}
    else:
        try:
            with open(HUD_STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump({"close": True}, f)
        except Exception:
            pass
        if os.path.exists(HUD_PID_FILE):
            try:
                with open(HUD_PID_FILE, "r") as f:
                    pid = int(f.read().strip())
                if psutil.pid_exists(pid):
                    psutil.Process(pid).terminate()
            except Exception:
                pass
        return {"status": "ok", "message": "HUD overlay closed"}


# ==========================================
# 1. SCREEN VISION & CURSOR
# ==========================================

def _take_screenshot_impl(region, filename):
    update_hud("Capture Screen", "", "vision")
    fname = filename or "desktop_latest.png"
    if not fname.endswith(".png") and not fname.endswith(".jpg"):
        fname += ".png"
    save_path = os.path.join(CACHE_DIR, fname)
    
    bbox = None
    if region and len(region) == 4:
        left, top, width, height = region
        bbox = (left, top, left + width, top + height)
        
    img = ImageGrab.grab(bbox=bbox)
    img.save(save_path)
    
    fg_hwnd = win32gui.GetForegroundWindow()
    fg_title = win32gui.GetWindowText(fg_hwnd) if fg_hwnd else "None"
    
    return {
        "status": "ok",
        "file_path": os.path.abspath(save_path),
        "width": img.width,
        "height": img.height,
        "active_window": fg_title,
        "hint": f"Screenshot saved. Use view_file on '{os.path.abspath(save_path)}' to visually inspect the screen."
    }

@mcp.tool()
def take_screenshot(region: Optional[List[int]] = None, filename: Optional[str] = None) -> Dict[str, Any]:
    """
    Captures a screenshot of the user's interactive desktop.
    
    Args:
        region: Optional [left, top, width, height] coordinates to crop.
        filename: Optional filename to save as. Defaults to 'desktop_latest.png'.
        
    Returns:
        Dictionary with status, saved file path, resolution, and active window title.
        The screenshot file can be viewed using 'view_file' for visual inspection.
    """
    return run_on_default_desktop(_take_screenshot_impl, region, filename)


def _take_screenshot_with_grid_impl(step: int, filename: Optional[str]):
    update_hud("Grid Overlay", f"{step}px", "vision")
    fname = filename or "desktop_grid.png"
    if not fname.endswith(".png") and not fname.endswith(".jpg"):
        fname += ".png"
    save_path = os.path.join(CACHE_DIR, fname)
    
    img = ImageGrab.grab().convert("RGBA")
    overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    
    step = max(20, step)
    w, h = img.width, img.height
    
    # Vertical grid lines
    for x in range(0, w, step):
        draw.line([(x, 0), (x, h)], fill=(255, 0, 0, 70), width=1)
        draw.rectangle([x, 2, x + 35, 16], fill=(0, 0, 0, 180))
        draw.text((x + 2, 2), str(x), fill=(255, 255, 0, 255))
        draw.rectangle([x, h - 18, x + 35, h - 4], fill=(0, 0, 0, 180))
        draw.text((x + 2, h - 18), str(x), fill=(255, 255, 0, 255))
        
    # Horizontal grid lines
    for y in range(0, h, step):
        draw.line([(0, y), (w, y)], fill=(255, 0, 0, 70), width=1)
        draw.rectangle([2, y, 35, y + 14], fill=(0, 0, 0, 180))
        draw.text((4, y), str(y), fill=(255, 255, 0, 255))
        draw.rectangle([w - 38, y, w - 2, y + 14], fill=(0, 0, 0, 180))
        draw.text((w - 36, y), str(y), fill=(255, 255, 0, 255))
        
    res_img = Image.alpha_composite(img, overlay).convert("RGB")
    res_img.save(save_path)
    
    fg_hwnd = win32gui.GetForegroundWindow()
    fg_title = win32gui.GetWindowText(fg_hwnd) if fg_hwnd else "None"
    
    return {
        "status": "ok",
        "file_path": os.path.abspath(save_path),
        "width": w,
        "height": h,
        "grid_step": step,
        "active_window": fg_title,
        "hint": f"Grid screenshot saved. Use view_file on '{os.path.abspath(save_path)}' to visually locate pixel coordinates."
    }

@mcp.tool()
def take_screenshot_with_grid(step: int = 100, filename: Optional[str] = None) -> Dict[str, Any]:
    """
    Captures a desktop screenshot with a labeled coordinate grid overlay (rulers & crosshairs).
    Allows instant, zero-guesswork visual reading of exact pixel coordinates when inspecting with view_file.
    
    Args:
        step: Grid line spacing in pixels (e.g. 50 or 100). Default is 100.
        filename: Optional filename to save as. Defaults to 'desktop_grid.png'.
    """
    return run_on_default_desktop(_take_screenshot_with_grid_impl, step, filename)


def _run_ocr_on_image_path(img_path: str, query: str = "") -> List[Dict[str, Any]]:
    async def _async_ocr():
        file = await win_storage.StorageFile.get_file_from_path_async(os.path.abspath(img_path))
        stream = await file.open_async(win_storage.FileAccessMode.READ)
        decoder = await win_imaging.BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        engine = win_ocr.OcrEngine.try_create_from_user_profile_languages()
        res = await engine.recognize_async(bitmap)
        
        results = []
        q = query.lower().strip()
        for line in res.lines:
            text = line.text.strip()
            if not q or q in text.lower():
                min_x = min(w.bounding_rect.x for w in line.words)
                min_y = min(w.bounding_rect.y for w in line.words)
                max_r = max(w.bounding_rect.x + w.bounding_rect.width for w in line.words)
                max_b = max(w.bounding_rect.y + w.bounding_rect.height for w in line.words)
                results.append({
                    "text": text,
                    "center": [round((min_x + max_r) / 2), round((min_y + max_b) / 2)],
                    "bounds": [round(min_x), round(min_y), round(max_r - min_x), round(max_b - min_y)]
                })
        return results
        
    res_box = []
    err_box = []
    def _worker():
        try:
            res_box.append(asyncio.run(_async_ocr()))
        except Exception as e:
            err_box.append(e)

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    if err_box:
        raise err_box[0]
    return res_box[0] if res_box else []


def _find_text_on_screen_impl(query: str, take_new_screenshot: bool):
    update_hud("OCR Search", query[:20] if query else "Full display", "vision")
    img_path = os.path.join(CACHE_DIR, "desktop_latest.png")
    if take_new_screenshot or not os.path.exists(img_path):
        _take_screenshot_impl(None, "desktop_latest.png")
    return _run_ocr_on_image_path(img_path, query)

@mcp.tool()
def find_text_on_screen(query: str = "", take_new_screenshot: bool = True) -> List[Dict[str, Any]]:
    """
    Uses Windows native Hardware-Accelerated OCR to detect and locate text labels on the screen.
    Returns matched text, bounding box [x, y, width, height], and center coordinate [cx, cy] for each hit.
    
    Args:
        query: Substring or phrase to search for (case-insensitive). If empty, returns all text found on screen.
        take_new_screenshot: If True, captures fresh screenshot before OCR. If False, runs OCR on latest cached screenshot.
    """
    return run_on_default_desktop(_find_text_on_screen_impl, query, take_new_screenshot)

def _click_text_impl(text: str, clicks: int, button: str, match_index: int):
    update_hud("Click Text", text[:20], "input")
    matches = _find_text_on_screen_impl(text, take_new_screenshot=True)
    if not matches:
        return {"status": "error", "message": f"Text '{text}' not found on screen via OCR"}
    if match_index >= len(matches):
        match_index = len(matches) - 1
    target = matches[match_index]
    cx, cy = target["center"]
    pyautogui.click(cx, cy, clicks=clicks, button=button)
    return {
        "status": "ok",
        "action": f"{button} clicked {clicks}x on '{target['text']}'",
        "position": [cx, cy],
        "bounds": target["bounds"],
        "total_matches": len(matches)
    }

@mcp.tool()
def click_text(text: str, clicks: int = 1, button: str = "left", match_index: int = 0) -> Dict[str, Any]:
    """
    Finds text on the screen using OCR and clicks its center coordinate.
    Eliminates manual coordinate guessing for text buttons, labels, and links (e.g. 'Document', 'Leo', 'Send', 'OK', 'Cancel').
    
    Args:
        text: Text to locate and click.
        clicks: 1 for single click, 2 for double click.
        button: 'left', 'right', or 'middle'.
        match_index: If multiple occurrences of text exist, which match to click (0 for first/topmost).
    """
    return run_on_default_desktop(_click_text_impl, text, clicks, button, match_index)


def _find_image_on_screen_impl(template_path: str, confidence: float):
    update_hud("Matching image template", os.path.basename(template_path), "vision")
    if not os.path.exists(template_path):
        return {"status": "error", "message": f"Template image '{template_path}' not found"}
    
    img_screen = ImageGrab.grab().convert("RGB")
    screen_np = np.array(img_screen)
    screen_bgr = cv2.cvtColor(screen_np, cv2.COLOR_RGB2BGR)
    
    template_bgr = cv2.imread(template_path)
    if template_bgr is None:
        return {"status": "error", "message": f"Could not read template '{template_path}'"}
        
    th, tw = template_bgr.shape[:2]
    res = cv2.matchTemplate(screen_bgr, template_bgr, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
    
    if max_val < confidence:
        return {
            "status": "not_found",
            "message": f"Template match confidence {round(float(max_val), 3)} is below threshold {confidence}",
            "best_confidence": round(float(max_val), 3)
        }
        
    cx = max_loc[0] + tw // 2
    cy = max_loc[1] + th // 2
    return {
        "status": "ok",
        "confidence": round(float(max_val), 3),
        "bounds": [max_loc[0], max_loc[1], tw, th],
        "center": [cx, cy]
    }

@mcp.tool()
def find_image_on_screen(template_path: str, confidence: float = 0.8) -> Dict[str, Any]:
    """
    Locates an image template or icon on the interactive desktop using OpenCV template matching.
    
    Args:
        template_path: Absolute path to the template image file.
        confidence: Matching threshold from 0.0 to 1.0 (default 0.8).
    """
    return run_on_default_desktop(_find_image_on_screen_impl, template_path, confidence)

def _click_image_impl(template_path: str, confidence: float, clicks: int, button: str):
    update_hud("Clicking template match", f"{os.path.basename(template_path)} • {clicks}x", "input")
    match = _find_image_on_screen_impl(template_path, confidence)
    if match.get("status") != "ok":
        return match
    cx, cy = match["center"]
    pyautogui.click(cx, cy, clicks=clicks, button=button)
    return {
        "status": "ok",
        "action": f"{button} clicked {clicks}x on template",
        "position": [cx, cy],
        "confidence": match["confidence"],
        "bounds": match["bounds"]
    }

@mcp.tool()
def click_image(template_path: str, confidence: float = 0.8, clicks: int = 1, button: str = "left") -> Dict[str, Any]:
    """
    Locates an image template/icon on screen using OpenCV and clicks its center.
    Ideal for graphical icons without text (e.g. paperclip attachment icon, send arrow, logos).
    """
    return run_on_default_desktop(_click_image_impl, template_path, confidence, clicks, button)


def _get_cursor_position_impl():
    pos = pyautogui.position()
    size = pyautogui.size()
    return {
        "cursor_x": pos.x,
        "cursor_y": pos.y,
        "screen_width": size.width,
        "screen_height": size.height
    }

@mcp.tool()
def get_cursor_position() -> Dict[str, Any]:
    """Returns the current mouse cursor (x, y) position and desktop resolution."""
    return run_on_default_desktop(_get_cursor_position_impl)


# ==========================================
# 2. MOUSE & TOUCH CONTROLS
# ==========================================

def _mouse_click_impl(x, y, button, clicks):
    pos_str = f"({x}, {y})" if (x is not None and y is not None) else ""
    update_hud("Click", f"{button} {pos_str}".strip(), "input")
    if x is not None and y is not None:
        pyautogui.moveTo(x, y)
    pyautogui.click(button=button, clicks=clicks)
    curr = pyautogui.position()
    return {"status": "ok", "action": f"{button} clicked {clicks}x", "position": [curr.x, curr.y]}

@mcp.tool()
def mouse_click(x: Optional[int] = None, y: Optional[int] = None, button: str = "left", clicks: int = 1) -> Dict[str, Any]:
    """
    Clicks the mouse at the given (x, y) coordinates or current position.
    
    Args:
        x: X-coordinate (optional).
        y: Y-coordinate (optional).
        button: 'left', 'right', or 'middle'.
        clicks: 1 for single click, 2 for double click.
    """
    return run_on_default_desktop(_mouse_click_impl, x, y, button, clicks)


def _mouse_move_impl(x, y, duration):
    update_hud("Move Cursor", f"{x}, {y}", "input")
    pyautogui.moveTo(x, y, duration=duration)
    return {"status": "ok", "position": [x, y]}

@mcp.tool()
def mouse_move(x: int, y: int, duration: float = 0.2) -> Dict[str, Any]:
    """Moves the mouse smoothly to (x, y)."""
    return run_on_default_desktop(_mouse_move_impl, x, y, duration)


def _mouse_drag_impl(start_x, start_y, end_x, end_y, duration):
    update_hud("Drag Cursor", f"({start_x},{start_y})", "input")
    pyautogui.moveTo(start_x, start_y)
    pyautogui.dragTo(end_x, end_y, duration=duration, button="left")
    return {"status": "ok", "dragged_from": [start_x, start_y], "to": [end_x, end_y]}

@mcp.tool()
def mouse_drag(start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.5) -> Dict[str, Any]:
    """Drags the mouse from (start_x, start_y) to (end_x, end_y)."""
    return run_on_default_desktop(_mouse_drag_impl, start_x, start_y, end_x, end_y, duration)


def _mouse_scroll_impl(clicks):
    direction = "UP" if clicks > 0 else "DOWN"
    update_hud("Scroll", f"{direction} {abs(clicks)}", "input")
    pyautogui.scroll(clicks)
    return {"status": "ok", "scrolled": clicks}

@mcp.tool()
def mouse_scroll(clicks: int) -> Dict[str, Any]:
    """
    Scrolls the mouse wheel.
    Positive value scrolls UP, negative value scrolls DOWN.
    """
    return run_on_default_desktop(_mouse_scroll_impl, clicks)


# ==========================================
# 3. KEYBOARD & INPUT
# ==========================================

def _type_text_impl(text, press_enter, delay):
    preview = (text[:20] + '..') if len(text) > 20 else text
    update_hud("Type", f'"{preview}"', "input")
    pyautogui.write(text, interval=delay)
    if press_enter:
        pyautogui.press("enter")
    return {"status": "ok", "typed_length": len(text), "pressed_enter": press_enter}

@mcp.tool()
def type_text(text: str, press_enter: bool = False, delay: float = 0.0) -> Dict[str, Any]:
    """
    Types text into the currently focused window/input field.
    
    Args:
        text: Text to type.
        press_enter: If True, presses Enter key after typing.
        delay: Delay in seconds between keystrokes.
    """
    return run_on_default_desktop(_type_text_impl, text, press_enter, delay)


def _paste_text_impl(text: str, press_enter: bool):
    preview = (text[:20] + '..') if len(text) > 20 else text
    update_hud("Paste", f'"{preview}"', "input")
    pyperclip.copy(text)
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")
    if press_enter:
        time.sleep(0.05)
        pyautogui.press("enter")
    return {"status": "ok", "pasted_length": len(text), "pressed_enter": press_enter}

@mcp.tool()
def paste_text(text: str, press_enter: bool = False) -> Dict[str, Any]:
    """
    Safely and instantaneously types text by copying to clipboard and triggering Ctrl+V paste.
    Immune to character dropping, typing latency, keyboard layout mismatch, and autocomplete interference.
    Ideal for file paths, URLs, long messages, and non-ASCII/Indonesian characters.
    
    Args:
        text: Text to paste.
        press_enter: If True, presses Enter key immediately after pasting.
    """
    return run_on_default_desktop(_paste_text_impl, text, press_enter)


def _press_key_impl(key):
    update_hud("Key", key, "input")
    pyautogui.press(key.lower())
    return {"status": "ok", "key_pressed": key}

@mcp.tool()
def press_key(key: str) -> Dict[str, Any]:
    """
    Presses a specific key (e.g. 'enter', 'esc', 'tab', 'backspace', 'space', 'up', 'down', 'f5').
    """
    return run_on_default_desktop(_press_key_impl, key)


def _hotkey_impl(keys):
    norm_keys = [k.lower() for k in keys]
    update_hud("Hotkey", "+".join(norm_keys), "input")
    norm_keys = [k.lower() for k in keys]
    pyautogui.hotkey(*norm_keys)
    return {"status": "ok", "hotkey_executed": "+".join(norm_keys)}

@mcp.tool()
def hotkey(keys: List[str]) -> Dict[str, Any]:
    """
    Presses a key combination shortcut simultaneously (e.g. ['ctrl', 'c'], ['alt', 'tab'], ['win', 'r'], ['ctrl', 'shift', 'esc']).
    """
    return run_on_default_desktop(_hotkey_impl, keys)


# ==========================================
# 4. CLIPBOARD
# ==========================================

@mcp.tool()
def clipboard_get() -> Dict[str, Any]:
    """Returns the text currently stored in the Windows clipboard."""
    text = pyperclip.paste()
    return {"status": "ok", "clipboard_text": text}


@mcp.tool()
def clipboard_set(text: str) -> Dict[str, Any]:
    """Sets the text of the Windows clipboard."""
    pyperclip.copy(text)
    return {"status": "ok", "message": f"Clipboard updated ({len(text)} characters)"}


# ==========================================
# 5. WINDOW MANAGEMENT
# ==========================================

def _list_windows_impl():
    h_desk = win32service.OpenDesktop("Default", 0, False, win32con.GENERIC_ALL)
    fg_hwnd = win32gui.GetForegroundWindow()
    windows = []
    
    def enum_cb(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).strip()
            if title:
                rect = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                if w > 0 and h > 0:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    pname = ""
                    try:
                        pname = psutil.Process(pid).name()
                    except Exception:
                        pass
                    windows.append({
                        "hwnd": hwnd,
                        "title": title,
                        "process_name": pname,
                        "pid": pid,
                        "bounds": [rect[0], rect[1], w, h],
                        "is_active": (hwnd == fg_hwnd)
                    })
                    
    win32gui.EnumDesktopWindows(h_desk, enum_cb, None)
    return windows

@mcp.tool()
def list_windows() -> List[Dict[str, Any]]:
    """
    Enumerates all visible, titled application windows on the interactive desktop.
    Returns title, HWND, process name, PID, and bounding box for each window.
    """
    return run_on_default_desktop(_list_windows_impl)


def force_foreground_window(hwnd: int) -> bool:
    """
    Forcibly brings a target window to the foreground, bypassing Windows
    ForegroundLockTimeout which normally causes background processes to only flash
    the taskbar icon.
    Uses the Win32 Alt-key event + AttachThreadInput + SwitchToThisWindow technique.
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    
    # 1. Restore window if minimized
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    else:
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

    fg_hwnd = user32.GetForegroundWindow()
    if fg_hwnd == hwnd:
        return True
        
    fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None)
    cur_thread = kernel32.GetCurrentThreadId()

    # 2. Simulate Alt key down/up (resets Windows foreground lock)
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)

    # 3. Attach thread input to foreground thread to gain focus authority
    attached = False
    if fg_thread != cur_thread and fg_thread != 0:
        attached = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))

    try:
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        user32.SwitchToThisWindow(hwnd, True)
    finally:
        if attached:
            user32.AttachThreadInput(cur_thread, fg_thread, False)

    return user32.GetForegroundWindow() == hwnd


def _focus_window_impl(query):
    update_hud("Focus", query[:20], "busy")
    h_desk = win32service.OpenDesktop("Default", 0, False, win32con.GENERIC_ALL)
    matched_hwnd = None
    matched_title = ""
    
    q_lower = query.lower()
    def enum_cb(hwnd, extra):
        nonlocal matched_hwnd, matched_title
        if matched_hwnd:
            return
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).strip()
            if title and q_lower in title.lower():
                matched_hwnd = hwnd
                matched_title = title
            elif query.isdigit():
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid == int(query):
                    matched_hwnd = hwnd
                    matched_title = title

    win32gui.EnumDesktopWindows(h_desk, enum_cb, None)
    if not matched_hwnd:
        return {"status": "error", "message": f"No visible window found matching '{query}'"}
        
    success = force_foreground_window(matched_hwnd)
    return {
        "status": "ok" if success else "warning",
        "hwnd": matched_hwnd,
        "title": matched_title,
        "message": f"Window '{matched_title}' brought to foreground"
    }

@mcp.tool()
def focus_window(query: str) -> Dict[str, Any]:
    """
    Brings a window matching the title query (case-insensitive substring) or PID to the foreground.
    Restores the window if it was minimized.
    """
    return run_on_default_desktop(_focus_window_impl, query)


def _close_window_impl(query):
    update_hud(f"Closing window: '{query}'", "Sending WM_CLOSE signal", "busy")
    h_desk = win32service.OpenDesktop("Default", 0, False, win32con.GENERIC_ALL)
    matched_hwnd = None
    matched_title = ""
    
    q_lower = query.lower()
    def enum_cb(hwnd, extra):
        nonlocal matched_hwnd, matched_title
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).strip()
            if title and q_lower in title.lower():
                matched_hwnd = hwnd
                matched_title = title
    win32gui.EnumDesktopWindows(h_desk, enum_cb, None)
    
    if not matched_hwnd:
        return {"status": "error", "message": f"No window found matching '{query}'"}
        
    win32gui.PostMessage(matched_hwnd, win32con.WM_CLOSE, 0, 0)
    return {"status": "ok", "message": f"Sent close signal to '{matched_title}'"}

@mcp.tool()
def close_window(query: str) -> Dict[str, Any]:
    """Gracefully closes a window matching the title query or PID."""
    return run_on_default_desktop(_close_window_impl, query)


def _screenshot_window_impl(window_query, filename):
    update_hud(f"Capturing window: '{window_query}'", "Cropping to window bounds", "vision")
    focus_res = _focus_window_impl(window_query)
    if focus_res.get("status") == "error":
        return focus_res
    hwnd = focus_res["hwnd"]
    time.sleep(0.15)
    rect = win32gui.GetWindowRect(hwnd)
    w = max(1, rect[2] - rect[0])
    h = max(1, rect[3] - rect[1])
    fname = filename or f"{window_query}_window.png"
    return _take_screenshot_impl([rect[0], rect[1], w, h], fname)

@mcp.tool()
def screenshot_window(window_query: str, filename: Optional[str] = None) -> Dict[str, Any]:
    """
    Brings the target application window to the foreground and takes a screenshot cropped exactly to its bounds.
    Eliminates background desktop clutter.
    """
    return run_on_default_desktop(_screenshot_window_impl, window_query, filename)


def _click_dialog_button_impl(button_name: str):
    update_hud(f"Clicking dialog button: '{button_name}'", "Enumerating dialog controls", "input")
    fg_hwnd = win32gui.GetForegroundWindow()
    if not fg_hwnd:
        return {"status": "error", "message": "No active foreground window found"}
        
    target_btn = None
    target_name = button_name.lower().strip()
    
    def enum_cb(h, _):
        nonlocal target_btn
        if target_btn:
            return
        cls = win32gui.GetClassName(h).lower()
        if "button" in cls:
            txt = win32gui.GetWindowText(h).strip().lower()
            if target_name in txt or (target_name == "ok" and txt == "ok"):
                target_btn = h
                
    win32gui.EnumChildWindows(fg_hwnd, enum_cb, None)
    if not target_btn:
        return {"status": "error", "message": f"Button '{button_name}' not found in active dialog '{win32gui.GetWindowText(fg_hwnd)}'"}
        
    rect = win32gui.GetWindowRect(target_btn)
    cx = (rect[0] + rect[2]) // 2
    cy = (rect[1] + rect[3]) // 2
    
    try:
        win32gui.SendMessage(target_btn, win32con.BM_CLICK, 0, 0)
    except Exception:
        pyautogui.click(cx, cy)
        
    return {
        "status": "ok",
        "message": f"Clicked dialog button '{win32gui.GetWindowText(target_btn)}'",
        "position": [cx, cy]
    }

@mcp.tool()
def click_dialog_button(button_name: str = "OK") -> Dict[str, Any]:
    """
    Deterministically clicks a standard button (e.g. 'OK', 'Cancel', 'Yes', 'No', 'Save', 'Open')
    in the active modal or dialog window.
    """
    return run_on_default_desktop(_click_dialog_button_impl, button_name)


# ==========================================
# 6. APP & FILE LAUNCHER
# ==========================================

def _open_app_or_file_impl(target, args):
    name = os.path.basename(target) if ("\\" in target or "/" in target) else target
    update_hud(f"Launching: {name}", f"Args: '{args}'" if args else "Default shell handler", "busy")
    try:
        if args:
            ret = win32api.ShellExecute(0, "open", target, args, "", win32con.SW_SHOWNORMAL)
        else:
            ret = win32api.ShellExecute(0, "open", target, "", "", win32con.SW_SHOWNORMAL)
            
        if ret > 32:
            return {"status": "ok", "message": f"Launched '{target}' successfully"}
        else:
            os.startfile(target)
            return {"status": "ok", "message": f"Opened '{target}' via os.startfile"}
    except Exception as e:
        return {"status": "error", "message": f"Failed to open '{target}': {str(e)}"}

@mcp.tool()
def open_app_or_file(target: str, args: str = "") -> Dict[str, Any]:
    """
    Opens any application, file, directory, or URL in Windows default handler via ShellExecute.
    
    Examples:
        - Apps: 'calc', 'notepad', 'code', 'spotify', 'chrome'
        - Files: 'C:\\Users\\...\\file.pdf', 'D:\\data.xlsx'
        - Folders: 'C:\\Users\\...\\Downloads'
        - URLs: 'https://github.com'
    """
    return run_on_default_desktop(_open_app_or_file_impl, target, args)


@mcp.tool()
def find_files(query: str, search_dir: Optional[str] = None, max_results: int = 20) -> List[str]:
    """Fast search for files matching query (supports wildcards or substrings) in a directory tree."""
    target_dir = search_dir or os.path.expanduser("~")
    update_hud(f"Searching files: '{query}'", f"Directory: {target_dir}", "busy")
    results = []
    q_lower = query.lower()
    try:
        for root, dirs, files in os.walk(target_dir):
            for f in files:
                if q_lower in f.lower():
                    results.append(os.path.join(root, f))
                    if len(results) >= max_results:
                        return results
    except Exception as e:
        logger.warning(f"File search warning: {e}")
    return results


# ==========================================
# 7. SEMANTIC UI AUTOMATION (UIA Tree)
# ==========================================

def _get_window_controls_impl(window_title, max_depth):
    update_hud(f"Inspecting UI tree: '{window_title}'", f"Max depth: {max_depth}", "busy")
    h_desk = win32service.OpenDesktop("Default", 0, False, win32con.GENERIC_ALL)
    matched_hwnd = None
    
    q_lower = window_title.lower()
    def enum_cb(hwnd, extra):
        nonlocal matched_hwnd
        if matched_hwnd:
            return
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).strip()
            if title and q_lower in title.lower():
                matched_hwnd = hwnd
    win32gui.EnumDesktopWindows(h_desk, enum_cb, None)
    
    if not matched_hwnd:
        return [{"status": "error", "message": f"Window '{window_title}' not found"}]
        
    ctrl = auto.ControlFromHandle(matched_hwnd)
    elements = []
    
    def walk(node, depth):
        if depth > max_depth or len(elements) >= 50:
            return
        for child in node.GetChildren():
            try:
                rect = child.BoundingRectangle
                name = child.Name or ""
                ctype = child.ControlTypeName or ""
                auto_id = child.AutomationId or ""
                if name or auto_id:
                    elements.append({
                        "name": name,
                        "type": ctype,
                        "automation_id": auto_id,
                        "bounds": [rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top] if rect else []
                    })
                walk(child, depth + 1)
            except Exception:
                pass

    walk(ctrl, 1)
    return elements

@mcp.tool()
def get_window_controls(window_title: str, max_depth: int = 2) -> List[Dict[str, Any]]:
    """
    Inspects interactive UI controls (buttons, input boxes, check boxes, tabs) inside a window
    using Windows UI Automation (Accessibility Tree).
    """
    return run_on_default_desktop(_get_window_controls_impl, window_title, max_depth)


def _click_ui_control_impl(window_title, control_name, control_type):
    update_hud(f"Clicking UI control: '{control_name}'", f"Window: '{window_title}'", "input")
    h_desk = win32service.OpenDesktop("Default", 0, False, win32con.GENERIC_ALL)
    matched_hwnd = None
    
    q_lower = window_title.lower()
    def enum_cb(hwnd, extra):
        nonlocal matched_hwnd
        if matched_hwnd:
            return
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).strip()
            if title and q_lower in title.lower():
                matched_hwnd = hwnd
    win32gui.EnumDesktopWindows(h_desk, enum_cb, None)
    
    if not matched_hwnd:
        return {"status": "error", "message": f"Window '{window_title}' not found"}
        
    ctrl = auto.ControlFromHandle(matched_hwnd)
    target_lower = control_name.lower()
    ctype_lower = control_type.lower()
    found = None
    
    def search(node, depth):
        nonlocal found
        if found or depth > 4:
            return
        for child in node.GetChildren():
            try:
                cname = (child.Name or "").lower()
                ctype = (child.ControlTypeName or "").lower()
                if target_lower in cname:
                    if not ctype_lower or ctype_lower in ctype:
                        found = child
                        return
                search(child, depth + 1)
            except Exception:
                pass

    search(ctrl, 1)
    if not found:
        return {"status": "error", "message": f"Control '{control_name}' not found in '{window_title}'"}
        
    try:
        found.Click()
        return {"status": "ok", "message": f"Clicked control '{found.Name}' ({found.ControlTypeName})"}
    except Exception:
        rect = found.BoundingRectangle
        if rect:
            cx = (rect.left + rect.right) // 2
            cy = (rect.top + rect.bottom) // 2
            pyautogui.click(cx, cy)
            return {"status": "ok", "message": f"Clicked via coordinates ({cx}, {cy}) for control '{found.Name}'"}
        return {"status": "error", "message": "Failed to invoke click on control"}

@mcp.tool()
def click_ui_control(window_title: str, control_name: str, control_type: str = "") -> Dict[str, Any]:
    """
    Deterministically clicks a specific named UI element inside a window by searching its Accessibility Tree.
    """
    return run_on_default_desktop(_click_ui_control_impl, window_title, control_name, control_type)


# ==========================================
# 8. SYSTEM VITALS & AUDIO
# ==========================================

def _get_system_vitals_impl():
    cpu = psutil.cpu_percent(interval=0.1)
    ram = psutil.virtual_memory()
    uptime_hrs = round((time.time() - psutil.boot_time()) / 3600, 1)
    
    disks = {}
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks[part.device] = {
                "total_gb": round(usage.total / (1024**3), 1),
                "free_gb": round(usage.free / (1024**3), 1),
                "percent_used": usage.percent
            }
        except Exception:
            pass
            
    fg_hwnd = win32gui.GetForegroundWindow()
    fg_title = win32gui.GetWindowText(fg_hwnd) if fg_hwnd else "None"
    
    return {
        "cpu_usage_percent": cpu,
        "ram_usage_percent": ram.percent,
        "ram_used_gb": round((ram.total - ram.available) / (1024**3), 1),
        "ram_total_gb": round(ram.total / (1024**3), 1),
        "uptime_hours": uptime_hrs,
        "disks": disks,
        "active_window": fg_title
    }

@mcp.tool()
def get_system_vitals() -> Dict[str, Any]:
    """
    Returns real-time health metrics of the PC: CPU load, RAM usage, disk space,
    battery status, system uptime, and currently focused window.
    """
    return run_on_default_desktop(_get_system_vitals_impl)


def _get_audio_volume_impl():
    devices = AudioUtilities.GetSpeakers()
    vol = devices.EndpointVolume
    scalar = vol.GetMasterVolumeLevelScalar()
    return {
        "master_volume": round(scalar * 100),
        "is_muted": bool(vol.GetMute())
    }

@mcp.tool()
def get_audio_volume() -> Dict[str, Any]:
    """Returns current master speaker volume level (0-100) and mute state."""
    return run_on_default_desktop(_get_audio_volume_impl)


def _set_audio_volume_impl(level):
    level = max(0, min(100, level))
    update_hud("Setting master volume", f"Target level: {level}%", "busy")
    devices = AudioUtilities.GetSpeakers()
    vol = devices.EndpointVolume
    vol.SetMasterVolumeLevelScalar(level / 100.0, None)
    return {"status": "ok", "new_volume": level}

@mcp.tool()
def set_audio_volume(level: int) -> Dict[str, Any]:
    """Sets master speaker volume level from 0 to 100."""
    return run_on_default_desktop(_set_audio_volume_impl, level)


def _mute_audio_impl(mute):
    update_hud("Toggling master audio mute", f"Target mute: {mute}", "busy")
    devices = AudioUtilities.GetSpeakers()
    vol = devices.EndpointVolume
    curr_mute = bool(vol.GetMute())
    new_mute = (not curr_mute) if mute is None else mute
    vol.SetMute(int(new_mute), None)
    return {"status": "ok", "is_muted": new_mute}

@mcp.tool()
def mute_audio(mute: Optional[bool] = None) -> Dict[str, Any]:
    """Mutes, unmutes, or toggles master audio if mute is not specified."""
    return run_on_default_desktop(_mute_audio_impl, mute)


# ==========================================
# 9. WINDOWS NOTIFICATIONS
# ==========================================

@mcp.tool()
def get_recent_notifications(count: int = 5) -> List[Dict[str, Any]]:
    """
    Retrieves the latest Windows toast notifications from the Action Center.
    Requires Windows Runtime UserNotificationListener.
    """
    update_hud("Reading Windows notifications", "Action Center query", "busy")
    try:
        import asyncio
        import winsdk.windows.ui.notifications.management as mgmt
        import winsdk.windows.ui.notifications as notif
        
        async def _fetch():
            listener = mgmt.UserNotificationListener.current
            access = await listener.request_access_async()
            if access != mgmt.UserNotificationListenerAccessStatus.ALLOWED:
                return [{"error": "Notification access not allowed"}]
            items = await listener.get_notifications_async(notif.NotificationKinds.TOAST)
            res = []
            for n in items[-count:]:
                app = n.app_info.display_info.display_name if n.app_info else "Unknown"
                binding = n.notification.visual.get_binding(notif.KnownNotificationBindings.toast_generic)
                texts = [el.text for el in binding.get_text_elements()] if binding else []
                res.append({
                    "id": n.id,
                    "app": app,
                    "texts": texts,
                    "time": str(n.creation_time)
                })
            return res

        res_box = []
        err_box = []
        def _worker():
            try:
                res_box.append(asyncio.run(_fetch()))
            except Exception as e:
                err_box.append(e)

        t = threading.Thread(target=_worker)
        t.start()
        t.join()
        if err_box:
            raise err_box[0]
        return res_box[0] if res_box else []
    except Exception as e:
        return [{"error": f"Failed to fetch notifications: {str(e)}"}]


# ==========================================
# 10. OFFICE & PRODUCTIVITY AUTOMATION
# ==========================================

def _excel_format_borders_impl(file_path: str, sheet_name: Optional[str], range_address: Optional[str], border_style: str):
    fname = os.path.basename(file_path)
    update_hud("Formatting Excel borders", f"{fname} • {sheet_name or 'active'}", "excel")
    if not os.path.exists(file_path):
        return {"status": "error", "message": f"File '{file_path}' not found"}
        
    try:
        wb = openpyxl.load_workbook(file_path)
        if sheet_name:
            if sheet_name not in wb.sheetnames:
                return {"status": "error", "message": f"Sheet '{sheet_name}' not found. Available: {wb.sheetnames}"}
            ws = wb[sheet_name]
        else:
            ws = wb.active
            
        side = Side(style=border_style, color="000000")
        border = Border(left=side, right=side, top=side, bottom=side)
        
        if range_address:
            cells = ws[range_address]
        else:
            min_r, max_r = ws.min_row, ws.max_row
            min_c, max_c = ws.min_column, ws.max_column
            cells = ws.iter_rows(min_row=min_r, max_row=max_r, min_col=min_c, max_col=max_c)
            
        count = 0
        for row in cells:
            if isinstance(row, tuple):
                for cell in row:
                    cell.border = border
                    count += 1
            else:
                row.border = border
                count += 1
                
        wb.save(file_path)
        return {
            "status": "ok",
            "message": f"Successfully applied '{border_style}' borders to {count} cells in sheet '{ws.title}'",
            "file_path": file_path,
            "sheet": ws.title
        }
    except Exception as e:
        return {"status": "error", "message": f"Excel format error: {str(e)}"}

@mcp.tool()
def excel_format_borders(file_path: str, sheet_name: Optional[str] = None, range_address: Optional[str] = None, border_style: str = "thin") -> Dict[str, Any]:
    """
    Programmatically applies table borders (All Borders) to an Excel worksheet using openpyxl.
    Guarantees reliable, instant formatting without manual GUI ribbon clicking.
    
    Args:
        file_path: Absolute path to the .xlsx file.
        sheet_name: Name of worksheet (defaults to active sheet).
        range_address: Cell range e.g. 'A1:H20'. If omitted, formats the entire used data extent.
        border_style: 'thin', 'medium', 'double', or 'thick'.
    """
    return _excel_format_borders_impl(file_path, sheet_name, range_address, border_style)


def _get_open_excel_workbooks_impl():
    update_hud("Scanning open Excel workbooks", "Checking processes & open handles", "excel")
    results = []
    seen = set()
    
    h_desk = win32service.OpenDesktop("Default", 0, False, win32con.GENERIC_ALL)
    def enum_cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).strip()
            if "excel" in title.lower() or title.lower().endswith((".xlsx", ".xls")):
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if title not in seen:
                    seen.add(title)
                    results.append({"type": "window", "title": title, "hwnd": hwnd, "pid": pid})
    win32gui.EnumDesktopWindows(h_desk, enum_cb, None)
    
    for p in psutil.process_iter(["pid", "name"]):
        if p.info["name"] and "excel" in p.info["name"].lower():
            try:
                for f in p.open_files():
                    if f.path.lower().endswith((".xlsx", ".xls", ".xlsm", ".csv")):
                        if f.path not in seen:
                            seen.add(f.path)
                            results.append({"type": "open_file", "path": f.path, "pid": p.info["pid"]})
            except Exception:
                pass
                
    return results

@mcp.tool()
def get_open_excel_workbooks() -> List[Dict[str, Any]]:
    """
    Enumerates all open Excel workbooks currently running on the system by inspecting Excel processes,
    window titles, and open file handles.
    """
    return run_on_default_desktop(_get_open_excel_workbooks_impl)


if __name__ == "__main__":
    # Run the FastMCP server over standard I/O for Antigravity integration
    mcp.run()
