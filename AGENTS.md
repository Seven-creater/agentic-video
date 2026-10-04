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

## 2026-10-04 explicit authorization for one additional revision

The user answered “需要” to the explicit proposal to add one render in the
original task directory. This authorizes exactly render_2 (effective limit 3),
not a fresh run or another render beyond it. Record this permission as a separate
hash-bound editing_revision_authorization artifact; keep the original input lock,
the 80-request total, the 16-window limit, uncertain 004 and all old results.
Use the independent --revise-editing entry point. GLM may select up to six of
the existing watched windows to reread editing conditions, with new overlays
separate from historical events/ranges. Conflicting observations block the
affected window in this revision. Preserve result.json; the revision uses
result_revision_2.json and selection_revision_2.json. Review the actual new
render and let GLM compare it with the previous valid candidates. No additional
creative approval is required within this explicitly authorized scope.

## 2026-10-04 appended revision outcome

The authorized revision actually completed: 58/80 total requests, 16/16 unique
fine windows, and exactly three renders under the effective limit of three.
GLM reread six existing windows, produced a 34-second 720x1280/30fps candidate
from five source ranges with Chinese captions (no freeze), and selected render_2.
Read result_revision_2.json and docs/REFERENCE_LIBRARY_REVISION_20261004.md;
result.json still records the historical first-run choice. No fourth render is
authorized. Official MCP was stopped after all new requests settled. Cache-only
resume added zero calls and kept the video SHA, all 58 call records, the original
43 records and 216 protected files unchanged. Unknown glm_004 remains uncertain.

The new model review says theme/editing/continuity pass, but this is not an
independent quality pass: blind reading and target review disagree about visible
action and a supporting character's identity. The EDL uses the original reference
soundtrack with speech, loop=false; decoded output is silent from 22 to 34 seconds.
Audio rhythm and audiovisual meaning remain unverified. Keep these limitations
and model replies; do not silently fix them with human-selected footage or audio.
Execution validation and semantic evidence audit are separate appended artifacts.

## 2026-10-04 appended visual narrative clarification

The user explains that the reference has BGM without spoken dialogue and that
precise content should be understandable from visible footage. Follow the
visual_narrative_primary_v1 clarification in REFERENCE_LIBRARY_SPEC.md:
review the actual film silently, hide the intended plot/theme answer, describe
observable difficulty, action, change/result and montage relationships with
output-time evidence, then compare the apparent meaning with the reference.
Missing auditory input alone is not a failure of visual narrative comprehension.
Music rhythm and audiovisual craft are separate evaluation dimensions. Captions
may assist; distinguish text evidence from visible action and never claim that
a caption-visible blind reading establishes caption-free comprehension.
This is a newly documented criterion, not yet wired into runtime prompts or
retroactively applied to old verdicts. Do not modify old ASR, audio measurements
or model responses, or infer any additional render authorization from it.

## 2026-10-04 appended end-to-end audit

Read docs/REFERENCE_LIBRARY_E2E_AUDIT_20261004.md before proposing fixes.
The silent visual audit finds partial narrative/method transfer, despite the
preserved model pass. Main gaps: broad event descriptions applied to short EDL
ranges, omitted action outcomes, captions not mapped to their actual evidence
time, target review contradicting blind facts, and final selection inheriting
textual review errors. Actual range provenance agrees with GLM's plan; no evidence
of a different FFmpeg cut was found. The training window's later flips and held
dumpling were independently checked in actual proxy frames, not assumed from its
model description; no replacement EDL was supplied. The earlier reference-speech
claim was based on unverified ASR and is withdrawn as a confirmed fact.
These are audited gaps, not implemented fixes. Preserve old verdicts and inputs;
requests remain 58/80 and the effective render limit remains three. Audit frames
and an appended evidence artifact do not authorize further paid calls or renders.

## 2026-10-04 appended editing-knowledge observation

- Read `docs/REFERENCE_CRAFT_IMPLEMENTATION_20261004.md` for `--reference-craft`.
  Project-owned generic cards are actually included in GLM prompts; this is not
  a globally installed Codex skill or proof of specialist model ability.
- Actual requests 077–079 completed two reference-only stages, including one
  bounded repair for an unsupported operation name. Current usage is 79/80.
  Original 004 remains uncertain. No additional movie windows or render exist.
- GLM selected 14–18 and 18–21.933333 seconds itself; actual second media is one
  normal-speed continuous envelope at local 30 fps. Cloud sampling is unknown.
  Do not describe this as full-frame viewing or as verification of match montage
  and speed techniques throughout the reference. Content remains model evidence.
- Knowledge snapshots, allocation, original calls and completed result are hash
  bound. 76 prior calls and 420 historical files were checked unchanged. Cache
  resume validates new raw responses, parsed content, media and lineage as well.
- Original `result_semantic_revision_3.json` retains its original 76-call usage
  and failed final-review status. The old continuation entry returns that record
  after new-stage protection checks, without relabeling it with later usage.
- MCP stopped after verification; both entrypoints resumed with zero requests.
  Only one request remains, insufficient for another complete edit/review loop.
  Do not reset budgets, re-submit unknown work or imply a fifth render is authorized.

# Frozen autonomous reference-to-generated-video route

## 2026-10-04 authorized full-reference semantic continuation

The user explicitly authorized one full end-to-end run and clarified that GLM
must decide what matters throughout the entire reference. The earlier 5–17 second
example is not a prescribed interval. A new hash-bound
`semantic_continuation_authorization` appends render_3 (effective limit four),
preserving the original 80-request cap, all 58 baseline calls and historical
files. It does not reset the 16 unique library fine-window limit.
`--continue-semantic` runs/resumes this one continuation in the original folder.
Full-reference analysis receives no old reference explanation, ASR, Codex cut
list or editing answers. The model generates a new interpretation and methods;
all exact source-slice observations finish before a separate batched comparison.
Each slice retains its independent facts, source SHA/range and observation hash.
At most six slices and 22 additional requests, including format repairs, were
reserved before the first new call. The new result is stored separately from
old outputs. Do not reinterpret prior pass/partial scores as the new result.
Coverage/observation strategy remain model statements, not proof of exhaustive
frame inspection or parameter learning. The model connection was re-established
in this Codex session; no standard API provider or standalone server was added.
Calls 059 and 060 returned known replies but failed the full reference-output
contract (missing field, then a noncontiguous reported coverage table). Preserve
both failures. `partial_reference_navigation_reconciliation_v1` binds the unchanged
valid reference/method subobjects from 060 to its request/reply/media hashes, with
no additional reference request and no artificial coverage repair. These are
model estimates used for navigation, not complete reference understanding or a
retroactive protocol pass. Its gaps are reported-coverage gaps, not proof of
unwatched intervals. Source facts, comparison and output gates remain strict.

Calls 061 and 062 also returned known replies but failed strict plan evidence
checks. Preserve both. This was not a source-window overrun: one selected range's
asserted roles did not match its recorded usable-role set, and a caption cited an
event outside the selected range. Do not fix those creative choices by hand or
relax the checks. `one_evidence_feedback_replan_v1` records one separate model
replanning stage, fed only the model's own failed proposal and deterministic
contract diagnostics. At the recorded 62-request baseline, its remaining 18
requests reserve at most five exact slices plus replan, batch comparison, blind
reading and output review, each with at most one format repair. The allocation
is immutable on resume. No additional semantic replanning loop is authorized by
this implementation; render_3 is still the sole new render.

The fifth slice's original 069 reply had invalid zero-length evidence intervals;
its one repair 070 fixed those intervals but omitted `uncertainties`. Preserve
both failures and do not issue a third observation request. The continuation-only
`omitted_uncertainties_report_reconciliation_v1` may bind that known repair and
append an explicit program note that uncertainty was not reported and remains
unknown. It never substitutes an empty list, changes model facts, source times,
identities or evidence IDs, or imports root-level untyped inference into evidence.
Every other typed observation constraint still validates. Old raw replies and
their lack of a successful parsed record remain unchanged. The normalized record
and its protocol limit are separate artifacts and force a limited result status.

The batch original 071 has two unescaped quotes and a missing final root-object
delimiter; its only repair 072 exhausted output tokens with no content. The
continuation-only punctuation reconciliation inserts those syntax characters
and copies the same claim's existing model reason into empty limitations. It
does not change verdicts, IDs or evidence. Preserve both failed raw calls and
do not create old parsed files. The normalized report has nine unsupported and
one partial item; those are model checks, not nine independently proven errors.

Final state: render_3 is 34 seconds, SHA
`d49da8831b975fc655ad96eb7453311fa7c3e4d7ae68b54207d89ed643c77190`.
Requests are 76/80, with no new unique fine windows or generated assets.
The blind reading reports essential text dependence. Review 075 returned empty
content and repair 076 failed claim-ID/evidence binding; preserve its raw partial
ratings without converting them into a valid review. The final result has
`review=null`, `review_status=incomplete_protocol_failure`, and a false semantic
gate. `deliver_actual_candidate_with_incomplete_review_v1` delivers the real
candidate with that limitation only after both known replies, never while
pending/uncertain or by fabricating a verdict. Official MCP is stopped; cached
resume adds no requests and preserves all baseline history and the new video SHA.
Read `docs/REFERENCE_LIBRARY_SEMANTIC_RUN_20261004.md` for current evidence.
This attempt did not establish autonomous time-compression/slow-motion transfer.
Independent source observation can itself be wrong: the high-resolution source
frame at 314 seconds shows six bowls, unlike the model's three/four descriptions.
No manual audit finding was supplied as a creative answer to GLM.

## 2026-10-04 forward semantic audit and time-compression research

Read `docs/REFERENCE_TIME_COMPRESSION_20261004.md` for the actual reference evidence,
new `--semantic-audit` mode and implemented/proposed boundary. This mode includes
editing-v2 and applies only before a task has paid plans. Exact source slices are
read without a plan first; intended claims are compared in a separate hash-bound
call. Typed silent output facts, source claim checks and unresolved contradictions
gate success and evidence-based selection. All calls and one allowed repair count
against the locked budget. Persisted search/plan allocations must survive resume.
One slot may contain several disjoint slices; each must remain within completed
fine evidence. GLM still owns slices, slots and retiming decisions. No hand-picked
replacement movie EDL was supplied. The reference's original match duration and
exact slow-motion factor are unknown. A detector candidate is not a confirmed cut.
This implementation was tested with synthetic media/queue replies, not a new GLM
quality evaluation. It did not reinterpret old reviews or authorize a fourth
render, new real requests, or a sibling directory to reset the current task budget.


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

## 2026-10-04 appended forward active-finecut engineering

Read `docs/ACTIVE_FINE_CUT_IMPLEMENTATION_20261004.md` for the current implementation/proposal boundary.
`--active-finecut` includes semantic-audit/editing-v2 only before a task has paid plans or any render.
GLM owns the draft, final microcuts, retiming and optional holds; Codex supplies generic decision
knowledge and deterministic validation, never the reference plot, prescribed seconds or real movie slices.
All final slices receive independent facts before claim comparison. Original draft slot obligations
are retained verbatim as stable claims in the actual-output review even when final slots are restructured.
Silent blind reading precedes independent economy review; neither receives draft/finecut intent.
The latter may see only actual output timing and the earlier independent silent reading.
The quality gate combines semantic evidence, original obligation coverage and economy review.
Limited candidate selection requires limitations and cannot discard an available joint pass.
Mappings and model verdicts do not prove semantic truth or unfamiliar-viewer comprehension.

The generic handbook snapshot is hash-bound on first activation; old knowledge snapshots remain unchanged.
Plans reserve `12 + 4N` requests, including one format repair per stage and exact final-slice checks.
Budget allocations persist on resume. Synthetic media/queue tests verify plumbing and rejection gates,
not GLM editing quality. This turn made zero real model requests and zero new movie renders.

The fixed real run remains 79/80 requests, 16/16 unique fine windows and four actual renders.
`--prepare-active-finecut` is CPU-only, idempotent and does not activate policies or authorize costs.
Its same-directory proposal reserves at most 44 new requests, eight final slices and one new render,
with no new unique movie fine windows. If approved, its cumulative call ceiling is 79+44=123,
not 80+44=124; preserve the original base cap, input lock and all historical calls.
The extension execution adapter is not active or implemented by this forward mode. It must preserve
old stage authorization/allocation validation, the uncertain 004 no-replay rule and cached old results.
Do not infer authorization for this extension from the existence of the CPU proposal or CLI flags.
