# Idle Home Bot Prototype

This repository contains a configurable Windows desktop macro runner for looping `Idle Home` in `VRChat` desktop mode.

It does not read VRChat memory or inject code. It finds the `VRChat` window, checks for a `1600x900` client area, and then replays a timed state machine:

1. Pick up the sword.
2. Move to the fixed combat position.
3. Enable the avatar auto-attack gimmick.
4. Wait for the configured combat duration.
5. Trigger Astral Ascension.
6. Repeat.

## Files

- `idle_home_bot.py`: main runner and calibrator.
- `idle_home_config.json`: main config for timings and sequences.
- `idle_home_config.extra.json`: optional extra sequences, loaded automatically.
- `launch_gui.bat`: double-click launcher for the tuning GUI when running from a cloned repo.

## Important input note

In `VRChat` desktop mode, many interactions are not driven by the Windows cursor.

- World interactions often use the center reticle.
- Some menus use an internal or virtual cursor.
- In those cases, `GetCursorPos` will stay near the screen center, even though the game appears to move a cursor or view.

That means `calibrate` is only useful when `VRChat` is showing a true cursor-driven UI.

For world interactions, prefer:

- `left_click` with no target, which clicks at the current in-game focus.
- `mouse_move_relative`, which sends relative mouse movement instead of forcing the Windows cursor to an absolute screen position.

## Commands

Validate the config:

```powershell
python .\idle_home_bot.py validate
```

List visible windows:

```powershell
python .\idle_home_bot.py list-windows
```

Capture cursor-based points:

```powershell
python .\idle_home_bot.py calibrate
```

Run one cycle without sending real input:

```powershell
python .\idle_home_bot.py run --once --dry-run
```

Run one sequence for tuning:

```powershell
python .\idle_home_bot.py run-sequence enable_auto_attack --startup-delay 3
```

Run a scratch test sequence from the extra config:

```powershell
python .\idle_home_bot.py run-sequence test --startup-delay 3
```

Run a wheel-only test:

```powershell
python .\idle_home_bot.py run-wheel 120 --repeat 10 --startup-delay 3
```

Run the loop:

```powershell
python .\idle_home_bot.py run
```

Run the tuning GUI:

```powershell
python .\idle_home_gui.py
```

Or double-click:

```text
launch_gui.bat
```

When the GUI is open, it also starts a small LAN status page. You can open the shown URL from another device on the same network, such as an iPhone in Safari, to check:

- Running / Stopped
- Current cycle
- Current sequence
- Last log line
- Last error
- Latest failure screenshot

Optional stop notifications can be sent through `ntfy`.

- Set `notifications.ntfy.topic` in the selected config to enable it
- Leave `notifications.ntfy.topic` empty to disable it
- The bot sends a notification when it stops on a `BotError`
- The GUI also has a `Test Notification` button for a manual push test

Or open a specific config file:

```powershell
.\launch_gui.bat idle_home_config_4790K.json
```

Or use the dedicated launcher:

```text
launch_gui_4790K.bat
```

Build a Windows release package:

```powershell
.\build_release.bat 0.0.5
```

This creates:

- `release\IdleHomeBot-v0.0.5\`
- `release\IdleHomeBot-v0.0.5.zip`

After the build, both of these should be runnable because the config and templates are copied into them:

- `dist\IdleHomeBotGUI\IdleHomeBotGUI.exe`
- `release\IdleHomeBot-v0.0.5\IdleHomeBotGUI.exe`

## Calibration workflow

Use this only for true cursor-based UI. If every capture comes back around `800,450` on a `1600x900` client, that is expected for center-locked desktop input, and you should switch to relative mouse actions instead of point capture.

1. Launch `VRChat` in desktop mode.
2. Set the client area to `1600x900`.
3. Place your character at the exact start point for the loop.
4. Run `python .\idle_home_bot.py calibrate`.
5. For each point name, move the cursor over the target inside `VRChat` and press `F8`.
6. Use `F12` at any time to abort.

For the current `Idle Home` flow described here, you will usually skip this step and tune sequences directly.

## Config structure

`timing`

- `startup_delay_sec`: countdown before the loop starts.
- `combat_duration_sec`: how long to wait after enabling the auto-attack.
- `after_cycle_wait_sec`: delay between completed loops.

`sequences`

Each sequence is a list of actions. Supported action types:

- `wait`
- `mouse_move`
- `mouse_move_relative`
- `mouse_drag_relative`
- `mouse_wheel`
- `vision_center_click`
- `vision_wait_absent`
- `pattern_click`
- `left_click`
- `right_click`
- `key_tap`
- `key_hold`

Examples:

```json
{ "type": "wait", "seconds": 0.5 }
{ "type": "mouse_move_relative", "dx": 320, "dy": -120, "steps": 8, "step_delay_ms": 15 }
{ "type": "mouse_drag_relative", "button": "middle", "dx": 0, "dy": 540, "steps": 16, "step_delay_ms": 16 }
{ "type": "mouse_wheel", "delta": 120, "repeat": 10, "pause_sec": 0.02 }
{ "type": "vision_center_click", "template": "templates/sword_pickup.png", "anchor_x": 190, "anchor_y": 280 }
{ "type": "vision_wait_absent", "template": "templates/ascend_confirm_button.png", "absence_threshold": 0.55, "initial_wait_sec": 5.0 }
{ "type": "pattern_click", "offsets": [[0, 0], [20, 0], [-20, 0], [0, 20], [0, -20]], "pause_sec": 0.03 }
{ "type": "left_click" }
{ "type": "key_tap", "key": "R" }
{ "type": "key_hold", "key": "W", "seconds": 1.25 }
```

`vision_center_click` supports optional post-click verification:

```json
{
  "type": "vision_center_click",
  "template": "templates/sword_pickup.png",
  "verify_absent_after_click": true,
  "verify_wait_sec": 0.35,
  "absent_threshold": 0.45,
  "retry_on_verify_failure": true,
  "low_score_retry_count": 3,
  "low_score_retry_delay_sec": 0.12
}
```

`idle_home_config.extra.json`

- This file is optional.
- If it exists, it is loaded automatically after `idle_home_config.json`.
- Use it for scratch sequences such as `test` without touching the main config.
- `sequences`, `timing`, `hotkeys`, `window`, and `points` from the extra file override or extend the base config.

Failure screenshots

- On `BotError`, the bot saves a screenshot, metadata, the current cycle log, a recent log tail, and recent sequence-boundary screenshots under `failure_captures/`
- Filenames include timestamp, cycle, sequence, and action for easier triage
- The `.log` file is a per-cycle log snapshot, and `.recent.log` includes a rolling recent tail
- The extra `_snap*.png` files show recent `before_*` / `after_*` sequence states, which is usually more useful than manually recording video

Example:

```json
{
  "sequences": {
    "test": [
      { "type": "key_hold", "key": "W", "seconds": 0.5 },
      { "type": "wait", "seconds": 0.25 },
      { "type": "mouse_wheel", "delta": 120, "repeat": 10, "pause_sec": 0.02 }
    ]
  }
}
```

## Tuning order

Use `run-sequence` and adjust one sequence at a time.

1. `pickup_sword`
2. `move_to_combat_position`
3. `enable_auto_attack`
4. `ascend`

Recommended commands:

```powershell
python .\idle_home_bot.py run-sequence pickup_sword --startup-delay 3
python .\idle_home_bot.py run-sequence move_to_combat_position --startup-delay 3
python .\idle_home_bot.py run-sequence enable_auto_attack --startup-delay 3
python .\idle_home_bot.py run-sequence ascend --startup-delay 3
python .\idle_home_bot.py run --once --startup-delay 3
```

## Notes

- The sample config is only a starting point. Movement timings and click targets will need adjustment for your avatar, camera setup, and world routing.
- `idle_home_gui.py` exposes the main timing and movement values that usually need tuning on another PC.
- In the GUI, `F1` starts the loop and `F2` requests a stop even while `VRChat` is focused.
- The GUI also includes one-click buttons for `pickup_sword`, `move_to_combat_position`, `enable_auto_attack`, and `ascend` so you can tune one section at a time.
- The GUI starts with `idle_home_config.json` by default, and `Open Config...` can switch to another JSON file for load/save/run.
- `launch_gui.bat` also accepts an optional config path and opens the GUI with that file already selected.
- The GUI saves to the currently selected JSON file only when you press `Save`.
- The GUI also starts a small HTTP status page on the local network, typically `http://<PC-IP>:8787/`. Windows may ask for a firewall prompt the first time.
- The GUI exposes `ntfy Server`, `ntfy Topic`, `ntfy Priority`, and `ntfy Tags`. If `ntfy Topic` is non-empty, the bot sends a stop notification on failure.
- In the iPhone `ntfy` app, leave `Use another server` off when using the public `ntfy.sh` service. Only turn it on for a self-hosted server.
- The release build is `onedir`, not `onefile`, so `idle_home_config.json` and `templates\` remain editable next to the EXE.
- If the desktop cursor appears stuck at the center during calibration, stop using point capture for that part of the flow. Replace it with `mouse_move_relative` and center-based clicks.
- The runner stops if the VRChat client area does not match `1600x900`. Use `--allow-size-mismatch` only when intentionally recalibrating or testing another setup.
- The tool assumes the target window title contains `VRChat`.
