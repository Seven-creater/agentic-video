# Reference -> playable rough -> visual-story skill finecut

Latest user (2026-10-10) requests Goal completion: verify original chat,
extract the working77s ->21.9s line, clean code/deployment and run both references
in parallel. This supersedes historical review-only/stop/limited-trial notes.
Future references are random. No fixed22/180s,80calls,16windows or2rounds as total
limits. Stop on no evidence/real-edit/review progress or unknown request outcome.
Never replay unknown requests, silently reset old usage or loop for a forced pass.

Canonical server: omni-server (library/server_cli.py) -> clean_chain.py.
Rough: pipeline.execute with verified original_rough_v1 templates/renderer,
active_finecut=False, semantic_audit=False, editing_v2=False. Generate/play/review
actual rough before passing its real video to story_finecut.py.
Fine: packaged visual-story-finecut skill/cards, full rough observation,
model-selected local video/PTS frames, EDL, actual render, independent picture
reading/reference comparison and progress-driven revision. GLM owns creative
choices. Do not supply teacher EDL/movie plot or old77 cuts.
Historical resource bytes stay unchanged; a thin runtime adapter removes old
total caps. Source/time/stream/SHA validation and technical chunking remain.

History proof: docs/HISTORICAL_CLEAN_CHAIN.md and original workspace
runs/history_clean_chain_20261010/CHAT_PROOF.md. Old controllers/recoveries/output
remain stopped and immutable, outside the default server entry. New explicitly
requested evaluations live under oldtask/evaluations, bind old live ledgers and
the global historical manifest, and aggregate prior usage. Do not hot-swap workers.

Server references: shared/data/ref/video.mp4 (21.933333s) and
shared/data/ref/7692329355342679331/video.mp4 (198.461995s). Three-film library:
shared/data/videos. Detached jobs persist after SSH/Codex disconnect. Domestic
Coding Plan/OpenCode + unaltered official vision MCP; private keys/media outside
Git. Direct native SSH/SFTP with ProxyCommand/ProxyJump disabled; no system proxy
change. MiniMax/discovery remain frozen. No FlashVID compression is active.

Playable/decoded is not a quality pass. Preserve negative/conflicting reviews.
Deliver copyable absolute paths, rough/fine candidates and actual limitations.
Previous full instructions/handoff are archived in docs/archive. Python sources
live in src/omni_story; install editable before development.
