"""
AI Engine — Dual-Engine mit Codex CLI + Claude CLI
Dual-Engine AI-System fuer ShadowOps v4.

Architektur:
  - CodexProvider: Codex CLI (primaer) mit --output-schema fuer strukturierten Output
  - ClaudeProvider: Claude CLI (fallback) mit Schema-Anweisung im Prompt
  - TaskRouter: Severity + Task-basiertes Routing zu Engine/Modell
  - AIEngine: Hauptklasse, kompatibel mit altem AIService-Interface

Sicherheit:
  - Verwendet asyncio.create_subprocess_exec (NICHT shell=True)
  - CLAUDECODE env var wird aus subprocess-Umgebung entfernt
  - Kein User-Input wird direkt an Shell weitergegeben
"""

import asyncio
import json
import logging
import os
import re
import tempfile
from typing import Dict, Optional, List
from pathlib import Path

logger = logging.getLogger('shadowops')

# Basis-Pfad zu den JSON-Schemas
SCHEMAS_DIR = Path(__file__).parent.parent / 'schemas'


# ============================================================================
# CODEX CLI PROVIDER
# ============================================================================

class CodexProvider:
    """
    Provider fuer OpenAI Codex CLI.

    CLI-Aufruf (via create_subprocess_exec, kein Shell):
        codex  exec  --ephemeral  -s  workspace-write  -m  MODEL  -q  PROMPT
        Bei Schema: --output-schema SCHEMA_PATH

    Modelle:
        fast     -> gpt-4o (schnell, guenstig)
        standard -> gpt-5.3-codex (ausgewogen)
        thinking -> o3 (tiefe Analyse, laengerer Timeout)
    """

    def __init__(self, config: dict):
        self.models = config.get('models', {
            'fast': 'gpt-4o',
            'standard': 'gpt-5.3-codex',
            'thinking': 'o3',
        })
        self.timeout = config.get('timeout', 60)
        self.timeout_thinking = config.get('timeout_thinking', 300)

    def _get_clean_env(self) -> dict:
        """Erstellt eine bereinigte Umgebungskopie ohne CLAUDECODE"""
        env = os.environ.copy()
        env.pop('CLAUDECODE', None)
        return env

    def _resolve_model(self, model: str) -> str:
        """Loest Model-Klasse (fast/standard/thinking) zum konkreten Modellnamen auf"""
        return self.models.get(model, model)

    def _get_timeout(self, model: str) -> int:
        """Gibt den passenden Timeout fuer das Modell zurueck"""
        if model == 'thinking':
            return self.timeout_thinking
        return self.timeout

    def _extract_json(self, text: str) -> Optional[Dict]:
        """
        Extrahiert JSON aus CLI-Output.
        1. Versuch: Gesamter Text als JSON
        2. Fallback: JSON aus Markdown-Codeblock extrahieren
        3. Fallback: Erstes JSON-Objekt im Text finden
        """
        # 1. Versuch: Direktes JSON-Parsing
        try:
            return json.loads(text.strip())
        except (json.JSONDecodeError, ValueError):
            pass

        # 2. Fallback: JSON aus ```json ... ``` Codeblock
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except (json.JSONDecodeError, ValueError):
                pass

        # 3. Fallback: Erstes { ... } Objekt im Text
        brace_start = text.find('{')
        if brace_start >= 0:
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[brace_start:i + 1])
                        except (json.JSONDecodeError, ValueError):
                            break

        return None

    async def query(
        self,
        prompt: str,
        model: str = 'standard',
        schema_path: Optional[Path] = None,
        timeout: Optional[int] = None,
    ) -> Optional[Dict]:
        """
        Fuehrt eine strukturierte Abfrage ueber Codex CLI aus.

        Args:
            prompt: Der Analyse-Prompt
            model: Modell-Klasse (fast/standard/thinking)
            schema_path: Optionaler Pfad zur JSON-Schema-Datei
            timeout: Optionaler Timeout (ueberschreibt Default)

        Returns:
            Geparstes JSON-Dict oder None bei Fehler
        """
        resolved_model = self._resolve_model(model)
        effective_timeout = timeout or self._get_timeout(model)
        env = self._get_clean_env()

        # CLI-Argumente aufbauen — kein Shell, nur Argumentliste
        # --skip-git-repo-check: Kein trusted directory noetig
        # -c mcp_servers={}: Keine MCP-Server laden (schneller, keine Auth-Fehler)
        args = [
            'codex', 'exec', '--ephemeral',
            '--skip-git-repo-check',
            '-c', 'mcp_servers={}',
            '-s', 'workspace-write',
            '-m', resolved_model,
        ]

        if schema_path:
            args.extend(['--output-schema', str(schema_path)])

        # Prompt als positionales Argument am Ende (Codex CLI v0.104+)
        args.append(prompt)

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout,
            )

            stdout = stdout_bytes.decode('utf-8', errors='replace').strip()
            stderr = stderr_bytes.decode('utf-8', errors='replace').strip()

            if proc.returncode != 0:
                logger.warning(f"Codex CLI Fehler (Exit {proc.returncode}): {stderr[-500:]}")
                return None

            if not stdout:
                logger.warning("Codex CLI: Leere Antwort erhalten")
                return None

            result = self._extract_json(stdout)
            if result is None:
                logger.warning(f"Codex CLI: JSON-Parsing fehlgeschlagen fuer Output ({len(stdout)} Zeichen)")
            return result

        except asyncio.TimeoutError:
            logger.error(f"Codex CLI: Timeout nach {effective_timeout}s (Modell: {resolved_model})")
            return None
        except FileNotFoundError:
            logger.error("Codex CLI: 'codex' Befehl nicht gefunden")
            return None
        except Exception as e:
            logger.error(f"Codex CLI: Unerwarteter Fehler: {e}")
            return None

    async def query_raw(
        self,
        prompt: str,
        model: str = 'standard',
        timeout: Optional[int] = None,
    ) -> Optional[str]:
        """
        Fuehrt eine Text-Abfrage ueber Codex CLI aus (kein JSON-Parsing).

        Returns:
            Rohtext-Antwort oder None bei Fehler
        """
        resolved_model = self._resolve_model(model)
        effective_timeout = timeout or self._get_timeout(model)
        env = self._get_clean_env()

        args = [
            'codex', 'exec', '--ephemeral',
            '--skip-git-repo-check',
            '-c', 'mcp_servers={}',
            '-s', 'workspace-write',
            '-m', resolved_model,
            prompt,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout,
            )

            stdout = stdout_bytes.decode('utf-8', errors='replace').strip()

            if proc.returncode != 0:
                stderr = stderr_bytes.decode('utf-8', errors='replace').strip()
                logger.warning(f"Codex CLI Raw Fehler (Exit {proc.returncode}): {stderr[-500:]}")
                return None

            return stdout or None

        except asyncio.TimeoutError:
            logger.error(f"Codex CLI Raw: Timeout nach {effective_timeout}s")
            return None
        except Exception as e:
            logger.error(f"Codex CLI Raw: Fehler: {e}")
            return None

    async def is_available(self) -> bool:
        """Prueft ob die Codex CLI verfuegbar ist"""
        try:
            proc = await asyncio.create_subprocess_exec(
                'codex', '--version',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            return proc.returncode == 0
        except Exception:
            return False


# ============================================================================
# CLAUDE CLI PROVIDER
# ============================================================================

class ClaudeProvider:
    """
    Provider fuer Anthropic Claude CLI.

    CLI-Aufruf (via create_subprocess_exec, kein Shell):
        claude  -p  PROMPT  --output-format  json  --model  MODEL  --max-turns  1
        Fuer Raw: --output-format text

    Schema-Handling:
        Claude unterstuetzt kein --output-schema Flag.
        Stattdessen wird das Schema als Anweisung in den Prompt eingebaut.

    JSON-Parsing:
        Claude gibt oft {"result": "..."} Wrapper zurueck.
        Der innere result-String wird als JSON geparst.
    """

    def __init__(self, config: dict):
        self.cli_path = config.get('cli_path', '/home/cmdshadow/.local/bin/claude')
        self.models = config.get('models', {
            'fast': 'claude-sonnet-4-6',
            'standard': 'claude-sonnet-4-6',
            'thinking': 'claude-opus-4-6',
        })
        self.timeout = config.get('timeout', 120)

    def _get_clean_env(self) -> dict:
        """Erstellt eine bereinigte Umgebungskopie ohne CLAUDECODE"""
        env = os.environ.copy()
        env.pop('CLAUDECODE', None)
        return env

    def _resolve_model(self, model: str) -> str:
        """Loest Model-Klasse zum konkreten Modellnamen auf"""
        return self.models.get(model, model)

    def _build_prompt_with_schema(self, prompt: str, schema_path: Optional[Path]) -> str:
        """
        Baut Schema-Anweisungen in den Prompt ein (fuer Claude).
        Claude hat kein --output-schema, daher muss das Schema im Prompt stehen.
        """
        if not schema_path or not schema_path.exists():
            return prompt

        try:
            schema_content = schema_path.read_text(encoding='utf-8')
            schema_instruction = (
                f"\n\nAntworte AUSSCHLIESSLICH mit gueltigem JSON, das diesem Schema entspricht:\n"
                f"```json\n{schema_content}\n```\n"
                f"Kein zusaetzlicher Text, nur das JSON-Objekt."
            )
            return prompt + schema_instruction
        except Exception as e:
            logger.warning(f"Schema-Datei konnte nicht gelesen werden: {e}")
            return prompt

    def _parse_claude_response(self, text: str) -> Optional[Dict]:
        """
        Parst Claude CLI JSON-Antwort.
        Claude gibt oft {"result": "..."} zurueck, wobei result ein JSON-String ist.
        """
        try:
            parsed = json.loads(text.strip())
        except (json.JSONDecodeError, ValueError):
            # Fallback: JSON aus Text extrahieren
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except (json.JSONDecodeError, ValueError):
                    return None
            else:
                return None

        # Pruefe ob es ein {"result": "..."} Wrapper ist
        if isinstance(parsed, dict) and 'result' in parsed and isinstance(parsed['result'], str):
            inner_text = parsed['result']
            try:
                inner = json.loads(inner_text)
                if isinstance(inner, dict):
                    return inner
            except (json.JSONDecodeError, ValueError):
                pass
            # Wenn inner kein JSON ist, gib den Wrapper selbst zurueck
            return parsed

        return parsed if isinstance(parsed, dict) else None

    async def query(
        self,
        prompt: str,
        model: str = 'standard',
        schema_path: Optional[Path] = None,
        timeout: Optional[int] = None,
    ) -> Optional[Dict]:
        """
        Fuehrt eine strukturierte Abfrage ueber Claude CLI aus.

        Args:
            prompt: Der Analyse-Prompt
            model: Modell-Klasse (fast/standard/thinking)
            schema_path: Optionaler Pfad zur JSON-Schema-Datei
            timeout: Optionaler Timeout (ueberschreibt Default)

        Returns:
            Geparstes JSON-Dict oder None bei Fehler
        """
        resolved_model = self._resolve_model(model)
        effective_timeout = timeout or self.timeout
        env = self._get_clean_env()

        # Schema in Prompt einbauen (Claude hat kein --output-schema)
        full_prompt = self._build_prompt_with_schema(prompt, schema_path)

        args = [
            self.cli_path,
            '-p', full_prompt,
            '--output-format', 'json',
            '--model', resolved_model,
            '--max-turns', '1',
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout,
            )

            stdout = stdout_bytes.decode('utf-8', errors='replace').strip()
            stderr = stderr_bytes.decode('utf-8', errors='replace').strip()

            if proc.returncode != 0:
                logger.warning(f"Claude CLI Fehler (Exit {proc.returncode}): {stderr[:200]}")
                return None

            if not stdout:
                logger.warning("Claude CLI: Leere Antwort erhalten")
                return None

            result = self._parse_claude_response(stdout)
            if result is None:
                logger.warning(f"Claude CLI: JSON-Parsing fehlgeschlagen fuer Output ({len(stdout)} Zeichen)")
            return result

        except asyncio.TimeoutError:
            logger.error(f"Claude CLI: Timeout nach {effective_timeout}s (Modell: {resolved_model})")
            return None
        except FileNotFoundError:
            logger.error(f"Claude CLI: '{self.cli_path}' nicht gefunden")
            return None
        except Exception as e:
            logger.error(f"Claude CLI: Unerwarteter Fehler: {e}")
            return None

    async def query_raw(
        self,
        prompt: str,
        model: str = 'standard',
        timeout: Optional[int] = None,
    ) -> Optional[str]:
        """
        Fuehrt eine Text-Abfrage ueber Claude CLI aus (kein JSON-Parsing).

        Returns:
            Rohtext-Antwort oder None bei Fehler
        """
        resolved_model = self._resolve_model(model)
        effective_timeout = timeout or self.timeout
        env = self._get_clean_env()

        args = [
            self.cli_path,
            '-p', prompt,
            '--output-format', 'text',
            '--model', resolved_model,
            '--max-turns', '1',
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout,
            )

            stdout = stdout_bytes.decode('utf-8', errors='replace').strip()

            if proc.returncode != 0:
                stderr = stderr_bytes.decode('utf-8', errors='replace').strip()
                logger.warning(f"Claude CLI Raw Fehler (Exit {proc.returncode}): {stderr[:200]}")
                return None

            return stdout or None

        except asyncio.TimeoutError:
            logger.error(f"Claude CLI Raw: Timeout nach {effective_timeout}s")
            return None
        except Exception as e:
            logger.error(f"Claude CLI Raw: Fehler: {e}")
            return None

    async def is_available(self) -> bool:
        """Prueft ob die Claude CLI verfuegbar ist"""
        try:
            proc = await asyncio.create_subprocess_exec(
                self.cli_path, '--version',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            return proc.returncode == 0
        except Exception:
            return False


# ============================================================================
# TASK ROUTER
# ============================================================================

class TaskRouter:
    """
    Config-basiertes Routing von Tasks zu Engines und Modellen.

    Routing-Keys:
        {severity}_{task_type}  -> z.B. "critical_analysis", "high_verify"
        {task_type}             -> z.B. "verify", "patch_notes", "incident"

    Schema-Mapping:
        analysis/fix -> fix_strategy.json
        patch_notes  -> patch_notes.json
        incident     -> incident_analysis.json
    """

    SCHEMA_MAP = {
        'fix_strategy': 'fix_strategy.json',
        'patch_notes': 'patch_notes.json',
        'incident_analysis': 'incident_analysis.json',
        'coordinated_plan': 'coordinated_plan.json',
    }

    # Standard-Routing wenn kein spezifischer Key gefunden
    DEFAULT_SEVERITY_MAP = {
        'CRITICAL': 'thinking',
        'HIGH': 'standard',
        'MEDIUM': 'standard',
        'LOW': 'fast',
    }

    def __init__(self, ai_config: dict, schemas_dir: Path = None):
        self.config = ai_config
        self.routing = ai_config.get('routing', {})
        self.primary_config = ai_config.get('primary', {})
        self.fallback_config = ai_config.get('fallback', {})
        self.schemas_dir = schemas_dir or SCHEMAS_DIR

    def _resolve_schema_path(self, schema_name: str) -> Optional[Path]:
        """Loest Schema-Name zu Dateipfad auf"""
        filename = self.SCHEMA_MAP.get(schema_name)
        if not filename:
            # Versuche direkt als Dateiname
            filename = f"{schema_name}.json"

        path = self.schemas_dir / filename
        if path.exists():
            return path

        logger.warning(f"Schema-Datei nicht gefunden: {path}")
        return path  # Pfad trotzdem zurueckgeben, Existenzpruefung beim Lesen

    def _get_engine_models(self, engine: str) -> dict:
        """Gibt die Modell-Konfiguration fuer eine Engine zurueck"""
        if engine == 'codex':
            return self.primary_config.get('models', {})
        elif engine == 'claude':
            return self.fallback_config.get('models', {})
        return {}

    def get_route(self, severity: str, task_type: str) -> dict:
        """
        Bestimmt die optimale Route fuer einen Task.

        Args:
            severity: Event-Severity (CRITICAL, HIGH, MEDIUM, LOW)
            task_type: Task-Typ (analysis, verify, patch_notes, incident, fix)

        Returns:
            Dict mit engine, model, model_class, schema_path
        """
        severity_upper = severity.upper()

        # 1. Spezifischer Key: {severity}_{task_type}
        specific_key = f"{severity_upper.lower()}_{task_type}"
        if specific_key in self.routing:
            return self._build_route(self.routing[specific_key])

        # 2. Task-Typ Key: {task_type}
        if task_type in self.routing:
            return self._build_route(self.routing[task_type])

        # 3. Default-Routing basierend auf Severity
        model_class = self.DEFAULT_SEVERITY_MAP.get(severity_upper, 'standard')

        # Bestimme Standard-Schema basierend auf Task-Typ
        schema_name = 'fix_strategy'
        if task_type == 'patch_notes':
            schema_name = 'patch_notes'
        elif task_type == 'incident':
            schema_name = 'incident_analysis'

        return self._build_route({
            'engine': 'codex',
            'model_class': model_class,
            'schema': schema_name,
        })

    def _build_route(self, route_config: dict) -> dict:
        """Baut ein vollstaendiges Route-Dict aus der Routing-Konfiguration"""
        engine = route_config.get('engine', 'codex')
        model_class = route_config.get('model_class', 'standard')
        schema_name = route_config.get('schema', 'fix_strategy')

        # Modell aufloesen
        models = self._get_engine_models(engine)
        model = models.get(model_class, model_class)

        # Schema-Pfad aufloesen
        schema_path = self._resolve_schema_path(schema_name)

        return {
            'engine': engine,
            'model': model,
            'model_class': model_class,
            'schema_path': schema_path,
        }


# ============================================================================
# AI ENGINE (Hauptklasse)
# ============================================================================

class AIEngine:
    """
    Hauptklasse fuer AI-gesteuerte Security-Analyse.

    Kompatibel mit dem alten AIService-Interface:
    - generate_fix_strategy(context) -> Optional[Dict]
    - get_ai_analysis(prompt, context, use_critical_model) -> Optional[str]
    - verify_fix(fix_description, fix_commands, event) -> Optional[Dict]

    Neue Methoden:
    - generate_coordinated_plan(prompt, context) -> Optional[Dict]
    - generate_raw_text = Alias fuer get_ai_analysis
    """

    def __init__(
        self,
        config,
        context_manager=None,
        discord_logger=None,
    ):
        self.config = config
        self.context_manager = context_manager
        self.discord_logger = discord_logger

        ai_cfg = config.ai if hasattr(config, 'ai') and isinstance(config.ai, dict) else {}

        # Provider erstellen
        primary_cfg = ai_cfg.get('primary', {
            'models': {'fast': 'gpt-4o', 'standard': 'gpt-5.3-codex', 'thinking': 'o3'},
            'timeout': 60,
            'timeout_thinking': 300,
        })
        fallback_cfg = ai_cfg.get('fallback', {
            'cli_path': '/home/cmdshadow/.local/bin/claude',
            'models': {'fast': 'claude-sonnet-4-6', 'standard': 'claude-sonnet-4-6', 'thinking': 'claude-opus-4-6'},
            'timeout': 120,
        })

        self.codex = CodexProvider(primary_cfg)
        self.claude = ClaudeProvider(fallback_cfg)
        self.router = TaskRouter(ai_cfg)

        # Stats-Tracking
        self.stats = {
            'codex_calls': 0,
            'codex_success': 0,
            'codex_failures': 0,
            'claude_calls': 0,
            'claude_success': 0,
            'claude_failures': 0,
        }

        # Logging
        logger.info("AI Engine initialisiert (Dual-Engine: Codex + Claude)")
        logger.info(f"  Codex-Modelle: {primary_cfg.get('models', {})}")
        logger.info(f"  Claude-Modelle: {fallback_cfg.get('models', {})}")

    # ------------------------------------------------------------------
    # generate_raw_text Alias
    # ------------------------------------------------------------------

    @property
    def generate_raw_text(self):
        """Alias fuer get_ai_analysis (Abwaertskompatibilitaet)"""
        return self.get_ai_analysis

    async def get_raw_ai_response(self, prompt: str, use_critical_model: bool = False) -> Optional[str]:
        """Alias fuer get_ai_analysis (Abwaertskompatibilitaet mit PatchNotesManager)"""
        return await self.get_ai_analysis(prompt=prompt, use_critical_model=use_critical_model)

    # ------------------------------------------------------------------
    # Oeffentliche Methoden
    # ------------------------------------------------------------------

    async def generate_fix_strategy(self, context: Dict) -> Optional[Dict]:
        """
        Generiert eine Fix-Strategie fuer ein Security-Event.
        Kompatibel mit dem alten AIService-Interface.

        Args:
            context: Dict mit 'event' und 'previous_attempts'

        Returns:
            Dict mit description, confidence, steps, analysis oder None
        """
        event = context.get('event', {})
        previous_attempts = context.get('previous_attempts', [])
        severity = event.get('severity', 'MEDIUM')
        source = event.get('source', 'unknown').upper()

        # Discord-Logger: Start
        if self.discord_logger:
            self.discord_logger.log_ai_learning(
                f"AI Analyse gestartet\n"
                f"Source: {source} | Severity: {severity}\n"
                f"Retry: {len(previous_attempts)} vorherige Versuche",
                severity="info"
            )

        # Prompt bauen
        prompt = self._build_analysis_prompt(event, previous_attempts)

        # Route bestimmen
        route = self.router.get_route(severity, 'analysis')

        # Ausfuehren mit Fallback
        result = await self._execute_with_fallback(prompt, route)

        if result:
            confidence = result.get('confidence', 0)
            description = result.get('description', 'N/A')

            # Discord-Logger: Erfolg
            if self.discord_logger:
                self.discord_logger.log_ai_learning(
                    f"AI Analyse erfolgreich\n"
                    f"Confidence: {confidence:.0%}\n"
                    f"Strategy: {description[:150]}{'...' if len(description) > 150 else ''}",
                    severity="success"
                )

            logger.info(f"AI Analyse abgeschlossen: {confidence:.0%} Confidence via {route['engine']}/{route['model']}")
            return result

        # Discord-Logger: Fehler
        if self.discord_logger:
            self.discord_logger.log_ai_learning(
                f"AI Analyse fehlgeschlagen\n"
                f"Alle Engines (Codex + Claude) ohne Ergebnis",
                severity="error"
            )

        logger.error("AI Analyse: Alle Engines fehlgeschlagen")
        return None

    async def generate_coordinated_plan(
        self,
        prompt: str,
        context: Dict,
    ) -> Optional[Dict]:
        """
        Generiert einen koordinierten Plan fuer mehrere Events (Batch).

        Args:
            prompt: Batch-Analyse-Prompt
            context: Kontext mit severity, batch_size etc.

        Returns:
            Dict mit Plan-Details oder None
        """
        severity = context.get('severity', 'HIGH')
        route = self.router.get_route(severity, 'analysis')

        # Koordinierte Plaene brauchen eigenes Schema (nicht fix_strategy)
        coordinated_schema = self.router._resolve_schema_path('coordinated_plan')
        if coordinated_schema:
            route['schema_path'] = coordinated_schema

        result = await self._execute_with_fallback(prompt, route)

        if result:
            logger.info(f"Koordinierter Plan erstellt: {result.get('description', 'N/A')[:100]}")
            if self.discord_logger:
                try:
                    self.discord_logger.log_orchestrator(
                        f"Koordinierter Plan erstellt\n"
                        f"Events: {context.get('batch_size', '?')} | Engine: {route['engine']}",
                        severity="info"
                    )
                except Exception:
                    pass  # log_orchestrator ist optional

        return result

    async def get_ai_analysis(
        self,
        prompt: str,
        context: str = "",
        use_critical_model: bool = False,
    ) -> Optional[str]:
        """
        Fuehrt eine Text-Analyse durch (kein strukturiertes JSON).

        Args:
            prompt: Analyse-Prompt
            context: Zusaetzlicher Kontext-String
            use_critical_model: True = Thinking-Modell verwenden

        Returns:
            Rohtext-Antwort oder None
        """
        model = 'thinking' if use_critical_model else 'standard'
        full_prompt = f"{prompt}\n\nKontext:\n{context}" if context else prompt

        # Primaer: Codex
        result = await self.codex.query_raw(full_prompt, model=model)
        if result:
            return result

        # Fallback: Claude
        logger.info("Codex Raw fehlgeschlagen, Fallback auf Claude")
        result = await self.claude.query_raw(full_prompt, model=model)
        return result

    async def verify_fix(
        self,
        fix_description: str,
        fix_commands: List[str],
        event: Dict,
    ) -> Optional[Dict]:
        """
        Verifiziert einen Fix-Vorschlag ueber Claude (Opus).

        Args:
            fix_description: Beschreibung des Fixes
            fix_commands: Liste der auszufuehrenden Befehle
            event: Das urspruengliche Security-Event

        Returns:
            Verifikations-Ergebnis als Dict oder None
        """
        commands_str = "\n".join(f"  - {cmd}" for cmd in fix_commands)

        prompt = (
            f"Verifiziere den folgenden Security-Fix:\n\n"
            f"Beschreibung: {fix_description}\n\n"
            f"Befehle:\n{commands_str}\n\n"
            f"Original-Event:\n{json.dumps(event, indent=2, default=str)}\n\n"
            f"Pruefe:\n"
            f"1. Ist der Fix sicher und korrekt?\n"
            f"2. Gibt es Risiken oder Nebenwirkungen?\n"
            f"3. Ist ein Rollback moeglich?\n"
            f"4. Wie hoch ist die Confidence (0.0 - 1.0)?"
        )

        route = self.router.get_route(event.get('severity', 'HIGH'), 'verify')

        result = await self.claude.query(
            prompt,
            model=route.get('model_class', 'thinking'),
            schema_path=route.get('schema_path'),
        )

        if result:
            logger.info(f"Fix-Verifikation: {result.get('confidence', 0):.0%} Confidence")

        return result

    async def run_analyst_session(
        self,
        prompt: str,
        timeout: int = 1800,
        max_turns: int = 25,
    ) -> Optional[Dict]:
        """
        Startet eine autonome Claude Code Session zur Server-Analyse.

        Die Session kann frei den Server explorieren (Logs, Docker, Configs, etc.)
        und liefert strukturierte Ergebnisse zurueck.

        Args:
            prompt: Analyse-Prompt mit Aufgabenbeschreibung
            timeout: Maximale Laufzeit in Sekunden (Default: 30 Min)
            max_turns: Maximale Anzahl an Tool-Aufrufen (Default: 25)

        Returns:
            Dict mit Session-Ergebnissen oder None bei Fehler
        """
        # Temp-Datei fuer strukturierten Output
        tmp_path = tempfile.mktemp(suffix='.json', prefix='analyst_')
        schema_path = os.path.join(
            os.path.dirname(__file__), '..', 'schemas', 'analyst_session.json'
        )

        # Schema-Inhalt laden fuer Prompt-Injection
        try:
            with open(schema_path, 'r', encoding='utf-8') as f:
                schema_content = f.read()
        except Exception as e:
            logger.error(f"Analyst-Schema nicht lesbar: {e}")
            return None

        # Prompt erweitern: Ergebnisse als JSON in Temp-Datei schreiben
        full_prompt = (
            f"{prompt}\n\n"
            f"--- AUSGABE-ANWEISUNG ---\n"
            f"Wenn du fertig bist, schreibe deine Ergebnisse als valides JSON "
            f"in die Datei: {tmp_path}\n"
            f"Das JSON MUSS diesem Schema entsprechen:\n"
            f"```json\n{schema_content}\n```\n"
            f"Nutze das Write-Tool um die Datei zu erstellen. "
            f"Kein Markdown, nur reines JSON."
        )

        # Erlaubte Tools — Bash-Prefixe + MCP-Tools + Kern-Tools
        # Format: Bash(command:*) mit Doppelpunkt (NICHT Leerzeichen!)
        allowed_tools = (
            # Bash-Befehle (kein rm, kein dd)
            'Bash(git:*),Bash(docker:*),Bash(ufw:*),Bash(systemctl:*),'
            'Bash(ss:*),Bash(who:*),Bash(df:*),Bash(free:*),Bash(ps:*),'
            'Bash(cat:*),Bash(ls:*),Bash(find:*),Bash(chmod:*),Bash(chown:*),'
            'Bash(apt:*),Bash(npm:*),Bash(go:*),Bash(curl:*),Bash(head:*),'
            'Bash(tail:*),Bash(wc:*),Bash(grep:*),Bash(trivy:*),Bash(cscli:*),'
            'Bash(aide:*),Bash(certbot:*),Bash(gh:*),Bash(sudo:*),'
            # Kern-Tools
            'Read,Glob,Grep,Write,Edit,ToolSearch,'
            # MCP: Docker (read-only)
            'mcp__docker__list-containers,mcp__docker__get-logs,'
            # MCP: Postgres (read-only!)
            'mcp__postgres-guildscout__execute_sql,'
            'mcp__postgres-guildscout__list_schemas,'
            'mcp__postgres-guildscout__list_objects,'
            'mcp__postgres-guildscout__get_object_details,'
            'mcp__postgres-guildscout__analyze_db_health,'
            'mcp__postgres-zerodox__execute_sql,'
            'mcp__postgres-zerodox__list_schemas,'
            'mcp__postgres-zerodox__list_objects,'
            'mcp__postgres-zerodox__get_object_details,'
            'mcp__postgres-zerodox__analyze_db_health,'
            # MCP: Redis (read-only)
            'mcp__redis__info,mcp__redis__scan_keys,mcp__redis__get,'
            'mcp__redis__hgetall,mcp__redis__lrange,mcp__redis__type,'
            'mcp__redis__dbsize,mcp__redis__client_list,'
            # MCP: GitHub (Issues + Read)
            'mcp__github__list_issues,mcp__github__search_issues,'
            'mcp__github__search_code,mcp__github__get_file_contents,'
            'mcp__github__issue_write,mcp__github__issue_read'
        )

        args = [
            self.claude.cli_path,
            '-p', full_prompt,
            '--model', 'claude-opus-4-6',
            '--max-turns', str(max_turns),
            '--output-format', 'text',
            '--verbose',
            '--allowedTools', allowed_tools,
        ]

        env = self.claude._get_clean_env()

        logger.info(
            f"Analyst-Session gestartet (Timeout: {timeout}s, Max-Turns: {max_turns})"
        )

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd='/home/cmdshadow',
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )

            stdout = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ''
            stderr = stderr_bytes.decode('utf-8', errors='replace') if stderr_bytes else ''

            if proc.returncode != 0:
                logger.error(
                    f"Analyst-Session fehlgeschlagen (rc={proc.returncode}): "
                    f"{stderr[:500]}"
                )
                return None

            # Ergebnis aus Temp-Datei lesen
            result = self._read_analyst_result(tmp_path, stdout)

            if result:
                findings_count = len(result.get('findings', []))
                knowledge_count = len(result.get('knowledge_updates', []))
                logger.info(
                    f"Analyst-Session abgeschlossen: "
                    f"{findings_count} Findings, {knowledge_count} Knowledge-Updates"
                )
            else:
                logger.warning("Analyst-Session: Kein strukturiertes Ergebnis erhalten")

            return result

        except asyncio.TimeoutError:
            logger.warning(f"Analyst-Session Timeout nach {timeout}s — versuche Teilergebnisse")
            if proc:
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass

            # Versuche trotzdem die Temp-Datei zu lesen (wurde evtl. schon geschrieben)
            result = self._read_analyst_result(tmp_path)
            if result:
                result['summary'] = f"[TIMEOUT] {result.get('summary', 'Session abgebrochen')}"
                findings_count = len(result.get('findings', []))
                logger.info(
                    f"Analyst-Session (Timeout): {findings_count} Findings aus Teilergebnis"
                )
            return result

        except Exception as e:
            logger.error(f"Analyst-Session Fehler: {e}", exc_info=True)
            return None

        finally:
            # Temp-Datei aufraeumen
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
                pass

    def _read_analyst_result(
        self,
        tmp_path: str,
        stdout: str = "",
    ) -> Optional[Dict]:
        """
        Liest das Analyst-Ergebnis aus Temp-Datei oder extrahiert es aus stdout.

        Args:
            tmp_path: Pfad zur Temp-Datei
            stdout: stdout-Output als Fallback

        Returns:
            Geparstes Dict oder None
        """
        # Primaer: Temp-Datei
        if os.path.exists(tmp_path):
            try:
                with open(tmp_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                logger.debug("Analyst-Ergebnis aus Temp-Datei gelesen")
                return data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Temp-Datei nicht parsbar: {e}")

        # Fallback: JSON aus stdout extrahieren
        if stdout:
            # Suche nach JSON-Objekt das mit {"summary" beginnt
            match = re.search(r'\{"summary".*', stdout, re.DOTALL)
            if match:
                json_str = match.group(0)
                # Finde das passende schliessende Bracket
                depth = 0
                end_idx = 0
                for i, ch in enumerate(json_str):
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            end_idx = i + 1
                            break

                if end_idx > 0:
                    try:
                        data = json.loads(json_str[:end_idx])
                        logger.debug("Analyst-Ergebnis aus stdout extrahiert")
                        return data
                    except json.JSONDecodeError:
                        logger.warning("JSON aus stdout nicht parsbar")

        return None

    # ------------------------------------------------------------------
    # Interne Methoden
    # ------------------------------------------------------------------

    async def _execute_with_fallback(
        self,
        prompt: str,
        route: dict,
    ) -> Optional[Dict]:
        """
        Fuehrt einen Query mit Primary-Engine aus, bei Fehler Fallback.

        Args:
            prompt: Der Analyse-Prompt
            route: Routing-Info (engine, model, model_class, schema_path)

        Returns:
            Ergebnis-Dict oder None
        """
        primary_engine = route.get('engine', 'codex')
        model_class = route.get('model_class', 'standard')
        schema_path = route.get('schema_path')

        # Primary Engine
        if primary_engine == 'codex':
            primary = self.codex
            fallback = self.claude
            primary_name = 'codex'
            fallback_name = 'claude'
        else:
            primary = self.claude
            fallback = self.codex
            primary_name = 'claude'
            fallback_name = 'codex'

        # Primary Versuch
        self.stats[f'{primary_name}_calls'] += 1
        result = await primary.query(
            prompt,
            model=model_class,
            schema_path=schema_path,
        )

        if result:
            self.stats[f'{primary_name}_success'] += 1
            return result

        # Primary fehlgeschlagen
        self.stats[f'{primary_name}_failures'] += 1
        logger.warning(f"{primary_name.capitalize()} fehlgeschlagen, Fallback auf {fallback_name.capitalize()}")

        # Fallback
        self.stats[f'{fallback_name}_calls'] += 1
        result = await fallback.query(
            prompt,
            model=model_class,
            schema_path=schema_path,
        )

        if result:
            self.stats[f'{fallback_name}_success'] += 1
            return result

        self.stats[f'{fallback_name}_failures'] += 1
        return None

    def _build_analysis_prompt(
        self,
        event: Dict,
        previous_attempts: List[Dict],
    ) -> str:
        """
        Baut einen detaillierten Analyse-Prompt fuer Security-Events.

        Args:
            event: Das Security-Event
            previous_attempts: Liste vorheriger fehlgeschlagener Versuche

        Returns:
            Fertiger Prompt-String
        """
        severity = event.get('severity', 'UNKNOWN')
        source = event.get('source', 'unknown')
        event_type = event.get('event_type', 'unknown')
        details = event.get('details', {})

        # Details formatieren
        details_str = json.dumps(details, indent=2, default=str) if details else "Keine Details verfuegbar"

        prompt_parts = [
            f"Du bist ein Security-Analyst fuer Linux-Server (Debian 12).",
            f"Analysiere das folgende Security-Event und erstelle eine Fix-Strategie.",
            f"",
            f"## Event-Details",
            f"- Quelle: {source}",
            f"- Typ: {event_type}",
            f"- Severity: {severity}",
            f"- Details:",
            f"```json",
            f"{details_str}",
            f"```",
        ]

        # RAG-Kontext vom ContextManager
        if self.context_manager:
            try:
                infra_context = self.context_manager.get_infrastructure_context()
                if infra_context:
                    prompt_parts.extend([
                        f"",
                        f"## Infrastruktur-Kontext",
                        f"{infra_context}",
                    ])
            except Exception as e:
                logger.debug(f"ContextManager Fehler (ignoriert): {e}")

        # Vorherige Versuche
        if previous_attempts:
            prompt_parts.extend([
                f"",
                f"## Vorherige Versuche (fehlgeschlagen)",
                f"Die folgenden Ansaetze wurden bereits versucht und sind fehlgeschlagen.",
                f"Finde einen ANDEREN Loesungsweg!",
                f"",
            ])
            for i, attempt in enumerate(previous_attempts, 1):
                strategy = attempt.get('strategy', {})
                desc = strategy.get('description', 'N/A') if isinstance(strategy, dict) else str(strategy)
                error = attempt.get('error', 'Unbekannt')
                prompt_parts.append(f"### Versuch {i}")
                prompt_parts.append(f"- Strategie: {desc}")
                prompt_parts.append(f"- Ergebnis: {attempt.get('result', 'failed')}")
                prompt_parts.append(f"- Fehler: {error}")
                prompt_parts.append("")

        prompt_parts.extend([
            f"",
            f"## Anforderungen an die Antwort",
            f"- Erstelle eine konkrete, ausfuehrbare Fix-Strategie",
            f"- Jeder Schritt muss einen konkreten Linux-Befehl enthalten",
            f"- Bewerte das Risiko jedes Schritts (low/medium/high)",
            f"- Gib eine Confidence-Bewertung (0.0-1.0) an",
            f"- Beruecksichtige Rollback-Moeglichkeiten",
        ])

        return "\n".join(prompt_parts)
