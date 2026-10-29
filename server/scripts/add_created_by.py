import sys
sys.path.insert(0, "/Users/ablitt/Desktop/AI伴侣/trandsai/server")

from sqlalchemy import text
from core.database import get_db, CompanionORM


def main():
    with get_db() as db:
        # 确保列存在
        try:
            db.execute(text("ALTER TABLE companions ADD COLUMN created_by VARCHAR(64) DEFAULT ''"))
            db.commit()
            print("[OK] 已添加 created_by 列")
        except Exception as e:
            print(f"[INFO] created_by 列可能已存在: {e}")


if __name__ == "__main__":
    main()
