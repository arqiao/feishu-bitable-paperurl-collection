# Command Output Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `goAIPM --towiki` recover safely from transient failures and make both requested programs report accurate, actionable outcomes.

**Architecture:** Keep retry policy local to `goAIPM.py`: classify transient exceptions, retry read-only image downloads directly, and restart the complete document write after ambiguous write failures. Keep `goMessage.py` behavior unchanged while collecting explicit counters for its final report.

**Tech Stack:** Python 3, `requests`, `unittest`, existing Feishu client.

---

### Task 1: Towiki Recovery and Reporting

**Files:**
- Modify: `src/goAIPM.py`
- Create: `tests/test_towiki_output.py`

- [x] Write tests that simulate a first transient write failure followed by
  success and assert that the document is cleared twice, the full write is
  restarted, and recovery is printed.
- [x] Run
  `PYTHONPATH=src python -m unittest tests.test_towiki_output` and verify the
  new test fails because no whole-document retry exists.
- [x] Add a transient-error classifier covering timeout, connection,
  chunked-read, HTTP 429, and HTTP 5xx cases while excluding authentication,
  permission, and deterministic validation failures.
- [x] Add bounded read retries to `_towiki_download_image()`.
- [x] Make `_towiki_append_blocks()` report successful block progress.
- [x] Make `process_towiki()` retry the complete clear-and-write operation,
  print recovery, and print an explicit partial-document warning on final
  failure.
- [x] Run the focused test and verify it passes.

### Task 2: Accurate GoMessage Summary

**Files:**
- Modify: `src/goMessage.py`
- Create: `tests/test_gomessage_output.py`

- [x] Write tests for a summary containing one parse error and for the
  no-new-message state message.
- [x] Run
  `PYTHONPATH=src python -m unittest tests.test_gomessage_output` and verify the
  tests fail because no structured summary helper exists.
- [x] Count parsing errors separately from successful parses.
- [x] Track Bitable writes, skips, recalls, and state advancement for the final
  summary without changing processing decisions.
- [x] Print the selected profile and an explicit no-new-message state result.
- [x] Run the focused test and verify it passes.

### Task 3: Documentation and Verification

**Files:**
- Modify: `docs/technical/ACCESS_FAILURE_DIAGNOSTICS_PLAN.md`

- [x] Add completed ledger entries for `goAIPM --towiki` and
  `goMessage --profile`.
- [x] Run
  `python -m compileall -q src/goAIPM.py src/goMessage.py tests/test_towiki_output.py tests/test_gomessage_output.py`.
- [x] Run
  `PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py'`.
- [x] Rerun both supplied `--towiki` commands and verify each ends with a clear
  success result after writing all blocks.
- [x] Rerun `python src/goMessage.py --profile ai` and verify the no-new-message
  path does not duplicate records or advance state.
