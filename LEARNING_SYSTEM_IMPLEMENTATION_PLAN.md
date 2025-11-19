# Learning System Implementation Plan

## 🚨 PROBLEM IDENTIFIED

Die KI "schläft" - kein Context, kein Learning, kein Feedback!

### Was FEHLT:
1. **Koordinierter Plan**: Nutzt NICHT den Context Manager (kein Code-Wissen!)
2. **Phase Execution**: Übergibt KEINE `previous_attempts` (kein Learning!)
3. **Feedback-Loop**: Nach Phasen wird KEIN Feedback gespeichert
4. **Persistenz**: Gelerntes Wissen wird nicht gespeichert

### Was existiert (aber nicht genutzt wird):
- ✅ Context Manager vorhanden (lädt Code + Infra)
- ✅ `previous_attempts` Mechanismus existiert
- ✅ AI Service kann Context verarbeiten

---

## 🎯 IMPLEMENTATION PLAN

### Phase 1: Context Manager in Orchestrator integrieren

**Datei:** `src/integrations/orchestrator.py`

**Änderungen in `_build_coordination_prompt()`:**

```python
def _build_coordination_prompt(self, context: Dict) -> str:
    """Baut Prompt für koordinierte Planung"""

    prompt_parts = []

    # 1. ADD: Context Manager Integration
    if self.ai_service and self.ai_service.context_manager:
        prompt_parts.append("# INFRASTRUCTURE & PROJECT KNOWLEDGE BASE")
        prompt_parts.append("Du hast Zugriff auf detaillierte Informationen über die Server-Infrastruktur und laufende Projekte.")
        prompt_parts.append("Nutze diesen Kontext für informierte, sichere Entscheidungen.\n")

        # Get relevant context for all events
        for event in context['events']:
            relevant_context = self.ai_service.context_manager.get_relevant_context(
                event['source'],
                event.get('event_type', 'unknown')
            )
            prompt_parts.append(relevant_context)

        prompt_parts.append("\n" + "="*80 + "\n")

    # 2. Existing coordination prompt
    prompt_parts.append(f"""# Koordinierte Security Remediation

Du bist ein Security-Engineer der einen KOORDINIERTEN Gesamt-Plan erstellt.

## Wichtig:
- Analysiere ALLE {context['event_count']} Events ZUSAMMEN
- Nutze den INFRASTRUCTURE & PROJECT KNOWLEDGE BASE Kontext
- Erkenne Abhängigkeiten zwischen Projekten
- Erstelle EINE sequentielle Ausführungs-Pipeline
- Vermeide Race Conditions und Breaking Changes

## Events im Batch:
""")

    # ... rest of existing code

    return "\n".join(prompt_parts)
```

---

### Phase 2: previous_attempts in Phase Execution übergeben

**Datei:** `src/integrations/orchestrator.py`

**Änderungen in `_execute_phases()`:**

```python
# Around line 610 - where strategy is generated
if not strategy:
    # Generate strategy if not in phase
    logger.info(f"      Generating strategy for {event.source}...")

    # NEW: Build context with previous attempts for this event
    strategy_context = {
        'event': event.to_dict(),
        'previous_attempts': []  # TODO: Track attempts per event in batch
    }

    # NEW: If we have history for this event type, add it
    if hasattr(self, 'event_history'):
        event_signature = f"{event.source}_{event.event_type}"
        if event_signature in self.event_history:
            strategy_context['previous_attempts'] = self.event_history[event_signature][-3:]  # Last 3 attempts

    strategy = await self.ai_service.generate_fix_strategy(strategy_context)
```

**ADD: Event History Tracking:**

```python
class RemediationOrchestrator:
    def __init__(self, ...):
        # ... existing code ...

        # NEW: Event history for learning
        self.event_history = {}  # {event_signature: [attempts]}
        self.history_file = 'logs/event_history.json'
        self._load_event_history()

    def _load_event_history(self):
        """Load event history from disk"""
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, 'r') as f:
                    self.event_history = json.load(f)
                logger.info(f"📚 Loaded {len(self.event_history)} event histories")
        except Exception as e:
            logger.error(f"Error loading event history: {e}")

    def _save_event_history(self):
        """Save event history to disk"""
        try:
            os.makedirs(os.path.dirname(self.history_file), exist_ok=True)
            with open(self.history_file, 'w') as f:
                json.dump(self.event_history, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving event history: {e}")
```

---

### Phase 3: Feedback-Loop implementieren

**Datei:** `src/integrations/orchestrator.py`

**Änderungen nach jeder Phase:**

```python
# After fix execution (around line 680)
if fix_result and fix_result.get('status') == 'success':
    fix_success = True
    logger.info(f"      ✅ Fix successful on attempt {attempt}/{max_retries}: {fix_result.get('message', 'Fixed')}")

    # NEW: Record successful fix in history
    event_signature = f"{event.source}_{event.event_type}"
    if event_signature not in self.event_history:
        self.event_history[event_signature] = []

    self.event_history[event_signature].append({
        'timestamp': datetime.now().isoformat(),
        'strategy': strategy,
        'result': 'success',
        'message': fix_result.get('message'),
        'details': fix_result.get('details'),
        'attempt': attempt
    })

    # Keep only last 10 attempts per event type
    self.event_history[event_signature] = self.event_history[event_signature][-10:]
    self._save_event_history()

    break
else:
    # NEW: Record failed attempt
    last_error = fix_result.get('error', 'Unknown error')
    logger.warning(f"      ⚠️ Attempt {attempt}/{max_retries} failed: {last_error}")

    event_signature = f"{event.source}_{event.event_type}"
    if event_signature not in self.event_history:
        self.event_history[event_signature] = []

    self.event_history[event_signature].append({
        'timestamp': datetime.now().isoformat(),
        'strategy': strategy,
        'result': 'failed',
        'error': last_error,
        'attempt': attempt
    })

    self.event_history[event_signature] = self.event_history[event_signature][-10:]
    self._save_event_history()
```

---

### Phase 4: AI Prompt Enhancement für Smart Decisions

**Datei:** `src/integrations/ai_service.py`

**Änderungen in `_build_analysis_prompt()`:**

```python
# Add after infrastructure context (around line 186)

# Add previous attempts context for learning
if previous_attempts and len(previous_attempts) > 0:
    prompt_parts.append("# LEARNING FROM PREVIOUS ATTEMPTS")
    prompt_parts.append("The system has tried to fix similar issues before. Learn from these attempts:\n")

    for i, attempt in enumerate(previous_attempts, 1):
        prompt_parts.append(f"## Attempt {i}")
        prompt_parts.append(f"**Strategy:** {attempt.get('strategy', {}).get('description', 'N/A')}")
        prompt_parts.append(f"**Result:** {attempt.get('result', 'unknown')}")
        if attempt.get('error'):
            prompt_parts.append(f"**Error:** {attempt['error']}")
        prompt_parts.append("")

    prompt_parts.append("**IMPORTANT:** Analyze what worked and what didn't. Adapt your strategy accordingly!")
    prompt_parts.append("\n" + "="*80 + "\n")

# Add smart upgrade decisions for external images
if source == 'trivy' and details.get('ExternalImages'):
    prompt_parts.append("# SMART DOCKER IMAGE UPGRADE DECISION")
    prompt_parts.append("For external images without security updates on current version:")
    prompt_parts.append("1. Check docker-compose.yml/deployment configs for version constraints")
    prompt_parts.append("2. Consider major version upgrades ONLY if:")
    prompt_parts.append("   - Breaking changes are documented and manageable")
    prompt_parts.append("   - Migration path exists (e.g., postgres:15 → postgres:16)")
    prompt_parts.append("   - Risk is justified by vulnerability severity")
    prompt_parts.append("3. For unclear cases: MONITOR instead of risky upgrades")
    prompt_parts.append("\n" + "="*80 + "\n")
```

---

### Phase 5: Smart Docker Version Upgrade Logic

**Datei:** `src/integrations/docker_image_analyzer.py`

**Neue Methode hinzufügen:**

```python
def check_major_version_upgrade(self, image_name: str, current_tag: str) -> Optional[Dict]:
    """
    Check if major version upgrade is available and safe

    Returns:
        Dict with upgrade info or None if not recommended
    """
    name, _ = self._parse_image_name(image_name)

    # Known safe upgrade paths
    safe_upgrades = {
        'postgres': {
            '15': {'next': '16', 'notes': 'Requires pg_upgrade or dump/restore'},
            '14': {'next': '15', 'notes': 'Minor breaking changes in config'},
        },
        'redis': {
            '7': {'next': '8', 'notes': 'Check for deprecated commands'},
            '6': {'next': '7', 'notes': 'Review ACL changes'},
        }
    }

    # Extract major version from tag
    import re
    version_match = re.match(r'^(\d+)', current_tag)
    if not version_match:
        return None

    current_major = version_match.group(1)

    # Check if we have upgrade info
    if name in safe_upgrades and current_major in safe_upgrades[name]:
        upgrade_info = safe_upgrades[name][current_major]

        return {
            'current_version': current_tag,
            'recommended_version': upgrade_info['next'],
            'upgrade_type': 'major',
            'notes': upgrade_info['notes'],
            'requires_manual_migration': True,
            'risk_level': 'medium'
        }

    return None
```

**Integration in `get_remediation_strategy()`:**

```python
def get_remediation_strategy(self, image_info: ImageInfo, vulnerability_count: int) -> Dict:
    """
    Generate smart remediation strategy
    """
    if image_info.is_external:
        if image_info.update_available:
            return {
                'action': 'upgrade',
                'description': f"Upgrade external image from {image_info.tag} to {image_info.latest_version}",
                # ... existing code ...
            }
        else:
            # NEW: Check for major version upgrades
            upgrade_info = self.check_major_version_upgrade(image_info.name, image_info.tag)

            if upgrade_info and vulnerability_count > 10:  # Only if many vulns
                return {
                    'action': 'major_upgrade',
                    'description': f"Consider major version upgrade: {upgrade_info['current_version']} → {upgrade_info['recommended_version']}",
                    'steps': [
                        f"⚠️  MANUAL REVIEW REQUIRED",
                        f"Current: {image_info.name}:{upgrade_info['current_version']}",
                        f"Recommended: {image_info.name}:{upgrade_info['recommended_version']}",
                        f"Notes: {upgrade_info['notes']}",
                        f"Vulnerabilities: {vulnerability_count} (justifies upgrade)",
                        "Review breaking changes in upstream docs",
                        "Test in staging environment first"
                    ],
                    'confidence': 'medium',
                    'reason': f"No updates on current version, but major upgrade available. {vulnerability_count} vulnerabilities present.",
                    'requires_approval': True
                }

            # Fallback to monitoring
            return {
                'action': 'monitor',
                # ... existing code ...
            }
```

---

## 📋 IMPLEMENTATION CHECKLIST

- [ ] Phase 1: Context Manager in Orchestrator (koordinierter Plan)
- [ ] Phase 2: previous_attempts in Phase Execution
- [ ] Phase 3: Event History Tracking (load/save)
- [ ] Phase 4: Feedback-Loop nach jeder Phase
- [ ] Phase 5: AI Prompt Enhancement (Learning Context)
- [ ] Phase 6: Smart Docker Major Version Upgrades
- [ ] Phase 7: Testing mit neuem Trivy Scan
- [ ] Phase 8: Verify Learning funktioniert (2. Scan sollte besser sein)

---

## 🧪 TESTING PLAN

1. **Test 1: Context wird genutzt**
   - Trigger Trivy Scan
   - Check logs: "INFRASTRUCTURE KNOWLEDGE BASE" im Prompt?

2. **Test 2: Learning funktioniert**
   - Erster Fix-Versuch → Fehler
   - Zweiter Fix-Versuch → Sollte andere Strategy haben
   - Check logs: "LEARNING FROM PREVIOUS ATTEMPTS"?

3. **Test 3: History Persistence**
   - Bot restart
   - Check: `logs/event_history.json` existiert?
   - Neue Events nutzen alte History?

4. **Test 4: Smart Upgrades**
   - Trivy findet postgres:15 vulns
   - Bot schlägt postgres:16 upgrade vor (mit Manual Review)
   - Approval-Request zeigt upgrade notes

---

## 🎯 EXPECTED OUTCOME

Nach Implementierung sollte die KI:
1. ✅ Code-Kontext haben (welche docker-compose.yml, welche Versionen)
2. ✅ Aus Fehlern lernen (andere Strategy bei Retry)
3. ✅ Intelligente Upgrade-Vorschläge machen (postgres:15→16)
4. ✅ Persistentes Wissen aufbauen (über Bot-Restarts hinweg)

---

## 📝 NOTES

- Event History File: `logs/event_history.json`
- Format: `{event_signature: [attempts]}`
- Keep last 10 attempts per event type
- Event signature: `{source}_{event_type}` (z.B. "trivy_docker_vulnerabilities")

---

Generated: 2025-11-19 22:30
By: Claude Code (Sonnet 4.5)
Status: READY FOR IMPLEMENTATION
