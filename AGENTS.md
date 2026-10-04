# Active implementation track: reference-driven movie-library editing

As of 2026-10-04, read `docs/REFERENCE_LIBRARY_SPEC.md` before planning new work.
The latest user-confirmed task is reference-first: a fixed reference defines the
theme and editing intent; a much larger movie library supplies actual footage.
Library evidence may reshape slots and character routes, but must not silently
replace the reference or weaken its intended takeaway. Keep the MiniMax generation
and Douyin discovery routes frozen. Initial research notes are historical; the
user subsequently authorized implementing and running the library route.
Preserve observed facts, external film knowledge, task-specific interpretations,
and unverified hypotheses separately. No fabricated footage, source ranges, or
successful evaluations. Record new policy versions without rewriting old runs.

## 2026-10-04 local implementation and run

- Read `docs/REFERENCE_LIBRARY_LOCAL_RUN.md` for the active CLI and resume rules.
  `omni-library` is independent of the frozen generation/discovery entry points.
- Fixed reference: `data/ref/video.mp4`, approximately 21.93 seconds. The actual
  local library now contains three Kung Fu Panda films under `data/videos/`,
  approximately 277.47 minutes total; do not assume the earlier tentative one/two
  domestic-film library is the current input.
- Fixed task directory: `runs/library_reference_20261004/`. Preserve its input,
  policy and budget lock, original responses, reconciliation records and caches.
  Do not reset the budget by deleting state or registering a sibling directory.
- The active model is GLM-5.3-Flash through the official vision MCP connected by
  this Codex session. The local Python pipeline exchanges jobs with that session's
  MCP queue; it is not a standalone Coding Plan API service. Do not silently turn
  this into a standard model API, a relay server or a headless deployment.
- Credentials are supplied through the process environment or `mcp_launch`'s
  hidden prompt, never source, documentation or command-line key arguments.
- CPU `faster-whisper` small/int8 produces unverified speech evidence. It does not
  establish music rhythm, speaker identity or visible action. This run uses
  sampled contact sheets rather than FlashVID; do not claim token compression.
- GLM owns reference interpretation, candidate retrieval, character/slot choices,
  source in/out points and the EDL on this route. Codex executes and validates;
  it does not supply a human plot or choose real movie slices.
- Source times and stream selections bind to the actual file SHA. Model-selected
  edit intervals must be contained in completed fine-observation windows.
  FFmpeg global audio stream index 2 selects the library's recorded Mandarin
  track; the reference's recorded global audio stream index is 0.
- Status when this implementation note was written: actual reference reading
  completed; first coarse-image call returned HTTP 200 with 8,192 output tokens
  and no usable content. Preserve this known reply and usage; one bounded format
  repair is in progress. No library final video has been completed yet.

These implementation notes append the authorized new route. They do not
retroactively change old MiniMax/Omni runs or turn model review into human truth.

## 2026-10-04 appended continuation and adaptive strategy

This update supersedes the early progress snapshot above, while preserving its
historical evidence. The first page's bounded repair `glm_003` actually succeeded.
`glm_004` failed after about 306 seconds with no captured model reply and remains
`uncertain`; never convert it to success or refund its request-budget count.

- Do not replay the uncertain original request, its request digest, its media
  SHA, or the same observation lineage (`kind`, `source_sha256`,
  `source_start_s`, `source_end_s`) under different encoding, filenames or prompts.
- The user's no-replay rule does not prohibit all other work. This run explicitly
  records `independent_media_no_unknown_replay_v1` as an appended continuation
  policy, allowing only new, independently bound inputs within the original
  budget. Pending `submitted` calls still require waiting; the base state class
  retains its conservative default when this policy has not been enabled.
- `adaptive_coarse_v2` replaces exhaustive traversal of the original 30-page grid:
  18 whole-film navigation frames per source, then at most four model-selected
  regions of up to 600 seconds across the entire library. Each round may select
  up to eight continuous fine windows of up to 90 seconds; the original hard cap
  of 16 fine windows, two renders and 80 model requests is unchanged.
- The external official-MCP package directory also needs `undici@7.16.0`.
  The local HTTP dispatcher uses 600-second header/body timeouts; official MCP
  configuration supplies a 16,384 output-token limit and 600-second request
  timeout. The guard still blocks internal retries and passes the official model
  request body through unchanged.
- A default five-minute header timeout is a candidate explanation for `glm_004`,
  not a confirmed cause: the original error cause was not captured. Preserve that
  uncertainty. As of this update, overview calls 005 and 006 have replies, 007 is
  still submitted, and no library final video exists.

## 2026-10-04 appended editing implementation

Read `docs/EDITING_IMPLEMENTATION_20261004.md` for the new forward protocol.
`--editing-v2` binds every original reference method to model-selected EDL
operations and requires actual-output method checks. It cannot reinterpret an
already planned run or change locked budgets. Legacy requests and render caches
remain recoverable. `--audit-editing` is a CPU-only append-only observation of
existing reference/renders, never a quality pass or an additional render.
The renderer supports model-owned static captions and real tail-frame holds;
these do not fabricate source events. Scene-change candidates are not semantic
shots; EDL ranges are not shot counts. Audio rhythm remains unverified.
The completed fixed run has 43/80 requests, 16/16 fine windows and 2/2 renders;
selected review remains theme partial / editing partial / continuity pass.
No new real GLM video or successful editing-transfer evaluation resulted from
this engineering update. Do not reset its budget to test the new protocol.

# Frozen autonomous reference-to-generated-video route

The generation-specific provider and submission rules below describe the existing
route; they do not require generating new assets for the movie-library research.

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
