#!/usr/bin/env python3
"""
GameHub 数据导出脚本
抓取所有游戏资讯并生成静态 JSON 文件，供 GitHub Pages 直接使用

用法:
    python export_data.py                    # 导出到 ./dist/data/news.json
    python export_data.py --output ./data    # 指定输出目录
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


def export_all_news(output_dir: str = "./dist/data") -> dict:
    """抓取所有游戏资讯并导出为 JSON 文件"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    all_news = []
    game_stats = {}

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始抓取资讯...")

    for game_name in GAMES:
        print(f"  抓取 {game_name}...", end=" ", flush=True)
        try:
            items = fetch_game_news_direct(game_name)
            all_news.extend(items)
            game_stats[game_name] = len(items)
            print(f"{len(items)} 条 ✓")
        except Exception as e:
            print(f"失败 ✗ ({e})")
            game_stats[game_name] = 0

    # 按时间倒序
    all_news.sort(key=lambda x: x.get("pubDate", ""), reverse=True)

    # 生成元数据
    metadata = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_count": len(all_news),
        "games": game_stats,
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

    print(f"\n完成！共 {len(all_news)} 条资讯")
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
