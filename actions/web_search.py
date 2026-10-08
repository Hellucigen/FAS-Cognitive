# web_search.py — FAS 网络搜索动作（认知动作节点后端）
# ============================================================================
# 万物皆图：本模块只是"网络搜索"图节点的执行器。触发判定、激活、
# 记录全部在 app.py 管线中经图完成，此处不旁路认知流程。
#
# 搜索源：Bing 中国版优先，百度兜底（国内可达、无需 API Key）。
# 解析为标题+摘要块，供回答生成注入。HTML 解析失败时返回空列表，
# 由调用方降级为无搜索回答。
# ============================================================================

import logging
import re
import html as html_mod
from urllib.parse import quote_plus

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9",
}
_TIMEOUT = 10


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return html_mod.unescape(text).strip()


def _strip_tags(fragment: str) -> str:
    return html_mod.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).strip()


def _search_bing(query: str, top_n: int = 5) -> list:
    url = f"https://cn.bing.com/search?q={quote_plus(query)}&mkt=zh-CN"
    resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    results = []
    # b_algo 是 Bing 每条结果的容器：h2>a 标题，后续片段为摘要
    for block in re.findall(r'<li class="b_algo".*?</li>', resp.text, re.S)[:top_n]:
        m_title = re.search(r"<h2[^>]*>.*?<a[^>]*>(.*?)</a>", block, re.S)
        m_href = re.search(r'<h2[^>]*>.*?<a[^>]*href="([^"]+)"', block, re.S)
        snippet = ""
        m_p = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        if m_p:
            snippet = _strip_tags(m_p.group(1))
        if m_title:
            results.append({
                "title": _clean(m_title.group(1)),
                "snippet": snippet[:300],
                "url": m_href.group(1) if m_href else "",
                "source": "bing",
            })
    return results


def _search_baidu(query: str, top_n: int = 5) -> list:
    url = f"https://www.baidu.com/s?wd={quote_plus(query)}&rn={top_n}"
    resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    results = []
    for block in re.findall(r'<div class="result[^"]*"[^>]*>.*?</div>\s*</div>',
                            resp.text, re.S)[:top_n]:
        m_title = re.search(r"<h3[^>]*>.*?<a[^>]*>(.*?)</a>", block, re.S)
        snippet = ""
        m_abstract = re.search(
            r'<span class="content-right_[^"]*">(.*?)</span>', block, re.S)
        if m_abstract:
            snippet = _strip_tags(m_abstract.group(1))
        if m_title:
            results.append({
                "title": _clean(m_title.group(1)),
                "snippet": snippet[:300] or _strip_tags(block)[:200],
                "url": "https://www.baidu.com/s?wd=" + quote_plus(query),
                "source": "baidu",
            })
    return results


def do_web_search(query: str, top_n: int = 5) -> list:
    """执行搜索，返回 [{title, snippet, url, source}]。全部失败返回 []。"""
    query = str(query or "").strip()
    if not query:
        return []
    for name, fn in (("bing", _search_bing), ("baidu", _search_baidu)):
        try:
            results = fn(query, top_n)
            if results:
                logger.info(f"[WebSearch] {name} 命中 {len(results)} 条: {query[:40]}")
                return results
            logger.warning(f"[WebSearch] {name} 解析到 0 条结果")
        except Exception as e:
            logger.warning(f"[WebSearch] {name} 失败: {e}")
    return []
