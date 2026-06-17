# 测试脚本目录

本目录包含项目的所有测试脚本。

## 文件说明

- **test_parser.py** - URL识别测试，验证各种网站URL的识别规则
- **test_toutiao.py** - 头条文章解析测试，验证头条链接的标题和日期提取
- **test_all_improvements.py** - 综合测试，验证所有改进功能
- **test_reorder.py** - 多维表格物理排序测试，覆盖家族树、排序、父记录回填、重复链接和分批执行

## 运行测试

从项目根目录运行测试：

```bash
# 测试URL识别
python tests/test_parser.py

# 测试头条解析
python tests/test_toutiao.py

# 运行所有测试
python tests/test_all_improvements.py

# 运行多维表格物理排序测试
python -m unittest tests/test_reorder.py

# 排序工具静态编译检查
python -m py_compile src/reorderMain.py src/reorderSorter.py src/reorderTreeBuilder.py src/feishu_client.py tests/test_reorder.py
```

## 添加新测试

添加新测试时：
1. 创建新的测试文件，命名为 `test_*.py`
2. 使用清晰的测试用例和断言
3. 在本README中添加说明
