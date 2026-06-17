"""
多维表格自动分类脚本
基于 LLM（DeepSeek/DashScope）对文章标题+来源进行主题分类。
从已标注的参考表构建 few-shot 样本，批量调用 LLM 分类后回写多维表格。

典型用法：
  python src/autoClassify.py
      日常运行：分类目标表中“主题分类”为空、“人工/程序”为空、链接非空的记录。

  python src/autoClassify.py --dry-run --limit 20
      试运行：调用 LLM 并预览前 20 条分类结果，但不回写多维表格。

  python src/autoClassify.py --date 20260401 20260423
      按日期范围处理：只处理“日期”字段在区间内的记录（YYYYMMDD，含两端）。

  python src/autoClassify.py --line 1 500
      按行号范围处理：使用全表读取后按 1-based 行号截取，适合人工排查。

  python src/autoClassify.py --table tblXXX
      临时指定目标表：覆盖 cfg/config.yaml 中 auto_classify.target_table.table_id。

  python src/autoClassify.py --refresh
      强制重建缓存：重新读取字段选项和 few-shot 样本。

  python src/autoClassify.py --retry-unclassified
      定期复盘未分类记录：允许“人工/程序”为反斜杠（\\）的记录重新分类。

参数说明:
  --dry-run
      只输出分类/标记预览，不回写多维表格。
  --limit N
      限制本次送入 LLM 的记录数，主要用于调试或小批量复盘。
  --line START END
      按行号范围处理（1-based，含两端）；该模式会全表读取，因为飞书 filter
      不能可靠表达行号区间。
  --date START END
      按日期范围处理（YYYYMMDD，含两端）；默认路径先用飞书 filter 缩小候选，
      日期范围读取后在本地校验，避免飞书日期字段类型差异导致漏处理。
  --table TABLE_ID
      指定目标表 table_id，覆盖 config.yaml 配置。
  --refresh
      强制刷新字段选项和 few-shot 样本缓存；缓存默认 168 小时有效。
  --retry-unclassified
      默认跳过“人工/程序”为 人工、程序、反斜杠（\\）的记录；开启后，
      反斜杠（\\）且主题分类为空的记录会重新进入待分类列表。
  --type
      保留参数，暂未实现。

字段语义:
  “人工/程序”为空：尚未处理，可由程序分类。
  “人工/程序”为“程序”：本脚本已成功分类。
  “人工/程序”为“人工”：已有人工分类，脚本不覆盖。
  “人工/程序”为反斜杠（\\）：程序曾尝试但无法判断；日常跳过，定期复盘
      时使用 --retry-unclassified。
  “链接”为空：不分类，也不写入“人工/程序”。

配置:
  cfg/config.yaml          auto_classify 节（LLM、样本表、目标表）
  加密全局变量机制     LLM API key；支持 deepseek.api_key / dashscope.api_key
      等 provider 嵌套结构，也兼容 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY
      这类全局变量式键名。
"""

import os
import sys
import io
import json
import time
import argparse
import hashlib
import requests
import yaml
import re
from difflib import get_close_matches

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'src'))

from feishu_client import FeishuClient

CONFIG_PATH = os.path.join(PROJECT_ROOT, 'cfg', 'config.yaml')
CACHE_DIR = '/tmp/auto_classify_cache'
CACHE_TTL_SECONDS = 168 * 3600
TARGET_FIELDS = ['\u6807\u9898', '\u6765\u6e90', '\u65e5\u671f', '\u94fe\u63a5',
                 '\u4e3b\u9898\u5206\u7c7b', '\u4eba\u5de5/\u7a0b\u5e8f']
PROVIDER_DEFAULTS = {
    'deepseek': {
        'api_style': 'openai_chat',
        'strict_json': True,
        'prompt': {'max_candidate_options': 80, 'max_sample_categories': 24, 'samples_per_category': 1},
    },
    'openai': {
        'api_style': 'openai_chat',
        'strict_json': True,
        'prompt': {'max_candidate_options': 100, 'max_sample_categories': 24, 'samples_per_category': 1},
    },
    'volcengine': {
        'api_style': 'openai_chat',
        'strict_json': True,
        'prompt': {'max_candidate_options': 80, 'max_sample_categories': 20, 'samples_per_category': 1},
    },
    'minimax': {
        'api_style': 'anthropic_messages',
        'strict_json': False,
        'anthropic_version': '2023-06-01',
        'prompt': {'max_candidate_options': 60, 'max_sample_categories': 16, 'samples_per_category': 1},
    },
}


def _setup_encoding():
    if sys.platform == 'win32':
        if hasattr(sys.stdout, 'buffer'):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
        if hasattr(sys.stderr, 'buffer'):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', line_buffering=True)


def load_llm_credentials():
    """从加密全局变量机制加载 LLM 密钥。"""
    try:
        from secrets_loader import load as _secrets_load
    except ImportError:
        sys.path.insert(0, '/Volumes/DATADRIVE/workspace/sys')
        from secrets_loader import load as _secrets_load
    return _secrets_load('global', 'gkeys') or {}


def _get_llm_api_key(llm_creds, provider):
    provider_cfg = llm_creds.get(provider, {})
    if isinstance(provider_cfg, dict) and provider_cfg.get('api_key'):
        return provider_cfg['api_key']
    return llm_creds.get(f'{provider.upper()}_API_KEY', '')


def _get_llm_model(llm_cfg, llm_creds):
    provider = llm_cfg['provider']
    model = llm_cfg.get('model', '')
    provider_cfg = llm_creds.get(provider, {})
    if provider == 'volcengine' and model == 'endpoint_id':
        if isinstance(provider_cfg, dict):
            return provider_cfg.get('endpoint_id', '')
        return ''
    return model


def _provider_defaults(provider):
    return PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS['deepseek'])


def _merge_dict(base, overlay):
    merged = dict(base or {})
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _effective_prompt_cfg(ac_cfg):
    llm_cfg = ac_cfg.get('llm', {})
    provider = llm_cfg.get('provider', 'deepseek')
    defaults = _provider_defaults(provider).get('prompt', {})
    return _merge_dict(defaults, ac_cfg.get('prompt', {}))


class LLMClient:
    """轻量 LLM 客户端，支持 OpenAI-compatible 和 Anthropic-compatible 接口"""

    def __init__(self, llm_cfg, llm_creds):
        self.llm_cfg = llm_cfg
        provider = llm_cfg['provider']
        provider_defaults = _provider_defaults(provider)
        self.model = _get_llm_model(llm_cfg, llm_creds)
        if not self.model:
            raise ValueError(f'未配置 {provider} 的 model')
        self.base_url = llm_cfg['base_url'].rstrip('/')
        self.max_tokens = llm_cfg.get('max_tokens', 4096)
        self.temperature = llm_cfg.get('temperature', 0.1)
        self.api_style = llm_cfg.get('api_style', provider_defaults.get('api_style', 'openai_chat'))
        self.strict_json = llm_cfg.get('strict_json', provider_defaults.get('strict_json', True))
        self.anthropic_version = llm_cfg.get('anthropic_version',
                                             provider_defaults.get('anthropic_version', '2023-06-01'))
        self.api_key = _get_llm_api_key(llm_creds, provider)
        if not self.api_key:
            env_key = f'{provider.upper()}_API_KEY'
            raise ValueError(f'未配置 {provider} 的 api_key，请检查全局密钥配置中的 '
                             f'{provider}.api_key 或 {env_key}')
        self.total_calls = 0
        self.total_input = 0
        self.total_output = 0

    def chat(self, system_prompt, user_prompt):
        if self.api_style == 'anthropic_messages':
            return self._chat_anthropic(system_prompt, user_prompt)
        return self._chat_openai(system_prompt, user_prompt)

    def _url(self, path):
        if self.base_url.endswith('/v1') and path.startswith('/v1/'):
            path = path[3:]
        return f'{self.base_url}{path}'

    def _chat_openai(self, system_prompt, user_prompt):
        url = self._url('/chat/completions')
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {self.api_key}'}
        payload = {
            'model': self.model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            'max_tokens': self.max_tokens,
            'temperature': self.temperature,
        }
        if self.strict_json:
            payload['response_format'] = {'type': 'json_object'}
        for key in ('top_p', 'frequency_penalty', 'presence_penalty', 'seed'):
            if key in self.llm_cfg:
                payload[key] = self.llm_cfg[key]
        for attempt in range(3):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=120)
                resp.raise_for_status()
                data = resp.json()
                usage = data.get('usage', {})
                self.total_calls += 1
                self.total_input += usage.get('prompt_tokens', 0)
                self.total_output += usage.get('completion_tokens', 0)
                return data['choices'][0]['message']['content']
            except requests.exceptions.HTTPError:
                if self.strict_json and resp.status_code in (400, 422):
                    payload.pop('response_format', None)
                    self.strict_json = False
                    print('  当前 LLM 接口不支持 strict JSON，已降级为普通 JSON 提示')
                    continue
                if resp.status_code == 429:
                    time.sleep(2 ** attempt * 5)
                    continue
                print(f'  LLM 调用失败: {resp.status_code} {resp.text[:200]}')
                raise
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                if attempt < 2:
                    time.sleep(2 ** attempt * 5)
                    continue
                raise
        raise RuntimeError('LLM 调用重试次数耗尽')

    def _chat_anthropic(self, system_prompt, user_prompt):
        url = self._url('/v1/messages')
        headers = {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'anthropic-version': str(self.anthropic_version),
        }
        payload = {
            'model': self.model,
            'system': system_prompt,
            'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': user_prompt}]}],
            'max_tokens': self.max_tokens,
            'temperature': self.temperature,
        }
        for key in ('thinking', 'service_tier', 'top_p'):
            if key in self.llm_cfg:
                payload[key] = self.llm_cfg[key]
        for attempt in range(3):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=120)
                resp.raise_for_status()
                data = resp.json()
                usage = data.get('usage', {})
                self.total_calls += 1
                self.total_input += usage.get('input_tokens', 0)
                self.total_output += usage.get('output_tokens', 0)
                content = data.get('content', [])
                if isinstance(content, str):
                    return content
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get('type') == 'text':
                        texts.append(block.get('text', ''))
                return ''.join(texts)
            except requests.exceptions.HTTPError:
                if resp.status_code == 429:
                    time.sleep(2 ** attempt * 5)
                    continue
                print(f'  LLM 调用失败: {resp.status_code} {resp.text[:200]}')
                raise
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                if attempt < 2:
                    time.sleep(2 ** attempt * 5)
                    continue
                raise
        raise RuntimeError('LLM 调用重试次数耗尽')

    def report(self):
        print(f'\nLLM 用量: {self.total_calls} 次调用, '
              f'输入 {self.total_input} tokens, 输出 {self.total_output} tokens')

    def usage(self):
        return {
            'calls': self.total_calls,
            'input': self.total_input,
            'output': self.total_output,
        }


# --------------- 缓存功能 ---------------

def _fmt_seconds(seconds):
    """格式化耗时，避免日志里出现过长小数"""
    return f'{seconds:.2f}s'


def _fmt_time(ts):
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))


def _print_token_usage(prefix, usage):
    print(f'  {prefix} token: {usage["calls"]} 次调用, '
          f'输入 {usage["input"]}, 输出 {usage["output"]}')

def _cache_key(sample_tables):
    """根据样本表配置生成缓存文件名，配置变了缓存自动失效"""
    raw = json.dumps(sample_tables, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode()).hexdigest()[:8]


def _load_cache(sample_tables, ttl_seconds=CACHE_TTL_SECONDS):
    """加载样本和字段选项缓存，返回 (samples, valid_options) 或 None"""
    started = time.time()
    key = _cache_key(sample_tables)
    path = os.path.join(CACHE_DIR, f'cache_{key}.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        ts = data.get('timestamp', 0)
        age = time.time() - ts
        if age > ttl_seconds:
            print(f'  缓存已过期: 创建于 {_fmt_time(ts)}, '
                  f'年龄 {_fmt_seconds(age)}, TTL {_fmt_seconds(ttl_seconds)}')
            return None
        samples = data.get('samples', {})
        valid_options = data.get('valid_options', [])
        sample_record_count = data.get('sample_record_count', 0)
        cat_count = len(samples)
        opt_count = len(valid_options)
        print(f'  命中缓存: {cat_count} 个分类样本, {opt_count} 个字段选项'
              f' (创建时间: {_fmt_time(ts)}, 年龄: {_fmt_seconds(age)}, '
              f'加载耗时: {_fmt_seconds(time.time() - started)})')
        print(f'  缓存样本表读取记录数: {sample_record_count}')
        _print_token_usage('缓存阶段 LLM', {'calls': 0, 'input': 0, 'output': 0})
        return samples, valid_options
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def _save_cache(sample_tables, samples, valid_options, sample_record_count=0):
    """保存样本和字段选项到缓存"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = _cache_key(sample_tables)
    path = os.path.join(CACHE_DIR, f'cache_{key}.json')
    data = {
        'timestamp': time.time(),
        'samples': samples,
        'valid_options': valid_options,
        'sample_record_count': sample_record_count,
    }
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f'  缓存已保存: {path}')
    except OSError as e:
        print(f'  缓存保存失败: {e}')


# --------------- 字段提取 ---------------

def extract_title(fields):
    """从记录 fields 中提取标题文本"""
    title = fields.get('\u6807\u9898', '')
    if isinstance(title, list):
        return title[0].get('text', '') if title else ''
    return str(title)


def extract_source(fields):
    """从记录 fields 中提取来源文本"""
    src = fields.get('\u6765\u6e90', '')
    if isinstance(src, str):
        return src
    if isinstance(src, dict):
        return src.get('text', '')
    return ''


def extract_url(fields):
    """从记录 fields 中提取链接文本"""
    url = fields.get('\u94fe\u63a5', '')
    if isinstance(url, str):
        return url.strip()
    if isinstance(url, dict):
        return str(url.get('link') or url.get('text') or '').strip()
    if isinstance(url, list):
        for item in url:
            if isinstance(item, dict):
                value = item.get('link') or item.get('text')
            else:
                value = item
            if value:
                return str(value).strip()
    return ''


# --------------- JSON 解析 ---------------

def parse_llm_json(reply):
    """从 LLM 回复中提取 JSON 结果数组"""
    text = reply.strip()
    if '```' in text:
        match = re.search(r'```(?:json)?\s*(.*?)\s*```', text, re.DOTALL)
        if match:
            text = match.group(1)
    # 修复常见格式错误
    text = text.replace('\uff0c', ',')  # 中文逗号转英文
    text = re.sub(r',\s*([\]}])', r'\1', text)  # 末尾多余逗号
    text = re.sub(r'[\u201c\u201d]', '"', text)  # 中文引号转英文
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            from json_repair import loads
            data = loads(text)
        except (ImportError, Exception):
            print(f'  JSON 解析失败: {text[:200]}')
            return []
    if isinstance(data, dict):
        return data.get('results') or data.get('items') or []
    return data


def _fuzzy_match(input_str, valid_options, threshold=0.8):
    """简单模糊匹配，处理LLM返回的轻微拼写错误"""
    input_str = input_str.strip()
    if not input_str:
        return None
    matches = get_close_matches(input_str, valid_options, n=1, cutoff=threshold)
    return matches[0] if matches else None


# --------------- 样本构建 ---------------

def build_samples(client, sample_tables, field_name='\u4e3b\u9898\u5206\u7c7b'):
    """从样本表拉取已标注数据，按分类聚合，每类取最多 3 条"""
    by_cat = {}
    total_records = 0
    for tbl in sample_tables:
        app_token = tbl['app_token']
        table_id = tbl['table_id']
        print(f'  拉取样本: {table_id}...', end='', flush=True)
        records = client.get_bitable_records(app_token, table_id,
                                             ['\u6807\u9898', '\u6765\u6e90', field_name, '\u4eba\u5de5/\u7a0b\u5e8f'])
        total_records += len(records)
        print(f' {len(records)} 条')
        for r in records:
            cat = r.get(field_name)
            if not cat:
                continue
            flag = r.get('\u4eba\u5de5/\u7a0b\u5e8f', '')
            if flag and flag != '\u4eba\u5de5':
                continue
            title = extract_title(r)
            src = extract_source(r)
            if not title:
                continue
            by_cat.setdefault(cat, []).append(f'{title}\uff08{src}\uff09' if src else title)

    samples = {}
    for cat, titles in by_cat.items():
        samples[cat] = sorted(titles)[:3]
    print(f'  样本构建完成: {len(samples)} 个分类, 读取样本记录 {total_records} 条')
    return samples, total_records


# --------------- Prompt 构建 ---------------

def _text_terms(text):
    text = str(text or '').lower()
    terms = set(re.findall(r'[a-z0-9_+-]{2,}', text))
    cjk = re.findall(r'[\u4e00-\u9fff]', text)
    for i in range(len(cjk) - 1):
        terms.add(''.join(cjk[i:i + 2]))
    return terms


def _record_text(record):
    title = extract_title(record)
    src = extract_source(record)
    return f'{title} {src}'.strip()


def _rank_categories(valid_options, samples, records_batch, max_options):
    if not max_options or max_options >= len(valid_options):
        return list(valid_options)

    batch_terms = set()
    for r in records_batch:
        batch_terms.update(_text_terms(_record_text(r)))

    scored = []
    for idx, cat in enumerate(valid_options):
        cat_text = cat + ' ' + ' '.join(samples.get(cat, []))
        cat_terms = _text_terms(cat_text)
        score = len(batch_terms & cat_terms)
        if cat in samples:
            score += 0.1
        scored.append((score, -idx, cat))

    scored.sort(reverse=True)
    ranked = [cat for _, _, cat in scored[:max_options]]
    return ranked


def _select_prompt_samples(samples, candidate_options, records_batch, max_categories, per_category):
    if max_categories <= 0 or per_category <= 0:
        return []

    batch_terms = set()
    for r in records_batch:
        batch_terms.update(_text_terms(_record_text(r)))

    scored = []
    for idx, cat in enumerate(candidate_options):
        titles = samples.get(cat, [])
        if not titles:
            continue
        terms = _text_terms(cat + ' ' + ' '.join(titles))
        scored.append((len(batch_terms & terms), -idx, cat, titles))

    scored.sort(reverse=True)
    selected = []
    for _, _, cat, titles in scored[:max_categories]:
        selected.append((cat, titles[:per_category]))
    return selected


def build_topic_prompt(valid_options, samples, records_batch, prompt_cfg=None):
    """构建主题分类的 prompt"""
    prompt_cfg = prompt_cfg or {}
    max_options = prompt_cfg.get('max_candidate_options', 80)
    max_sample_categories = prompt_cfg.get('max_sample_categories', 24)
    samples_per_category = prompt_cfg.get('samples_per_category', 1)
    candidate_options = _rank_categories(valid_options, samples, records_batch, max_options)

    system = ('你是专业的文章分类助手。分类规则：\n'
              '1. 严格从给定的分类选项中选择最匹配的分类，不要自创分类\n'
              '2. 优先匹配最具体、最细分的分类，避免选择大类\n'
              '3. 如果多个分类都符合，选择相关性最高的一个\n'
              '4. 如果完全无法判断，主题分类返回空字符串\n'
              '5. 只返回紧凑JSON对象，不要解释、不要额外文字、不要换行缩进。')

    sample_lines = []
    for cat, titles in _select_prompt_samples(samples, candidate_options, records_batch,
                                              max_sample_categories, samples_per_category):
        sample_lines.append(f'[{cat}]')
        for t in titles:
            sample_lines.append(f'- {t}')

    records_lines = []
    for i, r in enumerate(records_batch):
        rid = r.get('_record_id', '')
        records_lines.append(f'{i+1}. id="{rid}" {_record_text(r)}')

    sample_block = chr(10).join(sample_lines) if sample_lines else '\u65e0'
    user = (f'## \u5206\u7c7b\u9009\u9879\uff08\u5019\u9009{len(candidate_options)}/{len(valid_options)}\u4e2a\uff09\n'
            f'{", ".join(candidate_options)}\n\n'
            f'## \u5206\u7c7b\u53c2\u8003\u793a\u4f8b\n{sample_block}\n\n'
            f'## \u5f85\u5206\u7c7b\u6587\u7ae0\n{chr(10).join(records_lines)}\n\n'
            f'\u8fd4\u56deJSON对象，schema固定为：'
            f'{{"results":[{{"id":"record_id","\u4e3b\u9898\u5206\u7c7b":"\u5206\u7c7b\u540d"}}]}}\n'
            f'\u6bcf\u6761\u8bb0\u5f55\u90fd\u5fc5\u987b\u5728results\u4e2d\u51fa\u73b0\uff1b'
            f'\u65e0\u6cd5\u5224\u65ad\u5219\u4e3b\u9898\u5206\u7c7b\u586b""\u3002'
            f'\u8f93\u51fa\u5fc5\u987b\u662f\u5355\u884c\u7d27\u51d1JSON\u3002')
    return system, user


# --------------- 批量分类 ---------------

def classify_batch(llm, valid_options, samples, records_batch, prompt_cfg=None, retry_single=True):
    """对一批记录调用 LLM 分类，批量失败时降级为逐条尝试"""
    allowed_ids = {r.get('_record_id', '') for r in records_batch if r.get('_record_id')}
    try:
        system, user = build_topic_prompt(valid_options, samples, records_batch, prompt_cfg)
        reply = llm.chat(system, user)
        results = parse_llm_json(reply)
    except Exception as e:
        if retry_single and len(records_batch) > 1:
            print(f'\n  批量处理失败，降级为逐条尝试...')
            all_results = []
            for r in records_batch:
                try:
                    res = classify_batch(llm, valid_options, samples, [r], prompt_cfg, retry_single=False)
                    all_results.extend(res)
                except Exception:
                    continue
            return all_results
        raise

    if not isinstance(results, list):
        return []
    valid_set = set(valid_options)
    validated = []
    seen_ids = set()
    for item in results:
        rid = item.get('id', '').strip()
        cat = item.get('\u4e3b\u9898\u5206\u7c7b', '').strip()
        if not rid:
            continue
        if rid not in allowed_ids:
            print(f'  忽略未知 record_id: {rid}')
            continue
        if rid in seen_ids:
            print(f'  忽略重复 record_id: {rid}')
            continue
        seen_ids.add(rid)
        if cat and cat not in valid_set:
            matched = _fuzzy_match(cat, valid_set)
            if matched:
                cat = matched
                print(f'  模糊匹配: "{cat}" -> "{matched}"（record={rid}）')
            else:
                print(f'  无效分类值: {cat}（record={rid}）')
                cat = ''
        if cat:
            validated.append({'record_id': rid,
                              'fields': {'\u4e3b\u9898\u5206\u7c7b': cat, '\u4eba\u5de5/\u7a0b\u5e8f': '\u7a0b\u5e8f'}})
    return validated


# --------------- 记录筛选 ---------------

def _date_in_range(record, args):
    if not args.date:
        return True
    d_start, d_end = args.date
    date_value = record.get('\u65e5\u671f')
    return bool(date_value and d_start <= date_value <= d_end)


def _select_records(records, args):
    """从候选记录中分出人工标记和待分类记录，并统计跳过原因"""
    stats = {
        'read': len(records),
        'date_skipped': 0,
        'empty_url_skipped': 0,
        'processed_skipped': 0,
    }
    manual_marks = []
    todo = []
    for r in records:
        if not _date_in_range(r, args):
            stats['date_skipped'] += 1
            continue
        if not extract_url(r):
            stats['empty_url_skipped'] += 1
            continue

        topic = r.get('\u4e3b\u9898\u5206\u7c7b')
        flag = r.get('\u4eba\u5de5/\u7a0b\u5e8f') or ''
        if topic and not flag:
            manual_marks.append(r)
        elif not topic and (not flag or (args.retry_unclassified and flag == '\\')):
            todo.append(r)
        else:
            stats['processed_skipped'] += 1
    return manual_marks, todo, stats


def _and_filter(parts):
    parts = [p for p in parts if p]
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0]
    return f'AND({",".join(parts)})'


def _build_candidate_filters(args):
    manual_filter = _and_filter([
        'CurrentValue.[\u94fe\u63a5] != ""',
        'CurrentValue.[\u4e3b\u9898\u5206\u7c7b] != ""',
        'CurrentValue.[\u4eba\u5de5/\u7a0b\u5e8f] = ""',
    ])

    flag_filter = 'CurrentValue.[\u4eba\u5de5/\u7a0b\u5e8f] = ""'
    if args.retry_unclassified:
        flag_filter = ''
    todo_filter = _and_filter([
        'CurrentValue.[\u94fe\u63a5] != ""',
        'CurrentValue.[\u4e3b\u9898\u5206\u7c7b] = ""',
        flag_filter,
    ])
    return manual_filter, todo_filter


def _dedupe_records(records):
    seen = set()
    deduped = []
    for r in records:
        rid = r.get('_record_id')
        if rid and rid in seen:
            continue
        if rid:
            seen.add(rid)
        deduped.append(r)
    return deduped


def _duplicate_url_stats(records):
    by_url = {}
    for r in records:
        url = extract_url(r)
        if not url:
            continue
        by_url.setdefault(url, []).append(r)
    duplicates = {url: items for url, items in by_url.items() if len(items) > 1}
    duplicate_records = sum(len(items) for items in duplicates.values())
    return len(duplicates), duplicate_records


def _print_failed_update_records(result, source_records):
    failed_records = result.get('failed_records') or []
    if not failed_records:
        return

    by_id = {r.get('_record_id'): r for r in source_records if r.get('_record_id')}
    print('  失败记录明细:')
    for item in failed_records[:20]:
        rid = item.get('record_id', '')
        src = by_id.get(rid, {})
        title = extract_title(src)
        source = extract_source(src)
        url = extract_url(src)
        fields = item.get('fields', {})
        error = item.get('error', '')
        print(f'    record_id: {rid}')
        print(f'      标题: {title[:120]}')
        print(f'      来源: {source}')
        print(f'      链接: {url}')
        print(f'      写入字段: {json.dumps(fields, ensure_ascii=False)}')
        print(f'      错误: {error}')
    if len(failed_records) > 20:
        print(f'    ... 其余 {len(failed_records) - 20} 条失败记录未展开')


def _full_scan_records(client, app_token, table_id):
    records = client.get_bitable_records(app_token, table_id, TARGET_FIELDS)
    if client.last_error:
        raise RuntimeError(f'目标表读取失败: {client.last_error}')
    return records


def _read_candidate_records(client, app_token, table_id, args):
    """读取候选记录。默认使用 filter，--line 使用全表读取。"""
    if args.line:
        print('  候选读取方式: full-scan (--line)')
        records = _full_scan_records(client, app_token, table_id)
        row_start, row_end = args.line
        records = records[row_start - 1:row_end]
        print(f'  行号范围: {row_start} ~ {row_end}（实际 {len(records)} 条）')
        return records, 'full-scan (--line)'

    print('  候选读取方式: filter')
    manual_filter, todo_filter = _build_candidate_filters(args)
    manual_candidates = client.search_bitable_records(app_token, table_id, TARGET_FIELDS, manual_filter)
    if client.last_error:
        print(f'  filter 查询失败，回退全表读取: {client.last_error}')
        return _full_scan_records(client, app_token, table_id), 'full-scan (filter fallback)'

    todo_candidates = client.search_bitable_records(app_token, table_id, TARGET_FIELDS, todo_filter)
    if client.last_error:
        print(f'  filter 查询失败，回退全表读取: {client.last_error}')
        return _full_scan_records(client, app_token, table_id), 'full-scan (filter fallback)'

    return _dedupe_records(manual_candidates + todo_candidates), 'filter'


# --------------- 主流程 ---------------

def run_topic_classify(client, ac_cfg, app_token, table_id, args):
    """执行主题分类"""
    print('\n=== 主题分类 ===')

    sample_tables = ac_cfg.get('sample_tables', [])

    # 尝试加载缓存
    cache_started = time.time()
    cached = None if getattr(args, 'refresh', False) else _load_cache(sample_tables)

    if cached:
        samples, valid_options = cached
    else:
        print('  开始重建缓存...', flush=True)
        # 获取合法选项
        print('获取字段选项...', flush=True)
        fields = client.get_bitable_fields(app_token, table_id)
        valid_options = []
        for f in fields:
            if f.get('field_name') == '\u4e3b\u9898\u5206\u7c7b':
                prop = f.get('property') or {}
                valid_options = [o['name'] for o in prop.get('options', [])]
                break
        if not valid_options:
            print('未找到\u201c主题分类\u201d字段选项')
            return
        print(f'  {len(valid_options)} 个合法选项')

        # 构建样本
        print('\n构建 few-shot 样本...', flush=True)
        samples, sample_record_count = build_samples(client, sample_tables, '\u4e3b\u9898\u5206\u7c7b')

        # 保存缓存
        _save_cache(sample_tables, samples, valid_options, sample_record_count)
        print(f'  缓存重建耗时: {_fmt_seconds(time.time() - cache_started)}')
        print(f'  缓存样本表读取记录数: {sample_record_count}')
        _print_token_usage('缓存阶段 LLM', {'calls': 0, 'input': 0, 'output': 0})

    # 拉取待分类记录
    print(f'\n拉取目标表 {table_id} 记录...', flush=True)
    read_started = time.time()
    records, read_mode = _read_candidate_records(client, app_token, table_id, args)
    if args.date:
        d_start, d_end = args.date
        print(f'  日期范围: {d_start} ~ {d_end}')
    print(f'  候选读取完成: {len(records)} 条, 耗时 {_fmt_seconds(time.time() - read_started)}')

    # 已有主题分类但\u201c人工/程序\u201d为空的，标记为\u201c人工\u201d
    manual_marks, todo, select_stats = _select_records(records, args)
    print(f'  读取记录数: {select_stats["read"]}')
    if read_mode == 'filter':
        print(f'  空链接跳过: {select_stats["empty_url_skipped"]}（filter 已尽量排除）')
        print(f'  已处理跳过: {select_stats["processed_skipped"]}（filter 已尽量排除）')
    else:
        print(f'  空链接跳过: {select_stats["empty_url_skipped"]}')
        print(f'  已处理跳过: {select_stats["processed_skipped"]}')
    if select_stats['date_skipped']:
        print(f'  日期范围外跳过: {select_stats["date_skipped"]}')

    if manual_marks and not args.dry_run:
        print(f'  标记 {len(manual_marks)} 条已有分类为\u201c人工\u201d...', flush=True)
        mark_updates = [{'record_id': r['_record_id'],
                         'fields': {'\u4eba\u5de5/\u7a0b\u5e8f': '\u4eba\u5de5'}} for r in manual_marks]
        result = client.batch_update_bitable_records(app_token, table_id, mark_updates)
        print(f'  完成: 成功 {result["success"]}\uff0c失败 {result["failed"]}')
        _print_failed_update_records(result, manual_marks)
    elif manual_marks:
        print(f'  [dry-run] 将标记 {len(manual_marks)} 条已有分类为\u201c人工\u201d')

    print(f'  待分类 {len(todo)} 条')
    dup_url_count, dup_record_count = _duplicate_url_stats(todo)
    if dup_url_count:
        print(f'  待分类重复链接: {dup_url_count} 个 URL，涉及 {dup_record_count} 条记录')

    if args.limit > 0:
        todo = todo[:args.limit]
        print(f'  限制处理 {len(todo)} 条')

    if not todo:
        print('无待分类记录')
        print(f'  分类阶段耗时: {_fmt_seconds(0)}')
        _print_token_usage('分类阶段 LLM', {'calls': 0, 'input': 0, 'output': 0})
        return

    # 批量分类
    classify_started = time.time()
    llm_creds = load_llm_credentials()
    llm = LLMClient(ac_cfg['llm'], llm_creds)
    prompt_cfg = _effective_prompt_cfg(ac_cfg)
    batch_size = ac_cfg.get('batch_size', 10)
    all_updates = []
    total_batches = (len(todo) + batch_size - 1) // batch_size

    for i in range(0, len(todo), batch_size):
        batch = todo[i:i + batch_size]
        batch_num = i // batch_size + 1
        print(f'\r  分类中... [{batch_num}/{total_batches}]', end='', flush=True)
        try:
            results = classify_batch(llm, valid_options, samples, batch, prompt_cfg)
            all_updates.extend(results)
        except Exception as e:
            print(f'\n  第 {batch_num} 批分类失败: {e}')
        time.sleep(0.5)

    print(f'\n  分类完成: {len(all_updates)} 条有结果')

    # LLM 未能分类的记录标记为反斜杠，后续用 --retry-unclassified 定期复盘。
    classified_ids = {u['record_id'] for u in all_updates}
    unclassified = [r for r in todo if r['_record_id'] not in classified_ids]
    if unclassified:
        unclass_updates = [{'record_id': r['_record_id'],
                            'fields': {'\u4eba\u5de5/\u7a0b\u5e8f': '\\'}} for r in unclassified]
        all_updates.extend(unclass_updates)
        print(f'  未分类 {len(unclassified)} 条，标记为"\\"')
    else:
        print('  未分类 0 条')

    print(f'  分类阶段耗时: {_fmt_seconds(time.time() - classify_started)}')
    _print_token_usage('分类阶段 LLM', llm.usage())

    if args.dry_run:
        print('\n[dry-run] 分类结果预览:')
        for u in all_updates[:20]:
            rid = u['record_id']
            cat = u['fields'].get('\u4e3b\u9898\u5206\u7c7b', '')
            title = ''
            for r in todo:
                if r.get('_record_id') == rid:
                    title = extract_title(r)
                    break
            print(f'  {title[:50]} -> {cat}')
        if len(all_updates) > 20:
            print(f'  ... 共 {len(all_updates)} 条')
        return

    # 回写
    if all_updates:
        print(f'\n回写多维表格...', flush=True)
        result = client.batch_update_bitable_records(app_token, table_id, all_updates)
        print(f'  成功 {result["success"]}\uff0c失败 {result["failed"]}')
        _print_failed_update_records(result, todo)

    llm.report()


def main():
    _setup_encoding()

    ap = argparse.ArgumentParser(description='多维表格自动分类')
    ap.add_argument('--type', action='store_true', help='填充类型分类（暂未实现）')
    ap.add_argument('--dry-run', action='store_true', help='只输出结果，不回写')
    ap.add_argument('--limit', type=int, default=0, help='限制处理条数')
    ap.add_argument('--line', nargs=2, type=int, metavar=('START', 'END'),
                    help='按行号范围处理（1-based，含两端）')
    ap.add_argument('--date', nargs=2, metavar=('START', 'END'),
                    help='按日期范围处理（YYYYMMDD，含两端）')
    ap.add_argument('--table', type=str, help='指定目标表 table_id')
    ap.add_argument('--refresh', action='store_true',
                    help='强制刷新样本和字段选项缓存')
    ap.add_argument('--retry-unclassified', action='store_true',
                    help='复盘“人工/程序”为反斜杠 \\ 的未分类记录')
    args = ap.parse_args()

    client = FeishuClient()
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    ac_cfg = config.get('auto_classify', {})
    if not ac_cfg:
        print('未配置 auto_classify，请检查 config.yaml')
        return

    target = ac_cfg['target_table']
    if args.table:
        target['table_id'] = args.table
    app_token = target['app_token']
    table_id = target['table_id']

    run_topic_classify(client, ac_cfg, app_token, table_id, args)


if __name__ == '__main__':
    main()
