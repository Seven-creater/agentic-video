# Slot fine-cut process-local file proofs

Policy: `slot_finecut_process_local_stat_sha_cache_v1`.

`slot_finecut_budget` and `slot_finecut_baselines` reuse a full SHA256 already
calculated in their current Python process only while the resolved file path and
`size`, `mtime_ns`, `ctime_ns`, `ino`, and `dev` remain identical. A cold process
always reads all file bytes for its first proof. Hashing compares stat values
before and after the read and rejects a file that changes during hashing.
Changed stat values discard the earlier proof and require a new full read.

`verified_sha256(path, force=True)` always reads the bytes again. `invalidate(path)`
removes one process-local entry; `reset()` removes all entries. None of these
functions edits files, authorizes execution, changes model inputs, or starts MCP.
The media and renderer SHA functions remain unchanged.

This optimization does not cache authorization objects, JSON parsing, call-prefix
checks, input locks, plan checks, evidence checks, or the bound preparation
comparison. Those semantic validations still execute on every load. It does not
persist a cache file or change historical policy/authorization artifacts.

An unchanged-stat cache hit is reuse of an earlier actual byte read, not a new
independent integrity audit. File-system metadata is not proof that bytes cannot
be changed while preserving metadata. Before delivering actual outputs, perform
an explicit complete audit of every historically protected file with
`force=True`, compare each full digest with its recorded digest, and keep that
fresh audit separately. Do not describe cached checks as final proof that old
bytes stayed unchanged.
