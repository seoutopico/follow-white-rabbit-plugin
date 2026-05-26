# Changelog

All notable changes to this plugin are documented here.

## [0.1.0] — initial release

### Added
- Plugin manifest (`.claude-plugin/plugin.json`)
- Marketplace entry (`.claude-plugin/marketplace.json`)
- `research-worker` agent: investigates one topic per invocation, produces sourced HTML briefings
- `/setup` command: interactive wizard — environment check, topics from scratch, schedule, GitHub Pages instructions
- `/cycle` command: trigger a full research + publish cycle on demand
- `/feedback` command: collect per-topic preferences to tune future runs
- `feed.py` in `bin/`: RSS engine + JSON archive + human-readable HTML pages
- `cycle.ps1` / `cycle.sh` in `bin/`: cross-platform orchestrator
- `topic-template` skill: how to write a good topic brief
- Templates for `config.yaml`, topic briefs, and project `CLAUDE.md`
- Documentation: installation, scheduling, publishing
