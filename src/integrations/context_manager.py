"""
Context Manager for RAG (Retrieval-Augmented Generation)

Loads and manages project knowledge base for context-aware AI analysis.
Provides relevant context to AI models for better security decision-making.
"""

import os
from pathlib import Path
from typing import Dict, List, Optional
import logging

logger = logging.getLogger('shadowops.context')


class ContextManager:
    """Manages project context and infrastructure knowledge"""

    def __init__(self):
        self.context_dir = Path(__file__).parent.parent.parent / 'context'
        self.projects: Dict[str, str] = {}
        self.infrastructure: str = ""
        self.loaded = False

    def load_all_contexts(self):
        """Load all project and system contexts"""
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

            self.loaded = True
            logger.info(f"📚 Knowledge base ready: {len(self.projects)} projects loaded")

        except Exception as e:
            logger.error(f"❌ Failed to load contexts: {e}")
            raise

    def get_relevant_context(self, event_source: str, event_type: str) -> str:
        """
        Get relevant context for a security event

        Args:
            event_source: Source of event (trivy, crowdsec, fail2ban, aide)
            event_type: Type of event (docker_vulnerabilities, threat, etc.)

        Returns:
            Formatted context string for AI analysis
        """
        if not self.loaded:
            self.load_all_contexts()

        context_parts = []

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
