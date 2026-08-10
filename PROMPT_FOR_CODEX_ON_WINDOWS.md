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

Research current software behavior from primary sources. Write a Luna-style narration and immutable per-shot plan. Each shot must list the exact Windows actions, required visible result, narration, timing limit, and target/include boxes. Validate the plan, then use production_director.py --execute-safe as the resumable control loop. Use the desktop recorder while you operate Windows, inspect the actual captured pixels, and retake confusing or background-only shots. Use my verified xAI custom voice from XAI_VOICE_ID for per-shot narration; never use an unverified clone. Transcribe and audit every generated line, then listen for pronunciation, identity, cadence, emotion, and artifacts. Seal recording and voice reviews as exact-media sidecars, never inline flags. Assemble shots only when the visual action and narration timing agree and both sealed reviews pass. Apply sparse focus zooms, transcribe the final, complete every generated frame/gap verdict, run the adversarial viewer pass, and do not accept or clean up until every QA gate passes.
```

After the skill is installed once, the short version is:

```text
Use the luna-longform-editor skill on "C:\path\to\video.mov" and run its full job-scoped evidence, revision, and acceptance workflow.
```
