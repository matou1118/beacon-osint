"""查失败源的配置和分页模式。"""
import yaml

with open('config/sources.yaml', 'r', encoding='utf-8') as f:
    d = yaml.safe_load(f)

sources = d.get('sources', [])

# 13个分页404的源
pagination_404 = [
    'sc_mohrss_gov', 'sc_ndrc_gov', 'sc_shanxi_gov', 'sc_mohurd_gov',
    'sc_energy_gov', 'sc_he_gov', 'mofcom_gov', 'sc_zj_gov',
    'sc_samr_gov', 'sc_ny', 'sc_xj_gov', 'ah_fgw', 'ah_kjt'
]

# 12个Playwright失败的源
pw_failed = [
    'sc_ha_gov', 'sc_hi_gov', 'sc_yn_gov', 'hk_gov', 'gd_swt',
    'sn_fgw', 'sc_nx_gov', 'tj_gov', 'ha_fgw', 'hb_kjt',
    'he_fgw', 'sc_fgw'
]

print("=" * 60)
print("分页404的源配置:")
print("=" * 60)
for s in sources:
    sid = s.get('id', '')
    if sid in pagination_404:
        print(f"\n  {sid}:")
        print(f"    url: {s.get('url', '')}")
        print(f"    access_method: {s.get('access_method', '')}")
        print(f"    render_mode: {s.get('render_mode', 'default')}")
        print(f"    playwright_fallback: {s.get('playwright_fallback', False)}")
        print(f"    archive_pages: {s.get('archive_pages', 'default')}")
        print(f"    max_items: {s.get('max_items', 'default')}")

print("\n" + "=" * 60)
print("Playwright失败的源配置:")
print("=" * 60)
for s in sources:
    sid = s.get('id', '')
    if sid in pw_failed:
        print(f"\n  {sid}:")
        print(f"    url: {s.get('url', '')}")
        print(f"    render_mode: {s.get('render_mode', 'default')}")
        print(f"    playwright_fallback: {s.get('playwright_fallback', False)}")
        print(f"    archive_pages: {s.get('archive_pages', 'default')}")
