"""
Database migration: Add user_id field to tables
Migrate from device_id to user_id for user identification
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import engine
from sqlalchemy import text, inspect


def migrate():
    inspector = inspect(engine)

    print("=" * 60)
    print("Starting database migration: device_id -> user_id")
    print("=" * 60)

    # 1. moment_likes table
    if "moment_likes" in inspector.get_table_names():
        cols = [c["name"] for c in inspector.get_columns("moment_likes")]
        if "user_id" not in cols:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE moment_likes ADD COLUMN user_id INTEGER"))
                conn.commit()
            print("[OK] Added moment_likes.user_id column")
        else:
            print("[SKIP] moment_likes.user_id column already exists")

        indexes = {idx["name"] for idx in inspector.get_indexes("moment_likes")}
        if "uniq_moment_like_user" not in indexes:
            with engine.connect() as conn:
                conn.execute(text("CREATE UNIQUE INDEX uniq_moment_like_user ON moment_likes (moment_id, user_id)"))
                conn.commit()
            print("[OK] Created uniq_moment_like_user unique index")
        else:
            print("[SKIP] uniq_moment_like_user index already exists")
    else:
        print("[!] moment_likes table does not exist, skipping")

    # 2. moment_comments table
    if "moment_comments" in inspector.get_table_names():
        cols = [c["name"] for c in inspector.get_columns("moment_comments")]
        if "user_id" not in cols:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE moment_comments ADD COLUMN user_id INTEGER"))
                conn.commit()
            print("[OK] Added moment_comments.user_id column")
        else:
            print("[SKIP] moment_comments.user_id column already exists")
    else:
        print("[!] moment_comments table does not exist, skipping")

    # 3. page_views table
    if "page_views" in inspector.get_table_names():
        cols = [c["name"] for c in inspector.get_columns("page_views")]
        if "user_id" not in cols:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE page_views ADD COLUMN user_id INTEGER"))
                conn.commit()
            print("[OK] Added page_views.user_id column")
        else:
            print("[SKIP] page_views.user_id column already exists")
    else:
        print("[!] page_views table does not exist, skipping")

    # 4. button_clicks table
    if "button_clicks" in inspector.get_table_names():
        cols = [c["name"] for c in inspector.get_columns("button_clicks")]
        if "user_id" not in cols:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE button_clicks ADD COLUMN user_id INTEGER"))
                conn.commit()
            print("[OK] Added button_clicks.user_id column")
        else:
            print("[SKIP] button_clicks.user_id column already exists")
    else:
        print("[!] button_clicks table does not exist, skipping")

    # 5. short_term_messages table (聊天记录按用户隔离)
    if "short_term_messages" in inspector.get_table_names():
        cols = [c["name"] for c in inspector.get_columns("short_term_messages")]
        if "user_id" not in cols:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE short_term_messages ADD COLUMN user_id INTEGER"))
                conn.commit()
            print("[OK] Added short_term_messages.user_id column")
        else:
            print("[SKIP] short_term_messages.user_id column already exists")
    else:
        print("[!] short_term_messages table does not exist, skipping")

    print("=" * 60)
    print("Migration completed!")
    print("=" * 60)


if __name__ == "__main__":
    migrate()
