from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import hashlib
import time
import os
from typing import Optional, List, Dict
import json

app = FastAPI()

# 允许前端跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# RSSHub 镜像列表（按优先级排列，自动选择可用的）
# 支持本地自建实例（GitHub Actions 中可通过 npm 启动）
# ============================================================

RSSHUB_MIRRORS = [
    "http://localhost:1200",         # 本地自建实例（GitHub Actions 中启动）
    "https://rsshub.app",            # 官方公共实例
    "https://rsshub.rssforever.com", # 社区镜像 1
    "https://rsshub.uneasy.win",    # 社区镜像 2
    "https://hub.slarker.me",        # 社区镜像 3
    "https://rss.shab.fun",          # 社区镜像 4
]

_selected_rsshub_base = None  # 运行时自动选择的镜像
_rsshub_checked = False       # 是否已执行过健康检查


def _check_rsshub_health(mirror: str) -> bool:
    """检查 RSSHub 镜像是否可用"""
    try:
        # 先尝试 /healthz（部分实例支持）
        resp = requests.get(f"{mirror}/healthz", timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            return True
    except Exception:
        pass
    # 备用：尝试一个实际的 RSS 路由
    try:
        resp = requests.get(
            f"{mirror}/bilibili/user/dynamic/1",
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if resp.status_code == 200 and ('<rss' in resp.text or '<feed' in resp.text):
            return True
    except Exception:
        pass
    return False


def get_rsshub_base() -> str:
    """自动选择可用的 RSSHub 镜像（带缓存，只检查一次）"""
    global _selected_rsshub_base, _rsshub_checked
    if _selected_rsshub_base:
        return _selected_rsshub_base
    if _rsshub_checked:
        # 已检查过但没有可用的，直接返回 None
        return None

    _rsshub_checked = True
    for mirror in RSSHUB_MIRRORS:
        if _check_rsshub_health(mirror):
            print(f"[RSSHub] 选择镜像: {mirror}")
            _selected_rsshub_base = mirror
            return mirror

    print("[警告] 所有 RSSHub 镜像都无法访问")
    return None


# ============================================================
# 游戏资讯源配置
# 每个游戏可配置多个 RSS 源，最终合并去重
# 支持 RSSHub 路由和直接 RSS 源
# ============================================================

# RSSHub 路由配置（不包含 base URL，运行时拼接）
# 注意：bilibili/user/dynamic 和 weibo/user 路由需要cookie，在公共镜像上通常不可用
# bilibili/user/video 路由不需要cookie，在本地Docker实例中可能可用
RSSHUB_ROUTES = {
    "原神": [
        "/hoyolab/news/zh-cn/2/1",              # HoYoLAB 官方新闻
        "/bilibili/user/dynamic/401742377",       # B站官方账号动态（需cookie）
        "/bilibili/user/video/401742377",         # B站官方投稿视频
    ],
    "崩铁": [
        "/hoyolab/news/zh-cn/6/1",              # HoYoLAB 官方新闻（崩铁 gid=6）
        "/bilibili/user/dynamic/1340190821",      # B站官方账号动态（需cookie）
        "/bilibili/user/video/1340190821",        # B站官方投稿视频
    ],
    "绝区零": [
        "/hoyolab/news/zh-cn/8/1",              # HoYoLAB 官方新闻（绝区零 gid=8）
        "/bilibili/user/dynamic/1636034895",      # B站官方账号动态（需cookie）
        "/bilibili/user/video/1636034895",        # B站官方投稿视频
    ],
    "终末地": [
        "/bilibili/user/dynamic/1265652806",      # B站官方账号动态（需cookie）
        "/bilibili/user/video/1265652806",        # B站官方投稿视频
    ],
    "第五人格": [
        "/bilibili/user/dynamic/364715840",        # B站官方账号动态（需cookie）
        "/bilibili/user/video/364715840",          # B站官方投稿视频
    ],
    "三角洲行动": [
        "/bilibili/user/dynamic/3494376565115651", # B站官方账号动态（需cookie）
        "/bilibili/user/video/3494376565115651",   # B站官方投稿视频
        "/weibo/user/6188277234",                  # 微博官方账号（需cookie）
        "/3dmgame/games/三角洲行动",                # 3DMGame 游戏资讯
    ],
    "燕云十六声": [
        "/bilibili/user/dynamic/1567141152",       # B站官方账号动态（需cookie）
        "/bilibili/user/video/1567141152",         # B站官方投稿视频
    ],
}

# RSSHub 关键词过滤配置（可选，只保留包含关键词的条目）
RSSHUB_KEYWORDS = {
    # 示例: "三角洲行动": ["公告", "版本", "活动", "更新", "赛季"],
}


def build_game_rss_sources() -> dict:
    """根据选定的 RSSHub 镜像构建游戏资讯源 URL 列表"""
    base = get_rsshub_base()
    if not base:
        return {}
    sources = {}
    for game, routes in RSSHUB_ROUTES.items():
        sources[game] = [f"{base}{route}" for route in routes]
    return sources

# ============================================================
# 简单内存缓存（避免频繁请求 RSSHub 被限流）
# 缓存有效期：30 分钟
# ============================================================

_cache = {}
CACHE_TTL = 30 * 60  # 30分钟


def get_cached(key: str):
    """从缓存获取数据，过期返回 None"""
    if key in _cache:
        data, timestamp = _cache[key]
        if time.time() - timestamp < CACHE_TTL:
            return data
    return None


def set_cached(key: str, data):
    """写入缓存"""
    _cache[key] = (data, time.time())


# ============================================================
# 直接抓取：B站官方动态 + 米游社公告
# 不依赖 RSSHub，稳定性更高
# ============================================================

# 各游戏官方 B站 UID
BILIBILI_UIDS = {
    "原神": "401742377",
    "崩铁": "1340190821",
    "绝区零": "1636034895",
    "终末地": "1265652806",
    "第五人格": "364715840",
    "三角洲行动": "3494376565115651",
    "燕云十六声": "1567141152",
}

# 各游戏米游社板块 ID (gids)
MIYOUSHE_GIDS = {
    "原神": "2",
    "崩铁": "6",
    "绝区零": "8",
}

# B站请求头（模拟浏览器，避免被拦截）
BILIBILI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}


def _get_bili_cookie_headers() -> dict:
    """获取带Cookie的B站请求头（Cookie从环境变量读取，不硬编码）"""
    headers = dict(BILIBILI_HEADERS)
    cookie = os.environ.get("BILIBILI_COOKIE", "")
    if cookie:
        headers["Cookie"] = cookie
    return headers

# 米游社请求头
MIYOUSHE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.miyoushe.com/",
    "x-rpc-client_type": "4",
    "x-rpc-app_version": "2.71.1",
}


def fetch_bilibili_dynamic(uid: str, game_name: str) -> list:
    """直接从 B站 API 抓取用户动态（需要Cookie才能获取完整数据）"""
    try:
        url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?host_mid={uid}&timezone_offset=-480"
        headers = _get_bili_cookie_headers()
        if "Cookie" not in headers:
            print(f"[B站动态] 无Cookie，可能被412拦截 ({game_name})")
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 412:
            print(f"[B站动态] 412 被拦截 ({game_name})，需配置BILIBILI_COOKIE环境变量")
            return []
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            print(f"[警告] B站动态获取失败 ({game_name}): {data.get('message', '未知错误')}")
            return []

        items = []
        cards = data.get("data", {}).get("items", [])
        cutoff_date = datetime.now() - timedelta(days=30)
        cookie_available = "Cookie" in headers

        for card in cards:
            modules = card.get("modules", {})
            author = modules.get("module_author", {}) or {}

            # 提取发布时间
            pub_ts = author.get("pub_ts")
            if not pub_ts:
                continue
            try:
                pub_ts = int(pub_ts)
            except (ValueError, TypeError):
                continue
            pub_date = datetime.fromtimestamp(pub_ts)
            if pub_date < cutoff_date:
                continue

            # 提取动态内容
            dyn = modules.get("module_dynamic", {}) or {}
            major = dyn.get("major") or {}
            desc = dyn.get("desc") or {}
            mtype = major.get("type", "")
            dyn_id = card.get("id_str", card.get("id", ""))

            title = ""
            image = ""
            images = []
            content_parts = []

            # 文本内容
            desc_text = desc.get("text", "") if desc else ""
            if desc_text:
                for p in desc_text.split('\n'):
                    p = p.strip()
                    if p:
                        content_parts.append(f"<p>{p}</p>")

            if mtype == "MAJOR_TYPE_DRAW":
                # 图文动态
                draws = major.get("draw", {}).get("items", [])
                images = [d.get("src", "") for d in draws if d.get("src")]
                image = images[0] if images else ""
                if not title and desc_text:
                    title = desc_text[:50]
                elif not title:
                    title = f"{game_name}官方图文动态"

            elif mtype == "MAJOR_TYPE_ARCHIVE":
                # 视频投稿
                archive = major.get("archive", {}) or {}
                title = archive.get("title", "") or f"{game_name}官方视频"
                cover = archive.get("cover", "")
                if cover:
                    image = cover
                    images = [cover]
                desc_text = archive.get("desc", "") or desc_text
                if desc_text and desc_text != title:
                    content_parts.append(f"<p>{desc_text[:200]}</p>")

            elif mtype == "MAJOR_TYPE_OPUS":
                # 专栏/长文动态
                opus = major.get("opus", {}) or {}
                title = opus.get("title", "") or desc_text[:50] or f"{game_name}官方动态"
                cover = opus.get("cover", "")
                if cover:
                    image = cover
                    images = [cover]
                # summary
                summary = opus.get("summary", {}) or {}
                summary_text = summary.get("text", "")
                if summary_text:
                    content_parts.append(f"<p>{summary_text}</p>")
                # pics
                pics = opus.get("pics", [])
                for pic in pics:
                    if isinstance(pic, dict):
                        pic_url = pic.get("url", "")
                        if pic_url and pic_url not in images:
                            images.append(pic_url)
                if not image and images:
                    image = images[0]

            elif mtype == "MAJOR_TYPE_ARTICLE":
                # 专栏文章
                article = major.get("article", {}) or {}
                title = article.get("title", "") or f"{game_name}官方文章"
                covers = article.get("covers", [])
                if covers:
                    images = covers
                    image = covers[0]

            else:
                # 未知类型，使用desc文本
                title = desc_text[:50] if desc_text else f"{game_name}官方动态"

            # 截取标题
            if len(title) > 80:
                title = title[:80] + "..."

            # 构建完整内容
            if images:
                for img_url in images[:9]:
                    content_parts.append(f'<p><img src="{img_url}" alt="" style="max-width:100%;border-radius:8px;"></p>')
            if not content_parts:
                content_parts.append(f"<p>{title}</p>")
            full_content = "\n".join(content_parts)

            # 摘要
            summary = clean_summary(desc_text) if desc_text else title

            # 链接
            link = f"https://t.bilibili.com/{dyn_id}" if dyn_id else f"https://space.bilibili.com/{uid}/dynamic"

            items.append({
                "id": f"bili_{uid}_{dyn_id}",
                "game": game_name,
                "title": title,
                "link": link,
                "pubDate": pub_date.strftime("%Y-%m-%d %H:%M:%S"),
                "summary": summary,
                "image": image,
                "images": images,
                "content": full_content,
                "source": "bilibili_dynamic",
            })

        print(f"[B站动态] 获取 {len(items)} 条动态 ({game_name}){' [Cookie]' if cookie_available else ' [无Cookie]'}")
        return items

    except Exception as e:
        print(f"[错误] 抓取B站动态失败 ({game_name}): {e}")
        return []


def fetch_bilibili_article_content(cv_id: str) -> str:
    """获取B站文章完整正文HTML（从文章页面提取）"""
    try:
        url = f"https://www.bilibili.com/read/cv{cv_id}"
        resp = requests.get(url, headers=_get_bili_cookie_headers(), timeout=15)
        if resp.status_code != 200:
            return ""

        soup = BeautifulSoup(resp.text, "html.parser")
        # B站文章正文在 opus-module-content div 中
        content_div = soup.find("div", class_="opus-module-content")
        if not content_div:
            content_div = soup.find("div", class_="article-content")
        if not content_div:
            return ""

        # 清洗：移除script/style，保留段落和图片
        for tag in content_div.find_all(["script", "style", "iframe"]):
            tag.decompose()

        # 转换为干净的 HTML 段落
        html_parts = []
        for el in content_div.find_all(["p", "img", "h1", "h2", "h3"]):
            if el.name == "img":
                src = el.get("src") or el.get("data-src") or ""
                if src and src.startswith("http"):
                    html_parts.append(f'<p><img src="{src}" alt="" style="max-width:100%;border-radius:8px;"></p>')
            elif el.name in ["h1", "h2", "h3"]:
                text = el.get_text(strip=True)
                if text:
                    html_parts.append(f"<p><strong>{text}</strong></p>")
            else:
                text = el.get_text(strip=True)
                if text and len(text) > 2:
                    html_parts.append(f"<p>{text}</p>")

        return "\n".join(html_parts) if html_parts else ""
    except Exception as e:
        print(f"[B站文章] 获取正文失败 cv{cv_id}: {e}")
        return ""


def fetch_bilibili_articles(uid: str, game_name: str) -> list:
    """
    从B站文章API获取UP主发布的专栏文章
    API: https://api.bilibili.com/x/space/article
    不需要cookie，返回中文内容，包含封面图和摘要
    """
    try:
        url = (
            f"https://api.bilibili.com/x/space/article"
            f"?mid={uid}&pn=1&ps=20&sort=publish_time&platform=web"
        )
        resp = requests.get(url, headers=_get_bili_cookie_headers(), timeout=10)
        if resp.status_code == 412:
            print(f"[B站文章] 412 被拦截 ({game_name})")
            return []
        if resp.status_code != 200:
            print(f"[B站文章] HTTP {resp.status_code} ({game_name})")
            return []

        data = resp.json()
        if data.get("code") != 0:
            print(f"[B站文章] Code {data.get('code')} ({game_name})")
            return []

        articles = data.get("data", {}).get("articles", [])
        if not articles:
            print(f"[B站文章] 无文章 ({game_name})")
            return []

        items = []
        # 按游戏设置不同的日期过滤范围
        # 三角洲行动更新频率低，使用365天；其他游戏90天
        cutoff_days = 365 if game_name == "三角洲行动" else 90
        cutoff_date = datetime.now() - timedelta(days=cutoff_days)

        for art in articles:
            pub_ts = art.get("publish_time", 0)
            if not pub_ts:
                continue
            pub_date = datetime.fromtimestamp(int(pub_ts))
            if pub_date < cutoff_date:
                continue

            title = art.get("title", "").strip()
            if not title:
                continue

            cv_id = str(art.get("id", ""))
            link = f"https://www.bilibili.com/read/cv{cv_id}" if cv_id else ""

            # 封面图
            image_urls = art.get("image_urls", [])
            images = [url for url in image_urls if url and url.startswith("http")]
            image = images[0] if images else ""

            # 摘要
            summary = art.get("summary", "")
            summary = re.sub(r'<[^>]+>', '', summary).strip()
            if not summary or len(summary) < 10:
                summary = title

            # 获取完整正文（每篇文章单独请求）
            content = fetch_bilibili_article_content(cv_id) if cv_id else ""
            if not content:
                content = f"<p>{summary}</p>"

            items.append({
                "id": f"bili_art_{cv_id}",
                "game": game_name,
                "title": title,
                "link": link,
                "pubDate": pub_date.strftime("%Y-%m-%d %H:%M:%S"),
                "summary": summary[:150] + ("..." if len(summary) > 150 else ""),
                "image": image,
                "images": images,
                "content": content,
                "source": "bilibili_article",
            })

        print(f"[B站文章] 获取 {len(items)} 篇文章 ({game_name}, {cutoff_days}天范围)")
        return items

    except Exception as e:
        print(f"[错误] B站文章获取失败 ({game_name}): {e}")
        return []


def fetch_bilibili_search_articles(keyword: str, game_name: str, max_results: int = 10) -> list:
    """
    通过B站搜索API获取社区文章（不需要cookie，但可能被412限流）
    用于补充官方数据源不足的游戏（如三角洲行动）
    """
    try:
        url = (
            f"https://api.bilibili.com/x/web-interface/search/type"
            f"?search_type=article&keyword={keyword}&order=pubdate&page=1"
        )
        # 带重试的请求（应对412限流）
        resp = None
        for attempt in range(3):
            resp = requests.get(url, headers=_get_bili_cookie_headers(), timeout=15)
            if resp.status_code == 200:
                break
            if resp.status_code == 412 and attempt < 2:
                print(f"[B站搜索] 412 限流，{attempt+1}秒后重试 ({game_name})")
                time.sleep(attempt + 1)
                continue
            print(f"[B站搜索] HTTP {resp.status_code} ({game_name})")
            return []

        if resp is None or resp.status_code != 200:
            return []

        data = resp.json()
        if data.get("code") != 0:
            print(f"[B站搜索] Code {data.get('code')} ({game_name})")
            return []

        results = data.get("data", {}).get("result", [])
        if not results:
            print(f"[B站搜索] 无搜索结果 ({game_name})")
            return []

        items = []
        cutoff_date = datetime.now() - timedelta(days=30)
        fetched = 0

        for r in results:
            if fetched >= max_results:
                break

            pub_ts = r.get("pubdate", 0)
            if not pub_ts:
                continue
            pub_date = datetime.fromtimestamp(int(pub_ts))
            if pub_date < cutoff_date:
                continue

            # 清理标题中的高亮标签
            title = r.get("title", "").replace('<em class="keyword">', "").replace("</em>", "").strip()
            if not title or len(title) < 5:
                continue

            cv_id = str(r.get("id", ""))
            if not cv_id:
                continue

            link = f"https://www.bilibili.com/read/cv{cv_id}"

            # 封面图
            cover = r.get("cover", "") or ""
            if cover and not cover.startswith("http"):
                cover = "https:" + cover

            # 摘要
            desc = r.get("desc", "")
            desc = re.sub(r'<[^>]+>', '', desc).strip()
            if not desc or len(desc) < 10:
                desc = title
            summary = desc[:150] + ("..." if len(desc) > 150 else "")

            # 获取完整正文
            content = fetch_bilibili_article_content(cv_id) if cv_id else ""
            if not content:
                content = f"<p>{summary}</p>"

            items.append({
                "id": f"bili_search_{cv_id}",
                "game": game_name,
                "title": title,
                "link": link,
                "pubDate": pub_date.strftime("%Y-%m-%d %H:%M:%S"),
                "summary": summary,
                "image": cover,
                "images": [cover] if cover else [],
                "content": content,
                "source": "bilibili_search",
            })
            fetched += 1
            time.sleep(0.5)

        print(f"[B站搜索] 获取 {len(items)} 篇文章 ({game_name}, 关键词: {keyword})")
        return items

    except Exception as e:
        print(f"[错误] B站搜索文章获取失败 ({game_name}): {e}")
        return []


def fetch_miyoushe_news(gid: str, game_name: str) -> list:
    """直接从米游社 API 抓取官方公告"""
    try:
        # 米游社公告列表 API（新闻分类）
        url = f"https://api-takumi.mihoyo.com/post/wapi/getNewsList?gids={gid}&page_size=20&type=1"
        resp = requests.get(url, headers=MIYOUSHE_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("retcode") != 0:
            print(f"[警告] 米游社公告获取失败 ({game_name}): {data.get('message', '未知错误')}")
            return []

        items = []
        news_list = data.get("data", {}).get("list", [])
        cutoff_date = datetime.now() - timedelta(days=30)

        for news in news_list:
            # 发布时间
            pub_ts = news.get("created_at") or news.get("post", {}).get("created_at")
            if not pub_ts:
                continue
            pub_date = datetime.fromtimestamp(int(pub_ts))
            if pub_date < cutoff_date:
                continue

            # 标题和摘要
            post = news.get("post", {})
            title = post.get("subject", "") or news.get("title", "") or f"{game_name}官方公告"
            post_id = post.get("post_id", "") or news.get("post_id", "")

            # 封面图（从 image_list 取第一张）
            image = ""
            images = []
            image_list = news.get("image_list", []) or post.get("images", [])
            if image_list:
                if isinstance(image_list[0], dict):
                    images = [img.get("url", "") for img in image_list if img.get("url", "")]
                    image = images[0] if images else ""
                else:
                    images = [img for img in image_list if img]
                    image = images[0] if images else ""

            # 获取帖子详情内容（带请求间隔避免限流）
            content = _fetch_miyoushe_post_content(post_id) if post_id else ""
            time.sleep(0.3)
            summary = clean_summary(content, 150) if content else title

            # 链接（按游戏名映射不同的米游社板块路径）
            # 米游社不同游戏的路径前缀
            game_paths = {"2": "ys", "6": "sr", "8": "zzz"}
            path_prefix = game_paths.get(gid, "ys")
            link = f"https://www.miyoushe.com/{path_prefix}/article/{post_id}" if post_id else "https://www.miyoushe.com/"

            items.append({
                "id": f"mys_{gid}_{post_id}",
                "game": game_name,
                "title": title,
                "link": link,
                "pubDate": pub_date.strftime("%Y-%m-%d %H:%M:%S"),
                "summary": summary,
                "image": image,
                "images": images,
                "content": content,
                "source": "miyoushe",
            })

        content_count = sum(1 for item in items if item.get("content"))
        print(f"[米游社] 获取 {len(items)} 条 ({game_name}, {content_count}条有内容)")
        return items

    except Exception as e:
        print(f"[错误] 抓取米游社公告失败 ({game_name}): {e}")
        return []


def _fetch_miyoushe_post_content(post_id: str) -> str:
    """获取米游社帖子详情内容（带重试和限流保护）"""
    for attempt in range(3):
        try:
            url = f"https://api-takumi.mihoyo.com/post/wapi/getPostFull?post_id={post_id}"
            resp = requests.get(url, headers=MIYOUSHE_HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if data.get("retcode") != 0:
                if attempt < 2:
                    time.sleep(1.0)
                    continue
                return ""
            raw_content = data.get("data", {}).get("post", {}).get("post", {}).get("content", "")
            if not raw_content:
                return ""

            soup = BeautifulSoup(raw_content, "html.parser")

            # 先从 ql-image 中提取图片，保留到内容中
            for img_el in soup.find_all("div", class_="ql-image"):
                img = img_el.find("img")
                if img and img.get("src"):
                    img_tag = soup.new_tag("img", src=img["src"])
                    img_el.replace_with(img_tag)
                else:
                    img_el.decompose()

            # 移除其他无意义元素
            for tag in soup.find_all(["script", "style", "iframe"]):
                tag.decompose()

            # 提取文本段落和图片，保留原始顺序
            html_parts = []
            for el in soup.find_all(["p", "img"]):
                if el.name == "img":
                    src = el.get("src") or ""
                    if src and src.startswith("http"):
                        html_parts.append(f'<p><img src="{src}" alt="" style="max-width:100%;border-radius:8px;"></p>')
                else:
                    text = el.get_text(strip=True)
                    if text:
                        html_parts.append(f"<p>{text}</p>")

            # 去重并保留顺序
            seen = set()
            unique_parts = []
            for part in html_parts:
                text = part.replace("<p>", "").replace("</p>", "").replace('<img ', '').strip()
                if text and text not in seen:
                    seen.add(text)
                    unique_parts.append(part)
            return "\n".join(unique_parts) if unique_parts else ""
        except Exception:
            if attempt < 2:
                time.sleep(1.0)
                continue
            return ""
    return ""


# ============================================================
# 官网新闻爬虫：第五人格、明日方舟终末地等
# 直接从官方网站抓取新闻，不依赖社区平台
# ============================================================

def fetch_identityv_official_news() -> list:
    """从第五人格官网抓取官方新闻"""
    try:
        url = "https://id5.163.com/news/official/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://id5.163.com/",
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = "utf-8"
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        items = []
        cutoff_date = datetime.now() - timedelta(days=30)
        seen_titles = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "id5.163.com/news/" not in href or ".html" not in href:
                continue

            title = a.get_text(strip=True)
            # 清理标题（去除前缀的"新闻"字样和末尾日期）
            title = re.sub(r"^新闻", "", title).strip()
            title = re.sub(r"\d{4}-\d{2}-\d{2}$", "", title).strip()
            if not title or len(title) < 5:
                continue
            if title in seen_titles:
                continue
            seen_titles.add(title)

            # 补全链接
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = "https://id5.163.com" + href

            # 从URL提取日期
            date_match = re.search(r"/(\d{8})/", href)
            pub_date = None
            if date_match:
                d = date_match.group(1)
                try:
                    pub_date = datetime(int(d[:4]), int(d[4:6]), int(d[6:8]))
                except ValueError:
                    pass

            if pub_date and pub_date < cutoff_date:
                continue

            if not pub_date:
                pub_date = datetime.now()

            items.append({
                "id": f"id5_official_{hashlib.md5(href.encode()).hexdigest()[:12]}",
                "game": "第五人格",
                "title": title,
                "link": href,
                "pubDate": pub_date.strftime("%Y-%m-%d %H:%M:%S"),
                "summary": title,
                "image": "",
                "images": [],
                "content": f"<p>{title}</p><p>详情请查看原文链接。</p>",
                "source": "id5_official",
            })

        return items

    except Exception as e:
        print(f"[错误] 抓取第五人格官网新闻失败: {e}")
        return []


def fetch_endfield_official_news() -> list:
    """从明日方舟·终末地官网抓取官方公告（解析 Next.js 数据）"""
    try:
        url = "https://endfield.hypergryph.com/news"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://endfield.hypergryph.com/",
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        items = []
        cutoff_date = datetime.now() - timedelta(days=30)

        # 收集所有 __next_f.push 数据片段
        all_data = {}
        for s in soup.find_all("script"):
            if not s.string:
                continue
            matches = re.findall(r'__next_f\.push\(\[1,"(\d+):(.*?)"\]\)', s.string)
            for idx, content in matches:
                all_data[idx] = content

        # 查找包含 bulletins 的片段并解析
        for idx, data in all_data.items():
            if "bulletins" not in data.lower():
                continue
            try:
                # 将JSON字符串反转义
                decoded_str = json.loads(f'"{data}"')
                # 提取 bulletins 数组
                bulletin_match = re.search(r'"bulletins":\s*(\[.*?\])\s*,\s*"', decoded_str, re.DOTALL)
                if not bulletin_match:
                    continue
                bulletins = json.loads(bulletin_match.group(1))

                for b in bulletins:
                    title = b.get("title", "").strip()
                    if not title:
                        continue

                    display_time = b.get("displayTime", 0)
                    if display_time:
                        pub_date = datetime.fromtimestamp(display_time)
                        if pub_date < cutoff_date:
                            continue
                    else:
                        pub_date = datetime.now()

                    cid = b.get("cid", "")
                    cover = b.get("cover", "")
                    # 新闻详情页链接
                    link = f"https://endfield.hypergryph.com/news/{cid}" if cid else url

                    items.append({
                        "id": f"endfield_official_{cid or hashlib.md5(title.encode()).hexdigest()[:12]}",
                        "game": "终末地",
                        "title": title,
                        "link": link,
                        "pubDate": pub_date.strftime("%Y-%m-%d %H:%M:%S"),
                        "summary": title,
                        "image": cover,
                        "images": [cover] if cover else [],
                        "content": f"<p>{title}</p><p>详情请查看原文链接。</p>",
                        "source": "endfield_official",
                    })
            except Exception as inner_e:
                print(f"[警告] 解析终末地官网数据片段 {idx} 失败: {inner_e}")
                continue

        return items

    except Exception as e:
        print(f"[错误] 抓取终末地官网新闻失败: {e}")
        return []


def fetch_yanyun_official_news() -> list:
    """抓取燕云十六声官网新闻"""
    url = "https://www.yysls.cn/news/official/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://www.yysls.cn/",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        items = []
        cutoff_date = datetime.now() - timedelta(days=30)
        current_year = datetime.now().year

        for li in soup.select(".news-list li"):
            a = li.find("a")
            if not a:
                continue
            title = a.get_text(strip=True).replace("新闻", "", 1).strip()
            # 清理标题末尾的日期（如 "08/21"）
            title = re.sub(r'\d{2}/\d{2}$', '', title).strip()
            link = a.get("href", "")
            if link and not link.startswith("http"):
                link = "https://www.yysls.cn" + link

            # 日期：从 URL 中提取 (20260822) 或从元素中提取
            date_text = ""
            date_elem = li.find(class_="date") or li.find(class_="time")
            if date_elem:
                date_text = date_elem.get_text(strip=True)

            # 从URL中提取完整日期
            date_match = re.search(r"/(\d{8})/", link)
            pub_date = None
            if date_match:
                try:
                    pub_date = datetime.strptime(date_match.group(1), "%Y%m%d")
                except ValueError:
                    pass
            elif date_text:
                # 只有月/日，补全年份
                try:
                    parts = date_text.replace("新闻", "").strip().split("/")
                    if len(parts) == 2:
                        pub_date = datetime(current_year, int(parts[0]), int(parts[1]))
                except (ValueError, IndexError):
                    pass

            if not pub_date:
                pub_date = datetime.now()
            if pub_date > cutoff_date:
                pub_date = pub_date  # 保留

            # 图片
            img = li.find("img")
            cover = img.get("src", "") if img else ""
            if cover and not cover.startswith("http"):
                cover = "https:" + cover

            items.append({
                "id": f"yanyun_official_{hashlib.md5(title.encode()).hexdigest()[:12]}",
                "game": "燕云十六声",
                "title": title,
                "link": link,
                "pubDate": pub_date.strftime("%Y-%m-%d %H:%M:%S"),
                "summary": title,
                "image": cover,
                "images": [cover] if cover else [],
                "content": f"<p>{title}</p><p>详情请查看原文链接。</p>",
                "source": "yanyun_official",
            })

            if pub_date < cutoff_date:
                break

        return items
    except Exception as e:
        print(f"[错误] 抓取燕云十六声官网新闻失败: {e}")
        return []


# ============================================================
# TapTap 爬虫
# 从 TapTap 社区获取游戏官方动态
# ============================================================

# TapTap 各游戏的 App ID 和官方用户 ID
TAPTAP_CONFIG = {
    "三角洲行动": {
        "app_id": "330259",
        "user_id": "565576800",
    },
}

TAPTAP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def _parse_taptap_time(time_str: str) -> datetime:
    """解析TapTap的相对时间为datetime"""
    now = datetime.now()
    time_str = time_str.strip()
    
    if time_str == "刚刚":
        return now
    elif time_str == "昨天":
        return now - timedelta(days=1)
    
    # 匹配 "X 分钟前" / "X 小时前" / "X 天前" / "X 周前" / "X 月前"
    match = re.match(r"(\d+)\s*(分钟|小时|天|周|月|年)前", time_str)
    if match:
        num = int(match.group(1))
        unit = match.group(2)
        
        if unit == "分钟":
            return now - timedelta(minutes=num)
        elif unit == "小时":
            return now - timedelta(hours=num)
        elif unit == "天":
            return now - timedelta(days=num)
        elif unit == "周":
            return now - timedelta(weeks=num)
        elif unit == "月":
            return now - timedelta(days=num * 30)
        elif unit == "年":
            return now - timedelta(days=num * 365)
    
    # 绝对日期格式
    for fmt in ["%Y-%m-%d", "%m-%d %H:%M", "%Y/%m/%d"]:
        try:
            if fmt == "%m-%d %H:%M":
                dt = datetime.strptime(time_str, fmt)
                return dt.replace(year=now.year)
            return datetime.strptime(time_str, fmt)
        except ValueError:
            continue
    
    return now


def fetch_taptap_feeds(app_id: str, user_id: str, game_name: str) -> list:
    """从TapTap论坛获取官方动态"""
    try:
        url = f"https://www.taptap.cn/app/{app_id}/topic?type=official"
        headers = dict(TAPTAP_HEADERS)
        headers["Referer"] = f"https://www.taptap.cn/app/{app_id}"
        
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        
        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select(".moment-feed-list-item")
        
        if not items:
            # 备选选择器
            items = soup.select(".moment-list-item")
        
        result = []
        cutoff_date = datetime.now() - timedelta(days=30)
        
        for item in items:
            # 确认是官方账号发布的
            author_link = item.find("a", href=re.compile(rf"/user/{user_id}"))
            if not author_link:
                continue
            
            # 标题/内容
            summary_elem = item.select_one(".moment-article__summary--content")
            if not summary_elem:
                summary_elem = item.select_one(".moment-article")
            if not summary_elem:
                continue
            
            title = summary_elem.get_text(strip=True)
            if not title or len(title) < 5:
                continue
            
            # 截断过长的标题
            if len(title) > 80:
                title = title[:80] + "..."
            
            # 链接
            link_elem = item.find("a", href=re.compile(r"/moment/"))
            link = link_elem.get("href", "") if link_elem else ""
            if link and link.startswith("/"):
                link = "https://www.taptap.cn" + link
            elif not link:
                link = f"https://www.taptap.cn/app/{app_id}/topic"
            
            # 时间
            time_text = ""
            # 在头像旁边的时间
            time_elem = item.select_one(".moment-avator__avator-left-box-content")
            if time_elem:
                time_match = re.search(r"(\d+\s*(分钟|小时|天|周|月|年)前|刚刚|昨天)", time_elem.get_text())
                if time_match:
                    time_text = time_match.group(1)
            
            pub_date = _parse_taptap_time(time_text) if time_text else datetime.now()
            
            if pub_date < cutoff_date:
                continue
            
            # 内容图片（优先用内容里的图，不是头像）
            image = ""
            images = []
            article_imgs = item.select(".moment-article img")
            if article_imgs:
                for img_el in article_imgs:
                    src = img_el.get("src", img_el.get("data-src", ""))
                    if src and "avatar" not in src:
                        if not src.startswith("http"):
                            src = "https:" + src
                        images.append(src)
                image = images[0] if images else ""
            else:
                # 找卡片里其他图片
                img = item.find("img")
                if img and "avatar" not in img.get("src", ""):
                    src = img.get("src", "")
                    if src:
                        if not src.startswith("http"):
                            src = "https:" + src
                        images.append(src)
                    image = src

            if image and not image.startswith("http"):
                image = "https:" + image

            # 分类标签
            tag_elem = item.find("a", href=re.compile(r"/group-label/"))
            tag = tag_elem.get_text(strip=True) if tag_elem else ""

            # 摘要
            summary = title
            if tag:
                summary = f"[{tag}] {summary}"

            # 生成完整内容
            html_parts = []
            if tag:
                html_parts.append(f"<p><strong>[{tag}]</strong></p>")
            paragraphs = [p.strip() for p in summary_elem.get_text('\n').split('\n') if p.strip()]
            for p in paragraphs:
                html_parts.append(f"<p>{p}</p>")
            for img_url in images:
                html_parts.append(f'<p><img src="{img_url}" alt="" style="max-width:100%;border-radius:8px;"></p>')
            full_content = "\n".join(html_parts) if html_parts else f"<p>{title}</p>"

            result.append({
                "id": f"taptap_{app_id}_{link.split('/')[-1].split('?')[0]}",
                "game": game_name,
                "title": title,
                "link": link,
                "pubDate": pub_date.strftime("%Y-%m-%d %H:%M:%S"),
                "summary": clean_summary(summary),
                "image": image,
                "images": images,
                "content": full_content,
                "source": "taptap",
            })
        
        print(f"[信息] TapTap抓取成功 ({game_name}): {len(result)} 条")
        return result
    
    except Exception as e:
        print(f"[错误] 抓取TapTap动态失败 ({game_name}): {e}")
        return []


# ============================================================
# 网易大神爬虫
# 从网易大神（ds.163.com）获取游戏官方账号动态
# ============================================================

# 网易大神各游戏官方账号 UID（32位hex字符串）
DS_OFFICIAL_UIDS = {
    "第五人格": "025ee033b501461e8c86eb541974cb06",
    "燕云十六声": "c47870f2c5f142a58ea746fbc4655165",
}

DS_API_BASE = "https://inf.ds.163.com"
DS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://ds.163.com/",
    "Accept": "application/json",
}


def fetch_ds_user_feeds(uid: str, game_name: str) -> list:
    """从网易大神获取指定用户的动态"""
    if not uid:
        return []
    try:
        url = f"{DS_API_BASE}/v1/web/feed/basic/getSomeOneFeeds?feedTypes=1,2,3,4,6,7,10,11&someOneUid={uid}&pageNum=1&pageSize=20"
        resp = requests.get(url, headers=DS_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 200:
            print(f"[警告] 网易大神获取动态失败 ({game_name}): {data.get('msg', '未知错误')}")
            return []

        result = data.get("result", {})
        feeds = result.get("feeds", [])
        items = []
        cutoff_date = datetime.now() - timedelta(days=30)

        for feed in feeds:
            # 发布时间（毫秒级时间戳）
            create_time = feed.get("createTime", 0)
            pub_date = None
            if create_time:
                try:
                    # 毫秒级时间戳
                    ts = int(create_time)
                    if ts > 10**12:  # 毫秒级
                        pub_date = datetime.fromtimestamp(ts / 1000)
                    else:
                        pub_date = datetime.fromtimestamp(ts)
                except (ValueError, TypeError, OSError):
                    pass

            if pub_date and pub_date < cutoff_date:
                continue
            if not pub_date:
                pub_date = datetime.now()

            # 解析内容
            content_json = feed.get("content", "{}")
            try:
                content = json.loads(content_json)
                body = content.get("body", {})
                text = body.get("text", "")
                # 去掉话题标签（#xxx#）作为标题
                title_text = re.sub(r'#[^#]+#', '', text).strip()
                # 取第一行或前50字当标题
                lines = [l.strip() for l in title_text.split('\n') if l.strip()]
                if lines:
                    title = lines[0][:50] + ("..." if len(lines[0]) > 50 else "")
                else:
                    title = text[:50] + ("..." if len(text) > 50 else "")
                if not title:
                    title = f"{game_name}官方动态"
                # 摘要用完整文本
                summary = title_text
                if summary and len(summary) > 150:
                    summary = summary[:150] + "..."
                # 所有图片列表（过滤掉视频URL）
                image_list = []
                media = body.get("media", [])
                if media and isinstance(media, list):
                    for m in media:
                        img_url = m.get("url", "") or m.get("cover", "")
                        if img_url and not any(img_url.endswith(ext) for ext in ['.mp4', '.mp3', '.avi', '.mov', '.flv', '.m4v']):
                            if 'vod.cc.163.com' not in img_url and 'video' not in img_url.lower():
                                image_list.append(img_url)
                # 封面图（第一张）
                image = image_list[0] if image_list else ""

                # 生成完整HTML内容（用于详情页）
                # 将换行转换为 <p> 段落，图片插入到对应位置
                html_parts = []
                paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
                for p in paragraphs:
                    # 去掉 #话题# 标签
                    clean_p = re.sub(r'#[^#]+#', '', p).strip()
                    if clean_p:
                        html_parts.append(f"<p>{clean_p}</p>")

                # 所有图片放到内容末尾
                if image_list:
                    for img_url in image_list:
                        html_parts.append(f'<p><img src="{img_url}" alt="" style="max-width:100%;border-radius:8px;"></p>')

                full_content = "\n".join(html_parts) if html_parts else f"<p>{title}</p>"
            except (json.JSONDecodeError, TypeError):
                title = f"{game_name}官方动态"
                summary = ""
                image = ""
                image_list = []
                full_content = f"<p>{title}</p>"

            if not title:
                title = f"{game_name}官方动态"

            feed_id = feed.get("id", "")
            link = f"https://m.ds.163.com/article/{feed_id}" if feed_id else "https://ds.163.com/"

            items.append({
                "id": f"ds_{uid}_{feed_id}",
                "game": game_name,
                "title": title,
                "link": link,
                "pubDate": pub_date.strftime("%Y-%m-%d %H:%M:%S"),
                "summary": clean_summary(summary) if summary else title,
                "image": image,
                "images": image_list,
                "content": full_content,
                "source": "wangyi_ds",
            })

        return items

    except Exception as e:
        print(f"[错误] 抓取网易大神动态失败 ({game_name}): {e}")
        return []


# ============================================================
# 官网新闻源配置
# ============================================================

OFFICIAL_SITE_FETCHERS = {
    "第五人格": fetch_identityv_official_news,
    "终末地": fetch_endfield_official_news,
    "燕云十六声": fetch_yanyun_official_news,
}

# Steam 游戏配置（Steam News API 免费，不需要认证）
STEAM_APP_IDS = {
    "三角洲行动": "2507950",
}


def fetch_steam_news(app_id: str, game_name: str) -> list:
    """
    从 Steam News API 获取游戏新闻
    API: https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/
    免费，不需要认证，返回官方公告和新闻
    """
    try:
        url = (
            f"https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
            f"?appid={app_id}&count=20&maxlength=5000&l=schinese"
        )
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}, verify=True)
        if resp.status_code != 200:
            print(f"[Steam] HTTP {resp.status_code} ({game_name})")
            return []

        data = resp.json()
        news_items = data.get("appnews", {}).get("newsitems", [])
        if not news_items:
            print(f"[Steam] 无新闻数据 ({game_name})")
            return []

        items = []
        for news in news_items:
            title = news.get("title", "").strip()
            if not title:
                continue

            link = news.get("url", "")
            if not link:
                link = f"https://store.steampowered.com/app/{app_id}"

            pub_date = datetime.fromtimestamp(news.get("date", 0))
            # 过滤30天前的新闻
            if pub_date < datetime.now() - timedelta(days=30):
                continue

            # 提取正文
            raw_html = news.get("contents", "")
            # Steam news 使用 [img]标签 和 [url=...]链接[/url] 等 BBCode
            # 转换为 HTML
            content_html = raw_html
            # 转换 [img]URL[/img] 为 <img>
            content_html = re.sub(r'\[img\](.+?)\[/img\]', r'<img src="\1" alt="" style="max-width:100%;border-radius:8px;">', content_html)
            # 转换 [url=URL]文字[/url] 为 <a>
            content_html = re.sub(r'\[url=(.+?)\](.+?)\[/url\]', r'<a href="\1" target="_blank">\2</a>', content_html)
            # 转换 [url]URL[/url]
            content_html = re.sub(r'\[url\](.+?)\[/url\]', r'<a href="\1" target="_blank">\1</a>', content_html)
            # 转换换行为段落
            paragraphs = [p.strip() for p in content_html.split('\n') if p.strip()]
            content_parts = []
            for p in paragraphs:
                if p.startswith('<img'):
                    content_parts.append(f"<p>{p}</p>")
                elif p.startswith('<a '):
                    content_parts.append(f"<p>{p}</p>")
                else:
                    content_parts.append(f"<p>{p}</p>")
            content = "\n".join(content_parts) if content_parts else f"<p>{title}</p>"

            # 提取图片
            images = re.findall(r'\[img\](.+?)\[/img\]', raw_html)

            # 生成摘要
            summary = clean_summary(raw_html, 150)
            if not summary or len(summary) < 10:
                summary = title

            items.append({
                "id": f"steam_{app_id}_{news.get('gid', '')}",
                "game": game_name,
                "title": title,
                "link": link,
                "pubDate": pub_date.strftime("%Y-%m-%d %H:%M:%S"),
                "summary": summary,
                "image": images[0] if images else "",
                "images": images,
                "content": content,
                "source": "steam",
            })

        print(f"[Steam] 获取 {len(items)} 条新闻 ({game_name})")
        return items

    except Exception as e:
        print(f"[错误] Steam 新闻获取失败 ({game_name}): {e}")
        return []


# ============================================================
# 小红书笔记爬虫（通过 SSR __INITIAL_STATE__ 数据提取）
# 不需要Cookie，从页面HTML中直接解析服务端渲染数据
# ============================================================

XHS_PROFILES = {
    "三角洲行动": "63205dd8000000002303aaa7",
}

XHS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.xiaohongshu.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_xiaohongshu_notes(user_id: str, game_name: str) -> list:
    """
    从小红书用户主页提取笔记（SSR数据，不需要Cookie）
    通过解析页面 __INITIAL_STATE__ 获取笔记列表
    """
    try:
        url = f"https://www.xiaohongshu.com/user/profile/{user_id}"
        resp = requests.get(url, headers=XHS_HEADERS, timeout=15)
        resp.raise_for_status()

        # 从HTML中提取 __INITIAL_STATE__
        match = re.search(
            r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>',
            resp.text,
            re.DOTALL
        )
        if not match:
            print(f"[小红书] 未找到 __INITIAL_STATE__ ({game_name})")
            return []

        # 解析JSON（小红书数据中有undefined，需要替换为null）
        raw_json = match.group(1).replace('undefined', 'null')
        state = json.loads(raw_json)

        # 提取笔记列表：state.user.notes[0] 是第一个标签页的笔记
        notes_data = state.get("user", {}).get("notes", [])
        if not notes_data or not isinstance(notes_data, list):
            print(f"[小红书] 无笔记数据 ({game_name})")
            return []

        # notes_data[0] 是一个列表，包含实际笔记对象
        notes = notes_data[0] if isinstance(notes_data[0], list) else []
        if not notes:
            print(f"[小红书] 笔记列表为空 ({game_name})")
            return []

        items = []
        cutoff_date = datetime.now() - timedelta(days=30)
        profile_url = f"https://www.xiaohongshu.com/user/profile/{user_id}"

        for note in notes:
            if not isinstance(note, dict):
                continue

            nc = note.get("noteCard", {})
            if not nc:
                continue

            title = nc.get("displayTitle", "").strip()
            if not title or len(title) < 2:
                continue

            # 时间戳（毫秒级）
            ts = nc.get("time", 0)
            pub_date = None
            if ts:
                try:
                    pub_date = datetime.fromtimestamp(ts / 1000)
                except (ValueError, TypeError, OSError):
                    pass

            if pub_date and pub_date < cutoff_date:
                continue
            if not pub_date:
                pub_date = datetime.now()

            # 封面图
            cover = nc.get("cover", {})
            if isinstance(cover, dict):
                image_url = cover.get("urlDefault", "") or cover.get("urlPre", "")
                # 从 infoList 获取备选
                if not image_url:
                    info_list = cover.get("infoList", [])
                    if info_list and isinstance(info_list, list):
                        for info in info_list:
                            if isinstance(info, dict) and info.get("url"):
                                image_url = info["url"]
                                break
            else:
                image_url = ""

            # 笔记链接（noteId为空时使用用户主页）
            xsec_token = nc.get("xsecToken", note.get("xsecToken", ""))
            note_id = nc.get("noteId", note.get("id", ""))
            if note_id:
                link = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}&xsec_source=pc_user" if xsec_token else f"https://www.xiaohongshu.com/explore/{note_id}"
            else:
                link = profile_url

            # 笔记类型
            note_type = nc.get("type", "normal")

            # 摘要
            desc = nc.get("desc", "")
            if desc:
                summary = desc[:150] + ("..." if len(desc) > 150 else "")
            else:
                summary = title

            # 图片列表
            image_list = []
            for img in nc.get("imageList", []):
                if isinstance(img, dict):
                    img_url = img.get("urlDefault", "") or img.get("urlPre", "")
                    if img_url:
                        image_list.append(img_url)
            if image_url and image_url not in image_list:
                image_list.insert(0, image_url)

            # 正文内容
            if desc:
                content = f"<p>{desc}</p>"
            else:
                content = f"<p>{title}</p><p>详情请查看原文链接。</p>"

            # 互动数据
            interact = nc.get("interactInfo", {})
            liked_count = interact.get("likedCount", "0") if isinstance(interact, dict) else "0"

            items.append({
                "id": f"xhs_{user_id}_{hashlib.md5(title.encode()).hexdigest()[:12]}",
                "game": game_name,
                "title": title,
                "link": link,
                "pubDate": pub_date.strftime("%Y-%m-%d %H:%M:%S"),
                "summary": summary,
                "image": image_url,
                "images": image_list,
                "content": content,
                "source": "xiaohongshu",
            })

        print(f"[小红书] 获取 {len(items)} 条笔记 ({game_name})")
        return items

    except Exception as e:
        print(f"[错误] 小红书笔记获取失败 ({game_name}): {e}")
        return []


def fetch_game_news_direct(game_name: str) -> list:
    """
    直接抓取单个游戏的所有资讯
    按方案一配置数据源优先级：
    - 原神/崩铁/绝区零 → 米游社API(主) + B站文章(补)
    - 三角洲行动 → Steam News API + 小红书(主) + B站文章/搜索(补)
    - 终末地 → 鹰角官网bulletins(主) + B站文章/搜索(补)
    - 第五人格 → 网易大神API(主)
    - 燕云十六声 → 网易大神API(主) + RSSHub/3DMGame(补)
    通用补充：B站动态 + RSSHub(当数据不足时)
    """
    all_items = []

    # ============================================================
    # 1. 主数据源（按游戏配置）
    # ============================================================

    # 米游社API → 原神/崩铁/绝区零
    if game_name in MIYOUSHE_GIDS:
        cache_key = f"mys:{game_name}"
        cached = get_cached(cache_key)
        if cached is not None:
            all_items.extend(cached)
        else:
            items = fetch_miyoushe_news(MIYOUSHE_GIDS[game_name], game_name)
            set_cached(cache_key, items)
            all_items.extend(items)

    # Steam News API → 三角洲行动
    if game_name in STEAM_APP_IDS:
        cache_key = f"steam:{game_name}"
        cached = get_cached(cache_key)
        if cached is not None:
            all_items.extend(cached)
        else:
            items = fetch_steam_news(STEAM_APP_IDS[game_name], game_name)
            set_cached(cache_key, items)
            all_items.extend(items)

    # 小红书笔记 → 三角洲行动（中文社区内容，不需要Cookie）
    if game_name in XHS_PROFILES:
        cache_key = f"xhs:{game_name}"
        cached = get_cached(cache_key)
        if cached is not None:
            all_items.extend(cached)
        else:
            items = fetch_xiaohongshu_notes(XHS_PROFILES[game_name], game_name)
            set_cached(cache_key, items)
            all_items.extend(items)

    # 鹰角官网bulletins → 终末地（森空岛无公开API，使用官网替代）
    if game_name == "终末地":
        cache_key = f"endfield:{game_name}"
        cached = get_cached(cache_key)
        if cached is not None:
            all_items.extend(cached)
        else:
            items = fetch_endfield_official_news()
            set_cached(cache_key, items)
            all_items.extend(items)

    # 网易大神API → 第五人格/燕云十六声
    if game_name in DS_OFFICIAL_UIDS and DS_OFFICIAL_UIDS[game_name]:
        cache_key = f"ds:{game_name}"
        cached = get_cached(cache_key)
        if cached is not None:
            all_items.extend(cached)
        else:
            items = fetch_ds_user_feeds(DS_OFFICIAL_UIDS[game_name], game_name)
            set_cached(cache_key, items)
            all_items.extend(items)

    # ============================================================
    # 2. 补充数据源（B站文章，所有游戏通用，不需要cookie）
    # ============================================================
    if game_name in BILIBILI_UIDS:
        cache_key = f"bili_art:{game_name}"
        cached = get_cached(cache_key)
        if cached is not None:
            all_items.extend(cached)
        else:
            items = fetch_bilibili_articles(BILIBILI_UIDS[game_name], game_name)
            set_cached(cache_key, items)
            all_items.extend(items)

    # ============================================================
    # 3. B站动态（所有游戏，常被412拦截，作为补充）
    # ============================================================
    if game_name in BILIBILI_UIDS:
        cache_key = f"bili:{game_name}"
        cached = get_cached(cache_key)
        if cached is not None:
            all_items.extend(cached)
        else:
            items = fetch_bilibili_dynamic(BILIBILI_UIDS[game_name], game_name)
            set_cached(cache_key, items)
            all_items.extend(items)

    # ============================================================
    # 4. B站搜索补充（当数据不足时，搜索社区文章）
    # ============================================================
    BILI_SEARCH_KEYWORDS = {
        "三角洲行动": "三角洲行动",
        "终末地": "明日方舟终末地",
    }
    if game_name in BILI_SEARCH_KEYWORDS and len(all_items) < 5:
        keyword = BILI_SEARCH_KEYWORDS[game_name]
        print(f"[B站搜索] {game_name} 不足({len(all_items)}条)，搜索补充...")
        cache_key = f"bili_search:{game_name}"
        cached = get_cached(cache_key)
        if cached is not None:
            all_items.extend(cached)
        else:
            items = fetch_bilibili_search_articles(keyword, game_name, max_results=10)
            set_cached(cache_key, items)
            all_items.extend(items)

    # ============================================================
    # 5. RSSHub/3DMGame 补充（当数据不足时的最终回退）
    # ============================================================
    if not all_items or len(all_items) < 5:
        rss_base = get_rsshub_base()
        if rss_base and game_name in RSSHUB_ROUTES:
            print(f"[RSSHub] {game_name} 不足({len(all_items)}条)，RSSHub补充...")
            cache_key = f"rss:{game_name}"
            cached = get_cached(cache_key)
            if cached is not None:
                all_items.extend(cached)
            else:
                routes = RSSHUB_ROUTES[game_name]
                keywords = RSSHUB_KEYWORDS.get(game_name)
                for route in routes:
                    rss_url = f"{rss_base}{route}"
                    print(f"[RSSHub] 尝试路由: {route}")
                    rss_items = fetch_rss_feed(rss_url, game_name, "rsshub", keywords=keywords)
                    print(f"[RSSHub]   -> {len(rss_items)} 条")
                    all_items.extend(rss_items)
                set_cached(cache_key, all_items.copy())
        elif not rss_base:
            print(f"[RSSHub] {game_name} 无可用镜像，跳过")

    # 去重
    all_items = deduplicate_news(all_items)

    return all_items


# ============================================================
# RSS 解析与数据清洗
# 使用 feedparser 将 RSSHub 的 XML 输出转换为统一的数据结构
# ============================================================

def extract_images_from_html(html_content: str) -> list:
    """从 HTML 内容中提取所有图片 URL"""
    if not html_content:
        return []
    urls = []
    soup = BeautifulSoup(html_content, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
        if src and src.startswith("http"):
            urls.append(src)
    return urls


def extract_image_from_html(html_content: str) -> str:
    """从 HTML 内容中提取第一张图片的 URL"""
    urls = extract_images_from_html(html_content)
    return urls[0] if urls else ""


def build_content_html(raw_html: str, max_length: int = 5000) -> str:
    """将 RSS 原始 HTML 清洗为干净的正文 HTML"""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    # 移除 script 和 style 标签
    for tag in soup.find_all(["script", "style", "iframe"]):
        tag.decompose()
    # 提取段落
    html_parts = []
    for el in soup.find_all(["p", "div", "span", "li", "h1", "h2", "h3", "br"]):
        if el.name == "br":
            continue
        text = el.get_text(strip=True)
        if text and len(text) > 2:
            html_parts.append(f"<p>{text}</p>")
    # 保留原始 HTML 中的图片
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if src and src.startswith("http"):
            html_parts.append(f'<p><img src="{src}" alt="" style="max-width:100%;border-radius:8px;"></p>')
    # 去重并保留顺序
    seen = set()
    unique_parts = []
    for part in html_parts:
        text = part.replace("<p>", "").replace("</p>", "").replace('<img ', '').strip()
        key = text[:80] if text else ""
        if key and key not in seen:
            seen.add(key)
            unique_parts.append(part)
    result = "\n".join(unique_parts)
    if len(result) > max_length:
        result = result[:max_length] + "..."
    return result


def clean_summary(html_content: str, max_length: int = 150) -> str:
    """从 HTML 中提取纯文本摘要"""
    if not html_content:
        return ""
    text = re.sub(r'<[^>]+>', '', html_content)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > max_length:
        text = text[:max_length] + "..."
    return text


def parse_date(date_str: str) -> Optional[datetime]:
    """解析各种格式的日期字符串"""
    if not date_str:
        return None
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def fetch_rss_feed(url: str, game_name: str, source_name: str = "rsshub", keywords: list = None) -> list:
    """
    从单个 RSS 源拉取并解析资讯
    将 RSSHub 的 RSS/Atom 输出转换为统一的数据结构：
    {title, pubDate, link, content, images, summary, image, source}
    keywords: 可选关键词过滤列表，标题或摘要中包含任一关键词才保留
    """
    try:
        feed = feedparser.parse(url)

        if feed.bozo and not feed.entries:
            print(f"[警告] RSS源解析失败 ({game_name}): {url}, 错误: {feed.bozo_exception}")
            return []

        if not feed.entries:
            print(f"[信息] RSS源无数据 ({game_name}): {url}")
            return []

        items = []
        cutoff_date = datetime.now() - timedelta(days=30)
        filtered_out = 0

        for entry in feed.entries:
            # 解析日期
            pub_date = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                pub_date = datetime(*entry.published_parsed[:6])
            elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                pub_date = datetime(*entry.updated_parsed[:6])
            elif hasattr(entry, 'published'):
                pub_date = parse_date(entry.published)

            if pub_date is None:
                pub_date = datetime.now()

            if pub_date.replace(tzinfo=None) < cutoff_date.replace(tzinfo=None):
                continue

            title = entry.get('title', '无标题').strip()
            link = entry.get('link', '')

            # 关键词过滤：标题或摘要中包含任一关键词才保留
            if keywords:
                raw_check = ""
                if hasattr(entry, 'summary'):
                    raw_check = entry.summary or ""
                elif hasattr(entry, 'description'):
                    raw_check = entry.description or ""
                check_text = (title + " " + raw_check).lower()
                if not any(kw.lower() in check_text for kw in keywords):
                    filtered_out += 1
                    continue

            # 提取正文 HTML
            raw_html = ""
            if hasattr(entry, 'content') and entry.content:
                raw_html = entry.content[0].get('value', '')
            elif hasattr(entry, 'summary'):
                raw_html = entry.summary
            elif hasattr(entry, 'description'):
                raw_html = entry.description

            # 提取图片列表
            images = extract_images_from_html(raw_html)
            image = images[0] if images else ""

            # 构建干净的正文内容
            content = build_content_html(raw_html)

            # 生成摘要
            summary = clean_summary(raw_html, 150)
            if not summary or len(summary) < 10:
                summary = title

            items.append({
                "id": hashlib.md5(f"{game_name}_{link}_{title}".encode()).hexdigest()[:12],
                "game": game_name,
                "title": title,
                "link": link,
                "pubDate": pub_date.strftime("%Y-%m-%d %H:%M:%S"),
                "summary": summary,
                "image": image,
                "images": images,
                "content": content,
                "source": source_name,
            })

        if filtered_out > 0:
            print(f"[RSS] 关键词过滤: 留{len(items)}条/滤去{filtered_out}条 ({game_name})")
        return items

    except Exception as e:
        print(f"[错误] 拉取RSS失败 ({game_name}): {url}, 异常: {e}")
        return []


def deduplicate_news(news_list: list) -> list:
    """根据标题去重（相似度判断）"""
    seen_titles = set()
    result = []
    for item in news_list:
        # 用标题哈希去重，忽略空白和标点
        clean_title = re.sub(r'[\s\W_]+', '', item['title']).lower()
        if not clean_title:
            continue
        title_hash = hashlib.md5(clean_title.encode('utf-8')).hexdigest()
        if title_hash not in seen_titles:
            seen_titles.add(title_hash)
            result.append(item)
    return result


# ============================================================
# API 接口
# ============================================================

@app.get("/api/news")
def get_news(game: str = None, source: str = "direct"):
    """
    获取游戏资讯
    - game: 可选，指定游戏名称（原神/崩铁/绝区零/终末地/第五人格/三角洲行动）
    - source: 数据源模式
        - direct: 直接调用 B站+米游社+官网 API（推荐，稳定，默认）
        - rsshub: 通过 RSSHub 抓取
    - 返回按时间倒序排列的资讯列表
    """
    # 确定要查询的游戏
    all_games = list(BILIBILI_UIDS.keys())
    if game and game in all_games:
        games_to_fetch = [game]
    else:
        games_to_fetch = all_games

    all_news = []

    if source == "rsshub":
        # RSSHub 模式（备用）
        game_sources = build_game_rss_sources()
        for game_name in games_to_fetch:
            if game_name not in game_sources:
                continue
            sources = game_sources[game_name]
            keywords = RSSHUB_KEYWORDS.get(game_name)
            for source_url in sources:
                cache_key = f"rss:{game_name}:{source_url}"
                cached = get_cached(cache_key)
                if cached is not None:
                    all_news.extend(cached)
                    continue
                items = fetch_rss_feed(source_url, game_name, keywords=keywords)
                set_cached(cache_key, items)
                all_news.extend(items)
    else:
        # 直连模式（默认推荐）
        for game_name in games_to_fetch:
            items = fetch_game_news_direct(game_name)
            all_news.extend(items)

    # 去重
    all_news = deduplicate_news(all_news)

    # 按时间倒序排列
    all_news.sort(key=lambda x: x['pubDate'], reverse=True)

    return all_news


@app.get("/api/test")
def test():
    """健康检查接口"""
    return {
        "status": "ok",
        "message": "后端服务正常运行！",
        "configured_games": list(BILIBILI_UIDS.keys()),
        "default_source": "direct (B站+米游社+官网直连)",
        "bilibili_accounts": BILIBILI_UIDS,
        "miyoushe_gids": MIYOUSHE_GIDS,
        "official_sites": list(OFFICIAL_SITE_FETCHERS.keys()),
    }


@app.get("/api/news/detail")
def get_news_detail(news_id: str):
    """
    获取资讯详情（完整正文）
    - news_id: 资讯ID，格式如 mys_2_77537726 或 bili_401742377_xxx
    """
    if not news_id:
        return {"error": "缺少 news_id 参数"}

    parts = news_id.split("_", 2)
    if len(parts) < 3:
        return {"error": "无效的 news_id 格式"}

    source = parts[0]
    gid_or_uid = parts[1]
    item_id = parts[2]

    if source == "mys":
        # 米游社帖子详情
        try:
            url = f"https://api-takumi.mihoyo.com/post/wapi/getPostFull?post_id={item_id}&gids={gid_or_uid}"
            resp = requests.get(url, headers=MIYOUSHE_HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            if data.get("retcode") != 0:
                return {"error": data.get("message", "获取详情失败")}

            # 米游社详情 API 返回结构是 data.post.post（多一层 post）
            post_data = data.get("data", {}).get("post", {})
            post = post_data.get("post", post_data)  # 兼容两种结构

            content = post.get("content", "")
            title = post.get("subject", "")
            images = post.get("images", [])
            pub_ts = post.get("created_at", 0)
            pub_date = datetime.fromtimestamp(int(pub_ts)).strftime("%Y-%m-%d %H:%M:%S") if pub_ts else ""

            # 提取所有图片 URL
            image_urls = []
            if images:
                for img in images:
                    if isinstance(img, dict):
                        url_img = img.get("url", "")
                        if url_img:
                            image_urls.append(url_img)
                    elif isinstance(img, str):
                        image_urls.append(img)

            # 如果 images 里没有，从 content 中正则提取
            if not image_urls:
                image_urls = re.findall(r'<img[^>]+src="([^"]+)"', content)

            # 从 structured_content 也提取图片（更全）
            structured = post.get("structured_content", [])
            if structured and len(image_urls) < len(structured):
                sc_images = []
                for block in structured:
                    if isinstance(block, dict):
                        insert = block.get("insert", {})
                        if isinstance(insert, dict) and "image" in insert:
                            sc_images.append(insert["image"])
                if sc_images:
                    image_urls = sc_images

            # 从 game 名反向查找（通过 gid 映射）
            gid_to_game = {v: k for k, v in MIYOUSHE_GIDS.items()}
            game_name = gid_to_game.get(gid_or_uid, "米游社")

            game_paths = {"2": "ys", "6": "sr", "8": "zzz"}
            path_prefix = game_paths.get(gid_or_uid, "ys")
            link = f"https://www.miyoushe.com/{path_prefix}/article/{item_id}"

            return {
                "id": news_id,
                "game": game_name,
                "title": title,
                "content": content,
                "images": image_urls,
                "pubDate": pub_date,
                "link": link,
                "source": "miyoushe",
            }

        except Exception as e:
            return {"error": f"获取详情失败: {str(e)}"}

    else:
        return {"error": f"暂不支持的资讯来源: {source}"}


@app.get("/api/debug-direct")
def debug_direct(game: str = "原神"):
    """
    调试接口：测试直连模式的数据源
    分别返回 B站动态、米游社公告、官网新闻 的抓取结果
    """
    result = {"game": game, "bilibili": None, "miyoushe": None, "official": None}

    # 测试 B站
    if game in BILIBILI_UIDS:
        uid = BILIBILI_UIDS[game]
        try:
            items = fetch_bilibili_dynamic(uid, game)
            result["bilibili"] = {
                "success": True,
                "uid": uid,
                "count": len(items),
                "sample": items[:3],
            }
        except Exception as e:
            result["bilibili"] = {"success": False, "error": str(e)}

    # 测试米游社
    if game in MIYOUSHE_GIDS:
        gid = MIYOUSHE_GIDS[game]
        try:
            items = fetch_miyoushe_news(gid, game)
            result["miyoushe"] = {
                "success": True,
                "gid": gid,
                "count": len(items),
                "sample": items[:3],
            }
        except Exception as e:
            result["miyoushe"] = {"success": False, "error": str(e)}

    # 测试官网新闻
    if game in OFFICIAL_SITE_FETCHERS:
        try:
            items = OFFICIAL_SITE_FETCHERS[game]()
            result["official"] = {
                "success": True,
                "count": len(items),
                "sample": items[:3],
            }
        except Exception as e:
            result["official"] = {"success": False, "error": str(e)}

    return result


@app.get("/api/debug-feed")
def debug_feed(game: str = "原神", source_index: int = 0):
    """
    调试接口：直接返回某个 RSS 源的原始解析结果
    用于排查数据源问题
    """
    game_sources = build_game_rss_sources()
    if game not in game_sources:
        return {"error": f"未找到游戏: {game}"}
    sources = game_sources[game]
    if source_index >= len(sources):
        return {"error": f"源索引超出范围，该游戏只有 {len(sources)} 个源"}
    url = sources[source_index]
    feed = feedparser.parse(url)
    return {
        "game": game,
        "source_url": url,
        "bozo": feed.bozo,
        "bozo_exception": str(feed.bozo_exception) if feed.bozo else None,
        "feed_title": feed.feed.get('title', '') if hasattr(feed, 'feed') else '',
        "entry_count": len(feed.entries),
        "sample_entries": [
            {
                "title": e.get('title', ''),
                "link": e.get('link', ''),
                "published": e.get('published', ''),
                "summary_preview": clean_summary(e.get('summary', ''), 100),
            }
            for e in feed.entries[:5]
        ]
    }
