# 文档目录

本目录包含项目的所有文档，按类型分为三个子目录。

## 根目录文档

- **CHANGELOG.md** - 变更日志
- **PRD_REGISTRY.md** - PRD 总集台账（所有 PRD 索引）
- **PROJECT_STRUCTURE.md** - 项目结构说明

### prd/ - 产品需求文档
- **PRD-001.md** - 飞书群消息整理工具 v1.0.0 PRD
- **PRD-002.md** - 飞书群消息整理工具 v1.1.0 PRD

## 目录结构

### guides/ - 使用指南
面向用户的使用说明文档：
- **REORDER_BITABLE_GUIDE.md** - 多维表格物理排序工具使用指南
- **RECALL_MESSAGES_GUIDE.md** - 消息撤回功能详细使用指南
- **MESSAGE_INDEX_GUIDE.md** - 消息序号功能说明
- **MESSAGE_RECALL_FAQ.md** - 消息撤回常见问题解答

### technical/ - 技术文档
面向开发者的技术说明文档：
- **ARCHITECTURE.md** - 项目架构设计说明
- **BITABLE_NOTES.md** - 飞书多维表格开发笔记
- **CSV_LOG_SPEC.md** - CSV 日志和缓存文件规范
- **TOKEN_MANAGEMENT.md** - Token管理机制说明
- **FEISHU_ACCESS_NOTE.md** - 飞书Wiki访问限制说明
- **FEISHU_MESSAGE_DELETE_NOTE.md** - 飞书消息管理机制详解（撤回vs删除）
- **WEIBO_PARSING_ISSUE.md** - 微博解析问题分析
- **URL_TRUNCATION_AND_PARSING_IMPROVEMENTS.md** - URL处理和解析改进说明

### archive/ - 历史归档
不再活跃但有参考价值的历史文档：
- **RECALL_SAFETY_FEATURES.md** - 删除功能安全特性说明
- **DOCUMENTATION_UPDATE.md** - 文档更新记录
- **IMPROVEMENTS_SUMMARY.md** - 功能改进总结
- **FINAL_IMPROVEMENTS_SUMMARY.md** - 最终改进总结
- **TOUTIAO_ARTICLE_SUMMARY.md** - 头条文章解析总结

## 文档维护

- 添加新功能时，请在相应目录添加文档
- 过时的文档移至 archive/ 目录
- 保持文档与代码同步更新
