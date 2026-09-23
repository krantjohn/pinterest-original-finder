<div align="center">

# 📌 Pinterest Original Finder

**Automatically discover true high-res author originals, compare resolutions, and package into ZIP.**
**自动寻找 Pinterest 图板背后真正作者原图、对比分辨率并打包导出的神器。**

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Powered by gallery-dl](https://img.shields.io/badge/engine-gallery--dl-orange.svg)](https://github.com/mikf/gallery-dl)
[![Telegram Bot](https://img.shields.io/badge/Telegram-Bot%20Supported-blue.svg)](https://core.telegram.org/bots)

[English](#-overview) | [中文说明](#-中文文档)

</div>

---

## 📖 Overview

When you save pins to a Pinterest board, Pinterest frequently compresses images down to 736p or 1080p, losing fine textures and compression details. **Pinterest Original Finder** solves this by:

1. **Parsing** all Pins from any public or private board (or single Pin / shortlink `pin.it`).
2. **Cascading Resolution Search**:
   - **Tier 1 (Outbound / Source Link)**: Inspects the artist's original post (Pixiv, Twitter/X, ArtStation, Danbooru, or source website `srcset` / full-res assets).
   - **Tier 2 (Reverse Search Fallback)**: If no source link is found or it's low-res, runs reverse image search via Yandex & SauceNAO, ranked by author domain credibility.
   - **Tier 3 (Pinterest Originals Fallback)**: Uses Pinterest's uncompressed originals version if no superior author source exists.
3. **Packaging & Delivery**: Packages all maximum-resolution images into a ZIP file along with a detailed `report.csv` and `report.json`, delivered straight to your terminal or **Telegram Bot**.

---

## 🇨🇳 中文文档

### 🌟 核心特性与 3 级画质策略

1. **优先级 1：Pin 原始出处（Outbound / Source 链接）**
   - 自动解析并清洗追踪参数（`utm_*` 等）。
   - 内置 **Pixiv**、**ArtStation**、**Danbooru / Gelbooru** 专属大图提取器。
   - 针对通用网站，深度扫描 OpenGraph、Twitter Card 大图以及 HTML `srcset`（自动挑选最大尺寸如 `7680w`）。
2. **优先级 2：反向搜图兜底（Reverse Image Search）**
   - 来源缺失或分辨率不足时，自动触发 **Yandex Images** 与 **SauceNAO** 反向搜图。
   - **域名可信度打分引擎**：将搜索结果按照可信度排序（Pixiv / Twitter / ArtStation / Behance / Cara > Boorus > 转载壁纸站）。
   - 严格进行分辨率对比，仅当搜索结果分辨率显著高于 Pinterest 压缩版本时采纳。
3. **优先级 3：Pinterest Originals 官方终极兜底**
   - 若前两层均未发现超越 Pinterest 的版本，则直接以 Pinterest 官方的 `originals` 画质保底。
   - 并在元数据报告中明确标注「未找到更高清来源（使用 Pinterest 原图）」。
4. **双重交互形态**
   - **Telegram 机器人**：手机聊天框发送图板链接，机器人异步处理并实时上报进度，完成后自动把 ZIP 包和报告直接回传至手机！
   - **CLI 命令行**：终端附带彩色进度条与漂亮的 Markdown 统计表格。

---

## 📁 项目结构

```text
pinterest-original-finder/
├── main.py                 # CLI 与 Telegram Bot 启动入口
├── bot/                    # Telegram Bot 模块
│   └── telegram_bot.py     # 异步 Bot 实现与实时进度上报
├── core/                   # 核心算法与爬虫逻辑
│   ├── board_parser.py     # 基于 gallery-dl 的图板与 Pin 解析引擎
│   ├── source_finder.py    # 来源链接抓取与大图提取（Pixiv/ArtStation/Boorus/HTML）
│   ├── reverse_search.py   # 反向搜图与域名可信度排序打分
│   ├── downloader.py       # 图片下载、尺寸探测与分辨率对比决策
│   ├── packager.py         # ZIP 归档与 CSV/JSON 报告生成（支持智能分卷）
│   └── pipeline.py         # 端到端串联控制器
├── utils/
│   └── logger.py           # Rich 彩色终端日志
├── config.py               # 全局配置中心
├── requirements.txt        # Python 依赖清单
├── .env.example            # 环境变量配置模板
└── README.md
```

---

## 🚀 快速上手 (Quick Start)

### 1. 安装环境

要求 Python 3.10+：

```bash
git clone https://github.com/your-username/pinterest-original-finder.git
cd pinterest-original-finder

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 方式 A：命令行运行 (CLI)

```bash
# 解析图板（支持完整 URL、单张 Pin、手机端 pin.it 短链接）
python main.py "https://www.pinterest.com/nasa/humans-in-space/" --limit 10

# 自定义导出目录
python main.py "https://pin.it/xxxxxx" -o ./my_downloads
```

终端执行完毕后会输出详细明细表格：
```text
                        Pin Processing Summary                         
┏━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━┓
┃ Pin ID           ┃ Title                     ┃ Status / Source       ┃ Pinterest Res ┃ Final Res     ┃ Higher?  ┃      Size ┃
┡━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━┩
│ 113842561208498… │ -                         │ reverse_search        │ 850x1327      │ 3576x4000     │ +1168.1% │ 5780.1 KB │
│ 113842561208498… │ Mika Misono               │ source_outbound       │ 1115x1859     │ 1200x2000     │   +15.8% │ 1693.0 KB │
└──────────────────┴───────────────────────────┴───────────────────────┴───────────────┴───────────────┴──────────┴───────────┘
```

### 3. 方式 B：Telegram 机器人运行

1. 在 Telegram 联系官方 [@BotFather](https://t.me/BotFather) 输入 `/newbot` 获取 `API Token`。
2. 复制 `.env.example` 为 `.env` 并填入 Token：
   ```bash
   cp .env.example .env
   # 编辑 .env: TELEGRAM_BOT_TOKEN=你的Token
   ```
3. 启动机器人：
   ```bash
   python main.py --bot
   ```
4. 在手机 Telegram 中私聊机器人发送图板链接，等待片刻即可收到打包好的 ZIP 文件！

---

## ⚙️ 进阶特性

- **私密图板 (Secret Boards)**：将浏览器的 Pinterest cookies 导出保存为项目根目录的 `cookies.txt`，即可自动解析私密图板。
- **二次元与画师强化 (SauceNAO)**：在 `.env` 中配置 `SAUCENAO_API_KEY`，可大幅提升 Pixiv / 插画的反查速度与每日配额。
- **超大体积自动分卷**：若打包文件超过 49MB（Telegram Bot 上传上限），系统会自动拆分为 Part 1, Part 2 分卷发送。

---

## 📄 开源许可 (License)

本项目采用 [MIT 许可证](LICENSE)。仅供个人学习、图像备份与技术研究使用。请尊重原作者版权。
