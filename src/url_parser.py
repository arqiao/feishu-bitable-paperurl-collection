"""
URL 解析器
对特定 URL 进行解析，提取来源、标题、日期等信息
"""

import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Dict, Optional
from urllib.parse import urlparse, parse_qs, urlencode, unquote, urlunparse
import re
import json
import hashlib


def normalize_url(url, lowercase=False):
    """URL 归一化：去除各平台无意义参数，去尾部斜杠。
    用于去重比较和缓存 key。

    Args:
        url: 待归一化的 URL
        lowercase: 是否将域名转为小写，默认 False
    """
    if not url:
        return url

    url_lower = url.lower()

    # 飞书 wiki/docx/会议纪要：去 ? 后所有参数
    if ('feishu.cn/wiki/' in url_lower or 'feishu.cn/docx/' in url_lower
            or 'larkoffice.com/' in url_lower
            or 'feishu.cn/minutes/' in url_lower) and '?' in url:
        url = url.split('?')[0]

    # 微信长链接：保留核心参数，去掉 scene 及之后
    elif 'mp.weixin.qq.com/s' in url_lower:
        if 'mp.weixin.qq.com/s?' in url_lower and '__biz=' in url_lower:
            idx = url.lower().find('&scene=')
            if idx > 0:
                url = url[:idx]
        elif '?' in url:
            url = url.split('?')[0]

    # YouTube：只保留 ?v=xxx
    elif ('youtube.com/watch?' in url_lower or 'youtu.be/' in url_lower) and '&' in url:
        url = url[:url.index('&')]

    # 腾讯企点：截断 &userId=
    elif 'cs.cloud.tencent.com/workbench/' in url_lower and '&userid=' in url_lower:
        idx = url_lower.index('&userid=')
        url = url[:idx]

    # 同花顺帖子：只保留 ?pid=xxx
    elif '10jqka.com.cn/' in url_lower and '?' in url:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        pid = params.get('pid', [None])[0]
        if pid:
            url = url.split('?')[0] + '?pid=' + pid
        else:
            url = url.split('?')[0]

    # 通用 truncate 站点：去 ? 后参数
    else:
        _truncate_domains = [
            'm.toutiao.com/', 'www.toutiao.com/',
            'bilibili.com/', 'm.bilibili.com/',
            'm.weibo.cn/', 'weibo.com/',
            'www.zhihu.com/question/',
            'www.xiaoyuzhoufm.com/episode/',
            'chainthink.cn/',
            'waytoagi.feishu.cn/',
        ]
        for domain in _truncate_domains:
            if domain in url_lower and '?' in url:
                url = url.split('?')[0]
                break

    # 去尾部斜杠，可选域名小写
    if lowercase:
        parsed = urlparse(url)
        url = urlunparse(parsed._replace(netloc=parsed.netloc.lower()))
    return url.rstrip('/')


class UrlParser:
    """URL 解析器"""

    def __init__(self, credentials=None):
        """
        Args:
            credentials: 外部凭证字典，可选，结构如下：
                {
                    'zsxq_token': '...',
                    'zhihu_cookies': {'z_c0': '...', 'd_c0': '...', '__zse_ck': '...'},
                    'feishu_user_token': '...',
                    'wechat_cookie': '...',
                }
        """
        self.credentials = credentials or {}
        self.session = requests.Session()
        self.session.trust_env = False
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

        # 移动端User-Agent，用于某些需要移动端访问的网站
        self.mobile_headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 11) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0 Mobile Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        }

        # 网站识别规则：URL特征 -> (来源名称, 解析方法, 是否截断URL)
        # 截断URL：True表示只保留第一个"?"之前的内容
        self.site_rules = [
            ('mp.weixin.qq.com', ('微信', self._parse_wechat_article, True)),
            ('https://wallstreetcn.com/articles/', ('APP-华尔街见闻', self._parse_general_article, False)),
            ('https://m.bilibili.com/video/', ('APP-哔哩哔哩', self._parse_bilibili_article, True)),  # 哔哩哔哩移动端
            ('https://b23.tv/', ('APP-哔哩哔哩', self._parse_bilibili_article, False)),
            ('bilibili.com/', ('APP-哔哩哔哩', self._parse_bilibili_article, True)),  # 哔哩哔哩其他页面
            ('www.zhihu.com/question/', ('APP-知乎', self._parse_zhihu_question, True)),
            ('https://zhuanlan.zhihu.com/p/', ('APP-知乎', self._parse_zhihu_zhuanlan, False)),
            ('https://www.biji.com/note/share_note/', ('Get笔记OT分享', self._parse_general_article, False)),
            ('https://www.xiaohongshu.com/discovery/item/', ('APP-小红书', self._parse_xiaohongshu_article, False)),
            ('xhslink.com', ('APP-小红书', self._parse_xiaohongshu_article, False)),
            ('https://m.weibo.cn/status/', ('App-微博', self._parse_weibo_article, True)),
            ('weibo.com', ('App-微博', self._parse_weibo_article, True)),
            ('https://m.okjike.com/originalPosts/', ('APP-即刻', self._parse_jike_article, False)),
            ('xiaobot.net/', ('APP-小报童', self._parse_xiaobot_article, False)),
            ('waytoagi.feishu.cn/record/', ('飞书-通往AGI之路', self._parse_feishu_record, False)),  # WTA 多维表格记录
            ('waytoagi.feishu.cn/', ('飞书-通往AGI之路', self._parse_feishu_article, True)),  # WTA 优先于通用飞书规则
            ('.feishu.cn/record/', ('飞书OT分享', self._parse_feishu_record, False)),  # 飞书多维表格记录
            ('.feishu.cn/base/', ('飞书OT分享', self._parse_feishu_base, False)),  # 飞书多维表格首页
            ('.feishu.cn/wiki/', ('飞书OT分享', self._parse_feishu_article, True)),
            ('.feishu.cn/docx/', ('飞书OT分享', self._parse_feishu_article, True)),
            ('.feishu.cn/community/', ('飞书社区', self._parse_feishu_community, False)),  # 飞书社区/飞行社
            ('https://m.toutiao.com/w/', ('头条OT', self._parse_toutiao_article, True)),  # 头条移动端w链接
            ('https://www.toutiao.com/video/', ('头条_video', self._parse_toutiao_video_article, True)),  # PC端
            ('https://m.toutiao.com/video/', ('头条_video', self._parse_toutiao_video_article, True)),  # 移动端
            ('https://www.toutiao.com/article/', ('头条OT', self._parse_toutiao_article, True)),  # PC端文章
            ('https://m.toutiao.com/article/', ('头条OT', self._parse_toutiao_article, True)),  # 移动端文章
            ('https://www.xiaoyuzhoufm.com/episode/', ('小宇宙OT', self._parse_xiaoyuzhou_article, True)),  # 小宇宙播客
            ('10jqka.com.cn/', ('APP-同花顺', self._parse_10jqka_article, True)),  # 同花顺
            ('watcha.cn/products/', ('Web-观猹', self._parse_watcha_article, False)),  # 观猹（JS渲染，无法提取标题）
            ('https://arxiv.org/', ('Web-arxiv', self._parse_arxiv_article, False)),  # arXiv
            ('https://clawdchat.cn/post/', ('Web_OT', self._parse_general_article, False)),  # ClawdChat
            ('https://dobby.now/community/view/', ('Web_OT', self._parse_general_article, False)),  # Dobby
            ('www.hit180.com/', ('Web-HIT专家网', self._parse_hit180_article, False)),  # HIT专家网
            ('chainthink.cn/', ('Web-ChainThink', self._parse_chainthink_article, True)),  # ChainThink
            ('https://cs.cloud.tencent.com/workbench/', ('Web_OT', self._parse_tencent_workbench, False)),  # 腾讯企点
            ('cloud.tencent.com/developer/', ('Web_腾讯云开发者社区', self._parse_tencent_developer, False)),  # 腾讯云开发者社区
            ('youtube.com/watch?v', ('Web-Youtube', self._parse_youtube_article, False)),  # YouTube
            ('bytedance.larkoffice.com/', ('飞书-字节跳动', self._parse_feishu_article, True)),  # 字节飞书
            ('garden.zsxq.com/', ('星球-AI产品经理大本营', self._parse_zsxq_garden, False)),  # 知识星球花园
            ('t.zsxq.com/', ('星球-AI产品经理大本营', self._parse_zsxq_article, False)),  # 知识星球短链
            ('articles.zsxq.com/', ('星球-AI产品经理大本营', self._parse_zsxq_article, False)),  # 知识星球文章
            ('.feishu.cn/minutes/', ('飞书会议纪要', self._parse_feishu_minutes, False)),  # 飞书会议纪要
            ('developers.weixin.qq.com/', ('微信官方文档', self._parse_weixin_devdoc, False)),  # 微信开发者文档
            ('shimo.im/', ('石墨分享', self._parse_shimo, False)),  # 石墨文档
            ('huggingface.co/', ('Web-Huggingface', self._parse_general_article, False)),  # Hugging Face
            ('modelscope.cn/', ('Web-魔搭', self._parse_general_article, False)),  # 魔搭社区
            ('github.com/', ('Web-GitHub', self._parse_general_article, False)),  # GitHub
            ('api-docs.deepseek.com/', ('Web-DeepSeek', self._parse_general_article, False)),  # DeepSeek
            ('ae.feishu.cn/', ('Web-飞书aPaaS', self._parse_general_article, False)),  # 飞书 aPaaS
            ('youtu.be/', ('Web-Youtube', self._parse_youtube_article, False)),  # YouTube 短链接
            ('x.com/', ('Web-X', self._parse_general_article, False)),  # X (Twitter)
        ]

    def parse_url(self, url: str, debug: bool = False, link_text: str = '') -> Dict:
        """解析 URL，提取文章信息

        Args:
            url: 要解析的URL
            debug: 是否输出调试信息

        Returns:
            Dict: 包含文章信息的字典
        """
        result = {
            'title': '',
            'publish_date': '',
            'weekday': '',
            'url': url,
            'source': '',
            'error_info': '',
            'remark': '',
            '_link_text': link_text,
        }

        try:
            # URL 归一化：去除各平台无意义参数
            original_url = url
            url = normalize_url(url)
            result['url'] = url

            # 遍历识别规则，找到匹配的网站
            matched_source = None
            matched_parser = None

            for url_pattern, (source_name, parser_func, _truncate) in self.site_rules:
                if url_pattern in url:
                    matched_source = source_name
                    matched_parser = parser_func
                    if debug:
                        print(f"  [DEBUG] 匹配规则: {url_pattern} -> {source_name}")
                    break

            if debug and url != original_url:
                print(f"  [DEBUG] URL归一化: {original_url} -> {url}")

            # 如果找到匹配的规则
            if matched_source and matched_parser:
                result['source'] = matched_source
                if debug:
                    print(f"  [DEBUG] 设置 source: {matched_source}")

                # 对于微信公众号，需要动态获取公众号名称
                if matched_source == '微信':
                    result = matched_parser(url, result)
                else:
                    result = matched_parser(url, result)
                    # 非微信网址，添加标识
                    if result['remark']:
                        result['remark'] = f"非微信网址；{result['remark']}"
                    else:
                        result['remark'] = '非微信网址'

                # 防御性检查：确保 source 不为空
                if not result.get('source'):
                    if debug:
                        print(f"  [DEBUG] 警告: source 被清空，恢复为: {matched_source}")
                    result['source'] = matched_source

            else:
                # 未识别的网站，尝试通用解析
                if debug:
                    print(f"  [DEBUG] 未匹配到任何规则")
                result = self._parse_general_article(url, result)
                if result['title'] and result['publish_date']:
                    # 标题和日期都提取到了，视为有效解析
                    result['source'] = 'Web_OT'
                    result['error_info'] = ''
                else:
                    result['error_info'] = '未提取来源信息'
                # 非微信网址，添加标识
                if result['remark']:
                    result['remark'] = f"非微信网址；{result['remark']}"
                else:
                    result['remark'] = '非微信网址'

            # 微信文章解析失败：来源标记为"微信X失效"
            if 'mp.weixin.qq.com/' in url and result['source'] == '微信':
                result['source'] = '微信X失效'

            result.pop('_link_text', None)
            return result

        except Exception as e:
            if debug:
                print(f"  [DEBUG] 解析异常: {e}")
            result['error_info'] = f'解析异常: {str(e)}'
            result['remark'] = f'访问异常: {str(e)}'
            result.pop('_link_text', None)
            return result

    def _parse_wechat_article(self, url: str, result: Dict) -> Dict:
        """解析微信公众号文章"""
        try:
            # 微信合辑页：提取公众号名称，标题用 link_text 或"合辑页"
            if 'mp.weixin.qq.com/mp/appmsgalbum' in url:
                return self._parse_wechat_album(url, result)

            # 对 s?__biz= 长链接，自动补 chksm 参数绕过验证
            fetch_url = url
            if 'mp.weixin.qq.com/s?' in url and '__biz=' in url and 'chksm=' not in url:
                qs = parse_qs(urlparse(url).query)
                sn = qs.get('sn', [''])[0]
                if sn:
                    chksm = hashlib.md5(sn.encode()).hexdigest()
                    fetch_url = url + '&chksm=' + chksm

            headers = dict(self.headers)
            wechat_cookie = self.credentials.get('wechat_cookie', '')
            if wechat_cookie:
                headers['Cookie'] = wechat_cookie
            response = self.session.get(fetch_url, headers=headers, timeout=10)
            response.encoding = 'utf-8'
            html_content = response.text
            soup = BeautifulSoup(html_content, 'html.parser')

            # 检测"账号已迁移"页面，提取新 URL 并重新解析
            if '账号已迁移' in html_content or '该公众号已迁移' in html_content:
                new_urls = re.findall(
                    r'https?://mp\.weixin\.qq\.com/s\?[^\"\'\s<>]+', html_content)
                new_urls = [u.replace('&amp;', '&') for u in new_urls
                            if '${' not in u]
                if new_urls:
                    new_url = new_urls[0].split('#')[0]  # 去掉 #rd fragment
                    if new_url.startswith('http://'):
                        new_url = 'https://' + new_url[7:]  # http → https
                    result['url'] = new_url
                    result['remark'] = f'账号已迁移，原链接: {url}'
                    return self._parse_wechat_article(new_url, result)
                result['error_info'] = '账号已迁移，未找到新链接'
                return result

            # 提取标题
            title_tag = soup.find('h1', class_='rich_media_title')
            if not title_tag:
                title_tag = soup.find('h1', id='activity-name')
            if title_tag:
                result['title'] = title_tag.get_text().strip()

            # 如果上面的方法没找到标题，尝试从og:title meta标签提取
            if not result['title']:
                og_title = soup.find('meta', property='og:title')
                if og_title:
                    result['title'] = og_title.get('content', '').strip()

            # 从 JavaScript 变量中提取公众号名称
            nickname_match = re.search(r'var\s+nickname\s*=\s*"([^"]+)"', html_content)
            if nickname_match:
                account_name = nickname_match.group(1).strip()
                result['source'] = f'微信-{account_name}'
            else:
                # 尝试从 nick_name 字段提取（新格式）
                nick_name_match = re.search(r'nick_name:\s*JsDecode\([\'"]([^\'"]+)[\'"]\)', html_content)
                if nick_name_match:
                    account_name = nick_name_match.group(1).strip()
                    result['source'] = f'微信-{account_name}'
                else:
                    nick_name_match = re.search(r'nick_name:\s*[\'"]([^\'"]+)[\'"]', html_content)
                    if nick_name_match:
                        account_name = nick_name_match.group(1).strip()
                        result['source'] = f'微信-{account_name}'
                    else:
                        # 备用方案：从 HTML 中提取
                        account_tag = soup.find('a', id='js_name')
                        if not account_tag:
                            account_tag = soup.find('strong', class_='profile_nickname')
                        if account_tag:
                            account_name = account_tag.get_text().strip()
                            result['source'] = f'微信-{account_name}'
                        else:
                            # 如果无法提取公众号名称，保持为"微信"
                            if not result['source']:
                                result['source'] = '微信'

            # 从 JavaScript 变量中提取发布时间戳
            # 尝试多种模式匹配
            time_match = re.search(r'"publish_time"\s*[:\}]\s*(\d{10})', html_content)
            if not time_match:
                time_match = re.search(r'publish_time%22%3A(\d{10})', html_content)
            if not time_match:
                time_match = re.search(r'var\s+ct\s*=\s*["\']?(\d{10})["\']?', html_content)
            if not time_match:
                # 尝试从 create_time 字段提取（新格式）
                time_match = re.search(r'create_time:\s*[\'"](\d{10})', html_content)

            if time_match:
                timestamp = int(time_match.group(1))
                dt = datetime.fromtimestamp(timestamp)
                result['publish_date'] = dt.strftime('%Y%m%d')  # YYYYMMDD 格式
                result['weekday'] = self._get_weekday(result['publish_date'])
            else:
                # 备用方案：从 createTime 变量提取
                create_time_match = re.search(r'var\s+createTime\s*=\s*["\'](\d+)["\']', html_content)
                if create_time_match:
                    timestamp = int(create_time_match.group(1))
                    dt = datetime.fromtimestamp(timestamp)
                    result['publish_date'] = dt.strftime('%Y%m%d')  # YYYYMMDD 格式
                    result['weekday'] = self._get_weekday(result['publish_date'])

            # 检查缺失的信息并记录到异常信息
            missing_info = []
            if not result['source']:
                missing_info.append('未提取来源')
            if not result['publish_date']:
                missing_info.append('未提取日期')
            if missing_info:
                result['error_info'] = '、'.join(missing_info)

            return result

        except Exception as e:
            result['error_info'] = f'微信文章解析失败: {str(e)}'
            result['remark'] = f'访问异常: {str(e)}'
            return result

    def _parse_wechat_album(self, url: str, result: Dict) -> Dict:
        """解析微信公众号合辑页（appmsgalbum）"""
        try:
            headers = dict(self.headers)
            wechat_cookie = self.credentials.get('wechat_cookie', '')
            if wechat_cookie:
                headers['Cookie'] = wechat_cookie
            response = self.session.get(url, headers=headers, timeout=10)
            response.encoding = 'utf-8'
            html = response.text

            # 提取公众号名称
            nick_match = re.search(r"nick_name:\s*'([^']+)'", html)
            if not nick_match:
                nick_match = re.search(r'var\s+nickname\s*=\s*"([^"]+)"', html)
            if nick_match:
                result['source'] = f'微信-{nick_match.group(1).strip()}'

            # 标题：优先用调用方传入的 link_text，否则标记为合辑页
            link_text = result.get('_link_text', '')
            if link_text:
                result['title'] = f'{link_text}（微信合辑页）'
            else:
                result['title'] = '（微信合辑页）'
            result['remark'] = '微信合辑页'
            return result
        except Exception as e:
            result['error_info'] = f'微信合辑页解析失败: {e}'
            return result

    def _parse_xiaohongshu_article(self, url: str, result: Dict) -> Dict:
        """解析小红书文章"""
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')

            # 小红书需要特殊处理，可能需要登录
            title_tag = soup.find('meta', property='og:title')
            if title_tag:
                result['title'] = title_tag.get('content', '').strip()

            if not result['title']:
                result['error_info'] = '需要登录或无法访问'
                result['remark'] = '小红书内容需要登录访问'
            else:
                # 检查缺失的信息
                missing_info = []
                if not result['publish_date']:
                    missing_info.append('未提取日期')
                if missing_info:
                    result['error_info'] = '、'.join(missing_info)

            return result

        except Exception as e:
            result['error_info'] = f'小红书解析失败: {str(e)}'
            result['remark'] = f'访问异常: {str(e)}'
            return result

    def _parse_weibo_article(self, url: str, result: Dict) -> Dict:
        """解析微博文章"""
        try:
            # 尝试使用API获取数据（使用移动端headers）
            status_id = url.split('/')[-1].split('?')[0]
            api_url = f'https://m.weibo.cn/statuses/show?id={status_id}'

            response = requests.get(api_url, headers=self.mobile_headers, timeout=10)

            # 尝试解析JSON
            try:
                data = response.json()
                if 'data' in data:
                    status = data['data']

                    # 提取文本（第一行作为标题）
                    if 'text' in status:
                        text_html = status['text']
                        text_soup = BeautifulSoup(text_html, 'html.parser')
                        text = text_soup.get_text().strip()
                        # 取第一行作为标题
                        first_line = text.split('\n')[0].strip()
                        if first_line:
                            result['title'] = first_line

                    # 提取日期
                    if 'created_at' in status:
                        created_at = status['created_at']
                        # 解析微博时间格式，如 "Fri Feb 07 12:34:56 +0800 2026"
                        try:
                            dt = datetime.strptime(created_at, '%a %b %d %H:%M:%S %z %Y')
                            result['publish_date'] = dt.strftime('%Y%m%d')
                            result['weekday'] = self._get_weekday(result['publish_date'])
                        except:
                            pass

            except:
                # JSON解析失败，可能需要登录
                pass

            # 如果没有获取到标题，尝试从HTML页面获取
            if not result['title']:
                response = requests.get(url, headers=self.mobile_headers, timeout=10)
                soup = BeautifulSoup(response.text, 'html.parser')

                # 微博需要特殊处理
                title_tag = soup.find('meta', property='og:title')
                if title_tag:
                    result['title'] = title_tag.get('content', '').strip()

            if not result['title']:
                result['error_info'] = '需要登录或无法访问'
                result['remark'] = '微博内容需要登录访问'
            else:
                # 检查缺失的信息
                missing_info = []
                if not result['publish_date']:
                    missing_info.append('未提取日期')
                if missing_info:
                    result['error_info'] = '、'.join(missing_info)

            # 确保 source 不为空（防御性编程）
            if not result.get('source'):
                result['source'] = 'App-微博'

            return result

        except Exception as e:
            result['error_info'] = f'微博解析失败: {str(e)}'
            result['remark'] = f'访问异常: {str(e)}'
            # 确保 source 不为空（防御性编程）
            if not result.get('source'):
                result['source'] = 'App-微博'
            return result

    def _parse_watcha_article(self, url: str, result: Dict) -> Dict:
        """解析观猹文章（JS渲染页面，无法提取标题和日期）"""
        result['title'] = ''
        result['publish_date'] = ''
        result['error_info'] = 'JS渲染页面、未提取标题、未提取日期'
        return result

    def _parse_zsxq_garden(self, url: str, result: Dict) -> Dict:
        """解析知识星球花园页面（需确保路径末尾有 /）"""
        if not url.endswith('/'):
            url += '/'
        try:
            resp = requests.get(url, headers=self.headers, timeout=10,
                                allow_redirects=False)
            if resp.status_code in (301, 302):
                resp = requests.get(url, headers=self.headers, timeout=10)
            resp.encoding = 'utf-8'
            soup = BeautifulSoup(resp.text, 'html.parser')
            title_tag = soup.find('title')
            if title_tag:
                result['title'] = title_tag.get_text().strip()
            if not result['title']:
                result['error_info'] = '未提取标题'
        except Exception as e:
            result['error_info'] = f'花园页面解析失败: {e}'
        return result

    def _parse_zsxq_article(self, url: str, result: Dict) -> Dict:
        """解析知识星球链接（通过 API 获取标题和日期）"""
        zsxq_token = self.credentials.get('zsxq_token', '')
        if not zsxq_token:
            result['error_info'] = '知识星球需登录、未配置access_token'
            return result
        # 提取 topic_id（短链需先解析重定向）
        topic_id = ''
        m_topic = re.search(r'/topic/(\d+)', url)
        if m_topic:
            topic_id = m_topic.group(1)
        elif 't.zsxq.com/' in url:
            location = self._resolve_short_url(url)
            if 'topic_id' in location:
                params = parse_qs(urlparse(location).query)
                topic_id = params.get('topic_id', [''])[0]
        if not topic_id:
            result['error_info'] = '无法从URL提取topic_id'
            return result
        try:
            headers = {'Cookie': f'zsxq_access_token={zsxq_token}',
                       'User-Agent': 'Mozilla/5.0'}
            data = None
            for attempt in range(5):
                resp = requests.get(f'https://api.zsxq.com/v2/topics/{topic_id}',
                                    headers=headers, timeout=10)
                data = resp.json()
                if data.get('succeeded'):
                    break
                if attempt < 4:
                    time.sleep(2 * (attempt + 1))
            if data and data.get('succeeded'):
                topic = data['resp_data']['topic']
                talk = topic.get('talk', {})
                # 优先使用 article.title（长文章标题）
                article_title = talk.get('article', {}).get('title', '').strip()
                if article_title.startswith('【') and article_title.endswith('】'):
                    article_title = article_title[1:-1]
                if article_title:
                    result['title'] = article_title
                else:
                    # 其次使用 talk.text 第一行
                    text = talk.get('text', '')
                    text = re.sub(r'<e [^>]+/>', '', text)
                    first_line = text.strip().split('\n')[0].strip()
                    if first_line.startswith('【') and first_line.endswith('】'):
                        first_line = first_line[1:-1]
                    if first_line:
                        result['title'] = first_line
                    else:
                        # 最后 fallback 到链接文字
                        link_text = result.get('_link_text', '')
                        if link_text and len(link_text) > 3:
                            result['title'] = link_text
                create_time = topic.get('create_time', '')
                if create_time:
                    result['publish_date'] = self._parse_date(create_time)
                    result['weekday'] = self._get_weekday(result['publish_date'])
            else:
                result['error_info'] = '知识星球API返回失败'
        except Exception as e:
            result['error_info'] = f'知识星球API失败: {e}'
        return result

    def _parse_arxiv_article(self, url: str, result: Dict) -> Dict:
        """解析 arXiv 论文页面（支持 PDF URL 自动转 abs）"""
        try:
            # PDF URL 转 abs
            fetch_url = url
            if 'arxiv.org/pdf/' in url:
                fetch_url = url.replace('arxiv.org/pdf/', 'arxiv.org/abs/')
            response = requests.get(fetch_url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')

            # 标题：去掉 [arxiv_id] 前缀
            title_tag = soup.find('title')
            if title_tag:
                title = title_tag.get_text().strip()
                title = re.sub(r'^\[\d+\.\d+\]\s*', '', title)
                result['title'] = title

            # 日期：优先从 submission history 取最新版本日期
            latest_date = self._get_arxiv_latest_date(soup)
            if latest_date:
                result['publish_date'] = latest_date
                result['weekday'] = self._get_weekday(latest_date)
            else:
                # fallback: citation_date meta 标签
                date_meta = soup.find('meta', attrs={'name': 'citation_date'})
                if date_meta:
                    date_str = date_meta.get('content', '')
                    result['publish_date'] = self._parse_date(date_str.replace('/', '-'))
                    result['weekday'] = self._get_weekday(result['publish_date'])

            if not result['title']:
                result['error_info'] = '未提取标题'
            if not result['publish_date']:
                result['error_info'] = (result['error_info'] + '、未提取日期').lstrip('、')
            return result
        except Exception as e:
            result['error_info'] = f'arxiv解析失败: {e}'
            return result

    def _parse_tencent_workbench(self, url: str, result: Dict) -> Dict:
        """解析腾讯企点链接"""
        result['error_info'] = 'JS渲染页面、未提取标题、未提取日期'
        return result

    def _parse_youtube_article(self, url: str, result: Dict) -> Dict:
        """解析 YouTube 链接"""
        # 尝试从 YouTube 页面提取日期
        try:
            resp = requests.get(url, headers=self.headers, timeout=10)
            html = resp.text

            # 尝试多种 YouTube 日期格式
            date_patterns = [
                r'"dateText":\{"simpleText":"(\d+年\d+月\d+日)"',
                r'"publishedTimeText":"([^"]+)"',
                r'"uploadDate":"([^"]+)"',
            ]
            for pat in date_patterns:
                m = re.search(pat, html)
                if m:
                    date_str = m.group(1)
                    parsed = self._parse_date(date_str)
                    if parsed:
                        result['publish_date'] = parsed
                        result['weekday'] = self._get_weekday(parsed)
                        break
        except Exception:
            pass

        if not result.get('publish_date'):
            result['error_info'] = 'JS渲染页面、未提取日期'
        return self._parse_general_article(url, result)

    def _parse_feishu_community(self, url: str, result: Dict) -> Dict:
        """解析飞书社区（飞行社）页面"""
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            html = response.text

            # 从内嵌 JS 提取 name（标题）
            name_match = re.search(r'"name":"([^"]+)"', html)
            if name_match:
                result['title'] = name_match.group(1)

            # 优先更新时间，回退创建时间
            time_match = (re.search(r'"updateTime":(\d{13})', html)
                          or re.search(r'"createTime":(\d{13})', html))
            if time_match:
                ts = int(time_match.group(1)) / 1000
                dt = datetime.fromtimestamp(ts)
                result['publish_date'] = dt.strftime('%Y%m%d')
                result['weekday'] = self._get_weekday(result['publish_date'])

            # 回退：从 <title> 提取标题
            if not result['title']:
                title_match = re.search(r'<title[^>]*>([^<]+)</title>', html)
                if title_match:
                    title = title_match.group(1).strip()
                    # 去掉【飞行社】前缀
                    title = re.sub(r'^【飞行社】', '', title)
                    result['title'] = title

            # 检查缺失信息
            missing = []
            if not result['title']:
                missing.append('未提取标题')
            if not result['publish_date']:
                missing.append('未提取日期')
            if missing:
                result['error_info'] = '、'.join(missing)

            return result

        except Exception as e:
            result['error_info'] = f'飞书社区解析失败: {str(e)}'
            result['remark'] = f'访问异常: {str(e)}'
            return result

    def _parse_tencent_developer(self, url: str, result: Dict) -> Dict:
        """解析腾讯云开发者社区文章"""
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.text, 'html.parser')

            # 提取标题
            title_tag = soup.find('title')
            if title_tag:
                title = title_tag.get_text().strip()
                # 去掉后缀 "-腾讯云开发者社区-腾讯云"
                title = re.sub(r'-腾讯云开发者社区.*$', '', title)
                result['title'] = title.strip()

            # 提取日期：从 class="date-text" 的 span 中提取
            date_span = soup.find('span', class_='date-text')
            if date_span:
                date_text = date_span.get_text().strip()
                date_match = re.search(r'(\d{4}-\d{2}-\d{2})', date_text)
                if date_match:
                    result['publish_date'] = date_match.group(1).replace('-', '')
                    result['weekday'] = self._get_weekday(result['publish_date'])

            # 检查缺失信息
            missing = []
            if not result['title']:
                missing.append('未提取标题')
            if not result['publish_date']:
                missing.append('未提取日期')
            if missing:
                result['error_info'] = '、'.join(missing)

            return result

        except Exception as e:
            result['error_info'] = f'腾讯云开发者社区解析失败: {str(e)}'
            result['remark'] = f'访问异常: {str(e)}'
            return result

    def _parse_hit180_article(self, url: str, result: Dict) -> Dict:
        """解析HIT专家网文章"""
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.encoding = response.apparent_encoding
            soup = BeautifulSoup(response.text, 'html.parser')

            # 提取标题
            title_tag = soup.find('title')
            if title_tag:
                result['title'] = title_tag.get_text().strip()

            # 提取日期：<span class="item">2026-03-08</span>
            item_spans = soup.find_all('span', class_='item')
            for span in item_spans:
                date_match = re.search(r'\d{4}-\d{2}-\d{2}', span.get_text())
                if date_match:
                    result['publish_date'] = date_match.group().replace('-', '')
                    result['weekday'] = self._get_weekday(result['publish_date'])
                    break

            missing = []
            if not result['title']:
                missing.append('未提取标题')
            if not result['publish_date']:
                missing.append('未提取日期')
            if missing:
                result['error_info'] = '、'.join(missing)

            return result

        except Exception as e:
            result['error_info'] = f'HIT专家网解析失败: {str(e)}'
            result['remark'] = f'访问异常: {str(e)}'
            return result

    def _parse_chainthink_article(self, url: str, result: Dict) -> Dict:
        """解析ChainThink文章"""
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.text, 'html.parser')

            # 从 ld+json 提取标题和日期
            import json as _json
            for script in soup.find_all('script', type='application/ld+json'):
                if not script.string:
                    continue
                data = _json.loads(script.string)
                if data.get('@type') == 'Article':
                    if data.get('headline'):
                        result['title'] = data['headline']
                    if data.get('datePublished'):
                        result['publish_date'] = self._parse_date(data['datePublished'])
                        result['weekday'] = self._get_weekday(result['publish_date'])
                    break

            # 回退：从 <title> 提取标题
            if not result['title']:
                title_tag = soup.find('title')
                if title_tag:
                    result['title'] = title_tag.get_text().strip()

            missing = []
            if not result['title']:
                missing.append('未提取标题')
            if not result['publish_date']:
                missing.append('未提取日期')
            if missing:
                result['error_info'] = '、'.join(missing)

            return result

        except Exception as e:
            result['error_info'] = f'ChainThink解析失败: {str(e)}'
            result['remark'] = f'访问异常: {str(e)}'
            return result

    def _parse_general_article(self, url: str, result: Dict) -> Dict:
        """解析通用文章"""
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.encoding = response.apparent_encoding
            soup = BeautifulSoup(response.text, 'html.parser')

            # 尝试提取标题
            title_tag = soup.find('title')
            if not title_tag:
                title_tag = soup.find('h1')
            if title_tag:
                result['title'] = title_tag.get_text().strip()

            # 尝试提取日期
            date_patterns = [
                soup.find('meta', property='article:published_time'),
                soup.find('time'),
                soup.find('span', class_=re.compile(r'date|time', re.I))
            ]
            for date_tag in date_patterns:
                if date_tag:
                    date_str = date_tag.get('content') or date_tag.get('datetime') or date_tag.get_text()
                    if date_str:
                        result['publish_date'] = self._parse_date(date_str.strip())
                        result['weekday'] = self._get_weekday(result['publish_date'])
                        break

            # 检查缺失的信息
            missing_info = []
            if not result['title']:
                missing_info.append('未提取标题')
            if not result['publish_date']:
                missing_info.append('未提取日期')
            if missing_info:
                if result['error_info']:
                    result['error_info'] += '、' + '、'.join(missing_info)
                else:
                    result['error_info'] = '、'.join(missing_info)

            return result

        except Exception as e:
            result['error_info'] = f'通用解析失败: {str(e)}'
            result['remark'] = f'访问异常: {str(e)}'
            return result

    def _parse_date(self, date_str: str) -> str:
        """解析日期字符串，返回 YYYYMMDD 格式"""
        try:
            # 尝试多种日期格式
            date_formats = [
                '%Y-%m-%d',
                '%Y/%m/%d',
                '%Y年%m月%d日',
                '%Y年%m月%d',
                '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%dT%H:%M:%S',
                '%Y%m%d',
                '%b %d, %Y',      # Feb 24, 2025
                '%B %d, %Y',      # February 24, 2025
                '%d %b %Y',       # 24 Feb 2025
                '%d %B %Y',       # 24 February 2025
                '%b %d %Y',       # Feb 24 2025
                '%B %d %Y',       # February 24 2025
                '%Y年%m月',       # 2025年02月 (无日期)
            ]

            for fmt in date_formats:
                try:
                    dt = datetime.strptime(date_str[:19], fmt)
                    return dt.strftime('%Y%m%d')  # 改为 YYYYMMDD 格式
                except:
                    continue

            # 如果都失败，返回空字符串
            return ''

        except:
            return ''

    def _get_weekday(self, date_str: str) -> str:
        """获取星期几，输入格式为 YYYYMMDD"""
        try:
            if not date_str:
                return ''
            dt = datetime.strptime(date_str, '%Y%m%d')
            weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
            return weekdays[dt.weekday()]
        except:
            return ''

    def _parse_toutiao_video_article(self, url: str, result: Dict) -> Dict:
        """解析头条视频文章"""
        try:
            # 如果是PC端URL，转换为移动端URL
            if 'https://www.toutiao.com/' in url:
                url = url.replace('https://www.toutiao.com/', 'https://m.toutiao.com/')

            # 使用移动端User-Agent
            response = requests.get(url, headers=self.mobile_headers, timeout=10)
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')

            # 头条视频的数据在script标签中，以URL编码的JSON格式存储
            scripts = soup.find_all('script')

            for script in scripts:
                if script.string and len(script.string) > 1000:
                    # 尝试URL解码
                    try:
                        decoded = unquote(script.string)

                        # 尝试解析为JSON
                        data = json.loads(decoded)

                        # 查找articleInfo
                        if 'articleInfo' in data:
                            article_info = data['articleInfo']

                            # 提取标题
                            if 'title' in article_info:
                                title = article_info['title']
                                # 去掉可能的后缀（如" - 今日头条"）
                                if ' - 今日头条' in title:
                                    title = title.replace(' - 今日头条', '')
                                result['title'] = title

                            # 提取发布时间
                            if 'publishTime' in article_info:
                                timestamp = article_info['publishTime']
                                # 可能是字符串或整数
                                if isinstance(timestamp, str):
                                    timestamp = int(timestamp)
                                if isinstance(timestamp, int) and timestamp > 1000000000:
                                    # 如果是毫秒时间戳，转换为秒
                                    if timestamp > 10000000000:
                                        timestamp = timestamp // 1000
                                    dt = datetime.fromtimestamp(timestamp)
                                    result['publish_date'] = dt.strftime('%Y%m%d')
                                    result['weekday'] = self._get_weekday(result['publish_date'])

                            # 找到数据就退出
                            if result['title'] and result['publish_date']:
                                break
                    except:
                        # 如果解析失败，继续尝试下一个script
                        continue

            # 如果上面的方法没有成功，尝试从meta标签或第一个script（VideoObject）提取
            if not result['title']:
                # 尝试从第一个script标签（VideoObject）提取
                if len(scripts) > 0 and scripts[0].string:
                    try:
                        data = json.loads(scripts[0].string)
                        if 'name' in data:
                            title = data['name']
                            # 去掉可能的后缀
                            if ' - 今日头条' in title:
                                title = title.replace(' - 今日头条', '')
                            result['title'] = title

                        # VideoObject中可能有uploadDate
                        if 'uploadDate' in data:
                            date_str = data['uploadDate']
                            # 解析ISO格式日期
                            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                            result['publish_date'] = dt.strftime('%Y%m%d')
                            result['weekday'] = self._get_weekday(result['publish_date'])
                    except:
                        pass

                # 如果还是没有，尝试从meta标签提取
                if not result['title']:
                    og_title = soup.find('meta', property='og:title')
                    if og_title:
                        result['title'] = og_title.get('content', '').strip()

            # 检查缺失的信息
            if not result['title']:
                result['error_info'] = '未提取标题'
                result['remark'] = '头条视频内容无法访问'
            else:
                missing_info = []
                if not result['publish_date']:
                    missing_info.append('未提取日期')
                if missing_info:
                    result['error_info'] = '、'.join(missing_info)

            return result

        except Exception as e:
            result['error_info'] = f'头条视频解析失败: {str(e)}'
            result['remark'] = f'访问异常: {str(e)}'
            return result

    def _parse_feishu_record(self, url: str, result: Dict) -> Dict:
        """解析飞书多维表格 /record/ 分享链接
        从页面内嵌的 shareRecord JSON 提取记录字段信息
        """
        try:
            resp = requests.get(url, headers=self.headers, timeout=15)
            html = resp.text
        except Exception:
            result['error_info'] = '网络请求失败'
            return result

        m = re.search(
            r'window\.SERVER_DATA\.shareRecord\s*=\s*Object\((\{.*?\})\)',
            html, re.DOTALL)
        if not m:
            result['error_info'] = '未找到 shareRecord 数据'
            return result

        try:
            data = json.loads(m.group(1))
            rs = json.loads(data.get('RecordShare', '{}'))
        except (json.JSONDecodeError, TypeError):
            result['error_info'] = 'shareRecord JSON 解析失败'
            return result

        # 字段名映射: field_id -> name
        field_map = rs.get('fieldMap', {})
        fname_map = {fid: fi.get('name', fid) for fid, fi in field_map.items()}

        # 从 recordData 提取字段值
        record_data = rs.get('recordData', {})
        fields = {}
        for fid, cell in record_data.items():
            name = fname_map.get(fid, fid)
            val = cell.get('value')
            if isinstance(val, list):
                texts = [it.get('text', '').strip() for it in val
                         if isinstance(it, dict) and it.get('text', '').strip()]
                fields[name] = ' '.join(texts) if texts else None
            elif isinstance(val, (int, float)):
                fields[name] = val
            elif isinstance(val, str):
                fields[name] = val
            else:
                fields[name] = None

        # 标题：优先"报告标题"等含"标题"的字段，其次主键字段
        title = None
        for fname, fval in fields.items():
            if '标题' in fname and fval:
                title = str(fval)
                break
        if not title:
            primary_key = rs.get('primaryKey', '')
            pk_name = fname_map.get(primary_key, '')
            if pk_name and fields.get(pk_name):
                title = str(fields[pk_name])
        if not title:
            # 取第一个非空文本字段
            for fval in fields.values():
                if isinstance(fval, str) and fval:
                    title = fval[:100]
                    break
        result['title'] = title or ''

        # 日期：优先含"时间"/"日期"的字段
        for fname, fval in fields.items():
            if ('时间' in fname or '日期' in fname) and fval:
                ts = int(fval)
                if ts > 10000000000:
                    ts = ts // 1000
                if ts > 946684800:
                    dt = datetime.fromtimestamp(ts)
                    result['publish_date'] = dt.strftime('%Y%m%d')
                    result['weekday'] = self._get_weekday(result['publish_date'])
                    break

        # 补充 remark：表格名称
        table_name = rs.get('tableName', '')
        base_name = rs.get('baseName', '')
        if base_name or table_name:
            result['remark'] = f'{base_name} / {table_name}'.strip(' /')

        if not result['title']:
            result['error_info'] = '未提取标题'
        if not result['publish_date']:
            err = result.get('error_info', '')
            result['error_info'] = f'{err}、未提取日期'.strip('、')

        return result

    def _parse_feishu_base(self, url: str, result: Dict) -> Dict:
        """解析飞书多维表格 /base/ 首页链接
        从 SERVER_DATA.meta 提取标题，从 HTML 中提取 edit_time 作为日期
        """
        try:
            resp = requests.get(url, headers=self.headers, timeout=15)
            html = resp.text
        except Exception:
            result['error_info'] = '网络请求失败'
            return result

        m = re.search(
            r'window\.SERVER_DATA\s*=\s*Object\((\{.*?\})\)\s*;',
            html, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                meta = data.get('meta', {})
                result['title'] = meta.get('title', '')
            except (json.JSONDecodeError, TypeError):
                pass

        # 从 HTML 提取时间戳（优先更新时间，回退创建时间）
        et = None
        for time_key in ['"edit_time"', '"update_time"', '"create_time"']:
            m_time = re.search(time_key + r':(\d{10,13})', html)
            if m_time:
                et = m_time
                break
        if et:
            ts = int(et.group(1))
            if ts > 10000000000:
                ts = ts // 1000
            if ts > 946684800:
                dt = datetime.fromtimestamp(ts)
                result['publish_date'] = dt.strftime('%Y%m%d')
                result['weekday'] = self._get_weekday(result['publish_date'])

        result['remark'] = '飞书多维表格'

        if not result['title']:
            result['error_info'] = '未提取标题'
        if not result['publish_date']:
            err = result.get('error_info', '')
            result['error_info'] = f'{err}、未提取日期'.strip('、')

        return result

    def _parse_feishu_article(self, url: str, result: Dict) -> Dict:
        """解析飞书文档
        优先用飞书 API 获取标题和更新时间，API 不可用时回退 HTML 抓取
        """
        # 从 URL 提取 doc_token
        doc_token = url.rstrip('/').split('/')[-1].split('?')[0]

        # --- 策略一：飞书 API ---
        user_token = self.credentials.get('feishu_user_token', '')
        if user_token and doc_token:
            api_ok = self._parse_feishu_via_api(url, doc_token, user_token, result)
            if api_ok:
                return result

        # --- 策略二：HTML 抓取（回退） ---
        return self._parse_feishu_via_html(url, result)

    def _parse_feishu_via_api(self, url, doc_token, user_token, result):
        """通过飞书 drive meta API 获取文档标题和更新时间
        Returns: True 如果成功获取到标题
        """
        api_headers = {
            'Authorization': f'Bearer {user_token}',
            'Content-Type': 'application/json'
        }
        try:
            resp = requests.post(
                'https://open.feishu.cn/open-apis/drive/v1/metas/batch_query',
                headers=api_headers, timeout=10,
                json={'request_docs': [
                    {'doc_token': doc_token, 'doc_type': self._guess_feishu_doc_type(url)}
                ], 'with_url': False}
            )
            data = resp.json()
            if data.get('code') == 0:
                metas = data.get('data', {}).get('metas', [])
                if metas:
                    meta = metas[0]
                    result['title'] = meta.get('title', '')
                    edit_time = meta.get('latest_modify_time') or meta.get('create_time', '')
                    if edit_time:
                        ts = int(edit_time)
                        if ts > 10000000000:
                            ts = ts // 1000
                        dt = datetime.fromtimestamp(ts)
                        result['publish_date'] = dt.strftime('%Y%m%d')
                        result['weekday'] = self._get_weekday(result['publish_date'])

            if result['title']:
                if not result['publish_date']:
                    result['error_info'] = '未提取日期'
                return True
            return False
        except Exception:
            return False

    @staticmethod
    def _guess_feishu_doc_type(url):
        """从 URL 推断飞书文档类型"""
        if '/docx/' in url:
            return 'docx'
        if '/wiki/' in url:
            return 'wiki'
        if '/sheets/' in url:
            return 'sheet'
        if '/record/' in url:
            return 'bitable'
        return 'docx'

    def _parse_feishu_via_html(self, url, result):
        """通过 HTML 抓取飞书文档信息（回退方案）"""
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')
            scripts = soup.find_all('script')

            for script in scripts:
                if script.string and len(script.string) > 1000:
                    if '"title"' in script.string and 'update_time' in script.string:
                        title_match = re.search(r'"title"\s*:\s*"([^"]+)"', script.string)
                        time_patterns = [
                            r'"update_time"\s*:\s*(\d{10,})',
                            r'"updateTime"\s*:\s*(\d{10,})',
                            r'"create_time"\s*:\s*(\d{10,})',
                            r'"createTime"\s*:\s*(\d{10,})',
                        ]
                        time_match = None
                        for tp in time_patterns:
                            time_match = re.search(tp, script.string)
                            if time_match:
                                timestamp = int(time_match.group(1))
                                if timestamp > 946684800:
                                    break
                                else:
                                    time_match = None
                        if title_match and time_match:
                            result['title'] = title_match.group(1)
                            timestamp = int(time_match.group(1))
                            if timestamp > 10000000000:
                                timestamp = timestamp // 1000
                            dt = datetime.fromtimestamp(timestamp)
                            result['publish_date'] = dt.strftime('%Y%m%d')
                            result['weekday'] = self._get_weekday(result['publish_date'])
                            break

            if not result['title']:
                for script in scripts:
                    if script.string and len(script.string) > 1000:
                        if '"title"' in script.string:
                            title_match = re.search(r'"title"\s*:\s*"([^"]+)"', script.string)
                            if title_match:
                                result['title'] = title_match.group(1)
                                break

            if not result['title']:
                result['error_info'] = '未提取标题'
            elif not result['publish_date']:
                result['error_info'] = '未提取日期'
            return result
        except Exception as e:
            result['error_info'] = f'飞书文档解析失败: {str(e)}'
            return result

    def _parse_toutiao_article(self, url: str, result: Dict) -> Dict:
        """解析头条文章（非视频）"""
        try:
            # 如果是PC端URL，转换为移动端URL
            if 'https://www.toutiao.com/' in url:
                url = url.replace('https://www.toutiao.com/', 'https://m.toutiao.com/')

            # 使用移动端User-Agent
            response = requests.get(url, headers=self.mobile_headers, timeout=10)
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')

            # 首先尝试使用正则表达式直接提取URL编码的标题
            # 格式：title%22%3A%22[title]%22
            encoded_title_match = re.search(r'title%22%3A%22([^%]+(?:%[0-9A-F]{2}[^%]*)*?)%22', html)
            if encoded_title_match:
                title = unquote(encoded_title_match.group(1))
                # 清理标题：去掉作者和平台信息
                # 格式通常是：标题_作者_平台 或 标题 - 平台
                if '_' in title:
                    # 取第一个下划线之前的内容
                    title = title.split('_')[0]
                if ' - 今日头条' in title:
                    title = title.replace(' - 今日头条', '')
                if ' - 微头条' in title:
                    title = title.replace(' - 微头条', '')
                result['title'] = title.strip()

            # 提取URL编码的publishTime
            # 格式：publishTime%22%3A%22[timestamp]
            encoded_time_match = re.search(r'publishTime%22%3A%22(\d{10})', html)
            if encoded_time_match:
                timestamp = int(encoded_time_match.group(1))
                if timestamp > 1000000000:
                    dt = datetime.fromtimestamp(timestamp)
                    result['publish_date'] = dt.strftime('%Y%m%d')
                    result['weekday'] = self._get_weekday(result['publish_date'])

            # 如果上面没找到，尝试解析JSON
            if not result['publish_date']:
                scripts = soup.find_all('script')
                for script in scripts:
                    if script.string and len(script.string) > 1000:
                        # 尝试URL解码
                        try:
                            decoded = unquote(script.string)

                            # 尝试解析为JSON
                            data = json.loads(decoded)

                            # 查找articleInfo
                            if 'articleInfo' in data:
                                article_info = data['articleInfo']

                                # 提取标题
                                if 'title' in article_info:
                                    title = article_info['title']
                                    # 去掉可能的后缀（如" - 今日头条"）
                                    if ' - 今日头条' in title:
                                        title = title.replace(' - 今日头条', '')
                                    result['title'] = title

                                # 提取发布时间
                                if 'publishTime' in article_info:
                                    timestamp = article_info['publishTime']
                                    # 可能是字符串或整数
                                    if isinstance(timestamp, str):
                                        timestamp = int(timestamp)
                                    if isinstance(timestamp, int) and timestamp > 1000000000:
                                        # 如果是毫秒时间戳，转换为秒
                                        if timestamp > 10000000000:
                                            timestamp = timestamp // 1000
                                        dt = datetime.fromtimestamp(timestamp)
                                        result['publish_date'] = dt.strftime('%Y%m%d')
                                        result['weekday'] = self._get_weekday(result['publish_date'])

                                # 找到数据就退出
                                if result['title'] and result['publish_date']:
                                    break
                        except:
                            # 如果解析失败，继续尝试下一个script
                            continue

            # 如果上面的方法没有成功，尝试从meta标签提取标题
            if not result['title']:
                og_title = soup.find('meta', property='og:title')
                if og_title:
                    title = og_title.get('content', '').strip()
                    # 去掉可能的后缀
                    if ' - 今日头条' in title:
                        title = title.replace(' - 今日头条', '')
                    result['title'] = title

            # 检查缺失的信息
            if not result['title']:
                result['error_info'] = '未提取标题'
                result['remark'] = '头条文章内容无法访问'
            else:
                missing_info = []
                if not result['publish_date']:
                    missing_info.append('未提取日期')
                if missing_info:
                    result['error_info'] = '、'.join(missing_info)

            return result

        except Exception as e:
            result['error_info'] = f'头条文章解析失败: {str(e)}'
            result['remark'] = f'访问异常: {str(e)}'
            return result

    def _parse_xiaoyuzhou_article(self, url: str, result: Dict) -> Dict:
        """解析小宇宙播客"""
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.encoding = 'utf-8'
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')

            # 尝试从meta标签提取标题
            og_title = soup.find('meta', property='og:title')
            if og_title:
                result['title'] = og_title.get('content', '').strip()

            # 如果meta标签没有，尝试从title标签提取
            if not result['title']:
                title_tag = soup.find('title')
                if title_tag:
                    result['title'] = title_tag.get_text().strip()

            # 尝试从script标签中提取JSON数据
            scripts = soup.find_all('script', type='application/ld+json')
            for script in scripts:
                if script.string:
                    try:
                        data = json.loads(script.string)

                        # 提取标题
                        if 'name' in data and not result['title']:
                            result['title'] = data['name']

                        # 提取发布日期
                        if 'datePublished' in data:
                            date_str = data['datePublished']
                            # 解析ISO格式日期，如 "2024-01-15T10:30:00Z"
                            try:
                                dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                                result['publish_date'] = dt.strftime('%Y%m%d')
                                result['weekday'] = self._get_weekday(result['publish_date'])
                            except:
                                pass

                        # 如果找到了标题和日期，就退出
                        if result['title'] and result['publish_date']:
                            break
                    except:
                        continue

            # 检查缺失的信息
            missing_info = []
            if not result['title']:
                missing_info.append('未提取标题')
            if not result['publish_date']:
                missing_info.append('未提取日期')
            if missing_info:
                result['error_info'] = '、'.join(missing_info)

            return result

        except Exception as e:
            result['error_info'] = f'小宇宙播客解析失败: {str(e)}'
            result['remark'] = f'访问异常: {str(e)}'
            return result

    def _parse_bilibili_article(self, url: str, result: Dict) -> Dict:
        """解析哔哩哔哩视频/动态"""
        try:
            response = requests.get(url, headers=self.mobile_headers, timeout=10)
            response.encoding = 'utf-8'
            html = response.text

            # 日期方式1：pubdate 时间戳（视频页）
            pubdate_match = re.search(r'"pubdate":(\d{10})', html)
            if pubdate_match:
                timestamp = int(pubdate_match.group(1))
                if timestamp > 1000000000:
                    dt = datetime.fromtimestamp(timestamp)
                    result['publish_date'] = dt.strftime('%Y%m%d')
                    result['weekday'] = self._get_weekday(result['publish_date'])

            # 日期方式2：pub_time 中文格式（opus 动态页）
            if not result['publish_date']:
                pub_time_match = re.search(r'"pub_time":"(\d{4}年\d{2}月\d{2}日)', html)
                if pub_time_match:
                    date_str = pub_time_match.group(1)
                    result['publish_date'] = self._parse_date(date_str)
                    result['weekday'] = self._get_weekday(result['publish_date'])

            # 标题：优先 <title>，去掉哔哩哔哩后缀
            soup = BeautifulSoup(html, 'html.parser')
            title_tag = soup.find('title')
            if title_tag:
                title = title_tag.get_text().strip()
                for suffix in ('_哔哩哔哩_bilibili', '_bilibili_哔哩哔哩', ' - 哔哩哔哩'):
                    title = title.replace(suffix, '')
                # opus 动态页 title 是作者名，无意义，改从正文第一行提取
                if '的动态' in title or not title:
                    first_line = re.search(r'"words":"([^"]{5,100})', html)
                    if first_line:
                        title = first_line.group(1).split('\\n')[0]
                result['title'] = title.strip()

            # 检查缺失的信息
            missing_info = []
            if not result['title']:
                missing_info.append('未提取标题')
            if not result['publish_date']:
                missing_info.append('未提取日期')
            if missing_info:
                result['error_info'] = '、'.join(missing_info)

            return result

        except Exception as e:
            result['error_info'] = f'哔哩哔哩解析失败: {str(e)}'
            result['remark'] = f'访问异常: {str(e)}'
            return result

    def _parse_10jqka_article(self, url: str, result: Dict) -> Dict:
        """解析同花顺文章"""
        # /m/post 路径走专用 API
        if '/m/post' in url:
            return self._parse_10jqka_post(url, result)
        # 其他路径走通用解析
        result = self._parse_general_article(url, result)
        # 日期回退：从 URL 路径提取 /YYYYMMDD/
        if not result['publish_date']:
            date_match = re.search(r'/(\d{8})/', url)
            if date_match:
                result['publish_date'] = date_match.group(1)
                result['weekday'] = self._get_weekday(result['publish_date'])
                result['error_info'] = result['error_info'].replace('未提取日期', '').strip('、')
        return result

    def _parse_10jqka_post(self, url: str, result: Dict) -> Dict:
        """解析同花顺帖子（通过API获取数据）"""
        try:
            # 从URL中提取pid参数
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            pid = params.get('pid', [None])[0]
            if not pid:
                result['error_info'] = '未找到pid参数'
                return result

            # 调用同花顺分享数据API
            api_url = f"https://t.10jqka.com.cn/m/post/getPostShareData/?pid={pid}"
            response = requests.get(api_url, headers=self.mobile_headers, timeout=10)
            data = response.json()

            if data.get('errorCode') != 0:
                result['error_info'] = f"API返回错误: {data.get('errorMsg')}"
                return result

            post = data.get('result', {}).get('data', {}).get('post', {})

            # 提取标题
            title = post.get('title', '')
            if title:
                result['title'] = title

            # 提取日期：格式为 "02-12 19:35"，需要补充年份
            time_str = post.get('time', '')
            if time_str:
                try:
                    now = datetime.now()
                    date_part = time_str.split(' ')[0]  # "02-12"
                    month, day = date_part.split('-')
                    # 推算年份：如果月份大于当前月份，可能是去年的
                    year = now.year
                    if int(month) > now.month:
                        year -= 1
                    result['publish_date'] = f"{year}{month.zfill(2)}{day.zfill(2)}"
                    result['weekday'] = self._get_weekday(result['publish_date'])
                except (ValueError, IndexError):
                    result['error_info'] = f'日期解析失败: {time_str}'

            # 检查缺失的信息
            missing_info = []
            if not result['title']:
                missing_info.append('未提取标题')
            if not result['publish_date']:
                missing_info.append('未提取日期')
            if missing_info:
                result['error_info'] = '、'.join(missing_info)

            return result

        except Exception as e:
            result['error_info'] = f'同花顺解析失败: {str(e)}'
            result['remark'] = f'访问异常: {str(e)}'
            return result

    def _parse_zhihu_question(self, url: str, result: Dict) -> Dict:
        """解析知乎问答页面（带 cookie 访问）"""
        return self._parse_zhihu_with_cookie(url, result, is_question=True)

    def _parse_zhihu_zhuanlan(self, url: str, result: Dict) -> Dict:
        """解析知乎专栏文章（带 cookie 访问）"""
        return self._parse_zhihu_with_cookie(url, result, is_question=False)

    def _parse_zhihu_with_cookie(self, url: str, result: Dict, is_question=False) -> Dict:
        """知乎统一解析（cookie 访问）"""
        zhihu_cfg = self.credentials.get('zhihu_cookies', {})
        z_c0 = zhihu_cfg.get('z_c0', '')
        d_c0 = zhihu_cfg.get('d_c0', '')
        zse_ck = zhihu_cfg.get('__zse_ck', '')
        if not (z_c0 and d_c0 and zse_ck):
            result['error_info'] = '知乎需登录、未配置cookie'
            return result
        try:
            cookie = f'z_c0={z_c0}; d_c0={d_c0}; __zse_ck={zse_ck}'
            headers = {
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15',
                'Cookie': cookie}
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                result['error_info'] = f'知乎返回{resp.status_code}'
                return result
            soup = BeautifulSoup(resp.text, 'html.parser')
            h1 = soup.find('h1')
            if h1:
                result['title'] = h1.get_text().strip()
            if is_question:
                meta_date = (soup.find('meta', attrs={'itemprop': 'dateModified'})
                             or soup.find('meta', attrs={'itemprop': 'dateCreated'}))
            else:
                meta_date = (soup.find('meta', attrs={'itemprop': 'datePublished'})
                             or soup.find('meta', {'property': 'article:published_time'}))
            if meta_date:
                result['publish_date'] = self._parse_date(meta_date.get('content', ''))
                result['weekday'] = self._get_weekday(result['publish_date'])
            missing = []
            if not result['title']:
                missing.append('未提取标题')
            if not result['publish_date']:
                missing.append('未提取日期')
            if missing:
                result['error_info'] = '、'.join(missing)
        except Exception as e:
            result['error_info'] = f'知乎解析失败: {e}'
        return result

    def _resolve_short_url(self, url, timeout=10):
        """跟踪短链重定向，获取最终 URL"""
        try:
            resp = requests.get(url, allow_redirects=False, timeout=timeout,
                                headers={'User-Agent': 'Mozilla/5.0'})
            if resp.status_code in (301, 302, 303, 307, 308):
                return resp.headers.get('Location', url)
            return url
        except Exception:
            return url

    def _get_arxiv_latest_date(self, soup):
        """从 arxiv abs 页面的 submission history 提取最新版本日期"""
        hist = soup.find('div', class_='submission-history')
        if not hist:
            return ''
        text = hist.get_text()
        dates = re.findall(r'\w+,\s+(\d{1,2}\s+\w+\s+\d{4})\s+\d{2}:\d{2}:\d{2}', text)
        if not dates:
            return ''
        try:
            dt = datetime.strptime(dates[-1], '%d %b %Y')
            return dt.strftime('%Y%m%d')
        except ValueError:
            return ''

    def _extract_jike_date(self, url):
        """从即刻 post ID（MongoDB ObjectId）提取发布日期"""
        m = re.search(r'/originalPosts/([0-9a-f]{24})', url)
        if not m:
            return '', ''
        try:
            ts = int(m.group(1)[:8], 16)
            dt = datetime.utcfromtimestamp(ts)
            return dt.strftime('%Y%m%d'), self._get_weekday(dt.strftime('%Y%m%d'))
        except Exception:
            return '', ''

    def _parse_feishu_minutes(self, url: str, result: Dict) -> Dict:
        """解析飞书会议纪要（通过 Minutes API）"""
        m = re.search(r'/minutes/([A-Za-z0-9]+)', url)
        minute_token = m.group(1) if m else ''
        if not minute_token:
            result['error_info'] = '无法提取 minute_token'
            return result
        user_token = self.credentials.get('feishu_user_token', '')
        if not user_token:
            result['error_info'] = '飞书会议纪要需 user_access_token'
            return result
        try:
            headers = {'Authorization': f'Bearer {user_token}',
                       'Content-Type': 'application/json; charset=utf-8'}
            resp = requests.get(
                f'https://open.feishu.cn/open-apis/minutes/v1/minutes/{minute_token}',
                headers=headers, timeout=10)
            data = resp.json()
            if data.get('code') == 0:
                minute = data['data']['minute']
                result['title'] = minute.get('title', '')
                create_ts = minute.get('create_time', '')
                if create_ts:
                    dt = datetime.utcfromtimestamp(int(create_ts) / 1000)
                    result['publish_date'] = dt.strftime('%Y%m%d')
                    result['weekday'] = self._get_weekday(result['publish_date'])
        except Exception as e:
            result['error_info'] = f'会议纪要API失败: {e}'
        return result

    def _parse_weixin_devdoc(self, url: str, result: Dict) -> Dict:
        """解析微信开发者文档（JS 渲染，已知页面映射）"""
        known_pages = {
            '/devtools/download': '微信开发者工具下载地址与更新日志',
        }
        for path_key, title in known_pages.items():
            if path_key in url:
                result['title'] = title
                return result
        try:
            resp = requests.get(url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(resp.text, 'html.parser')
            title_tag = soup.find('title')
            if title_tag:
                result['title'] = title_tag.get_text().strip()
        except Exception as e:
            result['error_info'] = f'微信官方文档解析失败: {e}'
        return result

    def _parse_shimo(self, url: str, result: Dict) -> Dict:
        """解析石墨文档（通过 lizard-api 获取标题和日期）"""
        parts = url.rstrip('/').split('/')
        doc_id = parts[-1]
        try:
            api_url = f'https://shimo.im/lizard-api/files/{doc_id}'
            resp = requests.get(api_url, headers={'User-Agent': 'Mozilla/5.0',
                                                  'Accept': 'application/json'}, timeout=10)
            data = resp.json()
            name = data.get('name', '').strip()
            if name:
                result['title'] = name
            updated_at = data.get('updatedAt', '')
            if updated_at:
                result['publish_date'] = self._parse_date(updated_at)
                result['weekday'] = self._get_weekday(result['publish_date'])
            if not result['title']:
                result['error_info'] = '石墨API未返回文档名'
        except Exception as e:
            result['error_info'] = f'石墨文档解析失败: {e}'
        return result

    def _parse_xiaobot_article(self, url: str, result: Dict) -> Dict:
        """解析小报童文章（通过 API + 签名获取标题和日期）"""
        import hashlib
        token = self.credentials.get('xiaobot_token', '')
        if not token:
            link_text = result.get('_link_text', '')
            if link_text:
                result['title'] = link_text
                m = re.search(r'(\d{8})', link_text)
                if m:
                    result['publish_date'] = m.group(1)
                    result['weekday'] = self._get_weekday(result['publish_date'])
            if not result['title']:
                result['error_info'] = '小报童需配置xiaobot_token'
            return result
        # 从 URL 提取 post uuid 或 paper slug
        parsed = urlparse(url)
        path_parts = parsed.path.strip('/').split('/')
        if len(path_parts) >= 2 and path_parts[0] == 'post':
            post_id = path_parts[1]
            api_url = f'https://api.xiaobot.net/post/{post_id}'
            api_type = 'post'
        elif len(path_parts) >= 2 and path_parts[0] == 'p':
            slug = path_parts[1]
            api_url = f'https://api.xiaobot.net/paper/{slug}'
            api_type = 'paper'
        else:
            result['error_info'] = '小报童URL格式不支持API解析'
            return result
        try:
            ts = str(int(time.time()))
            sign_str = f'dbbc1dd37360b4084c3a69346e0ce2b2.{ts}'
            sign = hashlib.md5(sign_str.encode()).hexdigest()
            headers = {
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'application/json',
                'Authorization': f'Bearer {token}',
                'Timestamp': ts,
                'App-Version': '0.1',
                'Api-Key': 'xiaobot_web',
                'Sign': sign,
                'Origin': 'https://xiaobot.net',
            }
            resp = requests.get(api_url, headers=headers, timeout=10)
            data = resp.json()
            if 'data' in data:
                item = data['data']
                title = item.get('title', '') or item.get('name', '')
                title = title.strip()
                if title:
                    result['title'] = title
                    m = re.search(r'(\d{8})', title)
                    if m:
                        result['publish_date'] = m.group(1)
                        result['weekday'] = self._get_weekday(result['publish_date'])
                if not result['publish_date']:
                    created = item.get('created_at', '')
                    if created:
                        result['publish_date'] = self._parse_date(created)
                        result['weekday'] = self._get_weekday(result['publish_date'])
            else:
                result['error_info'] = f'小报童API: {data.get("message", "未知错误")}'
        except Exception as e:
            result['error_info'] = f'小报童解析失败: {e}'
        return result

    def _parse_jike_article(self, url: str, result: Dict) -> Dict:
        """解析即刻帖子（标题从页面提取，日期从 ObjectId 提取）"""
        try:
            resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
            soup = BeautifulSoup(resp.text, 'html.parser')
            for div in soup.find_all('div'):
                text = div.get_text(strip=True)
                cleaned = re.sub(r'^.{1,20}\d+[天小时月年分钟]+前', '', text)
                if len(cleaned) > 50 and '下载App' not in cleaned[:20]:
                    m = re.match(r'(.+?[。！？!?])', cleaned)
                    if m and len(m.group(1)) > 5:
                        result['title'] = m.group(1)
                    else:
                        result['title'] = cleaned[:80]
                    break
            if not result['title']:
                og_title = soup.find('meta', property='og:title')
                if og_title:
                    title = og_title.get('content', '').strip()
                    if ' - 即刻' in title:
                        title = title.rsplit(' - 即刻', 1)[0]
                    if title:
                        result['title'] = title
        except Exception:
            pass
        jike_date, jike_weekday = self._extract_jike_date(url)
        if jike_date:
            result['publish_date'] = jike_date
            result['weekday'] = jike_weekday
        return result
