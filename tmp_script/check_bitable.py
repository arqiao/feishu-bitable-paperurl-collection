"""临时脚本：检查多维表格日期字段的类型和已有数据格式"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))
from feishu_client import FeishuClient

client = FeishuClient()
cfg = client.config.get('target_bitable', {})
app_token = cfg.get('app_token', '')
table_id = cfg.get('table_id', '')
col_cfg = client.config.get('bitable_columns', {})
date_field = col_cfg.get('publish_date', '日期')

# 1. 查看字段定义
print("=== 字段定义 ===")
fields = client.get_bitable_fields(app_token, table_id)
for f in fields:
    print(f"  {f.get('field_name')}: type={f.get('type')}, ui_type={f.get('ui_type')}")

# 2. 读取前5条和最后5条记录的日期值
print(f"\n=== 记录中的 [{date_field}] 字段值 ===")
records = client.get_bitable_records(app_token, table_id, [date_field])
print(f"共 {len(records)} 条记录")
print("\n前5条:")
for r in records[:5]:
    val = r.get(date_field, '(空)')
    print(f"  {repr(val)}")
print("\n后5条:")
for r in records[-5:]:
    val = r.get(date_field, '(空)')
    print(f"  {repr(val)}")
