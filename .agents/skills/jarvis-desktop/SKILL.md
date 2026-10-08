---
name: jarvis-desktop
description: >-
  Jarvis Desktop Automation Suite: Inspect the screen with OCR & vision, click/type into desktop applications,
  manage windows, launch apps/files, automate Excel, and inspect system vitals on the user's Windows PC.
---

# Jarvis Desktop Automation Skill

This skill equips Antigravity to act as an autonomous Windows Desktop Assistant ("Jarvis") with first-class vision, OCR, input emulation, window management, application orchestration, and Office productivity tools.

## Available Capabilities & MCP Tools (`jarvis-desktop`)

### 1. Screen Vision & Coordinate Navigation
- `take_screenshot(region=None, filename=None)`: Captures the interactive Windows desktop. Inspect the resulting image with `view_file` to visually analyze what is open on screen.
- `take_screenshot_with_grid(step=100, filename=None)`: Captures the screen with an overlay of coordinate rulers and gridlines (default every 100px). Completely eliminates guesswork when estimating pixel coordinates `(x, y)` for graphical icons.
- `screenshot_window(window_query, filename=None)`: Focuses a target window and captures only its exact bounding box, eliminating background desktop clutter.
- `get_cursor_position()`: Returns mouse coordinates `(x, y)` and screen dimensions.

### 2. Hardware-Accelerated OCR & Visual Text Interaction
- `find_text_on_screen(query="", take_new_screenshot=True)`: Uses Windows native Hardware-Accelerated OCR (`winsdk.windows.media.ocr`) to locate text anywhere on screen (including inside Chrome, Electron, Canvas, WhatsApp, and Discord). Returns exact text, bounding box `[x, y, w, h]`, and center coordinates `[cx, cy]`.
- `click_text(text, clicks=1, button="left", match_index=0)`: Searches for text via OCR and clicks its center coordinate directly. No manual pixel coordinate calculation required! Ideal for text buttons, labels, and links (e.g. `'Document'`, `'Leo'`, `'Send'`, `'OK'`, `'Cancel'`, `'Next'`).

### 3. Visual Template Matching (OpenCV)
- `find_image_on_screen(template_path, confidence=0.8)`: Locates an icon, logo, or UI crop on screen using OpenCV template matching (`cv2.matchTemplate`).
- `click_image(template_path, confidence=0.8, clicks=1, button="left")`: Finds an icon template on screen and clicks its center. Ideal for textless graphical icons (e.g. paperclip attachment icon, send arrow, mic, audio buttons).

### 4. Mouse & Touch Emulation
- `mouse_click(x=None, y=None, button="left", clicks=1)`: Moves to coordinates and clicks (or double-clicks).
- `mouse_move(x, y, duration=0.2)`: Moves the mouse cursor.
- `mouse_drag(start_x, start_y, end_x, end_y)`: Drags between coordinates.
- `mouse_scroll(clicks)`: Scrolls wheel up (+) or down (-).

### 5. Keyboard & Input Emulation
- `paste_text(text, press_enter=False)`: **Preferred method for text entry.** Copies text to the Windows clipboard and performs an instantaneous `Ctrl+V` paste into the focused field, optionally pressing Enter. Immune to character dropping, typing latency, keyboard layout mismatch, and autocomplete interference. Ideal for file paths, URLs, long messages, and non-ASCII/Indonesian characters.
- `type_text(text, press_enter=False, delay=0.0)`: Types text character-by-character via keystroke simulation. Useful for interactive command prompts or forms where paste is disabled.
- `press_key(key)`: Presses individual keys (`enter`, `esc`, `tab`, `backspace`, `space`, `up`, `down`, `f5`).
- `hotkey(keys)`: Triggers combinations like `["ctrl", "c"]`, `["ctrl", "v"]`, `["alt", "tab"]`, `["win", "r"]`, `["win", "d"]`.

### 6. Window, Dialog & Task Management
- `list_windows()`: Enumerates all visible windows with title, HWND, PID, and bounding boxes.
- `focus_window(query)`: Brings a target window to foreground and restores if minimized.
- `close_window(query)`: Gracefully closes a window matching title or PID.
- `click_dialog_button(button_name="OK")`: Deterministically clicks standard Win32 dialog buttons (`OK`, `Cancel`, `Yes`, `No`, `Save`, `Open`, `Retry`) in the active modal dialog box without guessing coordinates.

### 7. Office & Excel Productivity Tools
- `get_open_excel_workbooks()`: Enumerates all open Excel workbooks currently running on the PC by inspecting Excel processes, window titles, and file handles.
- `excel_format_borders(file_path, sheet_name=None, range_address=None, border_style="thin")`: Programmatically formats cell borders (All Borders) on any Excel file (`.xlsx`) using `openpyxl`. Guarantees pixel-perfect results without GUI ribbon clicking.

### 8. Application & File Launching
- `open_app_or_file(target, args="")`: Launches desktop applications (`calc`, `notepad`, `spotify`, `code`, `chrome`), opens files in default programs, opens folders, or opens URLs.
- `find_files(query, search_dir)`: Fast filesystem search.

### 9. Semantic UI Automation (Accessibility Tree)
- `get_window_controls(window_title, max_depth=2)`: Retrieves structured buttons, input fields, and lists with names and AutomationIds.
- `click_ui_control(window_title, control_name)`: Clicks a control deterministically by name without guessing pixel coordinates.

### 10. System Vitals & Audio
- `get_system_vitals()`: Real-time CPU, RAM, disk space, active window, and uptime.
- `get_audio_volume()` / `set_audio_volume(level)` / `mute_audio(mute)`: Master volume and mute control.
- `get_recent_notifications(count)`: Retrieves recent Windows toast notifications.

### 11. Live Foreground HUD Log Window (Human Feedback)
- `set_hud_status(action, detail="", state="busy", clear_history=False)`: Updates the persistent floating HUD Log Window at the top-right of the physical monitor.
  - States: `'busy'` (green), `'thinking'` (yellow), `'vision'` (blue), `'input'` (orange), `'excel'` (purple), `'waiting'` (pink), `'success'` (teal), `'error'` (red), `'idle'` (gray).
  - Automatically transitions to `'thinking'` state with live animated heartbeat and elapsed counter when any tool completes, keeping the user informed while the agent reasons.
  - Maintains a scrolling activity log of recent actions with timestamps.
  - Remains persistent and open across thinking/reasoning turns until the task is complete.
- `show_hud(visible=True)`: Shows/resets or hides the HUD Log Window.
- **Key Architectures**:
  - `WS_EX_TRANSPARENT`: Total click-through. Never blocks clicks to underlying apps.
  - `WS_EX_NOACTIVATE`: Never steals window focus from the user or active app.
  - `WS_EX_TOPMOST`: Floats above full-screen browsers, Excel, code editors.
  - `WDA_EXCLUDEFROMCAPTURE`: **100% invisible to screen captures** (`take_screenshot`, `PIL.ImageGrab`, OCR, video recording). Visible *only* to the human user sitting at the physical monitor.
  - Detached Lifecycle: Runs independently on `WinSta0\Default`.

## Best Practices & Workflows

1. **For Text Buttons & Links:** Use `click_text(text)` first. It uses Windows OCR and finds the button in ~0.2s without manual coordinate math.
2. **For Textless Graphical Icons:** If the icon has no text (e.g. paperclip `📎` or paper airplane `➤`), use `take_screenshot_with_grid` to instantly read off the exact coordinate from the grid, or use `click_image(template_path)`.
3. **For Long Text or File Paths:** Always use `paste_text(path, press_enter=True)` instead of `type_text`. Keystroke simulation can get cut off or auto-completed by Windows common dialogs.
4. **For Excel Formatting:** Use `excel_format_borders(...)` programmatically rather than clicking ribbon buttons. It avoids permission prompts, active window conflicts, and ribbon misclicks.
5. **For Modal Popups:** Use `click_dialog_button("OK")` or `press_key("enter")` to clear standard Windows dialog boxes (`#32770`).
6. **For Downloaded Files (Verify Before Proceeding):**
   - **Never rely on transient web UI toasts, browser download popups, or in-browser previewers** (e.g. WhatsApp Web document viewer, Gmail previewer). Clicking in-app previewers frequently traps actions inside the browser DOM and causes accidental printing or forwarding of the web interface instead of the document itself.
   - Inspect the browser configuration or standard download location (e.g. `F:\DOWNLOADS` or Chrome `Preferences`).
   - Always verify the downloaded file physically on disk (confirm file existence, non-zero file size, and header magic bytes `%PDF-` / `PK\x03\x04`).
   - Open or process the physical file directly using native desktop viewers (`open_app_or_file`), headless scripts, or PyMuPDF/openpyxl.
7. **Safe Printing Policy:**
   - Never send print jobs to physical printers by default, in test runs, or without explicit confirmation.
   - However, you are **NOT** gated off from printing: you **CAN and SHOULD** proceed with printing IF the user explicitly asks or instructs you to print.
