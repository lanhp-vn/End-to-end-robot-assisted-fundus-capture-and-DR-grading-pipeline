# AmazingHand — Finger Calibration (Python)

Calibration procedure for the 4-finger AmazingHand using the scripts in `scripts/calibration/AmazingHand/`. Follows the *Step 3: Finger calibration with Python* section of `references/AmazingHand/docs/AmazingHand_Assembly.pdf` (pages 21–23), with the manual middle-position tuning replaced by the YAML-driven scripts in this repo.

---

## 1. Overview

- 4 fingers × 2 Feetech **SCS0009** servos = 8 servos on a shared TTL serial bus @ 1 Mbaud.
- Each finger is a **parallel mechanism**: the two servos combine to produce flexion/extension + abduction/adduction. They must be driven as a pair.
- All calibration state lives in a single YAML file — `scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml` — that every script reads from and writes to.
- Servo-ID convention (right hand, viewing from the palm side as in PDF page 21): **odd ID on the right servo, even ID on the left servo**.

  | Finger | Right servo (odd) | Left servo (even) |
  | --- | --- | --- |
  | Index  | 1 | 2 |
  | Middle | 3 | 4 |
  | Ring   | 5 | 6 |
  | Thumb  | 7 | 8 |

---

## 2. Prerequisites

### 2.1 Python environment

The repo uses `uv` to manage a single virtualenv at `.venv/`. The AmazingHand scripts share that env with the SO-ARM101 follower code — there is no separate install step for the calibration scripts.

From the repo root:

```powershell
uv sync
```

`uv sync` provisions `.venv` from `pyproject.toml` (Python 3.12, lerobot[feetech], rustypot, numpy, pyyaml, pydantic). Re-run it whenever `pyproject.toml` changes.

Run any calibration script with `uv run`:

```powershell
uv run python scripts/calibration/AmazingHand/AmazingHand_FingerTest.py
```

`uv run` resolves the interpreter and PYTHONPATH from `.venv/`, so you do not need to activate the env first.

### 2.2 USB↔TTL bridge & COM port

The Waveshare CH343 bridge enumerates as a COM port on Windows. On this machine it's `COM18`. The port is stored in `AmazingHand_calib_values.yaml`:

```yaml
com_port: COM18
baudrate: 1000000
timeout: 0.5
speed: 6
```

If the port changes, edit the YAML — every script picks it up automatically. Discover a changed port with:

```powershell
Get-PnpDevice -Class Ports -Status OK | Select-Object Name, DeviceID | Format-Table -AutoSize
```

### 2.3 Power

External **5 V / 2 A+** PSU wired to the servo bus hub. The USB bridge carries signal only — it cannot power 8 SCS0009s.

---

## 3. Calibration flow

Four steps, in order. Steps 2–4 all read from and/or update `AmazingHand_calib_values.yaml`.

### Step 1 — Burn unique IDs 1–8 with FD.exe

**Tool:** `references/FeetechServo/feetech debug tool master/FD1.9.8.2/FD.exe` (PDF page 21).

Factory default for every SCS0009 is ID `1`. Program each servo individually, one at a time, so duplicate IDs never share the bus.

1. Close any Python / serial-monitor windows holding the COM port.
2. PSU off. Disconnect all servos, then wire **exactly one** to the bus.
3. PSU on.
4. Launch `FD.exe` → **Programming** tab → set **Com = COM18**, **BaudR = 1000000** → click **Open**.
5. Click **Search** — the servo appears as ID 1.
6. Edit **address 5 ("ID")** to the target value (1..8) per the table in §1.
7. Click **Save** (next to the ID input box). Without Save the change is lost on power cycle.
8. Click **Search** again — same servo should now report the new ID.
9. Click **Close**, PSU off, swap to the next servo, repeat.

> **IMPORTANT — Right vs left matters.** Mis-assigning odd/even on a pair means `FingerCalib`'s Close/Open direction is inverted for that finger. Label each servo as you program it (thin marker on the body), and verify orientation against PDF page 21 before screwing anything together.

When all 8 are burned, wire them all back to the bus and PSU on.

### Step 2 — Reset motors to 0° before attaching horns

**Script:** `scripts/calibration/AmazingHand/AmazingHand_MotorReset.py`
**Replaces:** PDF page 22 ("Place servo horns as following picture").

Each servo needs to be sitting at its electrical 0° when the horn is screwed down — otherwise the finger's neutral pose is mechanically wrong and no software offset can fix it.

1. Make sure **no servo horns are attached yet**.
2. Run the script (`uv run python scripts/calibration/AmazingHand/AmazingHand_MotorReset.py`), type `yes` at the confirmation.
3. At `Which finger to reset? (index/middle/ring/thumb, 'q' to quit):`, enter a finger.
4. The script drives that finger's two servos to 0°, holds them under torque, and sets the finger's `middle_pos` values to `0` in the YAML.
5. Fit each horn onto the spline as close to the matching position on PDF page 22 as the geometry allows, then secure with the **M2×4 servo screw**.
6. Pick the next finger at the same prompt. Repeat for all four.
7. Type `q` — torque releases on all touched motors.

### Step 3 — Fine-tune middle positions

**Script:** `scripts/calibration/AmazingHand/AmazingHand_MiddlePos_FingerCalib.py`
**Follows:** PDF page 23.

Horn splines have discrete teeth (~15° each), so even a well-installed horn has a few degrees of residual offset. Dial it in interactively.

1. Run the script (`uv run python scripts/calibration/AmazingHand/AmazingHand_MiddlePos_FingerCalib.py`), enter a finger at the prompt.
2. The finger runs one Close→Open cycle, then:
   ```
   New MiddlePos_1 [current=0] (Enter to keep, 'save'/'q' to exit):
   ```
3. **Watch the CLOSED pose** — both servo horns should be aligned with the servo's middle plane (the seam line across the servo body; see PDF page 23 photo). If a horn sits a few degrees short or past that line, enter an integer degree offset. Press Enter to keep the other servo unchanged.
4. Next cycle reflects the new values. Iterate until both horns sit on the middle plane at full close.
5. Type `save` (or `q`, or Ctrl+C) when done — values persist to the YAML, and only the active finger's entry changes.
6. Re-run for each of the other three fingers.

> If a final offset is outside **±20°**, the horn is on the wrong spline tooth — repeat Step 2 for that finger, bumping the horn by one tooth.

### Step 4 — Audit with FingerTest

**Script:** `scripts/calibration/AmazingHand/AmazingHand_FingerTest.py`

Sanity check the saved values against the real mechanism.

1. Run (`uv run python scripts/calibration/AmazingHand/AmazingHand_FingerTest.py`), enter a finger.
2. The finger cycles Close↔Open indefinitely using the stored `middle_pos` values.
3. Watch for:
   - Clean closed pose — horns aligned with their servos' middle plane.
   - Clean open pose — finger returns to a neutral spread.
   - No buzz, whine, or heat at either endpoint.
4. Ctrl+C to stop; re-run for the next finger.

If anything looks off, revisit Step 3 for that finger.

### Step 5 — Full-hand demo

**Script:** `scripts/calibration/AmazingHand/AmazingHand_FullHand_Test.py`

This script provides a combined "fist → open hand → per-finger isolation" demo. It reads from `AmazingHand_calib_values.yaml` and drives the right hand through a complete cycle.

1. Run the script (`uv run python scripts/calibration/AmazingHand/AmazingHand_FullHand_Test.py`).
2. The hand will cycle through closing all fingers, opening all fingers, and then exercising each finger independently.
3. This is the end-to-end sanity check that proves the calibration matches the real mechanism across all fingers.

---

## 4. Where the calibration lives

Everything sits in `scripts/calibration/AmazingHand/AmazingHand_calib_values.yaml`:

```yaml
com_port: COM18
baudrate: 1000000
timeout: 0.5
speed: 6
fingers:
  index:
    servo_1: { id: 1, middle_pos: 0 }
    servo_2: { id: 2, middle_pos: 0 }
  middle:
    servo_1: { id: 3, middle_pos: 0 }
    servo_2: { id: 4, middle_pos: 0 }
  ring:
    servo_1: { id: 5, middle_pos: 0 }
    servo_2: { id: 6, middle_pos: 0 }
  thumb:
    servo_1: { id: 7, middle_pos: 0 }
    servo_2: { id: 8, middle_pos: 0 }
```

- `com_port`, `baudrate`, `timeout`, `speed` — shared serial + motion settings, used by all three scripts.
- `fingers.<finger>.servo_1` / `servo_2` — per-servo `id` and calibrated `middle_pos` (degrees).
- `servo_1` is the odd-ID (right) servo, `servo_2` is the even-ID (left) servo of the pair.

Editing the YAML by hand is fine — the scripts preserve any top-level keys you add.

---

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `ModuleNotFoundError: No module named 'yaml'` | Running with the system Python instead of `.venv/` | Use `uv run python …` (not bare `python …`); re-run `uv sync` if `.venv` is stale |
| `pip install yaml` fails with "no matching distribution" | Wrong package name | The import is `yaml` but the package is `pyyaml` |
| Only one servo of a pair responds | Duplicate ID on the bus | Wire each servo alone and re-burn via Step 1 |
| Close/Open motion looks inverted on one finger | Odd/even swap on that pair | Recheck Step 1 — odd ID on the right servo, even on the left |
| Horn sits badly at closed pose no matter what offset you enter | Horn on wrong spline tooth | Repeat Step 2 for that finger and bump the horn by one tooth |
| Servo buzzes/whines near an endpoint | Commanded past the mechanical stop | Reduce the offending `middle_pos` by 1–2° in Step 3 |
| COM port fails to open | Another program holds the port, or the bridge enumerated on a different COM | Close serial monitors / other Python sessions; update `com_port` in the YAML |
| Final offset needs to exceed ±20° | Horn on wrong tooth, or IDs swapped | Don't absorb it in software — fix the mechanical/ID issue |

---

## 6. One-line summary

Burn unique IDs (FD.exe) → reset each finger's motors to 0° and install horns (`AmazingHand_MotorReset.py`) → interactively dial in per-servo offsets (`AmazingHand_MiddlePos_FingerCalib.py`) → audit per-finger with `AmazingHand_FingerTest.py` → run the full-hand demo (`AmazingHand_FullHand_Test.py`). All state lives in `AmazingHand_calib_values.yaml`.
