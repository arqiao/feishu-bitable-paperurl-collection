"""从飞书电子表格导入已有数据到本地 CSV"""

import csv
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from feishu_client import FeishuClient

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_FILE = os.path.join(PROJECT_ROOT, 'data', 'message_log.csv')
CSV_HEADERS = [
    '标题', '日期', '星期', '链接', '来源',
    '标记', '是否重复', '消息序号', '摘录异常信息', '备注',
]


def append_rows_to_csv(rows: list):
    """追加数据行到 CSV 文件，不存在则自动创建含表头的文件（UTF-8 BOM）"""
    file_exists = os.path.exists(CSV_FILE)
    with open(CSV_FILE, 'a', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(CSV_HEADERS)
        writer.writerows(rows)


def main():
    client = FeishuClient()

    # 检查 token
    if not client.check_token_valid():
        print("Token 已过期，尝试刷新...")
        if not client.refresh_access_token():
            print("✗ Token 刷新失败，请运行 python src/modules/feishu_auth.py")
            return
    print("✓ Token 验证成功")

    # 获取表格配置
    spreadsheet_token = client.config['target_document']['token']
    sheet_id = client.config['target_document'].get('sheet_id', '')
    if not sheet_id:
        sheets = client.get_spreadsheet_sheets(spreadsheet_token)
        if not sheets:
            print("✗ 无法获取工作表信息")
            return
        sheet_id = sheets[0]['sheet_id']

    # 读取飞书表头
    header_values = client.get_spreadsheet_values(spreadsheet_token, sheet_id, "A1:Z1")
    if not header_values or not header_values[0]:
        print("✗ 无法读取表头")
        return
    feishu_headers = header_values[0]
    print(f"✓ 飞书表头: {feishu_headers}")

    # 构建列映射: config key -> 飞书列索引
    column_config = client.config['table_columns']
    column_mapping = {}
    for idx, header in enumerate(feishu_headers):
        for key, value in column_config.items():
            if header == value:
                column_mapping[key] = idx
                break
    print(f"✓ 列映射: {column_mapping}")

    # 读取全部数据行（A2:Z5000）
    print("\n正在读取飞书表格数据...")
    col_count = len(feishu_headers)
    end_col = chr(ord('A') + col_count - 1)
    range_str = f"A2:{end_col}5000"
    rows = client.get_spreadsheet_values(spreadsheet_token, sheet_id, range_str)
    if not rows:
        print("✗ 表格中没有数据")
        return
    print(f"✓ 读取到 {len(rows)} 行数据")

    # 转换为 CSV 行（按 CSV_HEADERS 固定顺序）
    csv_rows = []
    for row in rows:
        # 补齐列数
        while len(row) < col_count:
            row.append('')
        csv_row = build_csv_row(row, column_mapping)
        csv_rows.append(csv_row)

    # 检查是否已有 CSV 文件
    if os.path.exists(CSV_FILE):
        print(f"\n⚠ CSV 文件已存在: {CSV_FILE}")
        confirm = input("是否覆盖？(y/n): ").lower()
        if confirm != 'y':
            print("已取消")
            return
        os.remove(CSV_FILE)

    # 写入 CSV
    append_rows_to_csv(csv_rows)
    print(f"\n✓ 已导入 {len(csv_rows)} 行数据到 {CSV_FILE}")


def build_csv_row(row, column_mapping):
    """将飞书行数据按 CSV_HEADERS 顺序转换"""

    def get_cell(key):
        if key not in column_mapping:
            return ''
        idx = column_mapping[key]
        cell = row[idx] if idx < len(row) else ''
        if cell is None:
            return ''
        # 处理超链接单元格
        if isinstance(cell, list):
            for item in cell:
                if isinstance(item, dict) and item.get('link'):
                    return item['link'].strip()
            return ''
        return str(cell).strip()

    return [
        get_cell('title'),          # 文章标题
        get_cell('publish_date'),   # 文章发表日期
        get_cell('weekday'),        # 星期
        get_cell('url'),            # 文章链接URL
        get_cell('source'),         # 文章来源
        get_cell('mark'),           # 标记
        get_cell('is_duplicate'),   # 是否重复
        get_cell('message_index'),  # 消息序号
        get_cell('error_info'),     # 摘录异常信息
        get_cell('remark'),         # 备注
    ]


if __name__ == "__main__":
    main()
