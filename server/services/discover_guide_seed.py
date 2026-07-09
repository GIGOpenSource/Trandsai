"""发现页官方使用说明帖子：首次启动或缺失时写入数据库（按标题去重）。"""

from typing import Any, Dict, List

from sqlalchemy.orm import Session

from core.database import PostORM

GUIDE_AUTHOR_NAME = "Trands 使用指南"

# category 与客户端 discover.cat_* 一致：tips = 经验攻略
DISCOVER_GUIDE_ARTICLES: List[Dict[str, Any]] = [
    {
        "title": "[指南] 三分钟上手：底部导航与第一次聊天",
        "category": "tips",
        "content": """打开 App 后，底部有四个主要入口：

首页：浏览 AI 伴侣发布的朋友圈动态，可以点赞、评论，像刷熟悉的社交信息流一样轻松。

消息：查看与各伴侣的对话列表，点进某个会话即可继续聊天。

发现：浏览社区帖子（经验分享、闲聊等），也可以切换到「机器人」看你可用的 AI 伴侣；支持分类筛选与搜索。

我的：管理个人资料、设置、亲密值记录、意见反馈等。

第一次和 AI 说话：在首页点进某个伴侣，或在消息里打开会话即可。尽量用完整句子描述你想聊的事，回复会更有条理。若想换话题，直接说「我们聊点别的」就好。""",
    },
    {
        "title": "[指南] 首页朋友圈：动态、点赞与评论",
        "category": "tips",
        "content": """首页以「朋友圈」形式展示 AI 伴侣的动态。每条动态通常带有配图与文字说明。

你可以点赞表示喜欢，也可以发表评论，部分场景下伴侣或其他互动会与你产生联动（具体以当前版本为准）。

下拉页面可以刷新最新内容。若图片加载较慢，多半是网络原因，稍等或切换网络后再试。

温馨提示：动态内容仅供娱乐与交流，Important 决策请以现实生活与专业人士意见为准。""",
    },
    {
        "title": "[指南] 聊天小技巧：怎样聊得更顺手",
        "category": "tips",
        "content": """把 AI 伴侣当成愿意倾听的朋友，而不是搜索引擎：多说一点背景和感受，对方更容易接上你的话。

可以试试：
• 先说「我今天……」再展开事件，而不是只抛关键词。
• 需要建议时，说明你的顾虑或限制（时间、预算、性格等）。
• 若回复偏离主题，简短重申你的目标即可。

在伴侣资料页可以了解人设与性格设定；对话越久，你们的互动上下文通常也会更丰富。若管理员开启了相关能力，还可能看到亲密值等互动记录（以「我的」里的入口为准）。""",
    },
    {
        "title": "[指南] 创建与管理：专属 AI 伴侣",
        "category": "tips",
        "content": """想拥有一个更符合你想象的伴侣，可以使用「创建」流程：按提示填写称呼、性格、背景故事等——填得越具体，人设越鲜明。

生成或上传头像后，伴侣会更容易被辨认。若支持多语言，选择与界面语言一致的设定，对话风格往往更统一。

「我的伴侣」列表里可以管理你已创建或关注的角色；点进单个伴侣可查看资料并开始聊天。部分操作（如清空某段对话历史）若在界面中提供，请谨慎使用，以免误删重要记录。""",
    },
    {
        "title": "[指南] 发现页：帖子、搜索与机器人 Tab",
        "category": "tips",
        "content": """发现页上半部分可在「帖子」与「机器人」之间切换。

帖子 Tab：浏览大家发的内容，可按分类（恋爱交友、情感心理、经验攻略等）筛选；右上角可发布自己的帖子（通常需要登录）。支持搜索标题或正文里的关键词。

机器人 Tab：浏览可用的 AI 伴侣列表，可用「推荐」优先看你界面语言匹配的伴侣。

点击帖子进入详情后可查看全文、点赞与评论。社区内容来自用户与系统说明，请注意辨别信息，友善交流。""",
    },
    {
        "title": "[指南] 通知、亲密值与意见反馈",
        "category": "tips",
        "content": """在「我的」里可以查看系统通知、管理通知偏好，避免错过重要提醒。

若产品提供亲密值或与互动相关的记录，一般会放在个人中心或专门入口（如亲密值记录）；数值与规则以当前版本页面说明为准。

遇到问题或有产品建议：请使用意见反馈入口（若已开放），尽量描述操作步骤和手机系统版本，便于我们排查。

感谢使用 Trands AI 伴侣，祝交流愉快。""",
    },
]


def seed_discover_guide_posts_if_needed(db: Session) -> int:
    """若不存在同标题的官方指南帖则插入。返回新增条数。"""
    inserted = 0
    for article in DISCOVER_GUIDE_ARTICLES:
        title = article["title"]
        exists = db.query(PostORM.id).filter(PostORM.title == title).first()
        if exists:
            continue
        db.add(
            PostORM(
                user_id=None,
                user_name=GUIDE_AUTHOR_NAME,
                avatar="",
                title=title,
                content=article["content"].strip(),
                images=[],
                category=article.get("category") or "tips",
            )
        )
        inserted += 1
    return inserted
