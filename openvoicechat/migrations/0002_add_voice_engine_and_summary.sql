BEGIN IMMEDIATE;

ALTER TABLE conversations ADD COLUMN voice TEXT;
ALTER TABLE conversations ADD COLUMN tts_engine TEXT;
ALTER TABLE conversations ADD COLUMN summary TEXT;

INSERT INTO schema_migrations(version, applied_at)
VALUES (2, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));

COMMIT;
