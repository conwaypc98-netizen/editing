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
- Center the crop on the viewer's current action, not merely on the active window or cursor.
- Keep enough surrounding context that the viewer still understands where they are in Windows or the app. The crop should include the app title/header, the control group being changed, and any menu/search/dialog that explains how the user got there.
- If the action is in a corner or edge UI, such as bottom-left Windows search, Start/search, tray icons, taskbar controls, a browser download shelf, or a small installer prompt, frame that UI as the target. Do not center on the later app window until the action moves there.
- For app windows that are not visually centered, such as Logitech G Hub or a tweak utility sitting slightly off to one side, use the actual visible app bounds to calculate the crop. Do not assume the app is at screen center.
- For Windows Settings and utility windows, the target is usually the main settings pane, not the left navigation.
- For download/install moments, center the browser/download/install button only while that action is relevant.
- For result/proof moments, center the result panel or metric area.

## Framing Rules

- Plan zooms with boxes whenever possible. A box-based plan is smarter than a guessed center point because the render helper can keep important UI visible and reduce the zoom if the requested crop would cut something off.
- Use `target_box` for the main thing the viewer should inspect.
- Use `include_boxes` for required context that must remain visible, such as the Windows search bar while typing, a taskbar icon being clicked, an app title/header, a confirmation dialog, or a side panel that labels the current setting.
- If the target and required context are far apart, lower the zoom or stay full-frame. A slightly less zoomed shot is better than hiding the action.
- Before final delivery, sample at least one frame in the first second, middle, and last second of each zoom region. The viewer should be able to answer: what is being opened, changed, clicked, or proven?
- Fix any zoom where the showcased app/control is off-center, a typed search/menu is cropped, or the crop makes the screen feel like a random close-up.

## Motion Taste

- Use modest zoom: usually `1.08` to `1.16`; maximum `1.22` unless the source is extremely dense.
- Prefer stable regions lasting at least `3s`.
- Avoid more than one zoom change every few seconds.
- Hold the same zoom through a continuous workflow instead of zooming in and out on every click.
- Use smooth transitions around `0.35s` to `0.60s`.
- Keep cuts and zoom changes calm. If speech is already jump-cut tight, make the zoom plan even more sparse.

## Plan Format

Prefer a JSON plan with normalized boxes:

```json
{
  "zooms": [
    {
      "start": 12.5,
      "end": 34.0,
      "target_box": [0.34, 0.18, 0.82, 0.76],
      "zoom": 1.12,
      "label": "Windows mouse settings pane"
    },
    {
      "start": 72.0,
      "end": 78.5,
      "target_box": [0.00, 0.84, 0.34, 1.00],
      "include_boxes": [
        [0.00, 0.84, 0.34, 1.00]
      ],
      "zoom": 1.10,
      "label": "typing Logitech G Hub in Windows search"
    }
  ]
}
```

- `start` and `end` are seconds in the already-rendered edit that will receive the visual zoom.
- `target_box` is `[left, top, right, bottom]`, normalized from top-left `0.0` to bottom-right `1.0`.
- `include_boxes` is an optional list of additional normalized boxes that must not be cropped out.
- `padding` is optional and defaults to `0.035`; raise it slightly for app windows with important headers or edges.
- `center_x` and `center_y` are still supported for older plans, but use boxes for new work.
- The render helper automatically clamps centers and reduces zoom if the requested zoom would crop out a required box.
- Keep entries chronological and non-overlapping.
- Start after the intro slate duration unless the user explicitly asks otherwise.

Render with:

```bash
python3 scripts/apply_focus_zoom.py --input edited_with_intro.mp4 --zoom-plan focus_zoom_plan.json --output final_zoomed.mp4
```
