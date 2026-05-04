# SO-ARM101 — Follower Calibration

Calibration procedure for the SO-ARM101 follower arm with the gripper (motor ID 6) physically removed. Wraps lerobot's `lerobot-calibrate` so it does not crash on the missing motor 6.

Hardware: 5 STS3215 servos on a 12 V serial bus, IDs 1..5 shoulder → wrist (no gripper). The AmazingHand replaces the gripper at the end of the wrist.

Reference: <https://huggingface.co/docs/lerobot/so101?calibrate_follower=Command>.

---

## 1. Prerequisites

- 12 V / 3 A PSU on, Waveshare bus servo adapter (A) wired to the arm.
- Logic side of the adapter set to **3.3 V**.
- USB↔TTL adapter enumerated as a COM port. Discover with:

  ```powershell
  Get-PnpDevice -Class Ports -Status OK | Select-Object Name, DeviceID | Format-Table -AutoSize
  ```

  The Waveshare bus adapter typically enumerates as a CH343 or FT232 port. Note the COM number — it goes into `--robot.port` below.

- `uv sync` has been run from the repo root (provisions `.venv` with editable lerobot + our `SO101FollowerNoGripper` subclass).

> **Single-owner bus.** Close any other process holding the COM port (FD.exe, a serial monitor, a previous Python session) before running calibration. The driver chip will not multi-master.

## 2. Run calibration

From the repo root:

```powershell
uv run arm101-calibrate-follower `
    --robot.type=so101_follower_no_gripper `
    --robot.port=COM<X> `
    --robot.id=so101_follower
```

Replace `COM<X>` with the COM number you discovered above.

The `arm101-calibrate-follower` console script registers our `SO101FollowerNoGripper` subclass before delegating to lerobot's `calibrate()`. Without this wrapper, the upstream `lerobot-calibrate` does not know the type `so101_follower_no_gripper` exists.

## 3. What happens

Per the upstream lerobot calibration flow:

1. **Connect** — the script opens the bus and pings each of the 5 motors.
2. **Move-to-pose prompts** — you'll be asked to move the arm into specific poses (zero, max, min) one at a time. Press Enter at each pose.
3. **Save** — calibration is written to:
   ```
   ~/.cache/huggingface/lerobot/calibration/robots/so101_follower_no_gripper/<id>.json
   ```
   where `<id>` is what you passed via `--robot.id`. On Windows that's `%USERPROFILE%\.cache\huggingface\lerobot\calibration\robots\so101_follower_no_gripper\so101_follower.json`.

If you want the JSON written into the repo instead, add `--robot.calibration_dir=<path>` to the command line.

## 4. Discover the COM port

```powershell
uv run python scripts/calibration/so_arm101/find_port.py
```

Thin wrapper over `lerobot-find-port` (see `lerobot.scripts.lerobot_find_port`). The interactive flow asks you to unplug the adapter, hit Enter, replug, hit Enter again — the difference between the two enumerations is the port you want.

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `KeyError: 'so101_follower_no_gripper'` or `unknown robot type` | You ran `lerobot-calibrate` directly instead of `arm101-calibrate-follower` | Use `uv run arm101-calibrate-follower …` so the subclass registers first |
| `serial.serialutil.SerialException: could not open port 'COM<X>'` | Wrong COM number, or another process holds the port | Re-run §1 discovery; close FD.exe / serial monitors |
| Ping fails for one motor | Bad cable, dead servo, or duplicate ID on the bus | Wire that motor alone and check with FD.exe or `lerobot-find-port` |
| Calibration completes but arm jogs the wrong direction | Wire conventions mismatched | Re-check IDs 1..5 are shoulder_pan → shoulder_lift → elbow_flex → wrist_flex → wrist_roll |
| Position drifts after release | Backlash in the harmonic / belt drive, normal at low torque | Set holding torque from the teleop side; not a calibration issue |
