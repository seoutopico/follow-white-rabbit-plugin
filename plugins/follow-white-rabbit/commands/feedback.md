---
description: Walk through recent feed entries topic by topic, mark which ones you found valuable, and store the preference summary that will guide the next research runs. Use this whenever you want to refine the direction of your feeds.
argument-hint: "(no arguments needed)"
---

You are running the preference collector for the `follow-white-rabbit` system. Your job: show the user their recent entries, capture which ones resonated, and distil a short preference summary per topic. The research worker reads that summary on subsequent runs and uses it to steer angle and tone.

# How it works

1. Identify the user (skip if there's only one feed configured).
2. Loop through each topic in `config.yaml`.
3. For each topic, show the last ~10 entries and ask which ones the user liked.
4. Ask why (one quick multi-select per topic).
5. Distil a short preference summary.
6. Persist via `feed.py prefer`.

# Step 1 — Identify the user

Read `config.yaml`. Extract the `feeds` section.

- If only one feed bundle, use its `id` automatically.
- If more than one, use AskUserQuestion with `feed.id` as label and `feed.feed_name` as description.

# Step 2 — Loop through topics

For the selected user feed, get the `topics` list. For each topic:

## 2a. Fetch recent entries

```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/feed.py" state <topic_id>
```

Parse the JSON output. Take the most recent 10 entries (state is oldest-first; reverse it). Each entry has `guid`, `title`, `date`.

If fewer than 2 entries exist for this topic, skip it and move on.

## 2b. Ask for favourites

AskUserQuestion can show 4 options at a time. Loop the entries in batches of 3, with the 4th option being either "None / next batch" or "None / move on" on the last batch.

- **question**: "Which **<topic_name>** entries did you find valuable?"
- **header**: short tag (max 12 chars) — use the topic id truncated.
- **multiSelect**: true
- **options**: entry title (strip leading emoji if present) + entry date as description.

Collect picks across batches.

## 2c. Why (optional)

If the user picked at least 1 entry, one quick multi-select:

- **question**: "What made these stand out? (helps tune future entries)"
- **header**: "Why?"
- **multiSelect**: true
- **options**:
  - "Topic / angle was interesting" — "The subject itself drew me in"
  - "Good depth / analysis" — "Appreciated the thoroughness"
  - "Well written" — "The style and structure worked"
  - "Actionable / useful" — "I could act on this"

## 2d. Distil and store

After collecting picks and reasons, write a **preference summary** of 1-3 sentences capturing:
- Which sub-topics or angles the user gravitates toward.
- What style or depth they prefer.
- What seems less interesting to them (entries shown but not picked).

Read any existing summary first:
```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/feed.py" preferences <topic_id>
```

If a prior summary exists, **merge** the new signals with the old — preference learning is cumulative, not replace-on-write.

Then store:
```bash
python "${CLAUDE_PLUGIN_ROOT}/bin/feed.py" prefer <user_id> <topic_id> \
  --liked "<guid1>,<guid2>" \
  --shown "<guid1>,<guid2>,<guid3>,..." \
  --notes "<reasons selected, comma-separated>" \
  --summary "<your distilled summary>"
```

# Step 3 — Wrap up

Print:
- How many topics got feedback this run.
- Two or three key signals you learned, in one line each.
- Reminder: "These preferences will guide the next research cycle. The worker will also explore at least one angle outside your preferences each run, to keep the feed fresh."

# Notes

- Be fast and low-friction. Don't over-explain.
- If the user picks "Skip topic" or selects nothing, move on immediately — no follow-up.
- The summary is the key deliverable. Write it as guidance for an LLM researcher, not as a report for the user.
- If "Other" with custom text was picked at any point, weave it verbatim into the notes/summary.
