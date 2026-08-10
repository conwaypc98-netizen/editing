# Luna Longform Director

Portable Codex skill for evidence-driven Luna Tweak video production on macOS and Windows.

It supports two workflows:

- Intelligent editing of an existing recording using transcript, frame, duplicate-take, continuity, waveform, and rendered-output evidence.
- Autonomous production where Codex writes a shot plan, records each desktop demonstration, generates per-shot narration with a verified xAI custom voice, assembles the shots, and refuses delivery until technical, speech, plan, and visual review gates pass.

This is deliberately not a silence remover. Every kept range must explain its story role, viewer purpose, take choice, continuity, and evidence. Final visual review is fail-closed: an unreviewed crop or timeline is not considered passing.

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

After install, edit an existing recording with:

```text
Use the luna-longform-editor skill on "C:\path\to\video.mov". Create an isolated Luna job, build the evidence dossier, write and validate a reasoned edit plan, render and revise it, complete the fail-closed visual and transcript QA, then clean only that job's artifacts.
```

For autonomous recording, use:

```text
Use the luna-longform-editor skill in synthetic mode. Build a project brief and per-shot desktop plan for this topic: <topic>. Record and verify every desktop shot, then use my verified xAI voice ID from XAI_VOICE_ID for per-shot narration. Transcribe and audit each generated line, complete the listening review, then assemble, revise, and accept only a fully passing final.
```

The one manual voice step is creating and verifying your own custom voice in the xAI console. After that, set `XAI_API_KEY` and `XAI_VOICE_ID`; the workflow can generate narration automatically. API keys are never stored in the repo.

If you have not installed it yet, use:

```text
Install the Luna longform editor skill from this folder, run the Windows setup script, then use it to edit "C:\path\to\video.mov".
```

See `PROMPT_FOR_CODEX_ON_WINDOWS.md` for the fuller prompt.

## Included

- `luna-longform-editor/`: the Codex director/editor skill and automation scripts.
- `luna-longform-editor/channel_profile.json`: persistent feedback rules plus confidence-gated learned measurements.
- `Install-Windows.ps1`: root installer.
- `README_WINDOWS.md`: Windows setup notes.
- `PROMPT_FOR_CODEX_ON_WINDOWS.md`: copy/paste editing prompts.
- `tests/`: regression tests for job isolation, cleanup safety, plan/media integrity, intro evidence, voice consent/audit, style learning, shot review, and final acceptance.

Raw videos, rendered outputs, transcripts, FFmpeg binaries, and Python virtual environments are intentionally not committed.
