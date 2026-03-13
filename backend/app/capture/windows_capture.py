# windows_capture.py
# Adapted from the uploaded window-capture.py for the Rewind Markdown app
#
# Requires:
#   pip install pywin32 pillow

import os
import re
import ctypes

import win32con
import win32gui
import win32process
import win32ui
from PIL import Image

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
DWMWA_CLOAKED = 14

EXCLUDED_WINDOW_CLASSES = {
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "NotifyIconOverflowWindow",
    "Windows.UI.Core.CoreWindow",
}

# Must be called very early in process startup.
user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
PW_RENDERFULLCONTENT = 0x00000002

def get_window_text(hwnd: int) -> str:
    return win32gui.GetWindowText(hwnd).strip()

def get_window_class(hwnd: int) -> str:
    try:
        return win32gui.GetClassName(hwnd).strip()
    except win32gui.error:
        return ""

def is_window_cloaked(hwnd: int) -> bool:
    cloaked = ctypes.c_int()

    try:
        result = dwmapi.DwmGetWindowAttribute(
            hwnd,
            DWMWA_CLOAKED,
            ctypes.byref(cloaked),
            ctypes.sizeof(cloaked),
        )
    except OSError:
        return False

    return result == 0 and bool(cloaked.value)

def has_tool_window_style(hwnd: int) -> bool:
    try:
        exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    except win32gui.error:
        return False

    return bool(exstyle & win32con.WS_EX_TOOLWINDOW)

def is_real_window(hwnd: int) -> bool:
    if not win32gui.IsWindowVisible(hwnd):
        return False

    if win32gui.IsIconic(hwnd):
        return False

    if is_window_cloaked(hwnd):
        return False

    if has_tool_window_style(hwnd):
        return False

    title = get_window_text(hwnd)
    if not title:
        return False

    class_name = get_window_class(hwnd)
    if class_name in EXCLUDED_WINDOW_CLASSES:
        return False

    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    except win32gui.error:
        return False

    return (right - left) > 1 and (bottom - top) > 1

def safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
    return (name or "untitled")[:max_len]

def get_active_window():
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd or not is_real_window(hwnd):
        return None

    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except win32gui.error:
        return None

    return {
        "hwnd": hwnd,
        "title": get_window_text(hwnd),
        "pid": pid,
        "rect": (left, top, right, bottom),
    }

def capture_window(hwnd: int, output_path: str) -> bool:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = right - left
    height = bottom - top

    if width <= 0 or height <= 0:
        return False

    hwnd_desktop = win32gui.GetDesktopWindow()
    desktop_dc = win32gui.GetWindowDC(hwnd_desktop)
    img_dc = win32ui.CreateDCFromHandle(desktop_dc)
    mem_dc = img_dc.CreateCompatibleDC()
    screenshot = win32ui.CreateBitmap()

    try:
        screenshot.CreateCompatibleBitmap(img_dc, width, height)
        mem_dc.SelectObject(screenshot)

        ok = user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        if ok != 1:
            ok = user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), 0)
        if ok != 1:
            return False

        bmpinfo = screenshot.GetInfo()
        bmpbytes = screenshot.GetBitmapBits(True)

        image = Image.frombuffer(
            "RGB",
            (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
            bmpbytes,
            "raw",
            "BGRX",
            0,
            1,
        )
        image.save(output_path, "PNG")
        return True

    finally:
        win32gui.DeleteObject(screenshot.GetHandle())
        mem_dc.DeleteDC()
        img_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd_desktop, desktop_dc)

def main():
    if os.name != "nt":
        print("Windows only.")
        return

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")
    os.makedirs(out_dir, exist_ok=True)

    active_window = get_active_window()
    windows = [active_window] if active_window else []
    print(f"Found {len(windows)} active window(s)\n")

    for w in windows:
        filename = f'{w["pid"]}_{safe_filename(w["title"])}.png'
        path = os.path.join(out_dir, filename)

        try:
            success = capture_window(w["hwnd"], path)
            print(f'{"OK  " if success else "SKIP"} {w["title"]}')
        except Exception as e:
            print(f'ERR  {w["title"]}: {e}')

    print(f"\nSaved screenshots to: {out_dir}")

if __name__ == "__main__":
    main()


from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class CapturedWindow:
    hwnd: int
    title: str
    pid: int
    rect: tuple[int, int, int, int]
    path: Path
    captured_at: datetime

    @property
    def window_key(self) -> str:
        return f"{self.pid}:{self.title}"


def capture_active_window(output_dir: Path, *, captured_at: datetime | None = None) -> list[CapturedWindow]:
    if os.name != "nt":
        raise RuntimeError("The Win32 screenshot capture path only works on Windows.")

    captured_at = captured_at or datetime.now()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    window = get_active_window()
    if not window:
        return []

    results: list[CapturedWindow] = []
    filename = (
        f"{captured_at.strftime('%Y%m%d_%H%M%S')}_"
        f"{window['pid']}_{safe_filename(window['title'])}.png"
    )
    path = output_dir / filename
    success = capture_window(window["hwnd"], str(path))
    if success:
        results.append(
            CapturedWindow(
                hwnd=window["hwnd"],
                title=window["title"],
                pid=window["pid"],
                rect=window["rect"],
                path=path,
                captured_at=captured_at,
            )
        )
    return results


def capture_visible_windows(output_dir: Path, *, captured_at: datetime | None = None) -> list[CapturedWindow]:
    return capture_active_window(output_dir, captured_at=captured_at)
