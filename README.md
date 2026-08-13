# Luna Longform Director

Portable Codex skill for evidence-driven Luna Tweak video production on macOS and Windows.

It supports two workflows:

- Intelligent editing of an existing recording using transcript, frame, duplicate-take, continuity, waveform, and rendered-output evidence.
- Autonomous production where Codex writes a shot plan, records each desktop demonstration, generates per-shot narration with a verified xAI custom voice, has Grok audibly compare each take with the exact registered owner reference, assembles the shots, and refuses delivery until technical, speech, plan, and visual review gates pass.

This is deliberately not a silence remover. Every kept range must explain its story role, viewer purpose, take choice, continuity, and evidence. Final visual review is fail-closed: an unreviewed crop or timeline is not considered passing.

Synthetic production is resumable rather than conversationally memorized. `production_director.py` derives the next stage from an immutable shot-plan hash, exact media hashes, sealed recording/voice sidecars, transcripts, creator-fidelity reports, and final QA files. Replacing media, changing a shot, or updating the creator profile automatically makes affected reviews stale.

The bundled creator profile now contains low-confidence measurements from one accepted Luna tutorial: outcome-first hook timing, tutorial/CTA proportions, speech pace, action and transition density, viewer address, scene density, speech gaps, and loudness. It retains only aggregate measurements and short exemplars, not the full transcript. Learned constraints become strict only after enough accepted examples; direct feedback and truthful visible proof remain hard requirements.

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
Use the luna-longform-editor skill in synthetic mode. Build a schema-version 3 project and schema-version 4 shot plan for this topic: <topic>, including claim-support mappings, capture checkpoints, retake triggers, creator-style rationales, and evidence-bound voice-performance contracts. Validate it and pass the creator-fidelity plan audit, then resume with production_director.py --execute-safe. Use Computer Use to produce each desktop shot, seal exact-media recording and voice reviews, register my verified xAI voice against the exact reviewed source WAV, and keep resuming until the rebuilt final passes the adversarial creator-fidelity, visual, transcript, loudness, decode, plan, and zoom gates. Never infer that a background app was captured; inspect the actual pixels and retake or use the macOS/Windows exact-window state-storyboard fallback.
```

The one-time voice step is creating and verifying your own custom voice in the xAI console. Direct API creation is restricted to enabled Enterprise teams, so the package uses the console path instead of promising an unavailable free API workflow. After that, set `XAI_API_KEY` and `XAI_VOICE_ID`; the director downloads xAI's stored source audio, requires its SHA-256 to equal the exact human-reviewed WAV, and writes `voice/voice_registration.json`. It repeats that source-hash check before each real generation batch, generates only stale per-shot WAV/timestamp metadata, corrects measurable cadence when possible, and transcribes each result. For current xAI projects it then sends an exact registered-reference excerpt and the exact candidate to the pinned Grok Voice model for strict pronunciation, cadence, identity, emotion, clipping, stutter, and artifact review. Passing reports seal automatically; failed reports require a changed take, and inconclusive reports require a human fallback. This listening stage uses normal paid xAI API calls. API keys are never stored in the repo.

An owner-consented reference from an accepted video can be prepared locally with:

```bash
python3 luna-longform-editor/scripts/prepare_voice_reference.py --input accepted-video.mp4 --transcript-json transcript.json --output owner-reference.wav --report owner-reference-report.json --owner-consent-confirmed
```

Transcribe the exact prepared WAV, listen to it from beginning to end, and run `seal_voice_reference_review.py seal` followed by `verify`. Upload only when the exact-byte review reports `upload_ready: true`; the seal rejects stale preparation/transcript/audio evidence and requires explicit verdicts for owner identity, background audio, privacy, representative delivery, clipped words, and edit artifacts.

After uploading that exact WAV in the xAI console, bind the returned voice ID to it:

```bash
python3 luna-longform-editor/scripts/xai_voiceover.py register --reference voice/owner-reference.wav --preparation-report voice/owner-reference-report.json --transcript-json voice/owner-reference-transcript/transcript.json --reference-review voice/owner-reference-review.json --output voice/voice_registration.json --owner-consent-confirmed
```

For an existing synthetic job, the canonical resume command is:

```bash
python3 luna-longform-editor/scripts/production_director.py --job "/path/to/job" --execute-safe
```

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
- `tests/`: regression tests for job isolation, cleanup safety, plan/media integrity, intro evidence, voice consent, exact-reference Grok listening, creator-fidelity learning, claim contracts, shot review, and final acceptance.

Raw videos, rendered outputs, transcripts, FFmpeg binaries, and Python virtual environments are intentionally not committed.
