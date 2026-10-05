# One refinement output object

This describes serialization only. It gives no reference story, film slices or
creative solution. Input names are not output names.

The input original_draft is the prior model draft. observations contains all
unchanged source ranges, roles, reference methods and exhausted-input rules.
Return exactly one final JSON object with these root fields:

- plan: object. The COMPLETE final EDL, using the draft plan protocol, including
  sources, SHA, character bindings, slots, segments, captions, audio, dimensions,
  editing bindings, candidate dispositions and limitations. It may change the
  cuts, order and transforms based on evidence. Do not call this field draft.
- decisions: array, one per FINAL segment. segment_id, new_information,
  in_out_reason, speed_reason, hold_reason, origin, reference_method_ids.
- obligation_coverage: array, one per ORIGINAL slot. slot_id, segment_ids,
  status, reason. Retained slot IDs keep their original intended_takeaway exactly;
  new slot IDs may restructure the route, but account for original obligations.
- draft_dispositions: array, one per ORIGINAL segment, including removed ones.
  segment_id, decision, replacement_segment_ids, reason. Do not confuse original
  IDs with final IDs. A changed source range is replaced, never retained.
- duration: object. target_s and total_s are numbers. Compute total_s from the
  FINAL segments: sum((source_out_s-source_in_s)/speed + freeze_tail_s).
  over_target_reason is text, explaining necessary information if over target.
- timing_checks: array, one per FINAL segment. segment_id, purpose,
  essential_source_intervals, min_readable_s, reason. Each essential interval has
  source_start_s, source_end_s, visible_information, claim_ids, min_readable_s.
  Each minimum is a positive MODEL ESTIMATE, not a human measurement. Each
  interval must fit the selected source range and receive enough actual output
  exposure. Only an interval ending at source_out_s may gain tail-hold exposure.
  The union has its own segment minimum; overlapping intervals count once.
  Cover all of that segment's visual_claims. Earlier input fields are not proof
  of these claims; independent source checks follow.
- transition_checks: array, one per adjacent FINAL pair. from_segment_id,
  to_segment_id, relation, status, reason. These are proposals, not a pass.

Select a SINGLE literal value in each enumeration:

- origin: general_optimization OR reference_transfer.
- obligation status: preserved OR unresolved.
- draft disposition decision: retained OR replaced OR removed.
- purpose: setup OR action OR result OR reaction OR identity OR context.
- transition status: planned OR unresolved.

For example purpose="action" is valid; purpose="action/result" is invalid.
These examples show field syntax, not a prescribed creative choice. General
optimization has empty reference_method_ids; reference transfer cites actually
recorded method IDs. Never invent a known reference technique.

Do not return input envelopes such as policy, original_draft, observations,
response_contract or evidence_limit in place of the output. In particular,
decisions, obligation_coverage, draft_dispositions, duration and timing checks
belong beside plan, never inside plan. Populate the output skeleton fully; empty
objects/arrays are only placeholders for its shape.
