# Installation

## Prerequisites

| Required | What for |
|---|---|
| Claude Code (CLI or app) | Run the plugin |
| Python 3.9+ | The `feed.py` engine |
| `git` + GitHub account | Hosting the published feeds on Pages |

| Optional | What for |
|---|---|
| `gh` CLI | Lets `/setup` create your destination repo automatically (otherwise you create it manually) |
| `pwsh` (PowerShell 7) | Cleaner UTF-8 handling on Windows. Windows PowerShell 5.1 also works |

## 1. Install the plugin

Open Claude Code in any directory, then:

```
/plugin marketplace add seoutopico/follow-white-rabbit-plugin
/plugin install follow-white-rabbit@seoutopico
```

This caches the plugin at `~/.claude/plugins/follow-white-rabbit/`. Its `bin/` is added to your PATH automatically while the plugin is active.

## 2. Install Python dependency

```bash
pip install -r ~/.claude/plugins/follow-white-rabbit/bin/requirements.txt
```

(Or on Windows: `pip install -r "$env:USERPROFILE/.claude/plugins/follow-white-rabbit/bin/requirements.txt"`.)

## 3. Set up your first feed project

Pick or create a directory where you want this feed project to live, then in Claude Code:

```
/setup
```

The wizard asks for:

1. **Destination GitHub repo** — where the published feeds will live. With `gh` CLI it can create it for you; otherwise it walks you through creating it manually.
2. **Topics** — added one by one from scratch (no defaults). Each topic asks for: id, display name, depth, language, target entries per cycle, scope, what to skip, writing style.
3. **Schedule** — what time the daily run should fire. It registers the scheduled task for you (Windows Task Scheduler / cron / launchd).
4. **First cycle (optional)** — runs once now so the `gh-pages` branch is created and you have something to publish.

## 4. Activate GitHub Pages (one-time, manual)

After the first cycle creates the `gh-pages` branch, activate Pages in your destination repo:

1. Open `https://github.com/<you>/<your-repo>/settings/pages`
2. Source: **Deploy from a branch**
3. Branch: `gh-pages` → Folder: `/(root)` → Save
4. Wait 1-2 minutes
5. Open `https://<you>.github.io/<your-repo>/`

## Multiple feed projects

You can have several feed projects on the same machine — each in its own directory, each with its own `config.yaml`, each publishing to a different destination repo. Run `/setup` from each directory. The scheduled tasks are named after the feed slug so they don't collide.

## Updating the plugin

```
/plugin marketplace update
```

This pulls the latest plugin code. Your project files (`config.yaml`, topic briefs, `.state/`) are untouched.

## Uninstalling

```
/plugin uninstall follow-white-rabbit
```

The plugin is removed from `~/.claude/plugins/`. Your project files stay where they are. If you also want to stop the daily run, unregister the scheduled task:

- Windows: `Unregister-ScheduledTask -TaskName "follow-white-rabbit-<feed-slug>" -Confirm:$false`
- Linux: `crontab -e` and remove the line
- macOS: `launchctl unload ~/Library/LaunchAgents/com.follow-white-rabbit.<feed-slug>.plist`
