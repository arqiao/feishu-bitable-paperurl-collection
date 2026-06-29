# All CLI Parameters Reliability Design

## Scope

Audit and optimize every command-line path in the five scripts previously
updated:

| Script | Primary modes | Modifiers |
| --- | --- | --- |
| `dfZSXQ.py` | `--his`, `--update` | none |
| `goWTA.py` | `--his`, `--update` | none |
| `goWXGZH.py` | `--his`, `--update`, `--searchbiz`, `--repair-last-update` | `--list`, `--refresh-cache` |
| `goAIPM.py` | `--file`, `--list`, `--daily`, `--update`, `--weekly`, `--towiki` | none |
| `goMessage.py` | default incremental, `--all`, `--reset`, `--list-nolink` | `--profile`, `--start`, `--end` |

The existing command names and normal data-processing behavior remain
compatible. This work does not introduce a new CLI framework.

## Run Contract

Each mode must make five facts visible:

1. selected mode and input range;
2. target account, group, document, file, or profile;
3. external and local writes that will occur;
4. final counts for success, failure, skip, and partial results;
5. whether the incremental state changed, including the reason.

Invalid values and incompatible combinations must fail before client
initialization, network access, local writes, or external writes.

## State Safety

Historical and partial runs are not incremental checkpoints:

- `dfZSXQ --his` downloads the requested range without filtering against or
  updating `last_download_date`.
- `goWTA --his` and `goWXGZH --his` do not update their incremental markers.
- `goMessage --start/--end` does not update `last_processed_time`; otherwise
  unselected links from the same message timestamp could be skipped.
- `goMessage --reset` uses an effective start time of zero but does not persist
  zero before fetching. It writes the final state only after processing reaches
  the existing success condition.
- `goMessage --list-nolink` is read-only and cannot be combined with state
  mutation options.

## Parameter Validation

- Date ranges use valid calendar dates and require start date not later than
  end date.
- Message range indices are positive and require start not greater than end.
- `goMessage --reset` and `--all` are mutually exclusive.
- `goMessage --list-nolink` rejects `--reset`, `--start`, and `--end`.
- `goWXGZH --refresh-cache` is valid only with `--his` or `--update`.
- Missing list files and empty source values are reported before external
  access where possible.
- Modifiers that do not apply to a selected mode are rejected rather than
  silently ignored.

## Verification Policy

High-impact modes (`--reset`, `--all`, historical bulk processing,
`--repair-last-update`, and `--refresh-cache`) are tested with mocks and
read-only preflight checks only. Normal incremental and read-only modes may be
run against real services.

The parameter matrix is complete only when every row has:

- a validation test;
- a state-transition or no-transition test;
- an output assertion for its final result;
- a note explaining whether real execution was performed.

## Delivery Batches

1. State safety and argument validation.
2. Mode headers, final summaries, and removal of misleading/dead output.
3. Full parameter-matrix tests, low-risk real runs, and diagnostic-ledger
   review.
