# 源代码目录

本目录包含项目的所有Python源代码文件。

## 文件说明

- **goMessage.py** - 群消息主程序，负责从飞书群聊提取消息、解析链接并写入多维表格
- **goAIPM.py** - 周报处理主程序，从飞书 wiki 周报文档提取 URL → 解析 → 日报交叉检查 → 写入多维表格
- **goWTA.py** - WaytoAGI 知识库处理主程序，从知识库文档提取 URL → 两层解析 → 父记录关联 → 写入多维表格
- **auth.py** - 授权脚本，用于获取飞书API的访问令牌
- **feishu_client.py** - 飞书API客户端，封装了所有与飞书API的交互
- **url_parser.py** - URL解析器，负责从各种网站提取文章信息
- **recall_messages.py** - 消息撤回脚本，用于撤回群聊中的特定消息

## 使用方法

所有脚本都应该从项目根目录运行：

```bash
# 从项目根目录运行
python src/goMessage.py
python src/auth.py
python src/archive/recall_messages.py --list
```

## 开发说明

- 所有源代码文件都使用UTF-8编码
- 遵循PEP 8代码风格规范
- 添加新功能时请更新相应的文档
