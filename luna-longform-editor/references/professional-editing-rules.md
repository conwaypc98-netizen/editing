# Professional Luna Editing Rules

Use these rules when converting raw Luna recordings into a finished edit.

## Cut Selection

- Prefer meaning and fluency over silence length.
- Mark candidate cuts from transcript, audio, and visuals before rendering.
- Use word-level timestamps and waveform/speech-energy checks for cut points. Rounded transcript times are not accurate enough for final cuts.
- Keep the better take when the same idea appears twice.
- Choose the take with fewer stutters, fewer filler words, clearer wording, and better setup/payoff.
- If both takes are useful, combine only if the second adds new information.
- Keep small pauses that feel natural or help the viewer track what is happening.
- Cut long pauses that are just searching, waiting, thinking, typing, or dead air.
- If a pause feels awkward on playback, shorten it even when it is not completely silent.
- Leave a tiny tail after spoken words. Clipped syllables are worse than keeping an extra tenth of a second.
- Push spoken pacing hard enough that the creator sounds naturally fluent, not like separate clips with small delays between them.
- Lists in hooks or intros should be especially tight. If "FPS, latency, CPU, RAM, storage" has large gaps, split around the gaps and tighten it.

## Cut Mechanics

- Start cuts slightly before the first word, preferably on a low-energy waveform point.
- End cuts slightly after the last word, preferably on a low-energy waveform point.
- Avoid cutting through breaths, consonants, word tails, mouse-click pops, or loud waveform peaks.
- Use tiny audio fades at joins to prevent static/click artifacts. This is not a stylistic dissolve; it is an artifact fix.
- If a rendered cut produces static, a pop, or an overlapped-sounding syllable, adjust the boundary and rerender.
- Audit internal pauses inside kept segments. If a segment contains a pause longer than roughly 0.7 seconds, keep it only when the visual needs that time.
- Audit stretched short words as hidden pauses. Whisper-style transcripts may assign dead air to words like "it", "a", "and", "or", "so", or "the"; tighten these as if they were normal pauses.
- After rendering, transcribe the output and check for spoken gaps over roughly 0.55 seconds. If the gap does not serve the visual or sentence rhythm, revise and rerender.

## Stutters And Restarts

Common cut patterns:

- "I-I-I" or repeated first word: cut the messy start if the sentence restarts cleanly.
- "wait no" / "actually" / "let me..." followed by a better explanation: keep the better explanation.
- Same sentence repeated after a pause: keep the cleaner delivery.
- Partial sentence followed by a full sentence: remove the partial sentence.
- If cutting inside a sentence would sound unnatural, keep a slightly imperfect line instead.
- If the transcript shows the right words but playback still sounds hesitant, split around the overlong gap or held short word and rerender. Correct words are not enough; the delivery has to feel clean.

## Flow Check

Before rendering, read the keep list in order and ask:

- Does the video still tell one continuous story?
- Does the viewer know what tweak is being tested?
- Does each kept section either explain, demonstrate, prove, or conclude something?
- Are there accidental timeline jumps?
- Are repeated explanations removed?
- Does the intro connect cleanly into the test?
- If the intro has stutters or duplicate hook takes and the user has not locked it, clean the intro too.
- Does the ending show a result or clear stopping point?

## Intro Slate

When the user wants a branded/static intro:

- Detect the intro from the rendered transcript, not from a hard-coded timestamp. The intro usually includes the hook, proof/setup, and call to follow along; the body usually begins at language like "Alright guys..." or "before you run/apply..."
- Extract the final edited intro audio, then render that audio over the Luna intro background image.
- Cut from the intro slate into the real screen recording when the tutorial body begins and the viewer needs to see the app.
- Keep the intro slate 16:9 and full-frame. Scale/crop the image to match the video resolution.
- Do not leave the intro image on top of clicks, benchmark progress, results pages, or any section where the viewer needs visual proof.

## Reference YouTube Sections

If the recording includes watching a reference YouTube video:

- Cut the reference video unless it is absolutely necessary to understand the creator's tweak.
- Keep only a very short mention of what is being copied or compared.
- Do not leave long stretches of another creator's video playing.
- If the user narrates over the reference, keep the user's useful explanation and cut silent watching.

## Output Targets

- A 15-30 minute raw recording usually becomes 3-8 minutes.
- If the first 30-60 seconds are already edited, preserve that range exactly unless the user says otherwise.
- The finished edit should feel intentional, not just compressed.
