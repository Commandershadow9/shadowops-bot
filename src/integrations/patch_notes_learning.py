"""
Patch Notes Learning System — PostgreSQL-basiert.

Ersetzt die JSONL-Dateien durch eine gemeinsame agent_learning DB.
Schliesst den Feedback-Loop: Discord-Reactions → DB → Variant-Gewichtung → bessere Generierung.

Tabellen in agent_learning DB:
- agent_feedback: Universelles Feedback (Reactions, Ratings, Text)
- agent_quality_scores: Qualitaetsbewertung pro Output
- pn_generations: Jede generierte Patch Note
- pn_variants: Prompt-Varianten mit Performance pro Projekt
- pn_examples: Kuratierte Beispiele nach Feedback sortiert
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import asyncpg

logger = logging.getLogger('shadowops.pn_learning')

DSN = 'postgresql://agent_learning:agent_learn_2026@127.0.0.1:5433/agent_learning'


class PatchNotesLearning:
    """PostgreSQL-basiertes Learning fuer Patch Notes."""

    def __init__(self, dsn: str = DSN):
        self.dsn = dsn
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        if self.pool is not None:
            return
        self.pool = await asyncpg.create_pool(
            dsn=self.dsn, min_size=1, max_size=2, command_timeout=15,
        )
        logger.info("PatchNotesLearning DB verbunden")

    async def close(self):
        if self.pool:
            await self.pool.close()
            self.pool = None

    # ─────────────────────────────────────────────
    # Generierung aufzeichnen
    # ─────────────────────────────────────────────

    async def record_generation(
        self,
        project: str,
        version: str,
        variant_id: Optional[str],
        title: str,
        content: str,
        auto_quality: float,
        commits_count: int,
        token_usage: int = 0,
        discord_msg_id: Optional[int] = None,
    ) -> int:
        """Neue Patch-Note-Generierung aufzeichnen."""
        row = await self.pool.fetchrow(
            """INSERT INTO pn_generations
               (project, version, variant_id, title, content,
                auto_quality, commits_count, token_usage, discord_msg_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               ON CONFLICT (project, version)
               DO UPDATE SET variant_id=$3, title=$4, content=$5,
                  auto_quality=$6, commits_count=$7, token_usage=$8,
                  discord_msg_id=$9
               RETURNING id""",
            project, version, variant_id, title, content,
            auto_quality, commits_count, token_usage, discord_msg_id,
        )
        gen_id = row['id']

        # Varianten-Nutzung tracken
        if variant_id:
            await self.pool.execute(
                """INSERT INTO pn_variants (variant_id, project, times_used, last_used_at)
                   VALUES ($1, $2, 1, NOW())
                   ON CONFLICT (variant_id, project)
                   DO UPDATE SET times_used = pn_variants.times_used + 1, last_used_at = NOW()""",
                variant_id, project,
            )
            # Auch globale Statistik
            await self.pool.execute(
                """INSERT INTO pn_variants (variant_id, project, times_used, last_used_at)
                   VALUES ($1, '*', 1, NOW())
                   ON CONFLICT (variant_id, project)
                   DO UPDATE SET times_used = pn_variants.times_used + 1, last_used_at = NOW()""",
                variant_id,
            )

        # Als Beispiel speichern (initial mit auto_score)
        if auto_quality >= 70:
            await self.pool.execute(
                """INSERT INTO pn_examples
                   (project, version, variant_id, content, auto_score, combined_score)
                   VALUES ($1, $2, $3, $4, $5, $5)
                   ON CONFLICT (project, version)
                   DO UPDATE SET variant_id=$3, content=$4, auto_score=$5, combined_score=$5""",
                project, version, variant_id, content, auto_quality,
            )

        logger.info(
            "Generation aufgezeichnet: %s v%s (Variante: %s, Score: %.0f)",
            project, version, variant_id or '?', auto_quality,
        )
        return gen_id

    # ─────────────────────────────────────────────
    # Feedback aufzeichnen (von Discord)
    # ─────────────────────────────────────────────

    async def record_feedback(
        self,
        project: str,
        version: str,
        feedback_type: str,
        user_id: Optional[int] = None,
        user_name: Optional[str] = None,
        score_delta: int = 0,
        rating: Optional[int] = None,
        text_content: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ):
        """Discord-Feedback in DB speichern."""
        import json
        await self.pool.execute(
            """INSERT INTO agent_feedback
               (agent, project, reference_id, feedback_type,
                user_id, user_name, score_delta, rating, text_content, metadata)
               VALUES ('patch_notes', $1, $2, $3, $4, $5, $6, $7, $8, $9)""",
            project, version, feedback_type,
            user_id, user_name, score_delta, rating, text_content,
            json.dumps(metadata) if metadata else None,
        )

    async def get_aggregated_feedback(self, project: str, version: str) -> Dict:
        """Aggregiertes Feedback fuer eine bestimmte Version."""
        row = await self.pool.fetchrow(
            """SELECT
                   COALESCE(SUM(score_delta), 0) as total_score,
                   COUNT(DISTINCT user_id) as user_count,
                   AVG(rating) FILTER (WHERE rating IS NOT NULL) as avg_rating,
                   COUNT(*) as feedback_count
               FROM agent_feedback
               WHERE agent = 'patch_notes' AND project = $1 AND reference_id = $2""",
            project, version,
        )
        return {
            'total_score': row['total_score'] or 0,
            'user_count': row['user_count'] or 0,
            'avg_rating': round(float(row['avg_rating'] or 0), 1),
            'feedback_count': row['feedback_count'] or 0,
        }

    # ─────────────────────────────────────────────
    # Feedback-Window schliessen + Learning
    # ─────────────────────────────────────────────

    async def close_feedback_window(self, project: str, version: str):
        """Feedback-Window schliessen und Learning ausfuehren.

        Wird aufgerufen wenn genug Zeit vergangen ist (z.B. 7 Tage)
        oder beim naechsten Audit. Berechnet finale Scores und
        aktualisiert Varianten-Gewichtung + Beispiel-Ranking.
        """
        # 1. Feedback aggregieren
        feedback = await self.get_aggregated_feedback(project, version)

        # 2. Generation-Record laden
        gen = await self.pool.fetchrow(
            "SELECT * FROM pn_generations WHERE project=$1 AND version=$2",
            project, version,
        )
        if not gen:
            return

        if gen['feedback_closed']:
            return  # Schon verarbeitet

        auto_score = gen['auto_quality'] or 50.0
        # Feedback-Score: Normalisiert auf 0-100 Skala
        # Likes/Reactions: total_score (kann negativ sein), avg_rating: 1-5
        fb_score = 50.0  # Neutral wenn kein Feedback
        if feedback['feedback_count'] > 0:
            if feedback['avg_rating'] > 0:
                fb_score = feedback['avg_rating'] * 20  # 1-5 → 20-100
            else:
                # Nur Reactions: total_score normalisieren (0-50 Punkte erwartet)
                fb_score = min(100, max(0, 50 + feedback['total_score']))

        combined = (auto_score * 0.6) + (fb_score * 0.4)

        # 3. Generation updaten
        await self.pool.execute(
            """UPDATE pn_generations
               SET feedback_score = $1, feedback_closed = TRUE
               WHERE project = $2 AND version = $3""",
            fb_score, project, version,
        )

        # 4. Quality-Score speichern
        await self.pool.execute(
            """INSERT INTO agent_quality_scores
               (agent, project, reference_id, auto_score, feedback_score,
                combined_score, sample_count)
               VALUES ('patch_notes', $1, $2, $3, $4, $5, $6)
               ON CONFLICT (agent, project, reference_id)
               DO UPDATE SET feedback_score=$4, combined_score=$5,
                  sample_count=$6, assessed_at=NOW()""",
            project, version, auto_score, fb_score, combined,
            feedback['feedback_count'],
        )

        # 5. Varianten-Gewichtung aktualisieren
        variant_id = gen['variant_id']
        if variant_id:
            # Pro-Projekt Gewichtung
            for proj in (project, '*'):
                row = await self.pool.fetchrow(
                    "SELECT times_used, avg_auto_score, avg_feedback FROM pn_variants WHERE variant_id=$1 AND project=$2",
                    variant_id, proj,
                )
                if row:
                    n = row['times_used'] or 1
                    # Laufender Durchschnitt
                    new_auto = ((row['avg_auto_score'] or 50) * (n - 1) + auto_score) / n
                    new_fb = ((row['avg_feedback'] or 0) * (n - 1) + fb_score) / n
                    new_weight = new_auto * 0.6 + new_fb * 0.4

                    await self.pool.execute(
                        """UPDATE pn_variants
                           SET avg_auto_score=$1, avg_feedback=$2, combined_weight=$3
                           WHERE variant_id=$4 AND project=$5""",
                        new_auto, new_fb, new_weight, variant_id, proj,
                    )

        # 6. Beispiel-Ranking aktualisieren
        await self.pool.execute(
            """UPDATE pn_examples
               SET feedback_score = $1, combined_score = $2
               WHERE project = $3 AND version = $4""",
            fb_score, combined, project, version,
        )

        logger.info(
            "Feedback-Window geschlossen: %s v%s — Auto=%.0f, Feedback=%.0f, Combined=%.0f (%d Feedbacks)",
            project, version, auto_score, fb_score, combined, feedback['feedback_count'],
        )

    # ─────────────────────────────────────────────
    # Varianten-Auswahl (gewichtet)
    # ─────────────────────────────────────────────

    async def get_best_variant(self, project: str) -> Optional[str]:
        """Beste Variante fuer ein Projekt basierend auf Feedback-gewichteten Scores.

        Nutzt projekt-spezifische Daten wenn vorhanden, sonst globale.
        Gibt None zurueck wenn keine Daten vorhanden (→ Random-Auswahl).
        """
        # Projekt-spezifisch zuerst
        row = await self.pool.fetchrow(
            """SELECT variant_id, combined_weight
               FROM pn_variants
               WHERE project = $1 AND times_used >= 2
               ORDER BY combined_weight DESC
               LIMIT 1""",
            project,
        )
        if row:
            return row['variant_id']

        # Fallback: Global
        row = await self.pool.fetchrow(
            """SELECT variant_id, combined_weight
               FROM pn_variants
               WHERE project = '*' AND times_used >= 3
               ORDER BY combined_weight DESC
               LIMIT 1""",
        )
        return row['variant_id'] if row else None

    async def get_variant_stats(self, project: str = '*') -> List[Dict]:
        """Varianten-Statistiken fuer ein Projekt."""
        rows = await self.pool.fetch(
            """SELECT variant_id, times_used, avg_auto_score, avg_feedback,
                      combined_weight, last_used_at
               FROM pn_variants
               WHERE project = $1
               ORDER BY combined_weight DESC""",
            project,
        )
        return [dict(r) for r in rows]

    # ─────────────────────────────────────────────
    # Beispiel-Auswahl (feedback-gewichtet)
    # ─────────────────────────────────────────────

    async def get_best_examples(self, project: str, limit: int = 2) -> List[Dict]:
        """Beste Beispiele fuer Few-Shot, nach echtem Feedback sortiert.

        Bevorzugt projekt-spezifische Beispiele, faellt auf andere zurueck.
        """
        # Projekt-spezifisch
        rows = await self.pool.fetch(
            """SELECT project, version, variant_id, content, combined_score
               FROM pn_examples
               WHERE project = $1 AND is_active = TRUE
               ORDER BY combined_score DESC
               LIMIT $2""",
            project, limit,
        )

        if len(rows) < limit:
            # Auffuellen mit anderen Projekten
            existing_versions = [r['version'] for r in rows]
            extra = await self.pool.fetch(
                """SELECT project, version, variant_id, content, combined_score
                   FROM pn_examples
                   WHERE project != $1 AND is_active = TRUE
                     AND version != ALL($2)
                   ORDER BY combined_score DESC
                   LIMIT $3""",
                project, existing_versions or [''], limit - len(rows),
            )
            rows = list(rows) + list(extra)

        return [dict(r) for r in rows]

    # ─────────────────────────────────────────────
    # Offene Feedback-Windows pruefen
    # ─────────────────────────────────────────────

    async def get_unclosed_generations(self, min_age_hours: int = 168) -> List[Dict]:
        """Generierungen deren Feedback-Window noch offen ist (>7 Tage alt).

        Returns:
            Liste von {project, version, created_at}
        """
        rows = await self.pool.fetch(
            """SELECT project, version, created_at
               FROM pn_generations
               WHERE feedback_closed = FALSE
                 AND created_at < NOW() - make_interval(hours => $1)
               ORDER BY created_at ASC""",
            min_age_hours,
        )
        return [dict(r) for r in rows]
