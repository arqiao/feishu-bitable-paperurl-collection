# Command Output Reliability Design

## Scope

Improve these command paths:

- `python src/goAIPM.py --towiki <web-url> <wiki-url>`
- `python src/goAIPM.py --towiki <pdf-path> <wiki-url>`
- `python src/goMessage.py --profile ai`

The change covers terminal reporting and narrowly scoped recovery from transient
network failures. It does not change document parsing, message selection,
deduplication, recall policy, or state semantics.

## Towiki Behavior

Source reads and image downloads are safe to retry because they are read-only.
Document writes are not retried in place after an ambiguous timeout because the
server may already have accepted the request. Instead, a transient write failure
restarts the complete document replacement: clear the target and write all
blocks again.

The terminal output must show:

- source and target before work starts;
- source type and extracted block count;
- document-write attempt number and block progress;
- transient failure reason and whether a complete restart will occur;
- recovery after a later successful attempt;
- final success, or final failure with a warning that the target may contain
  partial content.

Authentication, permission, invalid input, and deterministic API validation
errors are not retried.

## GoMessage Behavior

The command must identify the selected profile and continue using the current
processing behavior. Its final summary separates:

- links parsed successfully;
- links with parsing errors;
- records written to the local CSV;
- records written to or skipped from Bitable;
- message recall results;
- whether `last_processed_time` advanced.

A parsing error remains logged to CSV and may still be recalled under the
existing policy. The output must state this rather than counting it as a
successful parse.

## Verification

Unit tests simulate transient recovery and final failure without external
writes. After tests pass, both supplied `--towiki` commands are rerun to restore
complete target documents. `goMessage --profile ai` is rerun only after the
already processed batch, so it should take the no-new-message path and must not
duplicate the 35 records.
