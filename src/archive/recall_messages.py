"""
撤回特定消息脚本
用于撤回指定序号的群聊消息

注意：
- 飞书API提供的是"撤回消息"功能
- 撤回后群里会显示"XXX撤回了一条消息"的提示
"""

import argparse
from datetime import datetime
from feishu_client import FeishuClient
from modules.config_utils import set_config_value_preserve_comments
import json
import re


def get_message_text(message: dict) -> str:
    """获取消息的文本内容"""
    content = message.get('body', {}).get('content', '')
    if not content:
        return ''

    try:
        content_json = json.loads(content)

        # 文本消息
        if message.get('msg_type') == 'text':
            return content_json.get('text', '')

        # 富文本消息
        elif message.get('msg_type') == 'post':
            text_parts = []
            post_content = content_json.get('content', {})
            for lang_content in post_content.values():
                for item in lang_content:
                    for element in item:
                        if element.get('tag') == 'text':
                            text_parts.append(element.get('text', ''))
                        elif element.get('tag') == 'a':
                            text_parts.append(element.get('text', ''))
            return ' '.join(text_parts)

        # 其他类型消息
        else:
            return f"[{message.get('msg_type', 'unknown')} 类型消息]"

    except Exception as e:
        return f"[解析失败: {e}]"


def parse_indices(indices_str: str) -> list:
    """解析消息序号参数

    支持格式：
    - 单个序号：1
    - 多个序号：1,3,5
    - 范围：1-5
    - 混合：1,3-5,7,10-15
    """
    indices = set()

    parts = indices_str.split(',')
    for part in parts:
        part = part.strip()
        if '-' in part:
            # 范围
            start, end = part.split('-')
            start = int(start.strip())
            end = int(end.strip())
            indices.update(range(start, end + 1))
        else:
            # 单个序号
            indices.add(int(part))

    return sorted(list(indices))


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='撤回指定序号的群聊消息（注意：撤回后群里会显示提示）')
    parser.add_argument('--indices', type=str,
                        help='要撤回的消息序号，支持格式：1 或 1,3,5 或 1-5 或 1,3-5,7')
    parser.add_argument('--dry-run', action='store_true',
                        help='试运行模式，只显示将要撤回的消息，不实际撤回')
    parser.add_argument('--list', action='store_true',
                        help='列出所有消息及其序号，不执行撤回')
    parser.add_argument('--confirm-each', action='store_true',
                        help='逐条确认模式，撤回每条消息前都需要确认')
    parser.add_argument('--debug', action='store_true',
                        help='调试模式，显示详细的API响应信息')
    args = parser.parse_args()

    print("=" * 60)
    print("撤回群聊消息工具")
    print("=" * 60)

    # 初始化客户端
    client = FeishuClient()

    # 检查 token 是否有效
    if not client.check_token_valid():
        print("\nToken 已过期或无效，尝试刷新...")
        if not client.refresh_access_token():
            print("\n✗ Token 刷新失败，请重新授权：")
            print("  运行命令: python src/modules/feishu_auth.py")
            return

    print("\n✓ Token 验证成功")

    # 获取群聊 ID
    chat_id = client.config['target_chat'].get('chat_id', '')
    if not chat_id:
        print("\n正在查找目标群聊...")
        chat_name = client.config['target_chat']['name']
        chat_id = client.find_chat_by_name(chat_name)

        if not chat_id:
            print(f"\n✗ 未找到群聊: {chat_name}")
            return

        client.config['target_chat']['chat_id'] = chat_id
        set_config_value_preserve_comments(
            client.config_path, ['target_chat', 'chat_id'], chat_id)
        print(f"✓ 找到群聊: {chat_name} (ID: {chat_id})")
    else:
        print(f"\n✓ 使用已配置的群聊 ID: {chat_id}")

    # 获取群聊消息（始终获取所有消息，不受 last_processed_time 限制）
    print("\n正在获取群聊消息...")
    messages = client.get_chat_messages(chat_id, start_time=0)
    total_messages_before = len(messages)
    print(f"✓ 获取到 {total_messages_before} 条消息")

    if total_messages_before == 0:
        print("\n没有消息需要处理")
        return

    # 为消息分配序号
    indexed_messages = []
    for idx, msg in enumerate(messages, 1):
        message_text = get_message_text(msg)
        indexed_messages.append({
            'index': idx,
            'message_id': msg.get('message_id'),
            'create_time': int(msg.get('create_time', '0')),
            'sender_id': msg.get('sender', {}).get('id', ''),
            'text': message_text
        })

    # 如果是列出模式，显示所有消息
    if args.list:
        print("\n" + "=" * 60)
        print(f"消息列表（共 {len(indexed_messages)} 条）")
        print("=" * 60)
        for msg_info in indexed_messages:
            create_time = datetime.fromtimestamp(msg_info['create_time'] / 1000)
            print(f"\n[消息 {msg_info['index']}] 时间: {create_time}")
            # 限制显示长度
            text = msg_info['text']
            if len(text) > 100:
                text = text[:100] + "..."
            print(f"  内容: {text}")
        print("\n" + "=" * 60)
        print(f"\n提示：使用 --indices 参数指定要撤回的消息序号")
        print(f"示例：python recall_messages.py --indices 1,3,5")
        print(f"示例：python recall_messages.py --indices 1-5")
        return

    # 解析要撤回的消息序号
    if not args.indices:
        print("\n✗ 请使用 --indices 参数指定要撤回的消息序号")
        print("  示例：python recall_messages.py --indices 1,3,5")
        print("  示例：python recall_messages.py --indices 1-5")
        print("  或使用 --list 参数查看所有消息")
        return

    try:
        target_indices = parse_indices(args.indices)
    except Exception as e:
        print(f"\n✗ 解析消息序号失败: {e}")
        print("  请检查格式是否正确")
        return

    print(f"\n✓ 要撤回的消息序号: {target_indices}")

    # 筛选要撤回的消息
    messages_to_recall = []
    for msg_info in indexed_messages:
        if msg_info['index'] in target_indices:
            messages_to_recall.append(msg_info)

    if len(messages_to_recall) == 0:
        print(f"\n✗ 未找到指定序号的消息")
        print(f"  可用的消息序号范围: 1-{len(indexed_messages)}")
        return

    print(f"✓ 找到 {len(messages_to_recall)} 条匹配的消息")

    # 从编号最靠后的消息开始撤回（倒序），避免序号混乱
    messages_to_recall.sort(key=lambda x: x['index'], reverse=True)
    print(f"✓ 将按倒序撤回（从编号最大的消息开始），避免序号混乱")

    # 显示将要撤回的消息
    print("\n" + "=" * 60)
    print("将要撤回的消息列表：")
    print("=" * 60)

    for msg_info in messages_to_recall:
        create_time = datetime.fromtimestamp(msg_info['create_time'] / 1000)
        # 限制显示长度
        text = msg_info['text']
        if len(text) > 100:
            text = text[:100] + "..."

        print(f"\n[消息 {msg_info['index']}] 发送时间: {create_time}")
        print(f"    内容: {text}")

    # 试运行模式
    if args.dry_run:
        print("\n" + "=" * 60)
        print("✓ 试运行模式，未实际撤回消息")
        print(f"  共找到 {len(messages_to_recall)} 条消息")
        print("  如需实际撤回，请去掉 --dry-run 参数")
        print("=" * 60)
        return

    # 确认撤回
    print("\n" + "=" * 60)
    print("⚠️  警告：撤回操作不可恢复！")
    print("⚠️  注意：撤回后群里会显示'XXX撤回了一条消息'的提示")
    print("=" * 60)

    if args.confirm_each:
        print(f"\n✓ 已启用逐条确认模式")
        print(f"  将在撤回每条消息前单独确认")
    else:
        confirm = input(f"\n确认撤回这 {len(messages_to_recall)} 条消息吗？(输入 y 确认): ")

        if confirm.lower() != 'y':
            print("\n✗ 已取消撤回操作")
            return

    # 执行撤回
    print("\n开始撤回消息...")
    success_count = 0
    error_count = 0
    skipped_count = 0

    for idx, msg_info in enumerate(messages_to_recall, 1):
        message_id = msg_info['message_id']
        message_index = msg_info['index']
        message_text = msg_info['text']

        # 限制显示长度
        if len(message_text) > 100:
            display_text = message_text[:100] + "..."
        else:
            display_text = message_text

        print(f"\n[{idx}/{len(messages_to_recall)}] 消息 {message_index}")
        print(f"  内容: {display_text}")

        # 逐条确认模式
        if args.confirm_each and not getattr(args, '_confirm_all', False):
            confirm = input(f"  确认撤回此消息？(y=撤回, n=跳过, a=确认剩余全部, q=退出): ").lower()

            if confirm == 'a':
                print(f"  ✓ 已切换为全部确认模式，剩余消息将自动撤回")
                args._confirm_all = True
            elif confirm == 'q':
                print(f"\n✗ 用户选择退出，停止撤回")
                break
            elif confirm != 'y':
                print(f"  ⊘ 已跳过")
                skipped_count += 1
                continue

        # 执行撤回
        if client.recall_message(message_id, show_detail=args.debug):
            print(f"  ✓ 撤回成功")
            success_count += 1
        else:
            print(f"  ✗ 撤回失败")
            error_count += 1

    # 输出统计
    print("\n" + "=" * 60)
    print("撤回完成")
    print("=" * 60)
    print(f"成功: {success_count} 条")
    print(f"失败: {error_count} 条")
    if args.confirm_each and skipped_count > 0:
        print(f"跳过: {skipped_count} 条")
    print(f"总计: {len(messages_to_recall)} 条")

    # 验证撤回结果
    if success_count > 0:
        print("\n正在验证撤回结果...")
        messages_after = client.get_chat_messages(chat_id, start_time=0)
        total_messages_after = len(messages_after)
        print(f"✓ 撤回前消息总数: {total_messages_before}")
        print(f"✓ 撤回后消息总数: {total_messages_after}")
        print(f"✓ 实际减少: {total_messages_before - total_messages_after} 条")

        if total_messages_before - total_messages_after == success_count:
            print(f"✓ 验证成功：消息已从服务器撤回")
        elif total_messages_before - total_messages_after == 0:
            print(f"\n提示：API返回撤回成功，但消息数量未变化")
            print(f"  这通常是飞书API缓存延迟导致的，消息实际上已被撤回")
            print(f"  请在飞书客户端中刷新群聊，确认消息是否已撤回")
            print(f"  注意：撤回的消息会显示为'XXX撤回了一条消息'")
        else:
            print(f"\n提示：撤回数量与预期不符")
            print(f"  预期撤回: {success_count} 条")
            print(f"  实际减少: {total_messages_before - total_messages_after} 条")
            print(f"  请在飞书客户端中确认实际撤回情况")


if __name__ == "__main__":
    main()
