"""将 companion_states 表中的 affection 列从 INTEGER 迁移到 REAL/FLOAT"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from core.database import get_db, engine, _is_sqlite


def migrate():
    with get_db() as db:
        if _is_sqlite:
            # SQLite 不支持 ALTER COLUMN，需要重建表
            print("[INFO] SQLite 检测到，使用重建表方式迁移...")
            
            # 1. 创建新表
            db.execute(text("""
                CREATE TABLE companion_states_new (
                    companion_id VARCHAR(8) PRIMARY KEY,
                    mood VARCHAR(20) DEFAULT '开心',
                    affection REAL DEFAULT 0,
                    summary TEXT DEFAULT '',
                    turns INTEGER DEFAULT 0,
                    evolved_personality TEXT DEFAULT '',
                    evolved_background TEXT DEFAULT '',
                    evolved_speech_style TEXT DEFAULT ''
                )
            """))
            
            # 2. 复制数据
            db.execute(text("""
                INSERT INTO companion_states_new 
                SELECT companion_id, mood, CAST(affection AS REAL), summary, turns, 
                       evolved_personality, evolved_background, evolved_speech_style
                FROM companion_states
            """))
            
            # 3. 删除旧表
            db.execute(text("DROP TABLE companion_states"))
            
            # 4. 重命名新表
            db.execute(text("ALTER TABLE companion_states_new RENAME TO companion_states"))
            
            db.commit()
            print("[OK] SQLite 迁移完成")
        else:
            # MySQL/PostgreSQL 可以直接 ALTER COLUMN
            try:
                db.execute(text("ALTER TABLE companion_states MODIFY COLUMN affection FLOAT DEFAULT 0"))
                db.commit()
                print("[OK] MySQL/PostgreSQL 迁移完成")
            except Exception as e:
                print(f"[WARN] 直接修改列类型失败，尝试其他方式: {e}")
                # 尝试添加临时列、复制数据、删除旧列、重命名
                db.execute(text("ALTER TABLE companion_states ADD COLUMN affection_new FLOAT DEFAULT 0"))
                db.execute(text("UPDATE companion_states SET affection_new = affection"))
                db.execute(text("ALTER TABLE companion_states DROP COLUMN affection"))
                db.execute(text("ALTER TABLE companion_states CHANGE affection_new affection FLOAT DEFAULT 0"))
                db.commit()
                print("[OK] 通过临时列方式迁移完成")


if __name__ == "__main__":
    migrate()
