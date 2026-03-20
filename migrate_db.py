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

    conn.commit()
    conn.close()
    print("Migration terminée.")

if __name__ == "__main__":
    migrate()
