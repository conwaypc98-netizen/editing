# Reasoned Edit Plan Format

Use JSON with a top-level `keep` array. `render_keep_list.py` ignores the extra reasoning fields, while `validate_edit_plan.py` requires and verifies them.

```json
{
  "schema_version": 2,
  "story": "Hook, prepare adapter, apply settings, show result, CTA.",
  "keep": [
    {
      "start": 11.325,
      "end": 15.715,
      "label": "clean hook",
      "story_role": "hook",
      "rationale": "Best complete hook take; earlier attempts repeat the opening.",
      "viewer_purpose": "State the payoff immediately.",
      "take_choice": "Kept the third take because it is complete and fluent.",
      "continuity": "Opens the video and leads directly into the tutorial.",
      "intro_slate": true,
      "evidence": {
        "transcript_segment_ids": [3],
        "frame_times": [12.0]
      }
    }
  ],
  "duplicate_resolutions": [
    {
      "group_id": "duplicate-001",
      "kept_range": [11.325, 15.715],
      "reason": "Complete wording and cleaner delivery."
    }
  ]
}
```

Every kept range must have a story role, rationale, viewer purpose, take choice, continuity note, and evidence. Add `allow_repeat: true` only when repetition is deliberate and useful. Add `lock: true` only for a user-approved finished range.

Build the first complete timeline from the transcript and overview evidence. Do not inspect every sampled frame sequentially. Open detailed source frames only around duplicate-take choices, speech defects, visual transitions, proof, and uncertain boundaries, then revise the first timeline with what those targeted checks establish.

Set `intro_slate: true` only on consecutive opening ranges whose visuals may be replaced by the Luna background without hiding an action or proof. The first screen-dependent range must omit it or set it to `false`.
