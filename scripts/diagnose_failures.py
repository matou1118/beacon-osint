"""详细诊断28个失败源的具体错误。"""
import sqlite3
import json

conn = sqlite3.connect('data/beacon.db')
c = conn.cursor()

c.execute("""
    SELECT source_id, consecutive_failures, last_error, status, last_success_at
    FROM source_sync_state
    WHERE status='error' OR consecutive_failures > 0
    ORDER BY consecutive_failures DESC
""")

rows = c.fetchall()
print(f"失败源总数: {len(rows)}\n")

# 分类错误
error_categories = {}
for row in rows:
    source_id = row[0]
    failures = row[1]
    error = row[2] or ""
    status = row[3]

    # 解析错误类型
    try:
        error_data = json.loads(error)
        if isinstance(error_data, list) and error_data:
            err_msg = error_data[0].get("error", "")
        else:
            err_msg = str(error_data)
    except:
        err_msg = error

    # 分类
    if "robots.txt" in err_msg or "robots" in err_msg:
        cat = "robots.txt 拦截"
    elif "archive" in err_msg.lower() or "list page" in err_msg.lower():
        cat = "列表页解析失败"
    elif "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
        cat = "超时"
    elif "connection" in err_msg.lower() or "connect" in err_msg.lower():
        cat = "连接失败"
    elif "404" in err_msg or "403" in err_msg or "502" in err_msg:
        cat = "HTTP错误"
    elif "encoding" in err_msg.lower():
        cat = "编码错误"
    else:
        cat = "其他"

    error_categories[cat] = error_categories.get(cat, 0) + 1
    print(f"  {source_id} (失败{failures}次): {cat}")
    print(f"    错误: {err_msg[:120]}")
    print()

print("\n错误分类统计:")
for cat, count in sorted(error_categories.items(), key=lambda x: -x[1]):
    print(f"  {cat}: {count} 个源")

conn.close()
