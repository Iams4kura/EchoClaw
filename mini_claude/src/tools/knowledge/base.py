"""知识源基类、搜索结果模型、源注册表。"""

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """统一搜索结果格式。"""

    title: str
    url: str
    snippet: str
    source_name: str = ""       # 来自哪个知识源
    domain: str = ""            # 域名
    is_trusted: bool = False    # 是否可信源
    published_at: Optional[str] = None  # 发布时间（如有）


class KnowledgeSource(ABC):
    """知识源基类。所有数据源继承此类。"""

    name: str = ""
    description: str = ""
    # 用于意图兜底匹配的关键词列表
    keywords: List[str] = []
    # 是否为默认兜底源
    is_default: bool = False

    @abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 8,
    ) -> List[SearchResult]:
        """执行搜索，返回结果列表。"""
        ...


class SourceRegistry:
    """知识源注册表 — 管理所有已注册的数据源，支持按名称查找和意图匹配。"""

    def __init__(self) -> None:
        self._sources: Dict[str, KnowledgeSource] = {}
        self._default: Optional[KnowledgeSource] = None

    def register(self, source: KnowledgeSource) -> None:
        """注册一个知识源。"""
        self._sources[source.name] = source
        if source.is_default:
            self._default = source
        logger.info("Registered knowledge source: %s", source.name)

    def get(self, name: str) -> Optional[KnowledgeSource]:
        """按名称查找知识源。"""
        return self._sources.get(name)

    def match(self, query: str) -> KnowledgeSource:
        """根据查询内容匹配最佳知识源。

        匹配逻辑：遍历所有源的 keywords，命中则返回该源。
        无命中则返回默认源。
        """
        query_lower = query.lower()
        best_source = None
        best_hits = 0

        for source in self._sources.values():
            if source.is_default:
                continue
            hits = sum(1 for kw in source.keywords if kw in query_lower)
            if hits > best_hits:
                best_hits = hits
                best_source = source

        if best_source:
            logger.info("Query matched source '%s' (%d keyword hits)", best_source.name, best_hits)
            return best_source

        if self._default:
            return self._default

        # 没有默认源时返回第一个
        return next(iter(self._sources.values()))

    def list_all(self) -> List[KnowledgeSource]:
        """返回所有已注册源。"""
        return list(self._sources.values())

    def list_names(self) -> List[str]:
        """返回所有源名称（用于工具 schema enum）。"""
        return list(self._sources.keys())
