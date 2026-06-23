# System-camera view calibration

Re-derive the Aurora screen ROI + alignment-arc bands + red HSV band(s) + coverage threshold when
the camera **resolution** or **lighting** changes. Writes
`src/arm101_hand/data/system_camera_config.yaml` (`screen_roi` + the `auto_trigger` block); keeps a
`.bak`.

The ROI is a **deskewed 5:3 (800x480) crop** — `screen_roi` carries a rotation `angle` so a slightly
tilted screen comes out upright. Detection is **red-only**: each arc is RED (misaligned) or not-red
(aligned). The arc bands are derived from the **camera circle** fitted on the bright/aligned frame
(symmetric left/right edges, battery corners excluded). No green is sampled.

Three captures: **white** startup screen, **red** (misaligned) arcs, **bright** aligned screen.

```powershell
# Live guided capture (white startup screen -> red arcs -> bright aligned screen):
uv run python scripts/calibration/system_camera/calibrate_view.py

# Or re-run detection on three already-saved frames (white / red / bright):
uv run python scripts/calibration/system_camera/calibrate_view.py --from-files white.jpg red.jpg bright.jpg
```

Keys: `1`/`2`/`3` pick a deskewed 5:3 ROI candidate · `m` drag manually (axis-aligned) · `r`
recapture · SPACE capture · at confirm: `y` write · `e` re-tune arc boxes · `r` redo · `q` quit.

At the confirm screen the **RED panel** should read both arcs RED and the **BRIGHT panel** both clear
(the fitted circle is drawn on it); the verdict line shows `gate both RED` / `release both clear`.
