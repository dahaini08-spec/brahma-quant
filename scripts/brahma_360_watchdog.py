"""
梵天360巡检脚本
[封印 2026-08-30 苏摩111]

cron调用：每4H运行一次
- 检查71项覆盖率
- 检查cron任务健康
- 覆盖率<90%或cron异常 → 推送苏摩
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from brahma_brain.brahma_health import run_watchdog


def main():
    result = run_watchdog('BTCUSDT')

    rate     = result.get('rate', 0)
    covered  = result.get('covered', 0)
    total    = result.get('total', 0)
    missing  = result.get('missing', [])
    healthy  = result.get('healthy', False)
    price    = result.get('price', 0)
    regime   = result.get('regime', '')
    ts       = result.get('checked_at', '')

    if result.get('error'):
        print(f'🔴 梵天360自检失败: {result["error"]}')
        return

    if healthy:
        # 健康 → 静默
        print(f'HEARTBEAT_OK 梵天360自检: {rate}% ({covered}/{total}) {ts}')
    else:
        # 异常 → 推送警告
        miss_str = ' / '.join(missing[:8])
        print(f'''🔴 梵天360自检异常 | {ts}
覆盖率: {rate}% ({covered}/{total}) — 低于90%警戒线
当前价: ${price:,.0f} | 体制: {regime}
缺失项: {miss_str}
请苏摩检查并修复后重新封印。''')


if __name__ == '__main__':
    main()
