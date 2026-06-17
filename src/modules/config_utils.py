"""配置文件工具函数。"""

import re
from datetime import datetime
import yaml


# Deprecated: simple text replacement is unsafe for nested/repeated config keys.
# Keep this stub only to make accidental future reuse fail loudly.
def update_config_field(config_path, field_name, old_val, new_val):
    raise RuntimeError(
        "update_config_field() 已停用，请改用 set_config_value() 或脚本内的精确定位写回逻辑"
    )


def set_config_value(config_path, path, value):
    raise RuntimeError(
        "set_config_value() 会整文件重写并丢失注释，已停用；请改用 set_config_value_preserve_comments()"
    )


def _format_yaml_scalar(value):
    """Format a scalar value for a single-line YAML replacement."""
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return 'null'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)

    text = str(value)
    if text == '':
        return "''"
    if re.fullmatch(r'[A-Za-z0-9_./:-]+', text):
        return text
    return "'" + text.replace("'", "''") + "'"


def set_config_value_preserve_comments(config_path, path, value, comment=None):
    """按 YAML 路径逐行更新单个标量值，尽量保留注释和原始格式。"""
    with open(config_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    def find_section_end(start_idx, current_indent):
        for i in range(start_idx + 1, len(lines)):
            stripped = lines[i].strip()
            if not stripped or stripped.startswith('#'):
                continue
            indent = len(lines[i]) - len(lines[i].lstrip(' '))
            if indent <= current_indent:
                return i
        return len(lines)

    start = 0
    end = len(lines)
    current_indent = -2

    for depth, key in enumerate(path):
        indent_spaces = depth * 2
        key_pattern = re.compile(rf'^({" " * indent_spaces}{re.escape(key)}:\s*)(.*?)(\s+#.*)?\s*$')
        found_idx = None

        for i in range(start, end):
            line = lines[i]
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            match = key_pattern.match(line.rstrip('\n'))
            if match:
                found_idx = i
                if depth == len(path) - 1:
                    prefix, _, old_comment = match.groups()
                    rendered = _format_yaml_scalar(value)
                    suffix = f'    # {comment}' if comment else (old_comment or "")
                    lines[i] = f'{prefix}{rendered}{suffix}\n'
                    with open(config_path, 'w', encoding='utf-8') as f:
                        f.writelines(lines)
                    return

                current_indent = indent_spaces
                start = i + 1
                end = find_section_end(i, current_indent)
                break

        if found_idx is None:
            raise ValueError(f'未找到配置路径: {".".join(path)}')

    raise ValueError(f'未找到配置路径: {".".join(path)}')


def format_unix_ts_comment(ts, fmt='%Y%m%d-%H:%M'):
    """把 Unix 时间戳格式化成内联注释文本。"""
    return datetime.fromtimestamp(int(ts)).strftime(fmt)


def set_list_item_scalar_preserve_comments(config_path, section_key, list_key,
                                           match_key, match_value, target_key,
                                           value, comment=None):
    """更新 YAML 中某个列表项的标量字段，并尽量保留注释。"""
    with open(config_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    in_section = False
    in_list = False
    target_item = False
    updated = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        if not in_section:
            if stripped == f'{section_key}:':
                in_section = True
            continue

        if in_section and not in_list:
            if stripped == f'{list_key}:':
                in_list = True
                continue
            if stripped and not line.startswith('  '):
                break
            continue

        if in_list:
            if stripped and not line.startswith('  '):
                break

            if stripped.startswith('- ') or stripped.startswith('# - '):
                target_item = False

            for prefix in (f'{match_key}:', f'#   {match_key}:'):
                if stripped.startswith(prefix):
                    value_text = stripped.split(':', 1)[1].strip()
                    target_item = (value_text == str(match_value))
                    break
            else:
                if target_item and stripped.startswith(f'{target_key}:'):
                    indent = line[:len(line) - len(line.lstrip())]
                    rendered = _format_yaml_scalar(value)
                    suffix = f'    # {comment}' if comment else ''
                    lines[i] = f'{indent}{target_key}: {rendered}{suffix}\n'
                    updated = True
                    break

    if not updated:
        raise ValueError(
            f'未找到列表项字段: {section_key}.{list_key} '
            f'[{match_key}={match_value}] -> {target_key}'
        )

    with open(config_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
