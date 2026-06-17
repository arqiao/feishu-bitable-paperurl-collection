#!/usr/bin/env python3
"""
使用 pandas 删除 Excel 文件中的空行
用于清理 cfg/WeChatGongZhongHao.xlsx 文件中的空行
"""

import pandas as pd
import numpy as np
import os

def remove_empty_rows_pandas(excel_file_path):
    """
    使用 pandas 从 Excel 文件中删除所有空行
    
    Args:
        excel_file_path (str): Excel 文件路径
    """
    print(f"正在处理文件: {excel_file_path}")
    
    # 检查文件是否存在
    if not os.path.exists(excel_file_path):
        print(f"错误: 文件不存在 - {excel_file_path}")
        return False
    
    try:
        # 读取 Excel 文件的所有工作表
        print("正在读取 Excel 文件...")
        excel_file = pd.ExcelFile(excel_file_path)
        sheet_names = excel_file.sheet_names
        
        print(f"找到 {len(sheet_names)} 个工作表: {sheet_names}")
        
        # 创建一个 ExcelWriter 对象来保存修改后的文件
        with pd.ExcelWriter(excel_file_path, engine='openpyxl', mode='w') as writer:
            
            for sheet_name in sheet_names:
                print(f"\n处理工作表: {sheet_name}")
                
                # 读取工作表
                df = pd.read_excel(excel_file_path, sheet_name=sheet_name)
                
                print(f"原始数据形状: {df.shape}")
                print(f"原始行数: {len(df)}")
                
                if len(df) == 0:
                    print("工作表为空，跳过处理")
                    # 保存空工作表
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
                    continue
                
                # 识别空行（所有列都为 NaN 或空字符串）
                # 首先将所有 NaN 转换为空字符串以便比较
                df_filled = df.fillna('')
                
                # 检查每一行是否所有列都为空字符串
                empty_mask = (df_filled.astype(str).apply(lambda x: x.str.strip()) == '').all(axis=1)
                
                empty_row_count = empty_mask.sum()
                print(f"发现空行数: {empty_row_count}")
                
                if empty_row_count > 0:
                    # 删除空行
                    df_cleaned = df[~empty_mask].reset_index(drop=True)
                    print(f"清理后行数: {len(df_cleaned)}")
                    print(f"成功删除 {empty_row_count} 个空行")
                else:
                    df_cleaned = df
                    print("未发现空行，无需删除")
                
                # 保存清理后的工作表
                df_cleaned.to_excel(writer, sheet_name=sheet_name, index=False)
        
        print(f"\n文件已成功保存: {excel_file_path}")
        return True
        
    except Exception as e:
        print(f"处理文件时出错: {str(e)}")
        import traceback
        print(f"详细错误信息: {traceback.format_exc()}")
        return False

if __name__ == "__main__":
    # Excel 文件路径
    excel_file = os.path.join("d:\\workspace\\clawbots\\feishuMSG-xls", "cfg", "WeChatGongZhongHao.xlsx")
    
    # 执行空行删除
    success = remove_empty_rows_pandas(excel_file)
    
    if success:
        print("\n空行删除完成！")
    else:
        print("\n处理失败！")