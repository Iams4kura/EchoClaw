"""内置知识源实现 — 通用搜索、新闻、技术文档。

复用 web_search.py 中的搜索引擎基础设施，按数据源类型做查询增强和结果过滤。
"""

import logging
import re
from datetime import date
from typing import List, Optional
from urllib.parse import urlparse

from .base import KnowledgeSource, SearchResult

logger = logging.getLogger(__name__)

# 延迟导入，避免循环依赖。在 search() 内按需使用。
_web_search_tool = None


def _get_tool():  # type: ignore[no-untyped-def]
    """懒加载 WebSearchTool 实例，复用其搜索引擎方法。"""
    global _web_search_tool
    if _web_search_tool is None:
        from ..web_search import WebSearchTool
        _web_search_tool = WebSearchTool()
    return _web_search_tool


def _extract_domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _tool_result_to_search_results(
    content: str, source_name: str,
) -> List[SearchResult]:
    """将 WebSearchTool 格式化的文本结果解析回 SearchResult 列表。"""
    results: List[SearchResult] = []
    # 格式：[title](url)  (tags)\nsnippet
    blocks = content.split("\n\n")
    for block in blocks:
        if not block.strip():
            continue
        lines = block.strip().split("\n", 1)
        first_line = lines[0]
        snippet = lines[1].strip() if len(lines) > 1 else ""

        # 解析 [title](url)
        m = re.match(r"\[(.+?)]\((.+?)\)", first_line)
        if m:
            title, url = m.group(1), m.group(2)
            domain = _extract_domain(url)
            is_trusted = "trusted" in first_line
            results.append(SearchResult(
                title=title, url=url, snippet=snippet,
                source_name=source_name, domain=domain, is_trusted=is_trusted,
            ))

    return results


class WebSource(KnowledgeSource):
    """通用网络搜索 — Bing 引擎，可信源排序。"""

    name = "general"
    description = "通用网络搜索，适用于大多数问题"
    keywords: List[str] = []  # 作为默认兜底，不需要关键词
    is_default = True

    async def search(self, query: str, max_results: int = 8) -> List[SearchResult]:
        tool = _get_tool()
        from ..web_search import _quote_protect_query
        result = await tool._search_bing(_quote_protect_query(query))
        if result.is_error:
            logger.warning("WebSource search failed: %s", result.content)
            return []
        return _tool_result_to_search_results(result.content, self.name)


class NewsSource(KnowledgeSource):
    """新闻搜索 — 日期增强 + 30天时效过滤 + 新闻媒体优先。"""

    name = "news"
    description = "新闻和时事搜索，优先返回近期权威媒体报道"
    keywords: List[str] = [
        # 中文
        "新闻", "最新", "今天", "昨天", "本周", "近期", "动态",
        "发布", "公告", "事件", "热点", "头条", "快讯", "突发",
        # 英文
        "news", "latest", "today", "breaking", "headline", "update",
    ]
    is_default = False

    # 新闻权威域名 — 搜索时追加 site: 限定
    _NEWS_DOMAINS = [
        "reuters.com", "apnews.com", "bbc.com", "nytimes.com",
        "theguardian.com", "bloomberg.com",
        "xinhuanet.com", "people.com.cn", "thepaper.cn",
        "caixin.com", "36kr.com",
    ]

    async def search(self, query: str, max_results: int = 8) -> List[SearchResult]:
        tool = _get_tool()
        from ..web_search import _enhance_news_query, _quote_protect_query

        protected_query = _quote_protect_query(query)

        # 策略 1：先用原始查询搜索（带黑名单过滤 + 30 天时效过滤）
        # 原始查询最能保留用户意图，不会被日期后缀干扰
        result = await tool._search_bing(
            protected_query, filter_blocked=True, max_age_days=30,
        )
        if not result.is_error and "No results found" not in result.content:
            results = _tool_result_to_search_results(result.content, self.name)
            if len(results) >= 3:
                return results

        # 策略 2：用日期增强查询补充（结果不足时）
        enhanced = _enhance_news_query(protected_query)
        if enhanced != protected_query:
            result = await tool._search_bing(
                enhanced, filter_blocked=True, max_age_days=30,
            )
            if not result.is_error and "No results found" not in result.content:
                return _tool_result_to_search_results(result.content, self.name)

        # 策略 3：fallback — 不带时效过滤的普通搜索
        result = await tool._search_bing(protected_query, filter_blocked=True)
        if result.is_error:
            return []
        return _tool_result_to_search_results(result.content, self.name)


class TechDocsSource(KnowledgeSource):
    """技术文档搜索 — 限定技术域名，过滤低质量内容。"""

    name = "tech"
    description = "技术文档和编程搜索，限定官方文档、GitHub、StackOverflow 等高质量技术站点"
    keywords: List[str] = [
        # 中文
        "文档", "教程", "报错", "bug", "框架", "库",
        "怎么用", "如何", "配置", "安装", "部署",
        # 英文
        "docs", "documentation", "api", "tutorial", "error",
        "framework", "library", "install", "setup", "config",
        "how to", "example", "usage",
        # 技术名词（常见的）
        "python", "java", "javascript", "typescript", "go", "rust",
        "react", "vue", "fastapi", "django", "spring",
        "docker", "kubernetes", "linux", "git", "npm", "pip",
    ]
    is_default = False

    _TECH_DOMAINS = [
        "github.com", "stackoverflow.com", "developer.mozilla.org",
        "docs.python.org", "python.org", "learn.microsoft.com",
        "cloud.google.com", "docs.aws.amazon.com",
        "reactjs.org", "vuejs.org", "fastapi.tiangolo.com",
        "docs.docker.com", "kubernetes.io",
        "pkg.go.dev", "docs.rs", "crates.io",
        "npmjs.com", "pypi.org",
        "medium.com", "dev.to", "juejin.cn", "segmentfault.com",
    ]

    async def search(self, query: str, max_results: int = 8) -> List[SearchResult]:
        tool = _get_tool()

        # 策略：追加 site: 限定到技术站点
        # 用 Bing 的 site: 语法，最多拼 3 个域名避免查询太长
        top_domains = self._TECH_DOMAINS[:3]
        site_query = query + " (" + " OR ".join(
            f"site:{d}" for d in top_domains
        ) + ")"

        result = await tool._search_bing(site_query, filter_blocked=True)
        tech_results: List[SearchResult] = []

        if not result.is_error and "No results found" not in result.content:
            tech_results = _tool_result_to_search_results(result.content, self.name)

        # 如果限定域名结果太少，补充一次通用 Bing 搜索并过滤技术域名
        if len(tech_results) < 3:
            fallback = await tool._search_bing(query)
            if not fallback.is_error:
                all_results = _tool_result_to_search_results(fallback.content, self.name)
                # 过滤出技术域名的结果
                tech_domains_set = set(self._TECH_DOMAINS)
                for r in all_results:
                    if any(r.domain == d or r.domain.endswith("." + d) for d in tech_domains_set):
                        if r not in tech_results:
                            tech_results.append(r)

        return tech_results[:max_results]
