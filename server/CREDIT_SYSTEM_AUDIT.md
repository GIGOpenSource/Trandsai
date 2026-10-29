# 积分系统流程检验报告

> 检验时间: 2026-08-13

---

## ✅ 积分扣除行为（全部已实现并记录）

### 1. 对话扣费 (`chat`)

**调用位置**: `api/companions.py:1849`
```python
_cr_result = consume_credits(
    _credit_db, user_id, _cost, "chat", "对话扣费"
)
```

**数据流向**:
```
用户发送消息
  ↓
check_chat_permission() - 检查权限
  ↓
consume_credits(db, user_id, 3, "chat", "对话扣费")
  ↓
_write_transaction(db, user_id, "chat", -3, new_balance)
  ↓
写入 CreditTransactionORM 表 ✅
```

**扣费逻辑** (credit_service.py:302-318):
- 先扣 `daily_gift_remaining`（每日赠送额度）
- 再扣 `balance`（充值余额）
- 记录到 `credit_transactions` 表，`amount = -3`

---

### 2. 创建智能体扣费 (`create_agent`)

**调用位置**: `api/companions.py:1291`
```python
consume_agent_creation(cost_db, user_id)
```

**数据流向**:
```
用户创建智能体
  ↓
check_agent_creation_permission() - 检查VIP + 数量限制 + 积分
  ↓
创建成功后
  ↓
consume_agent_creation(db, user_id)
  ↓
consume_credits(db, user_id, 50, "create_agent", "创建自定义智能体")
  ↓
_write_transaction(db, user_id, "create_agent", -50, new_balance)
  ↓
写入 CreditTransactionORM 表 ✅
```

---

### 3. 智能体每日消耗 (`agent_daily`)

**调用位置**: `services/daily_scheduler.py:126`
```python
consume_credits(db, user_id, total_cost, "agent_daily", f"智能体每日消耗 x{agent_count}")
```

**数据流向**:
```
每日定时任务触发 (daily_scheduler)
  ↓
_agent_daily_cost() - 计算用户所有智能体
  ↓
total_cost = agent_count * 10（每个智能体10积分/天）
  ↓
consume_credits(db, user_id, total_cost, "agent_daily", ...)
  ↓
_write_transaction(db, user_id, "agent_daily", -total_cost, new_balance)
  ↓
写入 CreditTransactionORM 表 ✅
```

---

## ✅ 充值赠送行为（全部已实现并记录）

### 1. 积分包充值 (`recharge`)

**调用位置**: `services/credit_service.py:424-500`

**数据流向**:
```
用户购买积分包 (如 pack_250)
  ↓
recharge_credits(db, user_id, "pack_250", "app_store", transaction_id)
  ↓
├── 写入 RechargeRecordORM (package_key="pack_250", credits=250) ✅
├── 更新 UserCreditORM.balance += 250 ✅
└── _write_transaction(db, user_id, "recharge", +250, new_balance) ✅
    └── 写入 CreditTransactionORM 表
```

**返回数据**:
```json
{
  "ok": true,
  "record_id": 123,
  "credits_added": 250,
  "balance": 500
}
```

---

### 2. VIP开通赠送 (`vip_subscribe` + `vip_gift`)

**调用位置**: `services/credit_service.py:324-419`

**配置来源**: 优先从 `benefit_products` 表读取 `daily_gift_credits` 和 `max_agents`

**数据流向**:
```
用户开通VIP (如 monthly)
  ↓
activate_vip(db, user_id, "monthly", "app_store", transaction_id, product_id="month.subscription")
  ↓
├── 从 benefit_products 表读取配置 ✅
│   - 优先: product_id 匹配 store_product_id 或 apple_product_id
│   - 降级: period 字段匹配 plan_type
│   - 读取: daily_gift_credits, max_agents
│
├── 写入 VipSubscriptionORM ✅
│   (plan_type="monthly", daily_credits=30, max_agents=3)
│
├── 写入 RechargeRecordORM（赠送记录）✅
│   (package_key="vip_gift_monthly", credits=30)
│
├── 更新 UserCreditORM ✅
│   - vip_type = "monthly"
│   - vip_expire_at = now + 30天
│   - daily_gift_total = 30
│   - daily_gift_remaining = 30（立即赠送当日额度）
│   - max_agents = 3
│
└── _write_transaction(db, user_id, "vip_subscribe", +30, new_balance) ✅
    └── 写入 CreditTransactionORM 表
```

**benefit_products 表配置示例**:
| period | daily_gift_credits | max_agents |
|--------|-------------------|------------|
| daily | 10 | 1 |
| weekly | 20 | 2 |
| monthly | 30 | 3 |
| yearly | 50 | 5 |
| lifetime | 100 | 10 |

---

### 3. VIP每日赠送刷新 (`vip_gift`)

**调用位置**: `services/credit_service.py:123-160`

**数据流向**:
```
用户首次登录/发消息（每天）
  ↓
refresh_daily_gifts(db, user_id)
  ↓
检查: is_vip_active() && last_daily_gift_date != today
  ↓
├── daily_gift_remaining = daily_gift_total（重置为每日额度）
├── last_daily_gift_date = today
└── _write_transaction(db, user_id, "vip_gift", +50, new_balance) ✅
    └── 写入 CreditTransactionORM 表
```

---

## 📊 数据表汇总

| 表名 | 用途 | 写入时机 |
|------|------|----------|
| `credit_transactions` | 积分账单（所有变动） | 每次扣费/充值/赠送 |
| `recharge_records` | 积分充值记录 + VIP赠送记录 | 充值/VIP开通时 |
| `vip_subscriptions` | VIP订阅记录 | VIP开通时 |
| `user_credits` | 用户当前积分状态 | 实时更新 |

---

## 🔍 交易记录查询验证

### 前端调用示例

```javascript
// 1. 查询VIP充值记录
GET /api/payment/transactions?type=vip_recharge_record

// 2. 查询积分充值记录（不含赠送）
GET /api/payment/transactions?type=point_recharge_record

// 3. 查询赠送记录（VIP开通赠送）
GET /api/payment/transactions?type=free_recharge_record

// 4. 查询积分使用记录
GET /api/payment/transactions?type=point_use_record
```

### 返回数据结构

**VIP充值记录** (`vip_recharge_record`):
```json
{
  "id": 1,
  "plan_type": "monthly",
  "plan_name": "月度会员",
  "price_usd": 9.99,
  "daily_credits": 50,
  "max_agents": 3,
  "start_at": "2026-08-13T10:00:00Z",
  "expire_at": "2026-09-12T10:00:00Z",
  "status": "active",
  "store_type": "app_store",
  "created_at": "2026-08-13T10:00:00Z"
}
```

**积分充值记录** (`point_recharge_record`):
```json
{
  "id": 1,
  "package_key": "pack_250",
  "package_name": "250积分包",
  "credits": 250,
  "price_usd": 4.99,
  "store_type": "app_store",
  "created_at": "2026-08-13T10:00:00Z"
}
```

**赠送记录** (`free_recharge_record`):
```json
{
  "id": 1,
  "package_key": "vip_gift_monthly",
  "gift_type": "月度会员",
  "credits": 50,
  "created_at": "2026-08-13T10:00:00Z"
}
```

**积分使用记录** (`point_use_record`):
```json
{
  "id": 1,
  "event_type": "chat",
  "event_name": "对话消耗",
  "amount": 3,
  "balance_after": 247,
  "description": "对话扣费",
  "created_at": "2026-08-13T10:05:00Z"
}
```

---

## ✅ 检验结论

| 场景 | 是否扣费 | 是否记录 | 状态 |
|------|----------|----------|------|
| 对话消耗 | ✅ 扣3积分 | ✅ 记录到 credit_transactions | 正常 |
| 创建智能体 | ✅ 扣50积分 | ✅ 记录到 credit_transactions | 正常 |
| 智能体每日消耗 | ✅ 扣10积分/个 | ✅ 记录到 credit_transactions | 正常 |
| 积分包充值 | ✅ 增加余额 | ✅ 记录到 recharge_records + credit_transactions | 正常 |
| VIP开通赠送 | ✅ 赠送积分 | ✅ 记录到 recharge_records + credit_transactions | 正常 |
| VIP每日赠送 | ✅ 刷新额度 | ✅ 记录到 credit_transactions | 正常 |

**所有积分行为均已正确实现并记录到对应数据表** ✅
