"""Frozen general-purpose prompts: no source-specific plots or human examples."""
from . import donor_prompts as donor

REFERENCE = donor.REFERENCE_PROMPT

VISUAL = """The new film must convey its essential meaning through visible
actions, expressions, object states and interactions alone. No explanatory
subtitles, dialogue or narration; BGM carries no plot facts. Bodily differences
are allowed, neither required nor disfavored. Invisible biography and author
explanations are not evidence a viewer can see. Prefer a small recurring cast,
stable locations and reusable props for image-conditioned video generation.
Real events can take longer than the eventual edit and span multiple takes.
Do not assign final seconds or pretend that generated footage already exists.
Keep major causal prerequisites, but ordinary transitions and waiting can be
omitted between shots. No prescribed domain, moral, reversal or ending.
"""

ROUTES = VISUAL + """Read the supplied model-authored reference interpretation
as a hypothesis, not a story to copy. Identify its central audience takeaway
and the actual sequence of information functions. These need not involve an
initial misjudgment or a reversal. Invent THREE distinct original premises
that could convey a similar takeaway and reasonably similar story progression.
People, activities, settings and specific cues are free choices. Do not merely
rename the source person or repeat the source activity. Choose the premise
whose central actions and results are easiest for a first-time viewer to see
and for several short generated takes to depict. Specific technical risks are
not automatically impossible; hidden expertise is not visible proof.
Return Chinese JSON only:
{"schema_version":"autonomous_routes_v1","reference_relation":{
"audience_takeaway":"...","information_sequence":["..."],
"evidence_mechanism":"...","ending_function":"...",
"uncertainties":[]},"routes":[{"route_id":"R1","title":"...",
"premise":"...","visible_action_and_result":"...","why_original":"...",
"production_risks":[]}],"selected_route_id":"R1","selection_reason":"..."}.
routes must contain exactly R1, R2 and R3. Input: """

# Reuse the untimed outline schema; replace its old prior-story precondition.
_old = """The prior story was model-authored and may contain errors. Keep its premise
and focal people, but repair its events and evidence scope yourself. Follow
the abstract audience relation, not source-video characters. Existing asset
IDs remain stable; add only essential assets. Write visible reproducible
appearance/clothing, location layout/lighting, prop structure and initial state.
"""
_new = """Develop the selected model-authored premise into a complete story.
Follow its abstract reference relation, not source-video characters. Invent
the required assets and stable IDs yourself. Write visible reproducible
appearance/clothing, location layout/lighting, prop structure and initial state.
"""
assert _old in donor.OUTLINE_PROMPT
OUTLINE = VISUAL + donor.OUTLINE_PROMPT.replace(_old, _new)
SEGMENT = VISUAL + donor.SEGMENT_PROMPT

REVIEW = """Independently review the supplied target and its dependency context
as pictures-only AI-film TEXT PLANNING. Judge the broad meaning, causal chain,
stable identities/props and state handoff. Real process duration is not final
edit duration. Do not impose a fixed story template, exact timing or an ending
that the reference does not require. When reference_relation is supplied, check
that the central takeaway is preserved, not that the topic or people match.
Distinguish an EXPLICIT contradiction or missing decisive evidence from a
possible production risk, an omitted routine transition, or optional polish.
A speculative difficulty is a risk, not proof of failure. Do not infer an
unwritten extra limb, reject disability, or assume an ordinary one-handed action
is impossible. Do not demand every gesture, specialist proof or transition.
For each issue cite a JSON pointer INSIDE target and the concrete consequence.
Do not rewrite the plot or prescribe a new domain. Blocking is reserved for
something that makes the core story contradictory or incomprehensible.
Return Chinese JSON only:
{"schema_version":"autonomous_review_v1","verdict":"pass|revise",
"issues":[{"path":"/field","severity":"blocking|risk|polish",
"kind":"contradiction|missing_evidence|meaning_mismatch|detail",
"reason":"..."}],"limitations":[]}.
verdict=revise exactly when at least one blocking issue exists; otherwise pass.
Input: """

REVISION = """Revise the supplied stage using independent feedback. The critic
can be mistaken: repair real contradictions and missing decisive information,
not speculative or harmless detail. Keep the same schema, unit IDs, asset IDs,
premise and central meaning. Do not edit locked preceding segments or silently
change their states. Return a complete replacement JSON, not the input bundle.
Input: """

BLIND = """Read only the supplied visible story as a silent film. You do not
know its author intent, reference or premise. No explanatory text, dialogue or
narration is available. Character descriptions give visible appearance, not
invisible biography. Explain what an ordinary first-time viewer could infer
from the proposed pictures, citing unit IDs. Assess whether the central event
and its result are understandable without inventing events. Ordinary omitted
transitions are allowed. Do not require a judgment reversal, victory, unanimous
agreement, a punchline or fixed story roles. Separate concrete contradictions
and decisive gaps from optional details and untested production risks. This
is a text-plan review, not a test of actual generated-video capability.
Return Chinese JSON only:
{"schema_version":"autonomous_blind_v1","viewer_takeaway":"...",
"sequence_reading":[{"unit_id":"U1","reading":"..."}],
"verdict":"usable|needs_revision","issues":[{"unit_ids":["U1"],
"severity":"blocking|risk|polish","reason":"..."}],"limitations":[]}.
Input: """

ALIGNMENT = """Compare the model-authored abstract reference relation with the
complete proposed screenplay and independent blind reading. Check central
audience takeaway and observable evidence mechanism. Story progression is a
soft similarity preference; domain, cast, bodily traits, unit count, exact
seconds and closing wording need not match. Mere surprise or surface resemblance
does not prove the same meaning. Do not use the author's intended_takeaway as
proof that viewers receive it; use events and the blind reading. Do not supply
a new plot. Return Chinese JSON only:
{"schema_version":"autonomous_alignment_v1",
"takeaway":{"status":"supported|uncertain|contradicted","reason":"..."},
"evidence_mechanism":{"status":"supported|uncertain|contradicted","reason":"..."},
"structure_similarity":"...","limitations":[]}.
Input: """

ALL = {name: value for name, value in (
    ("reference", REFERENCE), ("routes", ROUTES), ("outline", OUTLINE),
    ("segment", SEGMENT), ("review", REVIEW), ("revision", REVISION),
    ("blind", BLIND), ("alignment", ALIGNMENT))}
