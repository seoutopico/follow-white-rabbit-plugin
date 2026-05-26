---
name: topic-template
description: Reference for writing a good topic brief in follow-white-rabbit. Use when the user is adding or editing a topic and needs guidance on what makes a useful Scope, Skip, and Writing Style section. Reading this helps you ask the right questions and produce a brief the research worker can actually follow.
---

# How to write a good topic brief

Every topic in `follow-white-rabbit` has an editorial brief at `.claude/agents/topics/<id>.md`. The `research-worker` reads it before every research cycle. A good brief makes the difference between vague generic entries and sharp on-topic ones.

The template has three required sections (Scope, Skip, Writing Style) and one optional section (Research Strategy).

## Scope

What this topic covers. Be specific about sub-areas of interest. Lists of bullet points work better than prose.

**Bad** (vague):
> Latest news in AI.

**Good** (specific):
> - Releases and changelog entries from Claude Code, Cursor, Aider, Codex
> - New MCP servers from the official registry or with >100 stars on GitHub
> - Open-source agent frameworks with technical documentation
> - Patterns for multi-agent orchestration, with code examples
> - Bugs or limitations with documented workarounds

The more concrete, the easier it is for the worker to know what counts as an entry.

## Skip

What to exclude. This is often more important than Scope — it stops the worker from drifting into noise.

**Examples** (adapt to the topic):
> - Marketing announcements without technical substance
> - Viral tweets without code or demos
> - Generic opinions ("AI is changing everything")
> - 101-level tutorials (how to install, first prompt)
> - Funding rounds, hiring news, corporate announcements

If you find generic or off-topic entries in your feed, the fix is usually a better Skip section.

## Research Strategy (optional)

Special instructions for HOW to research this topic. Useful when there are specific sources, query patterns, or verification rules.

**Examples**:
> - Check the official changelog first: `https://docs.example.com/changelog`
> - Cross-reference any claim with at least 2 sources
> - For papers, use WebFetch to read the abstract and intro before writing
> - Searches can be in any language; final entries always in `<language>`

## Writing Style

This is what makes one topic read differently from another. Describe the **tone**, **structure**, and **depth** you want.

State a target word count explicitly:

> **Target: 600-800 words per entry.**

Then describe the voice:

> - Write like a senior colleague explaining something useful they discovered this week
> - Structure: problem context → what changed → how it applies → concrete example → trade-offs / what to watch
> - Be concrete: commands, file paths, real snippets — not generic descriptions
> - Assume the reader is intermediate, no need to re-explain basics
> - Close with: open questions, what to try next, or what to follow in the next release

For a different topic, you might want:

> - Casual, accessible tone for a non-technical reader
> - Short paragraphs (2-3 sentences max)
> - One key insight per entry, no laundry lists
> - Always end with a concrete recommendation

## What the worker does with the brief

On every cycle, the `research-worker` reads the brief before doing anything else and uses it as its editorial line. So:

- A vague brief produces vague entries.
- A brief without a Skip section produces noisy entries.
- A brief without a Writing Style section produces entries in a generic AI tone.

Spend 10 minutes on a topic brief and you save hours of unsatisfying output over the following weeks.

## File format

See `templates/topic-brief.template.md` in the plugin for the exact structure to fill in. The filename must match the topic `id` from `config.yaml` — for topic id `ai-research`, the brief lives at `.claude/agents/topics/ai-research.md`.
