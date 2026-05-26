# Publishing

The publish step at the end of `cycle.ps1` / `cycle.sh` pushes the generated feeds and HTML pages to the `gh-pages` branch of your destination repo. GitHub Pages then serves them at `base_url`.

## What gets published

After a cycle, the `gh-pages` branch contains:

| File | What it is |
|---|---|
| `index.html` | Landing page with links to each feed and to the archive |
| `index.opml` | OPML file — import this in Feedly / Inoreader to subscribe to all topics at once |
| `<feed-slug>.xml` | Combined RSS feed (all topics) |
| `<feed-slug>-<topic-id>.xml` | One per topic when `split_by_topic: true` |
| `<feed-slug>.html` | Combined human-readable page |
| `<feed-slug>-<topic-id>.html` | Per-topic human-readable page |
| `archive.html` | Chronological index of all entries by date |
| `archive-YYYY-MM-DD.html` | One page per day with every entry from that day grouped by topic |

The `archive-*.html` pages are the **permanent record** — once published, they are never pruned. Useful for searching past entries, your own or via an LLM.

## How the publish step works

1. Read `base_url` from `config.yaml`. Abort if it still contains a placeholder.
2. Read `origin` from `git remote get-url origin` in the project directory. Abort if it points to the plugin's own upstream repo (safety guard against publishing to the wrong place).
3. Clone the `gh-pages` branch of `origin` to a temp directory (or initialise it as an orphan branch if it doesn't exist yet).
4. Replace the contents with the fresh `feeds/*.{xml,html}` and `feeds/index.opml`.
5. Commit (author: `follow-white-rabbit / bot`).
6. Push.
7. If `websub_hub` is set in config, ping it for each feed URL so subscribed readers get the update instantly.

## Activate GitHub Pages (one-time, manual)

After the first publish, you must turn on Pages once in your destination repo:

1. Open `https://github.com/<you>/<your-repo>/settings/pages`
2. Source: **Deploy from a branch**
3. Branch: `gh-pages` / Folder: `/(root)` → **Save**
4. Wait 1-2 minutes
5. Visit `https://<you>.github.io/<your-repo>/`

GitHub Pages does not auto-enable just because the `gh-pages` branch exists — this step is the source of most "the page is not loading" issues on day 1.

## Custom domain (optional)

Add a `CNAME` file at the root of your project's `feeds/` directory with your domain (e.g. `feeds.example.com`). It will be copied to `gh-pages` on the next publish. Then point a DNS CNAME record at `<you>.github.io`.

## Skipping the publish step

For testing or while iterating on content:

```
/cycle --skip-publish
```

This runs research, writes entries locally, regenerates HTML, but does not touch `gh-pages`.

## What to do if publish fails

The most common failures:

- **Authentication.** `git push` to your repo fails because you don't have credentials set up. Fix: configure a Personal Access Token or SSH key.
- **Wrong origin.** The publish step refuses to run because `origin` points at the plugin's own repo. Fix: `git remote set-url origin https://github.com/<you>/<your-repo>.git`.
- **Placeholder `base_url`.** Edit `config.yaml` and set a real URL.
- **`gh-pages` exists but Pages isn't active.** The push succeeds but the site 404s. Activate Pages (above).

The full publish log is in `.logs/research-YYYY-MM-DD.log` in the project directory.
