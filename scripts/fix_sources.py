"""批量修复失败源配置：
1. 为 6 个 playwright_fallback=False 的源开启 fallback
2. 为所有失败源加 crawl4ai 兜底
3. 重置失败计数器
"""
import yaml
import sqlite3
from pathlib import Path

# 读取配置
with open('config/sources.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

sources = config.get('sources', [])

# 需要开启 playwright_fallback 的源
need_pw_fallback = [
    'mofcom_gov', 'sc_samr_gov', 'sc_ndrc_gov',
    'sc_mohrss_gov', 'sc_mohurd_gov', 'sc_energy_gov'
]

# 所有失败源（需要 crawl4ai 兜底）
all_failed = [
    'mofcom_gov', 'sc_samr_gov', 'sc_ndrc_gov', 'sc_shanxi_gov', 'sc_mohurd_gov',
    'sc_energy_gov', 'sc_he_gov', 'sc_ha_gov', 'cd_jinjiang_gov', 'sc_zj_gov',
    'sc_fj_gov', 'sc_hi_gov', 'sc_yn_gov', 'sc_samr_gov', 'sc_qh_gov', 'hk_gov',
    'gd_swt', 'sn_fgw', 'sc_ny', 'sc_nx_gov', 'sc_xj_gov', 'tj_gov', 'ha_fgw',
    'hb_kjt', 'ah_fgw', 'ah_kjt', 'he_fgw', 'sc_fgw', 'sc_mohrss_gov'
]

changed = 0
for s in sources:
    sid = s.get('id', '')

    # 1. 开启 playwright_fallback
    if sid in need_pw_fallback:
        if not s.get('playwright_fallback', False):
            s['playwright_fallback'] = True
            print(f"  {sid}: playwright_fallback -> True")
            changed += 1

    # 2. 为失败源加 crawl4ai 兜底（如果没有 render_mode）
    if sid in all_failed:
        rm = s.get('render_mode', '')
        if rm in ('', 'default', 'static'):
            s['render_mode'] = 'static_then_crawl4ai'
            print(f"  {sid}: render_mode -> static_then_crawl4ai")
            changed += 1

# 写回配置
with open('config/sources.yaml', 'w', encoding='utf-8') as f:
    yaml.safe_dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

print(f"\n配置修改: {changed} 处")

# 3. 重置失败计数器
conn = sqlite3.connect('data/beacon.db')
c = conn.cursor()
c.execute("UPDATE source_sync_state SET consecutive_failures=0, status='ok' WHERE status='error' OR consecutive_failures > 0")
reset_count = c.rowcount
conn.commit()
print(f"重置失败计数器: {reset_count} 个源")
conn.close()
