# Focus Zoom Rules

Use these rules after the semantic edit and intro slate are working. The goal is readability, not flashy motion.

## When To Zoom

- Add focus zooms during tutorial/body sections where the viewer needs to inspect a UI.
- Good targets: tweak utilities, Windows Settings, Device Manager, NVIDIA Control Panel, browser download buttons, mouse software controls, benchmark/results panels, and specific toggles or dropdowns.
- Do not zoom the intro slate by default.
- Do not zoom just because the cursor moves. Zoom when the viewer needs to understand the setting, button, panel, or result being shown.
- If the screen is already readable or the target is uncertain, keep the full frame.

## How To Choose The Target

- Use the transcript to know what is being showcased at that moment.
- Use contact sheets, screenshots, or sampled frames to locate the UI region on screen.
- Center the crop on the active window or control group, not necessarily on the cursor.
- Keep enough surrounding context that the viewer still understands where they are in Windows or the app.
- For Windows Settings and utility windows, the target is usually the main settings pane, not the left navigation.
- For download/install moments, center the browser/download/install button only while that action is relevant.
- For result/proof moments, center the result panel or metric area.

## Motion Taste

- Use modest zoom: usually `1.08` to `1.16`; maximum `1.22` unless the source is extremely dense.
- Prefer stable regions lasting at least `3s`.
- Avoid more than one zoom change every few seconds.
- Hold the same zoom through a continuous workflow instead of zooming in and out on every click.
- Use smooth transitions around `0.35s` to `0.60s`.
- Keep cuts and zoom changes calm. If speech is already jump-cut tight, make the zoom plan even more sparse.

## Plan Format

Create a JSON file with normalized center coordinates:

```json
{
  "zooms": [
    {
      "start": 12.5,
      "end": 34.0,
      "center_x": 0.62,
      "center_y": 0.45,
      "zoom": 1.12,
      "label": "Windows mouse settings pane"
    }
  ]
}
```

- `start` and `end` are seconds in the already-rendered edit that will receive the visual zoom.
- `center_x` and `center_y` are normalized from top-left `0.0` to bottom-right `1.0`.
- Keep entries chronological and non-overlapping.
- Start after the intro slate duration unless the user explicitly asks otherwise.

Render with:

```bash
python3 scripts/apply_focus_zoom.py --input edited_with_intro.mp4 --zoom-plan focus_zoom_plan.json --output final_zoomed.mp4
```
