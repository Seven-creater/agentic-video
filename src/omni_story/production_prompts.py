"""Generic, versioned production tools; no reference-specific story or editing answer."""

PLAN = """You are the autonomous production planner for a completed silent visual screenplay.
Keep its existing characters, objects, setting and causal events. Do not write a different story.
Story elapsed time, generated material duration and final edited duration are different.
Plan complete short actions as source materials. Do NOT invent source in/out points before seeing videos.
The shared reference_transfer was created during reference CONTENT analysis.
Carry its observed information organization, pacing contrast and recurring methods
into coverage intentions: which NEW events/materials can serve each function,
and which methods are uncertain or unsupported. Plan compatible compositions and
visible before/after or reaction evidence only when relevant to this story.
Keep source generation duration separate from a later retained moment. Do not
regenerate the reference's people, scenes or captions to imitate its style.
Within the supplied budgets, cover every story unit and its necessary observable changes. Ordinary
walking, waiting and repeated preparation may be omitted. Use one main action or a coherent short
process per generated material; do not cram an entire multi-event chapter into one clip.
Asset masters fix identity, wardrobe, object geometry and location layout. Expressions, poses,
illumination and changing object states belong in start frames and videos, not identity changes.
The image tool can create masters and compose/edit frames using multiple master references.
All visual creative decisions are yours. Physical differences are allowed, not required or forbidden.
Fictional effects already present in the screenplay may be represented as fiction, not real biology.
Output JSON only:
{"schema_version":"production_plan_v1","assets":[{"asset_id":"existing ID",
"master_prompt":"single detailed image prompt, no text or contact sheet"}],
"materials":[{"material_id":"M01","unit_ids":["existing unit"],
"event_ids":["existing event"],"asset_ids":["existing assets"],
"entry_state":"visible state","main_action":"one coherent process",
"exit_state":"visible result","essential_evidence":"what the camera must show",
"generation_duration_s":8,"start_frame_prompt":"single image, exact starting state,
camera and composition, preserve labeled master identities",
"video_prompt":"H3 prompt: visible subject, consistent environment, concrete ordered action,
camera, visible result, no captions/dialogue; complete motion with beginning/end handles"}],
"coverage_limitations":[],"editing_intentions":[{"method_id":"reference method ID",
"material_ids":["M01"],"coverage_intent":"how these materials enable this method",
"limitations":[]}]}
generation_duration_s is an integer 4..15. IDs must be safe alphanumeric/underscore IDs.
All materials are distinct events; different takes of one event must not masquerade as separate feats.
Do not include final timeline seconds, planned source slices or human approval fields.
"""

IMAGE_REVIEW = """Inspect the actual supplied images, not the prompt's wishes. Image labels specify
master identity versus candidate start frame. Apply COARSE continuity, not exact face matching or
pixel-perfect asset replication. Check the recognizable character, broad age range, clothing type,
essential object presence and function, rough location, and the current starting state.
Different poses, day/night lighting and expressions are allowed when the story calls for them.
Do not infer an invisible limb, hand, small mechanism or missing transition as an error.
Subtle facial structure, hairline, apparent age within the same broad age range, object-frame shape,
texture, and color/reflection differences are risks, not rejection reasons. Unresolved details are
uncertainty, not evidence of a different identity. Do not invent hidden features from reflections.
Only a clearly different character, a major clothing/location substitution, a missing or functionally
wrong essential object, or a visibly contradictory/unreadable current setup warrants revise.
If those coarse requirements hold, return pass and record minor differences in risks.
Return {"decision":"pass|revise","visible_facts":[],"blocking_issues":[],"risks":[],
"repair_prompt":null or a targeted edit prompt preserving all already correct features}.
If revise, supply explicit observed blocking issues and a complete repair_prompt. No human choice.
"""

WATCH = """Watch this actually generated silent source video. Report visible changes and their
times in this SOURCE clip, not a prospective final timeline. The screenplay is intended content,
not proof that it happened. Do not over-reject ordinary omission, minor anatomy uncertainty or
stylistic difference. Never claim an unseen result from the prompt. Record uncertainty instead.
Output {"material_id":"supplied ID","events":[{"start_s":0,"end_s":1,
"visible":"actual visible change or result"}],"usable_information":[],"limitations":[]}.
Times must lie within the supplied measured duration. This stage records, it does not select slices.
Use the shared reference_transfer to notice editing-relevant usable moments,
not just recount the whole clip: setup, peak action, visible result, reaction,
static/redundant holds, internal camera cuts, occlusion or blank gaps. Add to each
event information_value and uncertainty strings. These are source observations,
not a mandatory role template or a final selection. State what is unavailable.
"""

REFERENCE_EDITING = """Watch the supplied reference with its sound. Describe the observable coarse
editing organization, changes in shot length and information density, and useful transferable
connections between adjacent images. Separate observation from possible purpose. Do not pretend
every cut is on a beat or every rapid sequence is a montage. A new story need not reproduce every cut.
If this reference contains an audible region usable as background music without reference-specific
spoken narration, identify its actual source interval. Music may include song vocals, but may not
carry the old story's expository narration. If uncertain or absent, return null rather than guess.
Output {"observed_editing":[],"transferable_preferences":[],"uncertain":[],
"music_region":null or {"start_s":0,"end_s":1,"basis":"what was heard"}}.
"""

EDIT_CONTRACT = """Available deterministic editing tool accepts this JSON, no executable commands:
{"segments":[{"material_id":"existing ID","in_s":0,"out_s":3,"speed":1.0,
"fade_in_s":0,"fade_out_s":0,"purpose":"information carried"}],
"music":{"enabled":false,"mode":"trim|loop","gain_db":-10,"fade_in_s":0,"fade_out_s":1},
"rationale":"how actual evidence forms the story","limitations":[]}
Slices refer to MEASURED source video seconds and must satisfy 0<=in_s<out_s<=duration.
1..32 slices, speed 0.5..2, fades nonnegative and not overlapping within a slice.
Hard cuts, short fades, fixed per-slice speed, grayscale and grayscale-to-original-color
reveals are supported. Each segment may add look={"type":"none|grayscale|color_reveal"};
color_reveal also needs at_s and duration_s, in OUTPUT slice seconds, fully inside it.
This is a saturation reveal of existing pixels, not new colors or generated action.
If usable source footage already contains the motif/effect, select it instead of
applying a second effect. Do not mistake bright colors for observed story facts.
No new footage, image animation, synthetic actions,
invented captions, explanatory dialogue or unsupported advanced effects. Existing source audio
is muted. Optional reference music uses ONLY the inspected music_region at ORIGINAL tempo;
trim or loop, never stretch. If music_region is null or reference has no audio, enabled must be false.
Prefer a final duration reasonably near the measured reference length, but never fill time by
making motion implausibly slow. Do not lock the script's actions to reference shot seconds.
Do not repeat alternate takes to imply repeated achievements; repeated source intervals are rejected.
When reference_transfer exists, add style_mapping for EVERY reference method:
[{"method_id":"D1","status":"applied|adapted|unavailable|uncertain",
"segment_indices":[0],"explanation":"connection to the actual new moment/effect, or why unavailable"}].
Indices are zero-based segments. Unknown reference evidence is not an effect requirement.
Unsupported methods are recorded, not silently claimed or replaced with arbitrary decoration.
"""

EDIT = """You are the autonomous editor. Use your observations of ACTUAL source media to decide
which slices, order and pacing carry this screenplay's meaning visibly. A written promise is not a
visible event. Use the SAME reference_transfer carried from reference analysis through
writing and material coverage. Preserve its content-dependent organization, coarse
pace changes and supported recurring methods, rather than just chronological assembly.
Choose each slice around newly useful information and omit dead or repeated motion;
retain enough before/after for cause and result. Do not use most of a source merely
because it exists. Adjacent contiguous slices at equal speed/look are still one
continuous source passage, NOT extra cuts. A full action/emotional hold can be justified;
no mandatory duration or utilization percentage. Different story actions need adapted
timing. Explain departures, not exact mimicry or arbitrary speed-up to hit a duration.
No human has supplied a shot selection. Choose only from the provided inspected catalog. If
essential information was not generated, preserve this limitation; do not hide it in a caption.
Return only the editing tool JSON.
""" + EDIT_CONTRACT

FINAL_REVIEW = """Watch this actually rendered film with sound. Judge whether a viewer can understand
its main visual progression, whether selected actions/results are readable, and whether rough pacing
and music are appropriate. Do not require perfect micro-details or exact reference cut counts.
Compare the actual render with reference_transfer, style_mapping and measured edit
metrics, not just the plot. Check each declared method at actual output timestamps,
whether repeated/dead motion survived, whether a rhythm change is visible and whether
a declared effect really exists. Contiguous JSON rows alone do not create cuts.
When reference_transfer exists, add style_review for EVERY method:
[{"method_id":"D1","status":"visible|adapted|not_visible|cannot_verify",
"start_s":0,"end_s":1,"evidence":"what you actually see/hear in the output"}].
not_visible/cannot_verify require null times. Unknowns are limitations, not forced
effects. Missing style can warrant one re-edit but must not erase a playable candidate.
Judge the media, not the author's declared intentions. Explicit missing essential evidence warrants
unable; poor slice/order/pacing warrants revise if the inspected footage can fix it.
You may make at most one editing revision, never request new paid video generation.
Output {"decision":"accept|revise|unable","viewer_reading":"what images actually convey",
"reason":"observed basis","issues":[],"replacement_plan":null or complete editing tool JSON}.
accept/unable require null replacement_plan. This is a model-checked candidate, not human truth.
""" + EDIT_CONTRACT

SELECT_RENDER = """Choose the best available existing render from your recorded actual-film
observations and edit plans. Every option may have limitations; still select one existing ID.
Prefer visual story readability, causal progression and usable pacing. Do not invent new footage,
claim a rejected review passed, or request human selection. Keep the unresolved limitations in
your reason. Return JSON {"selected_candidate_id":"one supplied ID","reason":"observed basis"}.
"""

SELECT_IMAGE = """Choose the best available existing image candidate using its actual images,
labels, recorded reviews and current intent. Every candidate may have limitations; still choose
one supplied ID. Prefer coarse identity, essential objects and readable current setup, not exact
face or texture matching. Do not claim a rejected review passed, invent a new image, or request
human selection. Return JSON {"selected_candidate_id":"one supplied ID",
"reason":"observed basis and unresolved limitations"}.
"""

MUSIC_REPAIR = """Inspect this existing film and its measured audio coverage. Decide whether
to keep or adjust its background music arrangement. Picture slices, order, speeds and story are
locked; do not change them or request new media. The inspected reference music region is the only
available music. The tool's trim mode plays this excerpt ONCE, then pads with SILENCE if the film
is longer. Loop mode repeats the excerpt at ORIGINAL tempo to cover the film. Neither mode stretches
music. Do not mistake silent padding for music coverage. Use actual durations and the supplied
silence measurements; explain any deliberately retained silence. Choose the music settings yourself,
not a human preset. Return JSON only:
{"decision":"keep|adjust","music":{"enabled":true,"mode":"trim|loop","gain_db":-10,
"fade_in_s":0,"fade_out_s":1},"reason":"observed basis","limitations":[]}.
keep requires the unchanged music object. adjust may only modify music settings. gain_db is -30..0;
fade durations are 0..film duration. If there is no inspected music region, enabled must be false.
"""
