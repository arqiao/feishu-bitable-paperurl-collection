#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试头条链接解析"""

from url_parser import UrlParser

def test_toutiao_link():
    """测试头条链接解析"""
    parser = UrlParser()

    # 测试链接
    test_url = "https://m.toutiao.com/w/1856725917720584/"

    print(f"测试链接: {test_url}")
    print("-" * 60)

    result = parser.parse_url(test_url, debug=True)

    print(f"解析结果:")
    print(f"  标题: {result.get('title', '未提取到')}")
    print(f"  日期: {result.get('publish_date', '未提取到')}")
    print(f"  星期: {result.get('weekday', '未提取到')}")
    print(f"  来源: {result.get('source', '未提取到')}")
    print(f"  链接: {result.get('url', '未提取到')}")
    print("-" * 60)

    # 验证标题是否符合预期
    expected_title = "AI写代码比我快千倍，上网却像个第一天摸电脑的新手"
    if result.get('title') == expected_title:
        print("[OK] 标题解析正确！")
    else:
        print("[FAIL] 标题不符合预期")
        print(f"  期望: {expected_title}")
        print(f"  实际: {result.get('title')}")

if __name__ == "__main__":
    test_toutiao_link()
