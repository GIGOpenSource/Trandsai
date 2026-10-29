"""将 companion_states 表中的 affection 列从 INTEGER 迁移到 FLOAT（仅 PostgreSQL）"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from core.database import get_db, engine


def migrate():
    with get_db() as db:
        try:
            db.execute(text("ALTER TABLE companion_states ALTER COLUMN affection TYPE FLOAT"))
            db.commit()
            print("[OK] PostgreSQL 迁移完成")
        except Exception as e:
            print(f"[WARN] 修改列类型失败: {e}")


if __name__ == "__main__":
    migrate()
