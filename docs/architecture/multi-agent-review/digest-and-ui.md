---
title: Daily-Digest und Discord-UI
status: active
version: v1
last_reviewed: 2026-04-15
owner: CommanderShadow9
related:
  - ../../adr/008-multi-agent-review-pipeline.md
  - ../../plans/2026-04-14-multi-agent-review-design.md
  - ../jules-workflow/README.md
---

# Daily-Digest und Discord-UI

Phase 5 liefert die sichtbaren Artefakte der Multi-Agent-Pipeline: farbkodierte
Review-Embeds je Adapter und einen taeglichen Markdown-Digest, der in `ai-learning`
gepostet wird. Der Weekly-Recap (Discord-Embed mit Ampel-Status) nutzt dieselbe
Datenbasis und erscheint Freitags.

---

## Phase 5: Daily-Digest + Discord-Embeds

### Task 5.1: Review-Embed-Formatter

**Files:**

- Create: `src/integrations/github_integration/agent_review/discord_embed.py`

Baut `discord.Embed` aus Review + PR. Farbkodiert:

- Gruen = Approved
- Gelb = Revision angefordert
- Rot = Escalated

**Commit:**

```bash
git commit -m "feat: Review-Embed-Formatter mit Farbkodierung"
```

---

### Task 5.2: Daily-Digest

**Files:**

- Create: `src/integrations/github_integration/agent_review/daily_digest.py`
- Modify: `src/bot.py` — neuer `@tasks.loop(time=time(hour=8, minute=15))` Task

Query DB fuer:

- Reviews letzte 24h (by agent_type + verdict)
- Auto-Merges + Reverts
- Queue-Status
- Offene PRs wartend auf manuellen Merge
- Trends 7 Tage

Poste als Markdown in `ai-learning`.

**Commit:**

```bash
git commit -m "feat: Daily-Digest Task (08:15 in AI-Learning)"
```
