#!/usr/bin/env python3
"""
Daily GitHub Star Radar

统计过去 N 小时 GitHub 公开 Star 事件，生成“涨星最快项目排行榜”。
数据源：GH Archive hourly JSON archives。
说明：GitHub 的 Star 事件在 Events API / GH Archive 中叫 WatchEvent。
"""

from __future__ import annotations

import gzip
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests


GHARCHIVE_BASE = os.getenv("GHARCHIVE_BASE", "https://data.gharchive.org")
GITHUB_API = "https://api.github.com"


@dataclass
class RepoCandidate:
    repo_id: int
    repo_name: str
    delta_stars: int


@dataclass
class RepoReportRow:
    rank: int
    repo_name: str
    html_url: str
    delta_stars: int
    total_stars: int | None
    language: str
    explanation: str


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"环境变量 {name} 必须是整数，当前值：{raw!r}")


def truthy_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def openai_base_url_from_env() -> str | None:
    raw = (os.getenv("OPENAI_BASE_URL") or "").strip()
    if not raw:
        return None

    # Most OpenAI-compatible gateways expose the REST API under /v1 even when
    # users copy the site root as the base URL.
    base = raw.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def floor_to_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def hour_range(end_exclusive: datetime, hours: int) -> list[datetime]:
    """Return hourly timestamps [end-hours, end). datetimes must be UTC."""
    start = end_exclusive - timedelta(hours=hours)
    return [start + timedelta(hours=i) for i in range(hours)]


def gharchive_url(hour_dt: datetime) -> str:
    # GH Archive 的小时文件格式：YYYY-MM-DD-H.json.gz，其中 H 不补零
    return f"{GHARCHIVE_BASE}/{hour_dt:%Y-%m-%d}-{hour_dt.hour}.json.gz"


def iter_events_for_hour(hour_dt: datetime, retries: int = 3) -> Iterable[dict[str, Any]]:
    url = gharchive_url(hour_dt)
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=(15, 180)) as response:
                if response.status_code == 404:
                    print(f"[WARN] GH Archive file not found, skip: {url}", file=sys.stderr)
                    return
                response.raise_for_status()
                response.raw.decode_content = False

                with gzip.GzipFile(fileobj=response.raw) as gz:
                    for line in gz:
                        if not line:
                            continue
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue
                return
        except Exception as exc:  # network / gzip / chunked transfer errors
            last_error = exc
            if attempt < retries:
                sleep_s = 2 ** attempt
                print(f"[WARN] Download failed ({attempt}/{retries}) for {url}: {exc}; retry in {sleep_s}s", file=sys.stderr)
                time.sleep(sleep_s)
            else:
                print(f"[ERROR] Failed to process {url}: {last_error}", file=sys.stderr)


def collect_star_candidates(hours: list[datetime]) -> tuple[list[RepoCandidate], int]:
    """Aggregate WatchEvent stars and de-duplicate by repo_id + actor_id within the window."""
    counts: Counter[int] = Counter()
    latest_name_by_id: dict[int, str] = {}
    seen_repo_actor: set[tuple[int, int]] = set()

    for hour_dt in hours:
        print(f"[INFO] Processing GH Archive hour: {hour_dt.isoformat()} -> {gharchive_url(hour_dt)}")
        for event in iter_events_for_hour(hour_dt):
            if event.get("type") != "WatchEvent":
                continue

            payload = event.get("payload") or {}
            # GitHub 文档中 WatchEvent 的 action 只有 started，保险起见仍做判断
            if payload.get("action") not in (None, "started"):
                continue

            repo = event.get("repo") or {}
            actor = event.get("actor") or {}
            repo_id = repo.get("id")
            repo_name = repo.get("name")
            actor_id = actor.get("id")
            if not repo_id or not repo_name or not actor_id:
                continue

            key = (int(repo_id), int(actor_id))
            if key in seen_repo_actor:
                continue
            seen_repo_actor.add(key)
            counts[int(repo_id)] += 1
            latest_name_by_id[int(repo_id)] = str(repo_name)

    candidates = [
        RepoCandidate(repo_id=repo_id, repo_name=latest_name_by_id[repo_id], delta_stars=count)
        for repo_id, count in counts.most_common()
    ]
    return candidates, len(seen_repo_actor)


def github_headers(raw: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "daily-github-star-radar",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_get(path: str, raw: bool = False) -> requests.Response:
    url = path if path.startswith("http") else f"{GITHUB_API}{path}"
    response = requests.get(url, headers=github_headers(raw=raw), timeout=(15, 60))
    return response


def get_repo_details(full_name: str) -> dict[str, Any] | None:
    response = github_get(f"/repos/{full_name}")
    if response.status_code == 404:
        print(f"[WARN] Repo not found or unavailable: {full_name}", file=sys.stderr)
        return None
    if response.status_code == 403:
        print(f"[WARN] GitHub API rate limited or forbidden while fetching: {full_name}", file=sys.stderr)
        return None
    response.raise_for_status()
    return response.json()


def get_readme(full_name: str, max_chars: int = 3500) -> str:
    response = github_get(f"/repos/{full_name}/readme", raw=True)
    if response.status_code >= 400:
        return ""
    text = response.text or ""
    return clean_markdown(text)[:max_chars]


def clean_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[#>*_\-]{1,}", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def fallback_explanation(repo: dict[str, Any]) -> str:
    description = (repo.get("description") or "").strip()
    if not description:
        return "这个项目说明很少，需要点进 README 才能判断具体用途。"
    description = description.replace("|", "/").strip()
    if len(description) > 80:
        description = description[:77] + "…"
    return f"项目作者的简短说明是：{description}"


def safe_exception_summary(exc: Exception) -> str:
    parts = [exc.__class__.__name__]
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
    if status_code is not None:
        parts.append(f"status={status_code}")
    return " ".join(parts)


def generate_explanation_text(client: Any, model: str, instructions: str, prompt: str) -> str:
    try:
        response = client.responses.create(
            model=model,
            instructions=instructions,
            input=prompt,
        )
        return (response.output_text or "").strip()
    except Exception as exc:
        print(f"[WARN] Responses API failed, retry with Chat Completions: {safe_exception_summary(exc)}", file=sys.stderr)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": instructions},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    return (response.choices[0].message.content or "").strip()


def explain_for_beginner(repo: dict[str, Any], readme: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return fallback_explanation(repo)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=openai_base_url_from_env())
        model = os.getenv("OPENAI_MODEL", "gpt-5.5")
        topics = ", ".join(repo.get("topics") or [])
        prompt = f"""
请根据下面的 GitHub 项目信息，写一句中文大白话解释，让完全不懂编程的人也能听懂。

要求：
1. 只输出一句话，不要编号、不要引号、不要 Markdown。
2. 不超过 35 个中文字。
3. 说清楚“它能帮人做什么”，少用 AI、框架、SDK、CLI、数据库这类技术词；必须用时要解释成生活语言。
4. 不能编造项目没有提到的用途；不确定就保守表达。

项目名：{repo.get('full_name')}
GitHub 描述：{repo.get('description') or ''}
主要语言：{repo.get('language') or ''}
主题标签：{topics}
README 摘要：{readme[:3000]}
""".strip()
        text = generate_explanation_text(
            client=client,
            model=model,
            instructions="你是一个面向普通读者的科技资讯编辑，擅长把开源项目解释成一句人话。",
            prompt=prompt,
        )
        text = re.sub(r"^[\-•\d\.、\s]+", "", text).strip(" \"'“”‘’")
        text = text.replace("|", "/")
        return text or fallback_explanation(repo)
    except Exception as exc:
        print(f"[WARN] OpenAI explanation failed for {repo.get('full_name')}: {safe_exception_summary(exc)}", file=sys.stderr)
        return fallback_explanation(repo)


def build_report_rows(candidates: list[RepoCandidate], top_n: int, skip_forks: bool) -> list[RepoReportRow]:
    rows: list[RepoReportRow] = []

    # 多取一些候选，避免 fork / 删除 / API 失败后榜单不够
    candidate_limit = max(top_n * 4, 50)
    for candidate in candidates[:candidate_limit]:
        if len(rows) >= top_n:
            break

        repo = get_repo_details(candidate.repo_name)
        if not repo:
            continue
        if skip_forks and repo.get("fork"):
            continue
        if repo.get("archived"):
            # 归档项目通常不是“当天值得关注”的新鲜项目；如需保留可去掉这段
            continue

        full_name = repo.get("full_name") or candidate.repo_name
        readme = get_readme(full_name)
        explanation = explain_for_beginner(repo, readme)
        rows.append(
            RepoReportRow(
                rank=len(rows) + 1,
                repo_name=full_name,
                html_url=repo.get("html_url") or f"https://github.com/{full_name}",
                delta_stars=candidate.delta_stars,
                total_stars=repo.get("stargazers_count"),
                language=repo.get("language") or "-",
                explanation=explanation,
            )
        )
        time.sleep(0.2)

    return rows


def md_escape_cell(value: Any) -> str:
    text = str(value if value is not None else "-")
    return text.replace("|", "/").replace("\n", " ").strip()


def render_markdown(
    rows: list[RepoReportRow],
    report_date: datetime,
    window_start: datetime,
    window_end: datetime,
    total_star_events: int,
    skip_forks: bool,
) -> str:
    local_tz = report_date.tzinfo or timezone.utc
    local_start = window_start.astimezone(local_tz)
    local_end = window_end.astimezone(local_tz)

    lines = [
        f"# GitHub 过去 24 小时涨星最快项目榜单（{report_date:%Y-%m-%d}）",
        "",
        f"统计窗口：{local_start:%Y-%m-%d %H:%M} - {local_end:%Y-%m-%d %H:%M}（{getattr(local_tz, 'key', local_tz)}）",
        f"统计口径：基于 GH Archive 公开 WatchEvent，按 `repo_id + actor_id` 去重；本窗口共统计到 {total_star_events:,} 个去重 Star 事件。",
        f"过滤规则：{'已跳过 fork 和归档项目' if skip_forks else '未跳过 fork；已跳过归档项目'}。",
        "",
        "| 排名 | 项目 | 24h 新增 Star | 当前总 Star | 语言 | 小白解释 |",
        "|---:|---|---:|---:|---|---|",
    ]

    for row in rows:
        total = f"{row.total_stars:,}" if isinstance(row.total_stars, int) else "-"
        lines.append(
            "| {rank} | [{repo}]({url}) | {delta:,} | {total} | {lang} | {explain} |".format(
                rank=row.rank,
                repo=md_escape_cell(row.repo_name),
                url=row.html_url,
                delta=row.delta_stars,
                total=total,
                lang=md_escape_cell(row.language),
                explain=md_escape_cell(row.explanation),
            )
        )

    lines += [
        "",
        "---",
        "",
        "说明：Star 可以粗略代表项目热度，但不等于质量、稳定性或安全性；重要项目建议继续看 README、Issue 和最近提交。",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    top_n = env_int("TOP_N", 20)
    window_hours = env_int("WINDOW_HOURS", 24)
    data_delay_hours = env_int("DATA_DELAY_HOURS", 2)
    output_dir = Path(os.getenv("OUTPUT_DIR", "reports"))
    tz_name = os.getenv("TZ_NAME", "America/New_York")
    skip_forks = truthy_env("SKIP_FORKS", True)

    local_tz = ZoneInfo(tz_name)
    now_utc = datetime.now(timezone.utc)
    # GH Archive 最新小时文件可能尚未生成，默认回退 2 小时，更稳
    window_end = floor_to_hour(now_utc - timedelta(hours=data_delay_hours))
    hours = hour_range(window_end, window_hours)
    report_date = datetime.now(local_tz)

    window_start = hours[0] if hours else window_end
    print(f"[INFO] Report timezone: {tz_name}")
    print(f"[INFO] Window UTC: {window_start.isoformat()} - {window_end.isoformat()}")
    print(f"[INFO] TOP_N={top_n}, WINDOW_HOURS={window_hours}, DATA_DELAY_HOURS={data_delay_hours}")

    candidates, total_star_events = collect_star_candidates(hours)
    print(f"[INFO] Unique starred repo candidates: {len(candidates):,}")
    print(f"[INFO] Unique star events in window: {total_star_events:,}")

    rows = build_report_rows(candidates, top_n=top_n, skip_forks=skip_forks)
    markdown = render_markdown(rows, report_date, window_start, window_end, total_star_events, skip_forks)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{report_date:%Y-%m-%d}.md"
    output_path.write_text(markdown, encoding="utf-8")
    print(f"[INFO] Wrote report: {output_path}")


if __name__ == "__main__":
    main()
