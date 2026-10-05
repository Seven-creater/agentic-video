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

## 2026-10-04 latest user quota clarification

The user explicitly removed the program-selected 80-request ceiling and asked to stop on stalled,
unproductive work rather than loop indefinitely. This supersedes interpreting80 or the earlier44
CPU proposal as a mandatory new-request cap. Preserve those historical records unchanged.
New execution may use a separately recorded progress policy without a numerical total-request cap;
pending or unknown calls must never be replayed, stage repetition is not progress, and each known
format failure still receives at most one repair. Platform quota/authentication failures stop with evidence.

The user then requested research into MCP quota and whether MCP can be avoided, and confirmed Max.
Read `docs/GLM_MAX_QUOTA_AND_NATIVE_VIDEO_20261004.md` before changing the provider path.
Current public docs state shared model/MCP credits; the user's alleged monthly400 MCP limit remains
unverified at account level. Native GLM-5.3-Flash vision exists, but supported-agent subscription use
and independent Python standard-API billing are different. Do not silently convert this session's
official-MCP route into a standalone subscription HTTP service or claim removing MCP removes quota.
No new model calls or real extension authorization were activated during this quota research.

## 2026-10-05 active Goal and actual fine-cut execution

The user subsequently authorized real GLM editing without conserving quota, then explicitly asked
to use Goal to complete the task while they sleep. A Goal is active for an actual concise,
visually understandable reference-themed edit, with model-owned cuts and actual-footage review.
Do not treat a long ordinary-speed assembly or incomplete review as Goal completion.

Read `docs/ACTIVE_FINE_CUT_RUN_20261005.md` for actual progress. The same fixed task now has
`active_finecut_extension_v2` authorization, baseline79, no numeric request ceiling. Original80
is historical. Official MCP remains the provider; original004 is not replayed. Keep old artifacts.
The bridge restores the installed official131072 output default and records actual limits.
Strict append-only received-cache proofs bind old exact facts/claims to the current final model plan;
blind review checks all final slices, including fully cached ones. These are infrastructure fixes,
not creative overrides or evidence of good editing. Record new compatibility/iteration policies
separately; preserve failed raw replies and protocol-failure records rather than relabeling them.

## 2026-10-05 known round4 stop and historical-cache recovery

The actual ledger reached93 requests. New080–093 all have known replies; original004 remains uncertain.
Round4's final model plan is61 seconds with six1x segments and a3-second hold. Four exact facts/claims
are complete. The fifth exact slice reused original069 and its sole070 repair; neither passes the
complete current fact contract. This is a known pre-render failure, not a completed video or review.
Do not issue a third observation of that same input, invent render_4, or call the ordinary assembly concise.
The Goal may freeze this incomplete round and replan from actual fallible evidence in the same task.

Cache revalidation accidentally overwrote069's old protocol_failure metadata. Its exact original bytes
were restored only after matching the preexisting protected SHA; changed bytes and a recovery receipt
remain appended under artifacts/protocol_history_recovery_20261005. All other protected hashes agreed.
Cross-stage cache validation now preserves old parsed/failure files, records diagnostics separately,
and reuses the already recorded sole repair's exact request/reply. Missing historical parsed evidence
requires an explicit derived binding; never create a retroactive pass or a third paid format attempt.

## 2026-10-05 Goal round5 known failure and forward range navigation

The Goal is active. Actual094 and sole095 repair are received, both failing the same usable-range
contract; cumulative95, no render_5. Its57-second ordinary draft is not a completed fine edit.
The selected seg_3 is inside the watched window but spans separate usable blocks and incompatible
role sets. Another chosen exact slice is the old069/070 exhausted input. Do not replay any of them.
Round5 raw requests, replies and failure metadata remain unchanged; extra history protection is appended.

For new rounds>=6, goal_source_range_diagnostics_v1 mechanically lists ALL recorded usable ranges
in source-global seconds with unchanged role sets, event indices and observation hashes. It reports
all range/role and exhausted-input blockers, without providing replacement cuts or creative answers.
The same-directory navigation artifact is immutable and does not change old prompts or validators.
Actual short-source and output reviews are still necessary; a recorded usable range is not semantic truth.

## 2026-10-05 explicit user stop — current state

The user explicitly stopped the work and rejected further loops. Goal is PAUSED, pipeline and
official MCP stopped, mcp_stop exists. Cumulative96; original096 response is received and preserved,
with no subsequent refinement, facts, review or render. Goal rounds4–6 produced no new movie.
No Goal completion or quality pass is established. Preserve all history and original004 uncertainty.
Do not resume, launch another round, make model calls or render unless the user explicitly resumes.

## 2026-10-05 explicit research and Goal resume — latest state

The user explicitly resumed: research the literature, then use Goal to complete the edit.
Read docs/EDITING_READABILITY_RESEARCH_20261005.md. The research-backed continuation is active
in the same task; the earlier pause is historical. No numeric total-call cap is reinstated.
Round8's local model trimming reduced a45-second draft to32 seconds/ten1x slices, but source
checks expose action/result gaps. Calls125/126 both have known protocol failures; the sole repair
is exhausted, cumulative126, and round8 made no render. No existing movie is a new quality pass.
Forward evidence-first planning puts bound typed source observations before a new draft, retains
all16 watched windows, and omits redundant old creative bodies while preserving original records.
Old failed slots are not new-round obligations; new finecuts retain their own draft obligations.
Do not activate this strategy after its round has paid calls, normalize old replies, or third-replay
the125/126 observation lineage. Independent model facts can be wrong; human proxy-frame audits
remain separate from creative inputs. Original004 remains uncertain and all historical protections apply.

## 2026-10-05 explicit research-first Goal resume

The user subsequently instructed: “你先调研一下论文文献怎么解决这些问题，然后再以goal目标完成任务”.
This explicitly resumes actual work after primary-source research. Read
`docs/EDITING_READABILITY_RESEARCH_20261005.md`. Keep the historical stop and all old artifacts.

The forward `evidence_timing_refinement_v1` strategy may bind generic literature-derived knowledge
and already recorded independent exact-source model facts to the still-unsubmitted round6 finecut.
Original096 draft prompt/reply, navigation and old knowledge remain immutable. Read the received096,
use its sole unused repair with mechanical diagnostics of all original caption-time conflicts,
then continue only from a valid draft. Do not resubmit096 or supply replacement movie cuts.
Model-estimated essential exposure must be checked per interval as well as union per segment;
only core information continuing to the last source image can gain tail-hold exposure. These estimates
are not measured human readability. Check all adjacent transitions as proposals, not quality passes.

Independent source counterevidence for required visible action/outcome/identity stops before rendering.
A new separately registered Goal round may use that recorded counterevidence to reconstruct its own
route; never replay a paid refinement or recast an unsupported judgment as a format error.
Original004 uncertainty and069/070 exhausted observation still block replay. Keep16 unique windows,
same reference/library/run and official vision MCP. No numeric total-call ceiling is reintroduced.
Only actual source/evidence/edit changes count as progress; repeated unchanged blockers must stop.
Actual silent blind reading, economy/readability and target comparison remain required; no new quality
success is established merely by these engineering changes or synthetic tests.

## 2026-10-05 research round6 failure and flat-output forward strategy

Actual097 draft repair passed, with45 seconds of executable selected ranges/hold (model claimed47).
098 and its sole099 repair are known received and both lack root plan; no final source checks or
render6 occurred. Result_goal_feedback_6 remains stopped_protocol_failure; cumulative99.
Do not move draft into plan, fill dispositions or issue a third repair to relabel either old reply.

The separate flat_refinement_output_v2 snapshot applies only from unsubmitted round7 finecut.
It retains all reference/source/role/range/exhausted-input context, uses original_draft and observations
as input names, and one output shape with seven root refinement fields and single-value enums.
Read-only refinement diagnostics may list all mechanically checkable errors for the same new stage's
sole format repair. Alias scanning is diagnostic only; never an accepted normalized plan or pass.
All source, actual-output, silent-narrative and economy gates remain unchanged. Repeated unchanged
serialization failures after this substantive interface correction must stop, not launch endless rounds.

For the real round7, the forward format record can enable all_watched_proxy_cut_navigation_v1.
All16 old watched-proxy timelines already exist (742 visual-change candidates,12fps720x406,
threshold3); the earlier planning prompts lacked them. Read and hash old catalogs/caches/lineages;
do not overwrite catalogs, rank/select human cuts, or interpret candidates as semantic shots.
Offset-mapped source times are estimates with the recorded0.1s proxy tolerance, not native movie
frame precision. Preserve all windows/candidates in new hash-bound navigation; independent selected
source and actual-output facts remain mandatory. CPU binding adds no model calls or unique windows.

## 2026-10-05 round7 and one forward local-trim experiment

100 draft and101 refinement are received/parsed, but final plan still45s/five1x continuous ranges/2s hold.
102 and its sole103 claim-check repair are received protocol failures (limitations then uncertainties);
no round7 render, cumulative103. Do not fill missing arrays, move fields or issue a third equivalent check.

The one forward local_counterfactual_trim_v1 experiment applies only to round8, with a separately bound
goal_research_local_8. It reuses the exact received100 model draft, explicitly not a fabricated new draft call.
One actual parent clip per model-selected draft segment drives one local trim stage and at most one repair.
GLM chooses kept_slices, omitted process, speed and holds. Parent/source/proxy/stage bind one-to-one;
the final EDL may only use proposed executable operations, without duplication beyond proposed counts.
No manual story, source cuts or human-audit creative answers enter these prompts. If operations remain
unchanged, stop. This is a substantive local-decision experiment, not another retry of the failed7 interface.
Original source and output evidence gates remain strict; local proposals are not independent facts.
Read docs/EDITING_READABILITY_RESEARCH_20261005.md for the new implementation/proposal boundary.

The forward explicit_slice_claim_check_v1 retains legacy protocol/hash and all claims, with explicit
limitations per row and root uncertainties, plus batched mechanical diagnostics for its sole repair.
Source/fact/claim-content fingerprints ignore ID aliases when blocking exhausted comparison inputs;
102/103 must not be replayed under another stage or shape. Original official-MCP endpoint/HTTP no-retry
guard and history remain; additional trim stages require the new bound policy, never altered old grant.
Synthetic tests are not GLM quality success. The latest actual playable film remains historical render3.

## 2026-10-05 round9 known end and forward round10 design

Actual127/128 new draft is39 seconds. Actual129 proposes27 seconds, five1x ranges and1-second hold,
but129 has invalid claim-kind/exposure fields and130's sole repair rewrites its original slot obligation.
Both failed replies remain known and unparsed; cumulative130, no render9. Do not reinterpret either as pass.
The forward round10 design separates audience meaning from replaceable implementation before a new draft,
while preserving the existing immutable draft-obligation validator and actual silent/economy/target reviews.
Sampled source-frame image evidence may use the already supported official analyze_image with the same
GLM-5.3-Flash provider. Keep normal proxy, source times, frame/image/manifest hashes and sampled limits.
Underlying continuous source scopes still block unknown/exhausted replays, regardless of image carrier.
These changes remain unactivated until tests pass and a pre-paid hash-bound strategy is recorded.
Original004 uncertainty, all old replies, sixteen unique windows and the fixed reference/library/run remain.
