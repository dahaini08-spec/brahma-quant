# VIP极简策略模板 v1.0
# 原则：VIP只看策略，不看分析过程
# 更新：2026-08-26 苏摩111

---

🌿 **VIP · 姓赵不宣** | {DATE} {TIME} UTC
━━━━━━━━━━━━━━━━━━━━━━

**₿ BTC ${BTC_PRICE}**

🔴 **做空** `${BTC_SHORT_LOW} ~ ${BTC_SHORT_HIGH}`
止损 `${BTC_SHORT_SL}` · TP `${BTC_SHORT_TP}` · **R:R {BTC_SHORT_RR}**
仓位 {BTC_SHORT_SIZE}NAV · {LEV}x

🟢 **做多** `${BTC_LONG_LOW} ~ ${BTC_LONG_HIGH}`
止损 `${BTC_LONG_SL}` · TP `${BTC_LONG_TP}` · **R:R {BTC_LONG_RR}**
仓位 {BTC_LONG_SIZE}NAV · {LEV}x

━━━━━━━━━━━━━━━━━━━━━━

**Ξ ETH ${ETH_PRICE}**

🔴 **做空** `${ETH_SHORT_LOW} ~ ${ETH_SHORT_HIGH}`
止损 `${ETH_SHORT_SL}` · TP `${ETH_SHORT_TP}` · **R:R {ETH_SHORT_RR}**
仓位 {ETH_SHORT_SIZE}NAV · {LEV}x{ETH_SHORT_NOTE}

🟢 **做多** `${ETH_LONG_LOW} ~ ${ETH_LONG_HIGH}` 🔥优先
止损 `${ETH_LONG_SL}` · TP `${ETH_LONG_TP}` · **R:R {ETH_LONG_RR}**
仓位 {ETH_LONG_SIZE}NAV · {LEV}x

━━━━━━━━━━━━━━━━━━━━━━
**今日最优：{BEST_OPPORTUNITY}**
止损触及立即平，不抗单。
