"""查源配置和管线结构。"""
import yaml

with open('config/sources.yaml', 'r', encoding='utf-8') as f:
    d = yaml.safe_load(f)

s = d.get('sources', [])
print(f"总源数: {len(s)}")

# 调度层级统计
tiers = {}
for x in s:
    t = x.get('cadence_tier', 'unknown')
    tiers[t] = tiers.get(t, 0) + 1
print(f"调度层级: {tiers}")

# source_type 统计
types = {}
for x in s:
    t = x.get('source_type', 'unknown')
    types[t] = types.get(t, 0) + 1
print(f"源类型: {types}")

# 列出前 10 个源
print("\n前 10 个源:")
for x in s[:10]:
    print(f"  {x.get('source_id','')}: {x.get('name','')[:20]} | tier={x.get('cadence_tier','')} | type={x.get('source_type','')}")

# 检查是否有摘要相关配置
print("\n" + "=" * 60)
print("管线组件检查")
print("=" * 60)

from pathlib import Path
components = {
    '采集': 'collectors/base.py',
    '字段抽取': 'extractors/regex_fallback.py',
    'GLINER抽取': 'extractors/gliner_extractor.py',
    '附件下载': 'extractors/attachment_downloader.py',
    '规则引擎': 'rules/engine.py',
    '印证': 'rules/corroboration.py',
    '媒体聚类': 'rules/media_cluster.py',
    '行业聚焦': 'rules/industry_focus.py',
    '图谱': 'graph_service.py',
    'Markdown输出': 'output/markdown.py',
    '邮件推送': 'output/pusher.py',
    '调度器': 'scheduler.py',
    '管线入口': 'main.py',
}
for name, path in components.items():
    exists = Path(path).exists()
    size = Path(path).stat().st_size if exists else 0
    print(f"  {name}: {'✓' if exists else '✗'} {path} ({size} bytes)")

# 检查是否有摘要模块
print("\n摘要能力检查:")
summary_candidates = list(Path('.').rglob('*summary*')) + list(Path('.').rglob('*abstract*'))
if summary_candidates:
    for f in summary_candidates:
        print(f"  找到: {f}")
else:
    print("  ✗ 没有独立的摘要模块")

# main.py 管线流程
print("\n" + "=" * 60)
print("main.py 管线流程")
print("=" * 60)
import re
with open('main.py', 'r', encoding='utf-8') as f:
    content = f.read()
# 找关键步骤
steps = re.findall(r'(\w+_result|\w+_records)\s*=\s*(\w+\([^)]*\))', content)
for i, (var, call) in enumerate(steps):
    print(f"  {i+1}. {var} = {call}")
