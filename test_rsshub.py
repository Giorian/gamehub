#!/usr/bin/env python3
"""
RSSHub 接入方案测试脚本
验证 RSSHub 镜像可用性、RSS 解析和数据字段完整性

用法:
    python test_rsshub.py                    # 测试所有镜像和游戏
    python test_rsshub.py 三角洲行动           # 测试指定游戏
"""

import sys
import os
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (
    RSSHUB_MIRRORS,
    RSSHUB_ROUTES,
    _check_rsshub_health,
    fetch_rss_feed,
    build_game_rss_sources,
    get_rsshub_base,
    BILIBILI_UIDS,
)


def test_mirror_availability():
    """测试所有 RSSHub 镜像的可用性"""
    print("=" * 60)
    print("第一步：测试 RSSHub 镜像可用性")
    print("=" * 60)

    available = []
    for mirror in RSSHUB_MIRRORS:
        print(f"  测试 {mirror} ...", end=" ", flush=True)
        start = time.time()
        ok = _check_rsshub_health(mirror)
        elapsed = time.time() - start
        if ok:
            print(f"可用 ({elapsed:.1f}s) ✓")
            available.append(mirror)
        else:
            print(f"不可用 ({elapsed:.1f}s) ✗")

    print(f"\n可用镜像: {len(available)}/{len(RSSHUB_MIRRORS)}")
    return available


def test_game_feeds(game_name: str):
    """测试指定游戏的 RSS 数据获取"""
    print(f"\n{'=' * 60}")
    print(f"第二步：测试「{game_name}」的 RSS 数据")
    print("=" * 60)

    routes = RSSHUB_ROUTES.get(game_name, [])
    if not routes:
        print(f"  未配置 RSSHub 路由")
        return

    base = get_rsshub_base()
    if not base:
        print(f"  无可用 RSSHub 镜像，跳过")
        return

    print(f"  RSSHub 镜像: {base}")
    print(f"  配置路由: {len(routes)} 个")

    total_items = []
    for route in routes:
        url = f"{base}{route}"
        print(f"\n  路由: {route}")
        print(f"  URL: {url[:80]}...")

        start = time.time()
        items = fetch_rss_feed(url, game_name, "rsshub_test")
        elapsed = time.time() - start

        print(f"  结果: {len(items)} 条 ({elapsed:.1f}s)")

        if items:
            # 检查字段完整性
            sample = items[0]
            required_fields = ["id", "game", "title", "link", "pubDate", "summary", "image", "images", "content", "source"]
            missing = [f for f in required_fields if f not in sample or not sample.get(f)]
            print(f"  字段完整性: {len(required_fields) - len(missing)}/{len(required_fields)}")

            if missing:
                print(f"  缺失字段: {missing}")

            print(f"  样本:")
            print(f"    标题: {sample['title'][:50]}")
            print(f"    日期: {sample['pubDate']}")
            print(f"    链接: {sample['link'][:60]}")
            print(f"    图片: {len(sample.get('images', []))} 张")
            print(f"    正文: {len(sample.get('content', ''))} 字符")
            print(f"    来源: {sample.get('source', '?')}")

            total_items.extend(items)

    print(f"\n  「{game_name}」总计: {len(total_items)} 条 RSS 资讯")
    return total_items


def test_all_games():
    """测试所有已配置的游戏"""
    print("=" * 60)
    print("第三步：测试所有游戏")
    print("=" * 60)

    all_games = list(BILIBILI_UIDS.keys())
    all_results = {}

    for game_name in all_games:
        items = test_game_feeds(game_name)
        all_results[game_name] = len(items) if items else 0

    print(f"\n{'=' * 60}")
    print("汇总")
    print("=" * 60)
    total = 0
    for game, count in all_results.items():
        status = "✓" if count > 0 else "✗"
        print(f"  {status} {game}: {count} 条")
        total += count
    print(f"\n  总计: {total} 条")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        game_name = sys.argv[1]
        if game_name not in BILIBILI_UIDS:
            print(f"未知游戏: {game_name}")
            print(f"可选游戏: {', '.join(BILIBILI_UIDS.keys())}")
            sys.exit(1)
        test_mirror_availability()
        test_game_feeds(game_name)
    else:
        test_mirror_availability()
        test_all_games()
