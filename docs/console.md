# The console

```bash
make ui                      # once: build the UI (Node 18+)
uv run pheasant-lab serve    # http://127.0.0.1:8770
```

A browser surface over the CLI, in pheasant-kb's visual language. It never
computes a number a report states: the live view is a fold of `raw/*.jsonl`
done once in Python, and every run it starts is the ordinary CLI in a
detached child process. It binds loopback because it can start paid runs.

Every page is a real URL. Reloading `/configure`, `/live/<run>`,
`/reports/<run>` or `/reports/<run>/traces/<agent>` reloads that page, and
Configure keeps the overrides you made in this tab.

Every page fits the window: a long form, a run list, a report or an expanded
trace scrolls inside its own panel, while the plan, the agent list and the
waterfall's time axis stay where they are. On a narrow or short window the
pages fall back to scrolling as a whole.

## Configure

A form over the YAML. Every change is a `--set`, shown as the exact command
it will run. `plan` projects the worst-case cost as you edit, `doctor` checks
models, providers and the region's tools before any spend, and a change that
moves the config digest says so.

![Configure](images/configure.png)

## Research topics: from an intent, from seed terms, or both

Under **Research topics**, **+ New topic** opens a form.

1. **Start from what you want to find out.** Type an *intent* in your own
   words: the question, what would settle it, what you suspect is contested.

   ![An intent, typed](images/topic-intent-typed.png)

2. **Draft from intent.** The planner's model proposes a title, seed terms
   and facets, through `pheasant-lab draft-topic` under its own small budget
   (`--max-cost-usd`, default $0.25, reserved before the call). Everything it
   fills in stays editable. Under the offline `replay` provider the draft is
   rule-based and says so. With a hosted model it expands the field's
   vocabulary: synonyms, older names, identifiers.

   ![The drafted topic, ready to edit](images/topic-intent-drafted.png)

3. **Edit and save.** Seed terms are optional when there is an intent, and the
   intent is optional when there are seed terms. Saving checks the topic
   against the same model `load_config` uses, writes your current topics plus
   this one to `configs/topics.local.yaml` (ignored by git; the shipped
   topics files are never rewritten), selects it, and adds
   `--set experiment.topics_file=configs/topics.local.yaml` to the run.

   ![Saved and selected](images/topic-intent-saved.png)

The intent is saved on the topic, and the planner reads it as the brief on
every run (`prompts/planner.md`). It is part of the config digest only when
set, so existing topics keep their runs resumable. From a terminal:

```bash
pheasant-lab draft-topic --config configs/experiment.yaml \
  --intent "Why do some tardigrade species survive extreme radiation while others do not?"
```

A topic saved from an intent alone ran end to end on the offline demo:

![A run of a topic saved from an intent alone](images/live-intent-run.png)

## Live

A run as it happens, from its append-only trace: the phase stepper, the swarm
as a tree, a **constellation** and **swimlanes**, the raw event stream,
budget, facet coverage and a custody funnel.

- **Constellation.** Research branches with their sources in orbit, coloured
  by custody, travelling to a Pheasant node drawn as landing → index queue →
  indexed.
- **Swimlanes.** One lane per agent, plus the region and the indexer.

On a role-split Pheasant a sync is *queued*, and until an indexer claims it
the documents are accepted but not searchable. That interval is its own state:
a hatched "awaiting claim" bar, a notice and an amber funnel column.

![Pre-claim: the sync is queued and no indexer has claimed it](images/live-preclaim.png)

![Claimed and indexing](images/live-claimed.png)

**Zoom, pan and fit.** Every visual has **+**, **−** and **Fit** in its
corner. On the constellation, scroll to zoom; on the timelines, Ctrl + scroll
(⌘ on a Mac), so a plain scroll still scrolls the lanes. Drag to pan. A drag
never selects the node it ends on.

![The constellation, zoomed and panned](images/live-constellation-zoom.png)

![The swimlanes, zoomed to three seconds of the run](images/live-swimlanes-zoom.png)

The replay scrubber folds the same trace at any earlier sequence number.

![Replaying a run, in the dark theme](images/live-replay-dark.png)

## Reports and agent traces

**Reports** renders the run's own Markdown. **Agent traces** is every actor's
whole record: the orchestration, each research branch and each arm.

- A waterfall of the actor's span tree on the run's clock (zoomable), with
  every event inside the span that recorded it.
- An arm's answer span carries the question, the answer, its claims, its
  queries and what it read.

![An arm's trace](images/traces-arm.png)

Each Pheasant call shows its request and response exactly as the region
answered. JSON inside a text block is decoded for reading, with a toggle back
to the record.

![An answer and its MCP call](images/traces-answer-mcp.png)

![Request and response, side by side](images/traces-mcp-pair.png)

A research branch carries the claims it extracted.

![A research branch's trace and its claims](images/traces-researcher.png)

Events whose own span was never exported are placed by time and question, and
labelled `placed by time` / `placed by question`. Prompts and completions are
not recorded, only their digests, tokens and spend, and the page says so.

## Runs, and runs that outlive the console

**Launch run** starts `pheasant-lab run` detached. It is the supervised
pipeline, and it resumes any stage that crashes. Closing or restarting the
console does not stop it; a restarted console re-attaches. A run interrupted
any other way (a machine restart, a stage that crashed every attempt) is
marked **interrupted** and offers **Resume**, which continues from the run's
checkpoint. See [durability](durability.md).

![An interrupted run, with Resume](images/runs-interrupted.png)
