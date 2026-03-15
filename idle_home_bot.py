import argparse
import ctypes
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from ctypes import wintypes
from PIL import ImageGrab


try:
    ctypes.windll.user32.SetProcessDPIAware()
except AttributeError:
    pass


user32 = ctypes.windll.user32

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_WHEEL = 0x0800

SW_RESTORE = 9
MAPVK_VK_TO_VSC = 0

VK_CODES = {
    "ALT": 0x12,
    "BACKSPACE": 0x08,
    "CTRL": 0x11,
    "DOWN": 0x28,
    "END": 0x23,
    "ENTER": 0x0D,
    "ESC": 0x1B,
    "F1": 0x70,
    "F2": 0x71,
    "F3": 0x72,
    "F4": 0x73,
    "F5": 0x74,
    "F6": 0x75,
    "F7": 0x76,
    "F8": 0x77,
    "F9": 0x78,
    "F10": 0x79,
    "F11": 0x7A,
    "F12": 0x7B,
    "HOME": 0x24,
    "LEFT": 0x25,
    "PAGEDOWN": 0x22,
    "PAGEUP": 0x21,
    "RIGHT": 0x27,
    "SHIFT": 0x10,
    "SPACE": 0x20,
    "TAB": 0x09,
    "UP": 0x26,
}

EXTENDED_KEY_NAMES = {
    "DOWN",
    "END",
    "HOME",
    "LEFT",
    "PAGEDOWN",
    "PAGEUP",
    "RIGHT",
    "UP",
}


class AbortRequested(Exception):
    pass


class BotError(Exception):
    pass


ULONG_PTR = wintypes.WPARAM


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", INPUTUNION),
    ]


LPINPUT = ctypes.POINTER(INPUT)
user32.SendInput.argtypes = (wintypes.UINT, LPINPUT, ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


@dataclass
class WindowInfo:
    handle: int
    title: str
    origin_x: int
    origin_y: int
    client_width: int
    client_height: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Idle Home loop runner for VRChat desktop mode.")
    parser.add_argument(
        "--config",
        default="idle_home_config.json",
        help="Path to JSON config. Defaults to idle_home_config.json.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log verbosity.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate", help="Validate config structure.")
    subparsers.add_parser("list-windows", help="List visible top-level windows.")

    calibrate = subparsers.add_parser("calibrate", help="Capture point coordinates into the config file.")
    calibrate.add_argument(
        "--allow-size-mismatch",
        action="store_true",
        help="Skip the 1600x900 client-size requirement during calibration.",
    )

    run = subparsers.add_parser("run", help="Run the Idle Home loop.")
    run.add_argument("--once", action="store_true", help="Run a single loop and exit.")
    run.add_argument("--dry-run", action="store_true", help="Log actions without sending input.")
    run.add_argument(
        "--startup-delay",
        type=float,
        default=None,
        help="Override startup_delay_sec from the config.",
    )
    run.add_argument(
        "--allow-size-mismatch",
        action="store_true",
        help="Skip the 1600x900 client-size requirement during execution.",
    )

    run_sequence = subparsers.add_parser(
        "run-sequence",
        help="Run a single configured sequence for tuning.",
    )
    run_sequence.add_argument("sequence_name", help="Sequence name from the config.")
    run_sequence.add_argument("--dry-run", action="store_true", help="Log actions without sending input.")
    run_sequence.add_argument(
        "--startup-delay",
        type=float,
        default=None,
        help="Override startup_delay_sec from the config.",
    )
    run_sequence.add_argument(
        "--allow-size-mismatch",
        action="store_true",
        help="Skip the 1600x900 client-size requirement during execution.",
    )

    run_wheel = subparsers.add_parser(
        "run-wheel",
        help="Send a mouse wheel input to the focused VRChat window.",
    )
    run_wheel.add_argument("delta", type=int, help="Wheel delta. Positive is up, negative is down.")
    run_wheel.add_argument("--repeat", type=int, default=1, help="Number of wheel events to send.")
    run_wheel.add_argument(
        "--pause",
        type=float,
        default=0.05,
        help="Pause in seconds between repeated wheel events.",
    )
    run_wheel.add_argument("--dry-run", action="store_true", help="Log actions without sending input.")
    run_wheel.add_argument(
        "--startup-delay",
        type=float,
        default=None,
        help="Override startup_delay_sec from the config.",
    )
    run_wheel.add_argument(
        "--allow-size-mismatch",
        action="store_true",
        help="Skip the 1600x900 client-size requirement during execution.",
    )
    return parser.parse_args()


def configure_logging(level_name: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level_name),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


class InMemoryLogHandler(logging.Handler):
    def __init__(self, max_lines: int) -> None:
        super().__init__(level=logging.INFO)
        self.max_lines = max(int(max_lines), 100)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        self.acquire()
        try:
            self.lines.append(message)
            overflow = len(self.lines) - self.max_lines
            if overflow > 0:
                del self.lines[:overflow]
        finally:
            self.release()

    def clear(self) -> None:
        self.acquire()
        try:
            self.lines.clear()
        finally:
            self.release()

    def snapshot(self) -> list[str]:
        self.acquire()
        try:
            return list(self.lines)
        finally:
            self.release()


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise BotError(f"Config file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BotError(f"Invalid JSON in {path}: {exc}") from exc


def save_config(path: Path, config: dict[str, Any]) -> None:
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def get_extra_config_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.extra.json")


def merge_config(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if key in {"sequences", "points"}:
            combined = dict(base.get(key, {}))
            combined.update(value)
            merged[key] = combined
            continue
        if key in {"timing", "hotkeys", "window"}:
            combined = dict(base.get(key, {}))
            combined.update(value)
            merged[key] = combined
            continue
        merged[key] = value
    return merged


def load_runtime_config(path: Path) -> dict[str, Any]:
    config = load_config(path)
    extra_path = get_extra_config_path(path)
    if extra_path.exists():
        extra = load_config(extra_path)
        config = merge_config(config, extra)
    return config


def load_cv_image(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise BotError(f"Failed to load image: {path}")
    return image


def normalize_key_name(name: str) -> str:
    return name.strip().upper()


def key_to_vk(name: str) -> int:
    normalized = normalize_key_name(name)
    if len(normalized) == 1 and normalized.isalnum():
        return ord(normalized)
    if normalized in VK_CODES:
        return VK_CODES[normalized]
    raise BotError(f"Unsupported key name: {name}")


def send_input(input_obj: INPUT) -> None:
    sent = user32.SendInput(1, ctypes.byref(input_obj), ctypes.sizeof(INPUT))
    if sent != 1:
        raise BotError("SendInput failed.")


def is_extended_key_name(name: str) -> bool:
    return normalize_key_name(name) in EXTENDED_KEY_NAMES


def send_key(vk_code: int, key_up: bool, *, extended: bool = False) -> None:
    scan_code = user32.MapVirtualKeyW(vk_code, MAPVK_VK_TO_VSC)
    if scan_code == 0:
        flags = KEYEVENTF_KEYUP if key_up else 0
        input_obj = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk_code, dwFlags=flags))
        send_input(input_obj)
        return

    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if key_up else 0)
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    input_obj = INPUT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(wVk=0, wScan=scan_code, dwFlags=flags),
    )
    send_input(input_obj)


def tap_key(vk_code: int, hold_ms: int = 50, *, extended: bool = False) -> None:
    send_key(vk_code, key_up=False, extended=extended)
    time.sleep(max(hold_ms, 0) / 1000)
    send_key(vk_code, key_up=True, extended=extended)


def hold_key(vk_code: int, seconds: float, *, extended: bool = False) -> None:
    send_key(vk_code, key_up=False, extended=extended)
    time.sleep(max(seconds, 0.0))
    send_key(vk_code, key_up=True, extended=extended)


def send_mouse(flags: int) -> None:
    input_obj = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dwFlags=flags))
    send_input(input_obj)


def send_mouse_wheel(delta: int) -> None:
    input_obj = INPUT(
        type=INPUT_MOUSE,
        mi=MOUSEINPUT(mouseData=delta & 0xFFFFFFFF, dwFlags=MOUSEEVENTF_WHEEL),
    )
    send_input(input_obj)


def move_mouse_relative(dx: int, dy: int) -> None:
    input_obj = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dx=dx, dy=dy, dwFlags=MOUSEEVENTF_MOVE))
    send_input(input_obj)


def left_click() -> None:
    send_mouse(MOUSEEVENTF_LEFTDOWN)
    time.sleep(0.03)
    send_mouse(MOUSEEVENTF_LEFTUP)


def right_click() -> None:
    send_mouse(MOUSEEVENTF_RIGHTDOWN)
    time.sleep(0.03)
    send_mouse(MOUSEEVENTF_RIGHTUP)


def mouse_button_flags(button: str) -> tuple[int, int]:
    normalized = button.lower()
    if normalized == "left":
        return MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
    if normalized == "right":
        return MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
    if normalized == "middle":
        return MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP
    raise BotError(f"Unsupported mouse button: {button}")


def get_visible_windows() -> list[tuple[int, str]]:
    windows: list[tuple[int, str]] = []

    @WNDENUMPROC
    def enum_proc(hwnd: int, _: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if title:
            windows.append((hwnd, title))
        return True

    user32.EnumWindows(enum_proc, 0)
    return windows


def find_window(title_substring: str) -> tuple[int, str]:
    needle = title_substring.casefold()
    for hwnd, title in get_visible_windows():
        if needle in title.casefold():
            return hwnd, title
    raise BotError(f'No visible window matched "{title_substring}".')


def get_window_info(hwnd: int, title: str) -> WindowInfo:
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise BotError("GetClientRect failed.")
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise BotError("ClientToScreen failed.")
    return WindowInfo(
        handle=hwnd,
        title=title,
        origin_x=origin.x,
        origin_y=origin.y,
        client_width=rect.right - rect.left,
        client_height=rect.bottom - rect.top,
    )


def get_cursor_position() -> tuple[int, int]:
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise BotError("GetCursorPos failed.")
    return point.x, point.y


def set_cursor_position(x: int, y: int) -> None:
    if not user32.SetCursorPos(x, y):
        raise BotError("SetCursorPos failed.")


def screen_to_client(hwnd: int, x: int, y: int) -> tuple[int, int]:
    point = wintypes.POINT(x, y)
    if not user32.ScreenToClient(hwnd, ctypes.byref(point)):
        raise BotError("ScreenToClient failed.")
    return point.x, point.y


def capture_client_image(info: WindowInfo) -> np.ndarray:
    image = ImageGrab.grab(
        bbox=(
            info.origin_x,
            info.origin_y,
            info.origin_x + info.client_width,
            info.origin_y + info.client_height,
        ),
        all_screens=True,
    )
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def ensure_window(config: dict[str, Any], allow_size_mismatch: bool) -> WindowInfo:
    title_substring = str(config["window"]["title_substring"])
    hwnd, title = find_window(title_substring)
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.1)
    info = get_window_info(hwnd, title)
    expected_width = int(config["window"]["client_width"])
    expected_height = int(config["window"]["client_height"])
    if not allow_size_mismatch and (
        info.client_width != expected_width or info.client_height != expected_height
    ):
        raise BotError(
            f"VRChat client area is {info.client_width}x{info.client_height}, "
            f"expected {expected_width}x{expected_height}."
        )
    foreground = user32.GetForegroundWindow()
    if foreground != hwnd:
        raise BotError("VRChat window is not focused. Focus it and retry.")
    return info


def validate_action(action: dict[str, Any], points: dict[str, Any]) -> None:
    action_type = action.get("type")
    if action_type not in {
        "wait",
        "mouse_move",
        "mouse_move_relative",
        "mouse_drag_relative",
        "mouse_wheel",
        "vision_center_click",
        "vision_wait_absent",
        "pattern_click",
        "left_click",
        "right_click",
        "key_tap",
        "key_hold",
    }:
        raise BotError(f"Unsupported action type: {action_type}")

    if action_type == "wait":
        if "seconds" not in action:
            raise BotError("wait action requires seconds.")
        return

    if action_type == "mouse_move_relative":
        if "dx" not in action or "dy" not in action:
            raise BotError("mouse_move_relative action requires dx and dy.")
        return

    if action_type == "mouse_drag_relative":
        if "dx" not in action or "dy" not in action:
            raise BotError("mouse_drag_relative action requires dx and dy.")
        mouse_button_flags(str(action.get("button", "left")))
        return

    if action_type == "mouse_wheel":
        if "delta" not in action:
            raise BotError("mouse_wheel action requires delta.")
        return

    if action_type == "vision_center_click":
        if "template" not in action:
            raise BotError("vision_center_click action requires template.")
        search_region = action.get("search_region")
        if search_region is not None:
            if not isinstance(search_region, list) or len(search_region) != 4:
                raise BotError("vision_center_click search_region must be [x, y, width, height].")
        return

    if action_type == "vision_wait_absent":
        if "template" not in action:
            raise BotError("vision_wait_absent action requires template.")
        search_region = action.get("search_region")
        if search_region is not None:
            if not isinstance(search_region, list) or len(search_region) != 4:
                raise BotError("vision_wait_absent search_region must be [x, y, width, height].")
        if bool(action.get("retry_center_click_if_visible", False)):
            if "anchor_x" not in action or "anchor_y" not in action:
                raise BotError(
                    "vision_wait_absent with retry_center_click_if_visible requires anchor_x and anchor_y."
                )
        return

    if action_type == "pattern_click":
        offsets = action.get("offsets")
        if not isinstance(offsets, list) or not offsets:
            raise BotError("pattern_click action requires a non-empty offsets list.")
        for offset in offsets:
            if not isinstance(offset, list) or len(offset) != 2:
                raise BotError("pattern_click offsets must be [dx, dy] pairs.")
        return

    if action_type in {"key_tap", "key_hold"}:
        if "key" not in action:
            raise BotError(f"{action_type} action requires key.")
        key_to_vk(str(action["key"]))
        if action_type == "key_hold" and "seconds" not in action:
            raise BotError("key_hold action requires seconds.")
        return

    has_target = "target" in action
    has_xy = "x" in action and "y" in action
    if action_type == "mouse_move" and not (has_target or has_xy):
        raise BotError("mouse_move action requires target or x/y.")
    if has_target and action["target"] not in points:
        raise BotError(f'Unknown point target "{action["target"]}".')


def validate_config(config: dict[str, Any]) -> None:
    required_top_level = {"window", "hotkeys", "timing", "sequences"}
    missing = required_top_level - set(config)
    if missing:
        raise BotError(f"Missing top-level config keys: {sorted(missing)}")

    points = dict(config.get("points", {}))
    capture_order = list(config.get("capture_order", []))

    key_to_vk(str(config["hotkeys"]["abort"]))
    key_to_vk(str(config["hotkeys"]["capture"]))

    for point_name, point in points.items():
        if "x" not in point or "y" not in point:
            raise BotError(f"Point {point_name} must include x and y.")

    for point_name in capture_order:
        if point_name not in points:
            raise BotError(f'capture_order references unknown point "{point_name}".')

    sequences = config["sequences"]
    required_sequences = {
        "pickup_sword",
        "move_to_combat_position",
        "enable_auto_attack",
        "disable_auto_attack",
        "ascend",
        "after_ascend",
    }
    missing_sequences = required_sequences - set(sequences)
    if missing_sequences:
        raise BotError(f"Missing sequences: {sorted(missing_sequences)}")

    for sequence_name, actions in sequences.items():
        if not isinstance(actions, list):
            raise BotError(f"Sequence {sequence_name} must be a list.")
        for action in actions:
            if not isinstance(action, dict):
                raise BotError(f"Sequence {sequence_name} contains a non-object action.")
            validate_action(action, points)

    for timing_name in {"startup_delay_sec", "combat_duration_sec", "after_cycle_wait_sec"}:
        if timing_name not in config["timing"]:
            raise BotError(f"Missing timing value: {timing_name}")


def is_key_down(vk_code: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk_code) & 0x8000)


def wait_for_key_release(vk_code: int) -> None:
    while is_key_down(vk_code):
        time.sleep(0.02)


class IdleHomeBot:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        config_dir: Path,
        allow_size_mismatch: bool,
        dry_run: bool,
        abort_event: Any | None = None,
    ) -> None:
        self.config = config
        self.config_dir = config_dir
        self.allow_size_mismatch = allow_size_mismatch
        self.dry_run = dry_run
        self.abort_event = abort_event
        self.abort_vk = key_to_vk(str(config["hotkeys"]["abort"]))
        self.capture_vk = key_to_vk(str(config["hotkeys"]["capture"]))
        self.template_cache: dict[Path, np.ndarray] = {}
        self.current_cycle = 0
        self.current_sequence: str | None = None
        self.current_action_type: str | None = None
        self.failure_log_handler = InMemoryLogHandler(
            max_lines=int(self.config.get("failure_cycle_log_max_lines", 500))
        )
        self.failure_log_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
        )
        self.recent_log_handler = InMemoryLogHandler(
            max_lines=int(self.config.get("failure_recent_log_max_lines", 1000))
        )
        self.recent_log_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
        )
        self.recent_snapshot_limit = max(int(self.config.get("failure_recent_snapshot_count", 8)), 0)
        self.recent_snapshots: list[dict[str, Any]] = []
        logging.getLogger().addHandler(self.failure_log_handler)
        logging.getLogger().addHandler(self.recent_log_handler)

    def close(self) -> None:
        logging.getLogger().removeHandler(self.failure_log_handler)
        self.failure_log_handler.close()
        logging.getLogger().removeHandler(self.recent_log_handler)
        self.recent_log_handler.close()

    def record_state_snapshot(self, label: str) -> None:
        if self.dry_run or self.recent_snapshot_limit <= 0:
            return
        try:
            hwnd, title = find_window(str(self.config["window"]["title_substring"]))
            info = get_window_info(hwnd, title)
            screenshot = capture_client_image(info)
        except Exception:
            return

        self.recent_snapshots.append(
            {
                "label": label,
                "cycle": self.current_cycle,
                "sequence": self.current_sequence,
                "action": self.current_action_type,
                "image": screenshot,
            }
        )
        overflow = len(self.recent_snapshots) - self.recent_snapshot_limit
        if overflow > 0:
            del self.recent_snapshots[:overflow]

    def ensure_abort_not_requested(self) -> None:
        if self.abort_event is not None and self.abort_event.is_set():
            raise AbortRequested("Abort requested by controller.")
        if is_key_down(self.abort_vk):
            raise AbortRequested(
                f"Abort hotkey {self.config['hotkeys']['abort']} pressed."
            )

    def sleep_with_abort(self, seconds: float) -> None:
        deadline = time.monotonic() + max(seconds, 0.0)
        while time.monotonic() < deadline:
            self.ensure_abort_not_requested()
            time.sleep(0.02)

    def countdown(self, seconds: float) -> None:
        total = int(max(seconds, 0))
        if total <= 0:
            return
        logging.info("Starting in %s seconds. Focus VRChat now.", total)
        for remaining in range(total, 0, -1):
            logging.info("Countdown: %s", remaining)
            self.sleep_with_abort(1.0)

    def resolve_asset_path(self, path_value: str) -> Path:
        path = Path(path_value)
        if path.is_absolute():
            return path
        return self.config_dir / path

    def sanitize_label(self, value: str | None) -> str:
        if not value:
            return "none"
        sanitized = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)
        sanitized = sanitized.strip("_")
        return sanitized or "none"

    def write_cv_image(self, path: Path, image: np.ndarray) -> None:
        success, encoded = cv2.imencode(".png", image)
        if not success:
            raise BotError(f"Failed to encode image for {path}.")
        encoded.tofile(str(path))

    def capture_failure_artifacts(self, error_message: str) -> None:
        capture_dir = self.resolve_asset_path(str(self.config.get("failure_capture_dir", "failure_captures")))
        screenshot: np.ndarray | None = None
        try:
            hwnd, title = find_window(str(self.config["window"]["title_substring"]))
            info = get_window_info(hwnd, title)
            screenshot = capture_client_image(info)
        except Exception as exc:
            logging.warning("Failed to capture failure screenshot: %s", exc)

        capture_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        cycle_label = f"cycle{self.current_cycle:03d}" if self.current_cycle > 0 else "cycle000"
        sequence_label = self.sanitize_label(self.current_sequence)
        action_label = self.sanitize_label(self.current_action_type)
        base_name = f"{timestamp}_{cycle_label}_{sequence_label}_{action_label}"
        image_path = capture_dir / f"{base_name}.png"
        meta_path = capture_dir / f"{base_name}.txt"
        cycle_log_path = capture_dir / f"{base_name}.log"
        recent_log_path = capture_dir / f"{base_name}.recent.log"

        try:
            if screenshot is not None:
                self.write_cv_image(image_path, screenshot)
            meta_lines = [
                f"time={timestamp}",
                f"cycle={self.current_cycle}",
                f"sequence={self.current_sequence or ''}",
                f"action={self.current_action_type or ''}",
                f"error={error_message}",
            ]
            meta_path.write_text("\n".join(meta_lines) + "\n", encoding="utf-8")
            cycle_log_lines = self.failure_log_handler.snapshot()
            if cycle_log_lines:
                cycle_log_path.write_text("\n".join(cycle_log_lines) + "\n", encoding="utf-8")
            recent_log_lines = self.recent_log_handler.snapshot()
            if recent_log_lines:
                recent_log_path.write_text("\n".join(recent_log_lines) + "\n", encoding="utf-8")
            for index, snapshot in enumerate(self.recent_snapshots, start=1):
                snapshot_cycle = int(snapshot.get("cycle", 0))
                snapshot_sequence = self.sanitize_label(snapshot.get("sequence"))
                snapshot_label = self.sanitize_label(str(snapshot.get("label", f"snapshot_{index:02d}")))
                snapshot_path = capture_dir / (
                    f"{base_name}_snap{index:02d}_cycle{snapshot_cycle:03d}_{snapshot_sequence}_{snapshot_label}.png"
                )
                self.write_cv_image(snapshot_path, snapshot["image"])
            if screenshot is not None:
                logging.info("Saved failure artifacts to %s", image_path)
            else:
                logging.info("Saved failure artifacts to %s", meta_path)
        except OSError as exc:
            logging.warning("Failed to save failure artifacts: %s", exc)

    def load_template(self, path_value: str) -> np.ndarray:
        path = self.resolve_asset_path(path_value)
        if path not in self.template_cache:
            self.template_cache[path] = load_cv_image(path)
        return self.template_cache[path]

    def find_template(
        self,
        screenshot: np.ndarray,
        template: np.ndarray,
        search_region: list[int] | None,
        match_mode: str = "gray",
    ) -> tuple[float, tuple[int, int]]:
        offset_x = 0
        offset_y = 0
        haystack = screenshot
        if search_region is not None:
            region_x, region_y, region_width, region_height = [int(value) for value in search_region]
            if region_width <= 0 or region_height <= 0:
                raise BotError("vision_center_click search_region must have positive width and height.")
            if region_x >= screenshot.shape[1] or region_y >= screenshot.shape[0]:
                raise BotError("vision_center_click search_region starts outside the client area.")
            region_x = max(region_x, 0)
            region_y = max(region_y, 0)
            region_width = min(region_width, screenshot.shape[1] - region_x)
            region_height = min(region_height, screenshot.shape[0] - region_y)
            haystack = screenshot[region_y:region_y + region_height, region_x:region_x + region_width]
            offset_x = region_x
            offset_y = region_y

        if template.shape[0] > haystack.shape[0] or template.shape[1] > haystack.shape[1]:
            raise BotError("Template image is larger than the search area.")

        if match_mode == "color":
            result = cv2.matchTemplate(haystack, template, cv2.TM_CCOEFF_NORMED)
        else:
            haystack_gray = cv2.cvtColor(haystack, cv2.COLOR_BGR2GRAY)
            template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
            result = cv2.matchTemplate(haystack_gray, template_gray, cv2.TM_CCOEFF_NORMED)
        _, score, _, max_loc = cv2.minMaxLoc(result)
        return score, (offset_x + max_loc[0], offset_y + max_loc[1])

    def resolve_point(self, info: WindowInfo, action: dict[str, Any]) -> tuple[int, int]:
        if "target" in action:
            point = self.config.get("points", {})[str(action["target"])]
            relative_x = int(point["x"])
            relative_y = int(point["y"])
        else:
            relative_x = int(action["x"])
            relative_y = int(action["y"])
        return info.origin_x + relative_x, info.origin_y + relative_y

    def move_mouse(self, info: WindowInfo, action: dict[str, Any]) -> None:
        x, y = self.resolve_point(info, action)
        logging.info("Mouse move -> (%s, %s)", x, y)
        if not self.dry_run:
            set_cursor_position(x, y)
        self.sleep_with_abort(0.05)

    def move_mouse_relative(self, action: dict[str, Any]) -> None:
        dx = int(action["dx"])
        dy = int(action["dy"])
        steps = max(int(action.get("steps", 1)), 1)
        step_delay_ms = int(action.get("step_delay_ms", 15))
        logging.info("Mouse move relative -> dx=%s dy=%s steps=%s", dx, dy, steps)
        if self.dry_run:
            self.sleep_with_abort((step_delay_ms * steps) / 1000)
            return

        moved_x = 0
        moved_y = 0
        for index in range(steps):
            target_x = round(dx * (index + 1) / steps)
            target_y = round(dy * (index + 1) / steps)
            step_x = target_x - moved_x
            step_y = target_y - moved_y
            moved_x = target_x
            moved_y = target_y
            move_mouse_relative(step_x, step_y)
            self.sleep_with_abort(step_delay_ms / 1000)

    def drag_mouse_relative(self, action: dict[str, Any]) -> None:
        button = str(action.get("button", "left")).lower()
        down_flag, up_flag = mouse_button_flags(button)
        dx = int(action["dx"])
        dy = int(action["dy"])
        steps = max(int(action.get("steps", 1)), 1)
        step_delay_ms = int(action.get("step_delay_ms", 15))
        hold_before_move_sec = float(action.get("hold_before_move_sec", 0.03))
        release_wait_sec = float(action.get("release_wait_sec", 0.03))
        logging.info(
            "Mouse drag relative -> button=%s dx=%s dy=%s steps=%s",
            button,
            dx,
            dy,
            steps,
        )
        if self.dry_run:
            self.sleep_with_abort(hold_before_move_sec + release_wait_sec + ((step_delay_ms * steps) / 1000))
            return

        send_mouse(down_flag)
        try:
            self.sleep_with_abort(hold_before_move_sec)
            moved_x = 0
            moved_y = 0
            for index in range(steps):
                target_x = round(dx * (index + 1) / steps)
                target_y = round(dy * (index + 1) / steps)
                step_x = target_x - moved_x
                step_y = target_y - moved_y
                moved_x = target_x
                moved_y = target_y
                move_mouse_relative(step_x, step_y)
                self.sleep_with_abort(step_delay_ms / 1000)
        finally:
            send_mouse(up_flag)
        self.sleep_with_abort(release_wait_sec)

    def click(self, info: WindowInfo, action: dict[str, Any], button: str) -> None:
        if "target" in action or ("x" in action and "y" in action):
            self.move_mouse(info, action)
        self.perform_click(button)

    def perform_click(self, button: str) -> None:
        logging.info("%s click", button)
        if self.dry_run:
            return
        if button == "left":
            left_click()
        else:
            right_click()

    def vision_center_click(self, info: WindowInfo, action: dict[str, Any]) -> None:
        template = self.load_template(str(action["template"]))
        match_mode = str(action.get("match_mode", "gray")).lower()
        anchor_x = int(action.get("anchor_x", template.shape[1] // 2))
        anchor_y = int(action.get("anchor_y", template.shape[0] // 2))
        search_region = action.get("search_region")
        threshold = float(action.get("threshold", 0.6))
        tracking_threshold = float(action.get("tracking_threshold", threshold))
        candidate_threshold = float(action.get("candidate_threshold", tracking_threshold))
        centered_fallback_threshold = float(action.get("centered_fallback_threshold", threshold))
        centered_fallback_retries = max(int(action.get("centered_fallback_retries", 1)), 1)
        tolerance_px = int(action.get("tolerance_px", 18))
        max_attempts = max(int(action.get("max_attempts", 6)), 1)
        gain_x = float(action.get("gain_x", 1.0))
        gain_y = float(action.get("gain_y", 1.0))
        max_step_px = max(int(action.get("max_step_px", 120)), 1)
        post_move_wait_sec = float(action.get("post_move_wait_sec", 0.05))
        pre_click_wait_sec = float(action.get("pre_click_wait_sec", 0.03))
        post_click_wait_sec = float(action.get("post_click_wait_sec", 0.1))
        verify_absent_after_click = bool(action.get("verify_absent_after_click", False))
        verify_wait_sec = float(action.get("verify_wait_sec", 0.25))
        absent_threshold = float(action.get("absent_threshold", candidate_threshold))
        retry_on_verify_failure = bool(action.get("retry_on_verify_failure", False))
        low_score_retry_count = max(int(action.get("low_score_retry_count", 0)), 0)
        low_score_retry_delay_sec = float(action.get("low_score_retry_delay_sec", post_move_wait_sec))
        restore_view_after_click = bool(action.get("restore_view_after_click", False))
        restore_wait_sec = float(action.get("restore_wait_sec", post_move_wait_sec))
        button = str(action.get("button", "left")).lower()
        locked_on = False
        centered_match_count = 0
        total_adjust_dx = 0
        total_adjust_dy = 0

        def restore_view() -> None:
            if not restore_view_after_click:
                return
            if total_adjust_dx == 0 and total_adjust_dy == 0:
                return
            logging.info("Vision restore -> dx=%s dy=%s", -total_adjust_dx, -total_adjust_dy)
            if not self.dry_run:
                move_mouse_relative(-total_adjust_dx, -total_adjust_dy)
            self.sleep_with_abort(restore_wait_sec)

        for attempt in range(1, max_attempts + 1):
            screenshot = capture_client_image(info)
            score, top_left = self.find_template(screenshot, template, search_region, match_mode)
            target_x = top_left[0] + anchor_x
            target_y = top_left[1] + anchor_y
            error_x = target_x - (info.client_width // 2)
            error_y = target_y - (info.client_height // 2)
            logging.info(
                "Vision candidate %s attempt=%s score=%.3f top_left=(%s,%s) error=(%s,%s)",
                action["template"],
                attempt,
                score,
                top_left[0],
                top_left[1],
                error_x,
                error_y,
            )
            required_threshold = tracking_threshold if locked_on else candidate_threshold
            if score < required_threshold:
                if low_score_retry_count > 0:
                    logging.info(
                        "Vision low score retry for %s "
                        "(score=%.3f, threshold=%.3f, remaining=%s).",
                        action["template"],
                        score,
                        required_threshold,
                        low_score_retry_count,
                    )
                    low_score_retry_count -= 1
                    self.sleep_with_abort(low_score_retry_delay_sec)
                    continue
                raise BotError(
                    f"vision_center_click could not find {action['template']} "
                    f"(score={score:.3f}, threshold={required_threshold:.3f}, "
                    f"top_left=({top_left[0]},{top_left[1]}), error=({error_x},{error_y}))."
                )
            locked_on = True

            if abs(error_x) <= tolerance_px and abs(error_y) <= tolerance_px:
                if score < threshold:
                    centered_match_count += 1
                    if (
                        score >= centered_fallback_threshold
                        and centered_match_count >= centered_fallback_retries
                    ):
                        logging.info(
                            "Vision centered fallback click for %s "
                            "(score=%.3f, threshold=%.3f, retries=%s).",
                            action["template"],
                            score,
                            centered_fallback_threshold,
                            centered_match_count,
                        )
                        self.sleep_with_abort(pre_click_wait_sec)
                        self.perform_click(button)
                        self.sleep_with_abort(post_click_wait_sec)
                        if verify_absent_after_click:
                            self.sleep_with_abort(verify_wait_sec)
                            verify_screenshot = capture_client_image(info)
                            verify_score, verify_top_left = self.find_template(
                                verify_screenshot,
                                template,
                                search_region,
                                match_mode,
                            )
                            logging.info(
                                "Vision post-click %s score=%.3f top_left=(%s,%s) absent_threshold=%.3f",
                                action["template"],
                                verify_score,
                                verify_top_left[0],
                                verify_top_left[1],
                                absent_threshold,
                            )
                            if verify_score >= absent_threshold:
                                if retry_on_verify_failure:
                                    logging.info(
                                        "Vision post-click still visible for %s; retrying.",
                                        action["template"],
                                    )
                                    centered_match_count = 0
                                    continue
                                raise BotError(
                                    f"vision_center_click clicked {action['template']} but target is still visible "
                                    f"(score={verify_score:.3f}, absent_threshold={absent_threshold:.3f}, "
                                    f"top_left=({verify_top_left[0]},{verify_top_left[1]}))."
                                )
                        restore_view()
                        return
                    logging.info(
                        "Vision centered but below click threshold for %s "
                        "(score=%.3f, threshold=%.3f). Retrying.",
                        action["template"],
                        score,
                        threshold,
                    )
                    self.sleep_with_abort(post_move_wait_sec)
                    continue
                centered_match_count = 0
                self.sleep_with_abort(pre_click_wait_sec)
                self.perform_click(button)
                self.sleep_with_abort(post_click_wait_sec)
                if verify_absent_after_click:
                    self.sleep_with_abort(verify_wait_sec)
                    verify_screenshot = capture_client_image(info)
                    verify_score, verify_top_left = self.find_template(
                        verify_screenshot,
                        template,
                        search_region,
                        match_mode,
                    )
                    logging.info(
                        "Vision post-click %s score=%.3f top_left=(%s,%s) absent_threshold=%.3f",
                        action["template"],
                        verify_score,
                        verify_top_left[0],
                        verify_top_left[1],
                        absent_threshold,
                    )
                    if verify_score >= absent_threshold:
                        if retry_on_verify_failure:
                            logging.info(
                                "Vision post-click still visible for %s; retrying.",
                                action["template"],
                            )
                            centered_match_count = 0
                            continue
                        raise BotError(
                            f"vision_center_click clicked {action['template']} but target is still visible "
                            f"(score={verify_score:.3f}, absent_threshold={absent_threshold:.3f}, "
                            f"top_left=({verify_top_left[0]},{verify_top_left[1]}))."
                        )
                restore_view()
                return
            centered_match_count = 0

            move_dx = int(round(error_x * gain_x))
            move_dy = int(round(error_y * gain_y))
            move_dx = max(-max_step_px, min(max_step_px, move_dx))
            move_dy = max(-max_step_px, min(max_step_px, move_dy))
            if move_dx == 0 and abs(error_x) > tolerance_px:
                move_dx = 1 if error_x > 0 else -1
            if move_dy == 0 and abs(error_y) > tolerance_px:
                move_dy = 1 if error_y > 0 else -1

            logging.info("Vision adjust -> dx=%s dy=%s", move_dx, move_dy)
            if not self.dry_run:
                move_mouse_relative(move_dx, move_dy)
            total_adjust_dx += move_dx
            total_adjust_dy += move_dy
            self.sleep_with_abort(post_move_wait_sec)

        raise BotError(f"vision_center_click failed to center {action['template']} in {max_attempts} attempts.")

    def vision_wait_absent(self, info: WindowInfo, action: dict[str, Any]) -> None:
        template_path = str(action["template"])
        template = self.load_template(template_path)
        search_region = action.get("search_region")
        match_mode = str(action.get("match_mode", "gray")).lower()
        absence_threshold = float(action.get("absence_threshold", action.get("threshold", 0.5)))
        initial_wait_sec = float(action.get("initial_wait_sec", 0.0))
        timeout_sec = float(action.get("timeout_sec", 10.0))
        poll_sec = float(action.get("poll_sec", 0.25))
        retry_center_click_if_visible = bool(action.get("retry_center_click_if_visible", False))
        retry_click_limit = max(int(action.get("retry_click_limit", 0)), 0)
        retry_click_interval_sec = float(action.get("retry_click_interval_sec", 1.0))

        if initial_wait_sec > 0:
            logging.info("Vision wait absent initial wait %.2fs for %s", initial_wait_sec, template_path)
            self.sleep_with_abort(initial_wait_sec)

        deadline = time.monotonic() + max(timeout_sec, 0.0)
        retry_clicks_used = 0
        next_retry_time = time.monotonic()

        while True:
            self.ensure_abort_not_requested()
            screenshot = capture_client_image(info)
            score, top_left = self.find_template(screenshot, template, search_region, match_mode)
            logging.info(
                "Vision wait absent %s score=%.3f top_left=(%s,%s) absence_threshold=%.3f",
                template_path,
                score,
                top_left[0],
                top_left[1],
                absence_threshold,
            )
            if score < absence_threshold:
                return

            now = time.monotonic()
            if (
                retry_center_click_if_visible
                and retry_clicks_used < retry_click_limit
                and now >= next_retry_time
            ):
                retry_clicks_used += 1
                next_retry_time = now + max(retry_click_interval_sec, 0.0)
                logging.info(
                    "Vision wait absent retry click for %s (%s/%s).",
                    template_path,
                    retry_clicks_used,
                    retry_click_limit,
                )
                click_action = dict(action)
                click_action["type"] = "vision_center_click"
                if "click_threshold" in action:
                    click_action["threshold"] = float(action["click_threshold"])
                self.vision_center_click(info, click_action)
                continue

            remaining = deadline - now
            if remaining <= 0:
                raise BotError(
                    f"vision_wait_absent timed out for {template_path} "
                    f"(score={score:.3f}, absence_threshold={absence_threshold:.3f}, "
                    f"top_left=({top_left[0]},{top_left[1]}), retries={retry_clicks_used}/{retry_click_limit})."
                )
            self.sleep_with_abort(min(max(poll_sec, 0.05), remaining))

    def pattern_click(self, action: dict[str, Any]) -> None:
        button = str(action.get("button", "left")).lower()
        if button not in {"left", "right"}:
            raise BotError(f"Unsupported pattern_click button: {button}")

        pause = float(action.get("pause_sec", 0.05))
        return_to_origin = bool(action.get("return_to_origin", True))
        current_dx = 0
        current_dy = 0

        logging.info("Pattern click with %s offsets", len(action["offsets"]))
        for offset_dx, offset_dy in action["offsets"]:
            step_dx = int(offset_dx) - current_dx
            step_dy = int(offset_dy) - current_dy
            if step_dx or step_dy:
                logging.info("Pattern move -> dx=%s dy=%s", step_dx, step_dy)
                if not self.dry_run:
                    move_mouse_relative(step_dx, step_dy)
                self.sleep_with_abort(pause)
            click_action: dict[str, Any] = {"type": f"{button}_click"}
            self.click(
                WindowInfo(handle=0, title="", origin_x=0, origin_y=0, client_width=0, client_height=0),
                click_action,
                button,
            )
            self.sleep_with_abort(pause)
            current_dx = int(offset_dx)
            current_dy = int(offset_dy)

        if return_to_origin and (current_dx or current_dy):
            logging.info("Pattern return -> dx=%s dy=%s", -current_dx, -current_dy)
            if not self.dry_run:
                move_mouse_relative(-current_dx, -current_dy)
            self.sleep_with_abort(pause)

    def run_action(self, info: WindowInfo, action: dict[str, Any]) -> None:
        self.ensure_abort_not_requested()
        action_type = str(action["type"])
        if action_type == "wait":
            duration = float(action["seconds"])
            logging.info("Wait %.2fs", duration)
            self.sleep_with_abort(duration)
            return

        if action_type == "mouse_move":
            self.move_mouse(info, action)
            return

        if action_type == "mouse_move_relative":
            self.move_mouse_relative(action)
            return

        if action_type == "mouse_drag_relative":
            self.drag_mouse_relative(action)
            return

        if action_type == "mouse_wheel":
            delta = int(action["delta"])
            repeat = max(int(action.get("repeat", 1)), 1)
            pause = float(action.get("pause_sec", 0.05))
            logging.info("Mouse wheel delta=%s repeat=%s", delta, repeat)
            for _ in range(repeat):
                if not self.dry_run:
                    send_mouse_wheel(delta)
                self.sleep_with_abort(pause)
            return

        if action_type == "vision_center_click":
            self.vision_center_click(info, action)
            return

        if action_type == "vision_wait_absent":
            self.vision_wait_absent(info, action)
            return

        if action_type == "pattern_click":
            self.pattern_click(action)
            return

        if action_type == "left_click":
            self.click(info, action, "left")
            return

        if action_type == "right_click":
            self.click(info, action, "right")
            return

        if action_type == "key_tap":
            key_name = str(action["key"])
            hold_ms = int(action.get("hold_ms", 50))
            logging.info("Key tap %s", key_name)
            if not self.dry_run:
                tap_key(
                    key_to_vk(key_name),
                    hold_ms,
                    extended=is_extended_key_name(key_name),
                )
            self.sleep_with_abort(0.05)
            return

        if action_type == "key_hold":
            key_name = str(action["key"])
            duration = float(action["seconds"])
            logging.info("Key hold %s for %.2fs", key_name, duration)
            if self.dry_run:
                self.sleep_with_abort(duration)
                return
            vk_code = key_to_vk(key_name)
            extended = is_extended_key_name(key_name)
            send_key(vk_code, key_up=False, extended=extended)
            try:
                self.sleep_with_abort(duration)
            finally:
                send_key(vk_code, key_up=True, extended=extended)
            self.sleep_with_abort(0.05)
            return

        raise BotError(f"Unsupported action type: {action_type}")

    def run_sequence(self, name: str, info: WindowInfo) -> None:
        if name not in self.config["sequences"]:
            raise BotError(f"Unknown sequence: {name}")
        actions = self.config["sequences"][name]
        if not actions:
            logging.info("Sequence %s is empty.", name)
            return
        logging.info("Sequence %s", name)
        previous_sequence = self.current_sequence
        previous_action_type = self.current_action_type
        self.current_sequence = name
        self.current_action_type = None
        self.record_state_snapshot(f"before_{name}")
        try:
            for action in actions:
                self.current_action_type = str(action.get("type", "unknown"))
                self.run_action(info, action)
            self.record_state_snapshot(f"after_{name}")
        finally:
            if sys.exc_info()[0] is None:
                self.current_sequence = previous_sequence
                self.current_action_type = previous_action_type

    def run_cycle(self) -> None:
        info = ensure_window(self.config, self.allow_size_mismatch)
        self.run_sequence("pickup_sword", info)
        self.run_sequence("move_to_combat_position", info)
        self.run_sequence("enable_auto_attack", info)
        combat_duration = float(self.config["timing"]["combat_duration_sec"])
        logging.info("Combat wait %.2fs", combat_duration)
        self.sleep_with_abort(combat_duration)
        self.run_sequence("disable_auto_attack", info)
        self.run_sequence("ascend", info)
        self.run_sequence("after_ascend", info)

    def run_forever(self, once: bool) -> None:
        self.countdown(float(self.config["timing"]["startup_delay_sec"]))
        cycle = 0
        while True:
            cycle += 1
            self.current_cycle = cycle
            self.failure_log_handler.clear()
            logging.info("Cycle %s start", cycle)
            self.record_state_snapshot("cycle_start")
            self.run_cycle()
            logging.info("Cycle %s complete", cycle)
            if once:
                return
            self.sleep_with_abort(float(self.config["timing"]["after_cycle_wait_sec"]))

    def run_named_sequence(self, name: str, startup_delay_override: float | None) -> None:
        startup_delay = (
            float(self.config["timing"]["startup_delay_sec"])
            if startup_delay_override is None
            else float(startup_delay_override)
        )
        self.failure_log_handler.clear()
        self.countdown(startup_delay)
        self.current_cycle = 0
        self.record_state_snapshot("sequence_run_start")
        info = ensure_window(self.config, self.allow_size_mismatch)
        self.run_sequence(name, info)

    def run_wheel(
        self,
        delta: int,
        repeat: int,
        pause_sec: float,
        startup_delay_override: float | None,
    ) -> None:
        startup_delay = (
            float(self.config["timing"]["startup_delay_sec"])
            if startup_delay_override is None
            else float(startup_delay_override)
        )
        self.failure_log_handler.clear()
        self.countdown(startup_delay)
        self.record_state_snapshot("wheel_run_start")
        ensure_window(self.config, self.allow_size_mismatch)
        action = {
            "type": "mouse_wheel",
            "delta": int(delta),
            "repeat": max(int(repeat), 1),
            "pause_sec": max(float(pause_sec), 0.0),
        }
        self.run_action(
            WindowInfo(handle=0, title="", origin_x=0, origin_y=0, client_width=0, client_height=0),
            action,
        )

    def calibrate(self) -> None:
        info = ensure_window(self.config, self.allow_size_mismatch)
        capture_order = list(self.config.get("capture_order", []))
        if not capture_order:
            raise BotError("No capture_order configured. Calibration is only for true cursor-based UI.")
        logging.info(
            "Calibrating against window '%s' at (%s, %s), client %sx%s",
            info.title,
            info.origin_x,
            info.origin_y,
            info.client_width,
            info.client_height,
        )
        wait_for_key_release(self.capture_vk)
        wait_for_key_release(self.abort_vk)
        for point_name in capture_order:
            logging.info(
                "Hover cursor over %s in VRChat, then press %s to capture. Press %s to abort.",
                point_name,
                self.config["hotkeys"]["capture"],
                self.config["hotkeys"]["abort"],
            )
            while True:
                self.ensure_abort_not_requested()
                if is_key_down(self.capture_vk):
                    screen_x, screen_y = get_cursor_position()
                    relative_x, relative_y = screen_to_client(info.handle, screen_x, screen_y)
                    if not (0 <= relative_x < info.client_width and 0 <= relative_y < info.client_height):
                        logging.warning(
                            "Cursor (%s, %s) is outside the VRChat client area. Retry.",
                            screen_x,
                            screen_y,
                        )
                        wait_for_key_release(self.capture_vk)
                        continue
                    self.config["points"][point_name]["x"] = relative_x
                    self.config["points"][point_name]["y"] = relative_y
                    logging.info("%s = (%s, %s)", point_name, relative_x, relative_y)
                    wait_for_key_release(self.capture_vk)
                    self.sleep_with_abort(0.15)
                    break
                time.sleep(0.02)


def command_validate(config_path: Path) -> None:
    config = load_runtime_config(config_path)
    validate_config(config)
    extra_path = get_extra_config_path(config_path)
    if extra_path.exists():
        logging.info("Config is valid: %s + %s", config_path, extra_path)
    else:
        logging.info("Config is valid: %s", config_path)


def command_list_windows() -> None:
    windows = sorted(get_visible_windows(), key=lambda entry: entry[1].casefold())
    if not windows:
        logging.warning("No visible windows found.")
        return
    for hwnd, title in windows:
        logging.info("0x%08X %s", hwnd, title)


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    bot: IdleHomeBot | None = None

    try:
        if args.command == "list-windows":
            command_list_windows()
            return 0

        config_path = Path(args.config)
        if args.command == "validate":
            command_validate(config_path)
            return 0

        if args.command == "calibrate":
            config = load_config(config_path)
        else:
            config = load_runtime_config(config_path)
        validate_config(config)

        bot = IdleHomeBot(
            config,
            config_dir=config_path.parent,
            allow_size_mismatch=getattr(args, "allow_size_mismatch", False),
            dry_run=getattr(args, "dry_run", False),
        )

        if args.command == "calibrate":
            bot.calibrate()
            save_config(config_path, bot.config)
            logging.info("Saved calibrated points to %s", config_path)
            return 0

        if args.command == "run":
            if args.startup_delay is not None:
                bot.config["timing"]["startup_delay_sec"] = float(args.startup_delay)
            bot.run_forever(once=args.once)
            return 0

        if args.command == "run-sequence":
            bot.run_named_sequence(args.sequence_name, args.startup_delay)
            return 0

        if args.command == "run-wheel":
            bot.run_wheel(args.delta, args.repeat, args.pause, args.startup_delay)
            return 0

        raise BotError(f"Unknown command: {args.command}")
    except AbortRequested as exc:
        logging.warning(str(exc))
        return 130
    except BotError as exc:
        if bot is not None and not getattr(args, "dry_run", False):
            bot.capture_failure_artifacts(str(exc))
        logging.error(str(exc))
        return 1
    except KeyboardInterrupt:
        logging.warning("Interrupted by user.")
        return 130
    finally:
        if bot is not None:
            bot.close()


if __name__ == "__main__":
    sys.exit(main())
