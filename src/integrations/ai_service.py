"""
AI Service for Security Analysis and Fix Strategy Generation
Supports Ollama (local), OpenAI, and Anthropic Claude with RAG context
"""

import asyncio
from typing import Dict, Optional
import logging
import json
import httpx

logger = logging.getLogger('shadowops')


class AIService:
    """AI-powered security analysis and fix generation with hybrid model support"""

    def __init__(self, config, context_manager=None):
        self.config = config
        self.context_manager = context_manager
        self.openai_client = None
        self.anthropic_client = None

        # Ollama configuration (PRIMARY - local & free)
        self.ollama_enabled = config.ai.get('ollama', {}).get('enabled', True)
        self.ollama_url = config.ai.get('ollama', {}).get('url', 'http://127.0.0.1:11434')
        self.ollama_model = config.ai.get('ollama', {}).get('model', 'phi3:mini')
        self.ollama_model_critical = config.ai.get('ollama', {}).get('model_critical', 'llama3.1')

        # Hybrid model selection (smart model choice based on severity)
        self.use_hybrid_models = config.ai.get('ollama', {}).get('hybrid_models', True)

        # Store config for lazy initialization
        self.openai_enabled = config.ai.get('openai', {}).get('enabled', False)
        self.openai_api_key = config.ai.get('openai', {}).get('api_key')
        self.openai_model = config.ai.get('openai', {}).get('model', 'gpt-4o')

        self.anthropic_enabled = config.ai.get('anthropic', {}).get('enabled', False)
        self.anthropic_api_key = config.ai.get('anthropic', {}).get('api_key')
        self.anthropic_model = config.ai.get('anthropic', {}).get('model', 'claude-3-5-sonnet-20241022')

        if self.ollama_enabled:
            if self.use_hybrid_models:
                logger.info(f"✅ Ollama Hybrid konfiguriert:")
                logger.info(f"   📊 Standard: {self.ollama_model} (schnell)")
                logger.info(f"   🧠 Critical: {self.ollama_model_critical} (intelligenter)")
            else:
                logger.info(f"✅ Ollama konfiguriert ({self.ollama_model} @ {self.ollama_url})")

        if self.openai_enabled and self.openai_api_key:
            logger.info(f"✅ OpenAI konfiguriert ({self.openai_model})")

        if self.anthropic_enabled and self.anthropic_api_key:
            logger.info(f"✅ Anthropic Claude konfiguriert ({self.anthropic_model})")

    async def generate_fix_strategy(self, context: Dict) -> Optional[Dict]:
        """
        Generate fix strategy using AI with deep analysis

        Hybrid AI approach:
        1. Try Ollama (local, free, unlimited) - PRIMARY
        2. Fallback to Anthropic (security-focused) - if Ollama fails
        3. Fallback to OpenAI (general-purpose) - final fallback

        Args:
            context: Dict with 'event' and 'previous_attempts'

        Returns:
            Dict with 'description', 'confidence', 'steps', 'analysis'
        """
        event = context['event']
        previous_attempts = context.get('previous_attempts', [])

        # Build detailed prompt for deep analysis with RAG context
        prompt = self._build_analysis_prompt(event, previous_attempts)

        # Try Ollama first (PRIMARY - local & free)
        if self.ollama_enabled:
            try:
                result = await self._analyze_with_ollama(prompt, event, context)
                if result:
                    logger.info(f"✅ Ollama Analyse: {result.get('confidence', 0):.0%} Confidence")
                    return result
            except Exception as e:
                logger.warning(f"⚠️ Ollama Analyse fehlgeschlagen, versuche Cloud-Alternativen: {e}")

        # Fallback to Anthropic (better for security analysis)
        if self.anthropic_enabled and self.anthropic_api_key:
            try:
                result = await self._analyze_with_anthropic(prompt, event)
                if result:
                    logger.info(f"✅ Claude Analyse: {result.get('confidence', 0):.0%} Confidence")
                    return result
            except Exception as e:
                logger.error(f"❌ Claude Analyse fehlgeschlagen: {e}")

        # Final fallback to OpenAI
        if self.openai_enabled and self.openai_api_key:
            try:
                result = await self._analyze_with_openai(prompt, event)
                if result:
                    logger.info(f"✅ OpenAI Analyse: {result.get('confidence', 0):.0%} Confidence")
                    return result
            except Exception as e:
                logger.error(f"❌ OpenAI Analyse fehlgeschlagen: {e}")

        logger.error("❌ Alle AI Services fehlgeschlagen")
        return None

    async def generate_coordinated_plan(self, prompt: str, context: Dict) -> Optional[Dict]:
        """
        Generiert koordinierten Gesamt-Plan für mehrere Events

        Args:
            prompt: Spezieller Orchestrator-Prompt
            context: Dict mit batch_events, sources, etc.

        Returns:
            Dict mit phases, description, confidence, etc.
        """
        logger.info(f"🎯 Generiere koordinierten Plan für {context.get('event_count', 0)} Events")

        # Bestimme Severity für Modell-Auswahl
        severity = context.get('highest_severity', 'HIGH')

        # Erstelle synthetisches Event für AI-Routing
        synthetic_event = {
            'source': 'orchestrator',
            'severity': severity,
            'event_type': 'coordinated_batch',
            'details': context
        }

        # Verwende Ollama mit speziellem Prompt
        if self.ollama_enabled:
            try:
                result = await self._analyze_with_ollama(prompt, synthetic_event, context)
                if result:
                    logger.info(f"✅ Koordinierter Plan erstellt: {result.get('confidence', 0):.0%} Confidence")
                    return result
            except Exception as e:
                logger.warning(f"⚠️ Ollama fehlgeschlagen bei koordinierter Planung: {e}")

        # Fallback zu Anthropic
        if self.anthropic_enabled and self.anthropic_api_key:
            try:
                result = await self._analyze_with_anthropic(prompt, synthetic_event)
                if result:
                    logger.info(f"✅ Koordinierter Plan (Claude): {result.get('confidence', 0):.0%} Confidence")
                    return result
            except Exception as e:
                logger.error(f"❌ Claude fehlgeschlagen: {e}")

        # Fallback zu OpenAI
        if self.openai_enabled and self.openai_api_key:
            try:
                result = await self._analyze_with_openai(prompt, synthetic_event)
                if result:
                    logger.info(f"✅ Koordinierter Plan (OpenAI): {result.get('confidence', 0):.0%} Confidence")
                    return result
            except Exception as e:
                logger.error(f"❌ OpenAI fehlgeschlagen: {e}")

        logger.error("❌ Alle AI Services fehlgeschlagen bei koordinierter Planung")
        return None

    def _build_analysis_prompt(self, event: Dict, previous_attempts: list) -> str:
        """Build detailed analysis prompt with security context and RAG"""
        source = event.get('source', 'unknown')
        severity = event.get('severity', 'UNKNOWN')
        event_type = event.get('event_type', 'unknown')
        details = event.get('details', {})

        # Build prompt with RAG context
        prompt_parts = []

        # Add infrastructure and project context if available
        if self.context_manager:
            prompt_parts.append("# INFRASTRUCTURE KNOWLEDGE BASE")
            prompt_parts.append("You have access to detailed information about the server infrastructure and running projects.")
            prompt_parts.append("Use this context to make informed, safe decisions.\n")

            # Get relevant context for this event
            relevant_context = self.context_manager.get_relevant_context(source, event_type)
            prompt_parts.append(relevant_context)
            prompt_parts.append("\n" + "="*80 + "\n")

            # Add safety rules
            safety_prompt = self.context_manager.build_safety_prompt()
            prompt_parts.append(safety_prompt)
            prompt_parts.append("\n" + "="*80 + "\n")

        # Main analysis prompt
        prompt_parts.append(f"""You are a senior DevOps security engineer analyzing a security event.

**CRITICAL: Provide deep technical analysis with high confidence scores (85%+) for well-researched fixes.**

# Security Event Analysis

**Source:** {source}
**Severity:** {severity}

## Event Details:
""")

        prompt = "\n".join(prompt_parts)

        # Add source-specific context
        if source == 'trivy':
            prompt += f"""
**Type:** Docker Vulnerability Scan
**Statistics:**
- Critical vulnerabilities: {details.get('Stats', {}).get('critical', 0)}
- High vulnerabilities: {details.get('Stats', {}).get('high', 0)}
- Medium vulnerabilities: {details.get('Stats', {}).get('medium', 0)}
- Affected images: {details.get('Stats', {}).get('images', 0)}

**Sample vulnerabilities:**
{self._format_vulnerabilities(details.get('Vulnerabilities', [])[:5])}

**Your Task:**
1. Analyze the vulnerability types and affected packages
2. Determine if these are:
   - Easily fixable (package updates available) → 90-95% confidence
   - Require minor code changes → 85-90% confidence
   - Major architectural changes needed → 70-85% confidence
   - No known fix available → <70% confidence
3. Provide specific Docker fix strategy with exact commands
"""

        elif source == 'fail2ban':
            bans = details.get('Bans', [])
            stats = details.get('Stats', {})
            total_bans = stats.get('total_bans', 0)

            # Count SSH bans
            ssh_bans = sum(1 for ban in bans if 'sshd' in ban.get('jail', '').lower())

            prompt += f"""
**Type:** Fail2ban Intrusion Detection
**Statistics:**
- Total banned IPs: {total_bans}
- SSH brute-force attempts: {ssh_bans}

**Banned IPs (sample):**
{self._format_bans(bans[:10])}

**Your Task:**
1. Analyze the attack pattern:
   - Is this a coordinated botnet attack? (multiple IPs, same targets)
   - Is this targeted SSH brute-force? (same IP, many attempts)
   - Geographic distribution of attacks?
2. Recommend remediation strategy:
   - For coordinated attacks (>50 IPs): Firewall rules, GeoIP blocking → 90% confidence
   - For SSH attacks (>=10 attempts): SSH hardening, port changes → 95% confidence
   - For isolated incidents: Monitor only → 85% confidence
3. Provide specific Linux commands for implementation
"""

        elif source == 'crowdsec':
            prompt += f"""
**Type:** CrowdSec Threat Intelligence
**IP:** {details.get('ip', 'N/A')}
**Scenario:** {details.get('scenario', 'N/A')}
**Country:** {details.get('country', 'N/A')}

**Your Task:**
1. Research the threat scenario (CVE, attack type)
2. Assess threat level and likelihood of success
3. Recommend mitigation:
   - Known CVE with patches: 90-95% confidence
   - Generic attack patterns: 85-90% confidence
   - Unknown/emerging threats: 75-85% confidence
4. Provide specific CrowdSec/firewall commands
"""

        elif source == 'aide':
            prompt += f"""
**Type:** AIDE File Integrity Check
**Changed files:** {details.get('files_changed', 0)}
**Added files:** {details.get('files_added', 0)}
**Removed files:** {details.get('files_removed', 0)}

**Your Task:**
1. Determine if changes are:
   - Expected (system updates, logs): Monitor only → 90% confidence
   - Suspicious (binaries, configs): Investigate deeply → 70-80% confidence
   - Critical (rootkit indicators): Emergency response → 95% confidence
2. Provide investigation and remediation steps
"""

        # Add previous attempt history
        if previous_attempts:
            prompt += "\n\n## Previous Failed Attempts:\n"
            for i, attempt in enumerate(previous_attempts, 1):
                prompt += f"\nAttempt {i}:\n"
                prompt += f"- Strategy: {attempt.get('strategy', 'N/A')}\n"
                prompt += f"- Result: {attempt.get('result', 'N/A')}\n"
                prompt += f"- Error: {attempt.get('error', 'N/A')}\n"
            prompt += "\n**Learn from these failures and adjust your strategy.**\n"

        # Response format
        prompt += """

# Required Response Format (JSON):

**WICHTIG: Alle Texte MÜSSEN auf DEUTSCH sein! (description, analysis, steps, reasoning)**

{
  "description": "Kurze 1-2 Sätze Beschreibung des Fixes (auf DEUTSCH)",
  "confidence": 0.XX,  // Float between 0.0 and 1.0
  "analysis": "Detaillierte 2-3 Absätze technische Analyse (auf DEUTSCH) mit:
    - Ursache des Sicherheitsproblems
    - Warum dieser Fix funktioniert
    - Potenzielle Risiken und Nebenwirkungen
    - Erwartetes Ergebnis",
  "steps": [
    "Schritt 1: Konkreter Befehl oder Aktion (auf DEUTSCH)",
    "Schritt 2: Verifikations-Schritt (auf DEUTSCH)",
    "Schritt 3: Rollback-Plan falls nötig (auf DEUTSCH)"
  ],
  "reasoning": "Warum diese Confidence? Welche Recherche wurde durchgeführt? (auf DEUTSCH)"
}

**CONFIDENCE GUIDELINES:**
- 95-100%: Gut dokumentierter Fix, in Production getestet, minimales Risiko
- 85-95%: Standard Security-Praxis, getesteter Ansatz, niedriges Risiko
- 70-85%: Erfordert sorgfältige Implementierung, moderates Risiko
- <70%: Experimentell, hohes Risiko, oder unzureichende Informationen

**WICHTIG:** Sei konservativ aber realistisch. Ein gut recherchiertes Docker-Update sollte 90%+ haben, nicht 70%.

**SPRACHE:** ALLE Texte (description, analysis, steps, reasoning) MÜSSEN auf DEUTSCH sein!
"""

        return prompt

    def _format_vulnerabilities(self, vulns: list) -> str:
        """Format vulnerability list for prompt"""
        if not vulns:
            return "No vulnerability details available"

        formatted = []
        for v in vulns[:5]:
            formatted.append(
                f"- {v.get('VulnerabilityID', 'N/A')}: {v.get('PkgName', 'N/A')} "
                f"({v.get('InstalledVersion', 'N/A')} → {v.get('FixedVersion', 'N/A')})"
            )
        return "\n".join(formatted)

    def _format_bans(self, bans: list) -> str:
        """Format ban list for prompt"""
        if not bans:
            return "No ban details available"

        formatted = []
        for b in bans[:10]:
            formatted.append(
                f"- IP: {b.get('ip', 'N/A')} | Jail: {b.get('jail', 'N/A')} | "
                f"Time: {b.get('time', 'N/A')}"
            )
        return "\n".join(formatted)

    async def _analyze_with_anthropic(self, prompt: str, event: Dict) -> Optional[Dict]:
        """Analyze with Anthropic Claude"""
        try:
            # Lazy initialize client
            if not self.anthropic_client:
                import anthropic
                self.anthropic_client = anthropic.AsyncAnthropic(api_key=self.anthropic_api_key)

            message = await self.anthropic_client.messages.create(
                model=self.anthropic_model,
                max_tokens=2000,
                temperature=0.3,  # Lower temperature for more focused analysis
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )

            # Parse JSON response
            content = message.content[0].text
            result = self._parse_json_response(content)

            if result:
                # Add metadata
                result['ai_model'] = self.anthropic_model
                result['ai_provider'] = 'anthropic'

            return result

        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            return None

    async def _analyze_with_openai(self, prompt: str, event: Dict) -> Optional[Dict]:
        """Analyze with OpenAI"""
        try:
            # Lazy initialize client
            if not self.openai_client:
                import openai
                self.openai_client = openai.AsyncOpenAI(api_key=self.openai_api_key)

            response = await self.openai_client.chat.completions.create(
                model=self.openai_model,
                messages=[
                    {"role": "system", "content": "You are a security engineer providing fix strategies in JSON format."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=2000
            )

            # Parse JSON response
            content = response.choices[0].message.content
            result = self._parse_json_response(content)

            if result:
                # Add metadata
                result['ai_model'] = self.openai_model
                result['ai_provider'] = 'openai'

            return result

        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return None

    async def _analyze_with_ollama(self, prompt: str, event: Dict, context: Optional[Dict] = None) -> Optional[Dict]:
        """Analyze with Ollama (local LLM) - Smart model selection based on severity"""
        try:
            # Select model based on severity (Hybrid strategy)
            severity = event.get('severity', 'UNKNOWN')

            if self.use_hybrid_models and severity == 'CRITICAL':
                selected_model = self.ollama_model_critical
                timeout = 360.0  # 6 minutes for critical events (llama3.1 needs time for deep analysis + RAG context)
                logger.info(f"🧠 Using {selected_model} for CRITICAL event (deep analysis)")
            else:
                selected_model = self.ollama_model
                timeout = 120.0  # 2 minutes for standard events
                if severity == 'CRITICAL':
                    logger.info(f"📊 Using {selected_model} for CRITICAL event (hybrid disabled)")

            # Log request details
            prompt_length = len(prompt)
            logger.info(f"📤 Sende Request an Ollama ({selected_model})")
            logger.info(f"   📝 Prompt-Länge: {prompt_length} Zeichen")
            logger.info(f"   ⏱️  Timeout: {timeout/60:.1f} Minuten")
            logger.info(f"   ⏳ Warte auf Antwort... (dies kann mehrere Minuten dauern)")

            import time
            import asyncio
            start_time = time.time()

            # Get streaming_state from context if available
            streaming_state = context.get('streaming_state', {}) if context else {}

            # Use STREAMING for real-time progress updates
            token_count = 0
            content = ""

            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                        "POST",
                        f"{self.ollama_url}/api/generate",
                        json={
                            "model": selected_model,
                            "prompt": prompt,
                            "stream": True,  # ENABLE STREAMING
                            "format": "json",
                            "options": {
                                "temperature": 0.3,
                                "num_predict": 2000,
                            }
                        }
                    ) as response:
                        if response.status_code != 200:
                            logger.error(f"Ollama API error: HTTP {response.status_code}")
                            return None

                        # Process stream line by line
                        full_response = ""
                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue

                            try:
                                chunk = json.loads(line)
                                if chunk.get('response'):
                                    full_response += chunk['response']
                                    token_count += 1

                                    # Update streaming state for Discord live updates
                                    if streaming_state:
                                        streaming_state['token_count'] = token_count
                                        # Extract recent snippet (last 200 chars, cleaned)
                                        recent = full_response[-200:] if len(full_response) > 200 else full_response
                                        streaming_state['last_snippet'] = recent.replace('\n', ' ').strip()

                                    # Log progress every 50 tokens
                                    if token_count % 50 == 0:
                                        elapsed = time.time() - start_time
                                        # Extract snippet for logging
                                        recent = full_response[-200:] if len(full_response) > 200 else full_response
                                        snippet = recent.replace('\n', ' ')[:100]
                                        logger.info(f"   📝 Tokens: {token_count} | Zeit: {elapsed:.0f}s | Snippet: {snippet}...")

                                # Check if done
                                if chunk.get('done'):
                                    logger.info(f"   ✅ Stream abgeschlossen ({token_count} tokens)")
                                    break

                            except json.JSONDecodeError:
                                continue

                        # Store final response
                        content = full_response

                # Calculate stats
                elapsed_time = time.time() - start_time
                logger.info(f"📥 Response erhalten nach {elapsed_time:.1f} Sekunden ({elapsed_time/60:.1f} Minuten)")

                # Log response stats
                response_length = len(content)
                logger.info(f"   📊 Response-Länge: {response_length} Zeichen")
                logger.info(f"   🔢 Tokens generiert: {token_count}")
                if elapsed_time > 0:
                    tokens_per_sec = token_count / elapsed_time
                    logger.info(f"   ⚡ Generation Speed: {tokens_per_sec:.1f} tokens/sec")

                # Parse JSON response
                logger.info(f"   🔍 Parse JSON-Response...")
                # Check if this is a coordinated plan (from orchestrator)
                is_coordinated = context.get('is_coordinated_planning', False) if context else False
                result = self._parse_json_response(content, is_coordinated_plan=is_coordinated)

                if result:
                    # Add metadata
                    result['ai_model'] = selected_model
                    result['ai_provider'] = 'ollama'
                    logger.info(f"   ✅ JSON erfolgreich geparst (Confidence: {result.get('confidence', 0):.0%})")
                else:
                    logger.warning(f"   ⚠️ JSON-Parsing fehlgeschlagen - ungültiges Format")

                return result

            except Exception as e:
                logger.error(f"Ollama streaming error: {e}")
                return None

        except Exception as e:
            logger.error(f"Ollama API error: {e}")
            return None

    def _parse_json_response(self, content: str, is_coordinated_plan: bool = False) -> Optional[Dict]:
        """Parse JSON from AI response

        Args:
            content: AI response content
            is_coordinated_plan: If True, validates for coordinated plan format (phases, rollback_plan)
                                If False, validates for fix strategy format (steps)
        """
        import json
        import re

        # Try to extract JSON from markdown code blocks
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if json_match:
            content = json_match.group(1)

        # Try direct JSON parse
        try:
            result = json.loads(content)

            # Validate required fields based on type
            if is_coordinated_plan:
                # Coordinated plan format
                required = ['description', 'confidence', 'phases']
                if all(field in result for field in required):
                    # Ensure confidence is float
                    result['confidence'] = float(result['confidence'])
                    logger.info(f"✅ Koordinierter Plan geparst: {len(result.get('phases', []))} Phasen")
                    return result
                else:
                    missing = [f for f in required if f not in result]
                    logger.error(f"Missing required fields for coordinated plan: {missing}")
                    logger.debug(f"Available fields: {list(result.keys())}")
                    return None
            else:
                # Fix strategy format
                required = ['description', 'confidence', 'steps']
                if all(field in result for field in required):
                    # Ensure confidence is float
                    result['confidence'] = float(result['confidence'])
                    return result
                else:
                    logger.error(f"Missing required fields in AI response: {result.keys()}")
                    return None

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI JSON response: {e}")
            logger.debug(f"Response content: {content[:500]}")
            return None
