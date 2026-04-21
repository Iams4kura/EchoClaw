"""WebSearchTool - Web search functionality.

搜索链路（按优先级）：
  1. 百度 AI 搜索（首选）—— 通过千帆 API 调用，返回 AI 总结 + 引用来源
  2. 知识源路由（降级）—— 按查询意图匹配新闻/技术/通用源，内部走 Bing
  3. 纯 Bing（兜底）—— cn.bing.com

百度 AI 搜索需要配置环境变量 BAIDU_AI_SEARCH_API_KEY（千帆平台 API Key）。
未配置时自动跳过，走知识源路由 → Bing 降级链路。

新闻类查询自动增强（追加日期限定），通过可信源白名单排序结果。
"""

import asyncio
import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

from .base import BaseTool, PermissionCategory
from ..models.tool import ToolResult

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
}

_TAG_RE = re.compile(r"<[^>]+>")

# ---- 可信源配置 ----

_TRUSTED_DOMAINS: set[str] = {
    # 国际通讯社 / 主流媒体
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk",
    "nytimes.com", "washingtonpost.com", "theguardian.com",
    "economist.com", "ft.com", "bloomberg.com", "wsj.com",
    "nature.com", "science.org", "arxiv.org",
    # 科技媒体
    "techcrunch.com", "arstechnica.com", "theverge.com", "wired.com",
    # 中文权威媒体
    "xinhuanet.com", "people.com.cn", "chinadaily.com.cn",
    "thepaper.cn", "caixin.com", "yicai.com",
    "36kr.com", "geekpark.net", "sspai.com",
    # 官方 / 学术
    "gov.cn", "github.com", "stackoverflow.com",
    "wikipedia.org", "zhihu.com",
    # 技术文档
    "python.org", "docs.python.org", "developer.mozilla.org",
    "learn.microsoft.com", "cloud.google.com",
}

# 低质量域名黑名单
_BLOCKED_DOMAINS: set[str] = {
    "jingyan.baidu.com",
    "zhidao.baidu.com",
    "wenku.baidu.com",
    "baijiahao.baidu.com",
    "dictionary.cambridge.org",
    "english.stackexchange.com",
    "doubao.zhanlian.net",
}

# 新闻类查询关键词
_NEWS_KEYWORDS_ZH = re.compile(
    r"新闻|最新|今[天日]|昨[天日]|本[周月]|近期|动态|发布|公告|事件|热[点搜]|头条|快讯|速报|突发"
)
_NEWS_KEYWORDS_EN = re.compile(
    r"\b(news|latest|today|yesterday|recent|update|announce|breaking|headline)\b",
    re.IGNORECASE,
)



def _strip_tags(text: str) -> str:
    """清除 HTML 标签并解码常见实体。"""
    text = _TAG_RE.sub("", text)
    for old, new in (
        ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#x27;", "'"), ("&#39;", "'"),
        ("&nbsp;", " "), ("&ensp;", " "), ("&#0183;", "·"),
    ):
        text = text.replace(old, new)
    return text.strip()


def _extract_domain(url: str) -> str:
    """提取 URL 的主域名（去掉 www. 前缀）。"""
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def _is_trusted(domain: str) -> bool:
    """判断域名是否为可信来源（支持子域名匹配）。"""
    for trusted in _TRUSTED_DOMAINS:
        if domain == trusted or domain.endswith("." + trusted):
            return True
    return False


def _is_blocked(domain: str) -> bool:
    """判断域名是否在黑名单中。"""
    for blocked in _BLOCKED_DOMAINS:
        if domain == blocked or domain.endswith("." + blocked):
            return True
    return False


def _is_news_query(query: str) -> bool:
    """判断查询是否为新闻类查询。"""
    return bool(_NEWS_KEYWORDS_ZH.search(query) or _NEWS_KEYWORDS_EN.search(query))


# 匹配中文+数字混合的专有名词短语（如"流浪地球3"、"三体2"、"原神4.0"、"庆余年2"）
# 模式：2-6个中文字符 + 数字（可含点号），这种组合几乎一定是作品名/产品名
_CJK_WITH_NUM_RE = re.compile(
    r'([\u4e00-\u9fff]{2,6})([\d]+(?:\.\d+)?)'
)

# 常见的前缀动词/修饰词，会被剥离后再匹配
_PREFIX_STRIP_RE = re.compile(
    r'^(?:详细|简单|具体)?(?:说说|说一下|说一说|聊聊|讲讲|讲一下|介绍|介绍一下|搜索|搜一下|查一下|看看)?'
)


def _quote_protect_query(query: str) -> str:
    """对查询中的专有名词短语加双引号，防止搜索引擎拆词。

    只处理「中文+数字」组合的专有名词（如"流浪地球3"、"原神4.0"），
    这种模式几乎一定是作品名/产品名/版本号，Bing 很容易拆开。

    纯中文短语不加引号（避免误伤正常查询）。

    例如：
    - "详细说说流浪地球3" → '详细说说"流浪地球3"'
    - "原神4.0版本更新了什么" → '"原神4.0"版本更新了什么'
    - "今天的重大新闻" → "今天的重大新闻"（不变）
    """
    # 已经有引号的不处理
    if '"' in query or '\u201c' in query or '\u201d' in query:
        return query

    # 先剥离常见前缀动词，在剩余部分中匹配专有名词
    stripped = _PREFIX_STRIP_RE.sub('', query)

    # 在剥离后的文本中找「中文+数字」组合
    matches = list(_CJK_WITH_NUM_RE.finditer(stripped))
    if not matches:
        return query

    # 计算原始 query 中的偏移量（前缀长度）
    prefix_len = len(query) - len(stripped)

    # 从后往前替换，避免偏移量问题
    result = query
    for m in reversed(matches):
        full = m.group(0)  # 如 "流浪地球3"
        # 跳过太短的（如 "第3"）
        if len(m.group(1)) < 2:
            continue
        # 映射回原始 query 的位置
        start = m.start() + prefix_len
        end = m.end() + prefix_len
        result = result[:start] + f'"{full}"' + result[end:]

    return result


def _enhance_news_query(query: str) -> str:
    """增强新闻查询：追加宽泛的时间限定，提高时效性结果的排名。

    注意：追加精确日期（如"2026年4月15日"）会严重干扰搜索引擎，
    导致引擎把日期当关键词匹配，返回不相关结果。
    只追加年月，给搜索引擎一个宽泛的时间信号即可。
    """
    today = date.today()
    # 如果查询中已经包含具体日期，不做修改
    if re.search(r"\d{4}[年/-]\d{1,2}", query):
        return query
    # 只追加年月，避免精确日期干扰搜索引擎
    date_suffix = f" {today.year}年{today.month}月"
    return query + date_suffix


# ---- 日期解析 ----

# 匹配 Bing 摘要开头的日期格式：2026年3月16日、2026-03-16、2026/3/16 等
_DATE_PREFIX_RE = re.compile(
    r"^(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})[日]?"
)

# 匹配相对时间：3天前、5小时前、2周前 等
_RELATIVE_TIME_ZH_RE = re.compile(
    r"^(\d+)\s*(分钟|小时|天|周|个月)前"
)

# 匹配英文相对时间：3 days ago, 5 hours ago 等
_RELATIVE_TIME_EN_RE = re.compile(
    r"^(\d+)\s*(minutes?|hours?|days?|weeks?|months?)\s*ago",
    re.IGNORECASE,
)


def _parse_pub_date(snippet: str) -> Optional[date]:
    """从摘要文本开头提取发布日期。

    Bing 搜索结果的摘要通常以日期开头，格式如：
    - "2026年3月16日 · 内容..."
    - "3天前 · 内容..."
    - "5 hours ago · content..."
    """
    text = snippet.strip()
    if not text:
        return None

    # 绝对日期：2026年3月16日
    m = _DATE_PREFIX_RE.match(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass

    # 中文相对时间：3天前
    m = _RELATIVE_TIME_ZH_RE.match(text)
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        return _relative_to_date(num, unit)

    # 英文相对时间：3 days ago
    m = _RELATIVE_TIME_EN_RE.match(text)
    if m:
        num = int(m.group(1))
        unit = m.group(2).rstrip("s").lower()  # days -> day
        return _relative_to_date_en(num, unit)

    return None


def _relative_to_date(num: int, unit: str) -> date:
    """中文相对时间转日期。"""
    today = date.today()
    if unit == "分钟" or unit == "小时":
        return today  # 今天内
    elif unit == "天":
        return today - timedelta(days=num)
    elif unit == "周":
        return today - timedelta(weeks=num)
    elif unit == "个月":
        # 粗略估算
        return today - timedelta(days=num * 30)
    return today


def _relative_to_date_en(num: int, unit: str) -> date:
    """英文相对时间转日期。"""
    today = date.today()
    if unit in ("minute", "hour"):
        return today
    elif unit == "day":
        return today - timedelta(days=num)
    elif unit == "week":
        return today - timedelta(weeks=num)
    elif unit == "month":
        return today - timedelta(days=num * 30)
    return today


class _SearchResult:
    """单条搜索结果，带来源可信度信息和发布日期。"""

    __slots__ = ("title", "url", "snippet", "domain", "source_label", "trusted", "is_news", "pub_date")

    def __init__(
        self, title: str, url: str, snippet: str = "",
        source_label: str = "", is_news: bool = False,
        pub_date: Optional[date] = None,
    ) -> None:
        self.title = title
        self.url = url
        self.snippet = snippet
        self.domain = _extract_domain(url)
        self.source_label = source_label or self.domain
        self.trusted = _is_trusted(self.domain)
        self.is_news = is_news
        self.pub_date = pub_date

    def format(self) -> str:
        """格式化为输出文本，附带来源标注。"""
        if self.url:
            line = f"[{self.title}]({self.url})"
        else:
            line = f"**{self.title}**"

        tags: list[str] = []
        if self.trusted:
            tags.append("trusted")
        if self.source_label:
            tags.append(self.source_label)
        if tags:
            line += f"  ({', '.join(tags)})"

        if self.snippet:
            line += f"\n{self.snippet}"

        return line


class WebSearchTool(BaseTool):
    """Search the web and return results."""

    name = "WebSearch"
    description = (
        "Search the web for information. Returns search result "
        "summaries with links and source credibility tags. "
        "Use for current events or information beyond the knowledge cutoff. "
        "Use the 'source' parameter to route to specialized knowledge sources: "
        "'news' for current events and breaking news, "
        "'tech' for programming docs and technical references, "
        "'general' for everything else (default). "
        "IMPORTANT: Do NOT call this tool more than 2 times for the same topic."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "source": {
                "type": "string",
                "description": (
                    "Knowledge source to search. "
                    "'news' = news/current events, "
                    "'tech' = technical docs/programming, "
                    "'general' = web search (default). "
                    "If omitted, auto-detected from query content."
                ),
                "enum": ["general", "news", "tech"],
            },
        },
        "required": ["query"],
    }
    permission_category = PermissionCategory.EXTERNAL

    # 知识源注册表，在 main.py 中注入
    _source_registry = None

    async def execute(
        self, params: dict, abort_event: Optional[asyncio.Event] = None,
    ) -> ToolResult:
        """搜索链路：百度AI(首选) → 知识源路由/Bing(降级)。"""
        query = params["query"]
        source_name = params.get("source")

        if not HAS_HTTPX:
            return ToolResult(
                content="httpx not installed. Run: pip install httpx",
                is_error=True,
            )

        errors: list[str] = []

        # ---- 首选：百度 AI 搜索 ----
        baidu_api_key = os.environ.get("BAIDU_AI_SEARCH_API_KEY", "")
        if baidu_api_key:
            logger.info("搜索链路: 尝试百度AI搜索, query=%s", query)
            result = await self._search_baidu_ai(query, baidu_api_key)
            if not result.is_error and "No results found" not in result.content:
                logger.info("搜索链路: 百度AI搜索成功")
                return result
            if result.is_error:
                logger.warning("搜索链路: 百度AI搜索失败: %s", result.content)
                errors.append(f"BaiduAI: {result.content}")
            else:
                logger.info("搜索链路: 百度AI搜索无结果, 降级")
        else:
            logger.info("搜索链路: BAIDU_AI_SEARCH_API_KEY 未配置, 跳过百度AI")

        # ---- 降级：知识源路由(Bing) / 纯 Bing ----
        if self._source_registry is not None:
            logger.info("搜索链路: 降级到知识源路由, query=%s", query)
            result = await self._execute_with_sources(query, source_name)
            if not result.is_error and "No results found" not in result.content:
                logger.info("搜索链路: 知识源路由成功")
                return result
            if result.is_error:
                logger.warning("搜索链路: 知识源路由失败: %s", result.content)
                errors.append(f"KnowledgeSource: {result.content}")

        # ---- 兜底：纯 Bing ----
        logger.info("搜索链路: 兜底纯Bing搜索, query=%s", query)
        web_query = _quote_protect_query(query)
        result = await self._search_bing(web_query)
        if not result.is_error and "No results found" not in result.content:
            return result
        if result.is_error:
            errors.append(f"Bing: {result.content}")

        if errors:
            return ToolResult(
                content="All search engines failed.\n" + "\n".join(errors),
                is_error=True,
            )
        return ToolResult(content=f"No results found for: {query}", is_error=False)

    # 知识源名称 → 中文显示名
    _SOURCE_DISPLAY_NAMES: dict[str, str] = {
        "general": "网页搜索",
        "news": "新闻搜索",
        "tech": "技术文档搜索",
    }

    async def _execute_with_sources(
        self, query: str, source_name: Optional[str],
    ) -> ToolResult:
        """通过知识源注册表路由搜索。"""
        registry = self._source_registry

        # 选择知识源
        if source_name:
            source = registry.get(source_name)
            if not source:
                source = registry.match(query)
            else:
                pass
        else:
            source = registry.match(query)

        display_name = self._SOURCE_DISPLAY_NAMES.get(source.name, source.name)
        source_tag = f"[source:{display_name}]"

        # 执行搜索
        try:
            results = await source.search(query)
        except Exception as e:
            return ToolResult(
                content=f"Knowledge source '{source.name}' failed: {e}",
                is_error=True,
            )

        if not results:
            return ToolResult(
                content=f"{source_tag}\nNo results found for: {query}",
                is_error=False,
            )

        # 格式化输出
        lines = [source_tag, ""]
        for r in results:
            line = f"[{r.title}]({r.url})"
            tags = []
            if r.is_trusted:
                tags.append("trusted")
            if r.domain:
                tags.append(r.domain)
            if tags:
                line += f"  ({', '.join(tags)})"
            if r.snippet:
                line += f"\n{r.snippet}"
            lines.append(line)

        lines.append(f"[Current date: {date.today().isoformat()}]")
        return ToolResult(content="\n\n".join(lines), is_error=False)


    # ---- 百度 AI 搜索 (首选) ----

    async def _search_baidu_ai(self, query: str, api_key: str) -> ToolResult:
        """通过百度千帆 AI 搜索 API 获取结果。

        调用 qianfan.baidubce.com/v2/ai_search/chat/completions，
        返回 AI 总结内容 + 引用来源列表。
        """
        is_news = _is_news_query(query)

        payload: dict = {
            "messages": [{"role": "user", "content": query}],
            "model": "ernie-4.5-turbo-128k",
            "search_source": "baidu_search_v2",
            "resource_type_filter": [{"type": "web", "top_k": 10}],
            "stream": False,
            "enable_corner_markers": True,
            "enable_deep_search": False,
            "search_mode": "required",
            "max_completion_tokens": 512,
        }

        # 新闻类查询：限制搜索结果时效性为近一个月，提升新闻相关结果的排名
        if is_news:
            payload["search_recency_filter"] = "month"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://qianfan.baidubce.com/v2/ai_search/chat/completions",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

            return self._format_baidu_ai_response(data, query)

        except httpx.TimeoutException:
            return ToolResult(content="Baidu AI Search timed out", is_error=True)
        except httpx.HTTPStatusError as e:
            body = ""
            try:
                body = e.response.json().get("message", "")
            except Exception:
                pass
            return ToolResult(
                content=f"Baidu AI Search HTTP {e.response.status_code}: {body}",
                is_error=True,
            )
        except Exception as e:
            return ToolResult(
                content=f"Baidu AI Search {type(e).__name__}: {e}",
                is_error=True,
            )

    @staticmethod
    def _format_baidu_ai_response(data: dict, query: str) -> ToolResult:
        """将百度 AI 搜索 API 响应格式化为 ToolResult。

        输出格式：
          [baidu-ai-search] AI 总结内容
          ---
          引用来源列表（带编号、标题、URL、摘要）
        """
        # 提取 AI 总结
        choices = data.get("choices", [])
        if not choices:
            return ToolResult(
                content=f"No results found for: {query}", is_error=False,
            )

        message = choices[0].get("message", {})
        content = message.get("content", "").strip()
        if not content:
            return ToolResult(
                content=f"No results found for: {query}", is_error=False,
            )

        lines: list[str] = ["[source:百度AI搜索]", "", content]

        # 提取引用来源
        references = data.get("references", [])
        if references:
            lines.append("")
            lines.append("---")
            lines.append("**References:**")
            for ref in references:
                ref_type = ref.get("type", "web")
                if ref_type != "web":
                    continue
                ref_id = ref.get("id", "")
                title = ref.get("title", "")
                url = ref.get("url", "")
                snippet = ref.get("content", "")
                ref_date = ref.get("date", "")
                website = ref.get("web_anchor") or ref.get("website", "")

                if not title or not url:
                    continue

                line = f"[{ref_id}] [{title}]({url})"
                if website:
                    line += f"  ({website})"
                if ref_date:
                    line += f"  [{ref_date}]"
                if snippet:
                    # 截断过长的摘要
                    if len(snippet) > 200:
                        snippet = snippet[:200] + "..."
                    line += f"\n{snippet}"
                lines.append(line)

        lines.append(f"\n[Current date: {date.today().isoformat()}]")
        return ToolResult(content="\n".join(lines), is_error=False)

    @staticmethod
    def _rank_results(
        results: list[_SearchResult], limit: int = 8,
        filter_blocked: bool = False, max_age_days: Optional[int] = None,
    ) -> list[_SearchResult]:
        """按可信度排序：过滤黑名单、时间过滤、限制同域名数量、可信源优先。

        Args:
            max_age_days: 最大允许的结果年龄（天数）。设为 None 则不过滤。
                         新闻查询建议设为 30，通用查询不限制。
        """
        today = date.today()
        filtered: list[_SearchResult] = []
        for r in results:
            if filter_blocked and _is_blocked(r.domain):
                continue
            # 时间过滤：如果设置了 max_age_days 且结果有日期，过滤超龄内容
            if max_age_days is not None and r.pub_date is not None:
                age = (today - r.pub_date).days
                if age > max_age_days:
                    continue
            filtered.append(r)

        # 限制同域名数量：可信源最多 3 条，非可信源最多 1 条
        domain_count: dict[str, int] = {}
        deduped: list[_SearchResult] = []
        for r in filtered:
            count = domain_count.get(r.domain, 0)
            max_per_domain = 3 if r.trusted else 1
            if count >= max_per_domain:
                continue
            domain_count[r.domain] = count + 1
            deduped.append(r)

        # 排序：可信源优先，其次保持原始排序
        deduped.sort(
            key=lambda r: (not r.trusted, filtered.index(r) if r in filtered else 999)
        )

        return deduped[:limit]

    @staticmethod
    def _format_results(results: list[_SearchResult], source: str = "") -> str:
        """格式化结果列表，头部附带搜索源标识，末尾附带当前日期供模型参考。"""
        parts: list[str] = []
        if source:
            parts.append(f"[source:{source}]")
        parts.append("\n\n".join(r.format() for r in results))
        parts.append(f"[Current date: {date.today().isoformat()}]")
        return "\n\n".join(parts)

    # ---- Bing (cn.bing.com) ----

    async def _search_bing(
        self, query: str, filter_blocked: bool = False,
        max_age_days: Optional[int] = None,
    ) -> ToolResult:
        """通过 cn.bing.com 搜索。"""
        try:
            async with httpx.AsyncClient(
                timeout=20.0, follow_redirects=True,
            ) as client:
                resp = await client.get(
                    "https://cn.bing.com/search",
                    params={"q": query, "count": "20"},
                    headers=_HEADERS,
                )
                resp.raise_for_status()
                results = self._parse_bing_html(resp.text)
                if not results:
                    return ToolResult(
                        content=f"No results found for: {query}", is_error=False,
                    )
                ranked = self._rank_results(
                    results, limit=8, filter_blocked=filter_blocked,
                    max_age_days=max_age_days,
                )
                return ToolResult(
                    content=self._format_results(ranked, source="Bing搜索"),
                    is_error=False,
                )
        except httpx.TimeoutException:
            return ToolResult(content="Bing timed out", is_error=True)
        except httpx.HTTPStatusError as e:
            return ToolResult(content=f"HTTP {e.response.status_code}", is_error=True)
        except Exception as e:
            return ToolResult(content=f"{type(e).__name__}: {e}", is_error=True)

    @staticmethod
    def _parse_bing_html(html: str) -> list[_SearchResult]:
        """从 Bing 搜索结果页面提取结果。

        Uses BeautifulSoup when available for robust parsing,
        falls back to regex for environments without bs4.
        """
        if HAS_BS4:
            return WebSearchTool._parse_bing_html_bs4(html)
        return WebSearchTool._parse_bing_html_regex(html)

    @staticmethod
    def _parse_bing_html_bs4(html: str) -> list[_SearchResult]:
        """Parse Bing results with BeautifulSoup."""
        soup = BeautifulSoup(html, "html.parser")
        results: list[_SearchResult] = []

        for li in soup.select("li.b_algo")[:20]:
            # Title and URL from h2 > a
            link = li.select_one("h2 a[href]")
            if not link:
                link = li.select_one("a[href]")
            if not link:
                continue

            url = link.get("href", "")
            title = link.get_text(strip=True)
            if not title or len(title) < 3:
                continue

            # Snippet from caption paragraph
            snippet = ""
            for sel in ("p.b_lineclamp", "div.b_caption p", "p"):
                p = li.select_one(sel)
                if p:
                    snippet = p.get_text(strip=True)
                    if len(snippet) > 15:
                        break
                    snippet = ""

            results.append(_SearchResult(
                title=title, url=url, snippet=snippet,
                pub_date=_parse_pub_date(snippet),
            ))

        return results

    @staticmethod
    def _parse_bing_html_regex(html: str) -> list[_SearchResult]:
        """Regex fallback for Bing parsing (when bs4 unavailable)."""
        results: list[_SearchResult] = []

        blocks = re.split(r'<li[^>]*class="b_algo"', html)
        for block in blocks[1:20]:
            title_m = re.search(
                r'<h2[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                block, re.DOTALL,
            )
            if not title_m:
                title_m = re.search(
                    r'<a[^>]*href="([^"]*)"[^>]*class="[^"]*tilk[^"]*"[^>]*>(.*?)</a>',
                    block, re.DOTALL,
                )
            if not title_m:
                continue

            url = title_m.group(1)
            title = _strip_tags(title_m.group(2))
            if not title or len(title) < 3:
                continue

            snippet = ""
            for pattern in (
                r'<p[^>]*class="[^"]*b_lineclamp[^"]*"[^>]*>(.*?)</p>',
                r'<p[^>]*>(.*?)</p>',
                r'<div[^>]*class="[^"]*b_caption[^"]*"[^>]*>.*?<p[^>]*>(.*?)</p>',
            ):
                snippet_m = re.search(pattern, block, re.DOTALL)
                if snippet_m:
                    snippet = _strip_tags(snippet_m.group(1))
                    if len(snippet) > 15:
                        break
                    snippet = ""

            results.append(_SearchResult(
                title=title, url=url, snippet=snippet,
                pub_date=_parse_pub_date(snippet),
            ))

        return results

