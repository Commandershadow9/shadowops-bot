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
import signal
import re
import tempfile
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, List, Tuple
from pathlib import Path

import jsonschema

logger = logging.getLogger('shadowops')

# Basis-Pfad zu den JSON-Schemas
SCHEMAS_DIR = Path(__file__).parent.parent / 'schemas'

# Schema-Cache: Datei-Pfad → geladenes Schema-Dict
_schema_cache: Dict[str, dict] = {}


# ============================================================================
# TOKEN USAGE PARSER (modul-lokal, pure, leicht testbar)
# ============================================================================

_ZERO_USAGE: Dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _parse_token_usage(stdout: str, stderr: str = "") -> Dict[str, int]:
    """Extrahiert Token-Verbrauch aus CLI-Output.

    Unterstuetzte Formate:
    1. Claude CLI `--output-format json`: `{"usage": {"input_tokens": X,
       "output_tokens": Y, "cache_creation_input_tokens": C, "cache_read_input_tokens": R}}`
       → input_tokens = X + C + R (Cache wird zur Input-Seite addiert, damit die
       Kosten stimmen), output_tokens = Y, total = summe.
    2. Codex CLI text mode: endet mit `tokens used\\n<number-with-optional-commas>`
       → nur Gesamtverbrauch bekannt, input/output bleiben 0.

    Gibt bei Fehlern / unbekanntem Format `_ZERO_USAGE`-Kopie zurueck. Nie `None` —
    die aufrufende Logik schreibt das Ergebnis direkt in eine INT-Spalte.
    """
    if not stdout and not stderr:
        return dict(_ZERO_USAGE)

    # 1. Versuch: Claude JSON-Output
    for source in (stdout, stderr):
        if not source:
            continue
        text = source.strip()
        if not text.startswith("{"):
            continue
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            continue
        try:
            input_base = int(usage.get("input_tokens", 0) or 0)
            cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
            cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
            output = int(usage.get("output_tokens", 0) or 0)
        except (TypeError, ValueError):
            continue
        input_total = input_base + cache_creation + cache_read
        return {
            "input_tokens": input_total,
            "output_tokens": output,
            "total_tokens": input_total + output,
        }

    # 2. Versuch: Codex text-Modus — "tokens used\n<number>"
    codex_re = re.compile(r"tokens\s+used\s*\n\s*([\d,]+)", re.IGNORECASE)
    for source in (stdout, stderr):
        if not source:
            continue
        match = codex_re.search(source)
        if match:
            try:
                total = int(match.group(1).replace(",", ""))
            except ValueError:
                continue
            return {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": total,
            }

    return dict(_ZERO_USAGE)


# ============================================================================
# CODEX CLI PROVIDER
# ============================================================================

class CodexProvider:
    """
    Provider fuer OpenAI Codex CLI.

    CLI-Aufruf (via create_subprocess_exec, kein Shell):
        codex  exec  -s  workspace-write  -m  MODEL  PROMPT
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
            'codex', 'exec',
            '--skip-git-repo-check',
            '-c', 'mcp_servers={}',
            '-s', 'workspace-write',
            '-m', resolved_model,
        ]

        if schema_path:
            args.extend(['--output-schema', str(schema_path)])

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=prompt.encode('utf-8')),
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
            if proc:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    await proc.wait()
                except Exception:
                    pass
            return None
        except FileNotFoundError:
            logger.error("Codex CLI: 'codex' Befehl nicht gefunden")
            return None
        except Exception as e:
            logger.error(f"Codex CLI: Unerwarteter Fehler: {e}")
            if proc:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    await proc.wait()
                except Exception:
                    pass
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
            'codex', 'exec',
            '--skip-git-repo-check',
            '-c', 'mcp_servers={}',
            '-s', 'workspace-write',
            '-m', resolved_model,
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=prompt.encode('utf-8')),
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
            if proc:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    await proc.wait()
                except Exception:
                    pass
            return None
        except Exception as e:
            logger.error(f"Codex CLI Raw: Fehler: {e}")
            if proc:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    await proc.wait()
                except Exception:
                    pass
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
            # Markdown-Codefences strippen (Claude wrapped JSON oft in ```json ... ```)
            stripped = re.sub(r'^```(?:json)?\s*\n?', '', inner_text.strip())
            stripped = re.sub(r'\n?```\s*$', '', stripped).strip()
            try:
                inner = json.loads(stripped)
                if isinstance(inner, dict):
                    return inner
            except (json.JSONDecodeError, ValueError):
                pass
            # Fallback: JSON-Objekt aus dem result-String extrahieren
            match = re.search(r'\{.*\}', inner_text, re.DOTALL)
            if match:
                try:
                    inner = json.loads(match.group(0))
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
            '-p', '-',
            '--output-format', 'json',
            '--model', resolved_model,
            '--max-turns', '1',
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=full_prompt.encode('utf-8')),
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
            if proc:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    await proc.wait()
                except Exception:
                    pass
            return None
        except FileNotFoundError:
            logger.error(f"Claude CLI: '{self.cli_path}' nicht gefunden")
            return None
        except Exception as e:
            logger.error(f"Claude CLI: Unerwarteter Fehler: {e}")
            if proc:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    await proc.wait()
                except Exception:
                    pass
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
            '-p', '-',
            '--output-format', 'text',
            '--model', resolved_model,
            '--max-turns', '1',
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=prompt.encode('utf-8')),
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
            if proc:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    await proc.wait()
                except Exception:
                    pass
            return None
        except Exception as e:
            logger.error(f"Claude CLI Raw: Fehler: {e}")
            if proc:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    await proc.wait()
                except Exception:
                    pass
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

    async def query_raw_with_usage(
        self,
        prompt: str,
        model: str = 'standard',
        timeout: Optional[int] = None,
    ) -> Tuple[Optional[str], Dict[str, int]]:
        """Wie query_raw, aber mit `--output-format json` um Usage-Metriken zu ernten.

        Returns:
            (text, usage) — text ist der vom Modell erzeugte Response-String
            (aus dem JSON-"result"-Feld extrahiert), usage ist ein Dict
            `{input_tokens, output_tokens, total_tokens}`. Bei Fehlern ist
            text None, usage = {0, 0, 0}.

        Wird von `AIEngine.review_pr` verwendet, damit Jules-Reviews den
        tatsaechlichen Token-Verbrauch in die DB schreiben koennen.
        """
        resolved_model = self._resolve_model(model)
        effective_timeout = timeout or self.timeout
        env = self._get_clean_env()

        args = [
            self.cli_path,
            '-p', '-',
            '--output-format', 'json',
            '--model', resolved_model,
            '--max-turns', '1',
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=prompt.encode('utf-8')),
                timeout=effective_timeout,
            )

            stdout = stdout_bytes.decode('utf-8', errors='replace').strip()
            stderr = stderr_bytes.decode('utf-8', errors='replace').strip()

            if proc.returncode != 0:
                logger.warning(
                    f"Claude CLI Usage-Raw Fehler (Exit {proc.returncode}): {stderr[:200]}"
                )
                return None, dict(_ZERO_USAGE)

            if not stdout:
                logger.warning("Claude CLI Usage-Raw: Leere Antwort erhalten")
                return None, dict(_ZERO_USAGE)

            usage = _parse_token_usage(stdout, stderr)

            # Result-Text aus dem JSON-Wrapper extrahieren
            text: Optional[str] = None
            try:
                payload = json.loads(stdout)
                if isinstance(payload, dict):
                    result_val = payload.get("result")
                    if isinstance(result_val, str):
                        text = result_val
            except (json.JSONDecodeError, ValueError):
                # Fallback: gib stdout roh zurueck, Parser oben hat Usage schon
                text = stdout

            return text, usage

        except asyncio.TimeoutError:
            logger.error(f"Claude CLI Usage-Raw: Timeout nach {effective_timeout}s")
            return None, dict(_ZERO_USAGE)
        except Exception as e:
            logger.error(f"Claude CLI Usage-Raw: Fehler: {e}")
            return None, dict(_ZERO_USAGE)


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

        # Globales Token-Budget (über alle AI-Calls des Tages)
        from datetime import datetime, timezone
        self._daily_max_tokens = ai_cfg.get('daily_token_budget', 100000)
        self._daily_tokens_used = 0
        self._token_budget_date = datetime.now(timezone.utc).date()

        # Codex-Quota-Cache: Überspringt Codex wenn Quota erschöpft
        # Wird gesetzt wenn "usage limit" Fehler erkannt wird
        self._codex_quota_exhausted_until: float = 0.0
        self._claude_quota_exhausted_until: float = 0.0

        # Stats-Tracking
        self.stats = {
            'codex_calls': 0,
            'codex_success': 0,
            'codex_failures': 0,
            'claude_calls': 0,
            'claude_success': 0,
            'claude_failures': 0,
        }

        # Zuletzt erfolgreich genutzte Engine (codex | claude | None)
        # Wird von Patch-Notes-Pipeline fuer Metriken ausgelesen.
        self._last_engine: Optional[str] = None

        # Token-Verbrauch der letzten AI-Operation. Wird nach jedem erfolgreichen
        # CLI-Call aktualisiert. Jules liest dies nach review_pr, der Scan-Agent
        # akkumuliert pro Session. Nicht `None` — immer ein Dict.
        self._last_token_usage: Dict[str, int] = dict(_ZERO_USAGE)

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
    # Token-Budget-Management
    # ------------------------------------------------------------------

    def _track_tokens(self, prompt: str) -> None:
        """Trackt geschätzten Token-Verbrauch."""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date()
        if today != self._token_budget_date:
            self._daily_tokens_used = 0
            self._token_budget_date = today
        estimated = len(prompt) // 4
        self._daily_tokens_used += estimated

    def is_budget_exhausted(self) -> bool:
        """Prüft ob das tägliche Token-Budget erschöpft ist."""
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date()
        if today != self._token_budget_date:
            return False
        return self._daily_tokens_used >= self._daily_max_tokens

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

    async def generate_structured_patch_notes(
        self,
        prompt: str,
        use_critical_model: bool = True,
    ) -> Optional[Dict]:
        """
        Generiert strukturierte Patch Notes mit dem patch_notes.json Schema.

        Nutzt _execute_with_fallback() fuer Codex (--output-schema) / Claude (Schema im Prompt).
        Liefert ein Dict mit title, tldr, discord_highlights, web_content, changes, stats etc.

        Args:
            prompt: Der vollstaendige Patch-Notes-Prompt
            use_critical_model: Ignoriert — Structured Output nutzt immer standard

        Returns:
            Strukturiertes Dict oder None bei Fehler
        """
        # Structured Output (--output-schema) braucht standard-Modell,
        # Thinking-Modelle (o3) unterstuetzen kein --output-schema
        model_class = 'standard'

        schema_path = self.router._resolve_schema_path('patch_notes')

        # Patch Notes brauchen mehr Zeit als normale Queries (langer Prompt + Schema)
        pn_timeout = max(self.codex.timeout_thinking, 300)

        route = {
            'engine': 'codex',
            'model': self.router._get_engine_models('codex').get(model_class, model_class),
            'model_class': model_class,
            'schema_path': schema_path,
            'timeout': pn_timeout,
        }

        result = await self._execute_with_fallback(prompt, route)

        if result and isinstance(result, dict):
            # Validierung: Kern-Felder fuer Discord + Web muessen vorhanden sein
            has_discord = result.get('title') and result.get('tldr') and result.get('discord_highlights')
            has_web = result.get('web_content')
            if has_discord and has_web:
                # Schema-Validierung (soft — warnt bei Abweichungen, blockiert nicht)
                try:
                    schema_file = SCHEMAS_DIR / 'patch_notes.json'
                    if schema_file not in _schema_cache:
                        with open(schema_file) as f:
                            _schema_cache[schema_file] = json.load(f)
                    jsonschema.validate(result, _schema_cache[schema_file])
                except jsonschema.ValidationError as ve:
                    field_path = '/'.join(str(x) for x in ve.path) if ve.path else 'root'
                    logger.warning("Patch Notes Schema-Warnung: %s (Feld: %s)", ve.message[:150], field_path)
                except Exception:
                    pass  # Schema-Datei nicht gefunden — kein Blocker
                logger.info(f"✅ Strukturierte Patch Notes generiert: {result.get('title')}")
                return result
            else:
                missing = []
                if not result.get('title'):
                    missing.append('title')
                if not result.get('tldr'):
                    missing.append('tldr')
                if not result.get('discord_highlights'):
                    missing.append('discord_highlights')
                if not result.get('web_content'):
                    missing.append('web_content')
                logger.warning(f"⚠️ Strukturiertes Ergebnis unvollstaendig (fehlend: {', '.join(missing)}), Fallback auf Raw-Text")
                return None

        return None

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
            self._last_engine = 'codex'
            return result

        # Fallback: Claude
        logger.info("Codex Raw fehlgeschlagen, Fallback auf Claude")
        result = await self.claude.query_raw(full_prompt, model=model)
        if result:
            self._last_engine = 'claude'
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

    async def review_pr(
        self,
        *,
        diff: str,
        finding_context: Dict[str, Any],
        project: str,
        iteration: int,
        project_knowledge: List[str],
        few_shot_examples: List[Dict[str, Any]],
        max_diff_chars: int = 8000,
        prompt_override: Optional[str] = None,
        model_preference: Optional[Tuple[str, str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Strukturiertes PR-Review via Claude (Thinking-Modell).

        Baut den Review-Prompt ueber jules_review_prompt, ruft Claude auf,
        validiert das Ergebnis gegen das jules_review-Schema und ueberschreibt
        das Verdict deterministisch via compute_verdict().

        Args:
            prompt_override: Optional vorgefertigter Prompt vom Adapter
                (SEO/Codex build_prompt()). Wenn None, wird der Jules-Prompt
                aus finding_context + few_shot gebaut (Legacy-Pfad).
            model_preference: Optional (primary, fallback) vom Adapter.
                Wenn None, wird die interne Security-Keyword-Heuristik genutzt.

        Returns:
            Validiertes Review-Dict oder None bei Fehler.
        """
        from integrations.github_integration.jules_review_prompt import (
            build_review_prompt, compute_verdict,
        )

        if prompt_override is not None:
            prompt = prompt_override
        else:
            prompt = build_review_prompt(
                finding=finding_context, project=project, diff=diff,
                iteration=iteration, project_knowledge=project_knowledge,
                few_shot_examples=few_shot_examples, max_diff_chars=max_diff_chars,
            )

        # Modell-Wahl: explizite Praeferenz vom Adapter hat Vorrang
        diff_len = len(diff)
        if model_preference is not None:
            primary, fallback = model_preference
        else:
            # Legacy-Heuristik: Opus fuer Security+komplexe PRs, Sonnet sonst
            is_security = any(
                k in (finding_context.get("category", "") + finding_context.get("title", "")).lower()
                for k in ("xss", "cve", "injection", "dos", "security", "auth", "csrf")
            )
            is_complex = diff_len > 3000 or is_security
            primary = "thinking" if is_complex else "standard"
            fallback = "standard" if is_complex else "thinking"
        timeout_s = 180 if primary == "thinking" else 120

        # Usage-Tracking initial leer — wird nach erfolgreichem Call befuellt.
        # Falls beide Modelle scheitern, bleibt 0 (kein Tokenverbrauch gemeldet).
        self._last_token_usage = dict(_ZERO_USAGE)

        raw = None
        for model_class, t in ((primary, timeout_s), (fallback, 120)):
            try:
                # query_raw_with_usage nutzt --output-format json, damit wir
                # tokens_consumed fuer jules_pr_reviews aus dem usage-Block holen
                # koennen. Text kommt aus dem "result"-Feld wie bei query_raw.
                raw, usage = await self.claude.query_raw_with_usage(
                    prompt, model=model_class, timeout=t,
                )
                if raw:
                    self._last_token_usage = usage
                    logger.info(
                        f"[jules] Claude-Response erhalten "
                        f"(model={model_class}, {len(raw)} chars, "
                        f"tokens={usage.get('total_tokens', 0)})"
                    )
                    break
                logger.warning(f"[jules] Claude empty response (model={model_class}), Fallback")
            except Exception as e:
                logger.warning(f"[jules] Claude-Call failed (model={model_class}): {e}")

        if not raw:
            logger.error("[jules] Claude-Call komplett fehlgeschlagen (beide Modelle)")
            return None

        clean = raw.strip()
        # Markdown-Fences entfernen
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        # JSON extrahieren — Claude fügt manchmal Text vor/nach dem JSON ein
        review = None
        try:
            review = json.loads(clean)
        except json.JSONDecodeError:
            # Versuche nur den JSON-Block zu finden ({...})
            start = clean.find("{")
            if start >= 0:
                # Finde die passende schließende Klammer
                depth = 0
                for i, c in enumerate(clean[start:], start):
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            try:
                                review = json.loads(clean[start:i + 1])
                                logger.info(f"[jules] JSON extrahiert aus Position {start}-{i+1}")
                            except json.JSONDecodeError:
                                pass
                            break
            if review is None:
                logger.error(f"[jules] JSON parse failed, raw[:300]={clean[:300]!r}")
                return None

        schema_path = Path(__file__).parent.parent / "schemas" / "jules_review.json"
        try:
            schema = json.loads(schema_path.read_text())
            jsonschema.validate(review, schema)
        except jsonschema.ValidationError as e:
            logger.error(f"[jules] Schema validation failed: {e.message}")
            return None
        except FileNotFoundError:
            logger.error(f"[jules] Schema not found at {schema_path}")
            return None

        review["verdict"] = compute_verdict(review)

        logger.info(
            f"[jules] review ok: verdict={review['verdict']} "
            f"blockers={len(review['blockers'])} "
            f"suggestions={len(review['suggestions'])} "
            f"nits={len(review['nits'])} "
            f"in_scope={review['scope_check']['in_scope']}"
        )
        return review

    async def run_analyst_session(
        self,
        prompt: str,
        timeout: int = 1800,
        max_turns: int = 25,
        codex_model: str = 'gpt-5.3-codex',
        claude_model: str = 'claude-opus-4-6',
    ) -> Optional[Dict]:
        """
        Startet eine autonome Analyst-Session (Codex primaer, Claude Fallback).

        Nutzt asyncio.create_subprocess_exec (NICHT shell=True) fuer sichere
        Prozesserzeugung ohne Command-Injection-Risiko.

        Args:
            prompt: Analyse-Prompt mit Aufgabenbeschreibung
            timeout: Maximale Laufzeit fuer Claude-Fallback (Default: 30 Min)
            max_turns: Maximale Tool-Aufrufe fuer Claude-Fallback (Default: 25)
            codex_model: Codex-Modell (Default: gpt-5.3-codex)
            claude_model: Claude-Fallback-Modell (Default: claude-opus-4-6)

        Returns:
            Dict mit Session-Ergebnissen (inkl. '_provider' Key) oder None
        """
        schema_path = os.path.join(
            os.path.dirname(__file__), '..', 'schemas', 'analyst_session.json'
        )

        # 1. Primaer: Codex (überspringen wenn Quota erschöpft oder explizit deaktiviert)
        if not codex_model:
            logger.info("Analyst-Session: Codex deaktiviert — direkt Claude (%s)", claude_model)
        elif time.time() < self._codex_quota_exhausted_until:
            remaining_h = (self._codex_quota_exhausted_until - time.time()) / 3600
            logger.info(
                "Analyst-Session: Codex-Quota erschöpft (noch %.1fh) — direkt Claude (%s)",
                remaining_h, claude_model,
            )
        else:
            logger.info("Analyst-Session: Versuche Codex (%s)", codex_model)
            result = await self._run_analyst_codex(prompt, schema_path, codex_model)
            if result:
                result['_provider'] = 'codex'
                result['_model'] = codex_model
                return result
            logger.info("Codex-Analyst ohne Ergebnis — Fallback auf Claude (%s)", claude_model)

        # 2. Fallback: Claude
        self.stats['codex_failures'] = self.stats.get('codex_failures', 0) + 1
        if self.is_claude_quota_exhausted():
            remaining_min = max(1, int((self._claude_quota_exhausted_until - time.time()) / 60))
            logger.info(
                "Analyst-Session: Claude-Quota erschöpft (noch %d Min) — Claude uebersprungen",
                remaining_min,
            )
            result = None
        else:
            result = await self._run_analyst_claude(
                prompt, schema_path, claude_model, timeout, max_turns,
            )
        if result:
            result['_provider'] = 'claude'
            result['_model'] = claude_model
            return result

        self.stats['claude_failures'] = self.stats.get('claude_failures', 0) + 1
        logger.error("Analyst-Session: Beide Engines (Codex + Claude) ohne Ergebnis")
        return None

    def is_claude_quota_exhausted(self) -> bool:
        return time.time() < self._claude_quota_exhausted_until

    def _extract_quota_reset_timestamp(
        self,
        message: str,
        default_seconds: int = 3600,
    ) -> Optional[float]:
        text = (message or "").strip()
        if not text:
            return None

        lower = text.lower()
        quota_markers = (
            'hit your limit',
            'usage limit',
            'rate limit',
            'quota',
            'overloaded',
            'too many requests',
        )
        if not any(marker in lower for marker in quota_markers):
            return None

        match_12h = re.search(
            r'(?:resets?|try again(?: at)?)\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b',
            lower,
        )
        if match_12h:
            hour = int(match_12h.group(1))
            minute = int(match_12h.group(2) or 0)
            meridiem = match_12h.group(3)
            if meridiem == 'pm' and hour != 12:
                hour += 12
            elif meridiem == 'am' and hour == 12:
                hour = 0

            now = datetime.now().astimezone()
            reset_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if reset_at <= now:
                reset_at += timedelta(days=1)
            return reset_at.timestamp()

        match_24h = re.search(
            r'(?:resets?|try again(?: at)?)\s+(?:at\s+)?(\d{1,2}):(\d{2})\b',
            lower,
        )
        if match_24h:
            hour = int(match_24h.group(1))
            minute = int(match_24h.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                now = datetime.now().astimezone()
                reset_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if reset_at <= now:
                    reset_at += timedelta(days=1)
                return reset_at.timestamp()

        return time.time() + default_seconds

    async def run_fix_session(
        self,
        prompt: str,
        timeout: int = 7200,
        max_turns: int = 200,
        model: str = 'claude-sonnet-4-6',
    ) -> Optional[Dict]:
        """Fix-Session: Arbeitet Findings aus der DB ab.

        Direkt via Claude CLI (kein Codex — Fixes brauchen Tool-Aufrufe).
        Mehr Turns und Timeout als Analyse-Session.
        """

        tmp_path = tempfile.mktemp(
            suffix='_fix_results.json',
            prefix='analyst_fix_',
            dir='/tmp',
        )

        schema_content = json.dumps({
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "finding_id": {"type": "integer"},
                            "action": {"type": "string", "enum": ["fixed", "pr_created"]},
                            "details": {"type": "string"}
                        },
                        "required": ["finding_id", "action", "details"]
                    }
                },
                "summary": {"type": "string"}
            },
            "required": ["results", "summary"]
        }, indent=2)

        full_prompt = (
            f"{prompt}\n\n"
            f"--- AUSGABE ---\n"
            f"Schreibe deine Ergebnisse als JSON in: {tmp_path}\n"
            f"Schema:\n```json\n{schema_content}\n```\n"
            f"Nutze Write um die Datei zu erstellen. PFLICHT fuer JEDES Finding."
        )

        # Fix-Session braucht Schreibrechte (chmod, config-edits, git, gh)
        allowed_tools = (
            'Bash(git:*),Bash(docker:*),Bash(ufw:*),Bash(systemctl:*),'
            'Bash(ss:*),Bash(df:*),Bash(free:*),Bash(ps:*),'
            'Bash(cat:*),Bash(ls:*),Bash(find:*),Bash(chmod:*),Bash(chown:*),'
            'Bash(apt:*),Bash(npm:*),Bash(go:*),Bash(curl:*),Bash(head:*),'
            'Bash(tail:*),Bash(wc:*),Bash(grep:*),Bash(trivy:*),Bash(cscli:*),'
            'Bash(fail2ban-client:*),Bash(aide:*),Bash(gh:*),Bash(sudo:*),'
            'Bash(cp:*),Bash(mv:*),Bash(mkdir:*),Bash(sed:*),'
            'Read,Glob,Grep,Write,Edit'
        )

        args = [
            self.claude.cli_path,
            '-p', '-',
            '--model', model,
            '--max-turns', str(max_turns),
            '--output-format', 'text',
            '--verbose',
            '--dangerously-skip-permissions',
            '--allowed-tools', allowed_tools,
        ]

        env = self.claude._get_clean_env()

        logger.info(
            "Fix-Session gestartet (Modell: %s, Timeout: %ds, Max-Turns: %d)",
            model, timeout, max_turns,
        )

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd='/home/cmdshadow',
                start_new_session=True,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=full_prompt.encode('utf-8')),
                timeout=timeout,
            )

            stdout = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ''
            stderr = stderr_bytes.decode('utf-8', errors='replace') if stderr_bytes else ''

            if proc.returncode != 0:
                logger.error("Fix-Session fehlgeschlagen (rc=%d): %s", proc.returncode, stderr[-500:])
                return None

            result = self._read_analyst_result(tmp_path, stdout)

            if result:
                results_count = len(result.get('results', []))
                fixed_count = sum(1 for r in result.get('results', []) if r.get('action') == 'fixed')
                pr_count = sum(1 for r in result.get('results', []) if r.get('action') == 'pr_created')
                logger.info(
                    "Fix-Session erfolgreich: %d Ergebnisse (%d fixed, %d PRs)",
                    results_count, fixed_count, pr_count,
                )
            else:
                logger.warning(
                    "Fix-Session: Kein strukturiertes Ergebnis (stdout=%d Bytes)",
                    len(stdout),
                )

            return result

        except asyncio.TimeoutError:
            logger.warning("Fix-Session: Timeout nach %ds — versuche Teilergebnisse zu retten", timeout)
            if proc:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    await proc.wait()
                except Exception:
                    pass
            # Teilergebnisse retten — Claude hat möglicherweise schon Findings gefixt
            partial = self._read_analyst_result(tmp_path)
            if partial:
                count = len(partial.get('results', []))
                logger.info("Fix-Session: %d Teilergebnisse gerettet trotz Timeout", count)
            return partial
        except Exception as e:
            logger.error("Fix-Session Fehler: %s", e, exc_info=True)
            # Auch bei Exceptions Teilergebnisse versuchen
            return self._read_analyst_result(tmp_path)
        finally:
            # Temp-Datei NICHT sofort löschen — erst nach erfolgreicher Verarbeitung
            # Aufräumen passiert beim nächsten Lauf oder durch /tmp Cleanup
            pass

    async def _run_analyst_codex(
        self,
        prompt: str,
        schema_path: str,
        model: str = 'gpt-5.3-codex',
        timeout: int = 900,
    ) -> Optional[Dict]:
        """Analyst-Session via Codex CLI (primaer).

        Verwendet create_subprocess_exec (kein Shell) mit fester Argumentliste.
        --dangerously-bypass-approvals-and-sandbox: Voller System-Zugriff (sudo, Docker, UFW etc.)
        damit der Security-Scan dieselbe Tiefe wie Claude erreicht.
        """
        env = self.codex._get_clean_env()
        fd, tmp_path = tempfile.mkstemp(suffix='.json', prefix='analyst_codex_')
        os.close(fd)

        # Prompt via stdin (ARG_MAX Limit bei grossen Prompts vermeiden)
        # -c mcp_servers={}: Keine MCP-Server laden (schneller, keine Auth-Fehler)
        # --dangerously-bypass-approvals-and-sandbox: Voller Zugriff fuer Security-Scans
        args = [
            'codex', 'exec',
            '--skip-git-repo-check',
            '-c', 'mcp_servers={}',
            '-s', 'workspace-write',
            '-m', model,
            '--output-schema', schema_path,
            '-o', tmp_path,
        ]

        logger.info("Codex-Analyst gestartet (Modell: %s, Timeout: %ds)", model, timeout)
        first_attempt = True

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd='/home/cmdshadow',
                start_new_session=True,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=prompt.encode('utf-8')), timeout=timeout
            )

            stdout = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ''
            stderr = stderr_bytes.decode('utf-8', errors='replace') if stderr_bytes else ''
            combined_output = "\n".join(
                part.strip() for part in (stderr, stdout) if part and part.strip()
            )

            if proc.returncode != 0:
                quota_reset_at = self._extract_quota_reset_timestamp(combined_output, default_seconds=6 * 3600)
                if quota_reset_at is not None:
                    self._codex_quota_exhausted_until = max(
                        self._codex_quota_exhausted_until,
                        quota_reset_at,
                    )
                    logger.error(
                        "Codex-Analyst: API-Quota erreicht: %s",
                        (combined_output or '(leer)')[-1500:],
                    )
                    return self._read_analyst_result(tmp_path, stdout)

                # Falls --skip-git-repo-check nicht unterstuetzt: Retry ohne Flag
                if first_attempt and 'skip-git-repo-check' in combined_output:
                    logger.info("--skip-git-repo-check nicht unterstuetzt, Retry ohne Flag")
                    first_attempt = False
                    args = [a for a in args if a != '--skip-git-repo-check']
                    proc = await asyncio.create_subprocess_exec(
                        *args,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        env=env,
                        cwd='/home/cmdshadow',
                        start_new_session=True,
                    )
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(input=prompt.encode('utf-8')), timeout=timeout
                    )
                    stdout = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ''
                    stderr = stderr_bytes.decode('utf-8', errors='replace') if stderr_bytes else ''
                    combined_output = "\n".join(
                        part.strip() for part in (stderr, stdout) if part and part.strip()
                    )
                    if proc.returncode != 0:
                        logger.warning(
                            "Codex-Analyst fehlgeschlagen (rc=%d, ohne Flag): %s",
                            proc.returncode, (combined_output or '(leer)')[-1500:],
                        )
                        return self._read_analyst_result(tmp_path, stdout)
                else:
                    logger.warning(
                        "Codex-Analyst fehlgeschlagen (rc=%d): %s",
                        proc.returncode, (combined_output or '(leer)')[-1500:],
                    )
                    return self._read_analyst_result(tmp_path, stdout)

            self._codex_quota_exhausted_until = 0.0
            # Token-Verbrauch aus Codex-Output extrahieren ("tokens used\n<n>")
            self._last_token_usage = _parse_token_usage(stdout, stderr)
            result = self._read_analyst_result(tmp_path, stdout) or self.codex._extract_json(stdout)
            if result and ('summary' in result or 'findings' in result):
                findings_count = len(result.get('findings', []))
                knowledge_count = len(result.get('knowledge_updates', []))
                logger.info(
                    "Codex-Analyst erfolgreich: %d Findings, %d Knowledge-Updates, tokens=%d",
                    findings_count, knowledge_count,
                    self._last_token_usage.get('total_tokens', 0),
                )
                self.stats['codex_success'] = self.stats.get('codex_success', 0) + 1
                return result

            logger.warning(
                "Codex-Analyst: Output kein gueltiges Analyst-Schema "
                "(stdout=%d Bytes, tmp_exists=%s)",
                len(stdout), os.path.exists(tmp_path),
            )
            return None

        except asyncio.TimeoutError:
            logger.warning("Codex-Analyst: Timeout nach %ds", timeout)
            if proc:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    await proc.wait()
                except Exception:
                    pass
            return None

        except FileNotFoundError:
            logger.error("Codex CLI nicht gefunden — ist 'codex' installiert?")
            return None

        except Exception as e:
            logger.error("Codex-Analyst Fehler: %s", e, exc_info=True)
            return None
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
                pass

    async def _run_analyst_claude(
        self,
        prompt: str,
        schema_path: str,
        model: str = 'claude-opus-4-6',
        timeout: int = 1800,
        max_turns: int = 25,
    ) -> Optional[Dict]:
        """Analyst-Session via Claude CLI (Fallback).

        Verwendet create_subprocess_exec (kein Shell) mit fester Argumentliste.
        """
        fd, tmp_path = tempfile.mkstemp(suffix='.json', prefix='analyst_')
        os.close(fd)  # Claude schreibt selbst in die Datei

        # Schema-Inhalt laden fuer Prompt-Injection
        try:
            with open(schema_path, 'r', encoding='utf-8') as f:
                schema_content = f.read()
        except Exception as e:
            logger.error("Analyst-Schema nicht lesbar: %s", e)
            return None

        # Prompt erweitern: Ergebnisse als JSON in Temp-Datei schreiben
        full_prompt = (
            f"{prompt}\n\n"
            f"--- KRITISCHE AUSGABE-ANWEISUNG (MUSS BEFOLGT WERDEN) ---\n"
            f"Du hast maximal {max_turns} Tool-Aufrufe. Teile sie so ein:\n"
            f"- Verwende MAXIMAL die Haelfte fuer Untersuchungen\n"
            f"- Schreibe die Ergebnisse FRUEHZEITIG in die Datei\n\n"
            f"PFLICHT: Schreibe deine Ergebnisse als valides JSON "
            f"in die Datei: {tmp_path}\n"
            f"Das JSON MUSS diesem Schema entsprechen:\n"
            f"```json\n{schema_content}\n```\n"
            f"Nutze das Write-Tool um die Datei zu erstellen. "
            f"Kein Markdown, nur reines JSON.\n\n"
            f"WICHTIG: Auch wenn du nicht alle Bereiche untersuchen konntest, "
            f"MUSST du die bisherigen Ergebnisse in die Datei schreiben. "
            f"Eine unvollstaendige Analyse mit geschriebener Datei ist BESSER "
            f"als eine gruendliche Analyse ohne Ergebnis-Datei!"
        )

        # Erlaubte Tools — Bash-Prefixe + Kern-Tools (keine MCPs noetig)
        # Analyst = reine Analyse (read-only Bash + Write für Ergebnis-Datei)
        allowed_tools = (
            'Bash(docker:*),Bash(systemctl:*),Bash(ss:*),Bash(df:*),Bash(free:*),'
            'Bash(ps:*),Bash(cat:*),Bash(ls:*),Bash(find:*),Bash(head:*),'
            'Bash(tail:*),Bash(wc:*),Bash(grep:*),Bash(trivy:*),Bash(cscli:*),'
            'Bash(fail2ban-client:*),Bash(aide:*),Bash(ufw:*),Bash(who:*),'
            'Bash(curl:*),Bash(sudo:*),Bash(git:*),'
            'Read,Glob,Grep,Write'
        )

        # Prompt via stdin (ARG_MAX), skip-permissions (damit Tools ohne Approval laufen)
        # --allowed-tools: Read-only Bash + Write (nur für Ergebnis-Datei)
        # --output-format json: liefert usage-Block fuer Token-Tracking (Text-Mode hat keinen)
        args = [
            self.claude.cli_path,
            '-p', '-',
            '--model', model,
            '--max-turns', str(max_turns),
            '--output-format', 'json',
            '--verbose',
            '--dangerously-skip-permissions',
            '--allowed-tools', allowed_tools,
        ]

        env = self.claude._get_clean_env()

        logger.info(
            "Claude-Analyst gestartet (Modell: %s, Timeout: %ds, Max-Turns: %d)",
            model, timeout, max_turns,
        )

        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd='/home/cmdshadow',
                start_new_session=True,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=full_prompt.encode('utf-8')),
                timeout=timeout,
            )

            stdout = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else ''
            stderr = stderr_bytes.decode('utf-8', errors='replace') if stderr_bytes else ''
            combined_output = "\n".join(
                part.strip() for part in (stderr, stdout) if part and part.strip()
            )

            if proc.returncode != 0:
                quota_reset_at = self._extract_quota_reset_timestamp(combined_output, default_seconds=3600)
                if quota_reset_at is not None:
                    self._claude_quota_exhausted_until = max(
                        self._claude_quota_exhausted_until,
                        quota_reset_at,
                    )
                    logger.error(
                        "Claude-Analyst: API-Limit erreicht: %s",
                        (combined_output or '(leer)')[-1500:],
                    )
                else:
                    logger.error(
                        "Claude-Analyst fehlgeschlagen (rc=%d): %s",
                        proc.returncode, (combined_output or '(leer)')[-1500:],
                    )
                return self._read_analyst_result(tmp_path, stdout)

            self._claude_quota_exhausted_until = 0.0
            # Token-Verbrauch aus Claude-JSON-Output (usage-Block) extrahieren
            self._last_token_usage = _parse_token_usage(stdout, stderr)
            result = self._read_analyst_result(tmp_path, stdout)

            if result:
                findings_count = len(result.get('findings', []))
                knowledge_count = len(result.get('knowledge_updates', []))
                logger.info(
                    "Claude-Analyst erfolgreich: %d Findings, %d Knowledge-Updates, tokens=%d",
                    findings_count, knowledge_count,
                    self._last_token_usage.get('total_tokens', 0),
                )
                self.stats['claude_success'] = self.stats.get('claude_success', 0) + 1
            else:
                # Detailliertes Logging fuer Debugging
                stdout_len = len(stdout)
                tmp_exists = os.path.exists(tmp_path)
                tmp_size = os.path.getsize(tmp_path) if tmp_exists else 0
                # stdout-Preview für Debugging (was kam zurück?)
                stdout_preview = stdout[:200].strip() if stdout else '(leer)'
                stderr_preview = stderr[-500:].strip() if stderr else '(leer)'
                logger.warning(
                    "Claude-Analyst: Kein strukturiertes Ergebnis "
                    "(stdout=%d Bytes, tmp_exists=%s, tmp_size=%d)\n"
                    "   stdout-Preview: %s\n"
                    "   stderr-Tail: %s",
                    stdout_len, tmp_exists, tmp_size,
                    stdout_preview, stderr_preview,
                )

            return result

        except asyncio.TimeoutError:
            logger.warning("Claude-Analyst: Timeout nach %ds — versuche Teilergebnisse", timeout)
            if proc:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    await proc.wait()
                except Exception:
                    pass

            result = self._read_analyst_result(tmp_path)
            if result:
                result['summary'] = f"[TIMEOUT] {result.get('summary', 'Session abgebrochen')}"
            return result

        except Exception as e:
            logger.error("Claude-Analyst Fehler: %s", e, exc_info=True)
            return None

        finally:
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
                if isinstance(data, list):
                    data = {"results": data, "summary": ""}
                if not isinstance(data, dict):
                    logger.warning("Temp-Datei enthält unerwarteten Typ: %s", type(data).__name__)
                    return None
                logger.debug("Analyst-Ergebnis aus Temp-Datei gelesen")
                return data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Temp-Datei nicht parsbar: {e}")

        # Fallback: JSON aus stdout extrahieren (max 500 KB um CPU-Spikes zu vermeiden)
        if stdout:
            search_text = stdout[-500_000:] if len(stdout) > 500_000 else stdout

            # 1. Versuche JSON aus Markdown-Codeblöcken zu extrahieren
            code_blocks = re.findall(r'```(?:json)?\s*\n({.*?})\s*\n```', search_text, re.DOTALL)
            for block in code_blocks:
                try:
                    data = json.loads(block)
                    if 'summary' in data or 'findings' in data:
                        logger.debug("Analyst-Ergebnis aus Markdown-Codeblock extrahiert")
                        return data
                except json.JSONDecodeError:
                    continue

            # 2. Suche nach JSON-Objekt mit erwarteten Keys
            for key in ('"summary"', '"findings"', '"health_check_passed"'):
                pattern = r'\{[^{]*?' + re.escape(key)
                match = re.search(pattern + r'.*', search_text, re.DOTALL)
                if not match:
                    continue
                # Gehe zum Anfang des JSON-Objekts zurück
                start = search_text.rfind('{', 0, match.start() + 1)
                if start < 0:
                    start = match.start()
                json_str = search_text[start:]
                # Finde das passende schließende Bracket (max 200 KB scannen)
                depth = 0
                end_idx = 0
                scan_limit = min(len(json_str), 200_000)
                for i in range(scan_limit):
                    ch = json_str[i]
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
                        if 'summary' in data or 'findings' in data:
                            logger.debug("Analyst-Ergebnis aus stdout extrahiert (Key: %s)", key)
                            return data
                    except json.JSONDecodeError:
                        continue

            logger.debug(
                "Analyst stdout ohne JSON-Ergebnis (Laenge: %d, Anfang: %.200s...)",
                len(stdout), stdout[:200]
            )

        return None

    # ------------------------------------------------------------------
    # Interne Methoden
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_schema(result: Dict, schema_path: Optional[Path]) -> bool:
        """
        Validiert ein AI-Ergebnis gegen das zugehoerige JSON-Schema.

        Args:
            result: Das geparste AI-Ergebnis
            schema_path: Pfad zur JSON-Schema-Datei (None = keine Validierung)

        Returns:
            True wenn valide oder kein Schema vorhanden, False bei Validierungsfehler
        """
        if not schema_path:
            return True

        schema_key = str(schema_path)
        if schema_key not in _schema_cache:
            try:
                with open(schema_path, 'r') as f:
                    _schema_cache[schema_key] = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError) as e:
                logger.warning("Schema %s konnte nicht geladen werden: %s", schema_path, e)
                return True  # Bei Lade-Fehler durchlassen

        schema = _schema_cache[schema_key]
        try:
            jsonschema.validate(instance=result, schema=schema)
            return True
        except jsonschema.ValidationError as e:
            logger.warning("Schema-Validierung fehlgeschlagen (%s): %s", schema_path.name, e.message)
            return False

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
        # Token-Budget-Check (zentral, gilt für alle AI-Calls)
        if self.is_budget_exhausted():
            logger.warning("Token-Budget erschöpft (%d/%d) — AI-Call übersprungen",
                           self._daily_tokens_used, self._daily_max_tokens)
            return None

        primary_engine = route.get('engine', 'codex')
        model_class = route.get('model_class', 'standard')
        schema_path = route.get('schema_path')

        # Token-Tracking
        self._track_tokens(prompt)

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

        # Optionaler Timeout aus Route (z.B. Patch Notes brauchen mehr Zeit)
        route_timeout = route.get('timeout')

        # Codex-Quota-Cache: direkt Fallback wenn Quota erschöpft
        import time as _time
        if primary_name == 'codex' and _time.time() < self._codex_quota_exhausted_until:
            logger.info("Codex-Quota erschöpft — direkt %s", fallback_name.capitalize())
        else:
            # Primary Versuch (mit Retry)
            self.stats[f'{primary_name}_calls'] += 1
            result = await self._query_with_retry(
                primary, primary_name, prompt,
                model=model_class, schema_path=schema_path,
                timeout=route_timeout,
            )

            if result:
                if self._validate_schema(result, schema_path):
                    self.stats[f'{primary_name}_success'] += 1
                    self._last_engine = primary_name
                    return result
                else:
                    logger.warning("%s-Ergebnis hat Schema-Validierung nicht bestanden — Fallback",
                                   primary_name.capitalize())

            # Primary fehlgeschlagen
            self.stats[f'{primary_name}_failures'] += 1
            logger.warning(f"{primary_name.capitalize()} fehlgeschlagen, Fallback auf {fallback_name.capitalize()}")

        # Fallback (mit Retry)
        self.stats[f'{fallback_name}_calls'] += 1
        result = await self._query_with_retry(
            fallback, fallback_name, prompt,
            model=model_class, schema_path=schema_path,
            timeout=route_timeout,
        )

        if result:
            if self._validate_schema(result, schema_path):
                self.stats[f'{fallback_name}_success'] += 1
                self._last_engine = fallback_name
                return result
            else:
                logger.warning("%s-Ergebnis hat Schema-Validierung nicht bestanden — verwerfe Ergebnis",
                               fallback_name.capitalize())

        self.stats[f'{fallback_name}_failures'] += 1
        return None

    async def _query_with_retry(
        self, provider, provider_name: str, prompt: str, *,
        model: str, schema_path, timeout, max_retries: int = 2,
        backoff_base: float = 1.0,
    ):
        """Retry einen Provider-Call mit exponentiellem Backoff."""
        for attempt in range(max_retries):
            try:
                result = await provider.query(
                    prompt, model=model, schema_path=schema_path,
                    timeout=timeout,
                )
                if result is not None:
                    return result
            except Exception as e:
                logger.warning(
                    "%s Versuch %d/%d fehlgeschlagen: %s",
                    provider_name.capitalize(), attempt + 1, max_retries, e,
                )
            if attempt < max_retries - 1:
                delay = backoff_base * (2 ** attempt)
                logger.info("Retry %s in %.1fs...", provider_name.capitalize(), delay)
                import asyncio
                await asyncio.sleep(delay)
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
