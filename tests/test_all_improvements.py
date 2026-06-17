"""
综合测试：URL截断和解析功能
"""

import sys
sys.stdout.reconfigure(encoding='utf-8')

from url_parser import UrlParser

def test_url_truncation():
    """测试URL截断功能"""
    parser = UrlParser()

    print("=" * 60)
    print("测试1: URL截断功能")
    print("=" * 60)

    test_cases = [
        {
            'url': 'https://mp.weixin.qq.com/s/abc123?from=timeline&isappinstalled=0',
            'expected_url': 'https://mp.weixin.qq.com/s/abc123',
            'source': '微信'
        },
        {
            'url': 'https://m.weibo.cn/status/5264065811448972?sourceType=weixin',
            'expected_url': 'https://m.weibo.cn/status/5264065811448972',
            'source': 'App-微博'
        },
        {
            'url': 'https://m.toutiao.com/video/7591109484378849332/?app=news_article',
            'expected_url': 'https://m.toutiao.com/video/7591109484378849332/',
            'source': '头条_video'
        },
        {
            'url': 'https://m.toutiao.com/article/7578923974488064546/?app=news',
            'expected_url': 'https://m.toutiao.com/article/7578923974488064546/',
            'source': '头条OT'
        },
    ]

    for i, test_case in enumerate(test_cases, 1):
        print(f"\n测试用例 {i}: {test_case['source']}")
        print(f"原始URL: {test_case['url']}")

        result = parser.parse_url(test_case['url'])

        print(f"截断后: {result['url']}")
        print(f"期望值: {test_case['expected_url']}")

        if result['url'] == test_case['expected_url']:
            print("✅ 通过")
        else:
            print("❌ 失败")
            return False

    return True

def test_toutiao_video():
    """测试头条视频解析"""
    parser = UrlParser()

    print("\n" + "=" * 60)
    print("测试2: 头条视频解析")
    print("=" * 60)

    test_urls = [
        'https://m.toutiao.com/video/7591109484378849332/',
        'https://www.toutiao.com/video/7571827646221386292/',
    ]

    for url in test_urls:
        print(f"\n测试URL: {url}")
        result = parser.parse_url(url)

        print(f"来源: {result['source']}")
        print(f"标题: {result['title']}")
        print(f"日期: {result['publish_date']}")
        print(f"星期: {result['weekday']}")

        if result['title'] and result['publish_date']:
            print("✅ 通过")
        else:
            print("❌ 失败")
            return False

    return True

def test_toutiao_article():
    """测试头条文章解析"""
    parser = UrlParser()

    print("\n" + "=" * 60)
    print("测试3: 头条文章解析")
    print("=" * 60)

    test_urls = [
        'https://m.toutiao.com/article/7578923974488064546/',
        'https://www.toutiao.com/article/7578923974488064546/',
    ]

    for url in test_urls:
        print(f"\n测试URL: {url}")
        result = parser.parse_url(url)

        print(f"来源: {result['source']}")
        print(f"标题: {result['title']}")
        print(f"日期: {result['publish_date']}")
        print(f"星期: {result['weekday']}")

        if result['title'] and result['publish_date']:
            print("✅ 通过")
        else:
            print("❌ 失败")
            return False

    return True

def test_feishu():
    """测试飞书文档解析"""
    parser = UrlParser()

    print("\n" + "=" * 60)
    print("测试4: 飞书文档解析")
    print("=" * 60)

    url = 'https://waytoagi.feishu.cn/wiki/PPniw6JDKiJMgTkQAJtckyv6nYd'
    print(f"\n测试URL: {url}")

    result = parser.parse_url(url)

    print(f"来源: {result['source']}")
    print(f"标题: {result['title']}")
    print(f"日期: {result['publish_date']}")
    print(f"星期: {result['weekday']}")

    expected_title = "DemoDay：新手小白也能做出来的保姆级coding教程"
    expected_date = "20260207"

    if result['title'] == expected_title and result['publish_date'] == expected_date:
        print("✅ 通过")
        return True
    else:
        print("❌ 失败")
        return False

def test_weibo():
    """测试微博解析（预期受限于登录）"""
    parser = UrlParser()

    print("\n" + "=" * 60)
    print("测试5: 微博解析（预期受限于登录）")
    print("=" * 60)

    url = 'https://m.weibo.cn/status/5263671978887837'
    print(f"\n测试URL: {url}")

    result = parser.parse_url(url)

    print(f"来源: {result['source']}")
    print(f"标题: {result['title']}")
    print(f"日期: {result['publish_date']}")
    print(f"异常信息: {result['error_info']}")

    if result['source'] == 'App-微博':
        print("✅ 来源识别正常")
        return True
    else:
        print("❌ 来源识别失败")
        return False

if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("开始综合测试")
    print("=" * 60)

    results = []

    results.append(("URL截断功能", test_url_truncation()))
    results.append(("头条视频解析", test_toutiao_video()))
    results.append(("头条文章解析", test_toutiao_article()))
    results.append(("飞书文档解析", test_feishu()))
    results.append(("微博解析", test_weibo()))

    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{name}: {status}")

    all_passed = all(passed for _, passed in results)

    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 所有测试通过！")
    else:
        print("⚠️ 部分测试失败")
    print("=" * 60)
