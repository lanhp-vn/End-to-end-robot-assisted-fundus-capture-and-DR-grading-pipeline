# System-camera view calibration

Re-derive the Aurora screen ROI + alignment-arc regions + red/green HSV bands when the camera
**resolution** or **lighting** changes. Writes `src/arm101_hand/data/system_camera_config.yaml`
(`screen_roi` + the `auto_trigger` block); keeps a `.bak`.

```powershell
# Live guided capture (white startup screen -> red arcs -> green arcs):
uv run python scripts/calibration/system_camera/calibrate_view.py

# Or re-run detection on three already-saved frames:
uv run python scripts/calibration/system_camera/calibrate_view.py --from-files white.jpg red.jpg green.jpg
```

Keys: `1`/`2`/`3` pick an ROI candidate · `m` drag manually · `r` recapture · SPACE capture ·
at confirm: `y` write · `e` re-tune arc boxes · `r` redo · `q` quit.
