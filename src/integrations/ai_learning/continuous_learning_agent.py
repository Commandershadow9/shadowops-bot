"""
Continuous Learning Agent for ShadowOps Bot

This agent continuously analyzes the system, learns from git commits, code changes,
security events, and system behavior. It uses Ollama for intelligent analysis and
provides regular feedback via Discord.

Features:
- Continuous Git history analysis
- Code pattern learning
- Security event correlation
- System behavior learning
- Regular Discord reports
- Intelligent insights via Ollama
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
import json
from dataclasses import dataclass, field

from integrations.auto_fix_manager import FixProposal
from integrations.ai_learning.knowledge_synthesizer import KnowledgeSynthesizer

logger = logging.getLogger('shadowops.learning')


@dataclass
class LearningSession:
    """Represents a learning session with metrics"""
    session_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    items_analyzed: int = 0
    insights_generated: int = 0
    patterns_discovered: int = 0
    session_type: str = "continuous"  # continuous, git, code, security

    def duration_seconds(self) -> float:
        """Get session duration in seconds"""
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return (datetime.utcnow() - self.start_time).total_seconds()


@dataclass
class LearningInsight:
    """Represents an insight discovered by the learning agent"""
    insight_id: str
    category: str  # git_pattern, code_pattern, security_trend, system_behavior
    title: str
    description: str
    confidence: float  # 0.0 - 1.0
    discovered_at: datetime = field(default_factory=datetime.utcnow)
    data: Dict[str, Any] = field(default_factory=dict)

    def to_embed_field(self) -> Dict[str, str]:
        """Convert to Discord embed field format"""
        confidence_emoji = "🟢" if self.confidence >= 0.8 else "🟡" if self.confidence >= 0.6 else "🔴"
        return {
            "name": f"{confidence_emoji} {self.title}",
            "value": f"{self.description}\n*Confidence: {self.confidence*100:.0f}%*",
            "inline": False
        }


class ContinuousLearningAgent:
    """
    Continuous Learning Agent that analyzes system behavior,
    code changes, and security events in real-time.
    """

    def __init__(self, bot, config, ai_service, context_manager, discord_logger):
        """
        Initialize the Continuous Learning Agent

        Args:
            bot: Discord bot instance
            config: Configuration object
            ai_service: AI service for Ollama integration
            context_manager: Context manager with git/code analyzers
            discord_logger: Discord logger for feedback
        """
        self.bot = bot
        self.config = config
        self.ai_service = ai_service
        self.context_manager = context_manager
        self.discord_logger = discord_logger
        self.logger = logger

        # Learning state
        self.is_running = False
        self.current_session: Optional[LearningSession] = None
        self.insights_queue: List[LearningInsight] = []

        # Configuration
        self.learning_interval = 1800  # 30 minutes
        self.git_analysis_interval = 3600  # 1 hour
        self.code_analysis_interval = 7200  # 2 hours
        self.report_interval = 21600  # 6 hours (4x per day)
        self.log_analysis_interval = 7200  # 2 hours
        self.trend_report_interval = 86400  # 24 hours
        self.synthesis_interval = 21600  # 6 hours (4x per day for knowledge synthesis)

        # Knowledge Synthesizer for long-term learning
        self.knowledge_synthesizer = KnowledgeSynthesizer(ai_service=ai_service)

        # Discord channel
        self.learning_channel_id = config.channels.get('ai_learning', 0)
        notifications = getattr(config, 'auto_remediation', {}).get('notifications', {}) if hasattr(config, 'auto_remediation') else {}
        self._learning_channel_fallback = notifications.get('ai_learning_channel')
        if not self.learning_channel_id and self._learning_channel_fallback:
            self.learning_channel_id = self._learning_channel_fallback

        # Tasks
        self.continuous_task: Optional[asyncio.Task] = None
        self.git_task: Optional[asyncio.Task] = None
        self.code_task: Optional[asyncio.Task] = None
        self.log_task: Optional[asyncio.Task] = None
        self.report_task: Optional[asyncio.Task] = None
        self.trend_task: Optional[asyncio.Task] = None
        self.synthesis_task: Optional[asyncio.Task] = None

        # State caches
        self.last_git_hashes: Dict[str, set] = {}

        # Metrics
        self.total_sessions = 0
        self.total_insights = 0
        self.start_time = datetime.utcnow()

        # Trend state file
        self.trend_file = Path(__file__).parent.parent.parent / 'data' / 'learning_trends.json'

        self.logger.info("🧠 Continuous Learning Agent initialized")

    async def start(self):
        """Start all learning background tasks"""
        if self.is_running:
            self.logger.warning("⚠️ Learning agent already running")
            return

        self.is_running = True
        self.logger.info("🚀 Starting Continuous Learning Agent...")

        # Start all background tasks
        self.continuous_task = asyncio.create_task(self._continuous_learning_loop())
        self.git_task = asyncio.create_task(self._git_analysis_loop())
        self.code_task = asyncio.create_task(self._code_analysis_loop())
        self.log_task = asyncio.create_task(self._log_analysis_loop())
        self.report_task = asyncio.create_task(self._reporting_loop())
        self.trend_task = asyncio.create_task(self._trend_report_loop())
        self.synthesis_task = asyncio.create_task(self._knowledge_synthesis_loop())

        # Send startup message
        await self._send_learning_message(
            "🧠 **Continuous Learning System gestartet**\n"
            f"📊 Git Analysis: Alle {self.git_analysis_interval//60} Minuten\n"
            f"💻 Code Analysis: Alle {self.code_analysis_interval//60} Minuten\n"
            f"📈 Reports: Alle {self.report_interval//3600} Stunden\n"
            f"🔄 Kontinuierliches Learning: Alle {self.learning_interval//60} Minuten",
            color=0x00FF00
        )

        self.logger.info("✅ Continuous Learning Agent started successfully")

    async def stop(self):
        """Stop all learning background tasks"""
        if not self.is_running:
            return

        self.logger.info("🛑 Stopping Continuous Learning Agent...")
        self.is_running = False

        # Cancel all tasks
        for task in [self.continuous_task, self.git_task, self.code_task, self.report_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self.log_task:
            self.log_task.cancel()
            try:
                await self.log_task
            except asyncio.CancelledError:
                pass
        if self.trend_task:
            self.trend_task.cancel()
            try:
                await self.trend_task
            except asyncio.CancelledError:
                pass
        if self.synthesis_task:
            self.synthesis_task.cancel()
            try:
                await self.synthesis_task
            except asyncio.CancelledError:
                pass

        self.logger.info("✅ Continuous Learning Agent stopped")

    async def _continuous_learning_loop(self):
        """Main continuous learning loop"""
        while self.is_running:
            try:
                await asyncio.sleep(self.learning_interval)

                session = LearningSession(
                    session_id=f"continuous_{int(datetime.utcnow().timestamp())}",
                    start_time=datetime.utcnow(),
                    session_type="continuous"
                )
                self.current_session = session

                self.logger.info("🔄 Starting continuous learning session...")

                # Analyze system behavior
                await self._analyze_system_behavior(session)

                # Check for recent security events
                await self._analyze_recent_security_events(session)

                # Analyze project health trends
                await self._analyze_project_health_trends(session)

                session.end_time = datetime.utcnow()
                self.total_sessions += 1

                self.logger.info(
                    f"✅ Learning session complete: "
                    f"{session.items_analyzed} items, "
                    f"{session.insights_generated} insights, "
                    f"{session.duration_seconds():.1f}s"
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ Error in continuous learning loop: {e}", exc_info=True)
                await asyncio.sleep(300)  # Wait 5 minutes on error

    async def _git_analysis_loop(self):
        """Periodically analyze git history for patterns"""
        # Wait 5 minutes before first run to let system stabilize
        await asyncio.sleep(300)

        while self.is_running:
            try:
                session = LearningSession(
                    session_id=f"git_{int(datetime.utcnow().timestamp())}",
                    start_time=datetime.utcnow(),
                    session_type="git"
                )

                self.logger.info("📚 Starting Git history analysis...")

                # Analyze git commits for patterns
                insights = await self._analyze_git_patterns(session)

                if insights:
                    self.insights_queue.extend(insights)
                    session.insights_generated = len(insights)
                    self.total_insights += len(insights)

                    # Send immediate feedback for all git insights
                    for insight in insights:
                        await self._send_insight_notification(insight)

                session.end_time = datetime.utcnow()
                self.total_sessions += 1

                self.logger.info(
                    f"✅ Git analysis complete: {session.insights_generated} insights"
                )

                await asyncio.sleep(self.git_analysis_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ Error in git analysis loop: {e}", exc_info=True)
                await asyncio.sleep(1800)  # Wait 30 minutes on error

    async def _code_analysis_loop(self):
        """Periodically analyze code for patterns and vulnerabilities"""
        # Wait 10 minutes before first run
        await asyncio.sleep(600)

        while self.is_running:
            try:
                session = LearningSession(
                    session_id=f"code_{int(datetime.utcnow().timestamp())}",
                    start_time=datetime.utcnow(),
                    session_type="code"
                )

                self.logger.info("💻 Starting code analysis...")

                # Analyze code patterns
                insights = await self._analyze_code_patterns(session)

                if insights:
                    self.insights_queue.extend(insights)
                    session.insights_generated = len(insights)
                    self.total_insights += len(insights)

                    # Send immediate feedback for all code insights
                    for insight in insights:
                        await self._send_insight_notification(insight)

                session.end_time = datetime.utcnow()
                self.total_sessions += 1

                self.logger.info(
                    f"✅ Code analysis complete: {session.insights_generated} insights"
                )

                await asyncio.sleep(self.code_analysis_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ Error in code analysis loop: {e}", exc_info=True)
                await asyncio.sleep(1800)

    async def _reporting_loop(self):
        """Periodically send learning reports to Discord"""
        # Wait 15 minutes before first report
        await asyncio.sleep(900)

        while self.is_running:
            try:
                await asyncio.sleep(self.report_interval)

                self.logger.info("📊 Generating learning report...")
                await self._send_learning_report()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ Error in reporting loop: {e}", exc_info=True)
                await asyncio.sleep(3600)

    async def _analyze_system_behavior(self, session: LearningSession):
        """Analyze current system behavior and performance"""
        try:
            items_analyzed = 0

            # Check bot health
            if self.bot.is_ready():
                items_analyzed += 1
                latency_ms = round(self.bot.latency * 1000, 2)

                if latency_ms > 200:
                    insight = LearningInsight(
                        insight_id=f"latency_{int(datetime.utcnow().timestamp())}",
                        category="system_behavior",
                        title="Erhöhte Bot-Latenz erkannt",
                        description=f"Aktuelle Latenz: {latency_ms}ms (Normal: <200ms)",
                        confidence=0.9,
                        data={"latency_ms": latency_ms}
                    )
                    self.insights_queue.append(insight)
                    session.insights_generated += 1

            # Check project monitor status
            if hasattr(self.bot, 'project_monitor') and self.bot.project_monitor:
                projects = self.bot.project_monitor.projects
                items_analyzed += len(projects)

                # Check for projects with low uptime
                for name, project in projects.items():
                    if project.uptime_percentage < 95 and project.total_checks > 10:
                        insight = LearningInsight(
                            insight_id=f"uptime_{name}_{int(datetime.utcnow().timestamp())}",
                            category="system_behavior",
                            title=f"Niedrige Uptime: {name}",
                            description=f"Uptime: {project.uptime_percentage:.1f}% ({project.failed_checks} failures)",
                            confidence=0.85,
                            data={
                                "project": name,
                                "uptime": project.uptime_percentage,
                                "failures": project.failed_checks
                            }
                        )
                        self.insights_queue.append(insight)
                        session.insights_generated += 1

            session.items_analyzed += items_analyzed

        except Exception as e:
            self.logger.error(f"Error analyzing system behavior: {e}", exc_info=True)

    async def _analyze_recent_security_events(self, session: LearningSession):
        """
        🤖 KI-Learning: Analyze recent security events for attack patterns.

        Analyzes:
        - Fail2ban bans (SSH brute force, web attacks)
        - CrowdSec decisions (distributed attack detection)
        - IP geolocation patterns
        - Attack frequency and severity

        Generates insights about:
        - Coordinated attacks
        - Persistent attackers
        - Geographic attack patterns
        - Attack type trends
        """
        try:
            from collections import Counter
            from datetime import datetime, timedelta

            # Get security logs from log analyzer
            if not hasattr(self.bot, 'log_analyzer'):
                return

            log_analyzer = self.bot.log_analyzer
            insights = []

            # Analyze fail2ban events (last 24 hours)
            fail2ban_events = []
            try:
                fail2ban_log = log_analyzer.get_latest_log_entries('fail2ban', hours=24)
                if fail2ban_log:
                    # Parse ban events
                    for entry in fail2ban_log:
                        if 'Ban' in entry.get('message', ''):
                            # Extract IP from message like "Ban 1.2.3.4"
                            parts = entry['message'].split()
                            if len(parts) >= 2:
                                ip = parts[1]
                                fail2ban_events.append({
                                    'ip': ip,
                                    'timestamp': entry.get('timestamp'),
                                    'service': entry.get('jail', 'unknown')
                                })
            except Exception as e:
                self.logger.debug(f"Could not parse fail2ban logs: {e}")

            # Analyze CrowdSec events (last 24 hours)
            crowdsec_events = []
            try:
                crowdsec_log = log_analyzer.get_latest_log_entries('crowdsec', hours=24)
                if crowdsec_log:
                    for entry in crowdsec_log:
                        if 'decision' in entry.get('message', '').lower():
                            # CrowdSec decision events
                            crowdsec_events.append({
                                'timestamp': entry.get('timestamp'),
                                'message': entry.get('message')
                            })
            except Exception as e:
                self.logger.debug(f"Could not parse crowdsec logs: {e}")

            session.items_analyzed += len(fail2ban_events) + len(crowdsec_events)

            if not fail2ban_events and not crowdsec_events:
                return  # No events to analyze

            # Pattern 1: Identify repeat offenders (IPs banned multiple times)
            if fail2ban_events:
                ip_counts = Counter(e['ip'] for e in fail2ban_events)
                repeat_offenders = {ip: count for ip, count in ip_counts.items() if count >= 3}

                if repeat_offenders:
                    top_offender = max(repeat_offenders.items(), key=lambda x: x[1])
                    insight = LearningInsight(
                        insight_id=f"security_repeat_{int(datetime.utcnow().timestamp())}",
                        category="security_trend",
                        title="Persistent Attacker Detected",
                        description=f"IP {top_offender[0]} has been banned {top_offender[1]} times in 24h. "
                                  f"Total repeat offenders: {len(repeat_offenders)}. "
                                  f"Consider adding permanent blocks for persistent attackers.",
                        confidence=0.9,
                        data={"repeat_offenders": repeat_offenders}
                    )
                    insights.append(insight)
                    self.insights_queue.append(insight)
                    session.insights_generated += 1

            # Pattern 2: High attack volume (>20 bans in 24h)
            total_bans = len(fail2ban_events)
            if total_bans >= 20:
                services_targeted = Counter(e['service'] for e in fail2ban_events)
                most_targeted = max(services_targeted.items(), key=lambda x: x[1]) if services_targeted else ('unknown', 0)

                insight = LearningInsight(
                    insight_id=f"security_volume_{int(datetime.utcnow().timestamp())}",
                    category="security_trend",
                    title="High Attack Volume Detected",
                    description=f"{total_bans} attacks blocked in 24h. "
                              f"Most targeted: {most_targeted[0]} ({most_targeted[1]} attacks). "
                              f"Consider enabling additional protection or rate limiting.",
                    confidence=0.85,
                    data={"total_bans": total_bans, "services": dict(services_targeted)}
                )
                insights.append(insight)
                self.insights_queue.append(insight)
                session.insights_generated += 1

            # Pattern 3: Coordinated attack (multiple IPs in short time)
            if len(fail2ban_events) >= 10:
                # Group by hour to detect bursts
                hourly_counts = {}
                for event in fail2ban_events:
                    try:
                        ts = datetime.fromisoformat(event['timestamp'])
                        hour_key = ts.strftime('%Y-%m-%d %H:00')
                        hourly_counts[hour_key] = hourly_counts.get(hour_key, 0) + 1
                    except:
                        continue

                max_hourly = max(hourly_counts.values()) if hourly_counts else 0
                if max_hourly >= 10:
                    peak_hour = max(hourly_counts.items(), key=lambda x: x[1])[0]
                    unique_ips = len(set(e['ip'] for e in fail2ban_events))

                    insight = LearningInsight(
                        insight_id=f"security_coordinated_{int(datetime.utcnow().timestamp())}",
                        category="security_trend",
                        title="Coordinated Attack Pattern",
                        description=f"{max_hourly} attacks in single hour ({peak_hour}). "
                                  f"{unique_ips} unique IPs involved. "
                                  f"This suggests a coordinated or distributed attack.",
                        confidence=0.75,
                        data={"peak_hour": peak_hour, "peak_count": max_hourly, "unique_ips": unique_ips}
                    )
                    insights.append(insight)
                    self.insights_queue.append(insight)
                    session.insights_generated += 1

            # Send insights to Discord immediately
            for insight in insights:
                await self._send_insight_notification(insight)

        except Exception as e:
            self.logger.error(f"Error analyzing security events: {e}", exc_info=True)

    async def _analyze_project_health_trends(self, session: LearningSession):
        """Analyze trends in project health over time"""
        # This would analyze historical health data
        # For now, placeholder
        pass

    async def _analyze_git_patterns(self, session: LearningSession) -> List[LearningInsight]:
        """Analyze git commit patterns using Ollama"""
        insights = []

        try:
            if not self.context_manager.git_analyzers:
                return insights

            for project_name, analyzer in self.context_manager.git_analyzers.items():
                # Get recent commits (last 24 hours)
                all_commits = analyzer.load_commit_history()

                # Filter commits from last 24 hours (date is string in ISO format)
                from dateutil.parser import parse
                recent_commits = []
                for c in all_commits:
                    try:
                        commit_date = parse(c['date'])
                        if (datetime.utcnow() - commit_date).total_seconds() < 86400:
                            recent_commits.append(c)
                    except:
                        continue

                # Fallback: if nothing in last 24h, use latest commits anyway so we always learn
                fallback_used = False
                if not recent_commits and all_commits:
                    recent_commits = all_commits[:5]
                    fallback_used = True

                if not recent_commits:
                    continue

                session.items_analyzed += len(recent_commits)

                # Build compact summary of latest commits
                commit_summary = "\n".join([
                    f"- {(c.get('subject') or c.get('full_message',''))[:60]} by {c.get('author','?')}"
                    for c in recent_commits[:5]
                ]) or "Keine Commit-Messages gefunden"

                # Top changed files across recent commits
                file_freq: Dict[str, int] = {}
                for c in recent_commits:
                    for f in c.get('changed_files', []):
                        file_freq[f] = file_freq.get(f, 0) + 1
                top_files = ", ".join([f"{name} ({count}x)" for name, count in sorted(file_freq.items(), key=lambda x: x[1], reverse=True)[:5]])

                # Delta: new commits since last run
                new_commits = []
                if project_name not in self.last_git_hashes:
                    self.last_git_hashes[project_name] = set()
                seen_hashes = self.last_git_hashes[project_name]
                for c in recent_commits:
                    if c.get('hash') not in seen_hashes:
                        new_commits.append(c)
                # Update cache
                for c in recent_commits:
                    if c.get('hash'):
                        seen_hashes.add(c['hash'])
                delta_files = set()
                for c in new_commits:
                    for f in c.get('changed_files', []):
                        delta_files.add(f)
                delta_text = ", ".join(list(delta_files)[:5]) if delta_files else top_files

                prompt = f"""Analysiere diese Git Commits für das Projekt '{project_name}'{ ' (keine neuen Commits <24h, zeige letzte Updates)' if fallback_used else '' }:

{commit_summary}

Identifiziere Muster oder wichtige Erkenntnisse. Antworte in 1-2 Sätzen auf Deutsch."""

                description = None
                confidence = 0.65

                try:
                    analysis = await self.ai_service.get_ai_analysis(
                        prompt=prompt,
                        context="",
                        use_critical_model=False
                    )

                    if analysis and len(analysis) > 20:
                        description = analysis[:300]
                        confidence = 0.75
                except Exception as e:
                    self.logger.debug(f"Ollama analysis failed for {project_name}: {e}")

                if not description:
                    unique_authors = {c.get('author', 'unbekannt') for c in recent_commits}
                    timeframe = "letzten 24h" if not fallback_used else "letzten Updates (älter als 24h)"
                    description = (
                        f"{len(recent_commits)} Commit(s) in den {timeframe}. "
                        f"Autoren: {', '.join(unique_authors)}. "
                        f"Beispiele:\n{commit_summary}"
                    )[:300]
                else:
                    if top_files:
                        description = (description + f"\n\nGeänderte Dateien (häufig): {top_files}")[:400]
                    if delta_text:
                        description = (description + f"\nNeu seit letztem Lauf: {delta_text}")[:450]

                insight = LearningInsight(
                    insight_id=f"git_{project_name}_{int(datetime.utcnow().timestamp())}",
                    category="git_pattern",
                    title=f"Git-Aktivität in {project_name}",
                    description=description,
                    confidence=confidence,
                    data={
                        "project": project_name,
                        "commits_count": len(recent_commits)
                    }
                )
                insights.append(insight)
                session.patterns_discovered += 1

        except Exception as e:
            self.logger.error(f"Error analyzing git patterns: {e}", exc_info=True)

        return insights

    async def _log_analysis_loop(self):
        """Periodically analyze logs and send summaries"""
        # Wait a short period before first run to allow other components to initialize
        await asyncio.sleep(60) # Reduced from 900 to 60 seconds

        while self.is_running:
            try:
                context = ""
                try:
                    if self.context_manager.enable_log_learning and self.context_manager.log_analyzer:
                        context = self.context_manager.log_analyzer.get_all_insights(hours=6)
                        anomalies = self.context_manager.log_analyzer.get_anomalies_summary(hours=6)
                    else:
                        anomalies = []
                except Exception as e:
                    self.logger.debug(f"Log analysis failed: {e}")
                    anomalies = []

                if context and len(context) > 80:
                    await self._send_learning_message(
                        f"🪵 **Log Insights (letzte 6h)**\n{context[:1900]}",
                        color=0x95A5A6
                    )

                if anomalies:
                    await self._send_learning_message(
                        "⚠️ **Log Anomalien erkannt**\n- " + "\n- ".join(anomalies[:5]),
                        color=0xE67E22
                    )

                await asyncio.sleep(self.log_analysis_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ Error in log analysis loop: {e}", exc_info=True)
                await asyncio.sleep(1800)

    # ==== Trend storage helpers ==================================================
    def _load_trend_state(self) -> Dict[str, Any]:
        try:
            if self.trend_file.exists():
                return json.loads(self.trend_file.read_text(encoding='utf-8'))
        except Exception as e:
            self.logger.debug(f"Trend state load failed: {e}")
        return {}

    def _save_trend_state(self, state: Dict[str, Any]) -> None:
        try:
            self.trend_file.parent.mkdir(parents=True, exist_ok=True)
            self.trend_file.write_text(json.dumps(state, indent=2), encoding='utf-8')
        except Exception as e:
            self.logger.debug(f"Trend state save failed: {e}")

    def _update_trend(self, project_name: str, summary: Dict[str, Any], metrics: Dict[str, Any]) -> str:
        """Update trend state and return a short trend string."""
        state = self._load_trend_state()
        proj_state = state.get(project_name, {})

        fields = {
            'loc': summary.get('total_lines'),
            'files': summary.get('total_files'),
            'doc_cov': metrics.get('documentation', {}).get('documentation_coverage'),
            'coverage': metrics.get('testing', {}).get('coverage')
        }

        deltas = []
        for key, value in fields.items():
            if value is None:
                continue
            prev = proj_state.get(key)
            if prev is not None:
                delta = value - prev
                if abs(delta) >= 1:
                    deltas.append(f"{key}: {value} ({'+' if delta>=0 else ''}{delta})")
            proj_state[key] = value

        proj_state['updated_at'] = datetime.utcnow().isoformat()
        state[project_name] = proj_state
        self._save_trend_state(state)

        return " | ".join(deltas) if deltas else ""

    async def _trend_report_loop(self):
        """Send daily pinned trend report (long-term view)"""
        # Wait 30 minutes after start to avoid startup noise
        await asyncio.sleep(1800)

        while self.is_running:
            try:
                trend_state = self._load_trend_state()
                if not trend_state:
                    await asyncio.sleep(self.trend_report_interval)
                    continue

                lines = ["📈 **Langzeit-Trends (Coverage/LOC/Files)**"]
                for project, data in trend_state.items():
                    loc = data.get('loc', 'n/a')
                    files = data.get('files', 'n/a')
                    doc_cov = data.get('doc_cov', 'n/a')
                    cov = data.get('coverage', 'n/a')
                    lines.append(f"- {project}: LOC={loc}, Files={files}, Doc={doc_cov}%, Cov={cov}%")

                await self._send_learning_message("\n".join(lines), color=0x9B59B6, pin=True)
                await asyncio.sleep(self.trend_report_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ Error in trend report loop: {e}", exc_info=True)
                await asyncio.sleep(3600)

    async def _analyze_code_patterns(self, session: LearningSession) -> List[LearningInsight]:
        """Analyze code patterns using Ollama"""
        insights = []

        try:
            if not self.context_manager.code_analyzers:
                return insights

            for project_name, analyzer in self.context_manager.code_analyzers.items():
                # Use analyze_all() which returns cached results if available
                results = analyzer.analyze_all()

                if not results or not results.get('summary'):
                    continue

                summary = results.get('summary', {})
                metrics = results.get('metrics', {})
                session.items_analyzed += summary.get('total_files', 0)
                trend_text = self._update_trend(project_name, summary, metrics)

                # Analyze code statistics with Ollama
                doc_cov = metrics.get('documentation', {}).get('documentation_coverage')
                largest_files = metrics.get('complexity', {}).get('largest_files', []) or []
                largest_files_text = ", ".join([f"{f['module']} ({f['lines']} LOC)" for f in largest_files[:3]])
                test_files = metrics.get('testing', {}).get('test_files')
                coverage = metrics.get('testing', {}).get('coverage')
                frameworks = metrics.get('testing', {}).get('frameworks') or []
                auto_actions = []

                # Heuristic auto-insights (in case KI bleibt knapp)
                auto_insights = []
                if doc_cov is not None and doc_cov < 40:
                    auto_insights.append(f"Doku-Quote niedrig: {doc_cov}%")
                if largest_files:
                    biggest = largest_files[0]['lines']
                    if biggest > 800:
                        auto_insights.append(f"Große Datei entdeckt: {largest_files[0]['module']} ({biggest} LOC)")
                if summary.get('total_files', 0) > 0:
                    avg_loc = round(summary.get('total_lines', 0) / summary.get('total_files', 1), 1)
                    if avg_loc > 300:
                        auto_insights.append(f"Hohe durchschnittliche Dateigröße: {avg_loc} LOC/Datei")
                if test_files is not None and test_files == 0:
                    auto_insights.append("Keine Test-Dateien erkannt")
                elif test_files is not None and test_files < 5:
                    auto_insights.append(f"Wenige Test-Dateien: {test_files}")
                    auto_actions.append("Mehr Tests hinzufügen (wenige Test-Dateien erkannt)")
                js_funcs = metrics.get('js_ts', {}).get('functions')
                if js_funcs:
                    auto_insights.append(f"JS/TS Funktionen erkannt: {js_funcs}")
                if coverage is not None and coverage < 60:
                    auto_insights.append(f"Niedrige Test-Coverage: {coverage}%")
                    auto_actions.append("Coverage erhöhen (aktuell niedrig)")
                if coverage is None and test_files == 0:
                    auto_insights.append("Keine Coverage-Reports gefunden")
                if frameworks:
                    auto_insights.append(f"Test-Frameworks: {', '.join(frameworks)}")
                cycles = results.get('dependencies', {}).get('cycles', [])
                if cycles:
                    auto_insights.append(f"Import-Zyklen entdeckt: {len(cycles)}")
                    auto_actions.append("Import-Zyklen prüfen und abbauen")
                    # Show first cycle preview
                    cycle_preview = " -> ".join(cycles[0][:8])
                    auto_insights.append(f"Cycle-Pfad: {cycle_preview}...")

                stats_summary = f"""Projekt: {project_name}
Files: {summary.get('total_files', 0)}
Lines of Code: {summary.get('total_lines', 0)}
Doc Coverage: {doc_cov if doc_cov is not None else 'n/a'}%
Entry Points: {len(results.get('structure', {}).get('entry_points', []))}
Größte Files: {largest_files_text or 'n/a'}
Externe Dependencies: {len(results.get('dependencies', {}).get('external_dependencies', {}))}
Tests: {test_files if test_files is not None else 'n/a'}
JS/TS: funcs={js_funcs if js_funcs is not None else 'n/a'}, classes={metrics.get('js_ts', {}).get('classes', 'n/a')}, exports={metrics.get('js_ts', {}).get('exports', 'n/a')}
Coverage: {coverage if coverage is not None else 'n/a'}
Frameworks: {', '.join(frameworks) if frameworks else 'n/a'}
Import-Zyklen: {len(cycles) if cycles else 0}
Hauptsprache: Mixed (py/ts/js möglich)"""

                prompt = f"""Analysiere diese Code-Statistiken und gib 2-3 kurze Bullet-Insights (Architektur/Hotspots/Risiken) auf Deutsch:

{stats_summary}

Liefer konkrete Hinweise für Stabilität, Wartbarkeit oder Security (keine Floskeln)."""

                description = None
                confidence = 0.65

                try:
                    analysis = await self.ai_service.get_ai_analysis(
                        prompt=prompt,
                        context="",
                        use_critical_model=False
                    )

                    if analysis and len(analysis) > 20:
                        description = analysis[:400]
                        confidence = 0.7

                except Exception as e:
                    self.logger.debug(f"Ollama analysis failed for {project_name}: {e}")

                if not description:
                    description = (
                        f"Automatische Kurz-Einschätzung ohne KI: "
                        f"{summary.get('total_files', 0)} Dateien, "
                        f"{summary.get('total_lines', 0)} LOC im Projekt {project_name}, "
                        f"Doc-Coverage: {doc_cov if doc_cov is not None else 'n/a'}%, "
                        f"größte Files: {largest_files_text or 'n/a'}."
                    )[:400]
                else:
                    if auto_insights:
                        description = (description + "\n\nAutomatische Hinweise:\n- " + "\n- ".join(auto_insights))[:500]
                    if auto_actions:
                        description = (description + "\n\nAction Board:\n- " + "\n- ".join(auto_actions))[:700]
                    if trend_text:
                        description = (description + f"\n\nTrends: {trend_text}")[:800]

                # Alert channels for critical test/coverage gaps
                if (coverage is not None and coverage < 60) or (test_files is not None and test_files == 0):
                    await self._send_learning_message(
                        f"⚠️ **Qualitäts-Alert {project_name}**\n"
                        f"Coverage: {coverage if coverage is not None else 'n/a'} | Tests: {test_files if test_files is not None else 'n/a'}",
                        color=0xE74C3C
                    )

                # Post Auto-Fix Proposal in ai_code_scans if Actions vorhanden
                try:
                    if hasattr(self.bot, "auto_fix_manager") and (auto_actions or coverage is not None and coverage < 60 or (test_files is not None and test_files == 0)):
                        summary_line = f"Hotspots: {largest_files_text or 'n/a'}, Coverage: {coverage if coverage is not None else 'n/a'}%, Doc: {doc_cov if doc_cov is not None else 'n/a'}%"
                        actions_for_proposal = auto_actions or []
                        proposal = FixProposal(
                            project=project_name,
                            summary=summary_line,
                            actions=actions_for_proposal,
                            tests=[],  # Manager nutzt Defaults
                            suggested_tests=[]
                        )
                        await self.bot.auto_fix_manager.post_proposal(self.bot, proposal)
                except Exception as e:
                    self.logger.debug(f"Could not post auto-fix proposal: {e}")

                insight = LearningInsight(
                    insight_id=f"code_{project_name}_{int(datetime.utcnow().timestamp())}",
                    category="code_pattern",
                    title=f"Code-Analyse: {project_name}",
                    description=description,
                    confidence=confidence,
                    data={
                        "project": project_name,
                        "files": summary.get('total_files', 0),
                        "loc": summary.get('total_lines', 0)
                    }
                )
                insights.append(insight)

        except Exception as e:
            self.logger.error(f"Error analyzing code patterns: {e}", exc_info=True)

        return insights

    async def _get_learning_channel(self):
        """Get the AI learning channel"""
        channel = self.bot.get_channel(self.learning_channel_id)
        if not channel and self._learning_channel_fallback:
            channel = self.bot.get_channel(self._learning_channel_fallback)
            if channel:
                self.learning_channel_id = self._learning_channel_fallback
        return channel

    async def _send_learning_message(self, message: str, color: int = 0x3498DB, pin: bool = False):
        """Send a message to the AI learning channel"""
        try:
            channel = await self._get_learning_channel()
            if not channel:
                self.logger.warning("⚠️ AI learning channel not found")
                return

            import discord
            embed = discord.Embed(
                description=message,
                color=color,
                timestamp=datetime.utcnow()
            )
            embed.set_footer(text="Continuous Learning Agent")

            msg = await channel.send(embed=embed)
            if pin:
                try:
                    await msg.pin()
                except Exception:
                    self.logger.debug("Could not pin message")

        except Exception as e:
            self.logger.error(f"Error sending learning message: {e}")

    async def _send_insight_notification(self, insight: LearningInsight):
        """Send immediate notification for high-confidence insights"""
        try:
            channel = await self._get_learning_channel()
            if not channel:
                return

            import discord
            embed = discord.Embed(
                title=f"🔍 Neue Erkenntnis: {insight.category}",
                color=0xFFD700,
                timestamp=insight.discovered_at
            )

            field = insight.to_embed_field()
            embed.add_field(
                name=field['name'],
                value=field['value'],
                inline=field['inline']
            )

            await channel.send(embed=embed)

        except Exception as e:
            self.logger.error(f"Error sending insight notification: {e}")

    async def _send_learning_report(self, pin: bool = False):
        """Send periodic learning report to Discord"""
        try:
            channel = await self._get_learning_channel()
            if not channel:
                return

            import discord

            uptime = datetime.utcnow() - self.start_time
            uptime_hours = uptime.total_seconds() / 3600

            # Build report
            embed = discord.Embed(
                title="📊 AI Learning Report",
                description=f"Zusammenfassung der letzten {self.report_interval//3600} Stunden",
                color=0x00FF00,
                timestamp=datetime.utcnow()
            )

            # Statistics
            embed.add_field(
                name="📈 Statistiken",
                value=(
                    f"**Sessions:** {self.total_sessions}\n"
                    f"**Insights:** {self.total_insights}\n"
                    f"**Uptime:** {uptime_hours:.1f}h"
                ),
                inline=True
            )

            # Recent insights
            if self.insights_queue:
                recent = self.insights_queue[-3:]
                insights_text = "\n".join([
                    f"• {i.title[:40]}..." if len(i.title) > 40 else f"• {i.title}"
                    for i in recent
                ])
                embed.add_field(
                    name="🔍 Letzte Erkenntnisse",
                    value=insights_text or "Keine neuen Erkenntnisse",
                    inline=False
                )

            # System status
            if hasattr(self.bot, 'project_monitor') and self.bot.project_monitor:
                projects = self.bot.project_monitor.projects
                online = sum(1 for p in projects.values() if p.is_online)
                total = len(projects)

            embed.add_field(
                name="🖥️ System Status",
                value=f"**Projects:** {online}/{total} online",
                inline=True
            )

            embed.set_footer(text="Continuous Learning Agent")

            msg = await channel.send(embed=embed)
            if pin:
                try:
                    await msg.pin()
                except Exception:
                    self.logger.debug("Could not pin report message")

            # Clear old insights from queue (keep last 50)
            if len(self.insights_queue) > 50:
                self.insights_queue = self.insights_queue[-50:]

        except Exception as e:
            self.logger.error(f"Error sending learning report: {e}", exc_info=True)

    async def _knowledge_synthesis_loop(self):
        """
        🧠 Knowledge Synthesis Loop - Extracts long-term patterns from tracking data.

        This is the key to continuous improvement over months/years:
        - Runs every 6 hours (4x per day)
        - Extracts patterns from Auto-Fix, RAM, and Security tracking
        - Stores compressed knowledge in persistent knowledge base
        - Enables meta-learning (learning about learning)

        The knowledge base grows indefinitely while raw data is pruned.
        """
        # Wait 1 hour after startup before first synthesis
        await asyncio.sleep(3600)

        while self.is_running:
            try:
                self.logger.info("🧠 Starting knowledge synthesis...")

                # Run synthesis
                stats = await self.knowledge_synthesizer.synthesize_knowledge()

                # Send Discord notification about synthesis
                if stats["fix_patterns_extracted"] > 0 or stats["ram_patterns_extracted"] > 0:
                    await self._send_synthesis_notification(stats)

                self.logger.info(
                    f"✅ Knowledge synthesis complete: "
                    f"{sum(stats.values())} total patterns extracted"
                )

                # Wait for next synthesis
                await asyncio.sleep(self.synthesis_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"❌ Error in knowledge synthesis loop: {e}", exc_info=True)
                await asyncio.sleep(3600)  # Wait 1 hour on error

    async def _send_synthesis_notification(self, stats: Dict):
        """
        Send Discord notification about knowledge synthesis.

        Args:
            stats: Synthesis statistics
        """
        try:
            channel = await self._get_learning_channel()
            if not channel:
                return

            embed = discord.Embed(
                title="🧠 Knowledge Synthesis Complete",
                description="Long-term patterns extracted from learning data",
                color=0x9B59B6,  # Purple
                timestamp=datetime.utcnow()
            )

            # Add stats
            if stats["fix_patterns_extracted"] > 0:
                embed.add_field(
                    name="📊 Auto-Fix Patterns",
                    value=f"**{stats['fix_patterns_extracted']}** patterns extracted\n"
                          f"Success rates calculated per project",
                    inline=True
                )

            if stats["ram_patterns_extracted"] > 0:
                embed.add_field(
                    name="🧠 RAM Management Patterns",
                    value=f"**{stats['ram_patterns_extracted']}** patterns extracted\n"
                          f"Best cleanup methods identified",
                    inline=True
                )

            if stats["security_patterns_extracted"] > 0:
                embed.add_field(
                    name="🛡️ Security Patterns",
                    value=f"**{stats['security_patterns_extracted']}** patterns extracted\n"
                          f"Attack trends analyzed",
                    inline=True
                )

            if stats["meta_insights"] > 0:
                embed.add_field(
                    name="🚀 Meta-Learning",
                    value=f"**{stats['meta_insights']}** meta-insights generated\n"
                          f"Learning velocity calculated",
                    inline=False
                )

            # Add knowledge base stats
            kb = self.knowledge_synthesizer.knowledge
            total_projects = len(kb["fix_patterns"])
            total_models = len(kb["ram_patterns"])

            embed.add_field(
                name="📚 Knowledge Base Stats",
                value=f"**Projects tracked:** {total_projects}\n"
                      f"**Models tracked:** {total_models}\n"
                      f"**Total syntheses:** {kb['synthesis_count']}",
                inline=False
            )

            # Learning velocity if available
            if kb["meta_learning"].get("learning_velocity"):
                velocity = kb["meta_learning"]["learning_velocity"]
                embed.add_field(
                    name="📈 Learning Velocity",
                    value=f"**{velocity:.2f}** patterns per day\n"
                          f"System is continuously improving!",
                    inline=False
                )

            embed.set_footer(text="Knowledge Synthesizer • Long-term Learning")

            await channel.send(embed=embed)

        except Exception as e:
            self.logger.error(f"Error sending synthesis notification: {e}", exc_info=True)
