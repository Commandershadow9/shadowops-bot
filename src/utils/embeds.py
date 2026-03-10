"""
Discord Embed Builder für ShadowOps
Erstellt schöne, farbcodierte Embeds für verschiedene Alert-Typen
"""

import discord
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from enum import Enum


class Severity(Enum):
    """Alert Severity Levels"""
    LOW = ("🟢", 0x2ECC71, "Low")
    MEDIUM = ("🟡", 0xF39C12, "Medium")
    HIGH = ("🟠", 0xE67E22, "High")
    CRITICAL = ("🔴", 0xE74C3C, "Critical")
    INFO = ("ℹ️", 0x3498DB, "Info")
    SUCCESS = ("✅", 0x27AE60, "Success")

    def __init__(self, emoji: str, color: int, label: str):
        self.emoji = emoji
        self.color = color
        self.label = label


class EmbedBuilder:
    """Helper-Klasse zum Erstellen von Discord Embeds"""

    @staticmethod
    def create_alert(
        title: str,
        description: str,
        severity: Severity = Severity.INFO,
        fields: Optional[List[Dict[str, Any]]] = None,
        footer: Optional[str] = None,
        project_tag: Optional[str] = None,
        project_color: Optional[int] = None,
    ) -> discord.Embed:
        """
        Erstellt ein Alert-Embed

        Args:
            title: Embed-Titel
            description: Embed-Beschreibung
            severity: Alert-Severity (bestimmt Farbe und Emoji)
            fields: Liste von Feldern [{"name": "...", "value": "...", "inline": bool}]
            footer: Footer-Text
            project_tag: Project-Tag (z.B. "[SECURITY]")
            project_color: Überschreibt severity-Farbe mit Project-Farbe

        Returns:
            Discord Embed
        """
        # Titel mit Severity-Emoji
        full_title = f"{severity.emoji} {title}"
        if project_tag:
            full_title = f"{project_tag} {full_title}"

        # Farbe: Project-Color oder Severity-Color
        color = project_color if project_color else severity.color

        embed = discord.Embed(
            title=full_title,
            description=description,
            color=color,
            timestamp=datetime.now(timezone.utc)
        )

        # Felder hinzufügen
        if fields:
            for field in fields:
                embed.add_field(
                    name=field.get("name", ""),
                    value=field.get("value", ""),
                    inline=field.get("inline", True)
                )

        # Footer
        footer_text = footer or "ShadowOps Security Monitoring"
        embed.set_footer(
            text=footer_text,
            icon_url="https://cdn.discordapp.com/emojis/1234567890.png"  # Optional: Bot-Icon
        )

        return embed

    @staticmethod
    def fail2ban_ban(ip: str, jail: str, attempts: int = 0) -> discord.Embed:
        """Embed für Fail2ban IP-Ban"""
        return EmbedBuilder.create_alert(
            title="IP-Adresse gebannt (Fail2ban)",
            description=f"Brute-Force-Angriff erkannt und blockiert",
            severity=Severity.HIGH,
            fields=[
                {"name": "🌐 IP-Adresse", "value": f"`{ip}`", "inline": True},
                {"name": "🔒 Jail", "value": jail, "inline": True},
                {"name": "⚠️ Versuche", "value": str(attempts) if attempts else "N/A", "inline": True},
            ],
            project_tag="🖥️ [SERVER]"
        )

    @staticmethod
    def crowdsec_alert(ip: str, scenario: str, country: Optional[str] = None) -> discord.Embed:
        """Embed für CrowdSec Alert"""
        fields = [
            {"name": "🌐 IP-Adresse", "value": f"`{ip}`", "inline": True},
            {"name": "📋 Szenario", "value": scenario, "inline": True},
        ]
        if country:
            fields.append({"name": "🌍 Land", "value": country, "inline": True})

        return EmbedBuilder.create_alert(
            title="Bedrohung erkannt (CrowdSec AI)",
            description=f"KI-basierte Bedrohungserkennung hat verdächtige Aktivität identifiziert",
            severity=Severity.CRITICAL,
            fields=fields,
            project_tag="🖥️ [SERVER]"
        )

    @staticmethod
    def docker_scan_result(
        total_images: int,
        critical: int,
        high: int,
        medium: int,
        low: int
    ) -> discord.Embed:
        """Embed für Docker Security Scan Ergebnisse"""
        # Severity basierend auf Schwachstellen
        if critical > 0:
            severity = Severity.CRITICAL
            desc = "⚠️ Kritische Schwachstellen gefunden!"
        elif high > 0:
            severity = Severity.HIGH
            desc = "⚠️ Hohe Schwachstellen gefunden!"
        elif medium > 0:
            severity = Severity.MEDIUM
            desc = "Mittlere Schwachstellen gefunden"
        else:
            severity = Severity.SUCCESS
            desc = "✅ Keine kritischen Schwachstellen gefunden"

        return EmbedBuilder.create_alert(
            title="Docker Security Scan abgeschlossen",
            description=desc,
            severity=severity,
            fields=[
                {"name": "🐳 Gescannte Images", "value": str(total_images), "inline": True},
                {"name": "🔴 CRITICAL", "value": str(critical), "inline": True},
                {"name": "🟠 HIGH", "value": str(high), "inline": True},
                {"name": "🟡 MEDIUM", "value": str(medium), "inline": True},
                {"name": "🟢 LOW", "value": str(low), "inline": True},
            ],
            project_tag="🖥️ [SERVER]"
        )

    @staticmethod
    def backup_status(success: bool, database: str, size: Optional[str] = None) -> discord.Embed:
        """Embed für Backup-Status"""
        if success:
            severity = Severity.SUCCESS
            title = "Backup erfolgreich"
            desc = f"✅ Datenbank-Backup wurde erfolgreich erstellt"
        else:
            severity = Severity.CRITICAL
            title = "Backup fehlgeschlagen"
            desc = f"❌ Datenbank-Backup ist fehlgeschlagen!"

        fields = [
            {"name": "🗄️ Datenbank", "value": database, "inline": True},
        ]
        if size:
            fields.append({"name": "📦 Größe", "value": size, "inline": True})

        return EmbedBuilder.create_alert(
            title=title,
            description=desc,
            severity=severity,
            fields=fields,
            project_tag="🛡️ [SECURITY]"
        )

    @staticmethod
    def aide_check(files_changed: int, files_added: int, files_removed: int) -> discord.Embed:
        """Embed für AIDE File Integrity Check"""
        if files_changed > 0 or files_added > 0 or files_removed > 0:
            severity = Severity.HIGH
            desc = "⚠️ Dateisystem-Änderungen erkannt!"
        else:
            severity = Severity.SUCCESS
            desc = "✅ Keine unautorisierten Änderungen gefunden"

        return EmbedBuilder.create_alert(
            title="AIDE Integrity Check",
            description=desc,
            severity=severity,
            fields=[
                {"name": "📝 Geändert", "value": str(files_changed), "inline": True},
                {"name": "➕ Hinzugefügt", "value": str(files_added), "inline": True},
                {"name": "➖ Entfernt", "value": str(files_removed), "inline": True},
            ],
            project_tag="🖥️ [SERVER]"
        )

    @staticmethod
    def status_overview(
        fail2ban_active: bool,
        fail2ban_bans: int,
        crowdsec_active: bool,
        crowdsec_alerts: int,
        docker_last_scan: Optional[str] = None,
        aide_last_check: Optional[str] = None,
    ) -> discord.Embed:
        """Embed für Gesamt-Status-Übersicht"""

        # Status-Emoji
        f2b_status = "🟢 Aktiv" if fail2ban_active else "🔴 Inaktiv"
        cs_status = "🟢 Aktiv" if crowdsec_active else "🔴 Inaktiv"

        return EmbedBuilder.create_alert(
            title="Security Status Overview",
            description="Aktueller Status aller Security-Systeme",
            severity=Severity.INFO,
            fields=[
                {"name": "🛡️ Fail2ban", "value": f2b_status, "inline": True},
                {"name": "🚫 Gebannte IPs", "value": str(fail2ban_bans), "inline": True},
                {"name": "\u200b", "value": "\u200b", "inline": True},
                {"name": "🤖 CrowdSec", "value": cs_status, "inline": True},
                {"name": "⚠️ Aktive Alerts", "value": str(crowdsec_alerts), "inline": True},
                {"name": "\u200b", "value": "\u200b", "inline": True},
                {"name": "🐳 Docker Scan", "value": docker_last_scan or "Noch nicht durchgeführt", "inline": True},
                {"name": "📁 AIDE Check", "value": aide_last_check or "Noch nicht durchgeführt", "inline": True},
            ],
            project_tag="🖥️ [SERVER]"
        )

    @staticmethod
    def error_alert(component: str, error_message: str) -> discord.Embed:
        """
        Embed für Monitoring-Fehler

        Args:
            component: Name des betroffenen Systems (z.B. "Fail2ban Monitoring")
            error_message: Fehlerbeschreibung
        """
        return EmbedBuilder.create_alert(
            title=f"⚠️ Monitoring Error: {component}",
            description=f"Ein Fehler ist beim Monitoring aufgetreten:\n\n```\n{error_message[:500]}\n```",
            severity=Severity.CRITICAL,
            fields=[
                {"name": "🔧 Aktion erforderlich", "value": "Bitte Log-Dateien prüfen und System wiederherstellen", "inline": False},
                {"name": "⏰ Error-Alert Rate", "value": "Max. alle 30 Minuten", "inline": False},
            ]
        )

    @staticmethod
    def health_check_report(
        fail2ban_ok: bool,
        fail2ban_bans_today: int,
        crowdsec_ok: bool,
        crowdsec_decisions: int,
        docker_ok: bool,
        docker_last_scan: Optional[str],
        docker_vulnerabilities: int,
        aide_ok: bool,
        aide_last_check: Optional[str]
    ) -> discord.Embed:
        """
        Daily Health-Check Report

        Shows status of all monitoring systems
        """
        # Status Icons
        def status_icon(ok: bool) -> str:
            return "✅ Aktiv" if ok else "❌ Fehler"

        # Gesamtstatus
        all_ok = fail2ban_ok and crowdsec_ok and docker_ok and aide_ok
        overall_status = "🟢 Alle Systeme operational" if all_ok else "🔴 Fehler erkannt"

        return EmbedBuilder.create_alert(
            title="📊 Daily Security Health-Check",
            description=f"**{overall_status}**\n\nTäglicher Status-Report aller Monitoring-Systeme",
            severity=Severity.SUCCESS if all_ok else Severity.CRITICAL,
            fields=[
                {"name": "🚫 Fail2ban", "value": f"{status_icon(fail2ban_ok)}\n{fail2ban_bans_today} Bans heute", "inline": True},
                {"name": "🛡️ CrowdSec", "value": f"{status_icon(crowdsec_ok)}\n{crowdsec_decisions} aktive Decisions", "inline": True},
                {"name": "\u200b", "value": "\u200b", "inline": True},
                {"name": "🐳 Docker Scan", "value": f"{status_icon(docker_ok)}\n{docker_last_scan or 'Kein Scan'}\n{docker_vulnerabilities} CRITICAL", "inline": True},
                {"name": "🔒 AIDE", "value": f"{status_icon(aide_ok)}\n{aide_last_check or 'Kein Check'}", "inline": True},
                {"name": "\u200b", "value": "\u200b", "inline": True},
            ],
            footer="Nächster Check: Morgen 06:00 Uhr"
        )
