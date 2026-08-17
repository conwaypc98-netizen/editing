# Continue The Luna Autonomous Editor On Windows

This repository is the durable handoff. The original Mac chat is useful history,
but the code, tests, skill instructions, and evidence contracts in this repo are
the source of truth for continuing development on Windows.

## Preferred: Hand Off The Existing Chat

If **Settings > Connections** is available in the ChatGPT desktop app on both
computers, connect the Mac and Windows PC as hosts. Save this same Git repository
as a project on both computers. Then open the existing Luna editor chat on the
Mac, select its current run location in the chat footer, choose the Windows PC,
and select **Hand off**. Codex transfers the chat and Git state to the matching
Windows project.

The Windows PC must be awake, online, signed in to the same ChatGPT account and
workspace, and running the current desktop app. Handoff availability can vary by
rollout.

## Reliable Fallback: Start A Fresh Windows Chat

Clone the repository onto the native Windows drive:

```powershell
git clone https://github.com/conwaypc98-netizen/editing.git
cd editing
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\Install-Windows.ps1 -InstallFfmpeg
```

Open the cloned `editing` folder as a Codex project. Then paste this prompt:

```text
Use the luna-longform-editor skill and continue the autonomous Luna editor project in this repository.

Treat WINDOWS_CONTINUATION.md, README_WINDOWS.md, PROMPT_FOR_CODEX_ON_WINDOWS.md, luna-longform-editor/SKILL.md, and the repository tests as the durable handoff from the prior Mac task. Inspect git status and recent commits first. Verify the installed skill matches this checkout, run the full test suite, and exercise the native Windows recording and exact-window capture paths before changing behavior.

The active objective is a publishable Luna Tweak tutorial produced without me recording it: research the topic, plan evidence-bound shots, operate and record the real Windows desktop, use only my reviewed and registered xAI custom voice, audibly audit every generated line against the exact owner reference, assemble and edit the tutorial, and revise until creator-fidelity, transcript, visual, timing, zoom, audio, and adversarial final-review gates all pass. Do not replace editorial judgment with silence cutting. Do not claim that audio or pixels were reviewed unless the required evidence file was actually produced. Preserve good takes, repair only failed or stale stages, keep secrets and biometric voice files out of Git, and do not stop at a plan or mock output.

Start by reporting the exact Windows environment and the next evidence-backed action. Then continue the implementation and run a small real Windows tutorial end to end.
```

## Private Files To Move Separately

The public repository intentionally does not contain API keys, custom voice IDs,
owner-reference audio, raw recordings, or generated videos. Transfer those
privately to the Windows PC. Never add them to Git.

For xAI narration, set these only in the Windows user environment:

```powershell
[Environment]::SetEnvironmentVariable("XAI_API_KEY", "your-key", "User")
[Environment]::SetEnvironmentVariable("XAI_VOICE_ID", "your-voice-id", "User")
```

Restart the ChatGPT desktop app after setting them. The voice can be used only
after the repository's source-bound owner-reference review and xAI registration
checks pass. A technically valid WAV without a genuine owner listening/privacy
review is not approved production evidence.

## Current Verified State

- The skill supports semantic editing and autonomous schema-version 3 projects
  with schema-version 4 evidence-bound shot plans.
- Windows PowerShell installation, FFmpeg discovery, the isolated
  `faster-whisper` environment, pinned `websockets==15.0.1`, Python CLI entry
  points, and native Win32 window enumeration have run on GitHub's Windows host.
- Voice generation is bound to a reviewed xAI custom voice and exact source-audio
  hash. Grok's realtime audio model compares each candidate with an exact excerpt
  of that registered source before an automated voice review can pass.
- The production director is resumable and invalidates stale transcript, visual,
  voice, creator-fidelity, and final-QA evidence when bound inputs change.
- The remaining acceptance target is a real tutorial recorded on the owner's
  Windows desktop with the owner's verified xAI voice, followed by full rendered
  review. Synthetic placeholders and dry runs do not complete that target.

## Useful Verification

```powershell
git status -sb
python -m unittest -v tests.test_editor_system
python .\luna-longform-editor\scripts\record_desktop.py devices
python .\luna-longform-editor\scripts\capture_window_storyboard.py windows
python .\luna-longform-editor\scripts\production_director.py --help
```

Use `PROMPT_FOR_CODEX_ON_WINDOWS.md` for the full production prompt once the
Windows environment and private voice inputs are ready.
