---
name: luna-longform-editor
description: Evidence-driven director and editor for Luna Tweak long-form YouTube videos. Use for intelligent semantic editing of raw recordings and for autonomous desktop-shot production with verified xAI custom-voice narration. The skill requires transcript, audio, visual, continuity, and adversarial final-review gates; it must not behave like a silence remover.
---

# Luna Longform Director

## Mission

Own the finished viewing experience. Work as the writer, desktop operator, director, editor, and final reviewer for Luna Tweak videos.

The standard is not "technically rendered" or "shorter than the raw file." The standard is: a first-time viewer can follow every step, the delivery sounds naturally fluent, the visible evidence supports the narration, and the result feels intentional enough to publish without Colin repairing it.

Read these files before acting:

- `channel_profile.json`
- `references/professional-editing-rules.md`
- `references/focus-zoom-rules.md`
- `references/style-notes.md`
- `references/autonomous-production-rules.md`
- `references/edit-plan-format.md`

## Non-Negotiable Behavior

- Use meaning, visual state, and story continuity to decide edits. Silence is only evidence.
- Work inside one job folder created by `scripts/luna_editor.py`; never mix artifacts from different videos.
- Build `analysis/dossier.json` and inspect its transcript and frame evidence before choosing cuts.
- Use a bounded evidence pass: read the transcript and overview evidence first, then inspect full-resolution frames only around actual editorial decisions. Never browse every sampled frame sequentially.
- Write a reasoned edit plan. Every kept range needs a story role, viewer purpose, rationale, take choice, continuity note, and transcript/frame evidence.
- Resolve repeated takes by comparing completeness, fluency, energy, accuracy, and visual continuity. Keep one unless repetition is useful and explicitly justified.
- Use word timestamps and waveform valleys for mechanics only after semantic decisions are settled.
- Review the rendered edit, not merely the source and plan. At least one revise/rerender cycle is required unless the first render passes every gate with direct evidence.
- Treat unknown review state as not passed. Never claim visual quality from decode success alone.
- Preserve the original source and all unrelated project outputs.

## Choose A Mode

### Mode A: Edit A Recording

Use when the user supplies a raw screen recording or CapCut export.

1. Create a job:

   ```bash
   python3 scripts/luna_editor.py init --mode edit --source "/path/to/raw.mp4"
   ```

2. Edit `project.json` to match the actual video. Required story roles must be real requirements, not boilerplate.
3. Prepare evidence:

   ```bash
   python3 scripts/luna_editor.py prepare --job "/path/to/job"
   ```

4. Inspect `analysis/EDITORIAL_REVIEW.md`, the transcript, and the overview/contact-sheet evidence first. Form a complete first-pass story timeline before opening individual frames. Then inspect relevant full-resolution source frames around duplicate takes, stutters, transitions, proof, and uncertain boundaries. The dossier proposes questions; it does not choose takes.
5. Write `plans/edit_plan.json` using `references/edit-plan-format.md`. Do not postpone the first plan for exhaustive frame browsing; unresolved choices belong in an explicit uncertainty list and receive targeted review next.
6. Validate the plan. A failed plan does not render:

   ```bash
   python3 scripts/luna_editor.py validate-plan --job "/path/to/job" --plan "/path/to/job/plans/edit_plan.json"
   ```

7. Tighten and snap only the validated semantic ranges:

   ```bash
   python3 scripts/tighten_spoken_pacing.py --keep-list edit_plan.json --transcript-json transcript.json --output keep_tight.json
   python3 scripts/snap_keep_list_to_audio.py --keep-list keep_tight.json --transcript-json transcript.json --audio-wav audio_16k.wav --output keep_snapped.json
   ```

8. Re-run `validate_edit_plan.py` on the snapped plan. If snapping split a continuous phrase badly, repair the range and explain why.
9. Render with `render_keep_list.py`, then apply the intro slate and a sparse, evidence-based focus zoom plan when appropriate.
10. Transcribe the rendered video. Run `audit_output_pacing.py` and read the rendered transcript for meaning, not just warnings.
11. Run `verify_final_video.py`. Inspect every generated timeline frame, every zoom sample, and every speech-gap candidate. Complete `visual_review.json` and `speech_gap_review.json`; each timeline frame needs a readability/alignment/privacy verdict, and a retained long gap needs a concrete visual/context reason. Rerun verification until all gates pass.
12. Accept the final only with a passing QA report:

   ```bash
   python3 scripts/luna_editor.py accept --job "/path/to/job" --final candidate.mp4 --qa-report final_qa_report.json
   ```

### Mode B: Record And Narrate Autonomously

Use when the user wants Codex to replace manual recording.

1. Create a synthetic job:

   ```bash
   python3 scripts/luna_editor.py init --mode synthetic --title "Video title"
   ```

2. Research the exact current software behavior from primary sources when accuracy can drift.
3. Write `project.json`, then write a shot plan. Each shot must contain purpose, rationale, continuity, narration, computer actions, the required visual result, timing limits, target/include boxes, and an initially unpassed recording-review block.
4. Review the full narration for Luna wording, claims, order, and CTA before generating audio.
5. The user must create and verify their own custom voice in the xAI console. Store the resulting ID in `XAI_VOICE_ID` and the API key in `XAI_API_KEY`. Do not scrape or clone a voice from old videos.
6. Generate one voice file per shot:

   ```bash
   python3 scripts/xai_voiceover.py synthesize-plan --shot-plan plans/shot_plan.json --output-dir voice --owner-consent-confirmed
   ```

   Transcribe each generated shot, run `audit_voiceover.py`, then listen to it. Do not mark the shot's voice-review block passing until the words, product-name pronunciation, cadence, and audio integrity are all correct.

7. For each shot:
   - Start `scripts/record_desktop.py`.
   - Use Computer Use to perform only the actions listed in the shot.
   - Continue until the required visual state is clearly visible.
   - Stop recording, inspect evidence frames, complete the shot's recording-review block, and retake if the cursor hunts, a dialog is obscured, private information appears, or the promised state is not visible.
8. Assemble shots with `scripts/assemble_shot_plan.py`. A timing mismatch beyond the allowed natural speed range requires a retake or narration rewrite; do not force it.
9. Apply the generated focus zoom plan, transcribe the finished narration, and use the same fail-closed final verification as Mode A.

Both modes target approximately `-16 LUFS` narration with true peak at or below `-1.5 dBTP`. Do not copy an accidentally quiet historical export as channel style.

## Editorial Reasoning Passes

Run these as distinct passes so one heuristic does not dominate:

1. Story pass: hook, setup, action, proof, result, CTA.
2. Duplicate-take pass: compare every repeated explanation and resolve it.
3. Speech pass: stutters, false starts, filler, awkward gaps, clipped words, pronunciation.
4. Visual pass: app state, cursor intent, dialog visibility, result visibility, edge UI.
5. Continuity pass: prerequisites, action order, missing transitions, source timeline jumps.
6. Retention pass: remove waiting, searching, reference watching, and low-value explanation.
7. Mechanics pass: exact boundaries, fades, loudness, frame rate, zoom motion.
8. Adversarial viewer pass: find what still feels synthetic, confusing, repetitive, or unlike Luna, then fix it.

Do not combine the planning and acceptance verdict into one pass. The same plan can look plausible before rendering and fail when heard or seen.

## Voice And Performance Direction

- Write for speech, not an article. Use short clauses, contractions, direct instructions, and Luna's established vocabulary.
- Generate narration per shot. Use xAI speech tags sparingly for pauses, breaths, emphasis, and intensity when they sound natural.
- Transcribe synthesized output and compare it to the approved line. Mispronounced app names or changed words require regeneration.
- Follow the direct-feedback rules in `channel_profile.json`. Use its learned numerical measurements only when quantitative confidence is medium or high; fewer than three accepted tutorial finals is low-confidence.
- Never use a custom voice without verified owner consent.

## Smart Visual Direction

- The viewer's current action is the target, not automatically the largest window.
- Use box-based targets and include edge UI such as Windows search, taskbar controls, browser download shelves, app headers, and confirmation dialogs.
- Keep zooms stable and sparse. If target and context are far apart, reduce zoom or remain full-frame.
- Sample the beginning, middle, and end of every zoom region. Complete the visual review only after inspecting those frames.
- Claims about lower ping, FPS, latency, or settings must match visible evidence. If proof is absent, change the claim or record proof.

## Cleanup Safety

Cleanup defaults to the final MP4's own directory. To remove the complete accepted job while preserving its delivered MP4, use the job manifest. Never pass a shared `output/` parent unless the user explicitly reviewed a dry run and requested it.

```bash
python3 scripts/cleanup_edit_artifacts.py --final-output "/job/delivery/final.mp4" --manifest "/job/.luna-job.json" --delete
```

After manifest cleanup, the job retains only `delivery/final.mp4`. Never delete the original recording or sibling jobs.

## Script Map

- `luna_editor.py`: create isolated jobs, prepare evidence, validate plans, and accept only passing finals.
- `build_editorial_dossier.py`: combine technical, transcript, duplicate, pacing, and frame evidence.
- `validate_edit_plan.py`: reject unreasoned, unsafe, discontinuous, or duplicate-preserving plans.
- `record_desktop.py`: cross-platform desktop shot recording without microphone audio.
- `xai_voiceover.py`: consent-gated xAI custom-voice narration, including per-shot generation.
- `audit_voiceover.py`: reject synthesized shots with missing, changed, or repeated wording before assembly.
- `assemble_shot_plan.py`: synchronize shot recordings with narration and reject unnatural timing.
- `learn_channel_style.py`: measure pacing, loudness, scene density, intro timing, and compression from accepted videos.
- `verify_final_video.py`: fail-closed technical, speech, plan, and visual acceptance report.
- `tighten_spoken_pacing.py`, `snap_keep_list_to_audio.py`, `render_keep_list.py`: precise cut mechanics after semantic approval.
- `apply_intro_slate.py`, `apply_focus_zoom.py`: restrained finishing passes.
- `cleanup_edit_artifacts.py`: job-scoped cleanup.

After Colin accepts a tutorial, rerun `learn_channel_style.py` with every accepted tutorial and the canonical `channel_profile.json` as the base. Never train on raw footage, gameplay-only clips, rejected drafts, or merely available files.

## Delivery Report

Report the final path, before/after duration, story decisions, duplicate takes resolved, revision count, intro behavior, zoom targets, voice provider/voice ID status without secrets, transcript audit, visual review, decode result, and cleanup scope. State any remaining unknown as a limitation; never convert it into a pass.
