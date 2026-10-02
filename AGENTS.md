# Autonomous reference-to-final-video route

The user's only creative input is a reference video. The deliverable is an edited,
playable video, not an intermediate approval request. Credentials, storage paths,
and tool budgets are infrastructure inputs.

- Omni owns reference interpretation, story/asset decisions, candidate comparison,
  actual-footage observation, source in/out points, ordering, pacing, and final edits.
  Do not supply a human plot, hand-pick assets or slices, or rewrite its story.
- Use the user's restored Aliyun image/edit APIs and MiniMax H3 API. Do not switch
  providers silently. Asset continuity is coarse, not pixel-perfect likeness.
- Content rejection is feedback, not an automatic pipeline stop. Within the existing
  bounded budget, Omni may improve candidates and choose the best available valid
  candidate to continue. Record unresolved criticisms and rejected candidates.
  Do not convert a rejection to a pass or claim that limitations disappeared.
- Only choose actual, structurally valid artifacts and actual generated media.
  Missing outputs, invalid source ranges, authentication/balance failures, and lost
  submission replies remain technical problems; never fabricate a candidate or
  replay an uncertain paid POST to satisfy a 'never stop' instruction.
- Each video material is submitted once. Existing task IDs are queried/downloaded,
  never submitted again. Do not open new runs or erase records to reset budgets.
- Continue through footage observation and editing automatically when usable media
  exists. Review the actual rendered film. Deliver the best available final render
  with limitations clearly identified when quality remains imperfect.
- Preserve old prompts, raw responses, reviews, input/output SHAs, and failures.
  Policy changes must be recorded separately, not retroactively applied to old runs.
