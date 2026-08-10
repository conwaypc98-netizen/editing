# Luna Longform Director - Windows Portable

This folder contains the full `luna-longform-editor` Codex skill, including the job orchestrator, evidence dossier, reasoned-plan validator, desktop recorder, xAI custom-voice integration, shot assembler, final acceptance auditor, intro/zoom tools, and Windows wrappers.

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

After install, you can prompt Codex to edit a recording like this:

```text
Use the luna-longform-editor skill to edit "C:\path\to\my raw video.mov". Create an isolated job, build the evidence dossier, inspect the transcript and frames, write a reasoned edit plan, validate it, tighten and snap the approved ranges, render and revise, complete the visual review and transcript audit, and clean only this job after acceptance.
```

To have Codex record and narrate the tutorial instead of you:

```text
Use the luna-longform-editor skill in synthetic mode for this topic: <topic>. Write the script and shot plan, record each Windows desktop shot, verify the required UI state, generate narration with my verified xAI custom voice, transcribe and audit every generated line, complete the listening review, assemble it, and do not accept the final until every QA gate passes.
```

Create your own custom voice once in the xAI console, then set `XAI_API_KEY` and `XAI_VOICE_ID` in the environment visible to Codex. The script requires explicit owner-consent confirmation and does not clone voices from old videos.

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

Create an edit job:

```powershell
python .\luna-longform-editor\scripts\luna_editor.py init --mode edit --source "C:\path\to\video.mov"
```

List the screen recorder help:

```powershell
python .\luna-longform-editor\scripts\record_desktop.py --help
```

## Important

This package does not include your raw videos or generated drafts. Upload this folder or the `.zip` next to it to Google Drive, download it on Windows, unzip it, then run `Install-Windows.ps1`.
