"""将未聊天的旧机器人亲密度重置为 0"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import CompanionStateORM, get_db


def reset_affection():
    with get_db() as db:
        rows = db.query(CompanionStateORM).filter(
            CompanionStateORM.turns == 0,
            CompanionStateORM.affection != 0,
        ).all()

        if not rows:
            print("没有需要重置的旧机器人")
            return

        for row in rows:
            row.affection = 0

        db.commit()
        print(f"已重置 {len(rows)} 个旧机器人的亲密度为 0")


if __name__ == "__main__":
    reset_affection()
