import copy
import ctypes
import html
import json
import logging
import queue
import re
import socket
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any
from urllib.parse import urlparse

import idle_home_bot as botlib


APP_VERSION = "0.0.5"
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
CYCLE_START_RE = re.compile(r"\bCycle (\d+) start\b")
SEQUENCE_RE = re.compile(r"\bSequence ([A-Za-z0-9_]+)\b")
STATUS_SERVER_PORT = 8787
STATUS_SERVER_PORT_ATTEMPTS = 10

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


@dataclass(frozen=True)
class FieldSpec:
    label: str
    path: tuple[Any, ...]
    cast: type


class TextQueueHandler(logging.Handler):
    def __init__(self, target_queue: queue.Queue[str]) -> None:
        super().__init__()
        self.target_queue = target_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.target_queue.put(self.format(record))
        except Exception:
            self.handleError(record)


class GlobalHotkeyListener(threading.Thread):
    def __init__(self, event_queue: queue.Queue[tuple[str, str]]) -> None:
        super().__init__(daemon=True)
        self.event_queue = event_queue
        self.thread_id: int | None = None

    def run(self) -> None:
        self.thread_id = int(kernel32.GetCurrentThreadId())
        registered: list[int] = []
        try:
            for hotkey_id, key_name in ((1, "F1"), (2, "F2")):
                success = user32.RegisterHotKey(None, hotkey_id, MOD_NOREPEAT, botlib.key_to_vk(key_name))
                if success:
                    registered.append(hotkey_id)
                else:
                    self.event_queue.put(("log", f"WARNING Could not register global hotkey {key_name}."))

            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
                if msg.message == WM_HOTKEY:
                    if msg.wParam == 1:
                        self.event_queue.put(("hotkey", "start"))
                    elif msg.wParam == 2:
                        self.event_queue.put(("hotkey", "stop"))
        finally:
            for hotkey_id in registered:
                user32.UnregisterHotKey(None, hotkey_id)

    def stop(self) -> None:
        if self.thread_id is not None:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


def format_status_timestamp(timestamp: float | None) -> str:
    if timestamp is None:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def discover_status_hosts() -> list[str]:
    hosts: list[str] = []
    seen: set[str] = set()

    def add_host(address: str) -> None:
        if not address or address in seen or address.startswith("169.254."):
            return
        seen.add(address)
        hosts.append(address)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            add_host(sock.getsockname()[0])
    except OSError:
        pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_STREAM):
            add_host(info[4][0])
    except OSError:
        pass

    add_host("127.0.0.1")
    return hosts


class GuiStatusStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status = "Idle"
        self._running_mode: str | None = None
        self._current_cycle = "-"
        self._current_sequence = "-"
        self._last_log_line = ""
        self._last_error = ""
        self._last_update_ts: float | None = None
        self._config_path = ""
        self._failure_dir = Path.cwd() / "failure_captures"

    def set_config_path(self, config_path: Path) -> None:
        with self._lock:
            self._config_path = str(config_path)
            self._failure_dir = config_path.parent / "failure_captures"
            self._last_update_ts = time.time()

    def mark_runner_started(self, mode: str) -> None:
        with self._lock:
            self._status = f"Running: {mode}"
            self._running_mode = mode
            self._current_cycle = "-"
            self._current_sequence = "-"
            self._last_error = ""
            self._last_update_ts = time.time()

    def mark_stop_requested(self) -> None:
        with self._lock:
            self._status = "Stopping..."
            self._last_update_ts = time.time()

    def mark_idle(self) -> None:
        with self._lock:
            self._status = "Idle"
            self._running_mode = None
            self._current_cycle = "-"
            self._current_sequence = "-"
            self._last_update_ts = time.time()

    def update_from_log(self, message: str) -> None:
        clean = message.strip()
        if not clean:
            return
        with self._lock:
            self._last_log_line = clean
            self._last_update_ts = time.time()
            cycle_match = CYCLE_START_RE.search(clean)
            if cycle_match:
                self._current_cycle = cycle_match.group(1)
            sequence_match = SEQUENCE_RE.search(clean)
            if sequence_match:
                self._current_sequence = sequence_match.group(1)
            if " ERROR " in clean or clean.startswith("ERROR ") or re.search(r"\bERROR\b", clean):
                self._last_error = clean

    def latest_failure_image_path(self) -> Path | None:
        with self._lock:
            failure_dir = self._failure_dir
        if not failure_dir.exists():
            return None
        candidates = [path for path in failure_dir.glob("*.png") if "_snap" not in path.stem]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            data = {
                "status": self._status,
                "running_mode": self._running_mode,
                "current_cycle": self._current_cycle,
                "current_sequence": self._current_sequence,
                "last_log_line": self._last_log_line,
                "last_error": self._last_error,
                "last_update_ts": self._last_update_ts,
                "last_update": format_status_timestamp(self._last_update_ts),
                "config_path": self._config_path,
                "failure_dir": str(self._failure_dir),
            }
        failure_image = self.latest_failure_image_path()
        data["latest_failure_image"] = failure_image.name if failure_image is not None else None
        if failure_image is not None:
            data["latest_failure_image_mtime"] = failure_image.stat().st_mtime
        else:
            data["latest_failure_image_mtime"] = None
        return data


class IdleHomeStatusHttpServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], status_store: GuiStatusStore) -> None:
        super().__init__(server_address, StatusRequestHandler)
        self.status_store = status_store


class StatusRequestHandler(BaseHTTPRequestHandler):
    server_version = "IdleHomeStatus/1.0"

    @property
    def status_store(self) -> GuiStatusStore:
        return self.server.status_store  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self.serve_index()
            return
        if path == "/status.json":
            self.serve_json()
            return
        if path == "/latest-failure.png":
            self.serve_latest_failure_image()
            return
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def serve_index(self) -> None:
        snapshot = self.status_store.snapshot()
        image_tag = "<p>Latest failure image: none</p>"
        if snapshot["latest_failure_image"] is not None:
            image_tag = (
                '<p>Latest failure image</p>'
                f'<img src="/latest-failure.png?ts={int(snapshot["latest_failure_image_mtime"] or 0)}" '
                'style="max-width: 100%; border: 1px solid #bbb; border-radius: 6px;" alt="latest failure screenshot">'
            )

        html_body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="3">
  <title>Idle Home Bot Status</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 16px; line-height: 1.4; }}
    .grid {{ display: grid; grid-template-columns: 160px 1fr; gap: 8px 12px; margin-bottom: 16px; }}
    .label {{ color: #555; font-weight: 600; }}
    .mono {{ font-family: ui-monospace, Consolas, monospace; white-space: pre-wrap; word-break: break-word; }}
    .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 12px; margin-bottom: 16px; }}
  </style>
</head>
<body>
  <h1>Idle Home Bot Status</h1>
  <div class="card">
    <div class="grid">
      <div class="label">Status</div><div>{html.escape(str(snapshot["status"]))}</div>
      <div class="label">Current Cycle</div><div>{html.escape(str(snapshot["current_cycle"]))}</div>
      <div class="label">Current Sequence</div><div>{html.escape(str(snapshot["current_sequence"]))}</div>
      <div class="label">Last Update</div><div>{html.escape(str(snapshot["last_update"]))}</div>
      <div class="label">Config</div><div class="mono">{html.escape(str(snapshot["config_path"]))}</div>
      <div class="label">Failure Dir</div><div class="mono">{html.escape(str(snapshot["failure_dir"]))}</div>
    </div>
  </div>
  <div class="card">
    <div class="label">Last Log</div>
    <div class="mono">{html.escape(str(snapshot["last_log_line"])) or "-"}</div>
  </div>
  <div class="card">
    <div class="label">Last Error</div>
    <div class="mono">{html.escape(str(snapshot["last_error"])) or "-"}</div>
  </div>
  <div class="card">
    {image_tag}
  </div>
</body>
</html>
"""
        body = html_body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_json(self) -> None:
        body = json.dumps(self.status_store.snapshot(), ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_latest_failure_image(self) -> None:
        image_path = self.status_store.latest_failure_image_path()
        if image_path is None or not image_path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "No failure image")
            return
        body = image_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StatusServerThread(threading.Thread):
    def __init__(self, status_store: GuiStatusStore) -> None:
        super().__init__(daemon=True)
        self.status_store = status_store
        self.httpd: IdleHomeStatusHttpServer | None = None
        self.port: int | None = None
        self.urls: list[str] = []
        self.ready = threading.Event()

    def run(self) -> None:
        for port in range(STATUS_SERVER_PORT, STATUS_SERVER_PORT + STATUS_SERVER_PORT_ATTEMPTS):
            try:
                self.httpd = IdleHomeStatusHttpServer(("0.0.0.0", port), self.status_store)
                self.port = port
                break
            except OSError:
                continue

        if self.httpd is None or self.port is None:
            self.ready.set()
            logging.warning(
                "Could not start LAN status page on ports %s-%s.",
                STATUS_SERVER_PORT,
                STATUS_SERVER_PORT + STATUS_SERVER_PORT_ATTEMPTS - 1,
            )
            return

        self.urls = [f"http://{host}:{self.port}/" for host in discover_status_hosts()]
        self.ready.set()
        logging.info("Status page available at %s", " | ".join(self.urls))
        try:
            self.httpd.serve_forever(poll_interval=0.5)
        finally:
            self.httpd.server_close()

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()


class IdleHomeGuiApp:
    FIELD_GROUPS: list[tuple[str, list[FieldSpec]]] = [
        (
            "Timing",
            [
                FieldSpec("Startup Delay", ("timing", "startup_delay_sec"), float),
                FieldSpec("Combat Duration", ("timing", "combat_duration_sec"), float),
                FieldSpec("After Cycle Wait", ("timing", "after_cycle_wait_sec"), float),
            ],
        ),
        (
            "Pickup",
            [
                FieldSpec("Pickup Forward W", ("sequences", "pickup_sword", 0, "seconds"), float),
                FieldSpec("Pickup Right D", ("sequences", "pickup_sword", 1, "seconds"), float),
                FieldSpec("Pickup Pre-Vision", ("sequences", "pickup_sword", 2, "seconds"), float),
                FieldSpec("Pickup Wait After", ("sequences", "pickup_sword", 4, "seconds"), float),
                FieldSpec("Wheel Delta", ("sequences", "pickup_sword", 5, "delta"), int),
                FieldSpec("Wheel Repeat", ("sequences", "pickup_sword", 5, "repeat"), int),
                FieldSpec("Wheel Pause", ("sequences", "pickup_sword", 5, "pause_sec"), float),
                FieldSpec("Middle Drag Y", ("sequences", "pickup_sword", 7, "dy"), int),
                FieldSpec("Middle Drag Steps", ("sequences", "pickup_sword", 7, "steps"), int),
                FieldSpec("Restore Wait", ("sequences", "pickup_sword", 3, "restore_wait_sec"), float),
            ],
        ),
        (
            "Combat Move",
            [
                FieldSpec("Back S", ("sequences", "move_to_combat_position", 0, "seconds"), float),
                FieldSpec("Turn DX", ("sequences", "move_to_combat_position", 1, "dx"), int),
                FieldSpec("Forward W", ("sequences", "move_to_combat_position", 2, "seconds"), float),
            ],
        ),
        (
            "Auto Attack",
            [
                FieldSpec("Enable Wait After R", ("sequences", "enable_auto_attack", 1, "seconds"), float),
                FieldSpec("Enable DX", ("sequences", "enable_auto_attack", 2, "dx"), int),
                FieldSpec("Enable DY", ("sequences", "enable_auto_attack", 2, "dy"), int),
                FieldSpec("Enable Wait Before Click", ("sequences", "enable_auto_attack", 3, "seconds"), float),
                FieldSpec("Disable Wait Before ESC", ("sequences", "disable_auto_attack", 1, "seconds"), float),
            ],
        ),
        (
            "Ascend",
            [
                FieldSpec("Ascend Back S", ("sequences", "ascend", 0, "seconds"), float),
                FieldSpec("Ascend Turn DX", ("sequences", "ascend", 1, "dx"), int),
                FieldSpec("Ascend Forward W", ("sequences", "ascend", 2, "seconds"), float),
                FieldSpec("Ascend Pre-Vision", ("sequences", "ascend", 3, "seconds"), float),
                FieldSpec("Next Click Count", ("sequences", "ascend", 4, "click_repeat"), int),
                FieldSpec("To Astral DX", ("sequences", "ascend", 6, "dx"), int),
                FieldSpec("To Astral DY", ("sequences", "ascend", 6, "dy"), int),
                FieldSpec("Astral Wait", ("sequences", "ascend", 8, "seconds"), float),
                FieldSpec("Final DX", ("sequences", "ascend", 9, "dx"), int),
                FieldSpec("Final DY", ("sequences", "ascend", 9, "dy"), int),
            ],
        ),
        (
            "Advanced",
            [
                FieldSpec("Pickup Threshold", ("sequences", "pickup_sword", 3, "threshold"), float),
                FieldSpec("Pickup Candidate", ("sequences", "pickup_sword", 3, "candidate_threshold"), float),
                FieldSpec("Pickup Tracking", ("sequences", "pickup_sword", 3, "tracking_threshold"), float),
                FieldSpec("Ascend Candidate", ("sequences", "ascend", 4, "candidate_threshold"), float),
                FieldSpec("Ascend Tracking", ("sequences", "ascend", 4, "tracking_threshold"), float),
                FieldSpec("Ascend Fallback", ("sequences", "ascend", 4, "centered_fallback_threshold"), float),
            ],
        ),
        (
            "Notifications",
            [
                FieldSpec("ntfy Server", ("notifications", "ntfy", "server"), str),
                FieldSpec("ntfy Topic", ("notifications", "ntfy", "topic"), str),
                FieldSpec("ntfy Priority", ("notifications", "ntfy", "priority"), str),
                FieldSpec("ntfy Tags", ("notifications", "ntfy", "tags"), str),
            ],
        ),
    ]

    def __init__(self, root: tk.Tk, config_path: Path) -> None:
        self.root = root
        self.default_config_path = config_path
        self.config_path = config_path
        self.base_config = botlib.load_config(config_path)
        self.field_vars: dict[FieldSpec, tk.StringVar] = {}
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.event_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.stop_event: threading.Event | None = None
        self.worker_thread: threading.Thread | None = None
        self.running_mode: str | None = None
        self.hotkeys = GlobalHotkeyListener(self.event_queue)
        self.log_handler = TextQueueHandler(self.log_queue)
        self.status_store = GuiStatusStore()
        self.status_server = StatusServerThread(self.status_store)
        self.status_var = tk.StringVar(value="Idle")
        self.current_cycle_var = tk.StringVar(value="-")
        self.config_label_var = tk.StringVar()
        self.status_page_var = tk.StringVar(value="Status Page: starting...")
        self.allow_size_mismatch_var = tk.BooleanVar(value=False)
        self.status_store.set_config_path(config_path)

        self.root.title(f"Idle Home Bot v{APP_VERSION}")
        self.root.geometry("1320x840")
        self.root.minsize(1180, 720)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.configure_logging()
        self.build_ui()
        self.refresh_config_label()
        self.load_fields_from_config(self.base_config)
        self.hotkeys.start()
        self.status_server.start()
        self.status_server.ready.wait(1.0)
        self.refresh_status_page_label()
        self.root.after(100, self.process_queues)
        logging.info("GUI ready. F1=start loop, F2=stop.")

    def configure_logging(self) -> None:
        self.log_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
        )
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(self.log_handler)

    def build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="both", expand=True)

        controls = ttk.Frame(top)
        controls.pack(fill="x")

        ttk.Button(controls, text="Open Config...", command=self.select_config).pack(side="left")
        ttk.Button(controls, text="Use Default", command=self.use_default_config).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Reload", command=self.reload_from_disk).pack(side="left")
        ttk.Button(controls, text="Save", command=self.save_to_disk).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Run Loop (F1)", command=lambda: self.start_runner("loop")).pack(side="left", padx=(16, 0))
        ttk.Button(controls, text="Run Once", command=lambda: self.start_runner("once")).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Stop (F2)", command=self.request_stop).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Test Pickup", command=lambda: self.start_runner("sequence", "pickup_sword")).pack(side="left", padx=(16, 0))
        ttk.Button(controls, text="Test Move", command=lambda: self.start_runner("sequence", "move_to_combat_position")).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Test Attack", command=lambda: self.start_runner("sequence", "enable_auto_attack")).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Test Ascend", command=lambda: self.start_runner("sequence", "ascend")).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Test Notification", command=self.send_test_notification).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(
            controls,
            text="Allow Size Mismatch",
            variable=self.allow_size_mismatch_var,
        ).pack(side="right")

        status_row = ttk.Frame(top)
        status_row.pack(fill="x", pady=(8, 0))
        ttk.Label(status_row, textvariable=self.status_var).pack(side="left")
        ttk.Label(status_row, text="Current Cycle:").pack(side="right", padx=(0, 6))
        ttk.Label(status_row, textvariable=self.current_cycle_var).pack(side="right")
        ttk.Label(top, textvariable=self.config_label_var).pack(fill="x", pady=(2, 8))
        ttk.Label(top, textvariable=self.status_page_var, wraplength=1240, justify="left").pack(fill="x", pady=(0, 8))

        main = ttk.Panedwindow(top, orient="horizontal")
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main, padding=(0, 0, 10, 0))
        right = ttk.Frame(main)
        main.add(left, weight=3)
        main.add(right, weight=2)

        notebook = ttk.Notebook(left)
        notebook.pack(fill="both", expand=True)

        for group_name, specs in self.FIELD_GROUPS:
            frame = ttk.Frame(notebook, padding=12)
            notebook.add(frame, text=group_name)
            for row_index, spec in enumerate(specs):
                ttk.Label(frame, text=spec.label).grid(row=row_index, column=0, sticky="w", padx=(0, 10), pady=4)
                var = tk.StringVar()
                self.field_vars[spec] = var
                ttk.Entry(frame, textvariable=var, width=14).grid(row=row_index, column=1, sticky="ew", pady=4)
            frame.columnconfigure(1, weight=1)

        ttk.Label(right, text="Log").pack(anchor="w")
        self.log_text = tk.Text(right, wrap="word", height=30, state="disabled")
        self.log_text.pack(fill="both", expand=True)

    def refresh_config_label(self) -> None:
        self.config_label_var.set(f"Config: {self.config_path}")
        self.status_store.set_config_path(self.config_path)

    def refresh_status_page_label(self) -> None:
        if not self.status_server.urls:
            self.status_page_var.set("Status Page: unavailable")
            return
        loopback_urls = [url for url in self.status_server.urls if "127.0.0.1" in url]
        lan_urls = [url for url in self.status_server.urls if "127.0.0.1" not in url]
        parts: list[str] = []
        if loopback_urls:
            parts.append(f"Status Page: {loopback_urls[0]}")
        if lan_urls:
            parts.append("LAN: " + " | ".join(lan_urls))
        self.status_page_var.set("    ".join(parts))

    def load_selected_config(self, path: Path) -> None:
        self.base_config = botlib.load_config(path)
        self.config_path = path
        self.refresh_config_label()
        self.load_fields_from_config(self.base_config)

    def select_config(self) -> None:
        if self.is_running():
            messagebox.showwarning("Busy", "Stop the runner before switching the config.")
            return
        selected = filedialog.askopenfilename(
            title="Open Config",
            initialdir=str(self.config_path.parent),
            initialfile=self.config_path.name,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not selected:
            return
        path = Path(selected)
        try:
            self.load_selected_config(path)
        except Exception as exc:
            messagebox.showerror("Open config failed", str(exc))
            logging.error("Open config failed: %s", exc)
            return
        logging.info("Loaded %s", self.config_path)

    def use_default_config(self) -> None:
        if self.is_running():
            messagebox.showwarning("Busy", "Stop the runner before switching the config.")
            return
        if self.config_path == self.default_config_path:
            return
        try:
            self.load_selected_config(self.default_config_path)
        except Exception as exc:
            messagebox.showerror("Load default failed", str(exc))
            logging.error("Load default failed: %s", exc)
            return
        logging.info("Loaded %s", self.config_path)

    def reload_from_disk(self) -> None:
        if self.is_running():
            messagebox.showwarning("Busy", "Stop the runner before reloading the config.")
            return
        self.load_selected_config(self.config_path)
        logging.info("Reloaded %s", self.config_path)

    def save_to_disk(self) -> None:
        try:
            self.base_config = self.build_config_from_fields()
            botlib.save_config(self.config_path, self.base_config)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            logging.error("Save failed: %s", exc)
            return
        self.refresh_config_label()
        logging.info("Saved %s", self.config_path)

    def is_running(self) -> bool:
        return self.worker_thread is not None and self.worker_thread.is_alive()

    def start_runner(self, mode: str, sequence_name: str | None = None) -> None:
        if self.is_running():
            logging.warning("Runner is already active.")
            return
        try:
            config = self.build_config_from_fields()
            botlib.validate_config(config)
        except Exception as exc:
            messagebox.showerror("Invalid config", str(exc))
            logging.error("Invalid config: %s", exc)
            return

        config["runtime_status_urls"] = [url for url in self.status_server.urls if "127.0.0.1" not in url]

        self.stop_event = threading.Event()
        self.running_mode = mode if sequence_name is None else f"{mode}:{sequence_name}"
        self.status_var.set(f"Running: {self.running_mode}")
        self.current_cycle_var.set("-")
        self.status_store.mark_runner_started(self.running_mode)
        self.worker_thread = threading.Thread(
            target=self.worker_main,
            args=(config, mode, sequence_name),
            daemon=True,
        )
        self.worker_thread.start()

    def request_stop(self) -> None:
        if self.stop_event is None:
            logging.info("Stop requested, but runner is idle.")
            return
        self.stop_event.set()
        self.status_var.set("Stopping...")
        self.status_store.mark_stop_requested()
        logging.warning("Stop requested.")

    def send_test_notification(self) -> None:
        try:
            config = self.build_config_from_fields()
        except Exception as exc:
            messagebox.showerror("Invalid config", str(exc))
            logging.error("Invalid config: %s", exc)
            return

        topic = str(config.get("notifications", {}).get("ntfy", {}).get("topic", "")).strip()
        if not topic:
            messagebox.showwarning("ntfy disabled", "Set ntfy Topic before sending a test notification.")
            logging.warning("Test notification skipped because ntfy Topic is empty.")
            return

        config["runtime_status_urls"] = [url for url in self.status_server.urls if "127.0.0.1" not in url]
        bot = botlib.IdleHomeBot(
            config,
            config_dir=self.config_path.parent,
            allow_size_mismatch=self.allow_size_mismatch_var.get(),
            dry_run=True,
        )
        try:
            try:
                bot.current_cycle = int(self.current_cycle_var.get())
            except ValueError:
                bot.current_cycle = 0
            bot.current_sequence = self.status_store.snapshot().get("current_sequence") or None
            bot.current_action_type = "test_notification"
            urls = [url for url in config.get("runtime_status_urls", []) if str(url).strip()]
            message_lines = [
                f"PC: {socket.gethostname()}",
                f"Status: {self.status_var.get()}",
                f"Cycle: {self.current_cycle_var.get()}",
                f"Sequence: {bot.current_sequence or '-'}",
                "This is a manual test notification.",
            ]
            if urls:
                message_lines.append(f"Status Page: {urls[0]}")
            if bot.send_ntfy_notification("Idle Home Bot test", "\n".join(message_lines)):
                logging.info("Test notification sent.")
            else:
                logging.warning("Test notification was not sent.")
        finally:
            bot.close()

    def worker_main(self, config: dict[str, Any], mode: str, sequence_name: str | None) -> None:
        bot = botlib.IdleHomeBot(
            config,
            config_dir=self.config_path.parent,
            allow_size_mismatch=self.allow_size_mismatch_var.get(),
            dry_run=False,
            abort_event=self.stop_event,
        )
        try:
            if mode == "loop":
                bot.run_forever(once=False)
            elif mode == "once":
                bot.run_forever(once=True)
            elif mode == "sequence" and sequence_name is not None:
                bot.run_named_sequence(sequence_name, None)
            else:
                raise botlib.BotError(f"Unsupported GUI run mode: {mode}")
        except botlib.AbortRequested as exc:
            logging.warning(str(exc))
        except botlib.BotError as exc:
            bot.capture_failure_artifacts(str(exc))
            bot.send_failure_notification(str(exc))
            logging.error(str(exc))
        except Exception:
            bot.capture_failure_artifacts("Unexpected GUI runner error.")
            bot.send_failure_notification("Unexpected GUI runner error.")
            logging.exception("Unexpected GUI runner error.")
        finally:
            bot.close()
            self.event_queue.put(("runner", "finished"))

    def process_queues(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.append_log(message)

        while True:
            try:
                event_type, payload = self.event_queue.get_nowait()
            except queue.Empty:
                break
            if event_type == "hotkey":
                if payload == "start":
                    if not self.is_running():
                        logging.info("Global hotkey F1 received.")
                        self.start_runner("loop")
                elif payload == "stop":
                    logging.info("Global hotkey F2 received.")
                    self.request_stop()
            elif event_type == "runner":
                self.status_var.set("Idle")
                self.current_cycle_var.set("-")
                self.stop_event = None
                self.worker_thread = None
                self.running_mode = None
                self.status_store.mark_idle()
                logging.info("Runner finished.")
            elif event_type == "log":
                self.append_log(payload)

        self.refresh_status_page_label()
        self.root.after(100, self.process_queues)

    def append_log(self, message: str) -> None:
        self.status_store.update_from_log(message)
        match = CYCLE_START_RE.search(message)
        if match:
            self.current_cycle_var.set(match.group(1))
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def load_fields_from_config(self, config: dict[str, Any]) -> None:
        for spec, var in self.field_vars.items():
            try:
                value = self.get_by_path(config, spec.path)
            except (KeyError, IndexError, TypeError):
                value = ""
            var.set(str(value))

    def build_config_from_fields(self) -> dict[str, Any]:
        config = copy.deepcopy(self.base_config)
        for spec, var in self.field_vars.items():
            raw_value = var.get().strip()
            if raw_value == "":
                raise botlib.BotError(f"{spec.label} cannot be empty.")
            try:
                value = spec.cast(raw_value)
            except ValueError as exc:
                raise botlib.BotError(f"{spec.label} must be a {spec.cast.__name__}.") from exc
            self.set_by_path(config, spec.path, value)
        return config

    def get_by_path(self, data: Any, path: tuple[Any, ...]) -> Any:
        current = data
        for part in path:
            current = current[part]
        return current

    def set_by_path(self, data: Any, path: tuple[Any, ...], value: Any) -> None:
        current = data
        for index, part in enumerate(path[:-1]):
            next_part = path[index + 1]
            if isinstance(part, int):
                while len(current) <= part:
                    current.append({} if isinstance(next_part, str) else [])
            elif part not in current:
                current[part] = {} if isinstance(next_part, str) else []
            current = current[part]
        current[path[-1]] = value

    def on_close(self) -> None:
        if self.is_running():
            if not messagebox.askyesno("Exit", "Runner is active. Stop and close?"):
                return
            self.request_stop()
        self.hotkeys.stop()
        self.status_server.stop()
        logging.getLogger().removeHandler(self.log_handler)
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).resolve().parent
    else:
        base_dir = Path(__file__).resolve().parent
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        requested = Path(sys.argv[1].strip())
        if not requested.is_absolute():
            requested = base_dir / requested
        config_path = requested.resolve()
    else:
        config_path = base_dir / "idle_home_config.json"
    IdleHomeGuiApp(root, config_path)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
