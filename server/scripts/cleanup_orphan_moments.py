"""
清理孤儿朋友圈数据：删除 companion 已不存在的朋友圈、评论和点赞。
运行方式：python server/scripts/cleanup_orphan_moments.py
"""
import sys
import os

# 确保能 import server 目录下的模块
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.database import (
    CompanionORM, MomentORM, MomentCommentORM, MomentLikeORM, get_db
)


def cleanup_orphan_data():
    with get_db() as db:
        # 获取所有存在的 companion_id
        existing_ids = {c.id for c in db.query(CompanionORM.id).all()}
        print(f"现有智能体数量: {len(existing_ids)}")

        # 1. 清理孤儿朋友圈（companion 已不存在的朋友圈）
        all_moments = db.query(MomentORM).all()
        orphan_moment_ids = [
            m.id for m in all_moments if m.companion_id not in existing_ids
        ]
        if orphan_moment_ids:
            # 先删评论
            deleted_comments = db.query(MomentCommentORM).filter(
                MomentCommentORM.moment_id.in_(orphan_moment_ids)
            ).delete(synchronize_session=False)
            # 删点赞
            deleted_likes = db.query(MomentLikeORM).filter(
                MomentLikeORM.moment_id.in_(orphan_moment_ids)
            ).delete(synchronize_session=False)
            # 删朋友圈
            deleted_moments = db.query(MomentORM).filter(
                MomentORM.id.in_(orphan_moment_ids)
            ).delete(synchronize_session=False)
            print(f"清理孤儿朋友圈: {deleted_moments} 条, 关联评论: {deleted_comments} 条, 关联点赞: {deleted_likes} 条")
        else:
            print("没有孤儿朋友圈")

        # 2. 清理孤儿评论（companion 已不存在的评论，可能在别人的朋友圈下）
        orphan_comments = db.query(MomentCommentORM).filter(
            MomentCommentORM.companion_id != "",
            MomentCommentORM.companion_id.isnot(None),
            MomentCommentORM.companion_id.notin_(existing_ids)
        ).all()
        if orphan_comments:
            orphan_comment_ids = [c.id for c in orphan_comments]
            # 更新被删评论所在朋友圈的评论计数
            affected_moment_ids = list({c.moment_id for c in orphan_comments})
            deleted = db.query(MomentCommentORM).filter(
                MomentCommentORM.id.in_(orphan_comment_ids)
            ).delete(synchronize_session=False)
            # 修正受影响朋友圈的评论计数
            for mid in affected_moment_ids:
                real_count = db.query(MomentCommentORM).filter_by(moment_id=mid).count()
                moment = db.query(MomentORM).filter_by(id=mid).first()
                if moment:
                    moment.comments_count = real_count
            print(f"清理孤儿评论: {deleted} 条 (分布在 {len(affected_moment_ids)} 条朋友圈)")
        else:
            print("没有孤儿评论")

        db.commit()
        print("清理完成!")


if __name__ == "__main__":
    cleanup_orphan_data()
