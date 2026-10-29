"""全局运行时开关（改这里即可，无需依赖环境变量）。"""

# 自动发朋友圈：1=关闭，0=开启
AUTO_MOMENTS_OFF = 1

# 自动发圈长循环内每隔 N 个 companion 复查 AUTO_MOMENTS_OFF（0=仅入口检查）
AUTO_MOMENTS_LOOP_CHECK_EVERY = 20


def auto_moments_enabled() -> bool:
    """是否开启智能体自动发朋友圈（含定时任务 / warmup / AI 互评）。

    硬关闭：始终 False。恢复时改回 `return AUTO_MOMENTS_OFF == 0`。
    """
    return False
    # return AUTO_MOMENTS_OFF == 0
