# All CLI Parameters Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every CLI mode in the five maintained scripts safe state semantics, early parameter validation, and an unambiguous final result.

**Architecture:** Keep mode-specific behavior in its existing script. Add small pure validation and summary helpers that can be unit tested without constructing clients; do not add a shared framework until repeated semantics are proven stable.

**Tech Stack:** Python 3, `argparse`, `unittest`, existing Feishu and platform clients.

---

### Task 1: Historical State Safety

**Files:**
- Modify: `src/dfZSXQ.py`
- Modify: `src/goWTA.py`
- Modify: `src/goWXGZH.py`
- Create: `tests/test_history_mode_state.py`

- [x] Add failing tests proving historical runs neither read their current
  incremental marker as a lower bound nor write a new marker.
- [x] Run `PYTHONPATH=src python -m unittest tests.test_history_mode_state` and
  confirm failures show the current marker mutation.
- [x] Add an `advance_state` decision at each historical/incremental boundary.
- [x] Print `last_* 未更新，原因：历史模式不推进增量状态` after successful
  historical processing.
- [x] Run the focused tests and confirm all pass.

### Task 2: Message Range and Reset Safety

**Files:**
- Modify: `src/goMessage.py`
- Create: `tests/test_gomessage_parameters.py`

- [x] Add failing tests for invalid ranges, conflicting modes, delayed reset,
  and partial-range state suppression.
- [x] Run `PYTHONPATH=src python -m unittest tests.test_gomessage_parameters`
  and confirm failures occur before any mocked external access.
- [x] Add a pure `validate_cli_args(args)` helper returning a precise error or
  raising `ValueError`.
- [x] Treat reset as an effective full-history start without persisting zero.
- [x] Suppress state advancement for `--start/--end` and explain why in the
  final output.
- [x] Run focused tests and confirm all pass.

### Task 3: WXGZH Modifier Contracts

**Files:**
- Modify: `src/goWXGZH.py`
- Create: `tests/test_gowxgzh_parameters.py`

- [x] Add failing tests that reject `--refresh-cache` with search/repair modes
  and verify repair output is emitted once.
- [x] Run `PYTHONPATH=src python -m unittest tests.test_gowxgzh_parameters`.
- [x] Add pre-client argument validation and mode headers.
- [x] Remove the unreachable duplicate mojibake repair block.
- [x] Add final summaries for search, repair, history, and update modes.
- [x] Run focused tests and confirm all pass.

### Task 4: Date Validation and Core Mode Summaries

**Files:**
- Modify: `src/dfZSXQ.py`
- Modify: `src/goWTA.py`
- Modify: `src/goWXGZH.py`
- Create: `tests/test_date_parameter_validation.py`

- [x] Add failing tests for malformed dates, impossible calendar dates, and
  reversed ranges in all three scripts.
- [x] Run `PYTHONPATH=src python -m unittest tests.test_date_parameter_validation`.
- [x] Add pure date-range validators before client construction.
- [x] Add per-run totals and explicit final success/partial/failure summaries.
- [x] Run focused tests and confirm all pass.

### Task 5: Remaining AIPM Modes

**Files:**
- Modify: `src/goAIPM.py`
- Create: `tests/test_goaipm_parameters.py`

- [x] Add failing tests for `--file`, `--list`, `--daily`, and `--weekly`
  headers, missing inputs, and final outcomes.
- [x] Run `PYTHONPATH=src python -m unittest tests.test_goaipm_parameters`.
- [x] Make the primary mode group required and validate list files before
  client initialization.
- [x] Add mode, source, target, and final result output while preserving the
  existing parsing and Bitable behavior.
- [x] Run focused tests and confirm all pass.

### Task 6: Parameter Matrix and Documentation

**Files:**
- Create: `docs/technical/CLI_PARAMETER_AUDIT.md`
- Modify: `docs/README.md`
- Modify: `docs/technical/ACCESS_FAILURE_DIAGNOSTICS_PLAN.md`
- Create: `tests/test_cli_help.py`

- [x] Record every mode/modifier, side effect, state field, and verification
  method in `CLI_PARAMETER_AUDIT.md`.
- [x] Add `--help` subprocess tests for all five scripts.
- [x] Run `python -m compileall -q src tests`.
- [x] Run `PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py'`.
- [x] Perform only the approved low-risk real runs and record outcomes in the
  audit matrix.
- [x] Update the diagnostic ledger and mark every completed plan checkbox.
