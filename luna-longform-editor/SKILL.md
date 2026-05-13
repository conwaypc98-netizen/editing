---
name: luna-longform-editor
description: Professional AI editing workflow for Luna Tweak long-form YouTube videos. Use when Codex needs to intelligently edit, cut down, tighten, or revise raw Luna screen recordings or CapCut exports by preserving story flow, removing stutters, repeated takes, bad duplicate explanations, long pauses, reference-video watching, dead air, and low-value sections. This skill must be used for semantic editing of 16:9 Luna videos, especially when turning 15-30 minute recordings into clean 3-8 minute finished edits.
---

# Luna Longform Editor

## Role

Act like a professional editor, not a silence-removal script. Use tools to see/hear/transcribe the video, but make the editing decisions yourself.

The core question before every cut is: **does this make the video smoother, clearer, and more watchable while preserving the correct timeline?**

## Default Workflow

1. Preserve any user-declared finished section, such as "the intro is edited until 57 seconds." If the user later asks to revise the intro, edit it like any other section.
2. Inspect the video technically and visually:
   - Run `scripts/analyze_video.sh <video>` on macOS/Linux or `scripts/analyze_video.ps1 <video>` on Windows.
   - Open the contact sheet.
   - Check `ffprobe.txt` and `volume.txt`.
3. Create a transcript with timestamps whenever speech quality matters:
   - Use `scripts/transcribe_video.sh <video>` on macOS/Linux or `scripts/transcribe_video.ps1 <video>` on Windows if available in the environment.
   - If transcription is unavailable, set it up or clearly say that only a rough pause-based pass is possible.
4. Build a **cut list** before rendering:
   - Keep the best, most fluent version of repeated explanations.
   - Cut worse duplicates, false starts, stutters, restarts, and "let me say that again" moments.
   - Keep small natural pauses when they make the video breathe.
   - Remove long dead air, loading, waiting, and off-task browsing.
   - Remove sections where the user watches a reference YouTube video unless the user explicitly wants that context.
   - Keep the video in chronological order unless a deliberate restructuring is clearly better.
5. Tighten the cut mechanics before rendering:
   - Do not trust rounded transcript display times. Use word-level timestamps from `transcript.json`.
   - Run `scripts/tighten_spoken_pacing.py` on the semantic keep list before final snapping. This catches overlong word-to-word gaps and stretched short words that hide pauses inside tokens such as "it", "a", "and", or "or".
   - Run `scripts/snap_keep_list_to_audio.py` to align cut starts/ends to nearby low-energy waveform points.
   - Leave a small tail after the last spoken word so syllables are not clipped.
   - Check pause warnings from the snap script; split or shorten awkward pauses unless the visual needs the viewer to wait.
   - For tight Luna pacing, rendered speech should usually have no unexplained spoken gap over roughly 0.55s. Lists, hooks, and repeated-take cleanup should be tighter than ordinary sentence breaks.
6. Sanity-check the edit as a story:
   - Hook/setup -> what tweak is being tested -> before/after or run test -> result/proof -> conclusion.
   - No repeated explanation unless the second mention adds new information.
   - No jump that makes the viewer ask "how did we get here?"
7. Render from the refined cut list only after the editorial decisions are made.
8. Apply the intro slate when the intro is spoken over generic setup or when the user asks for the Luna intro image:
   - Detect the spoken intro boundary from the rendered transcript. The intro usually ends when the video shifts from hook/setup into the tutorial, often around "Alright guys..." or "before you run/apply..."
   - Use `scripts/apply_intro_slate.py` with the default asset `assets/luna_intro_background.png`, unless the user provides a different image.
   - The script extracts the edited intro audio and renders that audio over the still image, then continues with the normal edited video after the intro boundary.
   - Do not use the slate over the tutorial/body section where the viewer needs to see clicks, app state, benchmark progress, or results.
9. Add smart focus zooms after the intro when the tutorial UI would be hard to read:
   - Read `references/focus-zoom-rules.md` before planning zooms.
   - Use transcript context plus contact sheets/screenshots to decide what the viewer needs to see: tweak utility, Windows Settings panel, Device Manager, NVIDIA Control Panel, browser download button, benchmark/result area, or software control.
   - Build a sparse zoom plan. Keep zooms modest and stable; do not bounce in and out for every click.
   - Use `scripts/apply_focus_zoom.py` on the intro-slate/final visual pass. Exclude the intro slate itself unless the user explicitly wants a zoom there.
   - If the target is unclear, keep the full frame instead of guessing and hiding important context.
10. Verify the final duration, decode health, contact sheet, and transcript from the output.
11. Run a listening-style QA loop:
   - Transcribe the rendered edit.
   - Run `scripts/audit_output_pacing.py` on the rendered transcript.
   - Read the rendered transcript for repeated words, duplicate phrases, held short words, and awkward sentence/list gaps.
   - If the output still has obvious stutters or unnatural delays, revise the keep list and rerender instead of handing it over.
12. After the final MP4 is accepted for delivery, clean generated artifacts:
   - Run `scripts/cleanup_edit_artifacts.py --final-output <final.mp4> --delete`.
   - Keep only the delivered final MP4 in the output root unless the user explicitly asks to preserve drafts, contact sheets, keep lists, transcripts, or analysis files.
   - Never delete the user's original source recording.

## Do Not

- Do not blindly cut every pause.
- Do not treat silence detection as the editor.
- Do not remove all breathing room.
- Do not keep both versions when the speaker says the same idea twice because of a stutter or restart.
- Do not keep reference-video watching unless it directly helps the final viewer.
- Do not output a video that is shorter but confusing.
- Do not cut exactly at rounded second marks if speech is nearby.
- Do not accept clicks/static/noisy joins; rerender with audio-safe cut settings.
- Do not leave a delay that makes the speaker sound like separate clips stitched together when the line should feel like one natural sentence.
- Do not add constant aggressive zoom, rapid zoom pumping, or focus crops that hide the UI element being discussed.
- Do not zoom during the intro slate unless explicitly requested.

## Editorial Rules

Prefer clean, high-retention pacing:

- Cut a bad take when a more fluent duplicate appears soon after.
- If the speaker starts a sentence, pauses, and says it again better, keep the better version and remove the worse one.
- If a sentence has a small stutter but the meaning is clear and cutting would sound unnatural, keep it.
- If a long pause is used to show the app processing or a result appearing, keep only enough of it for the viewer to understand what happened.
- If a pause feels awkward in the final transcript/output, shorten it even if it is not technically silent.
- If a short word is stretched across a long pause in the transcript, treat it as a hidden pause/stutter and tighten around the real vocal sound.
- Keep proof moments: test start, tweak selection, before/after metrics, final results, and any clear payoff.
- Remove low-value mouse wandering, menu searching, typing delays, and waiting screens unless needed for comprehension.
- During tutorial/body sections, lightly zoom toward the UI being showcased when it improves readability. Keep the zoom centered on the actual setting, button, utility, benchmark, or panel being explained.

## Scripts

Bundled scripts live in `scripts/`:

- `setup_free_editor.sh`: install/check Auto-Editor.
- `setup_windows.ps1`: create the Windows transcription venv and check FFmpeg/Python prerequisites.
- `install_windows_skill.ps1`: copy this skill into `%USERPROFILE%\.codex\skills\luna-longform-editor` and optionally run Windows setup.
- `make_edit_proxy.sh`: convert source video to clean H.264/AAC.
- `analyze_video.sh`: create probe files, volume check, contact sheet, edit proxy, and rough duration previews.
- `analyze_video.ps1`: Windows video inspection, contact sheet, volume check, and proxy creation.
- `rough_cut_luna.sh`: render quick pause-based drafts only.
- `transcribe_video.sh`: extract audio and create a timestamped transcript when local transcription is available.
- `transcribe_video.ps1`: Windows transcript wrapper using the same faster-whisper Python transcriber.
- `tighten_spoken_pacing.py`: split a semantic keep list around unnatural internal speech gaps and held short words before final snapping.
- `snap_keep_list_to_audio.py`: refine a semantic keep list using word-level timestamps and waveform low-energy points; use this before final rendering.
- `audit_output_pacing.py`: scan the rendered transcript for long gaps, held short words, adjacent repeats, and repeated short phrases.
- `render_keep_list.py`: render an intelligent edit from Codex's keep-list decisions.
- `apply_intro_slate.py`: detect the intro boundary from the rendered transcript, extract the intro audio, and replace the intro visuals with a still image.
- `apply_focus_zoom.py`: apply a sparse, smooth focus-zoom plan after the intro so tutorial UI is easier to see without nauseating zoom motion.
- `cleanup_edit_artifacts.py`: after the final output is accepted, delete generated drafts, keep lists, contact sheets, transcript folders, and analysis files while keeping the final MP4.

Quick pause drafts are allowed for exploration, but the final Luna edit should use a transcript/visual review, spoken-pacing tightening, and a snapped keep list. `render_keep_list.py` uses frame/sample-accurate FFmpeg trim filters and tiny audio fades at edit points to avoid clipped words and static clicks.

## Reference Files

- Read `references/professional-editing-rules.md` before making semantic cuts.
- Read `references/focus-zoom-rules.md` before adding tutorial focus zooms.
- Read `references/style-notes.md` when applying saved channel preferences.

## Reporting

When done, include:

- final MP4 path
- duration before and after
- what sections were preserved
- what kind of cuts were made
- whether spoken-pacing tightening, word-level/audio-boundary snapping, rendered transcript audit, and decode verification were run
- whether intro slate detection/audio extraction was applied, including the detected intro duration
- whether smart focus zooms were applied, including the number of zoom regions and the main targets
- whether generated edit artifacts were cleaned up
- any known limitations, especially if no transcript was available
