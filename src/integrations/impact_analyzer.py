"""
Impact Analyzer - Project Impact Analysis

Analyzes the impact of security fixes on running projects:
- Identifies affected projects
- Determines service dependencies
- Assesses downtime risk
- Generates coordination plan
- Validates against DO-NOT-TOUCH rules
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .command_executor import CommandExecutor

logger = logging.getLogger('shadowops.impact_analyzer')


class ImpactSeverity(Enum):
    """Severity of impact on a project"""
    NONE = "none"           # No impact
    MINIMAL = "minimal"     # Minor changes, no service restart
    MODERATE = "moderate"   # Service restart required
    SIGNIFICANT = "significant"  # Downtime expected
    CRITICAL = "critical"   # Customer-facing outage


class ProjectStatus(Enum):
    """Current status of a project"""
    RUNNING = "running"
    STOPPED = "stopped"
    UNKNOWN = "unknown"
    ERROR = "error"


@dataclass
class ProjectInfo:
    """Information about a project"""
    name: str
    path: str
    priority: int  # 1 = highest, 3 = lowest
    status: ProjectStatus
    processes: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    ports: List[int] = field(default_factory=list)
    critical_paths: Set[str] = field(default_factory=set)
    safe_operations: Set[str] = field(default_factory=set)
    requires_approval: Set[str] = field(default_factory=set)


@dataclass
class ImpactAssessment:
    """Assessment of fix impact on projects"""
    affected_projects: List[str]
    impact_severity: ImpactSeverity
    downtime_estimate_seconds: int
    risks: List[str]
    mitigation_steps: List[str]
    service_order: List[str]  # Order to stop/start services
    requires_approval: bool
    approval_reason: Optional[str] = None


class ImpactAnalyzer:
    """
    Analyzes the impact of security fixes on running projects

    Features:
    - Project dependency tracking
    - Service status monitoring
    - Downtime estimation
    - Risk assessment
    - Coordination planning
    - DO-NOT-TOUCH validation
    """

    def __init__(self, executor: Optional[CommandExecutor] = None):
        """
        Initialize impact analyzer

        Args:
            executor: Command executor (creates one if not provided)
        """
        self.executor = executor or CommandExecutor()

        # Define projects (based on context files)
        self.projects = {
            'shadowops-bot': ProjectInfo(
                name='shadowops-bot',
                path='/home/cmdshadow/shadowops-bot',
                priority=1,  # Highest priority
                status=ProjectStatus.UNKNOWN,
                processes=['python', 'shadowops'],
                dependencies=[],  # No dependencies
                ports=[],
                critical_paths={
                    '/home/cmdshadow/shadowops-bot/config',
                    '/home/cmdshadow/shadowops-bot/logs/seen_events.json'
                },
                safe_operations={
                    'log_rotation',
                    'cache_cleanup',
                    'event_analysis'
                },
                requires_approval={
                    'config_changes',
                    'ai_service_changes',
                    'database_changes'
                }
            ),
            'guildscout': ProjectInfo(
                name='guildscout',
                path='/home/cmdshadow/GuildScout',
                priority=2,
                status=ProjectStatus.UNKNOWN,
                processes=['python', 'guildscout'],
                dependencies=[],
                ports=[],
                critical_paths={
                    '/home/cmdshadow/GuildScout/config',
                    '/home/cmdshadow/GuildScout/data'  # SQLite cache
                },
                safe_operations={
                    'log_analysis',
                    'cache_stats',
                    'csv_export'
                },
                requires_approval={
                    'database_changes',
                    'role_assignment',
                    'config_changes'
                }
            ),
            'sicherheitstool': ProjectInfo(
                name='sicherheitstool',
                path='/home/cmdshadow/project',
                priority=3,  # Lower priority for now (production later)
                status=ProjectStatus.UNKNOWN,
                processes=['node', 'npm'],
                dependencies=['postgresql'],
                ports=[3001],
                critical_paths={
                    '/home/cmdshadow/project/prisma',
                    '/home/cmdshadow/project/.env',
                    '/home/cmdshadow/project/package.json'
                },
                safe_operations={
                    'log_analysis',
                    'read_only_queries',
                    'metrics_collection'
                },
                requires_approval={
                    'database_schema',
                    'auth_changes',
                    'api_changes',
                    'service_restart'
                }
            ),
            'nexus': ProjectInfo(
                name='nexus',
                path='/opt/nexus',  # Typical Nexus location
                priority=2,
                status=ProjectStatus.UNKNOWN,
                processes=['nexus', 'java'],
                dependencies=[],
                ports=[8081],  # Default Nexus port
                critical_paths={
                    '/opt/nexus/data',
                    '/opt/nexus/etc'
                },
                safe_operations={
                    'log_analysis',
                    'metrics_collection'
                },
                requires_approval={
                    'config_changes',
                    'repository_changes',
                    'service_restart'
                }
            )
        }

        # System-wide protected paths
        self.protected_paths = {
            '/etc/passwd',
            '/etc/shadow',
            '/etc/ssh',
            '/boot',
            '/etc/systemd/system',
            '/etc/postgresql'
        }

        logger.info(f"🔍 Impact Analyzer initialized ({len(self.projects)} projects tracked)")

    async def analyze_impact(
        self,
        event_source: str,
        event_type: str,
        affected_paths: Optional[List[str]] = None,
        fix_strategy: Optional[Dict] = None
    ) -> ImpactAssessment:
        """
        Analyze impact of a security fix

        Args:
            event_source: Source of event (trivy, crowdsec, fail2ban, aide)
            event_type: Type of event
            affected_paths: Paths that will be modified
            fix_strategy: Planned fix strategy

        Returns:
            ImpactAssessment with detailed analysis
        """
        logger.info(f"🔍 Analyzing impact: {event_source} / {event_type}")

        # Update project statuses
        await self._update_project_statuses()

        # Determine affected projects
        affected_projects = self._determine_affected_projects(
            event_source,
            event_type,
            affected_paths,
            fix_strategy
        )

        # Assess impact severity
        severity = self._assess_severity(
            event_source,
            affected_projects,
            affected_paths,
            fix_strategy
        )

        # Estimate downtime
        downtime = self._estimate_downtime(affected_projects, severity, fix_strategy)

        # Identify risks
        risks = self._identify_risks(
            event_source,
            affected_projects,
            affected_paths,
            fix_strategy
        )

        # Generate mitigation steps
        mitigation = self._generate_mitigation(
            affected_projects,
            risks,
            fix_strategy
        )

        # Determine service order
        service_order = self._determine_service_order(affected_projects)

        # Check approval requirement
        requires_approval, approval_reason = self._check_approval_requirement(
            event_source,
            affected_projects,
            affected_paths,
            severity,
            fix_strategy
        )

        assessment = ImpactAssessment(
            affected_projects=affected_projects,
            impact_severity=severity,
            downtime_estimate_seconds=downtime,
            risks=risks,
            mitigation_steps=mitigation,
            service_order=service_order,
            requires_approval=requires_approval,
            approval_reason=approval_reason
        )

        logger.info(f"✅ Impact analysis complete:")
        logger.info(f"   Projects: {len(affected_projects)}")
        logger.info(f"   Severity: {severity.value}")
        logger.info(f"   Downtime: {downtime}s")
        logger.info(f"   Approval: {requires_approval}")

        return assessment

    async def _update_project_statuses(self):
        """
        Update running status of all projects in parallel

        Uses asyncio.gather() for concurrent status checks to improve performance.
        Each project status is independent, so no race conditions occur.
        """
        # Create tasks for parallel execution
        tasks = [
            self._check_single_project_status(project_name, project)
            for project_name, project in self.projects.items()
        ]

        # Execute all status checks concurrently
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_single_project_status(self, project_name: str, project: 'ProjectInfo'):
        """
        Check status of a single project (helper for parallel execution)

        This method is safe for concurrent execution as each project has its own status.
        No shared state is modified, preventing race conditions.
        """
        try:
            # Check if project processes are running
            for process_name in project.processes:
                result = await self.executor.execute(
                    f"pgrep -f '{process_name}' > /dev/null 2>&1",
                    timeout=5
                )

                if result.success:
                    project.status = ProjectStatus.RUNNING
                    logger.debug(f"✅ {project_name}: RUNNING")
                    break
            else:
                # No processes found
                project.status = ProjectStatus.STOPPED
                logger.debug(f"⏸️ {project_name}: STOPPED")

        except Exception as e:
            logger.warning(f"⚠️ Failed to check {project_name} status: {e}")
            project.status = ProjectStatus.UNKNOWN

    def _determine_affected_projects(
        self,
        event_source: str,
        event_type: str,
        affected_paths: Optional[List[str]],
        fix_strategy: Optional[Dict]
    ) -> List[str]:
        """Determine which projects are affected by the fix"""
        affected = set()

        # Source-based determination
        if event_source == 'trivy':
            # Docker vulnerabilities might affect all projects using Docker
            # For now, prioritize ShadowOps and GuildScout
            affected.add('shadowops-bot')
            affected.add('guildscout')

        elif event_source == 'crowdsec' or event_source == 'fail2ban':
            # Network security - might affect Sicherheitstool (API)
            # But also ShadowOps (monitoring)
            affected.add('shadowops-bot')

        elif event_source == 'aide':
            # File integrity - check affected paths
            if affected_paths:
                for path in affected_paths:
                    for project_name, project in self.projects.items():
                        if path.startswith(project.path):
                            affected.add(project_name)

        # Path-based determination
        if affected_paths:
            for path in affected_paths:
                for project_name, project in self.projects.items():
                    # Check if path is in project directory
                    if path.startswith(project.path):
                        affected.add(project_name)

                    # Check if path is critical for project
                    for critical_path in project.critical_paths:
                        if path.startswith(critical_path):
                            affected.add(project_name)

        # Strategy-based determination
        if fix_strategy:
            strategy_desc = fix_strategy.get('description', '').lower()

            # Check for project mentions in strategy
            for project_name in self.projects.keys():
                if project_name.lower() in strategy_desc:
                    affected.add(project_name)

            # Check for common operations
            if 'docker' in strategy_desc or 'container' in strategy_desc:
                affected.add('shadowops-bot')
                affected.add('guildscout')

            if 'database' in strategy_desc or 'postgres' in strategy_desc:
                affected.add('sicherheitstool')

            if 'npm' in strategy_desc or 'node' in strategy_desc:
                affected.add('sicherheitstool')

        # Always include shadowops-bot if nothing else affected
        # (it monitors the system)
        if not affected:
            affected.add('shadowops-bot')

        return sorted(list(affected), key=lambda p: self.projects[p].priority)

    def _assess_severity(
        self,
        event_source: str,
        affected_projects: List[str],
        affected_paths: Optional[List[str]],
        fix_strategy: Optional[Dict]
    ) -> ImpactSeverity:
        """Assess severity of impact"""

        # Check for protected path modifications
        if affected_paths:
            for path in affected_paths:
                if any(path.startswith(pp) for pp in self.protected_paths):
                    return ImpactSeverity.CRITICAL

        # Check for production system impact
        if 'sicherheitstool' in affected_projects:
            return ImpactSeverity.SIGNIFICANT

        # Check strategy for risky operations
        if fix_strategy:
            strategy = fix_strategy.get('description', '').lower()

            if any(word in strategy for word in ['database', 'schema', 'migration']):
                return ImpactSeverity.CRITICAL

            if any(word in strategy for word in ['restart', 'reload', 'stop']):
                return ImpactSeverity.MODERATE

            if any(word in strategy for word in ['update', 'upgrade', 'rebuild']):
                return ImpactSeverity.MODERATE

        # Event source based severity
        if event_source == 'aide':
            return ImpactSeverity.SIGNIFICANT  # File integrity issues are serious

        if event_source == 'trivy':
            return ImpactSeverity.MODERATE  # Vulnerabilities need fixing

        if event_source in ['crowdsec', 'fail2ban']:
            return ImpactSeverity.MINIMAL  # Network security, usually safe

        return ImpactSeverity.MINIMAL

    def _estimate_downtime(
        self,
        affected_projects: List[str],
        severity: ImpactSeverity,
        fix_strategy: Optional[Dict]
    ) -> int:
        """Estimate downtime in seconds"""

        base_downtime = {
            ImpactSeverity.NONE: 0,
            ImpactSeverity.MINIMAL: 0,
            ImpactSeverity.MODERATE: 30,
            ImpactSeverity.SIGNIFICANT: 60,
            ImpactSeverity.CRITICAL: 120
        }

        downtime = base_downtime[severity]

        # Add time per affected project
        downtime += len(affected_projects) * 10

        # Add time for specific operations
        if fix_strategy:
            strategy = fix_strategy.get('description', '').lower()

            if 'rebuild' in strategy or 'compile' in strategy:
                downtime += 120  # 2 minutes for rebuild

            if 'database' in strategy:
                downtime += 60  # 1 minute for DB operations

            if 'restart' in strategy:
                downtime += 15  # 15 seconds per restart

        return downtime

    def _identify_risks(
        self,
        event_source: str,
        affected_projects: List[str],
        affected_paths: Optional[List[str]],
        fix_strategy: Optional[Dict]
    ) -> List[str]:
        """Identify potential risks"""
        risks = []

        # Protected path risks
        if affected_paths:
            for path in affected_paths:
                if any(path.startswith(pp) for pp in self.protected_paths):
                    risks.append(f"⚠️ Modifying protected system path: {path}")

        # Project-specific risks
        for project_name in affected_projects:
            project = self.projects[project_name]

            if project.status == ProjectStatus.RUNNING:
                risks.append(f"⚠️ {project_name} is currently running, may need restart")

            if project_name == 'sicherheitstool':
                risks.append(f"⚠️ {project_name} is PRODUCTION, customer impact possible")

            # Check critical paths
            if affected_paths:
                for path in affected_paths:
                    for critical_path in project.critical_paths:
                        if path.startswith(critical_path):
                            risks.append(
                                f"⚠️ Modifying critical path for {project_name}: {path}"
                            )

        # Strategy-based risks
        if fix_strategy:
            strategy = fix_strategy.get('description', '').lower()

            if 'database' in strategy or 'schema' in strategy:
                risks.append("⚠️ Database modifications carry data loss risk")

            if 'irreversible' in strategy or 'permanent' in strategy:
                risks.append("⚠️ Operation may be irreversible")

            if fix_strategy.get('confidence', 1.0) < 0.85:
                risks.append(
                    f"⚠️ Low AI confidence ({fix_strategy.get('confidence', 0):.0%})"
                )

        return risks

    def _generate_mitigation(
        self,
        affected_projects: List[str],
        risks: List[str],
        fix_strategy: Optional[Dict]
    ) -> List[str]:
        """Generate mitigation steps"""
        mitigation = []

        # Always backup first
        mitigation.append("💾 Create comprehensive backup before changes")

        # Project-specific mitigation
        for project_name in affected_projects:
            project = self.projects[project_name]

            if project.status == ProjectStatus.RUNNING:
                mitigation.append(f"⏸️ Gracefully stop {project_name}")

            if project_name == 'sicherheitstool':
                mitigation.append(f"📢 Notify customers of maintenance window")

        # Risk-based mitigation
        if any('database' in risk.lower() for risk in risks):
            mitigation.append("💾 Create database dump before modification")

        if any('production' in risk.lower() for risk in risks):
            mitigation.append("🧪 Test changes in development environment first")

        # Always verify and restart
        mitigation.append("✅ Verify fix success before proceeding")
        mitigation.append("🔄 Restart affected services in correct order")
        mitigation.append("🏥 Run health checks after restart")

        return mitigation

    def _determine_service_order(self, affected_projects: List[str]) -> List[str]:
        """Determine order to stop/start services"""

        # Stop order: reverse dependency order
        # Start order: dependency order

        # Build dependency graph
        order = []

        # Priority-based ordering (highest priority last to stop, first to start)
        sorted_projects = sorted(
            affected_projects,
            key=lambda p: self.projects[p].priority,
            reverse=True  # Lowest priority first (stop first)
        )

        # Handle dependencies
        for project_name in sorted_projects:
            project = self.projects[project_name]

            # Add dependencies first
            for dep in project.dependencies:
                if dep not in order:
                    order.append(dep)

            # Then add project
            if project_name not in order:
                order.append(project_name)

        return order

    def _check_approval_requirement(
        self,
        event_source: str,
        affected_projects: List[str],
        affected_paths: Optional[List[str]],
        severity: ImpactSeverity,
        fix_strategy: Optional[Dict]
    ) -> Tuple[bool, Optional[str]]:
        """Check if human approval is required (Tuple for Python 3.9 compatibility)"""

        # Always require approval for CRITICAL severity
        if severity == ImpactSeverity.CRITICAL:
            return True, "Critical impact severity"

        # Check protected paths
        if affected_paths:
            for path in affected_paths:
                if any(path.startswith(pp) for pp in self.protected_paths):
                    return True, f"Protected system path: {path}"

        # Check project-specific approval requirements
        for project_name in affected_projects:
            project = self.projects[project_name]

            if project_name == 'sicherheitstool':
                return True, "Production system affected"

            # Check if operation requires approval
            if fix_strategy:
                strategy = fix_strategy.get('description', '').lower()

                for approval_op in project.requires_approval:
                    if approval_op.replace('_', ' ') in strategy:
                        return True, f"{project_name}: {approval_op} requires approval"

        # Check confidence level
        if fix_strategy:
            confidence = fix_strategy.get('confidence', 0)
            if confidence < 0.85:
                return True, f"Low confidence ({confidence:.0%})"

        # AIDE always requires approval (file integrity)
        if event_source == 'aide':
            return True, "File integrity changes require review"

        # Default: approval required (PARANOID mode)
        return True, "PARANOID mode: all changes require approval"

    def get_project_info(self, project_name: str) -> Optional[ProjectInfo]:
        """Get information about a project"""
        return self.projects.get(project_name)

    def list_projects(self) -> List[str]:
        """List all tracked projects"""
        return sorted(self.projects.keys(), key=lambda p: self.projects[p].priority)

    async def get_project_status(self, project_name: str) -> ProjectStatus:
        """Get current status of a project"""
        if project_name not in self.projects:
            return ProjectStatus.UNKNOWN

        await self._update_project_statuses()
        return self.projects[project_name].status

    def get_stats(self) -> Dict:
        """Get impact analyzer statistics"""
        running_count = sum(
            1 for p in self.projects.values()
            if p.status == ProjectStatus.RUNNING
        )

        return {
            'total_projects': len(self.projects),
            'running_projects': running_count,
            'protected_paths': len(self.protected_paths),
            'projects': [
                {
                    'name': p.name,
                    'priority': p.priority,
                    'status': p.status.value,
                    'path': p.path
                }
                for p in sorted(self.projects.values(), key=lambda x: x.priority)
            ]
        }
