# Daily GitHub Star Radar

每天早上自动生成「GitHub 过去 24 小时涨星最快项目榜单」，并把每个项目用一句小白能听懂的话解释清楚。

## 它怎么工作

1. 从 GH Archive 下载过去 24 小时的 GitHub 公开事件小时文件。
2. 只统计 `WatchEvent`，也就是用户给仓库点 Star 的事件。
3. 按 `repo_id + actor_id` 去重，避免同一个用户重复 star / unstar / star 造成重复计数。
4. 调 GitHub API 补充项目简介、总 Star、语言、README。
5. 用 OpenAI API 把项目信息压缩成一句中文大白话说明。
6. 生成 Markdown 文件到 `reports/YYYY-MM-DD.md`，并自动提交回仓库。

## 快速使用

1. 新建一个 GitHub 仓库。
2. 把本目录里的文件复制进去。
3. 在仓库设置里添加 Secret：
   - `OPENAI_API_KEY`：用于生成“小白解释”。不填也能跑，但解释会退化成 GitHub 原始描述。
   - `OPENAI_BASE_URL`：可选。使用 OpenAI 兼容网关时填写；如果填的是站点根地址，脚本会自动补成 `/v1`。
4. 推送到默认分支。
5. GitHub Actions 会每天 `America/New_York` 08:10 自动运行；也可以在 Actions 页面手动点 `Run workflow`。

## 常用配置

在 `.github/workflows/daily.yml` 里改环境变量：

- `TOP_N`：榜单数量，默认 20。
- `WINDOW_HOURS`：统计窗口，默认过去 24 小时。
- `DATA_DELAY_HOURS`：数据延迟，默认 2 小时，避免 GH Archive 最新文件还没生成。
- `TZ_NAME`：报告时区，默认 `America/New_York`。
- `SKIP_FORKS`：是否跳过 fork 项目，默认 `true`。
- `OPENAI_MODEL`：用于生成解释的模型，默认 `gpt-5.5`。
- `OPENAI_BASE_URL`：可选，自定义 OpenAI 兼容接口地址。

## 本地运行

```bash
pip install -r requirements.txt
export GITHUB_TOKEN="你的 GitHub token，可选但推荐"
export OPENAI_API_KEY="你的 OpenAI API key"
export OPENAI_BASE_URL="你的 OpenAI 兼容接口地址，可选"
python daily_github_star_radar.py
```

生成结果会出现在 `reports/` 目录。

## 自检

仓库包含不访问网络的单元测试，覆盖小时窗口计算、GH Archive gzip 解析、Star 去重和 Markdown 渲染：

```bash
python -m unittest discover -s tests -v
```

想快速验证脚本入口、但不下载完整 GH Archive 数据，可以把统计窗口设为 0：

```bash
WINDOW_HOURS=0 TOP_N=1 OUTPUT_DIR=/tmp/star-radar-smoke python daily_github_star_radar.py
```

## 注意

Star 增长只能代表“被关注得快”，不代表代码质量、安全性或长期维护质量。重要项目建议继续看 README、Issue、Release 和最近提交。
