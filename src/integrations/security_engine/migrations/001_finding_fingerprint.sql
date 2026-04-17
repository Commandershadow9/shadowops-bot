-- Zero-Downtime: nullable Spalte + Index-Concurrently fuer Live-Betrieb
ALTER TABLE findings
  ADD COLUMN IF NOT EXISTS finding_fingerprint text;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_findings_fingerprint_open
  ON findings(finding_fingerprint)
  WHERE status = 'open';

COMMENT ON COLUMN findings.finding_fingerprint IS
  'SHA1 aus category+project+files+keywords; ersetzt Titel-Match fuer Dedup (Plan 2026-04-17)';
