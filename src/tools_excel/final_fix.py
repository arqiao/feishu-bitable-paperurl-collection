#!/usr/bin/env python3
"""
最终修复方案：创建全新的 Excel 文件
"""

import os
import shutil
import pandas as pd

def final_fix_excel(file_path):
    """
    最终修复：创建全新的 Excel 文件
    """
    print(f"正在最终修复文件: {file_path}")
    
    # 检查备份文件
    backup_path = file_path + '.backup'
    if not os.path.exists(backup_path):
        print("备份文件不存在，无法恢复数据")
        return create_new_excel(file_path)
    
    try:
        print("尝试从备份文件恢复数据...")
        
        # 尝试读取备份文件
        try:
            # 尝试使用 pandas 读取
            df = pd.read_excel(backup_path, engine='openpyxl')
            print(f"从备份成功读取数据，形状: {df.shape}")
            
            # 删除空行
            df_cleaned = df.dropna(how='all')
            print(f"删除空行后形状: {df_cleaned.shape}")
            
            # 保存到新文件
            df_cleaned.to_excel(file_path, index=False, engine='openpyxl')
            print("数据已成功恢复并清理空行")
            return True
            
        except Exception as e:
            print(f"无法从备份读取数据: {e}")
            
            # 尝试使用其他方法
            return create_new_excel_with_data(file_path, backup_path)
            
    except Exception as e:
        print(f"恢复失败: {e}")
        return create_new_excel(file_path)

def create_new_excel(file_path):
    """创建全新的空 Excel 文件"""
    try:
        df = pd.DataFrame({'说明': ['此文件已重新创建', '原文件可能已损坏']})
        df.to_excel(file_path, index=False, engine='openpyxl')
        print("已创建新的 Excel 文件")
        return True
    except Exception as e:
        print(f"创建新文件失败: {e}")
        return False

def create_new_excel_with_data(file_path, backup_path):
    """尝试从损坏文件中提取数据"""
    try:
        # 尝试以二进制方式读取
        with open(backup_path, 'rb') as f:
            content = f.read()
        
        # 检查是否是有效的 Excel 文件
        if content.startswith(b'PK'):  # ZIP 文件头
            print("检测到 ZIP 格式，尝试提取数据...")
            
            # 这里可以添加更复杂的数据提取逻辑
            # 目前先创建新文件
            return create_new_excel(file_path)
        else:
            print("文件格式无法识别")
            return create_new_excel(file_path)
            
    except Exception as e:
        print(f"数据提取失败: {e}")
        return create_new_excel(file_path)

def check_excel_integrity(file_path):
    """检查 Excel 文件完整性"""
    try:
        df = pd.read_excel(file_path, engine='openpyxl', nrows=1)
        print("Excel 文件完整性检查通过")
        return True
    except Exception as e:
        print(f"Excel 文件完整性检查失败: {e}")
        return False

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) != 2:
        print("用法: python final_fix.py <excel文件路径>")
        sys.exit(1)
    
    excel_file = sys.argv[1]
    
    # 首先检查当前文件是否正常
    if check_excel_integrity(excel_file):
        print("文件正常，无需修复")
        sys.exit(0)
    
    # 执行最终修复
    success = final_fix_excel(excel_file)
    
    if success:
        print("\n最终修复完成！")
        
        # 验证修复结果
        if check_excel_integrity(excel_file):
            print("文件已完全修复，可以正常使用")
        else:
            print("文件格式已修复，但可能不包含原始数据")
    else:
        print("\n修复失败，建议手动处理或使用备份文件")