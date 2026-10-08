# Evidence and time in a concise visual edit

This is generic task knowledge. It contains no reference plot, movie character,
prescribed source intervals, or replacement EDL. It supplements older immutable
knowledge; it does not rewrite historical observations.

1. Select a minimum sufficient sequence, not a minimum number of seconds.
   Distinguish context, action, state change, result and reaction. Remove waiting,
   repeated explanations and already inferable process. Several disjoint moments
   can express one event. A new situation may need brief context re-establishment.
2. A cut must preserve the action relation needed to understand the next image.
   The same person appearing twice does not prove a causal connection. Do not
   end before an essential outcome becomes visible, then write that it happened.
   A reaction is useful only when its subject and cause can be understood.
3. Select evidence before choosing speed. Faster playback does not fix absent
   results. Slow motion or a hold may help read an existing crucial image; neither
   adds a missing participant, action or result. Trim semantically redundant
   footage first, then choose speed/hold for perception and emphasis.
4. Allocate time from the visible information, at the FINAL framing. Small
   characters, clutter, multi-person relations and subtle gestures demand more
   attention than a clear single-subject action. A wide image fitted into a tall
   canvas may become hard to read. Cropping may also lose essential participants.
   Choose supported fit/crop and output dimensions yourself; inspect actual output.
5. For every segment, propose essential source intervals and the minimum output
   exposure you think necessary to read them. Treat these as estimates, not
   measurements or proof. The program checks interval containment and speed/hold
   arithmetic; independent observers still check what the footage actually shows.
6. A title, caption or film memory cannot establish who receives an action or
   recognition. Look for the actor, the target and the visible response. If the
   target is absent, extending the wrong image is not an adequate revision.
7. Compare deletion choices: what exact information would disappear if a moment
   were removed? Preserve indispensable context and outcomes, omit repeated
   processes, and let relative pace express importance. Avoid giving each entire
   source event one long segment. Keep the result at the reference's compact scale
   when its actual evidence permits; do not invent reasons to retain dialogue.
8. Source counterevidence is a planning signal, not a formatting error. When
   independently observed short footage fails a required action/outcome/identity,
   change the model's footage choice or expression route before rendering. Keep
   the reference's intended takeaway. Record failed attempts without converting
   them to success. Repeating a label or unsupported explanation is not progress.
9. Silently read the actual output without its intended answer first. Ask what
   difficulty, action, change and result a viewer could infer from the images.
   Then separately compare the reference. Looking at a subject is not evidence
   of understanding the narrative. Model observers may themselves be wrong.
10. Check local economy AND local readability. Long may be necessary for a subtle
    relation; short may still waste time on the wrong object. Review required
    moments in actual output time, including whether speed and framing hide them.
    Music rhythm requires separate evidence and remains unknown without listening.

Research basis and limits:

- Write-A-Video (ACM TOG 2019), Section 6: combines shot, cut and segment costs.
  Its narration/user timing and tunable duration defaults are not a universal
  duration rule for silent animated narrative.
  https://cg.cs.tsinghua.edu.cn/papers/TOG-2019-Write-a-Video.pdf
- DIRECT (2026 preprint), Sections 4.2–4.3: hierarchical editing intent, feedback
  and dynamic trimming of sequences. This implementation does not reproduce its
  feature extractors, beam search or music-driven constraints.
  https://arxiv.org/html/2604.04875v1
- Magliano & Zacks (2011): action discontinuity influences perceived event
  boundaries. The action/result checks above are a task-specific inference.
  https://doi.org/10.1111/j.1551-6709.2011.01202.x
- Cutting & Armstrong (2016): face size and clutter affect expression recognition.
  No universal minimum duration for an animated action follows from that study.
  https://link.springer.com/article/10.3758/s13414-015-1003-5
- Loschky et al. (2015): narrative context improves causal comprehension of the
  same silent clip; similar gaze patterns need not imply similar understanding.
  https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0142474
- Automatic Non-Linear Video Editing Transfer (2021 preprint): handles framing
  and temporal style, but exact reference playback speeds were user labelled.
  This project must not claim it can infer an exact original speed from the edit.
  https://arxiv.org/html/2105.06988
