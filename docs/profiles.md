# Configuration Profiles

A profile is a named bundle of configuration values applied to `config.yaml` in one command. There are eight built-ins, each setting the same 74 keys, so switching profiles moves every tunable together instead of leaving a half-configured mixture.

```bash
mnemosyne profile list
mnemosyne profile show quality
mnemosyne profile apply speed --dry-run
mnemosyne profile apply speed
```

---

## The eight profiles

| Profile | Intent | Use case |
|---|---|---|
| `minimal` | No LLM, no embeddings. Pure SQLite and FTS5 | Local-only agents, CI, constrained devices |
| `speed` | Fastest recall. Bit vectors, small scan limits, aggressive degradation | Real-time agents |
| `quality` | Maximum recall quality. Float32, every experimental feature on | Research agents, precision-critical work |
| `research` | Deep memory, aggressive consolidation and linking | Multi-session research, literature review |
| `paranoid` | Security first. Strict write classifier, sync encryption, no host LLM | Sensitive data, compliance |
| `balanced` | Sensible defaults. The "just works" profile | General purpose, default install |
| `embedded` | Tiny limits, bit vectors, no LLM | Raspberry Pi, IoT, edge |
| `development` | Verbose. Diagnostics on, warn-mode classifier | Debugging Mnemosyne itself |

`embedded` keeps embeddings **on** despite the name; `minimal` is the only profile that disables them.

## How they differ

The full 74 keys per profile are in `mnemosyne/core/profiles.py` and printable with `mnemosyne profile show <name>`. The values that actually distinguish them:

| Setting | minimal | speed | quality | research | paranoid | balanced | embedded | development |
|---|---|---|---|---|---|---|---|---|
| `vec_type` | int8 | bit | float32 | int8 | int8 | int8 | bit | int8 |
| `vec_weight` | 0.0 | 0.4 | 0.6 | 0.5 | 0.5 | 0.5 | 0.4 | 0.5 |
| `fts_weight` | 1.0 | 0.5 | 0.2 | 0.3 | 0.3 | 0.3 | 0.5 | 0.3 |
| `importance_weight` | 0.0 | 0.1 | 0.2 | 0.2 | 0.2 | 0.2 | 0.1 | 0.2 |
| embeddings | **off** | on | on | on | on | on | on | on |
| `llm_enabled` | false | true | true | true | true | true | false | true |
| `llm_timeout` | 30 | 15 | 120 | 120 | 60 | 60 | 15 | 60 |
| `cross_session` | 0 | 0 | **1** | **1** | 0 | 0 | 0 | 0 |
| `default_scope` | session | session | **global** | **global** | session | session | session | session |
| `write_classifier` | off | off | warn | warn | **strict** | off | off | warn |
| `sync_encrypt` | false | false | false | false | **true** | false | false | false |
| `persona_enabled` | false | false | true | true | false | false | false | true |
| `auto_sleep_enabled` | false | true | true | true | true | false | true | true |

Only `quality` turns on the whole vector-dependent feature block (polyphonic recall, query intent, fact recall, enhanced recall, proactive linking, lenient fact match, recall diagnostics). `research` enables most of it. `development` enables only recall diagnostics. The rest disable all of it.

`paranoid` is the only profile with a non-empty `ignore_patterns`, covering passwords, tokens, API keys, secrets, `Bearer`, `Authorization`, and PEM key blocks.

## Validation

`apply` validates before writing and refuses the whole profile if any rule fails, so you never get a partially applied profile. Thirteen rules are enforced, and they exist because these combinations are silently useless rather than loudly broken:

- The three embedding-disable aliases (`no_embeddings`, `skip_embeddings`, `embeddings_off`) must agree.
- `vec_weight` must be above zero when embeddings are on. A profile with embeddings enabled and zero vector weight pays for embeddings and ignores them.
- `cross_session` requires `default_scope: global`. Cross-session recall over session-scoped memories returns nothing.
- `smart_compress`, `sleep_model_refresh_enabled`, `llm_conflict_detection`, and `persona_enabled` each require `llm_enabled`.
- `tier3_max_chars` must be above zero when `smart_compress` is on.
- `proactive_linking`, `polyphonic_recall`, `enhanced_recall`, and `query_intent` each conflict with `no_embeddings`.
- Every key must exist in `ENV_VAR_MAP`.

## The restart trap

Exactly one of the 74 keys is in `REQUIRES_RESTART`: **`vec_type`**. Every profile sets it, and the eight profiles disagree (`int8`, `bit`, `float32`).

This matters more than it looks. `vec_type` determines the element type of the sqlite-vec tables, which is fixed at creation. Applying a profile that changes it does not convert your existing vectors, and `mnemosyne config reload` cannot apply it either.

Crossing a `vec_type` boundary needs:

```bash
mnemosyne profile apply quality
# restart the process, then
mnemosyne reindex
```

`mnemosyne profile apply` prints only "Run 'mnemosyne config reload' to apply changes", which is insufficient advice whenever `vec_type` changed. Check `mnemosyne profile show` against your current config before applying.

## CLI

```bash
mnemosyne profile list                                  # all built-ins with rating bars
mnemosyne profile apply <name> [--dry-run] [--config <path>]
mnemosyne profile show <name>                            # key = value, sorted
mnemosyne profile create <name> [description...]         # see the caveat below

mnemosyne config reload                                  # re-read config.yaml
mnemosyne config get <key>
mnemosyne config set <key> <value>
mnemosyne config migrate                                 # import current env vars into config.yaml
```

`--config <path>` works on `apply` but is missing from the printed help.

`config set` warns when the key requires a restart.

> **`profile create` does not persist.** It writes into an in-memory dict that is discarded when the process exits, and `profile list` never returns user profiles anyway. Since each CLI invocation is a fresh process, a profile created this way is unreachable even from the very next command. Treat it as non-functional. To capture your own configuration, copy `config.yaml`, or add a profile to `PROFILES` in `mnemosyne/core/profiles.py`.

## config.yaml

Path resolution, in order: `$MNEMOSYNE_DATA_DIR/config.yaml`, then `$HERMES_HOME/mnemosyne/config.yaml`, then `~/.hermes/mnemosyne/config.yaml`.

Read precedence is **`config.yaml` > environment variable > built-in default**.

The file is seeded from defaults on first access, preferring any already-set environment variable, and seeding never overwrites an existing file. Writes are a full read-modify-write with sorted keys, so **comments do not survive** a `config set` or `profile apply`.

> Two subsystems ignore `config.yaml` entirely. The persona modules and SHMR read `os.environ` directly at import time, so the `persona_*` and `shmr_*` values a profile writes have no runtime effect unless something also exports them as environment variables. See [Persona](persona.md) and [SHMR](shmr.md).

## See also

- [Generated configuration reference](api/configuration.mdx) for every key, its real default, and restart requirements
- [Configuration](configuration.md) for the hand-written topic guide
