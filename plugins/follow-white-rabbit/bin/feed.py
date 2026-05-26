#!/usr/bin/env python3
"""RSS feed helper for follow-white-rabbit. Manages RSS 2.0 XML files and dedup state."""

import argparse
import contextlib
import html
import json
import os
import re
import sys
import uuid

if os.name == "nt":
    import msvcrt
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
else:
    import fcntl
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET


def fetch_og_image(url, timeout=10):
    """Try to extract og:image or twitter:image from a URL. Returns image URL or None."""
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; follow-white-rabbit/1.0)",
            "Accept": "text/html",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # Only read first 50KB to find meta tags quickly
            head_bytes = resp.read(51200)
            try:
                head_html = head_bytes.decode("utf-8", errors="ignore")
            except Exception:
                head_html = head_bytes.decode("latin-1", errors="ignore")

        # Try og:image first, then twitter:image
        for pattern in [
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
        ]:
            match = re.search(pattern, head_html, re.IGNORECASE)
            if match:
                img_url = match.group(1).strip()
                # Basic validation: must look like a URL
                parsed = urlparse(img_url)
                if parsed.scheme in ("http", "https") and parsed.netloc:
                    return img_url
        return None
    except Exception as e:
        print(f"  ⚠️  Auto-image fetch failed for {url}: {e}", file=sys.stderr)
        return None


def load_config(config_path=None):
    """Load a config YAML file and return settings + feeds."""
    import yaml

    if config_path is None:
        config_path = Path(__file__).parent / "config.yaml"
    else:
        config_path = Path(config_path)
        if not config_path.is_absolute():
            config_path = Path(__file__).parent / config_path
    if not config_path.exists():
        print(f"Error: {config_path} not found.", file=sys.stderr)
        sys.exit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_topic(config, topic_id):
    """Return topic definition by ID, or None."""
    for t in config.get("topics", []):
        if t["id"] == topic_id:
            return t
    return None


def _days_ago(date_str):
    """Return how many days ago a YYYY-MM-DD date string is."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).days
    except (ValueError, TypeError):
        return 999


def get_feeds_for_topic(config, topic_id):
    """Return list of feed defs that subscribe to a topic."""
    return [f for f in config.get("feeds", []) if topic_id in f.get("topics", [])]


def get_all_feed_names(config):
    """Return list of all combined_feed names from config."""
    return [f["combined_feed"] for f in config.get("feeds", [])]


def get_per_topic_feed_name(combined_feed, topic_id):
    """Return the per-topic feed slug: '{combined_feed}-{topic_id}'."""
    return f"{combined_feed}-{topic_id}"


def get_all_xml_names(config):
    """Return all feed slugs including per-topic splits."""
    names = []
    for feed_def in config.get("feeds", []):
        names.append(feed_def["combined_feed"])
        if feed_def.get("split_by_topic"):
            for topic_id in feed_def.get("topics", []):
                names.append(get_per_topic_feed_name(feed_def["combined_feed"], topic_id))
    return names


def get_dirs(config=None):
    """Return (feeds_dir, state_dir) from config, creating if needed."""
    if config is None:
        config = load_config()
    base = Path(__file__).parent
    feeds_dir = base / config.get("settings", {}).get("feeds_dir", "./feeds")
    state_dir = base / config.get("settings", {}).get("state_dir", "./.state")
    feeds_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    return feeds_dir, state_dir


def rfc822(dt=None):
    """Format datetime as RFC 822 for RSS."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    return format_datetime(dt)


def make_guid(feed_id, title, date_str):
    """Generate a deterministic GUID from feed_id + title + date."""
    raw = f"{feed_id}:{title}:{date_str}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))


def split_csv(value):
    """Split a comma-separated string into a list, stripping whitespace."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@contextlib.contextmanager
def lock_xml(xml_path):
    """Acquire exclusive lock on an XML feed file for read-modify-write.

    Cross-platform: uses fcntl.flock on Unix and msvcrt.locking on Windows.
    """
    lock_path = str(xml_path) + ".lock"
    fd = open(lock_path, "w")
    try:
        if os.name == "nt":
            while True:
                try:
                    msvcrt.locking(fd.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    import time
                    time.sleep(0.1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "nt":
            try:
                fd.seek(0)
                msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def write_xml(tree, path):
    """Write an ElementTree to an XML file with consistent formatting."""
    ET.indent(tree, space="  ")
    tree.write(path, encoding="unicode", xml_declaration=True)


def save_state(state, state_path):
    """Write state dict to a JSON file with consistent formatting."""
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def feed_path(feeds_dir, combined_feed):
    """Return the path to the combined feed XML."""
    return feeds_dir / f"{combined_feed}.xml"


def init_feed(name, description, feeds_dir, combined_feed, base_url=None, websub_hub=None):
    """Create the combined RSS feed XML file. No-op if it already exists."""
    path = feed_path(feeds_dir, combined_feed)
    if path.exists():
        print(f"Feed already exists: {path}")
        return

    link = f"{base_url.rstrip('/')}/{combined_feed}.xml" if base_url else f"https://example.com/{combined_feed}.xml"

    ATOM_NS = "http://www.w3.org/2005/Atom"
    ET.register_namespace("atom", ATOM_NS)

    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = name
    ET.SubElement(channel, "description").text = description
    ET.SubElement(channel, "link").text = link
    ET.SubElement(channel, "lastBuildDate").text = rfc822()
    ET.SubElement(channel, "generator").text = "follow-white-rabbit"

    # WebSub hub for instant feed reader notifications
    if base_url:
        feed_url = f"{base_url.rstrip('/')}/{combined_feed}.xml"
        if websub_hub:
            hub = ET.SubElement(channel, f"{{{ATOM_NS}}}link")
            hub.set("rel", "hub")
            hub.set("href", websub_hub)
        self_link = ET.SubElement(channel, f"{{{ATOM_NS}}}link")
        self_link.set("rel", "self")
        self_link.set("href", feed_url)
        self_link.set("type", "application/rss+xml")

    tree = ET.ElementTree(rss)
    write_xml(tree, path)
    print(f"Initialized feed: {path}")


def add_entry(feed_id, title, content_html, sources, feeds_dir, state_dir, target_feeds, run_id=None, image_url=None, emoji=None):
    """Add an entry to subscriber feed XMLs and update per-topic state.

    target_feeds: list of combined_feed name strings (e.g., ["daily-briefings", "rachel-briefings"])
    """
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    guid = make_guid(feed_id, title, date_str)

    # Prepend topic emoji to title if available
    if emoji:
        title = f"{emoji} {title}"

    # Normalize sources to list once
    source_list = sources if isinstance(sources, list) else split_csv(sources or "")

    # Auto-extract og:image from first source if no image provided (outside lock — network I/O)
    if not image_url and source_list:
        print(f"  📷 No --image provided, auto-extracting from {source_list[0]}...")
        image_url = fetch_og_image(source_list[0])
        if image_url:
            print(f"  ✅ Found og:image: {image_url[:80]}...")
        else:
            print(f"  ⚠️  No og:image found. Entry will lack thumbnail.")

    # Build content with sources
    body = content_html
    if source_list:
        body += "\n<hr/>\n<p><strong>Sources:</strong></p>\n<ul>\n"
        for url in source_list:
            escaped = html.escape(url)
            body += f'  <li><a href="{escaped}">{escaped}</a></li>\n'
        body += "</ul>"

    # Write to each subscriber feed XML
    wrote_any = False
    for combined_feed in target_feeds:
        path = feed_path(feeds_dir, combined_feed)
        if not path.exists():
            print(f"Warning: {path} not found, skipping. Run init first.", file=sys.stderr)
            continue

        with lock_xml(path):
            tree = ET.parse(path)
            root = tree.getroot()
            channel = root.find("channel")
            if channel is None:
                print(f"Error: Invalid feed XML for {combined_feed}.", file=sys.stderr)
                continue

            # Dedup: skip if guid already exists in this XML
            existing_guids = {item.find("guid").text for item in channel.findall("item") if item.find("guid") is not None}
            if guid in existing_guids:
                print(f"Skipped duplicate in {combined_feed}: {title}")
                continue

            item = ET.SubElement(channel, "item")
            ET.SubElement(item, "title").text = title
            ET.SubElement(item, "description").text = body
            ET.SubElement(item, "guid", isPermaLink="false").text = guid
            ET.SubElement(item, "pubDate").text = rfc822(now)
            ET.SubElement(item, "category").text = feed_id
            if source_list:
                ET.SubElement(item, "link").text = source_list[0]
            if image_url:
                ET.SubElement(item, "enclosure", url=image_url, type="image/jpeg", length="0")

            # Update lastBuildDate
            last_build = channel.find("lastBuildDate")
            if last_build is not None:
                last_build.text = rfc822(now)

            write_xml(tree, path)
            wrote_any = True
            print(f"  Wrote to {combined_feed}.xml")

    # Update state once (outside lock — per-topic file, no contention)
    new_fps = extract_fingerprints(title, content_html)
    update_state(feed_id, state_dir, {
        "guid": guid,
        "title": title,
        "date": date_str,
        "fingerprints": new_fps,
    }, run_id=run_id)

    # Snapshot to permanent archive (survives prune of the RSS XML)
    save_archive_entry(
        feed_id=feed_id,
        guid=guid,
        title=title,
        body=body,
        date_str=date_str,
        pub_date=rfc822(now),
        image_url=image_url,
        sources=source_list,
        state_dir=state_dir,
    )

    # Overlap warning: check new fingerprints against recent entries
    # Regenerate fingerprints from titles (not stored fps) for consistent comparison
    recent = [e for e in load_state(feed_id, state_dir).get("entries", [])
              if _days_ago(e.get("date", "")) <= 7 and e.get("guid") != guid]
    overlaps = []
    new_fp_set = set(new_fps)
    for existing in recent:
        existing_fps = set(extract_fingerprints(existing["title"], ""))
        shared = new_fp_set & existing_fps
        if len(shared) >= 2:
            overlaps.append((existing["title"], existing.get("date", "?"), shared))
    if overlaps:
        print(f"\n  *** OVERLAP WARNING ***")
        print(f"  This entry shares key terms with recent entries:")
        for ext_title, ext_date, shared in overlaps[:3]:
            print(f"    - \"{ext_title}\" ({ext_date})")
            print(f"      Shared: {', '.join(sorted(shared))}")
        print(f"  If this is a genuine follow-up with NEW FACTS, keep it.")
        print(f"  If this re-covers the same story, roll back with: python feed.py rollback {feed_id}\n")

    # Report word count so the agent can self-correct if too short
    import re
    text_only = re.sub(r'<[^>]+>', '', content_html).strip()
    char_count = len(text_only)
    word_count = len(text_only.split())
    print(f"Added entry to {feed_id}: {title} (~{word_count} words, {char_count} chars)")


def extract_fingerprints(title, _content):
    """Extract entity-based fingerprints for dedup from title.

    Produces two kinds of fingerprints from Latin-alphabet titles:
    1. Individual words (3+ chars, excluding stopwords)
    2. Multi-word sequences (consecutive tokens) - captures names like "Cole Palmer"
    """
    fingerprints = set()

    _STOPWORDS = frozenset({
        "the", "and", "for", "with", "from", "this", "that", "but", "not",
        "are", "was", "has", "had", "have", "will", "can", "its", "also",
        "into", "than", "been", "each", "more", "some", "new", "per",
    })

    # Strip leading emoji (topic prefix) before extraction
    clean = re.sub(r'^[\U0001f000-\U0001ffff\s]+', '', title)

    # --- English tokens ---
    # Find runs of consecutive Latin-alphabet/number tokens (split by punctuation)
    # Include accented chars (é, ü, etc.) common in artist/place names
    en_runs = re.findall(r'[A-Za-z\u00C0-\u024F0-9][A-Za-z\u00C0-\u024F0-9\s\-]*[A-Za-z\u00C0-\u024F0-9]', clean)
    for run in en_runs:
        words = run.split()
        # Individual words (3+ chars, not stopwords)
        for w in words:
            low = w.lower().strip("-")
            if len(low) >= 3 and low not in _STOPWORDS:
                fingerprints.add(low)
        # Multi-word phrases (2+ words) — captures "Cole Palmer", "Galaxy S26 Ultra"
        if len(words) >= 2:
            fingerprints.add(" ".join(w.lower() for w in words))


    return sorted(fingerprints) if fingerprints else [clean.lower().strip()]


def update_state(feed_id, state_dir, entry_record, run_id=None):
    """Update .state/<feed_id>.json with a new entry record."""
    state_path = state_dir / f"{feed_id}.json"
    if state_path.exists():
        with open(state_path) as f:
            state = json.load(f)
    else:
        state = {"last_run": None, "entries": []}

    now = datetime.now(timezone.utc).isoformat()
    state["last_run"] = now
    entry_record["run_id"] = run_id or now
    # Dedup: skip if guid already present (e.g., sync_to wrote the same entry)
    existing_guids = {e.get("guid") for e in state.get("entries", [])}
    if entry_record.get("guid") in existing_guids:
        save_state(state, state_path)
        return
    state["entries"].append(entry_record)
    # Keep only last 100 entries in state for memory
    state["entries"] = state["entries"][-100:]

    save_state(state, state_path)


def save_archive_entry(feed_id, guid, title, body, date_str, pub_date, image_url, sources, state_dir):
    """Snapshot a single entry as JSON in .state/archive/<feed_id>/<date>-<short_guid>.json.

    Append-only, idempotent: if the file already exists it's overwritten with the same content.
    This is the permanent record — survives prune of the RSS XML.
    """
    archive_dir = state_dir / "archive" / feed_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    short_guid = guid.replace("-", "")[:8]
    out_path = archive_dir / f"{date_str}-{short_guid}.json"
    payload = {
        "guid": guid,
        "topic_id": feed_id,
        "title": title,
        "body": body,
        "date": date_str,
        "pubDate": pub_date,
        "image": image_url or None,
        "sources": sources or [],
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_state(feed_id, state_dir):
    """Load state for a feed. Returns dict with last_run, entries, and knowledge."""
    state_path = state_dir / f"{feed_id}.json"
    if state_path.exists():
        with open(state_path) as f:
            state = json.load(f)
    else:
        state = {"last_run": None, "entries": []}
    # Ensure knowledge key exists (backward compat with pre-Phase1 state)
    if "knowledge" not in state:
        state["knowledge"] = {
            "brief": "",
            "key_entities": [],
            "active_threads": [],
        }
    return state


def prune_feed(keep, feeds_dir, state_dir, combined_feed):
    """Remove oldest entries beyond `keep` count from combined feed."""
    path = feed_path(feeds_dir, combined_feed)
    if not path.exists():
        print(f"Combined feed not found.", file=sys.stderr)
        sys.exit(1)

    with lock_xml(path):
        tree = ET.parse(path)
        root = tree.getroot()
        channel = root.find("channel")
        if channel is None:
            print(f"Error: Invalid feed XML.", file=sys.stderr)
            sys.exit(1)
        items = channel.findall("item")

        if len(items) <= keep:
            print(f"Feed has {len(items)} entries (limit: {keep}), no pruning needed.")
            return

        # Items are in document order (newest last since we append).
        to_remove = items[:len(items) - keep]
        removed_guids = set()
        for item in to_remove:
            guid_el = item.find("guid")
            if guid_el is not None:
                removed_guids.add(guid_el.text)
            channel.remove(item)

        write_xml(tree, path)

    # Clean all state files
    if removed_guids:
        for state_file in state_dir.glob("*.json"):
            with open(state_file) as f:
                state = json.load(f)
            before = len(state.get("entries", []))
            state["entries"] = [e for e in state.get("entries", []) if e.get("guid") not in removed_guids]
            if len(state["entries"]) < before:
                save_state(state, state_file)

    print(f"Pruned {len(to_remove)} entries, kept {keep}.")


def rollback_feed(feed_id, feeds_dir, state_dir, target_feeds):
    """Remove entries from the most recent run across all subscriber feeds.

    target_feeds: list of combined_feed name strings to rollback from.
    """
    state = load_state(feed_id, state_dir)
    entries = state.get("entries", [])

    if not entries:
        print(f"No entries to roll back for {feed_id}.")
        return

    # Find the run_id of the most recent entry
    last_entry = entries[-1]
    target_run_id = last_entry.get("run_id")

    if target_run_id is None:
        # Pre-run_id entries: fall back to date-based grouping
        target_date = last_entry.get("date")
        to_remove = [e for e in entries if e.get("date") == target_date]
    else:
        to_remove = [e for e in entries if e.get("run_id") == target_run_id]

    if not to_remove:
        print(f"No entries found for rollback in {feed_id}.")
        return

    guids_to_remove = {e["guid"] for e in to_remove}
    actually_removed = set()

    # Remove from each subscriber feed XML
    for combined_feed in target_feeds:
        path = feed_path(feeds_dir, combined_feed)
        if not path.exists():
            continue
        with lock_xml(path):
            tree = ET.parse(path)
            root = tree.getroot()
            channel = root.find("channel")
            if channel is not None:
                for item in channel.findall("item"):
                    guid_el = item.find("guid")
                    if guid_el is not None and guid_el.text in guids_to_remove:
                        channel.remove(item)
                        actually_removed.add(guid_el.text)
                write_xml(tree, path)

    if not actually_removed:
        print(f"No entries found to roll back for {feed_id}.")
        return

    # Update state once
    state["entries"] = [e for e in entries if e.get("guid") not in actually_removed]
    save_state(state, state_dir / f"{feed_id}.json")

    rolled = [e for e in to_remove if e["guid"] in actually_removed]
    print(f"Rolled back {len(rolled)} entries from {feed_id}:")
    for e in rolled:
        print(f"  - {e['title']}")


def list_entries(feed_id, _feeds_dir, state_dir):
    """List existing entries for a feed (reads from state for speed)."""
    state = load_state(feed_id, state_dir)
    if not state["entries"]:
        print(f"No entries for {feed_id}.")
        if state["last_run"]:
            print(f"Last run: {state['last_run']}")
        return

    print(f"Feed: {feed_id}")
    print(f"Last run: {state['last_run']}")
    print(f"Entries ({len(state['entries'])}):")
    for entry in reversed(state["entries"]):  # newest first
        print(f"  [{entry['date']}] {entry['title']}")
        if entry.get("fingerprints"):
            print(f"           fingerprints: {entry['fingerprints'][:3]}")


def show_state(feed_id, state_dir):
    """Dump state JSON for a feed, with a covered-subjects preamble for dedup."""
    state = load_state(feed_id, state_dir)

    # Print covered-subjects preamble for last 7 days
    # Regenerate fingerprints from titles for consistent entity display
    recent = [e for e in state.get("entries", []) if _days_ago(e.get("date", "")) <= 7]
    if recent:
        print("=== RECENTLY COVERED (last 7 days) — do NOT re-cover unless you have NEW FACTS ===")
        for e in sorted(recent, key=lambda x: x.get("date", ""), reverse=True):
            fps = extract_fingerprints(e["title"], "")
            print(f"  [{e.get('date', '')}] {e['title']}")
            if fps:
                print(f"           entities: {', '.join(fps[:8])}")
        print(f"  ({len(recent)} entries in last 7 days)")
        print("===")
        print()

    print(json.dumps(state, indent=2, ensure_ascii=False))


def show_knowledge(feed_id, state_dir):
    """Dump knowledge object for a feed (for agent to read before research)."""
    state = load_state(feed_id, state_dir)
    print(json.dumps(state["knowledge"], indent=2, ensure_ascii=False))


# --- Preference learning ---

def _pref_dir(state_dir, user_id):
    """Return and create preference directory for a user."""
    d = state_dir / "preferences" / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_pref(state_dir, user_id, topic_id):
    """Load preference file for a user-topic pair."""
    path = _pref_dir(state_dir, user_id) / f"{topic_id}.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {"user_id": user_id, "topic_id": topic_id, "rounds": [], "summary": ""}


def _save_pref(state_dir, user_id, topic_id, pref):
    """Save preference file for a user-topic pair."""
    path = _pref_dir(state_dir, user_id) / f"{topic_id}.json"
    with open(path, "w") as f:
        json.dump(pref, f, indent=2, ensure_ascii=False)
    return path


def record_preference(user_id, topic_id, state_dir, liked_guids, shown_guids, notes, summary):
    """Record a feedback round and update the preference summary."""
    pref = _load_pref(state_dir, user_id, topic_id)
    pref["rounds"].append({
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "shown": shown_guids,
        "liked": liked_guids,
        "notes": notes or "",
    })
    # Keep last 20 rounds to avoid bloat
    pref["rounds"] = pref["rounds"][-20:]
    if summary:
        pref["summary"] = summary
    path = _save_pref(state_dir, user_id, topic_id, pref)
    print(f"Saved preference for {user_id}/{topic_id} ({len(liked_guids)} liked out of {len(shown_guids)} shown)")
    return path


def show_preferences(topic_id, config, state_dir):
    """Show merged preference summaries for a topic across all subscribers."""
    subscribers = get_feeds_for_topic(config, topic_id)
    if not subscribers:
        print(f"No subscribers for topic {topic_id}.")
        return
    result = {"topic_id": topic_id, "user_preferences": []}
    for feed_def in subscribers:
        user_id = feed_def["id"]
        pref = _load_pref(state_dir, user_id, topic_id)
        if pref["summary"] or pref["rounds"]:
            total_shown = sum(len(r["shown"]) for r in pref["rounds"])
            total_liked = sum(len(r["liked"]) for r in pref["rounds"])
            result["user_preferences"].append({
                "user_id": user_id,
                "summary": pref["summary"],
                "feedback_rounds": len(pref["rounds"]),
                "total_shown": total_shown,
                "total_liked": total_liked,
                "last_feedback": pref["rounds"][-1]["date"] if pref["rounds"] else None,
            })
    if not result["user_preferences"]:
        print(f"No preferences recorded for topic {topic_id}.")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))


def update_knowledge(feed_id, state_dir, brief, entities, threads_json):
    """Update the knowledge object in state after a research cycle."""
    state_path = state_dir / f"{feed_id}.json"
    state = load_state(feed_id, state_dir)

    knowledge = state["knowledge"]

    if brief is not None:
        knowledge["brief"] = brief

    if entities is not None:
        knowledge["key_entities"] = entities

    if threads_json is not None:
        knowledge["active_threads"] = json.loads(threads_json)

    state["knowledge"] = knowledge

    save_state(state, state_path)

    print(f"Updated knowledge for {feed_id}")


def show_status(config):
    """Show a dashboard of all topics and feeds."""
    feeds_dir, state_dir = get_dirs(config)
    topics = config.get("topics", [])

    if not topics:
        print("No topics configured.")
        return

    max_entries = config.get("settings", {}).get("max_entries", 30)

    # Show feed XMLs
    print("Feeds:")
    for feed_def in config.get("feeds", []):
        path = feed_path(feeds_dir, feed_def["combined_feed"])
        status = "OK" if path.exists() else "MISSING"
        print(f"  {feed_def['id']:<12} {feed_def['combined_feed']}.xml  [{status}]  topics: {', '.join(feed_def.get('topics', []))}")
    print()

    # Show topics
    print(f"{'Topic':<22} {'Last Run':<25} {'Entries':<10} {'Target':<8} {'Feeds'}")
    print("-" * 85)

    for topic in topics:
        topic_id = topic["id"]
        state = load_state(topic_id, state_dir)
        entry_count = len(state.get("entries", []))
        target = topic.get("target", "-")

        last_run = state.get("last_run")
        lr_dt = None
        delta = None
        if last_run:
            try:
                lr_dt = datetime.fromisoformat(last_run)
                delta = datetime.now(timezone.utc) - lr_dt
            except (ValueError, TypeError):
                pass

        if delta is not None:
            if delta.days > 0:
                age = f"{delta.days}d ago"
            elif delta.seconds >= 3600:
                age = f"{delta.seconds // 3600}h ago"
            else:
                age = f"{delta.seconds // 60}m ago"
            lr_display = f"{age} ({lr_dt.strftime('%Y-%m-%d')})"
        elif last_run:
            lr_display = last_run[:19]
        else:
            lr_display = "never"

        subscriber_feeds = [f["id"] for f in get_feeds_for_topic(config, topic_id)]
        feeds_str = ", ".join(subscriber_feeds)

        print(f"{topic_id:<22} {lr_display:<25} {entry_count}/{max_entries:<7} {str(target):<8} {feeds_str}")


def check_targets(config, run_id):
    """Check if today's run met entry targets. Returns list of shortfall dicts.

    Exits 0 if all targets met, 1 if any shortfalls.
    """
    _, state_dir = get_dirs(config)
    topics = config.get("topics", [])
    shortfalls = []

    for topic in topics:
        topic_id = topic["id"]
        target = topic.get("target")
        if not target:
            continue

        state = load_state(topic_id, state_dir)
        entries = state.get("entries", [])

        # Count entries from this run_id
        if run_id:
            added = sum(1 for e in entries if e.get("run_id") == run_id)
        else:
            # Count today's entries
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            added = sum(1 for e in entries if e.get("date") == today)

        if added < target:
            shortfalls.append({
                "topic_id": topic_id,
                "target": target,
                "added": added,
                "gap": target - added,
            })

    if not shortfalls:
        print("All targets met.")
        return shortfalls

    print("TARGET SHORTFALLS:")
    for s in shortfalls:
        print(f"  {s['topic_id']:<22} added {s['added']}/{s['target']}  (need {s['gap']} more)")

    # Output as JSON for orchestrator to parse
    print(f"\n__SHORTFALLS_JSON__:{json.dumps(shortfalls)}")
    sys.exit(1)


def log_run(feed_id, log_data):
    """Append a structured log entry for a research run."""
    base = Path(__file__).parent
    log_dir = base / ".logs" / feed_id
    log_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = log_dir / f"{date_str}.json"

    if log_path.exists():
        with open(log_path) as f:
            entries = json.load(f)
    else:
        entries = []

    entries.append(log_data)

    with open(log_path, "w") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)

    print(f"Logged run for {feed_id}: {log_data.get('entries_added', 0)} added, {len(log_data.get('errors', []))} errors")


def generate_opml(config, base_url):
    """Generate an OPML file listing all user feeds (with per-topic channels if split)."""
    feeds_dir, _ = get_dirs(config)

    opml = ET.Element("opml", version="2.0")
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = "RSS Research Feeds"
    ET.SubElement(head, "dateCreated").text = rfc822()

    body = ET.SubElement(opml, "body")
    for feed_def in config.get("feeds", []):
        feed_url = f"{base_url.rstrip('/')}/{feed_def['combined_feed']}.xml"

        if feed_def.get("split_by_topic"):
            # Group: folder containing combined + per-topic feeds
            group = ET.SubElement(body, "outline",
                text=feed_def["feed_name"],
                title=feed_def["feed_name"],
            )
            ET.SubElement(group, "outline",
                type="rss",
                text=f"{feed_def['feed_name']} (All)",
                title=f"{feed_def['feed_name']} (All)",
                xmlUrl=feed_url,
                htmlUrl=base_url,
            )
            for topic_id in feed_def.get("topics", []):
                topic = get_topic(config, topic_id)
                topic_name = topic.get("name", topic_id) if topic else topic_id
                slug = get_per_topic_feed_name(feed_def["combined_feed"], topic_id)
                topic_url = f"{base_url.rstrip('/')}/{slug}.xml"
                ET.SubElement(group, "outline",
                    type="rss",
                    text=topic_name,
                    title=topic_name,
                    xmlUrl=topic_url,
                    htmlUrl=base_url,
                )
        else:
            ET.SubElement(body, "outline",
                type="rss",
                text=feed_def["feed_name"],
                title=feed_def["feed_name"],
                xmlUrl=feed_url,
                htmlUrl=base_url,
            )

    tree = ET.ElementTree(opml)
    opml_path = feeds_dir / "index.opml"
    write_xml(tree, opml_path)
    print(f"Generated OPML: {opml_path}")


def generate_index_html(config, base_url):
    """Generate a simple index.html listing all user feeds."""
    feeds_dir, state_dir = get_dirs(config)
    topics = config.get("topics", [])
    feed_defs = config.get("feeds", [])

    # Count total entries across all topics
    total_entries = 0
    for topic in topics:
        state = load_state(topic["id"], state_dir)
        total_entries += len(state.get("entries", []))

    # Build per-feed subscribe links (HTML for reading, XML for RSS readers)
    feed_links = []
    for feed_def in feed_defs:
        feed_html_url = f"{base_url.rstrip('/')}/{feed_def['combined_feed']}.html"
        feed_xml_url = f"{base_url.rstrip('/')}/{feed_def['combined_feed']}.xml"
        feed_links.append(
            f'    <a href="{feed_html_url}">{html.escape(feed_def["feed_name"])}</a> '
            f'<a href="{feed_xml_url}" class="rss" title="RSS XML">RSS</a>'
        )

    # Build per-topic channel sections for split feeds (link to HTML reading pages)
    per_topic_html = ""
    for feed_def in feed_defs:
        if feed_def.get("split_by_topic"):
            channel_links = []
            for topic_id in feed_def.get("topics", []):
                topic = get_topic(config, topic_id)
                topic_name = topic.get("name", topic_id) if topic else topic_id
                slug = get_per_topic_feed_name(feed_def["combined_feed"], topic_id)
                topic_html_url = f"{base_url.rstrip('/')}/{slug}.html"
                topic_xml_url = f"{base_url.rstrip('/')}/{slug}.xml"
                channel_links.append(
                    f'<a href="{topic_html_url}">{html.escape(topic_name)}</a> '
                    f'<a href="{topic_xml_url}" class="rss" title="RSS XML">RSS</a>'
                )
            if channel_links:
                per_topic_html += f'  <div class="channels"><strong>{html.escape(feed_def["feed_name"])} channels:</strong><br/>\n'
                per_topic_html += "    " + " &middot; ".join(channel_links) + "\n"
                per_topic_html += "  </div>\n"

    topic_list = ", ".join(t.get("name", t["id"]) for t in topics)

    subscribe_links = " &middot; ".join(feed_links)

    parts = [
        '<!DOCTYPE html>',
        '<html lang="en">',
        '<head>',
        '  <meta charset="utf-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1">',
        '  <title>RSS Research Feeds</title>',
        '  <style>',
        '    body { font-family: -apple-system, system-ui, sans-serif; max-width: 700px; margin: 2rem auto; padding: 0 1rem; color: #333; }',
        '    h1 { border-bottom: 2px solid #e0e0e0; padding-bottom: 0.5rem; }',
        '    .subscribe { margin: 1.5rem 0; padding: 1rem; border: 1px solid #e0e0e0; border-radius: 6px; }',
        '    .subscribe a { color: #0066cc; text-decoration: none; font-weight: bold; }',
        '    .subscribe a:hover { text-decoration: underline; }',
        '    .channels { margin: 0.5rem 0 0; padding: 0.75rem 1rem; background: #f8f8f8; border-radius: 4px; font-size: 0.9rem; line-height: 1.6; }',
        '    .channels a { color: #0066cc; text-decoration: none; }',
        '    .channels a:hover { text-decoration: underline; }',
        '    a.rss { font-size: 0.7rem; color: #999; text-decoration: none; padding: 0 0.3rem; border: 1px solid #ddd; border-radius: 3px; vertical-align: middle; }',
        '    a.rss:hover { color: #f26522; border-color: #f26522; }',
        '    .topics { margin: 1rem 0; color: #666; font-size: 0.9rem; }',
        '    .meta { color: #999; font-size: 0.85rem; margin-top: 0.5rem; }',
        '    footer { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #e0e0e0; color: #999; font-size: 0.85rem; }',
        '  </style>',
        '</head>',
        '<body>',
        '  <h1>RSS Research Feeds</h1>',
        '  <p>Deep research briefings delivered as RSS feeds.</p>',
        '  <div class="subscribe">',
        f'    {subscribe_links}',
        f'    &middot; <a href="{base_url.rstrip("/")}/index.opml">Import (OPML)</a>',
        f'    &middot; <a href="{base_url.rstrip("/")}/archive.html"><strong>Archivo histórico</strong></a>',
        '  </div>',
    ]
    if per_topic_html:
        parts.append(per_topic_html.rstrip())
    parts.extend([
        f'  <div class="topics"><strong>Topics:</strong> {html.escape(topic_list)}</div>',
        f'  <div class="meta">{total_entries} entries &middot; {datetime.now(timezone.utc).strftime("%Y-%m-%d")}</div>',
        f'  <footer>Generated by <a href="https://ainalluna.com/">ainalluna.com</a></footer>',
        '</body>',
        '</html>',
    ])

    index_path = feeds_dir / "index.html"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    print(f"Generated index: {index_path}")


PAGE_CSS = """
  body { font-family: -apple-system, system-ui, sans-serif; max-width: 760px; margin: 2rem auto; padding: 0 1rem; color: #222; line-height: 1.6; }
  header { border-bottom: 2px solid #e0e0e0; padding-bottom: 0.5rem; margin-bottom: 1.5rem; }
  header h1 { margin: 0 0 0.25rem; }
  header .nav { font-size: 0.85rem; color: #666; }
  header .nav a { color: #0066cc; text-decoration: none; }
  header .nav a:hover { text-decoration: underline; }
  article { padding: 1.5rem 0; border-bottom: 1px solid #eee; }
  article:last-child { border-bottom: none; }
  article h2 { margin: 0 0 0.25rem; font-size: 1.3rem; line-height: 1.3; }
  article .date { color: #999; font-size: 0.85rem; margin-bottom: 0.75rem; }
  article .body img, article .body figure img { max-width: 100%; height: auto; border-radius: 4px; margin: 0.5rem 0; }
  article .body figure { margin: 1rem 0; }
  article .body figure figcaption { font-size: 0.85rem; color: #666; text-align: center; margin-top: 0.25rem; }
  article .body a { color: #0066cc; }
  article .body hr { border: 0; border-top: 1px solid #eee; margin: 1rem 0; }
  article .body ul { padding-left: 1.2rem; }
  article .thumb { float: right; max-width: 220px; max-height: 160px; margin: 0 0 0.75rem 1rem; border-radius: 4px; }
  footer { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #e0e0e0; color: #999; font-size: 0.85rem; }
"""


def _parse_feed_items(xml_path):
    """Parse RSS XML and return (channel_title, channel_description, [items])."""
    if not xml_path.exists():
        return None, None, []
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError:
        return None, None, []
    channel = tree.getroot().find("channel")
    if channel is None:
        return None, None, []
    ch_title = (channel.findtext("title") or "").strip()
    ch_desc = (channel.findtext("description") or "").strip()
    items = []
    for item in channel.findall("item"):
        pub = item.findtext("pubDate") or ""
        # Sort key: parse pubDate, fallback to empty string for sort stability
        try:
            from email.utils import parsedate_to_datetime
            sort_dt = parsedate_to_datetime(pub) if pub else datetime.min.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            sort_dt = datetime.min.replace(tzinfo=timezone.utc)
        enclosure = item.find("enclosure")
        img_url = enclosure.get("url") if enclosure is not None else None
        items.append({
            "title": (item.findtext("title") or "").strip(),
            "description": item.findtext("description") or "",
            "link": (item.findtext("link") or "").strip(),
            "pubDate": pub,
            "date_short": sort_dt.strftime("%Y-%m-%d") if sort_dt > datetime.min.replace(tzinfo=timezone.utc) else "",
            "image": img_url,
            "sort_dt": sort_dt,
        })
    items.sort(key=lambda x: x["sort_dt"], reverse=True)  # newest first
    return ch_title, ch_desc, items


def _render_page(title, description, items, base_url, xml_filename):
    """Render a single HTML page for one feed (combined or per-topic)."""
    rss_url = f"{base_url.rstrip('/')}/{xml_filename}"
    parts = [
        '<!DOCTYPE html>',
        '<html lang="es">',
        '<head>',
        '  <meta charset="utf-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1">',
        f'  <title>{html.escape(title)}</title>',
        f'  <link rel="alternate" type="application/rss+xml" title="{html.escape(title)} RSS" href="{rss_url}">',
        '  <style>' + PAGE_CSS + '  </style>',
        '</head>',
        '<body>',
        '  <header>',
        f'    <h1>{html.escape(title)}</h1>',
        f'    <div class="nav"><a href="./">&larr; Volver al inicio</a> &middot; <a href="{rss_url}">Suscribir RSS</a> &middot; {len(items)} entradas</div>',
    ]
    if description:
        parts.append(f'    <p style="margin:0.5rem 0 0;color:#666;">{html.escape(description)}</p>')
    parts.append('  </header>')

    if not items:
        parts.append('  <p style="color:#999;">Todavía no hay entradas en este feed.</p>')
    else:
        for it in items:
            parts.append('  <article>')
            parts.append(f'    <h2>{html.escape(it["title"])}</h2>')
            if it["date_short"]:
                parts.append(f'    <div class="date">{it["date_short"]}</div>')
            if it["image"]:
                parts.append(f'    <img class="thumb" src="{html.escape(it["image"])}" alt="">')
            parts.append(f'    <div class="body">{it["description"]}</div>')
            parts.append('  </article>')

    parts.extend([
        f'  <footer>Generated by <a href="https://ainalluna.com/">ainalluna.com</a> &middot; {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</footer>',
        '</body>',
        '</html>',
    ])
    return "\n".join(parts)


def generate_topic_pages(config, base_url):
    """Generate human-readable HTML pages: one per topic + one per combined feed."""
    feeds_dir, _ = get_dirs(config)
    written = 0
    for feed_def in config.get("feeds", []):
        # Combined feed page
        combined = feed_def["combined_feed"]
        xml_path = feed_path(feeds_dir, combined)
        title, desc, items = _parse_feed_items(xml_path)
        if title is not None:
            html_out = _render_page(
                title or feed_def["feed_name"],
                desc or feed_def.get("feed_description", ""),
                items, base_url, f"{combined}.xml",
            )
            out_path = feeds_dir / f"{combined}.html"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(html_out)
            print(f"Generated page: {out_path} ({len(items)} entries)")
            written += 1

        # Per-topic pages if split_by_topic
        if feed_def.get("split_by_topic"):
            for topic_id in feed_def.get("topics", []):
                slug = get_per_topic_feed_name(combined, topic_id)
                topic_xml = feed_path(feeds_dir, slug)
                t_title, t_desc, t_items = _parse_feed_items(topic_xml)
                topic = get_topic(config, topic_id)
                topic_name = (topic.get("name", topic_id) if topic else topic_id)
                if t_title is not None:
                    html_out = _render_page(
                        t_title or topic_name,
                        t_desc or "",
                        t_items, base_url, f"{slug}.xml",
                    )
                    out_path = feeds_dir / f"{slug}.html"
                    with open(out_path, "w", encoding="utf-8") as f:
                        f.write(html_out)
                    print(f"Generated page: {out_path} ({len(t_items)} entries)")
                    written += 1
    print(f"Total HTML pages generated: {written}")


def backfill_archive_from_xml(config):
    """Read all current feed XMLs and snapshot their entries to the archive.

    One-shot migration: useful to seed the archive with entries that pre-date
    the snapshot logic. Idempotent — re-running just overwrites the same JSONs.
    """
    feeds_dir, state_dir = get_dirs(config)
    seeded = 0
    seen_guids = set()  # avoid double-counting entries that live in both combined + per-topic feed

    # Build map: topic_id -> list of feed XMLs that contain it (combined first, then per-topic)
    for topic in config.get("topics", []):
        topic_id = topic["id"]
        # Look for entries in all XMLs that subscribe to this topic
        for feed_def in config.get("feeds", []):
            if topic_id not in feed_def.get("topics", []):
                continue
            candidates = [feed_def["combined_feed"]]
            if feed_def.get("split_by_topic"):
                candidates.append(get_per_topic_feed_name(feed_def["combined_feed"], topic_id))

            for combined in candidates:
                xml_path = feed_path(feeds_dir, combined)
                if not xml_path.exists():
                    continue
                try:
                    tree = ET.parse(xml_path)
                except ET.ParseError:
                    continue
                channel = tree.getroot().find("channel")
                if channel is None:
                    continue
                for item in channel.findall("item"):
                    # Only archive entries that belong to this topic
                    cat = item.findtext("category")
                    if cat != topic_id:
                        continue
                    guid = item.findtext("guid") or ""
                    if not guid or guid in seen_guids:
                        continue
                    seen_guids.add(guid)
                    title = (item.findtext("title") or "").strip()
                    body = item.findtext("description") or ""
                    pub_date = item.findtext("pubDate") or ""
                    link = (item.findtext("link") or "").strip()
                    enclosure = item.find("enclosure")
                    img_url = enclosure.get("url") if enclosure is not None else None
                    # Derive date from pubDate
                    try:
                        from email.utils import parsedate_to_datetime
                        dt = parsedate_to_datetime(pub_date) if pub_date else None
                    except (TypeError, ValueError):
                        dt = None
                    date_str = dt.strftime("%Y-%m-%d") if dt else datetime.now(timezone.utc).strftime("%Y-%m-%d")

                    save_archive_entry(
                        feed_id=topic_id,
                        guid=guid,
                        title=title,
                        body=body,
                        date_str=date_str,
                        pub_date=pub_date,
                        image_url=img_url,
                        sources=[link] if link else [],
                        state_dir=state_dir,
                    )
                    seeded += 1
    print(f"Backfilled {seeded} entries into archive (unique GUIDs).")


def _load_archive_entries(config):
    """Load all archived JSON entries grouped by date.

    Returns: dict[date_str -> list[entry_dict]] sorted newest-date first.
    Each entry has a 'topic_id' and a 'topic_name' added for rendering.
    """
    _, state_dir = get_dirs(config)
    archive_root = state_dir / "archive"
    by_date = {}
    if not archive_root.exists():
        return by_date

    # Build topic_id -> topic_name map from config
    topic_names = {t["id"]: t.get("name", t["id"]) for t in config.get("topics", [])}

    for topic_dir in archive_root.iterdir():
        if not topic_dir.is_dir():
            continue
        topic_id = topic_dir.name
        topic_name = topic_names.get(topic_id, topic_id)
        for json_file in topic_dir.glob("*.json"):
            try:
                with open(json_file, encoding="utf-8") as f:
                    entry = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            entry["topic_id"] = topic_id
            entry["topic_name"] = topic_name
            date_str = entry.get("date") or "unknown"
            by_date.setdefault(date_str, []).append(entry)
    return by_date


SPANISH_MONTHS = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
}


def _format_date_es(date_str):
    """Format YYYY-MM-DD as '26 de mayo de 2026'."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return date_str
    return f"{dt.day} de {SPANISH_MONTHS[dt.month]} de {dt.year}"


def generate_archive_pages(config, base_url):
    """Generate one HTML page per archived date + a chronological index."""
    feeds_dir, _ = get_dirs(config)
    by_date = _load_archive_entries(config)

    if not by_date:
        print("Archive is empty — nothing to render. Run backfill-archive first.")
        return

    # Sort dates newest first
    sorted_dates = sorted(by_date.keys(), reverse=True)
    total_entries = sum(len(v) for v in by_date.values())

    # ---- Index page ----
    parts = [
        '<!DOCTYPE html>',
        '<html lang="es">',
        '<head>',
        '  <meta charset="utf-8">',
        '  <meta name="viewport" content="width=device-width, initial-scale=1">',
        '  <title>Archivo histórico — Follow the White Rabbit</title>',
        '  <style>' + PAGE_CSS + '  </style>',
        '</head>',
        '<body>',
        '  <header>',
        '    <h1>Archivo histórico</h1>',
        f'    <div class="nav"><a href="./">&larr; Volver al inicio</a> &middot; {total_entries} entradas en {len(sorted_dates)} días</div>',
        '    <p style="margin:0.5rem 0 0;color:#666;">Histórico completo de entradas por fecha de publicación. Cada día reúne todas las entradas de los 5 topics.</p>',
        '  </header>',
        '  <ul style="list-style:none;padding:0;">',
    ]
    for date_str in sorted_dates:
        entries = by_date[date_str]
        topics_in_day = sorted({e["topic_name"] for e in entries})
        topics_str = ", ".join(topics_in_day)
        parts.append(
            f'    <li style="padding:0.6rem 0;border-bottom:1px solid #eee;">'
            f'<a href="archive-{date_str}.html"><strong>{html.escape(_format_date_es(date_str))}</strong></a> '
            f'<span style="color:#999;">— {len(entries)} entradas</span><br/>'
            f'<span style="font-size:0.85rem;color:#666;">{html.escape(topics_str)}</span>'
            f'</li>'
        )
    parts.extend([
        '  </ul>',
        f'  <footer>Generated by <a href="https://ainalluna.com/">ainalluna.com</a> &middot; {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</footer>',
        '</body>',
        '</html>',
    ])
    index_path = feeds_dir / "archive.html"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    print(f"Generated archive index: {index_path} ({len(sorted_dates)} days)")

    # ---- One page per date ----
    pages_written = 0
    for date_str in sorted_dates:
        entries = by_date[date_str]
        # Group by topic within the day
        by_topic = {}
        for e in entries:
            by_topic.setdefault(e["topic_name"], []).append(e)
        # Sort entries within each topic by guid (stable) — no per-entry time available
        ordered_topics = sorted(by_topic.keys())

        page_parts = [
            '<!DOCTYPE html>',
            '<html lang="es">',
            '<head>',
            '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            f'  <title>{html.escape(_format_date_es(date_str))} — Archivo</title>',
            '  <style>' + PAGE_CSS + '  </style>',
            '  <style>h3.topic { margin: 2rem 0 0.5rem; padding-bottom: 0.3rem; border-bottom: 1px solid #ddd; color: #444; font-size: 1.1rem; }</style>',
            '</head>',
            '<body>',
            '  <header>',
            f'    <h1>{html.escape(_format_date_es(date_str))}</h1>',
            f'    <div class="nav"><a href="archive.html">&larr; Archivo</a> &middot; <a href="./">Inicio</a> &middot; {len(entries)} entradas</div>',
            '  </header>',
        ]

        for topic_name in ordered_topics:
            topic_entries = by_topic[topic_name]
            page_parts.append(f'  <h3 class="topic">{html.escape(topic_name)} <span style="color:#999;font-weight:normal;">({len(topic_entries)})</span></h3>')
            for it in topic_entries:
                page_parts.append('  <article>')
                page_parts.append(f'    <h2>{html.escape(it["title"])}</h2>')
                if it.get("image"):
                    page_parts.append(f'    <img class="thumb" src="{html.escape(it["image"])}" alt="">')
                page_parts.append(f'    <div class="body">{it["body"]}</div>')
                page_parts.append('  </article>')

        page_parts.extend([
            f'  <footer>Generated by <a href="https://ainalluna.com/">ainalluna.com</a> &middot; {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}</footer>',
            '</body>',
            '</html>',
        ])
        out_path = feeds_dir / f"archive-{date_str}.html"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(page_parts))
        pages_written += 1

    print(f"Generated {pages_written} daily archive pages.")


def backfill_split(config, feeds_dir):
    """Copy entries from combined feeds into per-topic feeds based on <category>."""
    for feed_def in config.get("feeds", []):
        if not feed_def.get("split_by_topic"):
            continue
        combined = feed_def["combined_feed"]
        path = feed_path(feeds_dir, combined)
        if not path.exists():
            continue

        tree = ET.parse(path)
        channel = tree.getroot().find("channel")
        if channel is None:
            continue

        # Group items by category (topic_id)
        items_by_topic = {}
        for item in channel.findall("item"):
            cat_el = item.find("category")
            if cat_el is not None and cat_el.text:
                items_by_topic.setdefault(cat_el.text, []).append(item)

        for topic_id in feed_def.get("topics", []):
            slug = get_per_topic_feed_name(combined, topic_id)
            topic_path = feed_path(feeds_dir, slug)
            if not topic_path.exists():
                print(f"  Skipping {slug} (not initialized)", file=sys.stderr)
                continue

            items = items_by_topic.get(topic_id, [])
            if not items:
                continue

            topic_tree = ET.parse(topic_path)
            topic_channel = topic_tree.getroot().find("channel")
            if topic_channel is None:
                continue

            # Collect existing guids to avoid duplicates
            existing = {i.find("guid").text for i in topic_channel.findall("item") if i.find("guid") is not None}
            added = 0
            for item in items:
                guid_el = item.find("guid")
                if guid_el is not None and guid_el.text in existing:
                    continue
                import copy
                topic_channel.append(copy.deepcopy(item))
                added += 1

            if added > 0:
                write_xml(topic_tree, topic_path)
                print(f"  {slug}: backfilled {added} entries")
            else:
                print(f"  {slug}: already up to date")


def backfill_images(feeds_dir, combined_feed):
    """Add og:image to existing entries that lack images."""
    path = feed_path(feeds_dir, combined_feed)
    if not path.exists():
        print(f"Feed not found: {path}", file=sys.stderr)
        return

    tree = ET.parse(path)
    root = tree.getroot()
    channel = root.find("channel")
    if channel is None:
        print("Invalid feed XML.", file=sys.stderr)
        return

    items = channel.findall("item")
    updated = 0
    skipped = 0
    failed = 0

    for item in items:
        # Skip items that already have an enclosure (image)
        if item.find("enclosure") is not None:
            skipped += 1
            continue

        # Get the first source URL from the link element
        link_el = item.find("link")
        if link_el is None or not link_el.text:
            failed += 1
            continue

        title_el = item.find("title")
        title = title_el.text if title_el is not None else "untitled"

        print(f"  Fetching image for: {title[:50]}...")
        image_url = fetch_og_image(link_el.text)

        if not image_url:
            print(f"    ❌ No og:image found")
            failed += 1
            continue

        # Add enclosure element
        ET.SubElement(item, "enclosure", url=image_url, type="image/jpeg", length="0")

        updated += 1
        print(f"    ✅ Added: {image_url[:60]}...")

    if updated > 0:
        write_xml(tree, path)

    print(f"\nBackfill complete: {updated} images added, {skipped} already had images, {failed} no image found.")


def main():
    parser = argparse.ArgumentParser(description="RSS feed helper for follow-white-rabbit")
    parser.add_argument("--config", default="config.yaml", help="Path to config file (default: config.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    # init
    sub.add_parser("init", help="Initialize all feed XMLs from config")

    # add
    p_add = sub.add_parser("add", help="Add an entry to a feed")
    p_add.add_argument("feed_id")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--content", required=True, help="HTML content of the entry")
    p_add.add_argument("--sources", default="", help="Comma-separated source URLs")
    p_add.add_argument("--image", default=None, help="URL of an image to use as RSS enclosure/thumbnail")
    p_add.add_argument("--run-id", default=None, help="Run identifier for rollback grouping")

    # prune
    p_prune = sub.add_parser("prune", help="Prune old entries from combined feed")
    p_prune.add_argument("--keep", type=int, default=50)

    # list
    p_list = sub.add_parser("list", help="List entries in a feed")
    p_list.add_argument("feed_id")

    # state
    p_state = sub.add_parser("state", help="Show raw state JSON for a feed")
    p_state.add_argument("feed_id")

    # knowledge
    p_knowledge = sub.add_parser("knowledge", help="Show knowledge brief for a feed")
    p_knowledge.add_argument("feed_id")

    # learn
    p_learn = sub.add_parser("learn", help="Update knowledge after research")
    p_learn.add_argument("feed_id")
    p_learn.add_argument("--brief", default=None, help="Updated knowledge brief (2-3 paragraphs)")
    p_learn.add_argument("--entities", default=None, help="Comma-separated key entities")
    p_learn.add_argument("--threads", default=None, help="JSON array of active thread objects")

    # rollback
    p_rollback = sub.add_parser("rollback", help="Remove entries from the most recent run")
    p_rollback.add_argument("feed_id")

    # status
    sub.add_parser("status", help="Show status dashboard for all feeds")

    # log
    p_log = sub.add_parser("log", help="Record a structured run log")
    p_log.add_argument("feed_id")
    p_log.add_argument("--started", required=True, help="ISO timestamp when run started")
    p_log.add_argument("--finished", required=True, help="ISO timestamp when run finished")
    p_log.add_argument("--queries", default="", help="Comma-separated search queries used")
    p_log.add_argument("--sources-consulted", type=int, default=0)
    p_log.add_argument("--entries-added", type=int, default=0)
    p_log.add_argument("--entries-skipped", type=int, default=0)
    p_log.add_argument("--threads-updated", default="", help="Comma-separated thread names")
    p_log.add_argument("--errors", default="", help="Comma-separated error descriptions")

    # opml
    p_opml = sub.add_parser("opml", help="Generate OPML file for all feeds")
    p_opml.add_argument("--base-url", required=True, help="Base URL where feeds are hosted")

    # index-html
    p_index = sub.add_parser("index-html", help="Generate index.html for all feeds")
    p_index.add_argument("--base-url", required=True, help="Base URL where feeds are hosted")

    # render-html (human-readable pages per feed and per topic)
    p_render = sub.add_parser("render-html", help="Generate human-readable HTML pages for each feed and topic")
    p_render.add_argument("--base-url", required=True, help="Base URL where feeds are hosted")

    # render-archive (chronological archive pages: index + one per date)
    p_arch = sub.add_parser("render-archive", help="Generate archive.html index + one archive-YYYY-MM-DD.html per date")
    p_arch.add_argument("--base-url", required=True, help="Base URL where feeds are hosted")

    # backfill-archive (one-shot: read current XMLs and snapshot to archive/)
    sub.add_parser("backfill-archive", help="One-shot: snapshot all entries from current feed XMLs to .state/archive/")

    # check-targets
    p_check = sub.add_parser("check-targets", help="Check if entry targets were met for a run")
    p_check.add_argument("--run-id", default=None, help="Run ID to check (default: today's entries)")

    # backfill-images
    sub.add_parser("backfill-images", help="Add og:image to existing entries that lack images")

    # backfill-split
    sub.add_parser("backfill-split", help="Copy entries from combined feeds into per-topic feeds")

    # prefer (record feedback)
    p_prefer = sub.add_parser("prefer", help="Record user preference feedback for a topic")
    p_prefer.add_argument("user_id", help="User ID (e.g., jimmy)")
    p_prefer.add_argument("topic_id", help="Topic ID")
    p_prefer.add_argument("--liked", default="", help="Comma-separated GUIDs of liked entries")
    p_prefer.add_argument("--shown", default="", help="Comma-separated GUIDs of all shown entries")
    p_prefer.add_argument("--notes", default="", help="Free-text user notes")
    p_prefer.add_argument("--summary", default="", help="LLM-distilled preference summary")

    # preferences (read for worker)
    p_prefs = sub.add_parser("preferences", help="Show merged preferences for a topic")
    p_prefs.add_argument("topic_id", help="Topic ID")

    args = parser.parse_args()
    config = load_config(args.config)
    feeds_dir, state_dir = get_dirs(config)

    if args.command == "init":
        # Init all feed XMLs from config
        for feed_def in config.get("feeds", []):
            settings = config.get("settings", {})
            init_feed(feed_def["feed_name"], feed_def.get("feed_description", ""),
                      feeds_dir, feed_def["combined_feed"],
                      base_url=settings.get("base_url"),
                      websub_hub=settings.get("websub_hub"))
            # Init per-topic feeds if split_by_topic is set
            if feed_def.get("split_by_topic"):
                for topic_id in feed_def.get("topics", []):
                    topic = get_topic(config, topic_id)
                    topic_name = topic.get("name", topic_id) if topic else topic_id
                    per_topic_slug = get_per_topic_feed_name(feed_def["combined_feed"], topic_id)
                    init_feed(topic_name,
                              f"Per-topic feed: {topic_name}",
                              feeds_dir, per_topic_slug,
                              base_url=settings.get("base_url"),
                              websub_hub=settings.get("websub_hub"))
    elif args.command == "add":
        sources = split_csv(args.sources)
        # Auto-resolve target feeds from config
        subscriber_feeds = get_feeds_for_topic(config, args.feed_id)
        target_feeds = [f["combined_feed"] for f in subscriber_feeds]
        # Include per-topic feeds for feeds with split_by_topic
        for f in subscriber_feeds:
            if f.get("split_by_topic"):
                target_feeds.append(get_per_topic_feed_name(f["combined_feed"], args.feed_id))
        if not target_feeds:
            print(f"Warning: no feeds subscribe to {args.feed_id}", file=sys.stderr)
        topic = get_topic(config, args.feed_id)
        emoji = topic.get("emoji") if topic else None
        add_entry(args.feed_id, args.title, args.content, sources, feeds_dir, state_dir,
                  target_feeds, run_id=args.run_id, image_url=args.image, emoji=emoji)
    elif args.command == "prune":
        # Prune all feed XMLs (including per-topic splits)
        for combined_feed in get_all_xml_names(config):
            prune_feed(args.keep, feeds_dir, state_dir, combined_feed)
    elif args.command == "list":
        list_entries(args.feed_id, feeds_dir, state_dir)
    elif args.command == "state":
        show_state(args.feed_id, state_dir)
    elif args.command == "knowledge":
        show_knowledge(args.feed_id, state_dir)
    elif args.command == "learn":
        entities = split_csv(args.entities) or None
        update_knowledge(args.feed_id, state_dir, args.brief, entities, args.threads)
    elif args.command == "rollback":
        subscriber_feeds = get_feeds_for_topic(config, args.feed_id)
        target_feeds = [f["combined_feed"] for f in subscriber_feeds]
        for f in subscriber_feeds:
            if f.get("split_by_topic"):
                target_feeds.append(get_per_topic_feed_name(f["combined_feed"], args.feed_id))
        rollback_feed(args.feed_id, feeds_dir, state_dir, target_feeds)
    elif args.command == "status":
        show_status(config)
    elif args.command == "log":
        log_run(args.feed_id, {
            "feed_id": args.feed_id,
            "started": args.started,
            "finished": args.finished,
            "queries": split_csv(args.queries),
            "sources_consulted": args.sources_consulted,
            "entries_added": args.entries_added,
            "entries_skipped": args.entries_skipped,
            "threads_updated": split_csv(args.threads_updated),
            "errors": split_csv(args.errors),
        })
    elif args.command == "opml":
        generate_opml(config, args.base_url)
    elif args.command == "index-html":
        generate_index_html(config, args.base_url)
    elif args.command == "render-html":
        generate_topic_pages(config, args.base_url)
    elif args.command == "render-archive":
        generate_archive_pages(config, args.base_url)
    elif args.command == "backfill-archive":
        backfill_archive_from_xml(config)
    elif args.command == "check-targets":
        check_targets(config, args.run_id)
    elif args.command == "backfill-images":
        for combined_feed in get_all_xml_names(config):
            print(f"\nBackfilling images for {combined_feed}...")
            backfill_images(feeds_dir, combined_feed)
    elif args.command == "backfill-split":
        backfill_split(config, feeds_dir)
    elif args.command == "prefer":
        liked = split_csv(args.liked)
        shown = split_csv(args.shown)
        record_preference(args.user_id, args.topic_id, state_dir, liked, shown, args.notes, args.summary)
    elif args.command == "preferences":
        show_preferences(args.topic_id, config, state_dir)


if __name__ == "__main__":
    main()
