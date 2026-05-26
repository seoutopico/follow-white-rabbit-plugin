---
description: Trigger a full research + publish cycle right now (instead of waiting for the scheduled daily run). Use it after adding a new topic, when you want fresh entries on demand, or to verify the system end-to-end.
argument-hint: "[--dry-run] [--skip-publish]"
---

You are running an on-demand cycle of the `follow-white-rabbit` system.

# What this does

The cycle orchestrator spawns one `research-worker` per topic in parallel, then publishes the resulting feeds and HTML pages to the user's `gh-pages` branch.

# Prerequisites (verify before launching)

1. `config.yaml` exists in the current directory.
2. The topics listed in `config.yaml` all have brief files at `.claude/agents/topics/<id>.md`.
3. `base_url` is set to a real URL (not the placeholder).
4. `origin` points to the user's own repo (not this plugin's repo).

If any of those fails, stop and tell the user which one — point them to `/setup` to fix it.

# Arguments

- No args → full cycle (research + publish).
- `--dry-run` → workers report what they would write, no entries added, no publish.
- `--skip-publish` → research runs and writes entries, but does not push to gh-pages (useful to verify content before publishing).

# How to run

Detect OS via `$OS` / `uname -s`:

### Windows

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/bin/cycle.ps1" <flags>
```

### macOS / Linux

```bash
bash "${CLAUDE_PLUGIN_ROOT}/bin/cycle.sh" <flags>
```

# While it runs

Stream the script's stdout to the user. The log is also written to `.logs/research-YYYY-MM-DD.log` in the project directory.

A typical cycle:
1. Sanity checks (python, claude, git, config.yaml all present).
2. Round 1: spawn N workers in parallel (one per topic). Wait for all with a per-worker timeout.
3. Check targets: if any topic produced fewer entries than its target, spawn a retry round for those topics only.
4. Prune (max 50 entries per feed XML; the JSON archive is untouched).
5. Render HTML pages (`render-html`) + archive pages (`render-archive`) + OPML + index.
6. Publish to `gh-pages`:
   - Sanity check: refuse to publish if origin points to the plugin's own upstream.
   - Clone gh-pages (or create it from scratch if first time).
   - Copy feeds + HTML to the clone.
   - Commit and push.

# When it finishes

Report:
- How many entries were added per topic.
- Whether the publish step succeeded.
- The public URL (from `base_url` in config.yaml) where the user can read the result.
- If the first publish ever, remind them to activate GitHub Pages in repo settings.
