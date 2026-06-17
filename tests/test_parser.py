"""
测试 URL 解析器的 URL 识别功能
"""
from url_parser import UrlParser

def test_url_recognition():
    """测试 URL 识别"""
    parser = UrlParser()

    # 测试 URL 列表
    test_urls = [
        'https://mp.weixin.qq.com/s/abc123',
        'https://wallstreetcn.com/articles/3012345',
        'https://b23.tv/abc123',
        'https://zhuanlan.zhihu.com/p/123456',
        'https://www.biji.com/note/share_note/abc',
        'https://www.xiaohongshu.com/discovery/item/abc123',
        'https://m.weibo.cn/status/123456',
        'https://m.okjike.com/originalPosts/abc123',
        'https://waytoagi.feishu.cn/wiki/abc123',
        'https://m.toutiao.com/video/123456',
        'https://m.toutiao.com/article/123456',
        'https://unknown-site.com/article/123',
    ]

    print("=" * 80)
    print("URL 识别测试（含备注标识）")
    print("=" * 80)

    for url in test_urls:
        # 只测试来源识别，不实际访问网站
        result = {'source': '', 'error_info': '', 'remark': ''}

        # 查找匹配的规则
        matched = False
        for url_pattern, (source_name, _) in parser.site_rules:
            if url_pattern in url:
                result['source'] = source_name
                matched = True
                break

        if not matched:
            result['source'] = '未识别'
            result['error_info'] = '未提取来源信息'
            result['remark'] = '非微信网址'
        elif source_name != '微信':
            result['remark'] = '非微信网址'

        print(f"\nURL: {url}")
        print(f"识别来源: {result['source']}")
        if result['remark']:
            print(f"备注: {result['remark']}")
        if result['error_info']:
            print(f"异常信息: {result['error_info']}")

if __name__ == '__main__':
    test_url_recognition()
