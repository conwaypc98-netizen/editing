# Autonomous Luna Production Rules

This workflow has two production modes. Both modes use the same evidence, review, and acceptance gates.

## Mode A: Edit A Human Recording

The source contains the creator's screen and voice. Build an evidence dossier, choose the best takes semantically, align cuts to words and waveform valleys, render, and review the rendered result.

## Mode B: Record And Narrate Autonomously

The agent writes a project brief and a shot plan before touching the desktop. Each shot must specify:

- The sentence the viewer will hear.
- The exact desktop action to demonstrate.
- The visual state that proves the action succeeded.
- Any UI regions that must stay visible.
- A maximum acceptable duration and whether timing may be adjusted.
- A structured spoken-claim-to-visible-evidence contract.
- Consequential capture checkpoints and shot-specific retake triggers.
- A creator-style rationale grounded in the learned profile without copying an old script.
- A voice-performance contract containing the exact approved spoken words with sparse xAI tags, planned speed, target WPM range, delivery intent, pronunciation checks, and audio retake triggers.

Validate this immutable shot specification and run `audit_creator_fidelity.py plan` before generating media. Do not store recording or voice verdicts in the shot plan. Seal them separately under `qa/reviews/recording/` and `qa/reviews/voice/`, bound to both the current shot-spec hash and exact media bytes.

Record shots separately. A failed or confusing shot is retaken; it is not hidden by narration. Generate cloned-voice narration per shot, then assemble only when the shot duration and narration duration are compatible.

Run `production_director.py --job <job> --execute-safe` as the resume loop. It may automate validation, xAI generation, transcription, audits, assembly, zoom rendering, QA generation, and acceptance. It must stop for ownership/configuration, actual voice listening, Computer Use, exact visual review, or a failed adversarial gate. Completing one of those actions does not authorize inventing evidence for another.

## Director Contract

The agent is responsible for the finished viewing experience, not merely valid files. Before a plan may render, every kept segment or synthetic shot needs:

- A story role: hook, setup, tutorial, proof, transition, or CTA.
- A concrete viewer purpose.
- Transcript or frame evidence.
- A continuity explanation for any non-obvious jump.
- A take-choice explanation when duplicate wording exists.

The validator must reject plans that lack these fields. "It looked okay" is not evidence.

## Evidence Passes

1. Technical pass: streams, duration, resolution, frame rate, loudness, decode health.
2. Language pass: word timestamps, stutters, false starts, duplicate takes, claims, instructions, proof, CTA, and creator-fingerprint evidence.
3. Visual pass: sampled frames around every spoken block and every candidate cut; identify the active app, action, dialog, result, and edge UI.
4. Continuity pass: confirm that every instruction has the required preceding state and visible result.
5. Cut-mechanics pass: snap boundaries to low-energy points, retain complete phonemes, and prevent clicks.
6. Viewer pass: review the rendered transcript and representative frames as if seeing the video for the first time.
7. Adversarial pass: try to find one reason the video feels synthetic, confusing, repetitive, clipped, visually lost, or unlike Luna. Fix it and rerun acceptance.

## Creator Twin Rules

- Learn only from finals Colin accepted. Never learn from raw footage, rejected drafts, or a technically passing render that he did not approve.
- Preserve direct feedback as hard rules. Learned statistics never override clarity, truthful claims, privacy, or visible proof.
- One accepted tutorial is low-confidence evidence. Use its measurements as review guidance, not a sentence-copying template. Three accepted tutorials permit medium-confidence range checks; eight permit high-confidence checks.
- Store portable aggregate measurements and short hook/transition/CTA/sign-off exemplars. Do not retain full transcripts in the installed profile.
- Measure outcome-hook length, tutorial/CTA proportions, WPM, action density, viewer address, transition language, filler rate, contractions, scene density, speech gaps, and loudness.
- Run the plan audit before voice generation and the final audit against the exact rendered transcript. Changing the profile, plan, or transcript invalidates the corresponding report.

## Voice Clone Rules

- Only use a voice that the owner created or verified through xAI's consent flow.
- Never create a clone from scraped or pre-existing recordings of another person.
- Before console upload, bind the selection transcript to the exact source, transcribe the exact 90-120 second prepared WAV, listen from beginning to end, and seal the exact-byte privacy/quality review. Upload only when `seal_voice_reference_review.py verify` returns `upload_ready: true`.
- Reject a reference containing another speaker, music, notifications, private speech, clipped words, edit artifacts, or delivery that is not representative of Luna tutorials. Automated transcript/audio checks cannot substitute for listening.
- Store only the `voice_id`; API keys remain in environment variables.
- Generate narration per shot so pacing can be directed with xAI speech tags and visual timing can be verified.
- Generate only missing or stale shots. Never overwrite current reviewed takes merely because another shot changed.
- Measure generated WPM against the shot contract, allow bounded automatic speed correction, and stop for direction changes if corrected delivery still misses the range.
- Keep tags sparse and tutorial-appropriate. The tag-stripped words must exactly match the approved narration.
- Listen or transcribe the synthesized narration before assembly. Reject mispronounced product names, unnatural emphasis, missing words, and cadence that does not match the channel profile.
- Require both a passing transcript-comparison report and an explicit per-shot listening review before assembly.
- Verify the configured xAI voice before synthesis. Preserve xAI timestamps and request metadata, retry only transient 429/500/503 failures with bounded exponential backoff, and never log the API key.
- Treat changing a narration line or replacing its audio bytes as automatic invalidation of the listening verdict.

## Desktop Recording Rules

- Start from a clean desktop state with notifications and unrelated personal windows hidden.
- Record at the final frame rate and aspect ratio when practical.
- Never type passwords, recovery codes, payment data, or private messages on camera.
- The cursor must move deliberately. Remove hunting, repeated clicks, accidental menus, and waiting.
- Keep a shot running until the required visual state is visibly achieved.
- Restore changed settings when the shot is only a demonstration and the project brief requires restoration.
- Accessibility actions can succeed while another app remains in the screen recording. Inspect the recorded pixels before sealing the shot; never infer foreground capture from the accessibility tree.
- On macOS or Windows, when the Codex host prevents the operated app from appearing in full-screen capture, use `capture_window_storyboard.py windows` to identify the target by owner/process, title, or exact window ID. Capture the exact window after each consequential action and render those evidence states into the shot MP4. Restore minimized windows first, inspect every captured image rather than trusting API success, and keep enough states to show prerequisites, action, and result. This is a capture fallback, not a substitute for doing the action.

## Retake And Invalidation Loop

1. Plan and validate the immutable shot specification.
2. Generate/capture media.
3. Audit words and inspect pixels from the exact media.
4. Seal voice and recording sidecars.
5. Assemble and run final evidence generation.
6. Try to disprove the result: wrong app, stale state, claim/visual mismatch, private content, clipped narration, awkward cadence, bad crop, or synthetic-looking transition.
7. If any item fails, revise the responsible plan or media. Do not preserve downstream passing flags; their hashes must become stale and the director must rebuild them.

## Acceptance Gates

A final video is not accepted unless:

- It fully decodes and contains expected video and audio streams.
- Integrated narration loudness is publish-ready and true peak does not clip.
- The rendered transcript has no unresolved repeated take, clipped sentence, or unexplained awkward speech gap.
- The exact final transcript matches the approved narration and passes the current creator-fidelity report.
- Every speech gap above the project threshold is removed or explicitly justified against visible viewer needs.
- Every instruction still has enough visual context to follow.
- Every focus crop contains its target and required context.
- The story includes the project brief's required roles.
- Claims and proof agree with what is visible.
- A final report records pass, fail, or unknown for each gate. Unknown is not a pass.

The final MP4 may be delivered only after all required gates pass. Cleanup is job-scoped and must never delete another project's outputs.
