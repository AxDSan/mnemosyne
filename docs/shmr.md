# SHMR: Self-Harmonizing Memory Reasoning

**Status: library only. Nothing in the shipped code calls it.** See [Wiring](#wiring) before planning around it.

SHMR clusters semantically similar facts, asks an LLM to reconcile the contradictions inside each cluster into a smaller set of stable higher-order beliefs, scores how coherent that result is, and keeps it only if the score clears a threshold. It is the piece of Mnemosyne that tries to reason *about* stored knowledge rather than retrieve it.

The research basis is ECHO-OR, rearchitected for BEAM.

---

## What a run does

`harmonize(beam, batch_size=None, max_iterations=None, similarity_threshold=None)` in `mnemosyne/core/shmr.py`:

1. **Initialize schema.** Creates `harmonic_beliefs` and `memory_resonance_log` if absent.
2. **Collect candidates.** Active rows from `facts` (embedding the `object` column), plus recent `episodic_memory` rows longer than 10 characters, truncated to 300 characters and synthesized as `memory contains <content>` pseudo-facts.
3. **Bail out** if fewer candidates than `MNEMOSYNE_SHMR_MIN_CLUSTER_SIZE`, returning `status: insufficient_candidates`.
4. **Cluster.** Builds an adjacency over pairwise cosine similarity at or above the threshold and takes connected components. This is O(n²) in the candidate count, which is why the batch size is small.
5. **Harmonize each cluster,** up to `max_iterations` times. The prompt asks the model to resolve contradictions, extract higher-order beliefs, dampen noise, and emit only stable beliefs as a JSON array of 1-5 items, each with an action of `create`, `update`, or `dampen`. Output parsing has four fallbacks: direct JSON, a fenced block, a bare array, then a per-object regex.
6. **Score harmony.** The mean of each belief's embedding similarity to the cluster centroid weighted by its confidence, multiplied by a consistency bonus derived from how similar the beliefs are to each other. If the score clears `MNEMOSYNE_SHMR_HARMONY_THRESHOLD`, apply and stop. Otherwise feed the proposed beliefs back into the cluster as candidates and iterate.
7. **Apply.** `dampen` reduces the source fact's confidence by 0.15 with a floor of 0.1. `update` rewrites the fact's object and confidence. All three actions then upsert a `harmonic_beliefs` row whose `provenance` is the JSON list of contributing fact ids.
8. **Log** one `memory_resonance_log` row and return counts, the average harmony score, and a duration.

Returned status is `harmonized` if anything was written, otherwise `no_convergence`.

## Tables

**`harmonic_beliefs`** holds `belief_id` (a hash of cluster, subject, predicate, and the first 50 characters of the object), `subject`, `predicate`, `object`, `confidence`, `provenance` (JSON array of source ids), `cluster_id`, `iteration`, `created_at`, `updated_at`. Indexed on subject, predicate, and confidence.

**`memory_resonance_log`** holds `id`, `session_id`, `cluster_count`, `beliefs_generated`, `contradictions_resolved`, `harmony_score_avg`, `duration_ms`, `created_at`.

Two known quirks: `iteration` is always written as `0` rather than the real iteration number, and the upsert omits `created_at`, so re-harmonizing an identical cluster resets that timestamp.

## Configuration

All seven are read at **module import time** and cannot be changed afterwards. Only three can be overridden per call.

| Variable | Default | Controls | Per-call override |
|---|---|---|---|
| `MNEMOSYNE_SHMR_BATCH_SIZE` | `50` | Facts pulled per run. Episodic pull is half this | yes |
| `MNEMOSYNE_SHMR_MAX_ITERATIONS` | `3` | Refinement attempts per cluster | yes |
| `MNEMOSYNE_SHMR_SIMILARITY_THRESHOLD` | `0.70` | Cosine threshold for clustering | yes |
| `MNEMOSYNE_SHMR_HARMONY_THRESHOLD` | `0.60` | Minimum harmony score to accept beliefs | no |
| `MNEMOSYNE_SHMR_MIN_CLUSTER_SIZE` | `2` | Minimum candidates to run, and minimum cluster size to keep | no |
| `MNEMOSYNE_SHMR_TEMPERATURE` | `0.2` | Sampling temperature for the harmonization call | no |
| `MNEMOSYNE_SHMR_MODEL` | `""` | Model override for the cloud fallback. Empty uses the extraction default | no |

> **`config.yaml` does not reach SHMR.** These keys exist in `ENV_VAR_MAP`, but the module never consults `MnemosyneConfig`. The defaults declared in `config.py` also disagree with the real ones above (it declares `shmr_max_iterations: 10`, `shmr_harmony_threshold: 0.5`, `shmr_min_cluster_size: 3`, `shmr_temperature: 0.3`). Trust this table and set environment variables.

## Dependencies

**Embeddings are required.** Candidate collection calls `embed` without a guard, so `harmonize()` raises on an install with embeddings disabled. This is the one hard requirement.

**An LLM is required to produce anything.** The call tries the local GGUF path first, accepting the result only if it is longer than 10 characters, then falls back to the cloud extraction client. Both paths swallow exceptions, though the cloud path now logs at debug level.

The cloud fallback was dead until recently: it imported `ExtractionConfig` and `ExtractionClient` from `mnemosyne.core.extraction`, which exports neither, and the resulting `ImportError` was swallowed. Only the local GGUF path could produce a belief. If you evaluated SHMR before that fix and saw nothing, that is why.

Without a reachable LLM, `harmonize()` does not fail loudly. Each iteration finds empty output and continues, all iterations are consumed per cluster, and the function returns normally with `beliefs_generated: 0` and `status: no_convergence`, having still written a resonance-log row. **There is no signal distinguishing "no LLM available" from "the model could not reach a coherent belief."** If you get `no_convergence`, verify your LLM configuration before concluding anything about your data.

## Other entry points

- `recall_beliefs(beam, query, top_k=10)`: retrieves the highest-confidence beliefs and rescores them by query similarity times confidence. Its docstring says recall calls it when `harmonic=True`; no such call site exists.
- `reflect(beam, question, facts=None, top_k=10)`: single-pass synthesis over fact recall output. Returns `None` when no LLM is available.
- `get_resonance_log(beam, limit=10)`: recent run history.

## Wiring

The docstring on `harmonize()` states that it is "called automatically by `mnemosyne_sleep()` after consolidation" and "can also be called directly via MCP tool." **Neither is true in this tree.**

- `sleep()` does not call it. The CLI `sleep` command calls `mem.sleep()` and nothing else.
- There is no `mnemosyne_shmr_*` MCP tool and no CLI subcommand.
- No test file references SHMR at all.
- The only references outside the module are configuration keys, this documentation, and `docs/rfc/noise-remediation-rnd.md`, which says plainly that SHMR "is not wired into the hygiene pipeline" and proposes wiring it.

To use it today, call it yourself:

```python
from mnemosyne.core.beam import BeamMemory
from mnemosyne.core.shmr import harmonize, get_resonance_log

beam = BeamMemory()
result = harmonize(beam)
print(result)          # {'clusters_found': ..., 'beliefs_generated': ..., 'status': ...}
print(get_resonance_log(beam, limit=5))
```

Start with a small `MNEMOSYNE_SHMR_BATCH_SIZE`. Clustering is quadratic in the candidate count and each cluster costs at least one LLM call, so a large batch on a slow endpoint takes a long time and produces one aggregate status at the end.

## See also

- [Architecture](architecture.md)
- `docs/rfc/noise-remediation-rnd.md` for the proposal to wire SHMR into the hygiene pipeline
- [Generated configuration reference](api/configuration.mdx)
