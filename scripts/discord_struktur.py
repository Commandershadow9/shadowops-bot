#!/usr/bin/env python3
"""Richtet Projektbereiche, Rollen und Rechte auf dem DEV-Server ein.

Einmalig auszuführen. Spricht die REST-Schnittstelle direkt an, statt eine
zweite Gateway-Sitzung zu öffnen -- die wuerde mit dem laufenden Bot
kollidieren.

Der Token wird über die vorhandene Konfigurationsklasse geladen und nie
ausgegeben.

Rechte hängen an der KATEGORIE, nicht am einzelnen Kanal: Kanäle erben sie.
Ein neuer Kanal in einer Projektkategorie ist damit automatisch richtig
eingestellt -- wer Rechte je Kanal setzt, vergisst es irgendwann.

  python3 scripts/discord_struktur.py --trocken   # nur anzeigen
  python3 scripts/discord_struktur.py             # ausfuehren
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "src"))

GUILD = "1438065435496157267"
PARTNER = "238749198923726859"

# Bots brauchen Zugriff auf jeden Kanal, in den sie schreiben. Wird das bei
# einer Kategorie vergessen, schweigt die betroffene Meldung stillschweigend --
# kein Fehler, keine Warnung, nur ein Kanal, der nichts mehr bekommt.
BOTS = {
    "1438067872957071360": "ShadowOps",
    "1480711684912971807": "Shadow Admin",
}
API = "https://discord.com/api/v10"

# Berechtigungen als Bitmaske
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
READ_HISTORY = 1 << 16

TROCKEN = "--trocken" in sys.argv


def token_holen() -> str:
    from utils.config import Config  # noqa: PLC0415

    cfg = Config(str(WURZEL / "config" / "config.yaml"))
    wert = cfg.discord_token
    if not wert:
        sys.exit("FEHLER: Kein Discord-Token über die Konfiguration erreichbar.")
    return wert


class Discord:
    """Schmaler REST-Zugriff über die Standardbibliothek.

    Bewusst ohne zusätzliche Abhängigkeit: Die Umgebung gehört einem
    laufenden Produktivdienst, dort wird für ein einmaliges Skript nichts
    nachinstalliert.
    """

    def __init__(self, token: str) -> None:
        self.kopf = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "shadowops-struktur/1.0",
        }

    def _ruf(self, methode: str, pfad: str, json_daten=None):
        rumpf = json.dumps(json_daten).encode() if json_daten is not None else None
        for _ in range(5):
            anfrage = urllib.request.Request(
                f"{API}{pfad}", data=rumpf, headers=self.kopf, method=methode
            )
            try:
                with urllib.request.urlopen(anfrage, timeout=20) as antwort:
                    text = antwort.read().decode()
                    return json.loads(text) if text.strip() else {}
            except urllib.error.HTTPError as e:
                text = e.read().decode()
                if e.code == 429:  # Ratenbegrenzung
                    try:
                        time.sleep(float(json.loads(text).get("retry_after", 2)) + 0.5)
                    except Exception:
                        time.sleep(3)
                    continue
                sys.exit(f"FEHLER {e.code} bei {methode} {pfad}: {text[:300]}")
        sys.exit(f"FEHLER: {pfad} auch nach 5 Versuchen ratenbegrenzt.")

    def rollen(self):
        return self._ruf("GET", f"/guilds/{GUILD}/roles")

    def kanaele(self):
        return self._ruf("GET", f"/guilds/{GUILD}/channels")

    def rolle_anlegen(self, name: str, farbe: int):
        return self._ruf(
            "POST",
            f"/guilds/{GUILD}/roles",
            json_daten={"name": name, "color": farbe, "mentionable": True, "permissions": "0"},
        )

    def kategorie_anlegen(self, name: str):
        return self._ruf(
            "POST", f"/guilds/{GUILD}/channels", json_daten={"name": name, "type": 4}
        )

    def verschieben(self, kanal_id: str, kategorie_id: str):
        return self._ruf("PATCH", f"/channels/{kanal_id}", json_daten={"parent_id": kategorie_id})

    def recht_setzen(self, kanal_id: str, ziel_id: str, typ: int, erlauben: int, verbieten: int):
        return self._ruf(
            "PUT",
            f"/channels/{kanal_id}/permissions/{ziel_id}",
            json_daten={"type": typ, "allow": str(erlauben), "deny": str(verbieten)},
        )

    def rolle_geben(self, nutzer_id: str, rollen_id: str):
        return self._ruf("PUT", f"/guilds/{GUILD}/members/{nutzer_id}/roles/{rollen_id}")


def main() -> None:
    d = Discord(token_holen())

    vorhandene_rollen = {r["name"]: r for r in d.rollen()}
    vorhandene_kanaele = {k["name"]: k for k in d.kanaele()}
    kategorien = {k["name"]: k for k in d.kanaele() if k["type"] == 4}

    print(f"Server hat {len(vorhandene_rollen)} Rollen, {len(vorhandene_kanaele)} Kanäle\n")

    # ---------------------------------------------------------------- Rollen
    gewuenschte_rollen = {
        "team": 0x5865F2,          # alle Menschen, sehen die übergreifenden Bereiche
        "p-avunex": 0xC0A080,      # je Projekt eine Rolle
        "p-zerodox": 0x06B6D4,
        "p-guildscout": 0x22C55E,
        "p-mayday": 0xEF4444,
    }
    rollen_ids: dict[str, str] = {}
    for name, farbe in gewuenschte_rollen.items():
        if name in vorhandene_rollen:
            rollen_ids[name] = vorhandene_rollen[name]["id"]
            print(f"  Rolle @{name} existiert bereits")
            continue
        if TROCKEN:
            print(f"  [trocken] Rolle @{name} anlegen")
            continue
        neu = d.rolle_anlegen(name, farbe)
        rollen_ids[name] = neu["id"]
        print(f"  Rolle @{name} angelegt")

    # ------------------------------------------------------------ Kategorien
    projekt_bereiche = {
        "\U0001f7e0 AVUNEX": ("p-avunex", ["updates-avunex"]),
    }

    for kat_name, (rolle, kanal_namen) in projekt_bereiche.items():
        if kat_name in kategorien:
            kat_id = kategorien[kat_name]["id"]
            print(f"\n  Kategorie '{kat_name}' existiert bereits")
        elif TROCKEN:
            print(f"\n  [trocken] Kategorie '{kat_name}' anlegen")
            continue
        else:
            kat_id = d.kategorie_anlegen(kat_name)["id"]
            print(f"\n  Kategorie '{kat_name}' angelegt")

        if TROCKEN:
            continue

        # ⚠️ Die Reihenfolge ist nicht beliebig: ERST die Ausnahmen eintragen,
        # DANN @everyone sperren. Wer zuerst sperrt, nimmt sich selbst den
        # Zugriff auf den Kanal, den er danach bearbeiten will -- dieser Bot
        # ist kein Administrator und sieht Kanäle nur über @everyone.
        # Ergebnis wäre 50001 Missing Access und eine halb eingerichtete
        # Kategorie, die niemand außer Administratoren sieht.
        if rolle in rollen_ids:
            d.recht_setzen(
                kat_id, rollen_ids[rolle], 0, VIEW_CHANNEL | SEND_MESSAGES | READ_HISTORY, 0
            )
        for bot_id in BOTS:
            d.recht_setzen(
                kat_id, bot_id, 1, VIEW_CHANNEL | SEND_MESSAGES | READ_HISTORY, 0
            )
        # Jetzt erst die Sperre. @everyone hat dieselbe ID wie die Guild.
        d.recht_setzen(kat_id, GUILD, 0, 0, VIEW_CHANNEL)
        print(f"    Rechte gesetzt: @{rolle} + {len(BOTS)} Bots sehen diesen Bereich")

        for kn in kanal_namen:
            if kn not in vorhandene_kanaele:
                print(f"    Kanal '{kn}' nicht gefunden, übersprungen")
                continue
            d.verschieben(vorhandene_kanaele[kn]["id"], kat_id)
            print(f"    '{kn}' verschoben")

    # ------------------------------------------------------------- Partner
    if not TROCKEN and "team" in rollen_ids and "p-avunex" in rollen_ids:
        for r in ("team", "p-avunex"):
            try:
                d.rolle_geben(PARTNER, rollen_ids[r])
                print(f"\n  Partner hat @{r} erhalten")
            except SystemExit as e:
                print(f"\n  Partner konnte @{r} nicht bekommen: {e}")

    print("\nFertig.")


if __name__ == "__main__":
    main()
