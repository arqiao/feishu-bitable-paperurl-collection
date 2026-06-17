# 飞书消息管理说明

## 问题背景

在使用飞书开放平台API管理群聊消息时，发现API的行为与飞书客户端不完全一致。

## 飞书客户端的两种操作

在飞书客户端中，右键点击消息可以看到两个不同的选项：

### 1. 撤回消息
- **位置**：右键菜单中间位置
- **效果**：消息被撤回后，群里所有人都能看到"XXX撤回了一条消息"的提示
- **作用范围**：服务器端操作，影响所有用户
- **可逆性**：不可恢复

### 2. 删除消息
- **位置**：右键菜单底部
- **效果**：消息仅对当前用户隐藏，其他人仍然可以看到
- **作用范围**：客户端本地操作，仅影响当前用户
- **可逆性**：可能可以通过重新加载恢复（取决于客户端实现）

## 飞书开放平台API的限制

### 可用的API

飞书开放平台只提供**撤回消息**的API：

```
DELETE /im/v1/messages/{message_id}
```

**官方文档**：https://open.feishu.cn/document/server-docs/im-v1/message/delete

**API说明**：
- 接口名称：撤回消息（官方文档标题）
- 功能描述：机器人可撤回自己发送的消息，群主可撤回群内消息
- 效果：撤回后群里会显示"XXX撤回了一条消息"的提示
- 权限要求：需要相应的消息管理权限

### 不可用的功能

**客户端的"删除"功能没有对应的API**：
- 飞书开放平台API文档中没有提供"删除消息"（仅自己隐藏）的接口
- 客户端的"删除"功能是本地操作，不通过服务器API实现
- 这种设计可能是为了：
  1. 保证群聊的透明度和可追溯性
  2. 防止滥用API批量隐藏消息
  3. 区分客户端功能和API功能的边界

## 代码实现

### 当前实现

我们的代码使用的是飞书的"撤回消息"API：

```python
# feishu_client.py
def recall_message(self, message_id: str, show_detail: bool = False) -> bool:
    """撤回消息

    注意：
        - 只能撤回自己发送的消息
        - 或者需要是群管理员才能撤回群内其他人的消息
        - 撤回后群里会显示"XXX撤回了一条消息"的提示
        - 这是飞书API的设计，无法真正删除消息而不留痕迹
    """
    url = f"{self.base_url}/im/v1/messages/{message_id}"
    headers = self._get_headers()
    response = requests.delete(url, headers=headers)
    # ...
```

## 使用建议

### 1. 理解功能限制
使用 `recall_messages.py` 脚本时，需要明确：
- 这是"撤回"操作，不是"删除"操作
- 撤回后群里会显示提示信息
- 无法实现客户端的"仅自己隐藏"功能

### 2. 脚本使用
```bash
# 撤回指定消息
python src/archive/recall_messages.py --indices 1,3,5

# 试运行模式（不实际撤回）
python src/archive/recall_messages.py --indices 1-10 --dry-run

# 逐条确认模式
python src/archive/recall_messages.py --indices 1-10 --confirm-each

# 查看所有消息
python src/archive/recall_messages.py --list
```

### 3. 注意事项
- 撤回操作不可恢复
- 撤回后会留下"XXX撤回了一条消息"的提示
- 如果需要"静默删除"（不留痕迹），目前API无法实现

## 可能的替代方案

如果确实需要"删除"功能（仅自己隐藏），可以考虑：

1. **联系飞书官方**：询问是否有未公开的API或企业版功能
2. **客户端操作**：手动在飞书客户端中使用"删除"功能
3. **消息过滤**：在应用层面过滤不想看到的消息，而不是从服务器删除

## 参考资料

- [飞书开放平台 - 撤回消息](https://open.feishu.cn/document/server-docs/im-v1/message/delete)
- [飞书开放平台 - 消息管理概述](https://open.feishu.cn/document/server-docs/im-v1/message/intro)

## 更新记录

- 2026-02-11：创建文档，说明撤回和删除的区别
