"""
Log File Analyzer für ShadowOps Bot
Analysiert Tool-Logs um aus Pattern, Fehlern und Trends zu lernen

Die KI lernt kontinuierlich aus:
- Fail2ban Logs (welche IPs greifen an? Welche Pattern?)
- CrowdSec Logs (welche Threats? Welche Decisions?)
- Docker Logs (welche Container-Probleme?)
- ShadowOps Logs (welche Fixes funktionieren?)
"""

import re
import os
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from typing import List, Dict, Optional, Any, Tuple
import logging

logger = logging.getLogger('shadowops')


class LogAnalyzer:
    """
    Analysiert Log-Dateien für Pattern-Erkennung und Anomalie-Detection

    Features:
    - Multi-Tool Log-Parsing (Fail2ban, CrowdSec, Docker, ShadowOps)
    - Pattern-Extraktion (häufige Fehler, wiederkehrende IPs, Error-Types)
    - Anomalie-Erkennung (ungewöhnliche Entries, Spikes, neue Patterns)
    - Trend-Analyse (zeitliche Entwicklung)
    - Learning-Integration (Context für AI)
    """

    def __init__(self, log_paths: Dict[str, str], max_lines: int = 5000):
        """
        Args:
            log_paths: Dict mit {tool_name: log_file_path}
            max_lines: Max Zeilen pro Log-File zu lesen
        """
        self.log_paths = log_paths
        self.max_lines = max_lines

        # Caches für Performance
        self.parsed_logs: Dict[str, List[Dict]] = {}
        self.pattern_cache: Dict[str, Any] = {}

        # Regex Patterns für verschiedene Log-Types
        self._init_patterns()

    def _init_patterns(self):
        """Initialize regex patterns für Log-Parsing"""

        # === FAIL2BAN LOG PATTERNS ===
        self.fail2ban_patterns = {
            'ban': re.compile(r'\[(\w+)\]\s+Ban\s+(\d+\.\d+\.\d+\.\d+)'),
            'unban': re.compile(r'\[(\w+)\]\s+Unban\s+(\d+\.\d+\.\d+\.\d+)'),
            'found': re.compile(r'\[(\w+)\]\s+Found\s+(\d+\.\d+\.\d+\.\d+)'),
            'timestamp': re.compile(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})')
        }

        # === CROWDSEC LOG PATTERNS ===
        self.crowdsec_patterns = {
            'decision': re.compile(r'decision\s+(\w+)\s+for\s+(\d+\.\d+\.\d+\.\d+)'),
            'ban': re.compile(r'ban.*?(\d+\.\d+\.\d+\.\d+)'),
            'scenario': re.compile(r'scenario[:\s]+([\'"]?)([^\'"]+)\1'),
            'timestamp': re.compile(r'time="([^"]+)"')
        }

        # === DOCKER LOG PATTERNS ===
        self.docker_patterns = {
            'error': re.compile(r'(ERROR|Error|error):\s*(.+)'),
            'warning': re.compile(r'(WARN|Warning|warning):\s*(.+)'),
            'container': re.compile(r'container\s+(\w+)'),
            'image': re.compile(r'image\s+([^\s]+)'),
            'timestamp': re.compile(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})')
        }

        # === SHADOWOPS LOG PATTERNS ===
        self.shadowops_patterns = {
            'fix_success': re.compile(r'(✅|SUCCESS).*?(?:fix|repair|resolve)', re.IGNORECASE),
            'fix_failed': re.compile(r'(❌|FAILED|ERROR).*?(?:fix|repair|resolve)', re.IGNORECASE),
            'event': re.compile(r'Event:\s*(\w+)'),
            'severity': re.compile(r'Severity:\s*(\w+)'),
            'timestamp': re.compile(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})')
        }

    def parse_log_file(self, tool_name: str, max_age_hours: int = 24) -> List[Dict]:
        """
        Parse ein Log-File und extrahiere strukturierte Daten

        Args:
            tool_name: Name des Tools (fail2ban, crowdsec, docker, shadowops)
            max_age_hours: Nur Einträge der letzten X Stunden

        Returns:
            Liste von Dicts mit Log-Einträgen
        """
        if tool_name not in self.log_paths:
            logger.warning(f"No log path configured for {tool_name}")
            return []

        log_file = self.log_paths[tool_name]

        if not os.path.exists(log_file):
            logger.debug(f"Log file not found: {log_file}")
            return []

        try:
            cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
            entries = []

            # Read log file (tail for performance)
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                # Read last N lines
                lines = self._tail_file(f, self.max_lines)

            # Parse based on tool type
            if tool_name == 'fail2ban':
                entries = self._parse_fail2ban(lines, cutoff_time)
            elif tool_name == 'crowdsec':
                entries = self._parse_crowdsec(lines, cutoff_time)
            elif tool_name == 'docker':
                entries = self._parse_docker(lines, cutoff_time)
            elif tool_name == 'shadowops':
                entries = self._parse_shadowops(lines, cutoff_time)
            else:
                # Generic parsing
                entries = self._parse_generic(lines, cutoff_time)

            self.parsed_logs[tool_name] = entries
            logger.info(f"✅ Parsed {len(entries)} entries from {tool_name} log")

            return entries

        except Exception as e:
            logger.error(f"❌ Error parsing {tool_name} log: {e}", exc_info=True)
            return []

    def _tail_file(self, file_handle, n: int) -> List[str]:
        """Read last N lines from file (efficient)"""
        # Seek to end
        file_handle.seek(0, 2)
        file_size = file_handle.tell()

        # Estimate bytes per line (assume ~150 chars avg)
        estimated_bytes = n * 150
        start_pos = max(0, file_size - estimated_bytes)

        file_handle.seek(start_pos)
        lines = file_handle.readlines()

        # Return last N lines
        return lines[-n:] if len(lines) > n else lines

    def _parse_fail2ban(self, lines: List[str], cutoff_time: datetime) -> List[Dict]:
        """Parse Fail2ban log entries"""
        entries = []

        for line in lines:
            timestamp_match = self.fail2ban_patterns['timestamp'].search(line)
            if not timestamp_match:
                continue

            try:
                timestamp = datetime.strptime(timestamp_match.group(1), '%Y-%m-%d %H:%M:%S')
                if timestamp < cutoff_time:
                    continue
            except:
                continue

            entry = {
                'timestamp': timestamp,
                'raw': line.strip()
            }

            # Check for ban
            ban_match = self.fail2ban_patterns['ban'].search(line)
            if ban_match:
                entry['action'] = 'ban'
                entry['jail'] = ban_match.group(1)
                entry['ip'] = ban_match.group(2)

            # Check for unban
            unban_match = self.fail2ban_patterns['unban'].search(line)
            if unban_match:
                entry['action'] = 'unban'
                entry['jail'] = unban_match.group(1)
                entry['ip'] = unban_match.group(2)

            # Check for found
            found_match = self.fail2ban_patterns['found'].search(line)
            if found_match:
                entry['action'] = 'found'
                entry['jail'] = found_match.group(1)
                entry['ip'] = found_match.group(2)

            if 'action' in entry:
                entries.append(entry)

        return entries

    def _parse_crowdsec(self, lines: List[str], cutoff_time: datetime) -> List[Dict]:
        """Parse CrowdSec log entries"""
        entries = []

        for line in lines:
            timestamp_match = self.crowdsec_patterns['timestamp'].search(line)
            if not timestamp_match:
                continue

            try:
                # CrowdSec uses RFC3339 format
                timestamp_str = timestamp_match.group(1)
                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                if timestamp.replace(tzinfo=None) < cutoff_time:
                    continue
            except:
                continue

            entry = {
                'timestamp': timestamp.replace(tzinfo=None),
                'raw': line.strip()
            }

            # Check for decision
            decision_match = self.crowdsec_patterns['decision'].search(line)
            if decision_match:
                entry['action'] = decision_match.group(1)
                entry['ip'] = decision_match.group(2)

            # Check for scenario
            scenario_match = self.crowdsec_patterns['scenario'].search(line)
            if scenario_match:
                entry['scenario'] = scenario_match.group(2)

            # Check for ban
            ban_match = self.crowdsec_patterns['ban'].search(line)
            if ban_match:
                entry['ip'] = ban_match.group(1)
                entry['action'] = 'ban'

            if 'action' in entry or 'scenario' in entry:
                entries.append(entry)

        return entries

    def _parse_docker(self, lines: List[str], cutoff_time: datetime) -> List[Dict]:
        """Parse Docker log entries"""
        entries = []

        for line in lines:
            timestamp_match = self.docker_patterns['timestamp'].search(line)
            if not timestamp_match:
                continue

            try:
                timestamp = datetime.fromisoformat(timestamp_match.group(1))
                if timestamp < cutoff_time:
                    continue
            except:
                continue

            entry = {
                'timestamp': timestamp,
                'raw': line.strip()
            }

            # Check for errors
            error_match = self.docker_patterns['error'].search(line)
            if error_match:
                entry['level'] = 'error'
                entry['message'] = error_match.group(2).strip()

            # Check for warnings
            warning_match = self.docker_patterns['warning'].search(line)
            if warning_match:
                entry['level'] = 'warning'
                entry['message'] = warning_match.group(2).strip()

            # Extract container
            container_match = self.docker_patterns['container'].search(line)
            if container_match:
                entry['container'] = container_match.group(1)

            # Extract image
            image_match = self.docker_patterns['image'].search(line)
            if image_match:
                entry['image'] = image_match.group(1)

            if 'level' in entry or 'container' in entry:
                entries.append(entry)

        return entries

    def _parse_shadowops(self, lines: List[str], cutoff_time: datetime) -> List[Dict]:
        """Parse ShadowOps bot log entries"""
        entries = []

        for line in lines:
            timestamp_match = self.shadowops_patterns['timestamp'].search(line)
            if not timestamp_match:
                continue

            try:
                timestamp = datetime.strptime(timestamp_match.group(1), '%Y-%m-%d %H:%M:%S')
                if timestamp < cutoff_time:
                    continue
            except:
                continue

            entry = {
                'timestamp': timestamp,
                'raw': line.strip()
            }

            # Check for fix success
            if self.shadowops_patterns['fix_success'].search(line):
                entry['type'] = 'fix_success'

            # Check for fix failed
            elif self.shadowops_patterns['fix_failed'].search(line):
                entry['type'] = 'fix_failed'

            # Extract event
            event_match = self.shadowops_patterns['event'].search(line)
            if event_match:
                entry['event'] = event_match.group(1)

            # Extract severity
            severity_match = self.shadowops_patterns['severity'].search(line)
            if severity_match:
                entry['severity'] = severity_match.group(1)

            if 'type' in entry or 'event' in entry:
                entries.append(entry)

        return entries

    def _parse_generic(self, lines: List[str], cutoff_time: datetime) -> List[Dict]:
        """Generic parsing for unknown log formats"""
        entries = []

        for line in lines:
            if line.strip():
                entries.append({
                    'timestamp': datetime.now(),
                    'raw': line.strip()
                })

        return entries[-self.max_lines:]  # Limit

    def analyze_patterns(self, tool_name: str, hours: int = 24) -> Dict[str, Any]:
        """
        Analysiere Pattern in Logs

        Args:
            tool_name: Tool name
            hours: Hours to analyze

        Returns:
            Dict with pattern analysis
        """
        entries = self.parse_log_file(tool_name, hours)

        if not entries:
            return {
                'total_entries': 0,
                'patterns': {}
            }

        analysis = {
            'total_entries': len(entries),
            'time_range': {
                'start': min(e['timestamp'] for e in entries),
                'end': max(e['timestamp'] for e in entries)
            },
            'patterns': {}
        }

        # Tool-specific analysis
        if tool_name == 'fail2ban':
            analysis['patterns'] = self._analyze_fail2ban_patterns(entries)
        elif tool_name == 'crowdsec':
            analysis['patterns'] = self._analyze_crowdsec_patterns(entries)
        elif tool_name == 'docker':
            analysis['patterns'] = self._analyze_docker_patterns(entries)
        elif tool_name == 'shadowops':
            analysis['patterns'] = self._analyze_shadowops_patterns(entries)

        return analysis

    def _analyze_fail2ban_patterns(self, entries: List[Dict]) -> Dict[str, Any]:
        """Analyze Fail2ban patterns"""
        patterns = {
            'actions': Counter(),
            'jails': Counter(),
            'top_ips': Counter(),
            'ban_rate': 0.0
        }

        for entry in entries:
            if 'action' in entry:
                patterns['actions'][entry['action']] += 1
            if 'jail' in entry:
                patterns['jails'][entry['jail']] += 1
            if 'ip' in entry and entry.get('action') == 'ban':
                patterns['top_ips'][entry['ip']] += 1

        # Calculate ban rate (bans per hour)
        if entries:
            time_span = (entries[-1]['timestamp'] - entries[0]['timestamp']).total_seconds() / 3600
            if time_span > 0:
                patterns['ban_rate'] = patterns['actions']['ban'] / time_span

        return patterns

    def _analyze_crowdsec_patterns(self, entries: List[Dict]) -> Dict[str, Any]:
        """Analyze CrowdSec patterns"""
        patterns = {
            'scenarios': Counter(),
            'actions': Counter(),
            'top_ips': Counter()
        }

        for entry in entries:
            if 'scenario' in entry:
                patterns['scenarios'][entry['scenario']] += 1
            if 'action' in entry:
                patterns['actions'][entry['action']] += 1
            if 'ip' in entry:
                patterns['top_ips'][entry['ip']] += 1

        return patterns

    def _analyze_docker_patterns(self, entries: List[Dict]) -> Dict[str, Any]:
        """Analyze Docker patterns"""
        patterns = {
            'levels': Counter(),
            'containers': Counter(),
            'common_errors': Counter()
        }

        for entry in entries:
            if 'level' in entry:
                patterns['levels'][entry['level']] += 1
            if 'container' in entry:
                patterns['containers'][entry['container']] += 1
            if 'message' in entry and entry.get('level') == 'error':
                # Extract first 50 chars of error as pattern
                error_pattern = entry['message'][:50]
                patterns['common_errors'][error_pattern] += 1

        return patterns

    def _analyze_shadowops_patterns(self, entries: List[Dict]) -> Dict[str, Any]:
        """Analyze ShadowOps patterns"""
        patterns = {
            'fix_types': Counter(),
            'events': Counter(),
            'severities': Counter(),
            'success_rate': 0.0
        }

        successes = 0
        failures = 0

        for entry in entries:
            if 'type' in entry:
                patterns['fix_types'][entry['type']] += 1
                if entry['type'] == 'fix_success':
                    successes += 1
                elif entry['type'] == 'fix_failed':
                    failures += 1
            if 'event' in entry:
                patterns['events'][entry['event']] += 1
            if 'severity' in entry:
                patterns['severities'][entry['severity']] += 1

        # Calculate success rate
        total_fixes = successes + failures
        if total_fixes > 0:
            patterns['success_rate'] = successes / total_fixes

        return patterns

    def detect_anomalies(self, tool_name: str, threshold: float = 2.0) -> List[Dict]:
        """
        Detect anomalies in logs (spikes, unusual patterns)

        Args:
            tool_name: Tool name
            threshold: Standard deviations for anomaly (default: 2.0)

        Returns:
            List of detected anomalies
        """
        entries = self.parse_log_file(tool_name, 24)

        if len(entries) < 10:
            return []  # Not enough data

        anomalies = []

        # Group entries by hour
        hourly_counts = defaultdict(int)
        for entry in entries:
            hour_key = entry['timestamp'].replace(minute=0, second=0, microsecond=0)
            hourly_counts[hour_key] += 1

        # Calculate mean and stddev
        counts = list(hourly_counts.values())
        mean = sum(counts) / len(counts)
        stddev = (sum((x - mean) ** 2 for x in counts) / len(counts)) ** 0.5

        # Detect spikes
        for hour, count in hourly_counts.items():
            if count > mean + (threshold * stddev):
                anomalies.append({
                    'type': 'spike',
                    'hour': hour,
                    'count': count,
                    'expected': mean,
                    'deviation': (count - mean) / stddev if stddev > 0 else 0
                })

        return anomalies

    def generate_context_for_ai(self, tool_name: str, hours: int = 24) -> str:
        """
        Generate AI context from log analysis

        Args:
            tool_name: Tool name
            hours: Hours to analyze

        Returns:
            Formatted context string
        """
        try:
            analysis = self.analyze_patterns(tool_name, hours)

            if analysis['total_entries'] == 0:
                return f"# {tool_name.upper()} LOG INSIGHTS\nNo recent log entries found.\n"

            context_parts = []
            context_parts.append(f"# {tool_name.upper()} LOG INSIGHTS")
            context_parts.append(f"Analyzed: Last {hours}h | {analysis['total_entries']} entries")
            context_parts.append("")

            patterns = analysis['patterns']

            # Tool-specific context
            if tool_name == 'fail2ban':
                context_parts.append("## Ban Activity")
                if patterns.get('actions'):
                    for action, count in patterns['actions'].most_common(3):
                        context_parts.append(f"- {action.title()}: {count}x")
                if patterns.get('top_ips'):
                    context_parts.append("\n## Most Aggressive IPs")
                    for ip, count in patterns['top_ips'].most_common(5):
                        context_parts.append(f"- {ip}: {count} bans")
                if patterns.get('ban_rate'):
                    context_parts.append(f"\n**Ban Rate**: {patterns['ban_rate']:.2f} bans/hour")

            elif tool_name == 'crowdsec':
                if patterns.get('scenarios'):
                    context_parts.append("## Top Threat Scenarios")
                    for scenario, count in patterns['scenarios'].most_common(5):
                        context_parts.append(f"- {scenario}: {count}x")
                if patterns.get('top_ips'):
                    context_parts.append("\n## Most Active Threat IPs")
                    for ip, count in patterns['top_ips'].most_common(5):
                        context_parts.append(f"- {ip}: {count} actions")

            elif tool_name == 'docker':
                if patterns.get('levels'):
                    context_parts.append("## Log Levels")
                    for level, count in patterns['levels'].most_common():
                        context_parts.append(f"- {level.upper()}: {count}x")
                if patterns.get('common_errors'):
                    context_parts.append("\n## Common Error Patterns")
                    for error, count in patterns['common_errors'].most_common(3):
                        context_parts.append(f"- {error}... ({count}x)")

            elif tool_name == 'shadowops':
                if patterns.get('success_rate'):
                    rate = patterns['success_rate'] * 100
                    context_parts.append(f"## Fix Success Rate: {rate:.1f}%")
                if patterns.get('events'):
                    context_parts.append("\n## Most Common Events")
                    for event, count in patterns['events'].most_common(5):
                        context_parts.append(f"- {event}: {count}x")

            # Add anomalies
            anomalies = self.detect_anomalies(tool_name)
            if anomalies:
                context_parts.append("\n## ⚠️ Detected Anomalies")
                for anomaly in anomalies[:3]:
                    hour = anomaly['hour'].strftime('%Y-%m-%d %H:00')
                    context_parts.append(f"- **{hour}**: Spike detected ({anomaly['count']} events, {anomaly['deviation']:.1f}σ)")

            context_parts.append("")
            return '\n'.join(context_parts)

        except Exception as e:
            logger.error(f"❌ Error generating log context for {tool_name}: {e}", exc_info=True)
            return f"# {tool_name.upper()} LOG INSIGHTS\n(Error loading log analysis)\n"

    def get_all_insights(self, hours: int = 24) -> str:
        """
        Get combined insights from all configured logs

        Args:
            hours: Hours to analyze

        Returns:
            Combined context string
        """
        insights = []

        for tool_name in self.log_paths.keys():
            context = self.generate_context_for_ai(tool_name, hours)
            if context and len(context) > 50:
                insights.append(context)

        if insights:
            return '\n\n'.join(insights)
        else:
            return "# LOG INSIGHTS\nNo log data available for analysis.\n"
