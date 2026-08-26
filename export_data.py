#!/usr/bin/env python3
"""
GameHub 数据导出脚本
抓取所有游戏资讯并生成静态 JSON 文件，供 GitHub Pages 直接使用

用法:
    python export_data.py                    # 导出到 ./dist/data/news.json
    python export_data.py --output ./data    # 指定输出目录

============================================================
数据源配置（国内社区优先，避免国际版/英文内容）
============================================================

游戏            | 主数据源              | 补充数据源
----------------|----------------------|------------------
原神            | 米游社API (gid=2)     | B站文章 + B站动态
崩铁            | 米游社API (gid=6)     | B站文章 + B站动态
绝区零          | 米游社API (gid=8)     | B站文章 + B站动态
三角洲行动       | 小红书笔记 + B站动态   | B站文章 + B站搜索
终末地          | 鹰角官网bulletins*    | B站文章 + B站搜索
第五人格        | 网易大神API           | B站文章 + B站动态
燕云十六声      | 网易大神API           | B站文章 + RSSHub

* 森空岛无公开API，使用鹰角官网(endfield.hypergryph.com)Next.js数据替代

通用补充：
- B站动态（自动生成buvid3防412，有Cookie更稳定）
- B站搜索（当数据不足5条时自动触发）
- RSSHub/3DMGame（当数据不足5条时作为最终回退）

B站请求自动生成buvid3标识防412限流，BILIBILI_COOKIE环境变量可选配置。
"""

import json
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

# 确保能 import main.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import fetch_game_news_direct, BILIBILI_UIDS

GAMES = list(BILIBILI_UIDS.keys())

# 数据源配置（用于日志和文档输出）
DATA_SOURCE_CONFIG = {
    "原神": {
        "primary": "米游社API (gid=2)",
        "supplementary": ["B站文章", "B站动态"],
        "fallback": "RSSHub (HoYoLAB + B站动态/投稿)",
    },
    "崩铁": {
        "primary": "米游社API (gid=6)",
        "supplementary": ["B站文章", "B站动态"],
        "fallback": "RSSHub (HoYoLAB + B站动态/投稿)",
    },
    "绝区零": {
        "primary": "米游社API (gid=8)",
        "supplementary": ["B站文章", "B站动态"],
        "fallback": "RSSHub (HoYoLAB + B站动态/投稿)",
    },
    "三角洲行动": {
        "primary": "小红书笔记 + B站动态(buvid3防412)",
        "supplementary": ["B站文章(365天)", "B站搜索"],
        "fallback": "RSSHub (B站动态/投稿 + 微博 + 3DMGame)",
    },
    "终末地": {
        "primary": "鹰角官网bulletins (endfield.hypergryph.com)",
        "supplementary": ["B站文章(90天)", "B站搜索"],
        "fallback": "RSSHub (B站动态/投稿)",
    },
    "第五人格": {
        "primary": "网易大神API (uid=025ee033...)",
        "supplementary": ["B站文章", "B站动态"],
        "fallback": "RSSHub (B站动态/投稿)",
    },
    "燕云十六声": {
        "primary": "网易大神API (uid=c47870f2...)",
        "supplementary": ["B站文章", "B站动态"],
        "fallback": "RSSHub (B站动态/投稿)",
    },
}


def export_all_news(output_dir: str = "./dist/data") -> dict:
    """抓取所有游戏资讯并导出为 JSON 文件"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    all_news = []
    game_stats = {}

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始抓取资讯...")
    print("=" * 60)
    print("数据源配置（国内社区优先）:")
    for game, config in DATA_SOURCE_CONFIG.items():
        print(f"  {game}: {config['primary']}")
    print("=" * 60)

    for game_name in GAMES:
        print(f"\n--- 抓取 {game_name} ---")
        config = DATA_SOURCE_CONFIG.get(game_name, {})
        print(f"  主数据源: {config.get('primary', '未知')}")
        print(f"  补充: {', '.join(config.get('supplementary', []))}")

        try:
            items = fetch_game_news_direct(game_name)
            all_news.extend(items)
            game_stats[game_name] = len(items)

            # 统计各数据源贡献
            source_counts = {}
            for item in items:
                src = item.get("source", "unknown")
                source_counts[src] = source_counts.get(src, 0) + 1

            print(f"  结果: {len(items)} 条 ✓")
            for src, count in sorted(source_counts.items(), key=lambda x: -x[1]):
                print(f"    - {src}: {count}条")
        except Exception as e:
            print(f"  失败 ✗ ({e})")
            game_stats[game_name] = 0

    # 按时间倒序
    all_news.sort(key=lambda x: x.get("pubDate", ""), reverse=True)

    # 生成元数据
    metadata = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_count": len(all_news),
        "games": game_stats,
        "data_source_plan": "国内社区优先：米游社+B站动态+小红书+网易大神",
    }

    # 导出全部资讯
    output_file = output_path / "news.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "metadata": metadata,
            "items": all_news,
        }, f, ensure_ascii=False, indent=2)

    # 按游戏分别导出（方便分游戏加载）
    for game_name in GAMES:
        game_items = [n for n in all_news if n.get("game") == game_name]
        game_file = output_path / f"news_{game_name}.json"
        with open(game_file, "w", encoding="utf-8") as f:
            json.dump({
                "metadata": {
                    "generated_at": metadata["generated_at"],
                    "total_count": len(game_items),
                    "game": game_name,
                },
                "items": game_items,
            }, f, ensure_ascii=False, indent=2)

    print(f"\n{'=' * 60}")
    print(f"完成！共 {len(all_news)} 条资讯")
    print(f"输出目录: {output_path.absolute()}")
    print(f"各游戏统计:")
    for game, count in game_stats.items():
        print(f"  {game}: {count} 条")

    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GameHub 数据导出")
    parser.add_argument("--output", "-o", default="./dist/data", help="输出目录")
    args = parser.parse_args()

    export_all_news(args.output)
