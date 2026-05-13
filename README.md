# Luna Longform Editor

Portable Codex skill for intelligently editing Luna Tweak long-form videos on Windows.

This is not a simple silence remover. The skill tells Codex to watch/transcribe the video, choose the best fluent takes, remove stutters and bad duplicates, keep the timeline logical, tighten spoken pacing, snap cuts to audio boundaries, apply the Luna intro slate when appropriate, add smart tutorial focus zooms, audit the rendered transcript, and clean generated artifacts when done.

## Windows Install

Download or clone this repo on Windows, open PowerShell in the repo folder, then run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\Install-Windows.ps1
```

If FFmpeg is not installed, try:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\Install-Windows.ps1 -InstallFfmpeg
```

The installer copies the skill to:

```text
%USERPROFILE%\.codex\skills\luna-longform-editor
```

## Prompt Codex On Windows

After install, use:

```text
Use the luna-longform-editor skill on "C:\path\to\video.mov" and clean up everything except the final MP4 when done.
```

The skill should add modest focus zooms after the intro when a Windows setting, utility, button, control panel, benchmark, or result needs to be easier to see. It should keep the zooms sparse, centered, and smooth.

If you have not installed it yet, use:

```text
Install the Luna longform editor skill from this folder, run the Windows setup script, then use it to edit "C:\path\to\video.mov".
```

See `PROMPT_FOR_CODEX_ON_WINDOWS.md` for the fuller prompt.

## Included

- `luna-longform-editor/`: the Codex skill.
- `Install-Windows.ps1`: root installer.
- `README_WINDOWS.md`: Windows setup notes.
- `PROMPT_FOR_CODEX_ON_WINDOWS.md`: copy/paste editing prompts.

Raw videos, rendered outputs, transcripts, FFmpeg binaries, and Python virtual environments are intentionally not committed.
