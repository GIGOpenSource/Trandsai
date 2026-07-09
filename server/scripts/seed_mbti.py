import random
import sys
sys.path.insert(0, "/Users/ablitt/Desktop/AI伴侣/trandsai/server")

from sqlalchemy import text
from core.database import get_db, CompanionORM

MBTI_TYPES = [
    "INTJ", "INTP", "ENTJ", "ENTP",
    "INFJ", "INFP", "ENFJ", "ENFP",
    "ISTJ", "ISFJ", "ESTJ", "ESFJ",
    "ISTP", "ISFP", "ESTP", "ESFP",
]

def main():
    with get_db() as db:
        # 确保列存在
        try:
            db.execute(text("ALTER TABLE companions ADD COLUMN mbti VARCHAR(10) DEFAULT ''"))
            db.commit()
            print("[OK] 已添加 mbti 列")
        except Exception as e:
            print(f"[INFO] mbti 列可能已存在: {e}")

        rows = db.query(CompanionORM).all()
        updated = 0
        for row in rows:
            if not row.mbti:
                row.mbti = random.choice(MBTI_TYPES)
                updated += 1
                print(f"  {row.name} -> {row.mbti}")

        print(f"\n[OK] 共更新 {updated} 个伴侣的 MBTI")

if __name__ == "__main__":
    main()
