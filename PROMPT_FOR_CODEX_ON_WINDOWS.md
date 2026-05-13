# Copy/Paste Prompt For Codex On Windows

```text
Use the luna-longform-editor skill. If it is not installed, install it from ".\luna-longform-editor" and run ".\luna-longform-editor\scripts\setup_windows.ps1" first.

Edit this Luna long-form video:
"C:\path\to\video.mov"

I want a professional semantic edit, not just pause removal. Watch and transcribe the video, find stutters, false starts, repeated takes, awkward pauses, dead air, low-value waiting, and reference-video watching. Keep the best fluent version when I repeat myself. Preserve chronological logic unless a restructure clearly improves the video. Use word-level timestamps and the audio waveform so cuts do not clip my voice or leave static/clicks. Tighten the spoken pacing until it sounds natural, apply the Luna intro slate when the intro is just spoken setup, then add modest smart focus zooms during the tutorial/body when the UI would be easier to understand zoomed in. Center each zoom on the actual setting, utility, button, control panel, benchmark, or result being showcased, keep zooms sparse and smooth, and avoid zooming in/out so much that it feels distracting. Audit the rendered transcript, rerender if there are still obvious stutters or awkward delays, verify the final video decodes, and clean generated artifacts so only the final MP4 remains.
```

After the skill is installed once, the short version is:

```text
Use the luna-longform-editor skill on "C:\path\to\video.mov" and clean up everything except the final MP4 when done.
```
