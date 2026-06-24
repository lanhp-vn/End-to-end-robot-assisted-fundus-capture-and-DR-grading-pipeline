# System-camera view calibration

Re-derive the Aurora screen ROI + alignment-arc boxes + LAB a* red cutoff + coverage threshold when
the camera **resolution** or **lighting** changes. Writes
`src/arm101_hand/data/system_camera_config.yaml` (`screen_roi` + the `auto_trigger` block); keeps a
`.bak`.

The ROI is a **deskewed 4:3 (640x480) crop** — `screen_roi` carries a rotation `angle` so a slightly
tilted screen comes out upright. Detection is **red-only by LAB a\***: each arc is RED (misaligned)
when enough of its box has a\* (the red-green axis) >= the cutoff `a_star_min`, else not-red (aligned).
a\* stays positive for a faint red tint even where the bright fundus washes the arcs toward white. The
two arc boxes are dragged by hand on the deskewed ROI; no green is sampled.

Three captures: **white** startup screen, **red** (misaligned) arcs, **bright** aligned screen.

```powershell
# Live guided capture (white startup screen -> red arcs -> bright aligned screen):
uv run python scripts/calibration/system_camera/calibrate_view.py

# Or re-run detection on three already-saved frames (white / red / bright):
uv run python scripts/calibration/system_camera/calibrate_view.py --from-files white.jpg red.jpg bright.jpg
```

Keys: `1`/`2`/`3` pick a deskewed 4:3 ROI candidate · `m` drag manually (axis-aligned) · `r`
recapture · SPACE capture · at confirm: `y` write · `e` re-tune arc boxes · `r` redo · `q` quit.

At the confirm screen the **RED panel** should read both arcs RED and the **BRIGHT panel** both clear
(each panel tints the pixels with a\* >= the cutoff); the verdict line shows `gate both RED` / `release both clear`.
