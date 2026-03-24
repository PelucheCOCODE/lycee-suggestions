#!/usr/bin/env python3
"""Migration: ajoute les colonnes needs_debate, vote_for, vote_against à suggestions,
   vote_type à votes, et crée la table suggestion_arguments si nécessaire."""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "instance", "suggestions.db")
if not os.path.exists(DB_PATH):
    DB_PATH = os.path.join(os.path.dirname(__file__), "suggestions.db")

def migrate():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Colonnes suggestions
    cur.execute("PRAGMA table_info(suggestions)")
    cols = [r[1] for r in cur.fetchall()]
    if "needs_debate" not in cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN needs_debate INTEGER DEFAULT 0")
        print("+ suggestions.needs_debate")
    if "vote_for" not in cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN vote_for INTEGER DEFAULT 0")
        print("+ suggestions.vote_for")
    if "vote_against" not in cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN vote_against INTEGER DEFAULT 0")
        print("+ suggestions.vote_against")
    if "ai_proportion" not in cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN ai_proportion REAL DEFAULT 0")
        print("+ suggestions.ai_proportion")
    if "ai_feasibility" not in cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN ai_feasibility REAL DEFAULT 0.5")
        print("+ suggestions.ai_feasibility")
    if "ai_cost" not in cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN ai_cost REAL DEFAULT 0.5")
        print("+ suggestions.ai_cost")
    if "ai_needs_debate" not in cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN ai_needs_debate INTEGER DEFAULT 0")
        print("+ suggestions.ai_needs_debate")

    # Colonne votes
    cur.execute("PRAGMA table_info(votes)")
    cols = [r[1] for r in cur.fetchall()]
    if "vote_type" not in cols:
        cur.execute("ALTER TABLE votes ADD COLUMN vote_type VARCHAR(10) DEFAULT 'for'")
        print("+ votes.vote_type")

    # Table calibration_debat
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='calibration_debat'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE calibration_debat (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposition TEXT NOT NULL,
                needs_debate INTEGER NOT NULL,
                created_at DATETIME
            )
        """)
        print("+ table calibration_debat")

    # Colonnes suggestions (completed_at, calibrated_at)
    cur.execute("PRAGMA table_info(suggestions)")
    cols = [r[1] for r in cur.fetchall()]
    if "completed_at" not in cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN completed_at DATETIME")
        print("+ suggestions.completed_at")
    if "calibrated_at" not in cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN calibrated_at DATETIME")
        print("+ suggestions.calibrated_at")

    # Colonnes announcements
    cur.execute("PRAGMA table_info(announcements)")
    cols = [r[1] for r in cur.fetchall()]
    if "is_priority" not in cols:
        cur.execute("ALTER TABLE announcements ADD COLUMN is_priority INTEGER DEFAULT 0")
        print("+ announcements.is_priority")
    if "extra_info" not in cols:
        cur.execute("ALTER TABLE announcements ADD COLUMN extra_info TEXT DEFAULT ''")
        print("+ announcements.extra_info")

    # Colonne display_pages.bus_excluded
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='display_pages'")
    if cur.fetchone():
        cur.execute("PRAGMA table_info(display_pages)")
        cols = [r[1] for r in cur.fetchall()]
        if "bus_excluded" not in cols:
            cur.execute("ALTER TABLE display_pages ADD COLUMN bus_excluded INTEGER DEFAULT 0")
            print("+ display_pages.bus_excluded")

    # Colonne scraped_news.batch_id
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='scraped_news'")
    if cur.fetchone():
        cur.execute("PRAGMA table_info(scraped_news)")
        cols = [r[1] for r in cur.fetchall()]
        if "batch_id" not in cols:
            cur.execute("ALTER TABLE scraped_news ADD COLUMN batch_id VARCHAR(50) DEFAULT 'legacy'")
            print("+ scraped_news.batch_id")

    # Table backups (Backup & Historique)
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='backups'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE backups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename VARCHAR(255) NOT NULL,
                size_bytes INTEGER DEFAULT 0,
                stats_json TEXT DEFAULT '{}',
                created_at DATETIME
            )
        """)
        print("+ table backups")

    # Table activity_logs
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='activity_logs'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type VARCHAR(50) NOT NULL,
                message TEXT DEFAULT '',
                detail TEXT DEFAULT '',
                ip VARCHAR(64) DEFAULT '',
                visitor_id VARCHAR(100) DEFAULT '',
                created_at DATETIME
            )
        """)
        print("+ table activity_logs")

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='proposal_arguments'")
    if cur.fetchone():
        cur.execute("PRAGMA table_info(proposal_arguments)")
        cols = [r[1] for r in cur.fetchall()]
        if cols and "reject_reason" not in cols:
            cur.execute("ALTER TABLE proposal_arguments ADD COLUMN reject_reason TEXT DEFAULT ''")
            print("+ proposal_arguments.reject_reason")

    cur.execute("PRAGMA table_info(suggestions)")
    cols = [r[1] for r in cur.fetchall()]
    if cols and "reject_reason" not in cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN reject_reason TEXT DEFAULT ''")
        print("+ suggestions.reject_reason")

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='suggestion_archive'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE suggestion_archive (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                suggestion_id INTEGER NOT NULL UNIQUE,
                title VARCHAR(200) DEFAULT '',
                original_text TEXT DEFAULT '',
                category VARCHAR(50) DEFAULT '',
                status VARCHAR(50) DEFAULT '',
                reject_reason TEXT DEFAULT '',
                vote_count INTEGER DEFAULT 0,
                needs_debate INTEGER DEFAULT 0,
                created_at DATETIME,
                completed_at DATETIME,
                deleted_at DATETIME,
                updated_at DATETIME
            )
        """)
        print("+ table suggestion_archive")

    # Table suggestion_arguments
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='suggestion_arguments'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE suggestion_arguments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                suggestion_id INTEGER NOT NULL REFERENCES suggestions(id),
                session_id VARCHAR(100) NOT NULL,
                side VARCHAR(10) NOT NULL,
                original_text TEXT NOT NULL,
                summary TEXT DEFAULT '',
                status VARCHAR(20) DEFAULT 'pending',
                created_at DATETIME,
                FOREIGN KEY(suggestion_id) REFERENCES suggestions(id)
            )
        """)
        print("+ table suggestion_arguments")

    cur.execute("PRAGMA table_info(suggestions)")
    cols = [r[1] for r in cur.fetchall()]
    if cols and "importance_score" not in cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN importance_score REAL DEFAULT 0")
        print("+ suggestions.importance_score")

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='community_messages'")
    if cur.fetchone():
        cur.execute("PRAGMA table_info(community_messages)")
        cols = [r[1] for r in cur.fetchall()]
        if cols and "client_message_id" not in cols:
            cur.execute("ALTER TABLE community_messages ADD COLUMN client_message_id VARCHAR(128)")
            print("+ community_messages.client_message_id")

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dilemmas'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE dilemmas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title VARCHAR(220) NOT NULL,
                option_a VARCHAR(500) NOT NULL,
                option_b VARCHAR(500) NOT NULL,
                scheduled_day VARCHAR(10) NOT NULL,
                created_at DATETIME,
                UNIQUE (scheduled_day)
            )
        """)
        print("+ table dilemmas")
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dilemma_votes'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE dilemma_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dilemma_id INTEGER NOT NULL REFERENCES dilemmas(id),
                session_id VARCHAR(100) NOT NULL,
                side VARCHAR(1) NOT NULL,
                created_at DATETIME,
                UNIQUE (dilemma_id, session_id)
            )
        """)
        print("+ table dilemma_votes")

    # NFC-V2: table nfc_locations
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='nfc_locations'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE nfc_locations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school_id VARCHAR(50) DEFAULT 'default',
                slug VARCHAR(100) NOT NULL UNIQUE,
                nfc_uid VARCHAR(100) UNIQUE,
                name VARCHAR(200) NOT NULL,
                description TEXT DEFAULT '',
                image_url VARCHAR(500),
                is_active INTEGER DEFAULT 1,
                category VARCHAR(50) DEFAULT 'Général',
                floor VARCHAR(20),
                building VARCHAR(50),
                created_at DATETIME,
                updated_at DATETIME
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS ix_nfc_loc_slug ON nfc_locations(slug)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_nfc_loc_school ON nfc_locations(school_id)")
        print("+ table nfc_locations")

    # NFC-V2: colonnes NFC sur suggestions
    cur.execute("PRAGMA table_info(suggestions)")
    cols = [r[1] for r in cur.fetchall()]
    if "nfc_location_id" not in cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN nfc_location_id INTEGER REFERENCES nfc_locations(id)")
        print("+ suggestions.nfc_location_id")
    if "source" not in cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN source VARCHAR(10) DEFAULT 'web'")
        print("+ suggestions.source")
    if "confirmation_count" not in cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN confirmation_count INTEGER DEFAULT 0")
        print("+ suggestions.confirmation_count")
    if "last_confirmed_at" not in cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN last_confirmed_at DATETIME")
        print("+ suggestions.last_confirmed_at")
    if "resolved_by_admin" not in cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN resolved_by_admin INTEGER DEFAULT 0")
        print("+ suggestions.resolved_by_admin")

    # NFC-V2: table nfc_confirmations
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='nfc_confirmations'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE nfc_confirmations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                suggestion_id INTEGER NOT NULL REFERENCES suggestions(id),
                school_id VARCHAR(50) DEFAULT 'default',
                session_hash VARCHAR(64) NOT NULL,
                confirmed_at DATETIME
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS ix_nfc_confirm_sugg_at ON nfc_confirmations(suggestion_id, confirmed_at)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_nfc_confirm_session ON nfc_confirmations(session_hash, suggestion_id)")
        print("+ table nfc_confirmations")

    # NFC-V2: index performance sur suggestions NFC
    cur.execute("CREATE INDEX IF NOT EXISTS ix_sugg_nfc_loc_status ON suggestions(nfc_location_id, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_sugg_last_confirmed ON suggestions(last_confirmed_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_sugg_confirm_count ON suggestions(confirmation_count)")

    # NFC-V2.2-ADMIN: colonnes suspension sur nfc_locations
    cur.execute("PRAGMA table_info(nfc_locations)")
    nfc_loc_cols = [r[1] for r in cur.fetchall()]
    if "pause_suggestions" not in nfc_loc_cols:
        cur.execute("ALTER TABLE nfc_locations ADD COLUMN pause_suggestions INTEGER DEFAULT 0")
        print("+ nfc_locations.pause_suggestions")
    if "pause_confirmations" not in nfc_loc_cols:
        cur.execute("ALTER TABLE nfc_locations ADD COLUMN pause_confirmations INTEGER DEFAULT 0")
        print("+ nfc_locations.pause_confirmations")

    # NFC-V2.2-ADMIN: colonnes réponse admin sur suggestions
    cur.execute("PRAGMA table_info(suggestions)")
    sugg_cols = [r[1] for r in cur.fetchall()]
    if "admin_reply" not in sugg_cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN admin_reply TEXT")
        print("+ suggestions.admin_reply")
    if "admin_reply_at" not in sugg_cols:
        cur.execute("ALTER TABLE suggestions ADD COLUMN admin_reply_at DATETIME")
        print("+ suggestions.admin_reply_at")

    # NFC-V2.2-ADMIN: table nfc_followups
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='nfc_followups'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE nfc_followups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id VARCHAR(100) NOT NULL,
                suggestion_id INTEGER NOT NULL REFERENCES suggestions(id),
                created_at DATETIME
            )
        """)
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_nfc_follow_session_sugg ON nfc_followups(session_id, suggestion_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_nfc_follow_session ON nfc_followups(session_id)")
        print("+ table nfc_followups")

    # NFC-V2.2-ADMIN: table nfc_notifications
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='nfc_notifications'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE nfc_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id VARCHAR(100) NOT NULL,
                suggestion_id INTEGER REFERENCES suggestions(id),
                notif_type VARCHAR(30) NOT NULL,
                message VARCHAR(500) NOT NULL,
                is_read INTEGER DEFAULT 0,
                created_at DATETIME
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS ix_nfc_notif_session_read ON nfc_notifications(session_id, is_read)")
        print("+ table nfc_notifications")

    # NFC status history (full audit trail)
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='nfc_status_history'")
    if not cur.fetchone():
        cur.execute("""
            CREATE TABLE nfc_status_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                suggestion_id INTEGER NOT NULL REFERENCES suggestions(id),
                old_status VARCHAR(50) NOT NULL,
                new_status VARCHAR(50) NOT NULL,
                changed_by VARCHAR(20) DEFAULT 'admin',
                note TEXT DEFAULT '',
                created_at DATETIME
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS ix_nfc_status_hist_sugg ON nfc_status_history(suggestion_id)")
        print("+ table nfc_status_history")

    # NFC-V2.4: nouvelles colonnes sur nfc_locations
    cur.execute("PRAGMA table_info(nfc_locations)")
    nfc_loc_cols2 = [r[1] for r in cur.fetchall()]
    if "base_location_id" not in nfc_loc_cols2:
        cur.execute("ALTER TABLE nfc_locations ADD COLUMN base_location_id INTEGER REFERENCES locations(id)")
        print("+ nfc_locations.base_location_id")
    if "sub_location" not in nfc_loc_cols2:
        cur.execute("ALTER TABLE nfc_locations ADD COLUMN sub_location VARCHAR(200)")
        print("+ nfc_locations.sub_location")
    if "custom_detail" not in nfc_loc_cols2:
        cur.execute("ALTER TABLE nfc_locations ADD COLUMN custom_detail VARCHAR(200)")
        print("+ nfc_locations.custom_detail")

    # NFC-V2.4: nouvelles colonnes sur suggestions
    cur.execute("PRAGMA table_info(suggestions)")
    sugg_cols2 = [r[1] for r in cur.fetchall()]
    if "support_count" not in sugg_cols2:
        cur.execute("ALTER TABLE suggestions ADD COLUMN support_count INTEGER DEFAULT 0")
        print("+ suggestions.support_count")
    if "reopened_at" not in sugg_cols2:
        cur.execute("ALTER TABLE suggestions ADD COLUMN reopened_at DATETIME")
        print("+ suggestions.reopened_at")
    if "hype_count" not in sugg_cols2:
        cur.execute("ALTER TABLE suggestions ADD COLUMN hype_count INTEGER DEFAULT 0")
        print("+ suggestions.hype_count")

    conn.commit()
    conn.close()
    print("Migration terminée.")

if __name__ == "__main__":
    migrate()
