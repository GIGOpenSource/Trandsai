import random
from datetime import datetime, timedelta
from sqlalchemy import text
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from core.database import engine
from services.image_generation import generate_image_with_cache, generate_moment_image_prompt

# 获取所有伴侣
companions = []
with engine.connect() as conn:
    result = conn.execute(text("SELECT id, name, city FROM companions"))
    for row in result:
        companions.append({"id": row.id, "name": row.name, "city": row.city})

if not companions:
    print("没有伴侣数据，请先创建伴侣")
    exit(1)

print(f"找到 {len(companions)} 个伴侣: {[c['name'] for c in companions]}")

# 朋友圈文案池
captions = [
    "今天终于去打卡了那家收藏半年的店，结果… 一个人吃了两份甜点，谁懂😌",
    "最近在看一本很妙的书，看到了第三章，突然想：如果有人能聊聊这段就好了",
    "今天居然被陌生人夸了一句（夸的什么保密），好心情持续到现在 ✨",
    "刚发现了一个离谱但超好用的小技巧，有没有人想听？算了，我憋不住先说…",
    "有人问我最近在忙什么。我说：忙着把日子过成别人想参与的样子 😏",
    "我今年的生活状态：不求被理解，但欢迎对号入座",
    "人真的要多出去走走，不然你怎么知道，今天会遇到谁、发生什么呢",
    "偶尔觉得，主动的人真挺可爱的。比如现在——这条就是给你们主动点赞的。",
    "我发现一件事：当我开始认真爱自己，突然就不缺别人的喜欢了。",
    "我现在过得太好了，唯一的bug是… 没有可以分享这杯咖啡的人 ☕️",
    "今天的咖啡比昨天的好喝，大概是因为心情不错",
    "刷到一句话说'吸引力就是答案'，我深以为然。",
    "最近换了新的香水，走在街上感觉自己气场两米八 ✨",
    "周末去了一家很隐秘的bar，酒保问我是不是常客。我说：今天第一次，但以后可能会是 🍸",
    "今天穿了一身很满意的搭配，照镜子的时候觉得：嗯，这个人值得被搭讪",
    "刚健完身，汗流浃背但心情超爽。有没有人懂这种快乐 💪",
    "朋友说我最近变化很大，问我是不是恋爱了。我笑而不语 😏",
    "今天拒绝了两个邀约，不是高冷，是真的想一个人待着。但如果是你，可能例外。",
    "新买的唱片到了，正在听。如果你也在听同一首歌，那我们挺有缘的 🎵",
    "今天的日落特别美，我站在那里看了十分钟。然后想：这么美的时刻，应该和谁分享呢 🌅",
    "刚才在地铁上被让座了，我看起来有那么柔弱吗？明明我气场很强的 💅",
    "今天的早餐是自己做的，摆盘花了二十分钟。不为谁，就是想让今天有个好看的開始 🍳",
    "我发现一个规律：每次我状态最好的时候，总会有奇怪的人出现。欢迎来对号入座",
    "练了一下午的琴，手指有点疼但心里特别安静。这种时刻，不太想被打扰，除非是你 🎹",
    "今天一个人看了场电影，散场后在商场逛了三个小时。自由的感觉真好，虽然偶尔也想有人拎包",
    "最近开始学习调酒，第一杯献给了自己。第二杯… 看心情 🍹",
    "今天的风很大，吹乱了头发。我站在路口没急着整理，觉得这种凌乱也挺好看的 💨",
    "有人说我朋友圈发得太少了。我说：好东西都是限量的，包括我的动态",
    "刚做完SPA，皮肤状态好到发光。现在出门买杯奶茶，希望有人能发现我的光 ✨",
    "今天的计划是：把自己收拾得漂漂亮亮，然后去书店坐一下午。不为遇见谁，就是心情好 📚",
    "我发现我对美食的容忍度越来越低了——好吃就是好吃，不好吃的第二口都不想吃。对人也一样 🍽️",
    "昨晚失眠，凌晨三点起来画画。画完了发现天亮了，这种疯狂的时刻居然有点浪漫 🎨",
    "今天在电梯里遇到一只超乖的金毛，它主人说它只喜欢好看的人。我信了 🐕",
    "朋友约我去相亲，我说不用了，我现在的生活不缺人，缺的是让我想开口的人",
    "今天的奶茶甜度刚刚好，就像我现在的生活——不甜不腻，刚好让人想再尝一口 🧋",
    "整理衣柜的时候翻到了去年买的一条裙子，还没穿过。今年一定要穿它出门，看看会不会被搭讪 👗",
    "今天的阳光特别偏爱我的书桌，我坐在那里不想动。如果阳光会说话，它应该在夸我 ☀️",
    "刚学会了一道新菜，卖相一百分。现在缺一个敢第一个试吃的人，有志愿者吗 🍲",
    "我发现快乐真的很简单：一杯好喝的咖啡 + 一个不用赶时间的下午。至于其他的，随缘 ☕️",
    "今天跑步的时候超过了三个人，虽然他们都是散步的，但我还是觉得自己很厉害 🏃",
    "最近开始写手账了，把每天的小确幸记下来。昨天那页写的是：被陌生人夸了好看 ✍️",
    "今天在美术馆里待了一下午，有一幅画我看了二十分钟。出来以后觉得，自己也该活得像一幅值得细看的画 🖼️",
    "刚收到一个快递，拆开发现是上个月冲动下单的耳环。戴上照了镜子：嗯，冲动是对的 💎",
    "今天的雨下得很有节奏感，我坐在窗边跟着敲手指。然后想：如果有人能一起听雨就好了，但我先不说是谁 🌧️",
    "周末去爬山，到山顶的时候只有我一个人。那种独占风景的感觉，又爽又有点想炫耀 ⛰️",
    "最近在读一本关于吸引力的书，里面说'你是什么样的人，就会吸引什么样的人'。我合上书本，深以为然 📖",
    "今天的晚餐是自己做的意面，番茄酱汁调得刚刚好。现在缺一杯红酒，和一个人 🍝",
    "刚才在便利店排队，前面的小哥哥回头看了我三次。我没看他，但我嘴角上扬了 😌",
    "我发现我最近越来越'难搞'了——不是要求高，是知道自己值得更好的。包括对朋友，对生活方式，对一切 💅",
    "今天的月亮特别圆，我拍了张照片，然后删了。有些美景，自己看过就够了。除非你在我身边 🌕",
]

random.shuffle(captions)

# 生成50条，时间分布在过去30天内
from datetime import timezone
now = datetime.now(timezone.utc)
moments_data = []

print("\n开始生成朋友圈配图（AI生成 + COS上传）...")

for i in range(50):
    companion = companions[i % len(companions)]
    # 过去30天内随机时间
    days_ago = random.randint(0, 30)
    hour = random.randint(6, 23)
    minute = random.randint(0, 59)
    second = random.randint(0, 59)
    created_at = now - timedelta(days=days_ago, hours=now.hour - hour, minutes=now.minute - minute, seconds=now.second - second)
    # 确保created_at不可能是未来时间
    if created_at > now:
        created_at = now - timedelta(hours=1)

    caption = captions[i % len(captions)]

    # 使用 AI 生成配图
    try:
        profile_dict = {
            "gender": "女",  # 默认
            "age": 22,
            "city": companion.get("city", ""),
            "personality": "",
            "hobbies": "",
            "mbti": "",
        }
        prompt, img_style = generate_moment_image_prompt(caption, profile=profile_dict)
        image_url = generate_image_with_cache(prompt, style=img_style, width=600, height=600)
        if not image_url or not image_url.startswith("http"):
            print(f"  [{i+1}/50] ⚠ 配图生成失败，跳过: {caption[:20]}...")
            continue
        print(f"  [{i+1}/50] ✓ 配图已生成: {image_url[:60]}...")
    except Exception as e:
        print(f"  [{i+1}/50] ✗ 配图生成异常: {e}")
        continue

    likes = random.randint(3, 88)
    comments = random.randint(0, 25)

    moments_data.append({
        "companion_id": companion["id"],
        "image_url": image_url,
        "caption": caption,
        "likes_count": likes,
        "comments_count": comments,
        "created_at": created_at.strftime("%Y-%m-%d %H:%M:%S"),
    })

# 按时间排序（新到旧）
moments_data.sort(key=lambda x: x["created_at"], reverse=True)

# 插入数据库
inserted = 0
with engine.begin() as conn:
    for m in moments_data:
        conn.execute(
            text("""
                INSERT INTO moments (companion_id, image_url, caption, likes_count, comments_count, created_at)
                VALUES (:companion_id, :image_url, :caption, :likes_count, :comments_count, :created_at)
            """),
            m
        )
        inserted += 1

print(f"\n成功插入 {inserted} 条朋友圈数据")

# 验证
with engine.connect() as conn:
    count = conn.execute(text("SELECT COUNT(*) FROM moments")).scalar()
    print(f"数据库中朋友圈总数: {count}")

    result = conn.execute(text("""
        SELECT c.name, COUNT(*) as cnt
        FROM moments m
        JOIN companions c ON m.companion_id = c.id
        GROUP BY c.name
    """))
    print("各伴侣朋友圈数量:")
    for row in result:
        print(f"  {row.name}: {row.cnt} 条")
