# AI Learning System - Multi-Project Configuration

## 🎯 Overview

The AI Learning System works **automatically for ALL projects** managed by ShadowOps.

Every project that has:
- ✅ `patch_notes.use_ai: true`
- ✅ `external_notifications` configured

Will automatically benefit from:
- 🧪 A/B Testing of prompts
- 👍 Feedback Collection (Discord reactions)
- 📊 Quality Scoring
- 🎯 Auto-Tuning
- 🧠 Shared Learning (all projects contribute to the same training pool)

---

## 📋 Configuration Examples

### Global AI Learning (Default)

```yaml
# config/config.yaml

# AI Learning is AUTOMATICALLY enabled for all projects
# No special configuration needed!

projects:
  guildscout:
    enabled: true
    path: /home/cmdshadow/GuildScout
    patch_notes:
      language: en
      use_ai: true  # ← Enables AI Learning automatically!
    external_notifications:
      - guild_id: 1390695394777890897
        channel_id: 1442887630034440426
        enabled: true
        notify_on:
          git_push: true

  sicherheitsdiensttool:
    enabled: true
    path: /home/cmdshadow/project/backend
    patch_notes:
      language: de
      use_ai: true  # ← Also benefits from AI Learning!
    external_notifications:
      - guild_id: 1234567890
        channel_id: 9876543210
        enabled: true
        notify_on:
          git_push: true

  # Add more projects - they all share the same AI Learning system!
```

---

## 🔄 How It Works Across Projects

### 1. Shared Learning Pool

All projects contribute to the **same training data**:

```
~/.shadowops/patch_notes_training/
├── patch_notes_training.jsonl     # Examples from ALL projects
├── good_examples.json              # Top 10 from ANY project
└── prompt_test_results.jsonl      # A/B tests from ALL projects
```

**Benefits:**
- GuildScout learns from SicherheitsdienstTool's good patch notes
- SicherheitsdienstTool benefits from GuildScout's patterns
- Faster improvement (more data)

### 2. Project-Specific vs Global Variants

**A/B Testing Strategy:**
- Same 3 default variants used for all projects
- Performance tracked per-project AND globally
- Best variant selection is weighted by **all projects' data**

**Why This Works:**
- Good patch notes structure is universal
- Project-specific details come from CHANGELOG, not prompt
- More data = better decisions

### 3. Feedback Collection

**Automatic for ALL projects:**
```
Git Push (any project)
      ↓
Patch Notes generated
      ↓
Posted to external Discord
      ↓
Reaction buttons added (👍 👎 ❤️ 🔥)
      ↓
Users react
      ↓
Feedback recorded with project name
```

---

## 🎛️ Per-Project Configuration (Optional)

### Disable AI Learning for Specific Project

If you want to disable AI Learning for a specific project:

```yaml
projects:
  legacy-project:
    enabled: true
    path: /home/cmdshadow/legacy
    patch_notes:
      language: en
      use_ai: false  # ← Disabled, uses categorized fallback
```

### Different Language Per Project

```yaml
projects:
  german-project:
    patch_notes:
      language: de  # German patch notes
      use_ai: true

  english-project:
    patch_notes:
      language: en  # English patch notes
      use_ai: true

# AI Learning works with BOTH languages!
```

### Project-Specific Minimum Quality

While not directly configurable, the system:
- Tracks quality scores per project
- Auto-tuning can be triggered per-project
- Admin commands support project filters

---

## 📊 Multi-Project Statistics

### View Stats for All Projects

```
/ai-stats
```

Shows:
- Total training examples (from all projects)
- Good examples (top performers from any project)
- Average quality score (across all projects)
- Projects list

### View Stats for Specific Project

```
/ai-tune guildscout
```

Shows improvements specific to GuildScout

### Fine-Tuning with Multi-Project Data

```
# Export data from ALL projects
/ai-export-finetune

# Or specific project only
/ai-export-finetune guildscout 85
```

---

## 🎯 Best Practices

### 1. Keep CHANGELOG.md Updated for ALL Projects

The AI learns best when CHANGELOG.md is detailed:

```markdown
## Version 2.3.0 - Major Update

### New Features
- Feature 1: Detailed description
  - Sub-point 1
  - Sub-point 2

### Bug Fixes
- Fix 1: What was broken and how it's fixed
```

### 2. Consistent Versioning

Use semantic versioning across projects:
- `v2.3.0` format in commit messages
- Or `Version 2.3.0` in CHANGELOG

This helps the system detect versions automatically.

### 3. Monitor Quality Scores

Check logs after each push:
```bash
journalctl --user -u shadowops-bot.service -f | grep "Quality Score"
```

Look for:
```
📊 Patch Notes Quality Score: 87.5/100
🌟 High-quality patch notes! Saved as training example.
```

### 4. Encourage User Feedback

Tell users to react to patch notes:
- 👍 = Good
- ❤️ = Love it
- 🔥 = Amazing
- 👎 = Needs improvement

More feedback = better AI!

---

## 🔧 Troubleshooting

### Issue: AI Learning not activating for a project

**Check:**
1. Is `patch_notes.use_ai: true`?
2. Does project have `external_notifications` configured?
3. Are `external_notifications.notify_on.git_push: true`?

**Logs to check:**
```bash
journalctl --user -u shadowops-bot.service -f | grep -i "feedback\|a/b test\|quality"
```

Look for:
```
🧪 A/B Test: Using variant 'Detailed Grouping' (ID: detailed_v1)
📊 Patch Notes Quality Score: 85.0/100
👍 Feedback collection activated for guildscout v2.3.0
```

### Issue: No reaction buttons on patch notes

**Possible causes:**
1. Bot lacks `ADD_REACTIONS` permission in target channel
2. `feedback_collector` not initialized (check startup logs)
3. Version not detected from commits

**Fix:**
- Ensure bot has proper permissions
- Add version to commit message: `feat: Version 2.3.0 - New features`

### Issue: Different projects need different prompts

**Solution:**
- Use A/B testing - the system automatically finds what works best per project
- Or create project-specific variants:
  ```python
  # Via admin command (future feature)
  /ai-add-variant "GuildScout Focused" "Emphasizes guild management features"
  ```

---

## 📈 Performance by Project

### Viewing Project-Specific Performance

The system tracks performance per project:

```
# In logs
📊 Patch Notes Quality Score: 87.5/100 (guildscout)
🧪 A/B Test result recorded for variant detailed_v1 (guildscout)

# Via command
/ai-tune guildscout  # Shows suggestions for GuildScout

# In training data
~/.shadowops/patch_notes_training/patch_notes_training.jsonl
# Each line has "project": "guildscout" field
```

### Cross-Project Learning

**Example Scenario:**

1. GuildScout v2.3.0 gets quality score 95/100
2. System saves as training example
3. Next SicherheitsdienstTool push uses this example in prompt
4. SicherheitsdienstTool benefits from GuildScout's good structure!

**Result:** All projects improve together!

---

## 🚀 Adding a New Project

To add a new project to AI Learning:

1. **Add to config.yaml:**
   ```yaml
   projects:
     new-project:
       enabled: true
       path: /path/to/project
       patch_notes:
         language: en
         use_ai: true  # ← That's it!
       external_notifications:
         - guild_id: YOUR_GUILD_ID
           channel_id: YOUR_CHANNEL_ID
           enabled: true
           notify_on:
             git_push: true
   ```

2. **Push a change:**
   - Add version to commit: `git commit -m "feat: Version 1.0.0 - Initial release"`
   - Push to main

3. **Watch the magic:**
   - AI Learning activates automatically
   - Reaction buttons added
   - Quality scored
   - A/B test recorded

**No additional setup needed!**

---

## 📚 Summary

### For ALL Projects Automatically:
✅ A/B Testing (3 variants, weighted selection)
✅ Feedback Collection (Discord reactions)
✅ Quality Scoring (0-100 scale)
✅ Training Data Collection
✅ Auto-Tuning (when conditions met)
✅ Shared Learning Pool

### Configuration Required:
- `patch_notes.use_ai: true`
- `external_notifications` configured with `git_push: true`

### Optional Per-Project:
- `language` (de/en)
- Disable with `use_ai: false`

### Admin Commands (work with ALL projects):
- `/ai-stats` - Global statistics
- `/ai-variants` - All prompt variants
- `/ai-tune [project]` - Tune for specific project (optional filter)
- `/ai-export-finetune [project]` - Export for specific or all projects

---

**Das System funktioniert automatisch für alle Projekte - einfach `use_ai: true` setzen und fertig!** 🎉
