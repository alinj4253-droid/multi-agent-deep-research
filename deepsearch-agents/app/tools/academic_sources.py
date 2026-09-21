"""
学术文献检索数据源模块

封装三个官方、免费、稳定的学术 API，统一返回结构化论文数据：
1. arXiv   —— CS/AI 预印本最全，含摘要与 PDF 链接，无需 Key
2. OpenAlex —— 2.5 亿+作品的开放学术索引，含引用数、年份、机构，无需 Key
3. Crossref —— 正式出版物 DOI 元数据，含期刊/会议、引用数，无需 Key

每个数据源独立容错：单一数据源失败不影响其他数据源结果。
"""

import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import requests
from dotenv import find_dotenv, load_dotenv

# 数据源模块也独立加载 .env，保证脱离主智能体单独运行（脚本/测试）时也能读到 key
load_dotenv(find_dotenv())

# 加入 mailto 可进入 OpenAlex / Crossref 的"礼貌池"，获得更稳定的限流待遇
# 联系邮箱仅从环境变量 ACADEMIC_CONTACT_EMAIL 读取（.env，已被 .gitignore 忽略）：
# 配置后才加入 mailto 进入 OpenAlex/Crossref 礼貌池；未配置则不发送 mailto，
# 也不硬编码任何虚假或真实邮箱。
ACADEMIC_CONTACT_EMAIL = os.getenv("ACADEMIC_CONTACT_EMAIL", "").strip()
_ua = "deepresearch-agent/1.0 (academic search"
if ACADEMIC_CONTACT_EMAIL:
    _ua += f"; mailto:{ACADEMIC_CONTACT_EMAIL}"
HEADERS = {"User-Agent": _ua + ")"}
POLITE_PARAMS = {"mailto": ACADEMIC_CONTACT_EMAIL} if ACADEMIC_CONTACT_EMAIL else {}
DEFAULT_TIMEOUT = 15

# 喂给大模型前的字段裁剪阈值，控制单次研究任务的上下文规模
ABSTRACT_MAX_CHARS = 500
AUTHORS_MAX = 6

ARXIV_API = "http://export.arxiv.org/api/query"
OPENALEX_API = "https://api.openalex.org/works"
CROSSREF_API = "https://api.crossref.org/works"

# OpenAlex 免费 API key：无 key 时免费配额按"出口 IP"共享，代理/共享网络易触发 429；
# 配 key 后走专属配额（key 免费、不绑卡，仅存于 .env，已被 .gitignore 忽略）
OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY", "").strip()

ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}


# ============================================================
# 文本工具
# ============================================================
def _clean(text: Optional[str]) -> str:
    """折叠空白并去除首尾空格"""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _normalize_title(title: str) -> str:
    """标题标准化，用于跨源去重（小写、去标点、压缩空格）"""
    t = title.lower()
    t = re.sub(r"[^a-z0-9一-龥]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def reconstruct_openalex_abstract(inverted_index: Optional[dict]) -> str:
    """
    OpenAlex 摘要以"倒排索引"形式存储：{单词: [位置,...]}
    本函数将其还原为正常语序的摘要文本（纯函数，便于单测）
    """
    if not inverted_index:
        return ""
    positions = []
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort(key=lambda x: x[0])
    return " ".join(w for _, w in positions)


def _strip_jats(text: Optional[str]) -> str:
    """去除 Crossref 摘要里残留的 JATS/XML 标签"""
    if not text:
        return ""
    return _clean(re.sub(r"<[^>]+>", "", text))


# 标题相关性把关用停用词：高被引排序会弱化文本相关性、混入跨领域高被引论文，
# 需要按"检索词与标题的判别词重叠"过滤，这些泛化学术词不具主题判别力
_TITLE_STOPWORDS = {
    "with", "for", "and", "the", "from", "using", "used", "based", "via",
    "towards", "toward", "novel", "efficient", "robust", "approach", "method",
    "methods", "model", "models", "learning", "neural", "network", "networks",
    "deep", "survey", "review", "real", "time", "case", "study",
    "a", "an", "of", "in", "on", "to", "by", "our", "their", "its",
}


def _query_terms(text: str) -> set[str]:
    """提取判别性词干（小写、去停用词、简单去复数），用于标题相关性把关"""
    terms = set()
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if len(w) < 3 or w in _TITLE_STOPWORDS:
            continue
        # 简单词干化：gaussians->gaussian、fields->field，便于单复数匹配
        terms.add(w[:-1] if len(w) > 4 and w.endswith("s") else w)
    return terms


def _title_overlap(title: str, query: str) -> int:
    """标题与检索词的判别词干交集数量"""
    return len(_query_terms(title) & _query_terms(query))


# ============================================================
# 三个数据源：各自返回统一结构的论文列表
# ============================================================
def search_arxiv(query: str, max_results: int = 5, year_from: Optional[int] = None) -> list[dict]:
    """
    检索 arXiv，返回统一结构论文列表

    年份限定直接在 API 侧用 submittedDate 范围过滤，而不是先取 top-N 再本地过滤：
    后者在指定较新年份时，会因为按相关性排序取回的 N 条里恰好没有新论文而返回空结果。
    """
    search_query = f"all:{query}"
    if year_from:
        # arXiv submittedDate 格式为 YYYYMMDDHHMM；上界给到当前年末，避免漏掉年内新论文
        search_query += f" AND submittedDate:[{year_from}01010000 TO 999912312359]"

    params = {
        "search_query": search_query,
        "start": 0,
        # API 侧已过滤年份，这里按需求量取回即可；未过滤时多取一些留给后续排序
        "max_results": max_results,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    resp = requests.get(ARXIV_API, params=params, headers=HEADERS, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    papers = []
    for entry in root.findall("atom:entry", ARXIV_NS):
        title = _clean(entry.findtext("atom:title", default="", namespaces=ARXIV_NS))
        abstract = _clean(entry.findtext("atom:summary", default="", namespaces=ARXIV_NS))
        published = entry.findtext("atom:published", default="", namespaces=ARXIV_NS)
        year = int(published[:4]) if published[:4].isdigit() else None

        authors = [
            _clean(a.findtext("atom:name", default="", namespaces=ARXIV_NS))
            for a in entry.findall("atom:author", ARXIV_NS)
        ]

        page_url = ""
        pdf_url = ""
        doi = ""
        for link in entry.findall("atom:link", ARXIV_NS):
            rel = link.get("rel", "")
            ltype = link.get("type", "")
            href = link.get("href", "")
            if ltype == "application/pdf":
                pdf_url = href
            elif rel == "alternate":
                page_url = href
        doi_el = entry.find("{http://arxiv.org/schemas/atom}doi")
        if doi_el is not None and doi_el.text:
            doi = doi_el.text.strip()

        papers.append(
            {
                "title": title,
                "authors": authors,
                "year": year,
                "abstract": abstract,
                "citation_count": None,
                "venue": "arXiv preprint",
                "doi": doi,
                "url": page_url,
                "pdf_url": pdf_url,
                "source": "arxiv",
            }
        )

    # 安全网：submittedDate 范围已在 API 侧过滤，这里再按发表年份兜一次，
    # 防止 arXiv 对区间语法的解析差异混入更早的论文（year 缺失时保留，不误杀）
    if year_from:
        papers = [p for p in papers if p["year"] is None or p["year"] >= year_from]
    return papers


def search_openalex(query: str, max_results: int = 5, year_from: Optional[int] = None) -> list[dict]:
    """检索 OpenAlex，返回统一结构论文列表"""
    filters = []
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    params = {
        "search": query,
        "per-page": max_results,
        **POLITE_PARAMS,
    }
    # 配了免费 key 就走专属配额，规避共享出口 IP 的 429
    if OPENALEX_API_KEY:
        params["api_key"] = OPENALEX_API_KEY
    if filters:
        params["filter"] = ",".join(filters)
    resp = requests.get(OPENALEX_API, params=params, headers=HEADERS, timeout=DEFAULT_TIMEOUT)
    if resp.status_code == 429:
        raise RuntimeError(
            "OpenAlex 限流(429)：当前出口 IP 的免费共享配额已耗尽，"
            "请在 .env 配置 OPENALEX_API_KEY 或切换网络后重试"
        )
    resp.raise_for_status()

    papers = []
    for w in resp.json().get("results", []):
        authors = []
        for a in w.get("authorships", [])[:10]:
            name = (a.get("author") or {}).get("display_name", "")
            if name:
                authors.append(name)

        venue = (
            (w.get("primary_location") or {}).get("source") or {}
        ).get("display_name", "") or ""

        pdf_url = ""
        best_oa = w.get("open_access") or {}
        if best_oa.get("oa_url"):
            pdf_url = best_oa["oa_url"]
        elif w.get("primary_location") and w["primary_location"].get("pdf_url"):
            pdf_url = w["primary_location"]["pdf_url"]

        papers.append(
            {
                "title": _clean(w.get("display_name", "")),
                "authors": authors,
                "year": w.get("publication_year"),
                "abstract": reconstruct_openalex_abstract(
                    w.get("abstract_inverted_index")
                ),
                "citation_count": w.get("cited_by_count"),
                "venue": venue,
                "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
                "url": w.get("id", "").replace("https://api.openalex.org/works/", "https://openalex.org/works/"),
                "pdf_url": pdf_url,
                "source": "openalex",
            }
        )
    return papers


def _crossref_query(
    query: str,
    max_results: int = 5,
    year_from: Optional[int] = None,
    sort_cited: bool = False,
) -> list[dict]:
    """单次 Crossref 查询；sort_cited=True 时按被引次数降序（召回高被引经典）"""
    # Crossref 不支持同一 type filter 传多个值（type:a,b 会被判为畸形 filter 并返回 400）；
    # 学术 query 本身相关性已足够，这里只按年份过滤，作品类型交由 venue/type 字段体现
    filters = []
    if year_from:
        filters.append(f"from-pub-date:{year_from}-01-01")
    params = {
        "query": query,
        "rows": max_results,
        "select": "title,author,published,DOI,container-title,is-referenced-by-count,abstract,type,URL",
        **POLITE_PARAMS,
    }
    if sort_cited:
        # 按被引降序：默认相关性排序偏重新论文，会漏掉高被引奠基工作
        params["sort"] = "is-referenced-by-count"
        params["order"] = "desc"
    if filters:
        params["filter"] = ",".join(filters)
    resp = requests.get(CROSSREF_API, params=params, headers=HEADERS, timeout=DEFAULT_TIMEOUT)
    resp.raise_for_status()

    papers = []
    for item in resp.json().get("message", {}).get("items", []):
        authors = []
        for a in item.get("author", [])[:10]:
            family = a.get("family", "")
            given = a.get("given", "")
            name = _clean(f"{family} {given}") if family else given
            if name:
                authors.append(name)

        year = None
        for key in ("published", "published-print", "published-online", "created"):
            parts = (item.get(key) or {}).get("date-parts", [[None]])
            if parts and parts[0] and parts[0][0]:
                year = int(parts[0][0])
                break

        venue_list = item.get("container-title", [])
        venue = venue_list[0] if venue_list else item.get("type", "")

        papers.append(
            {
                "title": _clean((item.get("title") or [""])[0]),
                "authors": authors,
                "year": year,
                "abstract": _strip_jats(item.get("abstract")),
                "citation_count": item.get("is-referenced-by-count"),
                "venue": venue,
                "doi": item.get("DOI", ""),
                "url": item.get("URL", ""),
                "pdf_url": "",
                "source": "crossref",
            }
        )
    return papers


def search_crossref(query: str, max_results: int = 5, year_from: Optional[int] = None) -> list[dict]:
    """
    Crossref 双路检索：
      - 相关性路（默认排序）：贴合检索词、偏新进展；
      - 高被引路（按被引降序）：召回奠基性经典，但该排序弱化文本相关性，
        会混入跨领域高被引论文（如医学论文），需用标题词重叠把关。
    两路并发、按标准化标题去重，保留高被引/有摘要的记录。
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_rel = pool.submit(_crossref_query, query, max_results, year_from, False)
        f_cited = pool.submit(_crossref_query, query, max_results, year_from, True)
        rel = f_rel.result()  # 相关性路是主路，失败直接抛出交由上层容错
        try:
            cited = f_cited.result()
        except Exception:
            cited = []  # 高被引路失败不影响相关性路

    # 高被引路：标题至少命中 2 个检索判别词（短 query 时退化为至少 1 个），过滤跨领域噪声
    need = min(2, len(_query_terms(query))) or 1
    cited = [p for p in cited if _title_overlap(p.get("title", ""), query) >= need]

    merged: dict[str, dict] = {}
    for p in rel + cited:
        key = _normalize_title(p.get("title", ""))
        if not key:
            continue
        old = merged.get(key)
        if old is None:
            merged[key] = p
        elif (p.get("citation_count") or -1, bool(p.get("abstract"))) > (
            old.get("citation_count") or -1,
            bool(old.get("abstract")),
        ):
            merged[key] = p
    return list(merged.values())


SOURCE_FUNCS = {
    "arxiv": search_arxiv,
    "openalex": search_openalex,
    "crossref": search_crossref,
}


# ============================================================
# 聚合：并发查询 + 去重 + 排序
# ============================================================
def _merge_source(a: str, b: str) -> str:
    """合并来源标签并去重、保序：openalex+openalex -> openalex；openalex+crossref 保留两者"""
    seen: list[str] = []
    for s in str(a).split("+") + str(b).split("+"):
        if s and s not in seen:
            seen.append(s)
    return "+".join(seen)


def _norm_doi(doi):
    """DOI 归一化：去 URL 前缀、小写、去空白，用于跨源强匹配。"""
    if not doi:
        return ""
    d = str(doi).strip().lower()
    d = d.replace("https://doi.org/", "").replace("http://dx.doi.org/", "")
    return d


def _fuse_records(primary: dict, secondary: dict) -> dict:
    """
    字段级融合同一篇论文的两条跨源记录（不做模糊匹配，调用方已确认是同一篇）。

    primary 为信息更全的主记录，secondary 提供互补字段：
    - citation_count：取较大数值（None 视为缺失）；
    - abstract/pdf_url/doi/venue/url：取更长的非空值；
    - authors：取人数更多的非空列表；
    - year：主记录缺失才用次记录补齐；两者都有但不一致时保留主记录；
    - source：合并为去重保序拼接字符串（兼容旧字段），并额外给出 sources 列表。
    """
    import copy

    base = copy.deepcopy(primary)

    cites = [
        c for c in (base.get("citation_count"), secondary.get("citation_count"))
        if isinstance(c, (int, float))
    ]
    base["citation_count"] = max(cites) if cites else None

    for field in ("abstract", "pdf_url", "doi", "venue", "url"):
        bv = str(base.get(field) or "")
        ov = str(secondary.get(field) or "")
        if len(ov.strip()) > len(bv.strip()):
            base[field] = secondary.get(field)

    if len(secondary.get("authors") or []) > len(base.get("authors") or []):
        base["authors"] = copy.deepcopy(secondary.get("authors"))

    if not base.get("year") and secondary.get("year"):
        base["year"] = secondary.get("year")

    base["source"] = _merge_source(base.get("source", ""), secondary.get("source", ""))
    base["sources"] = [x for x in str(base["source"]).split("+") if x]
    return base


def _record_completeness(p: dict):
    """完整度评分，决定融合时谁做主记录：引用数、有无摘要、非空字段数、作者数、标题长度。"""
    return (
        p.get("citation_count") if isinstance(p.get("citation_count"), (int, float)) else -1,
        1 if p.get("abstract") else 0,
        sum(1 for k in ("doi", "pdf_url", "venue", "url") if p.get(k)),
        len(p.get("authors") or []),
        len(p.get("title") or ""),
    )


def merge_and_rank(
    papers: list[dict],
    top_k: int,
    year_from: Optional[int] = None,
) -> list[dict]:
    """
    跨源去重并排序

    去重：标准化标题相同视为同一篇，优先保留信息更全（有摘要/引用数）的记录；

    排序分两种模式：
    - **未限定年份**（综述/发展脉络类需求）：优先高被引，其次年份更新，
      最后标题更长（信息更完整的代理指标）——高被引能召回奠基性工作。
    - **限定了 year_from**（"最新进展/近期趋势"类需求）：**年份优先**，
      同年内再按引用数排序。因为高被引需要时间积累，若仍按引用数打头，
      结果必然被更早的经典论文占据，与"最新"的诉求相反。
    """
    merged: dict[str, dict] = {}
    doi_index: dict[str, str] = {}  # 归一化 DOI -> 标题 key，支持 DOI 强匹配

    for p in papers:
        title_key = _normalize_title(p.get("title", ""))
        if not title_key:
            continue

        # 匹配优先级：DOI 完全一致（强）> 归一化标题一致（中）；不做模糊匹配防误合并
        doi_key = _norm_doi(p.get("doi"))
        match_key = doi_index.get(doi_key) if doi_key else None
        if match_key is None and title_key in merged:
            match_key = title_key

        if match_key is None:
            record = dict(p)
            record["sources"] = [
                x for x in str(record.get("source", "")).split("+") if x
            ]
            merged[title_key] = record
            if doi_key:
                doi_index[doi_key] = title_key
            continue

        old = merged[match_key]
        # 完整度更高者做主记录，再字段级融合，互补字段不再被丢弃
        if _record_completeness(p) > _record_completeness(old):
            fused = _fuse_records(p, old)
        else:
            fused = _fuse_records(old, p)
        merged[match_key] = fused
        fused_doi = _norm_doi(fused.get("doi"))
        if fused_doi and fused_doi not in doi_index:
            doi_index[fused_doi] = match_key

    if year_from:
        # 时效优先：新年份排前，同年内再比引用数
        def sort_key(p):
            return (
                p.get("year") or 0,
                p.get("citation_count") if p.get("citation_count") is not None else -1,
                len(p.get("title") or ""),
            )
    else:
        # 影响力优先：高被引排前，同引用再比年份
        def sort_key(p):
            return (
                p.get("citation_count") if p.get("citation_count") is not None else -1,
                p.get("year") or 0,
                len(p.get("title") or ""),
            )

    for rec in merged.values():
        rec.setdefault("sources", [x for x in str(rec.get("source", "")).split("+") if x])
    ranked = sorted(merged.values(), key=sort_key, reverse=True)
    return ranked[:top_k]


def academic_search(
    query: str,
    sources: Optional[list[str]] = None,
    max_results_per_source: int = 5,
    year_from: Optional[int] = None,
    top_k: int = 8,
) -> dict:
    """
    并发检索多个学术数据源并聚合

    :param query: 检索词或研究主题
    :param sources: 启用的数据源，默认全部三源
    :param max_results_per_source: 每个数据源拉取条数
    :param year_from: 限定起始年份
    :param top_k: 聚合后返回的论文条数
    :return: {"query", "sources_used", "sources_failed", "count", "papers"}
    """
    sources = sources or ["arxiv", "openalex", "crossref"]
    all_papers: list[dict] = []
    sources_used, sources_failed = [], []

    def _run(name):
        fn = SOURCE_FUNCS.get(name)
        if fn is None:
            return name, [], f"unknown source {name}"
        try:
            return name, fn(query, max_results=max_results_per_source, year_from=year_from), None
        except Exception as e:  # 单源失败不影响其他源
            return name, [], f"{name}: {type(e).__name__}: {str(e)[:120]}"

    # 并发查询三源，显著降低总延迟（串行约 5-6s，并发约 2s）
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        for name, papers, err in pool.map(lambda s: _run(s), sources):
            if err:
                sources_failed.append(err)
            else:
                sources_used.append(name)
                all_papers.extend(papers)

    ranked = merge_and_rank(all_papers, top_k=top_k, year_from=year_from)

    # 控制喂给大模型的 token 量：长摘要截断、作者只保留前若干位，
    # 避免一次综述任务（多次工具调用）累积过多上下文导致 LLM 整理缓慢
    for p in ranked:
        abstract = p.get("abstract") or ""
        if len(abstract) > ABSTRACT_MAX_CHARS:
            p["abstract"] = abstract[:ABSTRACT_MAX_CHARS].rstrip() + " …"
        authors = p.get("authors") or []
        if len(authors) > AUTHORS_MAX:
            p["authors"] = authors[:AUTHORS_MAX] + ["et al."]

    return {
        "query": query,
        "sources_used": sources_used,
        "sources_failed": sources_failed,
        "count": len(ranked),
        "papers": ranked,
    }


if __name__ == "__main__":
    from pprint import pprint

    pprint(academic_search("3D Gaussian Splatting dynamic scene", top_k=3))
