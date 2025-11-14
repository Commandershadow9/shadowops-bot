"""
AI Service for Security Analysis and Fix Strategy Generation
Supports OpenAI and Anthropic Claude
"""

import asyncio
from typing import Dict, Optional
import logging

logger = logging.getLogger('shadowops')


class AIService:
    """AI-powered security analysis and fix generation"""

    def __init__(self, config):
        self.config = config
        self.openai_client = None
        self.anthropic_client = None

        # Store config for lazy initialization
        self.openai_enabled = config.ai.get('openai', {}).get('enabled', False)
        self.openai_api_key = config.ai.get('openai', {}).get('api_key')
        self.openai_model = config.ai.get('openai', {}).get('model', 'gpt-4o')

        self.anthropic_enabled = config.ai.get('anthropic', {}).get('enabled', False)
        self.anthropic_api_key = config.ai.get('anthropic', {}).get('api_key')
        self.anthropic_model = config.ai.get('anthropic', {}).get('model', 'claude-3-5-sonnet-20241022')

        if self.openai_enabled and self.openai_api_key:
            logger.info(f"✅ OpenAI konfiguriert ({self.openai_model})")

        if self.anthropic_enabled and self.anthropic_api_key:
            logger.info(f"✅ Anthropic Claude konfiguriert ({self.anthropic_model})")

    async def generate_fix_strategy(self, context: Dict) -> Optional[Dict]:
        """
        Generate fix strategy using AI with deep analysis

        Args:
            context: Dict with 'event' and 'previous_attempts'

        Returns:
            Dict with 'description', 'confidence', 'steps', 'analysis'
        """
        event = context['event']
        previous_attempts = context.get('previous_attempts', [])

        # Build detailed prompt for deep analysis
        prompt = self._build_analysis_prompt(event, previous_attempts)

        # Try Anthropic first (better for security analysis)
        if self.anthropic_enabled and self.anthropic_api_key:
            try:
                result = await self._analyze_with_anthropic(prompt, event)
                if result:
                    logger.info(f"✅ Claude Analyse: {result.get('confidence', 0):.0%} Confidence")
                    return result
            except Exception as e:
                logger.error(f"❌ Claude Analyse fehlgeschlagen: {e}")

        # Fallback to OpenAI
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

    def _build_analysis_prompt(self, event: Dict, previous_attempts: list) -> str:
        """Build detailed analysis prompt with security context"""
        source = event.get('source', 'unknown')
        severity = event.get('severity', 'UNKNOWN')
        details = event.get('details', {})

        prompt = f"""You are a senior DevOps security engineer analyzing a security event.

**CRITICAL: Provide deep technical analysis with high confidence scores (85%+) for well-researched fixes.**

# Security Event Analysis

**Source:** {source}
**Severity:** {severity}

## Event Details:
"""

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

{
  "description": "Brief 1-2 sentence fix description",
  "confidence": 0.XX,  // Float between 0.0 and 1.0
  "analysis": "Detailed 2-3 paragraph technical analysis explaining:
    - Root cause of the security issue
    - Why your fix will work
    - Potential risks and side effects
    - Expected outcome",
  "steps": [
    "Step 1: Specific command or action",
    "Step 2: Verification step",
    "Step 3: Rollback plan if needed"
  ],
  "reasoning": "Why this confidence score? What research did you do?"
}

**CONFIDENCE GUIDELINES:**
- 95-100%: Well-documented fix, used in production environments, minimal risk
- 85-95%: Standard security practice, tested approach, low risk
- 70-85%: Requires careful implementation, moderate risk, needs testing
- <70%: Experimental, high risk, or insufficient information

**IMPORTANT:** Be conservative but realistic. A well-researched Docker update should be 90%+, not 70%.
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

    def _parse_json_response(self, content: str) -> Optional[Dict]:
        """Parse JSON from AI response"""
        import json
        import re

        # Try to extract JSON from markdown code blocks
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
        if json_match:
            content = json_match.group(1)

        # Try direct JSON parse
        try:
            result = json.loads(content)

            # Validate required fields
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
