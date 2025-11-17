"""
Approval Mode System for Auto-Remediation

Implements 3-tier approval system:
- PARANOID: User approves everything (learning phase)
- BALANCED: Auto-fix low-risk, approve high-risk
- AGGRESSIVE: Auto-fix most things with monitoring
"""

from enum import Enum
from typing import Dict, Optional
import logging

logger = logging.getLogger('shadowops.approval')


class ApprovalMode(Enum):
    """Operating modes for auto-remediation"""
    PARANOID = "paranoid"      # Mode 1: Approve everything
    BALANCED = "balanced"      # Mode 2: Selective auto-fix
    AGGRESSIVE = "aggressive"  # Mode 3: Maximum automation


class ApprovalDecision:
    """Decision on whether to auto-execute or request approval"""

    def __init__(
        self,
        should_auto_execute: bool,
        reason: str,
        risk_level: str,
        confidence_threshold: float
    ):
        self.should_auto_execute = should_auto_execute
        self.reason = reason
        self.risk_level = risk_level
        self.confidence_threshold = confidence_threshold


class ApprovalModeManager:
    """Manages approval mode logic and decision-making"""

    def __init__(self, mode: ApprovalMode, context_manager=None):
        self.mode = mode
        self.context_manager = context_manager
        logger.info(f"🔒 Approval Mode: {mode.value.upper()}")

    def should_auto_execute(
        self,
        event: Dict,
        fix_strategy: Dict
    ) -> ApprovalDecision:
        """
        Determine if a fix should be auto-executed or require approval

        Args:
            event: Security event details
            fix_strategy: Proposed fix from AI

        Returns:
            ApprovalDecision with auto-execute flag and reasoning
        """
        confidence = fix_strategy.get('confidence', 0.0)
        source = event.get('source', '')
        severity = event.get('severity', 'UNKNOWN')

        # PARANOID Mode: Always require approval
        if self.mode == ApprovalMode.PARANOID:
            return ApprovalDecision(
                should_auto_execute=False,
                reason="PARANOID Mode: Alle Fixes erfordern Genehmigung (Lernphase)",
                risk_level="LEARNING",
                confidence_threshold=0.0
            )

        # Check do-not-touch list
        if self._is_protected_operation(fix_strategy):
            return ApprovalDecision(
                should_auto_execute=False,
                reason="Operation betrifft geschützte Systeme (DO-NOT-TOUCH Liste)",
                risk_level="CRITICAL",
                confidence_threshold=1.0  # Never auto-execute
            )

        # BALANCED Mode: Auto-fix low-risk with high confidence
        if self.mode == ApprovalMode.BALANCED:
            # High confidence + low risk = auto-execute
            if confidence >= 0.85:
                risk_level = self._assess_risk_level(event, fix_strategy)

                if risk_level in ['LOW', 'MEDIUM']:
                    return ApprovalDecision(
                        should_auto_execute=True,
                        reason=f"BALANCED Mode: Hohe Confidence ({confidence:.0%}) + {risk_level} Risk",
                        risk_level=risk_level,
                        confidence_threshold=0.85
                    )
                else:
                    return ApprovalDecision(
                        should_auto_execute=False,
                        reason=f"BALANCED Mode: {risk_level} Risk erfordert Genehmigung",
                        risk_level=risk_level,
                        confidence_threshold=0.85
                    )
            else:
                return ApprovalDecision(
                    should_auto_execute=False,
                    reason=f"BALANCED Mode: Confidence ({confidence:.0%}) < 85%",
                    risk_level=self._assess_risk_level(event, fix_strategy),
                    confidence_threshold=0.85
                )

        # AGGRESSIVE Mode: Auto-fix most things
        if self.mode == ApprovalMode.AGGRESSIVE:
            # Lower threshold, more automation
            if confidence >= 0.75:
                risk_level = self._assess_risk_level(event, fix_strategy)

                # Only require approval for CRITICAL risk
                if risk_level == 'CRITICAL':
                    return ApprovalDecision(
                        should_auto_execute=False,
                        reason=f"AGGRESSIVE Mode: CRITICAL Risk erfordert Genehmigung",
                        risk_level=risk_level,
                        confidence_threshold=0.75
                    )
                else:
                    return ApprovalDecision(
                        should_auto_execute=True,
                        reason=f"AGGRESSIVE Mode: Confidence ({confidence:.0%}) ≥ 75%, Risk: {risk_level}",
                        risk_level=risk_level,
                        confidence_threshold=0.75
                    )
            else:
                return ApprovalDecision(
                    should_auto_execute=False,
                    reason=f"AGGRESSIVE Mode: Confidence ({confidence:.0%}) < 75%",
                    risk_level=self._assess_risk_level(event, fix_strategy),
                    confidence_threshold=0.75
                )

        # Default: require approval
        return ApprovalDecision(
            should_auto_execute=False,
            reason="Unbekannter Mode oder Fehler",
            risk_level="UNKNOWN",
            confidence_threshold=1.0
        )

    def _assess_risk_level(self, event: Dict, fix_strategy: Dict) -> str:
        """
        Assess risk level of a proposed fix

        Returns: 'LOW', 'MEDIUM', 'HIGH', or 'CRITICAL'
        """
        source = event.get('source', '')
        severity = event.get('severity', 'UNKNOWN')
        steps = fix_strategy.get('steps', [])

        # Check for high-risk operations in fix steps
        high_risk_keywords = [
            'database', 'postgresql', 'psql', 'drop', 'delete',
            'rm -rf', 'systemctl stop', 'kill -9',
            'iptables -F', 'ufw disable', 'chmod 777',
            'userdel', 'groupdel', 'passwd'
        ]

        medium_risk_keywords = [
            'restart', 'reload', 'docker stop', 'docker rm',
            'systemctl restart', 'npm install', 'apt-get',
            'git reset', 'git clean'
        ]

        # Convert steps to lowercase string for checking
        steps_text = ' '.join(steps).lower()

        # CRITICAL risk
        for keyword in high_risk_keywords:
            if keyword in steps_text:
                return 'CRITICAL'

        # Check if touching production systems
        if self.context_manager:
            # Check if modifying protected paths
            for step in steps:
                if any(path in step for path in ['/etc/', '/home/cmdshadow/project', 'sicherheitstool']):
                    return 'HIGH'

        # HIGH risk for production-affecting events
        if severity in ['CRITICAL', 'HIGH']:
            if source in ['aide', 'trivy']:  # File changes, vulnerabilities
                return 'HIGH'

        # MEDIUM risk
        for keyword in medium_risk_keywords:
            if keyword in steps_text:
                return 'MEDIUM'

        # Network security events - usually safe to auto-block
        if source in ['fail2ban', 'crowdsec']:
            return 'LOW'

        # Default to MEDIUM for unknown operations
        return 'MEDIUM'

    def _is_protected_operation(self, fix_strategy: Dict) -> bool:
        """Check if fix involves protected systems"""
        if not self.context_manager:
            return False

        steps = fix_strategy.get('steps', [])
        steps_text = ' '.join(steps)

        # Check against do-not-touch list
        protected_paths = self.context_manager.get_do_not_touch_list()

        for protected_path in protected_paths:
            if protected_path in steps_text:
                logger.warning(f"🚫 Protected path detected: {protected_path}")
                return True

        # Check for never-auto operations
        safe_ops = self.context_manager.get_safe_operations()
        never_auto = safe_ops.get('never_auto', [])

        for operation in never_auto:
            # Check if operation is mentioned in steps
            if operation.lower() in steps_text.lower():
                logger.warning(f"🚫 Never-auto operation detected: {operation}")
                return True

        return False

    def change_mode(self, new_mode: ApprovalMode):
        """Change approval mode"""
        old_mode = self.mode
        self.mode = new_mode
        logger.info(f"🔄 Approval Mode geändert: {old_mode.value} → {new_mode.value}")

    def get_mode_description(self) -> Dict:
        """Get description of current mode"""
        descriptions = {
            ApprovalMode.PARANOID: {
                "name": "PARANOID",
                "emoji": "🔒",
                "description": "Alle Fixes erfordern menschliche Genehmigung",
                "auto_execute": "Nie",
                "use_case": "Lernphase, maximale Sicherheit",
                "confidence_threshold": "N/A (alles wird genehmigt)"
            },
            ApprovalMode.BALANCED: {
                "name": "BALANCED",
                "emoji": "⚖️",
                "description": "Auto-Fix für sichere Operationen, Genehmigung für Risikoreiche",
                "auto_execute": "Low/Medium Risk + ≥85% Confidence",
                "use_case": "Produktionseinsatz mit Kontrolle",
                "confidence_threshold": "85%"
            },
            ApprovalMode.AGGRESSIVE: {
                "name": "AGGRESSIVE",
                "emoji": "⚡",
                "description": "Maximale Automatisierung, nur kritische Operationen genehmigen",
                "auto_execute": "Alles außer CRITICAL Risk + ≥75% Confidence",
                "use_case": "Hochautomatisierte Umgebung",
                "confidence_threshold": "75%"
            }
        }

        return descriptions.get(self.mode, {})
