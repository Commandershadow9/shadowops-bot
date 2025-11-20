"""
Context Manager for RAG (Retrieval-Augmented Generation)

Loads and manages project knowledge base for context-aware AI analysis.
Provides relevant context to AI models for better security decision-making.
"""

import os
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger('shadowops.context')

# Import Git History Analyzer for continuous learning from commits
try:
    from .git_history_analyzer import GitHistoryAnalyzer
    GIT_ANALYZER_AVAILABLE = True
except ImportError:
    GIT_ANALYZER_AVAILABLE = False
    logger.warning("⚠️ Git History Analyzer not available")

# Import Log Analyzer for continuous learning from tool logs
try:
    from .log_analyzer import LogAnalyzer
    LOG_ANALYZER_AVAILABLE = True
except ImportError:
    LOG_ANALYZER_AVAILABLE = False
    logger.warning("⚠️ Log Analyzer not available")


class ContextManager:
    """Manages project context and infrastructure knowledge with Git history learning"""

    def __init__(self, enable_git_learning: bool = True, git_history_days: int = 30):
        """
        Args:
            enable_git_learning: Enable Git history analysis for learning
            git_history_days: How many days of Git history to analyze
        """
        self.context_dir = Path(__file__).parent.parent.parent / 'context'
        self.projects: Dict[str, str] = {}
        self.infrastructure: str = ""
        self.loaded = False

        # === GIT HISTORY LEARNING ===
        self.enable_git_learning = enable_git_learning and GIT_ANALYZER_AVAILABLE
        self.git_analyzers: Dict[str, GitHistoryAnalyzer] = {}  # {project_name: analyzer}
        self.git_history_days = git_history_days

        if self.enable_git_learning:
            logger.info(f"🧠 Git Learning enabled: Analyzing last {git_history_days} days")
        else:
            if not GIT_ANALYZER_AVAILABLE:
                logger.warning("⚠️ Git Learning disabled: GitHistoryAnalyzer not available")
            else:
                logger.info("ℹ️ Git Learning disabled by config")

        # === LOG LEARNING (disabled for now - will be enabled in future) ===
        self.log_analyzer = None  # TODO: Enable log learning in next update

    def load_all_contexts(self):
        """Load all project and system contexts + initialize Git learning"""
        try:
            logger.info("📚 Loading project knowledge base...")

            # Load project contexts
            projects_dir = self.context_dir / 'projects'
            if projects_dir.exists():
                for project_file in projects_dir.glob('*.md'):
                    project_name = project_file.stem
                    with open(project_file, 'r', encoding='utf-8') as f:
                        self.projects[project_name] = f.read()
                    logger.info(f"✅ Loaded context for: {project_name}")

            # Load infrastructure context
            infra_file = self.context_dir / 'system' / 'infrastructure.md'
            if infra_file.exists():
                with open(infra_file, 'r', encoding='utf-8') as f:
                    self.infrastructure = f.read()
                logger.info("✅ Loaded infrastructure context")

            # === INITIALIZE GIT LEARNING ===
            if self.enable_git_learning:
                self._initialize_git_analyzers()

            self.loaded = True
            logger.info(f"📚 Knowledge base ready: {len(self.projects)} projects loaded")

        except Exception as e:
            logger.error(f"❌ Failed to load contexts: {e}")
            raise

    def _initialize_git_analyzers(self):
        """Initialize Git analyzers for known project paths"""
        logger.info("🧠 Initializing Git learning for projects...")

        # Map project names to their Git repository paths
        # These paths should come from config in production
        project_paths = {
            'shadowops-bot': Path(__file__).parent.parent.parent,  # Current bot repo
            # Add more projects as needed from config
            # 'sicherheitstool': Path('/home/cmdshadow/project/sicherheitstool'),
            # 'guildscout': Path('/home/cmdshadow/project/GuildScout'),
        }

        for project_name, project_path in project_paths.items():
            try:
                analyzer = GitHistoryAnalyzer(str(project_path), self.git_history_days)

                if analyzer.is_git_repository():
                    # Load commits immediately for caching
                    commits = analyzer.load_commit_history()
                    self.git_analyzers[project_name] = analyzer
                    logger.info(f"✅ Git learning active for {project_name} ({len(commits)} commits)")
                else:
                    logger.debug(f"⏭️ Skipping {project_name}: Not a git repository")

            except Exception as e:
                logger.warning(f"⚠️ Could not initialize Git learning for {project_name}: {e}")

        if self.git_analyzers:
            logger.info(f"🧠 Git learning ready for {len(self.git_analyzers)} project(s)")
        else:
            logger.warning("⚠️ No Git repositories found for learning")

    def get_relevant_context(self, event_source: str, event_type: str) -> str:
        """
        Get relevant context for a security event (WITH GIT LEARNING!)

        Args:
            event_source: Source of event (trivy, crowdsec, fail2ban, aide)
            event_type: Type of event (docker_vulnerabilities, threat, etc.)

        Returns:
            Formatted context string for AI analysis
        """
        if not self.loaded:
            self.load_all_contexts()

        context_parts = []

        # === GIT HISTORY LEARNING CONTEXT (NEW!) ===
        if self.enable_git_learning and self.git_analyzers:
            git_context = self._get_git_learning_context(event_source, event_type)
            if git_context:
                context_parts.append(git_context)
                context_parts.append("")

        # Always include infrastructure context
        context_parts.append("# SERVER INFRASTRUCTURE & SECURITY POLICIES")
        context_parts.append(self.infrastructure)
        context_parts.append("")

        # Determine which projects are relevant based on event
        relevant_projects = self._determine_relevant_projects(event_source, event_type)

        if relevant_projects:
            context_parts.append("# RELEVANT RUNNING PROJECTS")
            for project_name in relevant_projects:
                if project_name in self.projects:
                    context_parts.append(f"\n## {project_name.upper()}")
                    context_parts.append(self.projects[project_name])
                    context_parts.append("")

        # Add all other projects as general context
        other_projects = set(self.projects.keys()) - set(relevant_projects)
        if other_projects:
            context_parts.append("# OTHER RUNNING PROJECTS (For Reference)")
            for project_name in sorted(other_projects):
                context_parts.append(f"\n## {project_name.upper()}")
                # Only include summary, not full context
                summary = self._extract_summary(self.projects[project_name])
                context_parts.append(summary)
                context_parts.append("")

        return "\n".join(context_parts)

    def _get_git_learning_context(self, event_source: str, event_type: str) -> str:
        """
        Generate Git history learning context for AI

        Args:
            event_source: Event source (trivy, fail2ban, etc.)
            event_type: Event type

        Returns:
            Formatted Git history context
        """
        if not self.git_analyzers:
            return ""

        # Map event source to relevant keywords
        keywords_map = {
            'trivy': ['docker', 'vulnerability', 'CVE', 'security'],
            'fail2ban': ['ssh', 'ban', 'fail2ban', 'security'],
            'crowdsec': ['crowdsec', 'threat', 'ban', 'security'],
            'aide': ['file', 'integrity', 'aide', 'changes']
        }

        keywords = keywords_map.get(event_source, [event_source])

        # Collect Git context from all available analyzers
        git_contexts = []

        for project_name, analyzer in self.git_analyzers.items():
            try:
                context = analyzer.generate_context_for_ai(event_source, keywords)
                if context and len(context) > 50:  # Only include if meaningful
                    git_contexts.append(f"\n## Git History: {project_name.upper()}\n{context}")
            except Exception as e:
                logger.debug(f"Could not generate git context for {project_name}: {e}")

        if git_contexts:
            return '\n'.join(git_contexts)

        return ""

    def _determine_relevant_projects(self, event_source: str, event_type: str) -> List[str]:
        """Determine which projects are relevant for this event"""
        relevant = []

        # Docker vulnerabilities - all projects that use Docker
        if event_source == 'trivy':
            relevant.append('sicherheitstool')  # Uses Docker in future
            # Add more if they use Docker

        # AIDE file changes - check what was modified
        elif event_source == 'aide':
            relevant.append('sicherheitstool')  # Most likely production code
            relevant.append('shadowops-bot')    # Bot code changes
            relevant.append('guildscout')       # GuildScout code changes

        # CrowdSec/Fail2ban - network attacks could target any service
        elif event_source in ['crowdsec', 'fail2ban']:
            relevant.append('sicherheitstool')  # Production API (port 3001)
            # Bots don't expose network services, but include for awareness
            relevant.append('shadowops-bot')

        return relevant

    def _extract_summary(self, full_context: str) -> str:
        """Extract a brief summary from full context"""
        lines = full_context.split('\n')
        summary_lines = []

        # Get first 15 lines (usually contains overview and tech stack)
        for line in lines[:15]:
            if line.strip():
                summary_lines.append(line)

        summary_lines.append("_(Full context available if needed)_")
        return '\n'.join(summary_lines)

    def get_project_context(self, project_name: str) -> Optional[str]:
        """Get full context for a specific project"""
        if not self.loaded:
            self.load_all_contexts()
        return self.projects.get(project_name)

    def get_all_projects(self) -> List[str]:
        """Get list of all known projects"""
        if not self.loaded:
            self.load_all_contexts()
        return list(self.projects.keys())

    def get_infrastructure_context(self) -> str:
        """Get infrastructure and security policies context"""
        if not self.loaded:
            self.load_all_contexts()
        return self.infrastructure

    def get_do_not_touch_list(self) -> List[str]:
        """Extract DO-NOT-TOUCH paths from infrastructure context"""
        if not self.loaded:
            self.load_all_contexts()

        do_not_touch = []

        # Parse infrastructure context for DO-NOT-TOUCH section
        in_dnt_section = False
        for line in self.infrastructure.split('\n'):
            if '### DO-NOT-TOUCH' in line:
                in_dnt_section = True
                continue
            elif in_dnt_section:
                if line.startswith('###'):  # Next section
                    break
                # Extract paths (lines starting with /)
                stripped = line.strip()
                if stripped.startswith('/'):
                    # Remove comments
                    path = stripped.split('#')[0].strip()
                    do_not_touch.append(path)

        return do_not_touch

    def is_path_protected(self, path: str) -> bool:
        """Check if a path is in the DO-NOT-TOUCH list"""
        do_not_touch = self.get_do_not_touch_list()

        for protected_path in do_not_touch:
            # Check if path starts with protected path
            if path.startswith(protected_path.rstrip('/')):
                return True

        return False

    def get_safe_operations(self) -> Dict[str, List[str]]:
        """Extract safe operations by category from infrastructure context"""
        safe_ops = {
            'always_safe': [],
            'requires_approval': [],
            'never_auto': []
        }

        if not self.loaded:
            self.load_all_contexts()

        current_category = None
        for line in self.infrastructure.split('\n'):
            # Detect sections
            if '### Always Require Approval' in line or '#### Always Require Approval' in line:
                current_category = 'requires_approval'
                continue
            elif '#### Auto-Approve' in line:
                current_category = 'always_safe'
                continue
            elif '#### Never Auto-Execute' in line:
                current_category = 'never_auto'
                continue
            elif line.startswith('###') or line.startswith('##'):
                current_category = None
                continue

            # Extract numbered items
            if current_category and line.strip():
                # Remove numbering and cleanup
                if line.strip()[0].isdigit() and '.' in line:
                    item = line.split('.', 1)[1].strip()
                    safe_ops[current_category].append(item)

        return safe_ops

    def build_safety_prompt(self) -> str:
        """Build a safety prompt for AI with critical rules"""
        if not self.loaded:
            self.load_all_contexts()

        do_not_touch = self.get_do_not_touch_list()
        safe_ops = self.get_safe_operations()

        prompt = """
# CRITICAL SAFETY RULES

## DO-NOT-TOUCH Paths (Never modify automatically)
"""
        for path in do_not_touch:
            prompt += f"- {path}\n"

        prompt += """
## Never Auto-Execute
"""
        for op in safe_ops['never_auto']:
            prompt += f"- {op}\n"

        prompt += """
## Always Require Approval
"""
        for op in safe_ops['requires_approval']:
            prompt += f"- {op}\n"

        prompt += """
## Current Operating Mode: PARANOID
- ALL fixes require human approval
- No automatic execution allowed
- Maximum safety during learning phase

## Key Principles
1. NEVER modify production databases without explicit approval
2. NEVER change authentication systems
3. NEVER modify files in DO-NOT-TOUCH paths
4. NEVER restart customer-facing services during business hours
5. ALWAYS backup before making changes
6. If unsure, ASK - better safe than sorry

"""

        return prompt

    def get_git_learning_stats(self) -> Dict[str, Any]:
        """
        Get statistics about Git learning

        Returns:
            Dict with learning stats per project
        """
        if not self.enable_git_learning or not self.git_analyzers:
            return {'enabled': False, 'projects': {}}

        stats = {
            'enabled': True,
            'days_analyzed': self.git_history_days,
            'projects': {}
        }

        for project_name, analyzer in self.git_analyzers.items():
            try:
                project_stats = analyzer.get_statistics()
                stats['projects'][project_name] = project_stats
            except Exception as e:
                logger.debug(f"Could not get stats for {project_name}: {e}")
                stats['projects'][project_name] = {'error': str(e)}

        return stats

    def reload_git_history(self, project_name: Optional[str] = None):
        """
        Reload Git history (force cache refresh)

        Args:
            project_name: Optional - reload specific project, or all if None
        """
        if not self.enable_git_learning:
            logger.warning("Git learning is disabled")
            return

        if project_name:
            if project_name in self.git_analyzers:
                logger.info(f"🔄 Reloading Git history for {project_name}...")
                self.git_analyzers[project_name].load_commit_history(force_reload=True)
                self.git_analyzers[project_name].pattern_cache = None  # Clear pattern cache
                self.git_analyzers[project_name].analyze_patterns()
                logger.info(f"✅ Git history reloaded for {project_name}")
            else:
                logger.warning(f"No Git analyzer for {project_name}")
        else:
            logger.info("🔄 Reloading Git history for all projects...")
            for name, analyzer in self.git_analyzers.items():
                analyzer.load_commit_history(force_reload=True)
                analyzer.pattern_cache = None
                analyzer.analyze_patterns()
            logger.info(f"✅ Git history reloaded for {len(self.git_analyzers)} project(s)")
