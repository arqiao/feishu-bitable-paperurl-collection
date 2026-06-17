# 快速开始指南

## 第一步：安装依赖

```bash
pip install -r requirements.txt
```

## 第二步：配置飞书应用

### 1. 在飞书开放平台添加权限

访问 https://open.feishu.cn/，进入你的应用，添加以下权限：

**用户身份权限：**
- ✅ `im:chat:readonly`
- ✅ `im:message:readonly`
- ✅ `im:message`
- ✅ `bitable:app`
- ✅ `bitable:app:readonly`

**应用身份权限：**
- ✅ `im:message`
- ✅ `im:message.group_msg`

**如需使用 goAIPM.py / goWTA.py（飞书文档解析）：**
- ✅ `wiki:wiki:readonly`（读取 wiki 文档）
- ✅ `drive:drive.metadata:readonly`（获取文档元信息）

### 2. 配置重定向 URL

在 **安全设置** → **重定向 URL** 中添加：

```
http://localhost:8080/callback
```

### 3. 将机器人添加到群聊

在飞书中将你的应用机器人添加到目标群聊中。

## 第三步：填写配置文件

打开 `cfg/config.yaml`，填写飞书应用和业务配置。敏感凭证由本机密钥配置加载，不要提交到仓库。

```yaml
target_chat:
  name: "from_微信WeChat"

target_bitable:
  app_token: "..."
  table_id: "..."
```

## 第四步：运行授权

```bash
python src/auth.py
```

浏览器会自动打开授权页面，完成授权后返回终端。

## 第五步：运行主程序

### 默认模式（只处理新消息）
```bash
python src/goMessage.py
```

### 处理所有历史消息（测试用）
```bash
python src/goMessage.py --all
```

### 重置并重新开始
```bash
python src/goMessage.py --reset
```

### 处理指定范围的消息
```bash
python src/goMessage.py --start 3 --end 10
```

程序会自动：
1. 查找目标群聊
2. 读取消息并分配序号
3. 显示未含链接的消息清单
4. 提取链接
5. 解析文章信息
6. 写入本地 CSV 文件
7. 非重复数据写入飞书多维表格
8. 自动撤回已处理的群聊消息

## 后续使用

每次运行 `python src/goMessage.py` 只会处理新消息，不会重复处理。

## 其他程序

### 周报处理（goAIPM.py）

从飞书 wiki 周报文档提取 URL，解析文章信息，与日报交叉检查后写入多维表格。

```bash
python src/goAIPM.py                 # 处理 input/list_周报.txt 中的文档
```

### WaytoAGI 知识库处理（goWTA.py）

从 WaytoAGI 飞书知识库文档中按日期范围提取 URL，两层解析后写入多维表格。

```bash
python src/goWTA.py --his 20260301 20260307  # 历史批量：处理指定日期范围
python src/goWTA.py --update                  # 增量更新：处理上次之后的新内容
```

### 自动分类（autoClassify.py）

利用 LLM（DeepSeek）对多维表格中的文章记录自动分类打标签。从参考表学习已有分类模式，对目标表中未分类记录批量处理。

```bash
python src/autoClassify.py                      # 对目标表中未分类记录自动打标签
python src/autoClassify.py --dry-run             # 试运行，仅预览分类结果不写入
python src/autoClassify.py --batch-size 20       # 指定每批处理数量
```

**配置说明**：在 `cfg/config.yaml` 的 `auto_classify` 段配置 LLM 参数、参考表和目标表。

### 多维表格物理排序（reorderMain.py）

按 `cfg/config.yaml` 的 `reorderBitable` 段预览或执行目标表记录的物理重排。默认只预览，不写表。

```bash
python src/reorderMain.py                                # 预览排序结果
python src/reorderMain.py --show-records                 # 预览并显示范围内明细
python src/reorderMain.py --execute                      # 执行排序
python src/reorderMain.py --execute --max-temp-records 1500  # 限制每批临时新增数
```

工具会按完整家族树分批执行“批量创建 → 批量更新父记录 → 批量删除旧记录”，并自动避开飞书单表 20000 条记录上限。详细说明见 `docs/guides/REORDER_BITABLE_GUIDE.md`。

## 撤回消息功能

主程序处理完成后会自动撤回已处理的群聊消息，无需手动操作。

如果需要手动撤回群聊中的特定消息，可以使用撤回消息脚本。

### 快速使用

```bash
# 1. 列出所有消息及其序号
python src/archive/recall_messages.py --list

# 2. 试运行，查看将要撤回的消息
python src/archive/recall_messages.py --indices 1,3,5 --dry-run

# 3. 实际撤回
python src/archive/recall_messages.py --indices 1,3,5

# 4. 逐条确认撤回（推荐）
python src/archive/recall_messages.py --indices 1,3,5 --confirm-each
```

### 支持的序号格式

- 单个序号：`--indices 1`
- 多个序号：`--indices 1,3,5`
- 范围：`--indices 1-5`
- 混合：`--indices 1,3-5,7,10-15`

### 安全特性

- ✅ **倒序撤回**：自动从编号最大的消息开始撤回，避免序号混乱
- ✅ **逐条确认**：使用 `--confirm-each` 可以在撤回每条消息前单独确认（y=撤回, n=跳过, q=退出）
- ✅ **试运行模式**：使用 `--dry-run` 可以先查看将要撤回的消息

### 权限要求

⚠️ **重要：** 撤回消息需要特殊权限：
- 授权用户必须是目标群聊的**管理员**或**群主**
- 或者应用机器人必须是群管理员

详细说明请查看：`docs/guides/RECALL_MESSAGES_GUIDE.md`

## 命令行参数说明

### 主程序 (goMessage.py)

| 参数 | 说明 | 使用场景 |
|------|------|----------|
| 无参数 | 只处理新消息，更新记录 | 日常使用 |
| `--all` | 处理所有消息，更新记录 | 全量扫描 |
| `--reset` | 重置记录，处理所有消息 | 重新开始 |
| `--start N` | 从第 N 条链接开始处理 | 指定范围 |
| `--end N` | 处理到第 N 条链接 | 指定范围 |
| `--list-nolink` | 显示未含链接的消息清单 | 查看无链接消息 |
| `--help` | 显示帮助信息 | 查看用法 |

**范围参数示例：**
- `python src/goMessage.py --start 3 --end 10` - 只处理第 3 到第 10 条链接
- `python src/goMessage.py --start 5` - 从第 5 条开始处理到最后
- `python src/goMessage.py --end 20` - 只处理前 20 条链接

**显示无链接消息：**
- `python src/goMessage.py --list-nolink` - 显示未含链接的消息详细清单
- 此模式下只列出消息，不进行后续的链接解析和存储工作
- 会显示消息解析错误信息（如果有）
- 默认情况下只显示统计数量，不显示详细内容

### 撤回消息脚本 (recall_messages.py)

| 参数 | 说明 | 使用场景 |
|------|------|----------|
| `--list` | 列出所有消息及其序号 | 查看消息 |
| `--indices` | 指定要撤回的消息序号 | 撤回消息 |
| `--dry-run` | 试运行模式，不实际撤回 | 预览撤回 |
| `--confirm-each` | 逐条确认模式 | 谨慎撤回 |

**撤回示例：**
- `python src/archive/recall_messages.py --list` - 查看所有消息
- `python src/archive/recall_messages.py --indices 1,3,5 --dry-run` - 预览撤回
- `python src/archive/recall_messages.py --indices 1-10 --confirm-each` - 逐条确认撤回

**注意：** 撤回消息功能始终获取所有历史消息，不受主程序的处理时间限制。

## 定时运行（可选）

### Windows 任务计划程序

1. 打开"任务计划程序"
2. 创建基本任务
3. 设置触发器（如每小时）
4. 操作：启动程序 `python`
5. 参数：`D:\path\to\src\goMessage.py`
6. 起始于：`D:\path\to\project`

### Linux/Mac crontab

```bash
# 编辑 crontab
crontab -e

# 添加定时任务（每小时运行）
0 * * * * cd /path/to/project && python src/goMessage.py >> /path/to/log.txt 2>&1
```

## 常见问题

### Token 过期

重新运行授权：
```bash
python src/auth.py
```

### 找不到群聊

检查 `cfg/config.yaml` 中的群聊名称是否与飞书中完全一致。

### 权限不足

确保在飞书开放平台添加了所有必需的权限，并重新授权。

## 需要帮助？

查看完整文档：`README.md`
