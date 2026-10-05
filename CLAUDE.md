# CLAUDE.md — pheasant-swarm-lab

Context hand-off for any agent working on this repository. Read it first. It
describes the system **as it is now**; where this file and the code disagree,
the code is authoritative.

---

## 1. What this is

A laptop-local lab that drives a hierarchical research swarm into a
[Pheasant](https://github.com/esatt10/pheasant-kb) knowledge region, freezes an
evidence-linked benchmark, and compares five isolated arms against it. The
product question: **can a stateless agent with only Pheasant rival the
source-aware specialist that built the collection?**

The five questions it keeps apart — collection, persistence, retrieval,
answering, learning — are in `README.md`. This file is the parts a contributor
needs that the README does not carry.

## 2. Rules

1. **A metric that cannot carry its denominator reports
   `insufficient_evidence` with `value: None`.** Never `0.0`. A point that
   could not be measured is not one that measured badly.
   `MetricResult.validate()` enforces it and refuses a result with no
   `limitation`.
2. **A gate set cannot be constructed empty**, and a verdict is tri-state.
   `all([])` is `True`; `GateSet.__init__` refuses, and `PASS` requires that
   every gate was *evaluated*.
3. **Unknown is not negative.** Served, considered, included and
   `not_selected` carry weight zero. Only a caller's judgement or a
   deterministic matcher produces polarity.
4. **Isolation is code, not convention.** `arms/base.py` declares each arm's
   permitted inputs and raises. A change that hands a Pheasant arm the
   research package must fail a test, not a review.
5. **Raw JSONL is authoritative and append-only.** Corrections supersede; no
   row is ever rewritten. Every derived thing — the DuckDB projection, the
   metrics, the reports — rebuilds from it.
6. **Reserve before you spend.** `CostLedger.spend` reserves the worst case,
   runs, then reconciles. A guard that reserves an average lets a run overshoot
   on exactly the population of calls a budget exists to stop.
7. **Tool names are configured, never assumed**, and heuristic matching is off.
   `doctor` refuses before any spend.
8. **The offline suite must stay offline.** `tests/conftest.py` strips real
   credentials and points every default at something local.
9. **Two runs are comparable only when their `config_digest` matches.** The
   digest deliberately excludes *where* a run happened (output root, absolute
   config paths) — a run copied to another machine must still be resumable.
10. **The demo measures the demo.** Anything produced against the mock region
    or the `replay` provider says so in its own limitations section.

## 3. Canonical commands

```bash
./scripts/bootstrap.sh
uv run pheasant-lab demo --config configs/demo.yaml   # offline, ~12s, free
make check                                            # lint + schemas + tests
uv run python scripts/export_schemas.py               # after changing a record shape
make ui && uv run pheasant-lab serve                  # the console, http://127.0.0.1:8770
```

## 4. Design decisions worth knowing before you change something

- **The three-phase round.** `orchestrator._run_round` runs discovery
  concurrently, **admission sequentially and round-robin across subtopics**,
  then extraction concurrently. Admission is the only phase touching the shared
  registry: which branch reaches a shared candidate first decides which
  subtopic's question drives its extraction, so scheduling decided the corpus.
  Round-robin rather than one-branch-at-a-time, because draining a broad
  subtopic first starves a narrow one and its facet reports as uncovered when
  the corpus in fact covers it.
- **`fact_f1` is computed from TP/FP/FN, not from P and R.** An abstaining arm
  has no precision denominator; driving F1 off the ratios would drop every
  abstention out of the comparison and flatter the arm that abstained by
  removing it from its own denominator.
- **`read_passages` exists for every arm.** "A citation names something this
  session read" is the question. Defining it as "something a Pheasant search
  returned" scored the specialist's every citation invalid, because it does not
  use Pheasant.
- **Only `P0` is pinned to the sealed snapshot, and only where the region
  offers a pin.** A pinned search is answered from that state *or refused*,
  and `P1`'s own treatment moves the snapshot. pheasant >= 0.12.6 accepts
  `snapshot_id` and `as_of` on `search_context` (before that, HTTP only), so
  the shipped example config maps both and `P0` is pinned; against an older
  region, drop them from the map and `Retriever.supports_pinning` goes false
  and the run records that `P0` ran unpinned. The drift check runs once,
  before any arm, so it guards the freeze-to-evaluate gap. In pheasant a
  memory record is an indexed artifact: a memory write moves `corpus`,
  `graph` and `retrieval` too, never `memory` alone, so a second `evaluate`
  after `P1` has seeded is refused - correctly.
- **Pheasant's submission contract has three steps, and the lab owns two.**
  `submit_documents` lands bytes in a directory and receipts them; the caller
  registers that directory as a `document_folder` source (`Ingestor.register`,
  which needs the region's state path allow-listed); a sync indexes it; and
  `acknowledge_ingest` promotes receipts, answering with *counts* while the
  receipts themselves are read back through `get_ingest_status`. On a fleet
  the sync is published, so the barrier polls `still_accepted` rather than
  trusting one call.
- **A hit's text is a preview; the answerer reads documents.** Pheasant caps
  a hit at a 500-character `text_preview`, and the lab's documents open with
  front matter, so `Retriever.hydrate` reads each passage back whole through
  `get_file_summary` and records `text_source`. The join from a hit to the
  lab's source id is the receipts (`artifact_sources`), never
  `provenance.source_id`, which is the *region's* source name.
- **An argument absent from `argument_map` is one the lab does not send.**
  That is how a capability the region lacks is declared, and `doctor` checks
  every mapped name against the tool's advertised schema
  (`capabilities.configured_arguments`) so a map claiming something the tool
  never heard of is refused before any spend.
- **The benchmark version is content-addressed over the evidence ledger and
  the composition**, not over the run. A version carrying the run id would give
  the same question a different id in every run, and there would be no trend
  line.
- **A collection profile supplies defaults, never overrides.**
  `collection.profile` (`scholarly` | `web` | `balanced`, `profiles.py`) fills
  only the `collection`/`stopping` keys the file leaves unset, which is why
  `experiment.example.yaml` states none of them. "Authoritative" is the
  profile's `authoritative_source_types`, not peer review: under `web` a
  press piece counts toward sources and families but never toward
  `minimum_review_or_primary_sources`. `balanced` sets
  `max_results_per_provider`, because without it the first provider in the
  list fills every subtopic and the second is never asked.
- **Graph expansion is recorded, never read, and declared like the pin.**
  `replay.graph_expansion` is sent only when the pheasant file maps `expand`
  (`expand_sent` records which; the shipped file maps it, for pheasant >=
  0.13.1, and an older region unmaps it with `--set
  argument_map.search.expand=null`), and the neighbourhood lands on each search
  record as `graph_neighbors`. No arm reads it: handing answerers neighbours
  changes what `P0` measures, and on this corpus a depth-2 walk reaches other
  papers through shared authors and venues, decoys included. That is a design
  decision with its own evidence, not a side effect of an adapter update.
- **Question wording and its matcher are disjoint by construction.** The
  builder splits a claim into subject terms (which the question names) and
  answer terms (which the matcher requires), and `_harden` rebuilds any matcher
  the question would satisfy anyway. The leakage checker is the independent
  net, not the only one.
- **The console is a fold and a launcher, never a second runtime.**
  `console/projection.py` builds the live view from `raw/*.jsonl` alone, in
  Python, once - the browser renders the server's snapshot rather than
  re-deriving it, because a second fold in TypeScript would drift. Runs are
  CLI child processes (`console/launcher.py`), so a browser-started run and a
  typed one leave identical traces. Nothing the console computes is a number a
  report states; arm progress is counted, never scored (rule 1).
- **The console writes one file, and only for topics.** A research topic is
  content, not a setting, so it cannot be a `--set`; `console/topics.py`
  writes the config's current topics plus the new one to
  `configs/topics.local.yaml` (git-ignored) after validating with the same
  `Topic` model, re-resolves the whole config against what it wrote, and the
  run points at it with `--set experiment.topics_file=...`. The shipped topics
  files are never rewritten, and the argv is still the whole story.
- **Traces are joined, not recomputed.** `console/traces.py` attributes every
  event and span to one actor (`agent_id`, else `arm_id`, else
  orchestration), nests events by `span_id`, and joins an MCP log row to the
  `mcp.tool.call` event that recorded it on `(tool, attempt, duration_ms)` -
  the log carries no span id. An event whose span was never exported is
  placed in its own actor's narrowest enclosing span (same question when both
  carry one) and marked `placed: by_time`/`by_question`; otherwise it stays
  loose. Prompts and completions are not recorded, only their digests,
  tokens and spend, and the trace view says so.
- **The pre-claim interval is recorded, not inferred.** On a role-split
  Pheasant `sync_source` answers `status: queued`; the lab emits
  `ingest.sync` with that disposition, one `ingest.barrier` per acknowledge
  poll, and - when the region offers `get_index_queue` (capability
  `index_queue`, optional) - each task's claim state. A document is shown
  `awaiting_claim` only on that evidence; silence never moves a document
  forward. `index_queue` and `mock_claim_seconds` are left out of the config
  digest: they change what a run reports, not what it measures.

## 5. Traps this repository has already fallen into

- **A `Retry-After: 0` is a server saying "immediately".** `retry_after or
  backoff` swallowed it and waited the configured 30s instead. Falsy-zero.
- **A recorded snapshot id that is never sent is a run that looks pinned and is
  not.** `SearchRequest.as_arguments` built every other field and dropped
  `snapshot_id` and `as_of` on the floor. Found by the contract test that
  asserts a drifted snapshot refuses; no unit test could have seen it.
- **A fixed temp filename is a collision waiting for a second writer.** The
  mock region's save used `<name>.<pid>.tmp`; two branches in one process share
  a pid, so the first rename took the file out from under the second and the
  branch died. Unique per write, and unlink on failure.
- **A check-then-write across threads is not one step.** `state.known()`
  followed by `add_source()` let two branches admit the same candidate, and two
  runs of one configuration disagreed about their own corpus by a claim or two
   — enough to move a metric, not enough for anyone to notice why.
- **Restoring a thread-local you never set writes `None` back.** `Tracer.span`
  captured `getattr(self._local, "trace_id", None)` and restored it, so every
  event after the first span carried a null trace id. Captured through the
  properties now. Found by validating a real run against the published schema.
- **A config digest that includes the output directory makes a copied run
  unresumable.** Two runs are comparable on their experiment, not on where they
  were written.
- **A question built from a claim's own salient terms contains its own
  answer.** The first builder used the same terms for the wording and the
  matcher; the leakage checker refused three questions per run, correctly.
- **`ruff format` rewrites the lines your `sed` was aiming at.** Two test edits
  silently no-oped after a format pass and left an undefined name.
- **A mock kinder than the server hid every adapter bug at once.** Until the
  lab was first run end to end against a real pheasant (0.12.16), the mock had
  auto-indexed unregistered submissions, returned receipts under `receipts`
  rather than `accepted`/`rejected`, listed acknowledgements rather than
  counting them, put the lab's source id in `provenance`, returned whole
  passages, flattened the memory record and parsed a preference grammar
  pheasant does not have. So a live `collect` died at its first sync; with
  that fixed, every receipt read as `no_receipt`, acknowledging crashed, every
  hit collapsed onto one source id, answerers read front matter, memory ids
  were empty and every seeded preference was silently ignored. The mock now
  mirrors captured wire shapes, and `tests/contract/test_pheasant_wire.py`
  reads the captured payloads themselves (`tests/fixtures/pheasant/`) so the
  next drift fails offline. Regenerate those fixtures against a new release
  with `scripts/capture_pheasant_fixtures.py`, and keep the old ones: the
  readers are tested against every supported release, not only the newest.
- **A capture that never contained a hit shape cannot test it.** pheasant's
  graph arm puts a node hit's source on the hit, not under `provenance`, and
  has since before 0.12.16 - but that capture ranked no node hit into its
  result list, so `region_source` was read from `provenance` alone and such
  hits were fetched back with no `source_name`. The 0.13.0 capture ranked one
  second. Read every shape a capture holds, not just the first hit.
- **A field added to the config changes every existing run's digest.** The
  digest is over the whole resolved config, so a new setting - even unset -
  makes every run started before it refuse to resume without `--fork`.
  `LabConfig.digest` leaves `replay.graph_expansion` out while it is unset,
  and the `expand` mapping with it, since a mapping nothing asks for sends
  nothing; do the same for the next one.
- **A sequence number taken under a lock and written after it is not
  sequential.** `EventLog.emit` numbered events inside the lock and appended
  outside it, so concurrent branches wrote 10 before 9 and `verify` reported a
  discontinuity in a stream that had lost nothing - a replay test that failed
  one run in eight.
- **A mock that accepts an argument the real server rejects hides the bug it
  was built to expose.** The adapter mapped `snapshot_id` and `as_of` onto
  `search_context`, which pheasant then exposed on HTTP only. The mock accepted
  both, so the demo, the contract tests and the drift-refusal test all passed
  while a live run would have failed at `P0`'s first search — or, worse,
  ignored the pin and looked pinned. Two fixes, because one was not enough:
  the adapter sends only what the map declares (`pin_sent` records which), and
  preflight now checks every *configured* name rather than a static list that
  was written before the map existed.
- **A run that prints nothing is a run the launcher could not find.** The
  console first learnt a launch's run id from its output, and `demo` is
  silent for the whole of a barrier wait - exactly when someone wants to
  watch. The run directory is watched apart from the output now.
- **A backoff can step over a whole state.** The barrier polls with doubling
  backoff, so a claim that starts and finishes between two polls is never
  observed as `claimed`. The indexer lane says "claim between polls" rather
  than drawing the whole interval as a wait nobody serviced, and the test
  fixture's claim window is sized against the backoff so every state is seen.
- **A receipt's timestamp is its first one.** `ingest-receipts.jsonl`
  re-appends a receipt as `indexed` carrying the time it was *accepted*, so a
  replay that placed receipts by time showed documents indexed before the
  sync that indexed them. A replay admits `indexed` only after it has folded
  the crossed barrier, which is how the lab learnt it in the first place.
- **A span id on an event is not a span in `spans.jsonl`.** Events emitted on
  a worker thread (an arm's MCP calls and model calls, most orchestration
  bookkeeping) carry a span id the tracer never exported, so nesting by
  `span_id` alone left every arm event outside its question's span. The trace
  view places them by time and question, and says it did.
- **A drag ends in a click.** Panning a canvas by dragging across a node
  selected that node on release. `components/zoom.tsx` swallows the click
  after a real drag, in the capture phase so no node handler sees it.
- **Events and receipts are two files, read in either order.** Stamping
  "awaiting claim" on documents when the queued sync arrived missed every
  document whose acceptance was read afterwards. The claim state lives on the
  model and is applied at snapshot time.
