import copy
import ctypes
import logging
import queue
import re
import sys
import threading
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

import idle_home_bot as botlib


APP_VERSION = "0.0.2"
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
CYCLE_START_RE = re.compile(r"\bCycle (\d+) start\b")

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
    ]

    def __init__(self, root: tk.Tk, config_path: Path) -> None:
        self.root = root
        self.config_path = config_path
        self.extra_config_path = botlib.get_extra_config_path(config_path)
        self.local_config_path = botlib.get_local_config_path(config_path)
        self.base_config: dict[str, Any] = {}
        self.base_runtime_config: dict[str, Any] = {}
        self.local_override_config: dict[str, Any] = {}
        self.runtime_config: dict[str, Any] = {}
        self.field_vars: dict[FieldSpec, tk.StringVar] = {}
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.event_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.stop_event: threading.Event | None = None
        self.worker_thread: threading.Thread | None = None
        self.running_mode: str | None = None
        self.hotkeys = GlobalHotkeyListener(self.event_queue)
        self.log_handler = TextQueueHandler(self.log_queue)
        self.status_var = tk.StringVar(value="Idle")
        self.current_cycle_var = tk.StringVar(value="-")
        self.config_info_var = tk.StringVar()
        self.allow_size_mismatch_var = tk.BooleanVar(value=False)

        self.root.title(f"Idle Home Bot v{APP_VERSION}")
        self.root.geometry("1320x840")
        self.root.minsize(1180, 720)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.reload_config_state()
        self.configure_logging()
        self.build_ui()
        self.load_fields_from_config(self.runtime_config)
        self.hotkeys.start()
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

        ttk.Button(controls, text="Reload", command=self.reload_from_disk).pack(side="left")
        ttk.Button(controls, text="Save Local", command=self.save_to_disk).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Run Loop (F1)", command=lambda: self.start_runner("loop")).pack(side="left", padx=(16, 0))
        ttk.Button(controls, text="Run Once", command=lambda: self.start_runner("once")).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Stop (F2)", command=self.request_stop).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Test Pickup", command=lambda: self.start_runner("sequence", "pickup_sword")).pack(side="left", padx=(16, 0))
        ttk.Button(controls, text="Test Move", command=lambda: self.start_runner("sequence", "move_to_combat_position")).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Test Attack", command=lambda: self.start_runner("sequence", "enable_auto_attack")).pack(side="left", padx=(8, 0))
        ttk.Button(controls, text="Test Ascend", command=lambda: self.start_runner("sequence", "ascend")).pack(side="left", padx=(8, 0))
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
        ttk.Label(top, textvariable=self.config_info_var).pack(fill="x", pady=(2, 8))

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

    def reload_config_state(self) -> None:
        self.base_config = botlib.load_config(self.config_path)
        self.base_runtime_config = botlib.load_runtime_config(self.config_path, include_local=False)
        self.runtime_config = botlib.load_runtime_config(self.config_path, include_local=True)
        if self.local_config_path.exists():
            self.local_override_config = botlib.load_config(self.local_config_path)
        else:
            self.local_override_config = {}
        extra_name = self.extra_config_path.name if self.extra_config_path.exists() else "-"
        local_name = self.local_config_path.name
        local_state = "present" if self.local_config_path.exists() else "missing"
        self.config_info_var.set(
            f"Base: {self.config_path.name} | Extra: {extra_name} | Local: {local_name} ({local_state})"
        )

    def reload_from_disk(self) -> None:
        if self.is_running():
            messagebox.showwarning("Busy", "Stop the runner before reloading the config.")
            return
        self.reload_config_state()
        self.load_fields_from_config(self.runtime_config)
        logging.info("Reloaded %s (+ local overrides if present)", self.config_path.name)

    def save_to_disk(self) -> None:
        try:
            desired_config = self.build_config_from_fields()
            local_override = self.build_local_override_config(desired_config)
            if local_override:
                botlib.save_config(self.local_config_path, local_override)
                logging.info("Saved %s", self.local_config_path.name)
            else:
                if self.local_config_path.exists():
                    self.local_config_path.unlink()
                logging.info("Removed %s (no local overrides)", self.local_config_path.name)
            self.reload_config_state()
            self.load_fields_from_config(self.runtime_config)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            logging.error("Save failed: %s", exc)
            return

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

        self.stop_event = threading.Event()
        self.running_mode = mode if sequence_name is None else f"{mode}:{sequence_name}"
        self.status_var.set(f"Running: {self.running_mode}")
        self.current_cycle_var.set("-")
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
        logging.warning("Stop requested.")

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
            logging.error(str(exc))
        except Exception:
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
                logging.info("Runner finished.")
            elif event_type == "log":
                self.append_log(payload)

        self.root.after(100, self.process_queues)

    def append_log(self, message: str) -> None:
        match = CYCLE_START_RE.search(message)
        if match:
            self.current_cycle_var.set(match.group(1))
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def load_fields_from_config(self, config: dict[str, Any]) -> None:
        for spec, var in self.field_vars.items():
            value = self.get_by_path(config, spec.path)
            var.set(str(value))

    def build_config_from_fields(self) -> dict[str, Any]:
        config = copy.deepcopy(self.runtime_config)
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

    def build_local_override_config(self, desired_config: dict[str, Any]) -> dict[str, Any]:
        override = copy.deepcopy(self.local_override_config)

        timing_keys = {
            str(spec.path[1]) for _, specs in self.FIELD_GROUPS for spec in specs if spec.path[0] == "timing"
        }
        for key in timing_keys:
            desired_value = desired_config["timing"][key]
            base_value = self.base_runtime_config["timing"][key]
            if desired_value == base_value:
                if "timing" in override:
                    override["timing"].pop(key, None)
            else:
                override.setdefault("timing", {})[key] = desired_value
        if isinstance(override.get("timing"), dict) and not override["timing"]:
            override.pop("timing", None)

        sequence_names = {
            str(spec.path[1]) for _, specs in self.FIELD_GROUPS for spec in specs if spec.path[0] == "sequences"
        }
        for sequence_name in sequence_names:
            desired_sequence = desired_config["sequences"][sequence_name]
            base_sequence = self.base_runtime_config["sequences"][sequence_name]
            if desired_sequence == base_sequence:
                if "sequences" in override:
                    override["sequences"].pop(sequence_name, None)
            else:
                override.setdefault("sequences", {})[sequence_name] = copy.deepcopy(desired_sequence)
        if isinstance(override.get("sequences"), dict) and not override["sequences"]:
            override.pop("sequences", None)

        return override

    def get_by_path(self, data: Any, path: tuple[Any, ...]) -> Any:
        current = data
        for part in path:
            current = current[part]
        return current

    def set_by_path(self, data: Any, path: tuple[Any, ...], value: Any) -> None:
        current = data
        for part in path[:-1]:
            current = current[part]
        current[path[-1]] = value

    def on_close(self) -> None:
        if self.is_running():
            if not messagebox.askyesno("Exit", "Runner is active. Stop and close?"):
                return
            self.request_stop()
        self.hotkeys.stop()
        logging.getLogger().removeHandler(self.log_handler)
        self.root.destroy()


def main() -> int:
    root = tk.Tk()
    if getattr(sys, "frozen", False):
        base_dir = Path(sys.executable).resolve().parent
    else:
        base_dir = Path(__file__).resolve().parent
    config_path = base_dir / "idle_home_config.json"
    IdleHomeGuiApp(root, config_path)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
