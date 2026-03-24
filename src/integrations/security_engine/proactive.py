"""
ProactiveMode — Geplante Scans, Coverage-Checks, Trend-Erkennung

Statt auf Events zu reagieren, sucht der ProactiveMode aktiv nach:
- Coverage-Lücken: Bereiche die >7 Tage nicht gescannt wurden
- Trends: Steigende Ban-Rate, neue IP-Ranges
- Härtungs-Potential: Configs die nicht optimal sind
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger('shadowops.proactive')

# Bereiche die regelmäßig gescannt werden sollen
SCAN_AREAS = [
    'firewall',      # UFW-Regeln, offene Ports
    'ssh',           # SSHD-Config, Fail2ban-Jails
    'docker',        # Container-Security, Trivy-Scans
    'file_integrity', # AIDE-Checks
    'network',       # CrowdSec, Intrusion Detection
    'services',      # systemd Services, Ports, Health
    'secrets',       # .env-Dateien, API-Keys, Cert-Expiry
    'updates',       # OS-Packages, Docker-Images
]

# Coverage-Lücke: Bereich nicht gescannt seit X Tagen
COVERAGE_GAP_DAYS = 7


class ProactiveMode:
    """Proaktive Security-Härtung basierend auf Coverage und Trends"""

    def __init__(self, db, executor=None, ai_service=None):
        self.db = db
        self.executor = executor
        self.ai_service = ai_service

    async def get_coverage_gaps(self) -> List[Dict[str, Any]]:
        """Findet Bereiche die >7 Tage nicht gescannt wurden"""
        gaps = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=COVERAGE_GAP_DAYS)

        for area in SCAN_AREAS:
            try:
                # Prüfe ob Bereich kürzlich gescannt wurde
                if hasattr(self.db, 'get_scan_coverage'):
                    coverage = await self.db.get_scan_coverage(area)
                    if coverage and coverage.get('last_checked'):
                        last_checked = coverage['last_checked']
                        if isinstance(last_checked, str):
                            last_checked = datetime.fromisoformat(last_checked)
                        if last_checked.replace(tzinfo=timezone.utc) < cutoff:
                            days_ago = (datetime.now(timezone.utc) - last_checked.replace(tzinfo=timezone.utc)).days
                            gaps.append({
                                'area': area,
                                'last_checked': last_checked.isoformat(),
                                'days_since': days_ago,
                                'priority': 'high' if days_ago > 14 else 'medium',
                            })
                    else:
                        # Nie gescannt
                        gaps.append({
                            'area': area,
                            'last_checked': None,
                            'days_since': None,
                            'priority': 'high',
                        })
                else:
                    # DB hat keine Coverage-Tracking → alle als Lücke melden
                    gaps.append({
                        'area': area,
                        'last_checked': None,
                        'days_since': None,
                        'priority': 'low',
                    })
            except Exception as e:
                logger.debug(f"Coverage-Check für {area} fehlgeschlagen: {e}")

        return sorted(gaps, key=lambda g: {'high': 0, 'medium': 1, 'low': 2}.get(g['priority'], 3))

    async def get_fix_effectiveness(self, days: int = 30) -> Dict[str, Any]:
        """Analysiert wie effektiv Fixes der letzten N Tage waren"""
        stats = {}

        for source in ['fail2ban', 'crowdsec', 'trivy', 'aide']:
            try:
                rate = await self.db.get_success_rate(f"{source}_ban", days=days)
                stats[source] = {
                    'success_rate': rate,
                    'status': 'good' if rate >= 0.8 else 'warning' if rate >= 0.5 else 'critical',
                }
            except Exception as e:
                stats[source] = {'success_rate': 0.0, 'status': 'unknown', 'error': str(e)}

        return stats

    async def get_phase_type_effectiveness(self, days: int = 30) -> Dict[str, Any]:
        """Analysiert welche Phase-Typen am effektivsten sind"""
        try:
            return await self.db.get_phase_stats(days=days)
        except Exception:
            return {}

    async def generate_hardening_report(self) -> Dict[str, Any]:
        """Erstellt einen Härtungs-Report mit Empfehlungen"""
        gaps = await self.get_coverage_gaps()
        effectiveness = await self.get_fix_effectiveness()
        phase_stats = await self.get_phase_type_effectiveness()

        recommendations = []

        # Coverage-Lücken
        high_gaps = [g for g in gaps if g['priority'] == 'high']
        if high_gaps:
            areas = ', '.join(g['area'] for g in high_gaps)
            recommendations.append({
                'priority': 'high',
                'category': 'coverage',
                'message': f"Bereiche seit >7 Tagen nicht gescannt: {areas}",
                'action': 'deep_scan',
            })

        # Niedrige Erfolgsraten
        for source, stats in effectiveness.items():
            if stats.get('status') == 'critical':
                recommendations.append({
                    'priority': 'high',
                    'category': 'effectiveness',
                    'message': f"{source} Fix-Erfolgsrate nur {stats['success_rate']:.0%}",
                    'action': 'review_strategy',
                })

        return {
            'coverage_gaps': gaps,
            'fix_effectiveness': effectiveness,
            'phase_stats': phase_stats,
            'recommendations': recommendations,
            'generated_at': datetime.now(timezone.utc).isoformat(),
        }

    async def run_proactive_scan(self) -> Dict[str, Any]:
        """Führt einen proaktiven Scan basierend auf Coverage-Lücken aus"""
        report = await self.generate_hardening_report()
        logger.info(f"Proactive Report: {len(report['coverage_gaps'])} Lücken, "
                    f"{len(report['recommendations'])} Empfehlungen")
        return report
