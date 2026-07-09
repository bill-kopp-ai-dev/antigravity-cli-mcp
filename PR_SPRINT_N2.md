# PR — Sprint N+2: prompt primitives & docstrings

> Branch: `feat/sprint-N+2-prompts` → `main`
> Base: `2171363` (= `feat/sprint-N+1-quota`)
> Version bump: `0.1.0` → `0.2.0`

## Summary

Sprint N+2 makes the `@mcp.prompt` registry a first-class surface of the
public API (alongside the existing `@mcp.tool` registry) and gives every
exposed prompt and tool a full four-section docstring that mirrors the
in-code contract.

No runtime behavior change. Pure documentation + tests.

## Commits (4 atomic)

| Hash | Subject |
|------|---------|
| `504be3c` | `docs(server): enrich 14 tool docstrings + 4 prompt docstrings (Sprint N+2 F1+F5+F6)` |
| `3cb3cee` | `docs(contrato): add per-prompt contract sections (Sprint N+2 F3)` |
| `cb9b333` | `docs(server): enrich remaining 4 prompt docstrings (Sprint N+2 F6-r)` |
| `feac6e9` | `test(contrato): prompt drift guard coverage (Sprint N+2 F4)` |

Plus the docs commit:

| Hash | Subject |
|------|---------|
| `<pending>` | `docs: reflect Sprint N+2 surface in README + CHANGELOG + pyproject` |

## Changed files

```
 CHANGELOG.md                   |  53 +++++++ (this PR)
 CONTRATO_TOOLS.md              |  54 +++--
 README.md                      |  39 ++++-
 pyproject.toml                 |   2 +- (version bump)
 src/agy_mcp_server/server.py   | 726 ++++++++++++++++++++++++++++-----------
 tests/test_prompt_drift_guard.py|121 +++++++
 6 files changed, ~995 insertions, ~208 deletions
```

## Functional changes (all behavior-preserving)

### F1 / F5 / F6 — Tool + prompt docstring enrichment

Every `@mcp.tool` (14 tools) and the four orchestrator `@mcp.prompt`s
that were previously either one-line or undocumented now carry the full
four-section shape:

```
Input:
    - field (type, required): description
Returns:
    - type / model name
Raises:
    - exception class: when
Side effects:
    - observable mutation
Example:
    usage snippet
```

The `agy_quota` tool keeps its A/B/C/D hybrid strategy section as a
documented special case (not a regression).

### F10 — Move `QuotaExhaustedError`

Moved from the bottom of `server.py` (line 1994) to line 155
(immediately after `_agy_path()`). Necessary because:

- Tools at line 349, 801, 902 reference `QuotaExhaustedError` in their
  `Raises:` docstrings — by the time the docstrings resolve, the class
  must be defined.
- External callers that `from agy_mcp_server.server import
  QuotaExhaustedError` get a clean import regardless of where in the
  file they import from.

The class itself is unchanged (same `__init__` signature: `model, used,
limit, reset_in_seconds`). Docstring expanded with Attributes,
Resolution paths, and architectural rationale.

### F3 — CONTRATO §Prompts restructured

The flat 7-bullet §Prompts section was replaced with four sub-sections
that mirror the in-code shape:

- `### Sync orchestration` (1 prompt, signature + input/output fields)
- `### Async orchestration` (1 prompt)
- `### Selection & safety` (2 prompts)
- `### Cheatsheets` (4 prompts)

### F4 — `tests/test_prompt_drift_guard.py`

Six tests, all green:

```
tests/test_prompt_drift_guard.py::TestPromptDrift::test_contrato_file_exists
tests/test_prompt_drift_guard.py::TestPromptDrift::test_documented_prompts_match_registered
tests/test_prompt_drift_guard.py::TestPromptDrift::test_all_prompts_have_input_section
tests/test_prompt_drift_guard.py::TestPromptDrift::test_all_prompts_have_use_when_anchor
tests/test_prompt_drift_guard.py::TestPromptDrift::test_known_named_prompts_listed
tests/test_prompt_drift_guard.py::TestPromptDrift::test_prompts_section_has_subheadings
```

Mirrors `tests/test_contrato_drift.py` (tool surface) so a future
asymmetric edit (prompt registered but not documented, or vice versa)
will fail CI.

## Test delta

| Before Sprint N+2 | After Sprint N+2 | Delta |
|-------------------|------------------|-------|
| 241 passed | 252 passed | +11 |

(Six from `test_prompt_drift_guard.py`; the additional +5 came from
other N+2 refactors documented in the project CHANGELOG.)

## Files unchanged

- `src/agy_mcp_server/persistence.py`
- `src/agy_mcp_server/timeout_policy.py`
- `src/agy_mcp_server/quota_tracker.py`
- `src/agy_mcp_server/settings.py`
- `tests/test_self_test.py`
- `tests/test_security.py`
- `tests/test_provider.py`
- `tests/test_timeout_policy.py`
- `tools/agy_smoke.py`

## Risks

None identified. This PR:

- Does not change the public tool or prompt registry (still 14 tools,
  8 prompts).
- Does not change return shapes, exception types, or env-var names.
- Does not modify runtime behavior of any `agy_*` tool.
- Does not bump dependency versions.

## Rollback

Revert the merge commit; no schema migrations, no data migrations.

## Follow-ups (out of scope for Sprint N+2)

- Add async-mode regression tests for `agy_start_task` /
  `agy_poll_task` / `agy_cancel_task` parity (currently relies on
  smoke + self-test metadata).
- Promote `agy_contract` to also be exercised by `agy_self_test` (right
  now both exist independently).