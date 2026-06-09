# grab_toggle — grab, then click the index finger like a button

Status: approved (design), 2026-06-08
Owner: lanhp-vn
Related: `scripts/demos/grab_sequence.py`, `scripts/calibration/amazing_hand/jog.py`

## 1. Goal

A new demo, `scripts/demos/grab_toggle.py`, that behaves exactly like `grab_sequence.py`
through the staged forward grab (arm staged to `grab`, hand closes onto the object, both
held under torque), then enters an interactive loop where pressing a key toggles the
**index finger** in and out like pressing a button. On exit it falls through to the same
reverse-on-`h` / release-in-place path `grab_sequence.py` already has.

Concretely the operator sees the existing jog-style status line and high-load warning:

```text
step=20 | *ind b=  33 s= -39 |  mid b=  34 s= -19 |  rin b=  38 s= -22 |  thu b=  64 s=   0
  WARNING: high load on servo(s) [2] — back off one step and mark there
  step=20 | *ind b=  53 s= -39 |  mid b=  34 s= -19 |  rin b=  38 s= -22 |  thu b=  64 s=   0
  step=20 | *ind b=  33 s= -39 |  mid b=  34 s= -19 |  rin b=  38 s= -22 |  thu b=  64 s=   0
```

## 2. Approved decisions

- **Toggle target = base + delta from grab.** OUT base is the index base the hand settled
  at in `grab` (≈33). IN base is `clamp(OUT + delta, base_min, base_max)`. Side and the other
  three fingers stay put; only the index base moves.
- **Controls:** `SPACE` = toggle index IN/OUT; `[` / `]` = shrink / grow the toggle delta
  live; `q` / `Ctrl+C` = stop toggling and go to the usual exit prompt.
- **High load = warn only** (matches `jog.py`): print the warning, leave the finger where it
  is. No automatic back-off.
- **Code sharing = extract a shared helper.** The staged forward grab, the staged reverse,
  and the connect/release scaffolding move from `grab_sequence.py` into a reusable helper;
  both scripts call it. Ships as one atomic commit (IL-6) since it edits the existing demo.

## 3. Headroom verification (the gate)

Decoding the stored `grab` index pose `[72, 6]` (servo frame, odd ID 1 / even ID 2):

- odd ID 1, no inversion -> `pos1 = 72`; even ID 2, inverted -> `pos2 = -6`
- `base = (72 + (-6)) // 2 = 33`, `side = (-6 - 72) // 2 = -39`

Index `base_max = 70` (from `hand_calib_values.yaml`), so there is **37 deg of headroom**
above the grab base. A delta of 20 lands IN at base 53 — well under the limit. "Base + delta"
is a valid anchor; pressing in flexes the finger further than `grab`, exactly as intended.

## 4. Architecture (pure-core / thin-shell, matching the existing jog tools)

Three new files plus one behavior-preserving refactor:

### 4.1 `src/arm101_hand/scripts/grab_common.py` (new — extracted, no logic change)

Holds the parts of `grab_sequence.py` that both demos share. Behavior-preserving move only:

- `build_arm_plan(...)` — the current `_build_arm_plan` (clamped `grab` targets + `base_max`).
- `run_grab_demo(on_hold=None)` — the whole `main()` body: resolve plan + hand targets,
  build follower, open the hand controller, define the staged reverse, connect, push
  calibration, enable torque, run forward steps 1-4, then call `on_hold(ctx)` while both
  devices hold `grab`, then run the existing exit path (`confirm_and_release` with the staged
  reverse as `home_routine`, hand torque-off in an inner `finally`). With `on_hold=None` the
  observable behavior is identical to today's `grab_sequence.py`.
- `on_hold` receives a small context object exposing what an interactive hold needs: the open
  hand controller, the loaded hand calibration, and the loaded hand runtime config (tuning +
  speeds). It must not need anything else.

### 4.2 `src/arm101_hand/hand/index_toggle.py` (new — pure, testable, like `pose_jog.py`)

No hardware, no `msvcrt`. The testable core of the toggle:

- A small immutable state holding: `out_base`, `side` (held), `delta`, and `pressed` (bool).
- `in_base(state, base_min, base_max)` -> `clamp(out_base + delta, base_min, base_max)`.
- `target_base(state, base_min, base_max)` -> IN base when pressed, OUT base otherwise.
- Delta adjust helpers clamped to dedicated bounds (see §6) — independent of the jog
  `STEP_*` bounds; the true ceiling is `base_max`, enforced by the clamp above.
- A key->action map for `SPACE` / `[` / `]` / `q` so the shell stays a thin dispatcher.

### 4.3 `scripts/demos/grab_toggle.py` (new — `msvcrt` shell only)

- `on_hold(ctx)`: the interactive loop. Seed the OUT base + held side + the other three
  fingers' `(base, side)` from the hand's **present** position after grab settles
  (decompose, like `jog.hold_present_and_read_state`). Loop: read raw key -> map to action ->
  on toggle, drive the index's two servos to the new `(target_base, side)` via
  `drive_hand_servos` (position-polling), then read the index **present** position + load,
  print `format_hand_status(...)` and any `load_warning(...)`. `q` / `Ctrl+C` breaks the loop.
- `main()`: `return run_grab_demo(on_hold)`.

### 4.4 `scripts/demos/grab_sequence.py` (edit — reduced to a thin caller)

Becomes `def main(): return run_grab_demo()` plus its module docstring. No behavior change.

## 5. Reuse (no new rendering or warning code)

- **Status line:** `arm101_hand.hand.pose_jog.format_hand_status` with a `HandJogState`
  built from `active="index"`, `step=<delta>`, and the per-finger `(base, side)` cursor.
  Renders the exact `step=20 | *ind b= 53 s= -39 | mid ... | rin ... | thu ...` line; the
  `step=` field shows the live delta. Middle/ring/thumb are read once after grab settles
  (static under torque); the index is re-read each toggle.
- **Load warning:** `arm101_hand.hand.range_calib.load_warning(load1, load2)` on the index
  IDs `(1, 2)` -> the exact `WARNING: high load on servo(s) [2] ...` line.
- **Driving:** `arm101_hand.hand.motion.drive_hand_servos` (position-polling) on the two index
  IDs. IN uses `tuning.speeds.close`, OUT uses `tuning.speeds.open` (same directional speeds
  as the grab demo). Per-finger compose/decompose via `hand.kinematics`.

## 6. Honest display + constants

- **Present, not commanded.** Pressing into a button stalls the finger below the commanded
  IN base (`drive_hand_servos` times out and continues). The status line therefore shows the
  index's **present** decoded `(base, side)` read back after the move settles — the honest
  number that tells the operator how far the finger actually got.
- **Toggle delta constants** live in `index_toggle.py`: `TOGGLE_DELTA_DEFAULT = 20`,
  `TOGGLE_DELTA_MIN = 1`, `TOGGLE_DELTA_MAX = 40`. Independent of the jog `STEP_MAX`. The real
  limiter is the index's calibrated `base_max`, enforced by the clamp. No `hand_config.yaml`
  schema change (KISS).

## 7. Iron Laws / testing

- **IL-6:** `grab_common.py` + the `grab_sequence.py` reduction + the two new files ship in
  one atomic commit. Because the extraction touches the working `grab_sequence.py` demo, the
  plan calls for a hardware re-run of `grab_sequence.py` to confirm no regression (the
  hardware path can't be unit-tested).
- **IL-5:** reads `hand_calib_values.yaml`, `hand_config.yaml`, and the arm calibration JSON;
  writes none of them.
- **IL-1 / IL-4:** unchanged — same two-bus, one-owner-per-bus pattern as `grab_sequence.py`.
- **IL-2 / IL-3:** no `references/` edits; index = servo IDs `(1, 2)` per the canon.
- **Tests:** a host unit test for the `index_toggle` pure core (`in_base` clamping at
  `base_max`, OUT/IN selection, delta clamp to `[MIN, MAX]`, key->action map). Runs under
  `uv run pytest -m 'not hardware'`.

## 8. Out of scope (YAGNI)

No finger re-selection (index only), no free-jog arrows, no auto-back-off on load, no new
pose saving, no `hand_config.yaml` schema change, no arm motion during the toggle phase
(the arm holds `grab` under torque the whole time).

## 9. File manifest

| File | Change |
|---|---|
| `src/arm101_hand/scripts/grab_common.py` | new — extracted shared grab/reverse/release |
| `src/arm101_hand/hand/index_toggle.py` | new — pure toggle core |
| `src/arm101_hand/hand/__init__.py` | export the new toggle core symbols |
| `scripts/demos/grab_toggle.py` | new — msvcrt shell + toggle loop |
| `scripts/demos/grab_sequence.py` | edit — thin caller of `run_grab_demo()` |
| `tests/unit/test_index_toggle.py` | new — unit test for `index_toggle` core |
