# Luna Longform Editor - Windows Portable

This folder contains the full `luna-longform-editor` Codex skill, including the intro background image, focus-zoom rules, editing rules, Python render/audit tools, and Windows PowerShell wrappers.

## What You Need On Windows

- Codex on Windows.
- Python 3.10 or newer.
- FFmpeg and FFprobe in PATH.

The setup script creates the local transcription environment and installs `faster-whisper`. It can also try to install FFmpeg with `winget`.

## Install

Open PowerShell in this folder and run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\Install-Windows.ps1
```

If FFmpeg is not installed, either install it manually or run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\Install-Windows.ps1 -InstallFfmpeg
```

The installer copies the skill to:

```text
%USERPROFILE%\.codex\skills\luna-longform-editor
```

## Use

After install, you can prompt Codex on Windows like this:

```text
Use the luna-longform-editor skill to edit "C:\path\to\my raw video.mov".
Watch/transcribe it, remove stutters, repeated takes, awkward dead air, reference-video watching, and bad duplicate explanations. Keep the timeline logical, tighten spoken pacing, snap cuts to the voice waveform, apply the Luna intro slate when appropriate, audit the rendered transcript, and clean generated artifacts so only the final MP4 remains.
```

The skill also adds modest smart focus zooms after the intro when a tutorial UI would be easier to see. Codex should choose the zoom target from the transcript and visual review, frame the full viewer action, keep required context like Windows search/taskbar UI visible, then render it with `apply_focus_zoom.py`.

If the skill is not installed yet, start with:

```text
Install the Luna longform editor skill from this folder, run the Windows setup script, then use it to edit "C:\path\to\my raw video.mov".
```

## Useful Commands

Analyze a video:

```powershell
.\luna-longform-editor\scripts\analyze_video.ps1 "C:\path\to\video.mov"
```

Transcribe a video:

```powershell
.\luna-longform-editor\scripts\transcribe_video.ps1 "C:\path\to\video.mov"
```

Run setup again:

```powershell
.\luna-longform-editor\scripts\setup_windows.ps1
```

## Important

This package does not include your raw videos or generated drafts. Upload this folder or the `.zip` next to it to Google Drive, download it on Windows, unzip it, then run `Install-Windows.ps1`.
