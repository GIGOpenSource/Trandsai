# TrandsAI API 路由文档

> 自动生成于 2026-08-13，方便快速定位接口代码位置

---

## 📁 路由文件结构

```
server/
├── main.py                    # 应用入口，注册所有路由
├── api/
│   ├── auth.py               # 认证相关
│   ├── admin.py              # 管理后台
│   ├── companions.py         # AI伴侣
│   ├── moments.py            # 朋友圈
│   ├── posts.py              # 社区帖子
│   ├── payment.py            # 支付/积分
│   ├── benefits.py           # 福利商品
│   ├── feedback.py           # 用户反馈
│   ├── analytics.py          # 数据统计
│   ├── culture.py            # 文化数据
│   ├── devices.py            # 设备推送
│   ├── google_login.py       # Google登录
│   └── apple_login.py        # Apple登录
└── core/
    ├── middleware.py          # 中间件
    ├── auth.py               # 认证逻辑
    └── ...
```

---

## 🔐 认证模块 (`api/auth.py`)

| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| POST | `/api/auth/register` | 用户注册 | 228 |
| POST | `/api/auth/check-username` | 检查用户名可用 | 324 |
| POST | `/api/auth/login` | 用户登录 | 484 |
| POST | `/api/auth/logout` | 用户登出 | 554 |
| POST | `/api/auth/delete-account` | 注销账号 | 562 |
| GET | `/api/auth/me` | 获取当前用户信息 | 630 |
| PATCH | `/api/auth/me` | 更新用户信息（提交审核） | 679 |
| GET | `/api/users/stats` | 获取用户统计 | 727 |

---

## 🤖 AI伴侣模块 (`api/companions.py`)

| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| POST | `/companions/generate` | 生成伴侣 | 1149 |
| POST | `/companions` | 创建伴侣 | 1255 |
| GET | `/companions` | 获取伴侣列表 | 1315 |
| GET | `/companions/{companion_id}` | 获取伴侣详情 | 1364 |
| GET | `/companions/{companion_id}/messages` | 获取聊天记录 | 1373 |
| PUT | `/companions/{companion_id}` | 更新伴侣 | 1445 |
| POST | `/companions/{companion_id}/generate-avatar` | 生成头像 | 1477 |
| DELETE | `/companions/{companion_id}` | 删除伴侣 | 1490 |
| POST | `/companions/{companion_id}/outreach-preference` | 屏蔽触达 | 1502 |
| GET | `/companions/{companion_id}/outreach-preference` | 查询触达偏好 | 1529 |
| POST | `/companions/{companion_id}/clear-messages` | 清空聊天记录 | 1539 |
| POST | `/knowledge/search` | 搜索知识库 | 2452 |

---

## 📱 朋友圈模块 (`api/moments.py`)

| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/api/moments` | 获取朋友圈列表 | 80 |
| POST | `/api/moments/{moment_id}/like` | 点赞/取消点赞 | 221 |
| POST | `/api/moments/{moment_id}/comment` | 发表评论 | 240 |
| POST | `/api/moments/{moment_id}/regenerate-image` | 重新生成配图 | 265 |
| GET | `/api/moments/{moment_id}` | 获取朋友圈详情 | 289 |
| GET | `/api/companions/{companion_id}/moments` | 获取伴侣的朋友圈 | 303 |
| POST | `/api/moments/{moment_id}/icebreaker` | 生成破冰文案 | 314 |

---

## 📝 社区帖子模块 (`api/posts.py`)

| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/api/posts` | 获取帖子列表 | 53 |
| GET | `/api/posts/search` | 搜索帖子 | 75 |
| GET | `/api/posts/my` | 获取我的帖子 | 97 |
| POST | `/api/posts` | 发布帖子 | 122 |
| GET | `/api/posts/{post_id}` | 获取帖子详情 | 175 |
| POST | `/api/posts/{post_id}/like` | 点赞/取消点赞 | 209 |
| POST | `/api/posts/{post_id}/comment` | 发表评论 | 228 |
| DELETE | `/api/posts/{post_id}` | 删除帖子 | 255 |
| POST | `/api/upload/image` | 上传图片 | 281 |

---

## 💳 支付/积分模块 (`api/payment.py`)

| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/api/payment/credit-explain` | 积分说明 | 82 |
| GET | `/api/payment/vip-status` | 获取VIP和积分状态 | 100 |
| GET | `/api/payment/config` | 获取VIP和充值包配置 | 107 |
| GET | `/api/payment/transactions` | 获取交易记录（4种类型） | 120 |
| POST | `/api/payment/check-permission` | 权限检查 | 280 |
| POST | `/api/payment/verify-apple` | Apple IAP 验证 | 310 |
| POST | `/api/payment/verify-apple-receipt` | Apple 收据验证 | 384 |
| POST | `/api/payment/verify-google-receipt` | Google Play 验证 | 584 |
| POST | `/api/payment/apple-notification` | Apple 通知回调 | 781 |
| POST | `/api/payment/google-notification` | Google 通知回调 | 999 |

**交易记录类型说明** (`type` 参数):
| type值 | 说明 | 数据来源 |
|--------|------|----------|
| `vip_recharge_record` | VIP充值/续费记录 | VipSubscriptionORM |
| `point_recharge_record` | 积分充值记录（不含赠送） | RechargeRecordORM (排除vip_gift_*) |
| `free_recharge_record` | 赠送记录（VIP开通赠送） | RechargeRecordORM (只查vip_gift_*) |
| `point_use_record` | 积分使用记录 | CreditTransactionORM (amount < 0) |

---

## 🎁 福利商品模块 (`api/benefits.py`)

### 前端接口
| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/api/benefits` | 获取当前区域福利 | 134 |

### 管理后台接口
| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/api/admin/benefits/products` | 商品列表 | 221 |
| POST | `/api/admin/benefits/products` | 创建商品 | 237 |
| PUT | `/api/admin/benefits/products/{product_id}` | 更新商品 | 295 |
| DELETE | `/api/admin/benefits/products/{product_id}` | 删除商品 | 332 |
| GET | `/api/admin/benefits/templates` | 模板列表 | 350 |
| POST | `/api/admin/benefits/templates` | 创建模板 | 383 |
| PUT | `/api/admin/benefits/templates/{template_id}` | 更新模板 | 445 |
| DELETE | `/api/admin/benefits/templates/{template_id}` | 删除模板 | 506 |
| POST | `/api/admin/benefits/templates/{template_id}/products` | 绑定商品 | 524 |
| DELETE | `/api/admin/benefits/templates/{template_id}/products/{product_id}` | 解绑商品 | 556 |
| POST | `/api/admin/benefits/templates/{template_id}/regions` | 绑定区域 | 580 |
| DELETE | `/api/admin/benefits/templates/{template_id}/regions/{region_code}` | 解绑区域 | 612 |
| POST | `/api/admin/benefits/sync-prices` | 同步Apple价格 | 636 |
| GET | `/api/admin/benefits/prices/{product_id}` | 查看地区价格 | 712 |
| GET | `/api/admin/benefits/test-apple-price` | 测试Apple价格 | 746 |

---

## 💬 反馈模块 (`api/feedback.py`)

| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| POST | `/api/feedback` | 提交反馈 | 38 |
| GET | `/api/feedback/messages` | 获取反馈消息 | 184 |

---

## 📊 数据统计模块 (`api/analytics.py`)

| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| POST | `/api/analytics/page-view` | 记录页面浏览 | 28 |
| POST | `/api/analytics/button-click` | 记录按钮点击 | 43 |

---

## 🌍 文化数据模块 (`api/culture.py`)

| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/api/culture/names` | 获取随机姓名 | 10 |
| GET | `/api/culture/cities` | 获取城市列表 | 21 |

---

## 📱 设备推送模块 (`api/devices.py`)

| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| POST | `/push-token` | 注册推送token | 38 |
| DELETE | `/push-token` | 注销推送token | 57 |

---

## 🔑 第三方登录

### Google登录 (`api/google_login.py`)
| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| POST | `/api/auth/google` | Google登录 | 45 |

### Apple登录 (`api/apple_login.py`)
| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| POST | `/api/auth/apple` | Apple登录 | 252 |

---

## 👨‍💼 管理后台模块 (`api/admin.py`)

### 认证
| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| POST | `/api/admin/login` | 管理员登录 | 178 |

### 系统统计
| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/api/admin/stats` | 获取系统统计 | 203 |
| GET | `/api/admin/analytics` | 获取分析数据 | 1981 |
| GET | `/api/admin/analytics/export` | 导出分析数据 | 2107 |

### 伴侣管理
| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/api/admin/companions` | 获取伴侣列表 | 234 |
| GET | `/api/admin/companions/{companion_id}` | 获取伴侣详情 | 293 |
| POST | `/api/admin/companions` | 创建伴侣 | 1170 |
| PUT | `/api/admin/companions/{companion_id}` | 更新伴侣 | 285 |
| DELETE | `/api/admin/companions/{companion_id}` | 删除伴侣 | 247 |
| POST | `/api/admin/companions/{companion_id}/restore` | 恢复伴侣 | 268 |
| POST | `/api/admin/companions/reload-cache` | 刷新缓存 | 255 |
| POST | `/api/admin/companions/batch-generate` | 批量生成 | 1458 |
| POST | `/api/admin/companions/batch-generate-stream` | 流式批量生成 | 1475 |
| POST | `/api/admin/companions/batch-generate-all-langs-stream` | 多语言批量生成 | 1556 |
| GET | `/api/admin/companions/{companion_id}/agent-config` | 获取Agent配置 | 833 |
| PUT | `/api/admin/companions/{companion_id}/agent-config` | 更新Agent配置 | 844 |

### 朋友圈管理
| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/api/admin/moments` | 获取朋友圈列表 | 349 |
| POST | `/api/admin/moments` | 创建朋友圈 | 1577 |
| PUT | `/api/admin/moments/{moment_id}` | 更新朋友圈 | 1644 |
| DELETE | `/api/admin/moments` | 批量删除 | 360 |
| DELETE | `/api/admin/moments/{moment_id}` | 删除朋友圈 | 471 |
| POST | `/api/admin/moments/{moment_id}/regenerate-image` | 重新生成配图 | 480 |
| POST | `/api/admin/moments/regenerate` | 批量重新生成 | 418 |
| POST | `/api/admin/moments/batch-generate` | 批量生成 | 432 |

### 用户管理
| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/api/admin/users` | 获取用户列表 | 497 |
| POST | `/api/admin/users` | 创建用户 | 1669 |
| PUT | `/api/admin/users/{user_id}` | 更新用户 | 1724 |
| DELETE | `/api/admin/users/{user_id}` | 删除用户 | 1871 |
| GET | `/api/admin/users/{user_id}/companion-stats` | 用户伴侣统计 | 1767 |
| PUT | `/api/admin/users/{user_id}/companion-stats/{companion_id}` | 更新统计 | 1801 |
| POST | `/api/admin/users/batch-delete` | 批量删除用户 | 2346 |

### 知识库管理
| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/api/admin/knowledge` | 获取知识库列表 | 330 |
| POST | `/api/admin/knowledge` | 添加知识条目 | 335 |
| PUT | `/api/admin/knowledge/{entry_id}` | 更新知识条目 | 1927 |
| DELETE | `/api/admin/knowledge/{entry_id}` | 删除知识条目 | 341 |
| POST | `/api/admin/knowledge/search` | 搜索知识库 | 522 |
| POST | `/api/admin/knowledge/batch-delete` | 批量删除 | 2364 |
| GET | `/api/admin/embedding-status` | 嵌入状态 | 532 |

### 配置管理
| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/api/admin/config` | 获取系统配置 | 674 |
| POST | `/api/admin/config` | 更新系统配置 | 685 |
| POST | `/api/admin/config/test` | 测试模型连接 | 716 |
| GET | `/api/admin/agent-config` | 获取Agent配置 | 811 |
| PUT | `/api/admin/agent-config` | 更新Agent配置 | 820 |
| GET | `/api/admin/config-groups` | 获取配置组列表 | 539 |
| GET | `/api/admin/config-groups/{group_id}` | 获取配置组详情 | 560 |
| POST | `/api/admin/config-groups` | 创建配置组 | 580 |
| PUT | `/api/admin/config-groups/{group_id}` | 更新配置组 | 645 |
| DELETE | `/api/admin/config-groups/{group_id}` | 删除配置组 | 664 |
| POST | `/api/admin/config-groups/{group_id}/test` | 测试配置组 | 723 |

### 反馈管理
| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/api/admin/feedback` | 获取反馈列表 | 859 |
| GET | `/api/admin/feedback/{thread_id}/messages` | 获取反馈详情 | 895 |
| POST | `/api/admin/feedback/{thread_id}/reply` | 回复反馈 | 1143 |
| POST | `/api/admin/feedback` | 创建反馈会话 | 1886 |
| DELETE | `/api/admin/feedback/{thread_id}` | 删除反馈会话 | 1913 |

### 聊天会话管理
| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/api/admin/chat-sessions` | 获取会话列表 | 1002 |
| GET | `/api/admin/chat-sessions/messages` | 获取会话消息 | 927 |
| DELETE | `/api/admin/chat-sessions` | 批量删除会话 | 1078 |

### 通知管理
| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/api/notifications` | 获取系统通知 | 2489 |
| GET | `/api/admin/notifications` | 获取通知列表 | 2276 |
| POST | `/api/admin/notifications` | 创建通知 | 2294 |
| DELETE | `/api/admin/notifications/{notification_id}` | 删除通知 | 2317 |
| POST | `/api/admin/notifications/batch-delete` | 批量删除通知 | 2328 |

### 内容审核
| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/api/admin/audit/posts` | 帖子审核列表 | 2380 |
| POST | `/api/admin/audit/posts/{post_id}/approve` | 通过帖子 | 2397 |
| POST | `/api/admin/audit/posts/{post_id}/reject` | 驳回帖子 | 2414 |
| GET | `/api/admin/audit/profiles` | 资料审核列表 | 2433 |
| POST | `/api/admin/audit/profiles/{review_id}/approve` | 通过资料 | 2450 |
| POST | `/api/admin/audit/profiles/{review_id}/reject` | 驳回资料 | 2467 |

### 其他
| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| POST | `/api/admin/reset-all` | 重置所有数据 | 368 |
| POST | `/api/admin/generate-image` | 生成图片 | 1937 |
| GET | `/api/admin/outreach/funnel` | 运营漏斗看板 | 2548 |
| POST | `/api/admin/outreach/phase3-scan` | 触发Phase3扫描 | 2559 |

---

## 🔧 系统接口 (`main.py`)

| 方法 | 路径 | 说明 | 行号 |
|------|------|------|------|
| GET | `/` | API根路径 | 478 |
| GET | `/api/health` | 健康检查 | 473 |
| GET | `/admin` | 管理后台重定向 | 464 |

---

## 📌 快速查找指南

**我要改登录逻辑** → `api/auth.py`

**我要改聊天/AI对话** → `api/companions.py` + `services/` 目录

**我要改朋友圈** → `api/moments.py`

**我要改支付/VIP** → `api/payment.py`

**我要改管理后台** → `api/admin.py`

**我要改社区帖子** → `api/posts.py`

**我要改推送通知** → `api/devices.py`

---

## ⚠️ 注意事项

1. **Protected Files**: `server/core/response.py` 不可修改（除非明确要求）
2. **路由前缀**: 部分路由文件定义了 `prefix`，实际路径可能有前缀
3. **WebSocket**: 聊天 WebSocket 路由在 `companions.py` 中，以 `/ws/` 开头
4. **行号参考**: 行号基于 2026-08-13 扫描结果，代码变动后可能偏移
