# Copy/Paste Prompts For Codex On Windows

## Edit An Existing Recording

```text
Use the luna-longform-editor skill. If it is not installed, install it from ".\luna-longform-editor" and run ".\luna-longform-editor\scripts\setup_windows.ps1" first.

Edit this Luna long-form video:
"C:\path\to\video.mov"

Create an isolated Luna job and follow the skill's evidence-driven workflow. Build the dossier, inspect transcript and visual evidence, resolve duplicate takes, and write a reasoned edit plan where every kept range has a story role, viewer purpose, continuity note, take-choice rationale, and evidence. Validate before rendering. Tighten and waveform-snap only approved ranges. Review the rendered transcript and every zoom region, run the adversarial viewer pass, revise until all technical, speech, plan, and visual gates pass, then clean only this job's intermediates.
```

## Record And Narrate It For Me

```text
Use the luna-longform-editor skill in synthetic mode.

Topic: <exact video topic>
Goal: produce a publishable Luna Tweak long-form tutorial without me recording it.

Research current software behavior from primary sources. Read channel_profile.json, then write a schema-version 3 project, Luna-style narration, and a schema-version 4 immutable per-shot plan. Each shot must list the exact Windows actions, required visible result, narration, timing limit, target/include boxes, spoken-claim-to-visible-evidence contract, capture checkpoints, visual retake triggers, creator-style rationale, and a voice-performance contract. The voice contract must preserve exactly the approved spoken words while specifying sparse xAI tags, request speed, a creator-profiled WPM range, delivery intent, pronunciation checks, and audio retake triggers. Validate the plan and pass audit_creator_fidelity.py plan, then use production_director.py --execute-safe as the resumable control loop. Use the desktop recorder while you operate Windows, inspect the actual captured pixels, and retake confusing or background-only shots. If full-screen capture cannot see the operated app, use capture_window_storyboard.py to list windows, select the intended process/title or exact handle, capture every consequential state, inspect every image, and render the reviewed states to the shot path. Use only my verified xAI custom voice. If the clone is not created yet, prepare a source-bound 24 kHz owner reference, transcribe the exact WAV, listen from beginning to end, and upload only after seal_voice_reference_review.py verify reports upload_ready. After upload, run xai_voiceover.py register so xAI's stored source audio must hash-match the reviewed WAV; never hand-author voice_registration.json. Require the live source-hash recheck for each real narration batch. Preserve current reviewed takes, generate only stale shots, and use measured cadence correction rather than repeatedly synthesizing unchanged settings. Transcribe and audit every generated line, then run the evidence-bound Grok audio audit so it actually hears an exact registered-reference excerpt and the exact candidate. Automatically seal only a strict, confident pass covering pronunciation, identity, cadence, emotion, complete words, stutters, and artifacts. Change the voice-performance contract for concrete failures instead of retrying unchanged settings, and use a real human listening fallback for invalid, uncertain, or timed-out model results. Seal recording and voice reviews as exact-media sidecars, never inline flags. Assemble shots only when the visual action and narration timing agree and both sealed reviews pass. Apply sparse focus zooms, transcribe the final, pass the exact-transcript creator-fidelity audit, complete every generated frame/gap verdict, run the adversarial viewer pass, and do not accept or clean up until every QA gate passes.
```

After the skill is installed once, the short version is:

```text
Use the luna-longform-editor skill on "C:\path\to\video.mov" and run its full job-scoped evidence, revision, and acceptance workflow.
```
