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
- Keep synthetic shot specifications immutable after validation. Recording and voice verdicts belong in sealed sidecars under `qa/reviews/`; never write mutable review state into `shot_plan.json`.
- Bind every synthetic review to the shot-spec hash and exact media bytes. A changed instruction, crop, recording, or voice file makes the corresponding review stale and requires a new verdict.
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
3. Read the job's `channel_profile.json`, then write `project.json` and a schema-version 4 `plans/shot_plan.json`. Each shot must contain purpose, rationale, continuity, narration, exact computer actions, the required visible result, timing limits, target/include boxes, an explicit `claim_support` mapping, capture checkpoints, retake triggers, a creator-style rationale, and a `voice_performance` contract. That voice contract binds tag-bearing xAI text to the same approved spoken words, speed, a creator-profiled WPM range, delivery intent, pronunciation checks, and concrete audio retake triggers. Explain how the visible pixels support the spoken claim; repeating the topic on screen is not proof. Do not add inline review blocks. Validate and seal the immutable specification:

   ```bash
   python3 scripts/validate_shot_plan.py --shot-plan plans/shot_plan.json --project project.json --report qa/shot_plan_validation.json
   ```

4. Audit the full narration against the creator profile before generating audio. The audit checks story order, duplicate wording, timing feasibility, action/claim alignment, and measured Luna language behavior without requiring copied sentences:

   ```bash
   python3 scripts/audit_creator_fidelity.py plan --shot-plan plans/shot_plan.json --project project.json --channel-profile channel_profile.json --report qa/creator_fidelity_plan.json
   ```

   At low confidence, one accepted video supplies guidance and short exemplars only. At three accepted videos, learned ranges may become enforceable. Direct feedback and evidence rules always outrank imitation. Review claims, order, and CTA before generating audio; topic text is not proof of a measured result.
5. The user must create and verify their own custom voice in the xAI console. Store the resulting ID in `XAI_VOICE_ID` and the API key in `XAI_API_KEY`. Console creation is the normal non-Enterprise path; do not claim API voice creation is available when the team is not enabled for it. Do not scrape or create a custom voice from anyone except the consenting owner. An accepted owner recording can be prepared for the console with:

   ```bash
   python3 scripts/prepare_voice_reference.py --input accepted-video.mp4 --transcript-json transcript.json --output owner-reference.wav --report owner-reference-report.json --owner-consent-confirmed
   ```

   The prepared reference still requires an actual listening/privacy review before upload.

6. Use the resumable director as the canonical control loop:

   ```bash
   python3 scripts/production_director.py --job "/path/to/job" --execute-safe
   ```

   It derives the next state from evidence on disk, runs deterministic safe stages, and stops at the next semantic action: voice ownership/configuration, listening review, Computer Use recording, visual review, or adversarial final review. Complete that action, then run the same command again. Never skip ahead by manually declaring a stage complete.

7. When narration is missing and the verified xAI environment is configured, the director generates only missing or stale voice files and preserves current reviewed takes. The equivalent manual command for one shot is:

   ```bash
   python3 scripts/xai_voiceover.py synthesize-plan --shot-plan plans/shot_plan.json --output-dir voice --shot-id shot-001 --owner-consent-confirmed
   ```

   The client verifies the selected custom voice before synthesis, retries transient xAI failures, requests WAV output and timestamps, and writes request/media metadata without secrets. It measures actual WPM and may generate a corrected-speed take when the first attempt misses the approved range. A failed request cannot overwrite an existing good take. If automatic correction still misses, revise the evidence-bound voice contract instead of regenerating unchanged settings. Transcribe each generated shot, run `audit_voiceover.py`, then listen to the exact bytes. Seal a voice review only when wording, product-name pronunciation, measured cadence, identity, emotional delivery, and audio integrity all pass.

8. For each shot:
   - Start `scripts/record_desktop.py`.
   - Use Computer Use to perform only the actions listed in the shot.
   - Continue until the required visual state is clearly visible.
   - Stop recording and inspect the actual captured frames. Accessibility success is not foreground proof; reject a take if the recorder captured Codex or another app.
   - When Computer Use can operate an app but the full-screen recorder cannot see it, use `capture_window_storyboard.py windows` to identify the exact macOS or Windows app by owner/process, title, or window ID. Capture the clean state after each consequential action, inspect the actual image pixels, then render those reviewed states to the shot's MP4 path. The state storyboard is a deliberate edited tutorial shot, not permission to omit required steps.
   - Retake if the cursor hunts, a dialog is obscured, private information appears, the promised state is absent, or the narration claim and visible result disagree.
9. Seal recording and voice reviews with `seal_production_review.py`. Review sidecars must contain concrete notes and bind the current shot-spec hash, media hash, and evidence frames/audit. Never hand-edit a stale hash into passing state.
10. Run `production_director.py --execute-safe` again. It assembles only sealed shots, applies the generated focus zooms, transcribes the exact final candidate, audits that transcript against the approved script and creator profile, builds fail-closed QA templates, and pauses for adversarial frame/gap review.
11. Complete every timeline and zoom verdict. If the adversarial pass finds a mismatch or the final creator-fidelity report rejects repeated, changed, generic, or off-pace narration, repair the responsible script/shot, allow the hashes to invalidate downstream evidence, and resume. Acceptance and cleanup happen only after the rebuilt candidate passes every gate.

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
- `capture_window_storyboard.py`: capture exact macOS or Windows app states by owner/process, title, or window ID when full-screen recording cannot see the Computer Use target, reject blank captures, then render a deterministic MP4 shot.
- `production_director.py`: resumable evidence-derived state machine for synthetic production.
- `production_evidence.py`, `validate_shot_plan.py`, `seal_production_review.py`: immutable shot hashes and exact-media review gates.
- `creator_fidelity.py`, `audit_creator_fidelity.py`: measurable creator fingerprint plus plan/final likeness and narration/visual-contract gates.
- `prepare_voice_reference.py`: consent-gated 90-120 second owner-reference preparation for xAI custom voice setup.
- `xai_voiceover.py`: consent-gated xAI custom-voice narration, including per-shot generation.
- `audit_voiceover.py`: reject synthesized shots with missing, changed, or repeated wording before assembly.
- `assemble_shot_plan.py`: synchronize shot recordings with narration and reject unnatural timing.
- `learn_channel_style.py`: learn portable pacing, language, section, loudness, scene-density, and compression measurements from accepted videos without retaining full transcripts.
- `verify_final_video.py`: fail-closed technical, speech, creator-fidelity, plan, and visual acceptance report.
- `tighten_spoken_pacing.py`, `snap_keep_list_to_audio.py`, `render_keep_list.py`: precise cut mechanics after semantic approval.
- `apply_intro_slate.py`, `apply_focus_zoom.py`: restrained finishing passes.
- `cleanup_edit_artifacts.py`: job-scoped cleanup.

After Colin accepts a tutorial, rerun `learn_channel_style.py` with every accepted tutorial and the canonical `channel_profile.json` as the base. Never train on raw footage, gameplay-only clips, rejected drafts, or merely available files.

## Delivery Report

Report the final path, before/after duration, story decisions, duplicate takes resolved, revision count, intro behavior, zoom targets, voice provider/voice ID status without secrets, transcript audit, visual review, decode result, and cleanup scope. State any remaining unknown as a limitation; never convert it into a pass.
