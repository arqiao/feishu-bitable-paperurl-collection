











5月17日没识别是另一个原因：那条 API 里没有 article_url，正文写的是“敏感词发不出来，导出成PDF版来发了”，当前 --update 只处理星球文章直链，不处理附件 PDF。


解析小红书文档：
http://xhslink.com/o/4P09FiBepzE 在OpenClaw上成功解析了，你参考一下。
http://xhslink.com/a/N3kz8wEzZNbcb



- 下载youmind里的skills。是否需要有会员？

- “微信X失效”
- 配合OpenClaw系统调度下自动工作。

- 更新多维表格中的特定类型记录的标题、日期、来源等信息。
- 插入到多维表格中的特定位置。
- 多维表格的记录，自动标记、打标签，包括内容分类、关注度等信息。
- 多维表格的记录，自动排序。
- 进一步对文章内容进行摘要，在此基础上进一步实现个人的每日文摘。（因为是朋友圈推荐的内容，与在公网获得的文摘还是有所区别的）。

### 自动分类（autoClassify.py）
- 利用 LLM（DeepSeek）对多维表格中未分类的文章记录自动打标签
- 从参考表（sample_tables）学习已有分类模式，对目标表（target_table）中未分类记录批量分类
- 配置位于 `cfg/config.yaml` 的 `auto_classify` 段
- 运行命令：`python src/autoClassify.py` / `--dry-run` / `--batch-size N`
- 依赖：DeepSeek API Key（配置在 `cfg/llm_credentials.yaml` 或 `~/.config/secrets/gkeys.yaml`）

- 多维表格文件，合并2025年的两个表格的记录。

--------------------------------------------------------------------------------
--------------------------------------------------------------------------------
--------------------------------------------------------------------------------
# 自动运行指令
python src/goAIPM.py --towiki \
  https://articles.zsxq.com/id_08hjedox460c.html \
  https://arqiaoknow.feishu.cn/wiki/B206w99P0iJajLkOoSQcMWWwnwU
python src/goAIPM.py --towiki \
  /Volumes/DATADRIVE/shareAI/知识库_AI产品经理大本营/_非网页版本/星球周报_第415期_20260613.pdf \
  https://arqiaoknow.feishu.cn/wiki/JXErwE3x3iBBcIki7JkcXjVongb
                                                            # 将周报转写入飞书wiki文档。
python src/goAIPM.py --weekly https://arqiaoknow.feishu.cn/wiki/Yh1SwRjhciFpgMkOnNbchuCOnCd
                                                            # 直接运行周报，自动与多维表格中的日报内容，进行对比。
python src/goAIPM.py --file https://arqiaoknow.feishu.cn/wiki/Yh1SwRjhciFpgMkOnNbchuCOnCd
<!-- s                                                      # 直接运行周报，自动读取日报wiki文件的内容，进行对比。 -->

python src/goWTA.py --update
<!-- crond                                                  # 每天都运行，第 1 个（凌晨）。日报——WaytoAGI。 -->
python src/goAIPM.py --update
python src/goAIPM.py --daily https://t.zsxq.com/szV31
<!-- crond                                                  # 每天都运行，第 2 个（上午）。日报——产品经理大本营。 -->
python src/dfZSXQ.py --update
python src/dfZSXQ.py --his 20200320 20210104
<!-- crond                                                  # 每天都运行，第 3 个（傍晚）。下载知识星球的附件。 -->
python src/goWXGZH.py --update
<!-- crond                                                  # 每天都运行，第 4 个。20个微信公众号 -->
python src/goMessage.py --profile ai
python src/goMessage.py --profile ot
<!-- crond                                                  # 每天都运行，第 5 个。动态收藏URL。 -->
python src/autoClassify.py
<!-- crond                                                  # 每天都运行，第 6 个。自动分类打标签。 -->



--------------------------------------------------------------------------------
--------------------------------------------------------------------------------
--------------------------------------------------------------------------------
# 常用指令

## 授权
python src/auth.py



## 提取知名微信公众号文档
### 最新文档
python src/goWXGZH.py --update
### 历史文档
python src/goWXGZH.py --his 20260322 20260331
### 指定清单文件
python src/goWXGZH.py --his 20260322 20260331 --list cfg/my_list.yaml
### 直接输出公众号名称、alias、biz
python src/goWXGZH.py --searchbiz "DeepTech深科技"
准备工作：清除缓存文件、时间戳last_update复位为0
### 
python src/goWXGZH.py --repair-last-update



## 解析"WaytoAGI"网站内容中的URL
### 基于之前记录的已解析完成日期，解析该日期之后的日期的文字内容。
python src/goWTA.py --update
### 解析制定日期范围的文字内容。
python src/goWTA.py --his 20260322 20260331



## 解析"AI产品经理大本营"周报内容中的URL
### 处理自上次更新日期以后的所有日报wiki文档。
<!-- python src/goAIPM.py --update -->
### 直接处理单个日报wiki文档。
python src/goAIPM.py --daily https://t.zsxq.com/
### 直接处理单个周报wiki文档，自动与多维表格中的以往日报内容进行对比。
<!-- python src/goAIPM.py --weekly https://arqiaoknow.feishu.cn/wiki/SrSvwKNIMit9kokmGB3c7wXUnod -->
### 处理单个周报wiki文档。自动读取日报wiki文档的内容进行对比。
python src/goAIPM.py --file https://arqiaoknow.feishu.cn/wiki/xxx
### 指定列表文件（内含多个周报文档的URL）
python src/goAIPM.py --list input/list_周报.txt
### 默认读取 config 中配置的列表文件 （已删除）
python src/goAIPM.py



--------------------------------------------------------------------------------
--------------------------------------------------------------------------------
--------------------------------------------------------------------------------

## 只拉增量，启动加快
python src/goMessage.py --profile ai
python src/goMessage.py --profile ot

## 删除缓存文件后，全量拉取，生成缓存
python src/goMessage.py --all
## 缓存重建
python src/goMessage.py --reset

## 测试解析器
python tests/test_parser.py


## 废弃的
python src/goMessage.py --all --end 10
### 撤回指令，即将废弃
python src/goMessage.py --recall
python src/goMessage.py --all --recall
python src/archive/recall_messages.py --indices 57 --confirm-each
python src/archive/recall_messages.py --indices 56,60-62 --debug



--------------------------------------------------------------------------------
--------------------------------------------------------------------------------
--------------------------------------------------------------------------------
# 已知限制

## 飞书鉴权时效
- user_access_token 有效期约2小时，过期后自动使用 refresh_token 刷新
- refresh_token 有效期为30天，超过30天未使用需要重新手动授权（python src/auth.py）

## 飞书消息撤回机制
飞书群内撤回消息后，会显示"XXX撤回了一条消息"的提示。
这是飞书API的设计，DELETE /im/v1/messages/{message_id} 接口实际上是"撤回消息"而非"删除消息"。
无法真正删除消息而不留痕迹，这是为了保证群聊的透明度和可追溯性。

## 消息标记识别问题
消息标记为Flag是客户端本地行为，无法在服务端获得API。
Pin标记已实现识别（通过 get_pin_messages 接口）。

## 飞书文档权限
飞书文档解析优先取 update_time，取不到时 fallback 到 create_time。因权限原因，部分文档可能只能拿到创建日期而非最新更新日期。

## 部分网站需要登录
小红书、微博等平台的内容可能需要登录才能访问，解析时会标记"需要登录或无法访问"。

## 知乎 question 页面反爬限制（2026-02-15 调研）
知乎 `www.zhihu.com/question/` 类型页面有严格的反爬机制，当前 `_parse_zhihu_question` 方法无法提取标题和日期：
- 直接 requests.get 返回 403，页面内容是 JS 验证脚本（zse-ck v4）
- PC UA、移动端 UA、完整浏览器 headers 均返回 403
- Session 方式（先访问首页获取 cookie 再访问）仍然 403
- 知乎 API `/api/v4/questions/{id}` 需要 x-zse-96 签名，返回 403（code:10003）
- answers 接口触发反爬验证（code:40352），需要人机验证
- 部分不需要签名的接口（concerned_followers、similar-questions）能返回 200，但不包含问题标题和日期
- noembed/archive.org 均无法获取
- 可能的解决方案：cloudscraper、playwright/selenium 浏览器自动化、或者从飞书消息的分享卡片中提取标题信息

## 观猹 watcha.cn 页面 JS 渲染（2026-02-15 调研）
观猹 `watcha.cn/products/` 类型页面为纯前端 SPA，服务端返回的 HTML 不包含产品信息：
- 服务端返回固定的 HTML 模板，`<title>` 标签为通用的 "观猹丨玩 AI，上观猹！"
- 产品标题、描述等信息通过 JS 动态加载
- 尝试多种 API 路径（/api/、/trpc/、/server/）均返回 SPA 的 HTML
- 当前仅能识别来源为 `Web-观猹`，无法提取标题和日期
- 可能的解决方案：playwright/selenium 浏览器自动化渲染页面后提取




--------------------------------------------------------------------------------
--------------------------------------------------------------------------------
--------------------------------------------------------------------------------

对于如下链接的文件，分类为"星球-AI产品经理大本营"。
https://t.zsxq.com/E4uTc
https://t.zsxq.com/K02gq
https://t.zsxq.com/aPyIJ
https://articles.zsxq.com/id_282nwabe04j5.html
https://articles.zsxq.com/id_nujk4sh5adi7.html
https://articles.zsxq.com/id_3qlvc836c2kx.html
https://articles.zsxq.com/id_3qlvc836c2kx.html

其中，
https://t.zsxq.com/E4uTc 的标题为“重要_我最近突然意识到，AI不仅会给普通大众带来心理问题，对于高阶人士，也会很危险_20260225”，日期为20260225。
https://t.zsxq.com/K02gq 的标题为“AI商业化落地的一个模式洞察_20260226”，日期为20260226。
https://t.zsxq.com/aPyIJ 及 https://articles.zsxq.com/id_282nwabe04j5.html 的标题均为“我做副业，曾很多年都效果一般，但那一年，月收入涨了 10 倍……_20260224”，日期为20260224



https://www.zhihu.com/question/2005725246167147371，日期为20260214，标题为“杭州一创业者开 1 人公司，团队完全由 AI 智能体组成，月入 200 万，真能挣这么多钱吗？”


https://m.youtube.com/watch?v=iJEfIc1mrsc&pp=0gcJCUABo7VqN5tD 日期应解析为20260210。


https://dobby.now/community/view/c1cd69d3-5fc9-46b2-834e-d33b208d7e6a 的日期应解析为20260216。


分类为“Web_OT”的链接，需截断至“&userId=”之前的那部分字符串。
https://cs.cloud.tencent.com/workbench/?cid=ww48dc9b0429412b39&type=posterShare&id=2028379247501197313&userId=YuanLinHu&unionId=&WsUtmSource=material-share 的标题为“微信也能用OpenClaw!手把手教你如何实现”。


如果链接内含有“bytedance.larkoffice.com/”，则这类链接的来源为“飞书-字节跳动”。
带有“bytedance.larkoffice.com/”的URL，截断只保留第一个"?"之前的字符串内容。
https://bytedance.larkoffice.com/docx/EVYXdpdmhoVc0sxXR6dcsOu6nXc，标题为“即梦图片3.0模型 提示词手册”，日期为20250811。



- 对更多网站来源内容的解析。例如：微博、小宇宙、X等。
    “APP-微博”类型文章，需要登录权限，或者……
    x.com、twitter.com、youtube.com、youtu.be


把所有记录文档都进行更新。
你再核对一下，近三天的变更及工作经验都记录更新了吗？20260307

把经验记录到共享知识库中，同步github，同步OpenClaw服务器上的知识库内容。

基于我近期在多个项目中切换工作的情况，有没有需要更新到Claude Code的md文件的信息？
