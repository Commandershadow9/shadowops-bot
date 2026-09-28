#!/usr/bin/env bash
# ghcr-tag-cap.sh — begrenzt die Tags EINES Image-Repos auf die N neuesten
# (ZERODOX#3858).
#
# Anlass 27.09.2026: 94 Tags `ghcr.io/commandershadow9/zerodox-web` aus sieben
# Tagen (~2,4 GB je Tag, 211 GB ungenutzt). Jeder Deploy mit `--pull-image` zieht
# ein neues Image, das alte bleibt getaggt liegen. `docker image prune -f` fasst
# getaggte Images nie an — und das ist Absicht (#1186, s. u.). Der Deploy brach
# daraufhin an `Disk: nur 19% frei (Schwelle: 20%)` ab, waehrend der Watchdog
# „Auto-Prune: builder-cache: 0" als Erfolg meldete.
#
# ⚠ INVARIANTE: Geloescht wird AUSSCHLIESSLICH per `docker rmi <repo>:<tag>`.
# Das entfernt nur diesen einen Tag. Traegt dasselbe Image noch einen anderen
# Tag (`zerodox-zerodox-web:rollback`, `:latest`) oder nutzt es ein Container,
# bleibt es stehen — Docker verweigert bzw. untaggt nur. NIEMALS
# `docker image prune -a` oder `docker rmi <image-id>`: Beides nimmt das
# Rollback-Image mit (#1186).
#
# Aufruf:  ghcr_tag_cap <repo> <behalten> [trocken]
#   trocken=1 → nur ausgeben, was entfernt wuerde.
# Ausgabe: je Tag eine Zeile `entfernt <tag>` | `behalten <tag> (in Nutzung)` |
#          `wuerde_entfernen <tag>`. Rueckgabe immer 0 (Watchdog laeuft weiter).

ghcr_tag_cap() {
  local repo="$1" behalten="${2:-10}" trocken="${3:-0}" tag
  case "$behalten" in ''|*[!0-9]*) behalten=10 ;; esac
  # Mindestens 2 behalten: den laufenden Stand und einen Vorgaenger.
  [ "$behalten" -lt 2 ] && behalten=2

  docker images "$repo" --format '{{.CreatedAt}}\t{{.Tag}}' 2>/dev/null \
    | sort -r \
    | awk -F'\t' -v k="$behalten" 'NR>k && $2!="<none>" && $2!="" {print $2}' \
    | while IFS= read -r tag; do
        if [ "$trocken" = "1" ]; then
          echo "wuerde_entfernen $tag"
        elif docker rmi "$repo:$tag" >/dev/null 2>&1; then
          echo "entfernt $tag"
        else
          echo "behalten $tag (in Nutzung)"
        fi
      done
  return 0
}
