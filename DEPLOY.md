# GameHub 自动运营部署指南

## 🎯 推荐方案：GitHub Pages + Actions（零成本全自动）

把前端和静态数据都托管在 GitHub Pages 上，GitHub Actions 每天定时抓取资讯并自动更新。

### 优点
- **零成本**：GitHub Pages 和 Actions 免费额度完全够用
- **全自动**：每天定时更新，不需要自己维护服务器
- **稳定可靠**：GitHub 全球 CDN，访问速度快
- **历史记录**：每次更新都有 git 提交记录，可回溯

---

## 📦 一键部署步骤

### 1. 准备 GitHub 仓库

```bash
cd /Users/yuluji/Desktop/game-calendar-backend

# 初始化 git（如果还没有）
git init
git add main.py export_data.py requirements.txt run_local.sh .github/
git commit -m "init: GameHub backend"

# 创建 GitHub 仓库后，推送代码
git remote add origin https://github.com/你的用户名/gamehub.git
git branch -M main
git push -u origin main
```

### 2. 开启 GitHub Pages

1. 进入仓库 → Settings → Pages
2. Source 选择 **GitHub Actions**
3. 保存

### 3. 手动触发第一次构建

1. 进入仓库 → Actions → "Update GameHub Data"
2. 点击 **Run workflow** → 选择 main 分支 → 运行
3. 等 1-2 分钟，Pages 就部署好了

### 4. 访问你的 GameHub

部署成功后，访问地址是：
```
https://你的用户名.github.io/gamehub/
```

---

## ⏰ 更新频率配置

默认配置（`.github/workflows/update-data.yml`）：
- 每天 4 次：北京时间 8:00 / 12:00 / 18:00 / 22:00
- 推送代码时也会自动更新

修改频率：编辑 `.github/workflows/update-data.yml` 中的 cron 表达式：
```yaml
schedule:
  - cron: '0 0,4,10,14 * * *'  # UTC 时间 = 北京时间 +8小时
```

常用 cron 示例：
| 频率 | cron (UTC) | 北京时间 |
|------|-----------|----------|
| 每小时 | `0 * * * *` | 每小时 |
| 每天2次 | `0 0,12 * * *` | 8:00 / 20:00 |
| 每天4次 | `0 0,4,10,14 * * *` | 8:00 / 12:00 / 18:00 / 22:00 |

---

## 💻 本地开发模式

不想部署到 GitHub？本地也可以自动运行：

### 方式一：手动运行
```bash
cd /Users/yuluji/Desktop/game-calendar-backend
source venv/bin/activate
python export_data.py
# 生成的文件在 dist/data/ 目录
```

### 方式二：crontab 定时（Mac/Linux）
```bash
# 编辑 crontab
crontab -e

# 添加（每6小时更新一次）
0 */6 * * * cd /Users/yuluji/Desktop/game-calendar-backend && ./run_local.sh >> /tmp/gamehub.log 2>&1

# 保存退出
```

### 方式三：Mac launchd（更稳定）
创建 `~/Library/LaunchAgents/com.gamehub.update.plist`：
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.gamehub.update</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/yuluji/Desktop/game-calendar-backend/run_local.sh</string>
    </array>
    <key>StartInterval</key>
    <integer>21600</integer>
    <key>StandardOutPath</key>
    <string>/tmp/gamehub.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/gamehub.err</string>
</dict>
</plist>
```
```bash
# 加载
launchctl load ~/Library/LaunchAgents/com.gamehub.update.plist
```

---

## 📁 项目文件结构

```
game-calendar-backend/
├── main.py                    # 后端API + 所有爬虫逻辑
├── export_data.py             # 静态数据导出脚本
├── requirements.txt           # Python依赖
├── run_local.sh               # 本地运行脚本
├── index.html                 # 前端页面
├── .github/
│   └── workflows/
│       └── update-data.yml    # GitHub Actions 自动更新配置
└── dist/                      # 构建输出（Pages部署内容）
    ├── index.html
    └── data/
        ├── news.json          # 全部资讯
        ├── news_原神.json     # 分游戏资讯
        ├── news_崩铁.json
        └── ...
```

---

## 🔧 新增游戏的方法

编辑 `main.py`，在对应字典里添加配置：

```python
# B站UID
BILIBILI_UIDS = {
    "新游戏名": "B站UID",
}

# 米游社（米哈游系游戏）
MIYOUSHE_GIDS = {
    "新游戏名": "板块ID",
}

# 网易大神（网易系游戏）
DS_OFFICIAL_UIDS = {
    "新游戏名": "大神UID",
}

# TapTap
TAPTAP_CONFIG = {
    "新游戏名": {
        "app_id": "游戏ID",
        "user_id": "官方用户ID",
    },
}

# 官网爬虫
OFFICIAL_SITE_FETCHERS = {
    "新游戏名": fetch_xxx_official_news,
}
```

改完后提交到 GitHub，Actions 会自动重新构建。

---

## 📊 监控和排错

### GitHub Actions 日志
仓库 → Actions → 点击对应的 workflow run → 查看每个步骤的输出

### 常见问题

**Q: 某个数据源突然抓不到了？**
A: 可能是网站反爬或页面结构变了。查看 Actions 日志里的错误信息，对应调整爬虫代码。

**Q: GitHub Actions 分钟数不够用？**
A: 免费额度每月 2000 分钟，每次运行约 1 分钟，每天 4 次 = 每月 120 次 = 120 分钟，完全够用。

**Q: 想要更快的更新频率？**
A: 可以，但不建议少于每小时一次，避免给目标网站造成压力，也容易被封IP。

---

## 🚀 进阶玩法

1. **自定义域名**：在 Pages 设置里绑定自己的域名
2. **通知推送**：有新资讯时通过 Server酱 / 飞书机器人 推送通知
3. **数据备份**：历史数据都在 git 里，可以做趋势分析
4. **多端同步**：手机浏览器直接访问 GitHub Pages 地址
