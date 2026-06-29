# CLI 参数审计矩阵

本文档记录五个核心脚本全部命令行入口的运行契约、副作用、状态推进规则和验证结果。修改这些入口时，应同步更新本矩阵。

## 验证标记

- `实际`：已连接真实服务运行。
- `模拟`：使用单元测试或只读预检验证，不触发高副作用。
- `边界`：使用真实子进程验证参数校验，但在客户端初始化前退出。

## 参数矩阵

| 脚本 | 参数或模式 | 主要副作用 | 状态规则 | 验证 | 2026-06-29 结果 |
| --- | --- | --- | --- | --- | --- |
| `dfZSXQ.py` | `--his START END` | 下载历史附件到本地目录 | 不读取 marker 作为下界；不更新 `last_download_date` | 模拟 | 日期校验、历史下载和状态不推进测试通过 |
| `dfZSXQ.py` | `--update` | 下载新附件；成功后写配置 | 仅完整抓取且下载无失败时推进 | 实际 | 2 个群组均正常无新文件；汇总成功 |
| `goWTA.py` | `--his START END` | 解析并写入多维表格、缓存和日志 | 不更新 `last_processed_date` | 模拟 | 历史状态隔离测试通过 |
| `goWTA.py` | `--update` | 解析并写入多维表格、缓存和日志 | 写入完整且发现更晚日期才推进 | 实际 | 12 个入口均已去重；明确报告没有更晚日期 |
| `goWXGZH.py` | `--his START END` | 抓取、解析并写入多维表格 | 不更新账号 `last_update` 和成功状态缓存 | 模拟 | 历史状态隔离测试通过 |
| `goWXGZH.py` | `--update` | 抓取、解析、写表、缓存、清单和错误日志 | 每个账号仅在列表完整且写入成功时推进 | 实际 | 抓取/写入 2 条；1 个账号访问失败未推进 |
| `goWXGZH.py` | `--searchbiz KEYWORD` | 只读微信后台搜索 | 无状态更新 | 实际 | 找到 5 个公众号；后台 Token 未回显 |
| `goWXGZH.py` | `--repair-last-update` | 读取本地/多维表格依据并改写公众号清单 | 仅有可信修复依据时写清单 | 模拟 | 单路径输出；重复乱码死代码已删除 |
| `goWXGZH.py` | `--list FILE` | 改变账号清单来源 | 本身不改变状态语义 | 模拟 | 数据/修复模式允许；搜索模式拒绝 |
| `goWXGZH.py` | `--refresh-cache` | 从多维表格重建本地去重缓存 | 仅适用于历史或增量数据模式 | 模拟 | 无效组合在客户端初始化前拒绝 |
| `goAIPM.py` | `--file URL` | 解析单篇周报并写入多维表格、缓存和日志 | 无增量 marker | 模拟 | 模式、输入和汇总契约测试通过 |
| `goAIPM.py` | `--list FILE` | 批量解析周报并写入多维表格 | 无增量 marker | 边界 | 缺失列表文件在 Token 检查前拒绝 |
| `goAIPM.py` | `--daily URL` | 解析单篇日报并写入多维表格 | 无自动增量推进 | 模拟 | 最终成功/失败结论测试通过 |
| `goAIPM.py` | `--update` | 查找并处理新日报 | 仅成功日报推进 `last_processed_date` | 实际 | 未发现新日报；状态未更新原因清晰 |
| `goAIPM.py` | `--weekly URL` | 更新已有记录并新增周报记录 | 无增量 marker | 模拟 | 返回并显示最终成功/失败 |
| `goAIPM.py` | `--towiki SRC DST` | 清空并重写目标飞书文档 | 无增量 marker；失败提示可能部分写入 | 实际 | 网页 615 blocks、PDF 900 blocks 完整写入 |
| `goMessage.py` | 默认增量 | 写 CSV/日志/多维表格，撤回消息 | 完整写入后推进；消息列表访问失败时结果作废且不推进 | 实际 | 2 条解析、写入、撤回成功；随后正常空结果验证通过 |
| `goMessage.py` | `--profile NAME` | 选择群聊、多维表格和独立状态 | 只更新所选 profile | 实际 | `ai` profile 显示清晰 |
| `goMessage.py` | `--all` | 全量拉取、重建索引并正常处理/撤回 | 开始前不改状态；成功后写最终状态 | 模拟 | 状态起点和合法组合测试通过 |
| `goMessage.py` | `--reset` | 与全量模式相同，但语义为重设 checkpoint | 不提前写 0；成功完成后覆盖最终状态 | 模拟 | 延迟 reset 测试通过 |
| `goMessage.py` | `--start N` / `--end N` | 只处理提取后链接列表的部分范围 | 部分运行不推进状态 | 模拟 | 正数、顺序和状态隔离测试通过 |
| `goMessage.py` | `--list-nolink` | 只读列出无链接消息 | 不解析、不写入、不撤回、不推进状态 | 模拟 | 与 reset/range 冲突；可与 `--all` 联用 |

## 公共校验要求

- 历史日期必须是真实的 `YYYYMMDD` 日期，且开始日期不晚于结束日期。
- `goWTA.py` 继续兼容既有 `YYMMDD` 简写，并在校验时规范为 `YYYYMMDD`。
- 参数冲突、文件缺失和范围错误必须在客户端初始化前退出。
- 终端不得打印 Token、Cookie、Secret 等完整凭证值。
- 历史、只读和部分运行必须明确说明增量状态未更新。
- 外部列表访问失败必须与正常空列表使用不同返回值和终端结论。

## 已知外部问题

- 微信公众号“白鲸出海”当前返回 `ret=200002, errmsg=invalid args`。程序已将其归类为列表抓取失败，不更新该账号 `last_update`。
- `src/tools_excel/remove_empty_rows.py` 有一个既有的 Python `SyntaxWarning`，与本轮五个 CLI 脚本无关。

## 回归命令

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_*.py'
```
