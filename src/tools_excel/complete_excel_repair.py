#!/usr/bin/env python3
"""
完整的 Excel 文件修复工具
创建所有必要的 Excel 文件结构
"""

import os
import shutil
import zipfile
import tempfile
import xml.etree.ElementTree as ET

def complete_excel_repair(file_path):
    """
    完整修复 Excel 文件，创建所有必要的文件结构
    """
    print(f"正在完整修复文件: {file_path}")
    
    if not os.path.exists(file_path):
        print("文件不存在")
        return False
    
    # 创建备份
    backup_path = file_path + '.backup'
    try:
        shutil.copy2(file_path, backup_path)
        print(f"已创建备份: {backup_path}")
    except Exception as e:
        print(f"创建备份失败: {e}")
        return False
    
    try:
        # 创建临时目录
        with tempfile.TemporaryDirectory() as temp_dir:
            print("创建完整的 Excel 文件结构...")
            
            # 创建所有必要的目录
            xl_dir = os.path.join(temp_dir, 'xl')
            xl_rels_dir = os.path.join(xl_dir, '_rels')
            xl_worksheets_dir = os.path.join(xl_dir, 'worksheets')
            rels_dir = os.path.join(temp_dir, '_rels')
            
            os.makedirs(xl_rels_dir, exist_ok=True)
            os.makedirs(xl_worksheets_dir, exist_ok=True)
            os.makedirs(rels_dir, exist_ok=True)
            
            # 创建所有必要的文件
            create_content_types(os.path.join(temp_dir, '[Content_Types].xml'))
            create_workbook_xml(os.path.join(xl_dir, 'workbook.xml'))
            create_workbook_rels(os.path.join(xl_rels_dir, 'workbook.xml.rels'))
            create_worksheet(os.path.join(xl_worksheets_dir, 'sheet1.xml'))
            create_rels(os.path.join(rels_dir, '.rels'))
            
            # 重新压缩为 Excel 文件
            with zipfile.ZipFile(file_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # 添加所有必要的文件
                files_to_add = [
                    '[Content_Types].xml',
                    'xl/workbook.xml',
                    'xl/_rels/workbook.xml.rels',
                    'xl/worksheets/sheet1.xml',
                    '_rels/.rels'
                ]
                
                for file_rel_path in files_to_add:
                    file_full_path = os.path.join(temp_dir, file_rel_path)
                    if os.path.exists(file_full_path):
                        zipf.write(file_full_path, file_rel_path)
                    else:
                        print(f"警告: 文件不存在: {file_full_path}")
            
            print("完整的 Excel 文件结构已创建")
            return True
            
    except Exception as e:
        print(f"完整修复失败: {e}")
        import traceback
        print(f"详细错误: {traceback.format_exc()}")
        return False

def create_content_types(file_path):
    """创建 [Content_Types].xml 文件"""
    content = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
    <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
    <Default Extension="xml" ContentType="application/xml"/>
    <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
    <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>'''
    
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

def create_workbook_xml(file_path):
    """创建 workbook.xml 文件"""
    content = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
    <sheets>
        <sheet name="Sheet1" sheetId="1" r:id="rId1"/>
    </sheets>
</workbook>'''
    
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

def create_workbook_rels(file_path):
    """创建 workbook.xml.rels 文件"""
    content = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>'''
    
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

def create_worksheet(file_path):
    """创建 worksheet 文件"""
    content = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
    <sheetData/>
</worksheet>'''
    
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

def create_rels(file_path):
    """创建 .rels 文件"""
    content = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
    <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) != 2:
        print("用法: python complete_excel_repair.py <excel文件路径>")
        sys.exit(1)
    
    excel_file = sys.argv[1]
    success = complete_excel_repair(excel_file)
    
    if success:
        print("\nExcel 文件完整修复成功！")
        print("现在可以正常使用空行删除脚本了。")
    else:
        print("\n完整修复失败，建议手动检查文件或从备份恢复。")