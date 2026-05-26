# follow-white-rabbit

> A Claude Code plugin that turns your interests into daily research briefings — published as RSS feeds and readable HTML pages on your own GitHub Pages.

## What it does

You define topics in plain English. Every morning, a Claude agent researches them, writes deep briefings (sourced, deduplicated), and publishes:

- **RSS feeds** for your reader (Feedly, Inoreader, Reeder, NetNewsWire, etc.)
- **Readable HTML pages** at `https://<you>.github.io/<your-repo>/`
- **A chronological archive** by date so you can search past entries (yourself or via an LLM)

Everything runs inside your Claude Code subscription. No API keys, no external services.

## Install

```
/plugin marketplace add seoutopico/follow-white-rabbit-plugin
/plugin install follow-white-rabbit@seoutopico
```

## Set up your first feed

In any directory where you want your feed project to live:

```
/setup
```

The wizard will ask you for:

1. The GitHub repo that will host your published feeds (you can create it during setup)
2. Your topics — one by one, from scratch (no defaults)
3. The time of day you want the daily cycle to run
4. Whether you want it to register the scheduled task for you (Windows / macOS / Linux)

It will not touch anything until you confirm each step.

## What lives where

| Path | Contains |
|---|---|
| `~/.claude/plugins/follow-white-rabbit/` | Plugin code (managed by Claude Code; do not edit) |
| Your project directory | `config.yaml`, `.state/`, `feeds/`, your topic briefs |
| Your GitHub repo (`gh-pages` branch) | The published feeds and HTML pages |

## Commands

| Command | What it does |
|---|---|
| `/setup` | Interactive wizard — first-time setup or reconfigure |
| `/cycle` | Run a full research + publish cycle now (instead of waiting for the scheduled run) |
| `/feedback` | Walk through recent entries, mark favourites, tune the next runs |

## Requirements

- Python 3.9+
- `git` and a GitHub account (for hosting the published feeds)
- Optional: `gh` CLI (lets `/setup` create your destination repo automatically)
- Windows / macOS / Linux

## Docs

- [docs/installation.md](docs/installation.md) — step-by-step installation walkthrough
- [docs/scheduling.md](docs/scheduling.md) — scheduling cheatsheet (Task Scheduler / cron / launchd)
- [docs/publishing.md](docs/publishing.md) — how the publish step works and how to activate GitHub Pages

## License

MIT — see [LICENSE](LICENSE).
