"""
学术文献检索模块单元测试（纯逻辑，不依赖外网）

覆盖：
1. 文本工具：_clean / _normalize_title / reconstruct_openalex_abstract / _strip_jats
2. merge_and_rank：跨源标题去重、来源合并、按被引/年份排序、top_k 截断
3. academic_search：用假数据源验证并发聚合与"单源失败不影响其他源"的容错
"""

from app.tools import academic_sources as ac


# ============================================================
# 文本工具
# ============================================================
class TestTextUtils:
    def test_clean_collapses_whitespace(self):
        assert ac._clean("  3D   Gaussian\n\tSplatting  ") == "3D Gaussian Splatting"

    def test_clean_none_and_empty(self):
        assert ac._clean(None) == ""
        assert ac._clean("") == ""

    def test_normalize_title_strips_case_and_punct(self):
        assert ac._normalize_title("  3D Gaussian Splatting! ") == "3d gaussian splatting"

    def test_normalize_title_equivalent_for_dedup(self):
        assert ac._normalize_title("Dynamic 3D Gaussians:") == ac._normalize_title(
            "dynamic 3d gaussians"
        )

    def test_reconstruct_openalex_abstract_word_order(self):
        inv = {"Gaussian": [0, 2], "Splatting": [1], "dynamic": [3]}
        assert ac.reconstruct_openalex_abstract(inv) == "Gaussian Splatting Gaussian dynamic"

    def test_reconstruct_openalex_abstract_empty(self):
        assert ac.reconstruct_openalex_abstract(None) == ""
        assert ac.reconstruct_openalex_abstract({}) == ""

    def test_strip_jats_tags(self):
        raw = "<jats:title>Abstract</jats:title> We propose <italic>3DGS</italic>."
        assert ac._strip_jats(raw) == "Abstract We propose 3DGS."

    def test_strip_jats_none(self):
        assert ac._strip_jats(None) == ""


# ============================================================
# merge_and_rank
# ============================================================
def _paper(title, year, cites, source, abstract="abstract text"):
    return {
        "title": title,
        "authors": ["A"],
        "year": year,
        "abstract": abstract,
        "citation_count": cites,
        "venue": "venue",
        "doi": "",
        "url": "http://x",
        "pdf_url": "",
        "source": source,
    }


class TestMergeAndRank:
    def test_dedup_merges_sources_and_keeps_high_cited(self):
        a = _paper("High Cited Paper", 2023, 100, "openalex")
        b = _paper("high cited paper!", 2023, 0, "arxiv")  # 归一化后与 a 同标题
        c = _paper("Older Work", 2020, 50, "crossref")
        ranked = ac.merge_and_rank([a, b, c], top_k=10)

        assert len(ranked) == 2  # a/b 去重为一篇
        assert ranked[0]["title"] == "High Cited Paper"
        assert ranked[0]["source"] == "openalex+arxiv"  # 来源合并
        assert ranked[1]["title"] == "Older Work"

    def test_rank_by_citation_desc(self):
        low = _paper("Low", 2024, 1, "arxiv")
        high = _paper("High", 2022, 99, "openalex")
        ranked = ac.merge_and_rank([low, high], top_k=10)
        assert ranked[0]["title"] == "High"

    def test_top_k_truncation(self):
        papers = [_paper(f"Paper {i}", 2024, i, "arxiv") for i in range(5)]
        ranked = ac.merge_and_rank(papers, top_k=3)
        assert len(ranked) == 3

    def test_drops_empty_title(self):
        good = _paper("Good", 2024, 1, "arxiv")
        bad = _paper("", 2024, 999, "crossref")
        ranked = ac.merge_and_rank([good, bad], top_k=10)
        assert len(ranked) == 1
        assert ranked[0]["title"] == "Good"

    def test_none_citation_sorted_last(self):
        unknown = _paper("Unknown cites", 2024, None, "arxiv")
        known = _paper("Known cites", 2024, 5, "openalex")
        ranked = ac.merge_and_rank([unknown, known], top_k=10)
        assert ranked[0]["title"] == "Known cites"


class TestRecencyFirstRanking:
    """限定 year_from 时改为「年份优先」，解决问最新进展却返回高被引老论文的问题"""

    def test_year_from_puts_newer_paper_first_despite_lower_citations(self):
        # 2026 年新论文引用少，2023 年经典引用多；限定年份后新论文必须排前
        old_classic = _paper("Classic Foundational Work", 2023, 5000, "crossref")
        new_paper = _paper("Latest 2026 Progress", 2026, 3, "arxiv")
        ranked = ac.merge_and_rank([old_classic, new_paper], top_k=10, year_from=2024)

        assert ranked[0]["title"] == "Latest 2026 Progress"
        assert ranked[1]["title"] == "Classic Foundational Work"

    def test_without_year_from_keeps_citation_first(self):
        # 不传 year_from（综述/发展脉络需求）时保持原有「引用优先」行为
        old_classic = _paper("Classic Foundational Work", 2023, 5000, "crossref")
        new_paper = _paper("Latest 2026 Progress", 2026, 3, "arxiv")
        ranked = ac.merge_and_rank([old_classic, new_paper], top_k=10)

        assert ranked[0]["title"] == "Classic Foundational Work"

    def test_year_from_ties_broken_by_citation(self):
        # 同年内仍按引用数排序
        a = _paper("Same Year Low Cite", 2025, 2, "arxiv")
        b = _paper("Same Year High Cite", 2025, 80, "openalex")
        ranked = ac.merge_and_rank([a, b], top_k=10, year_from=2024)

        assert ranked[0]["title"] == "Same Year High Cite"

    def test_year_from_descending_by_year(self):
        papers = [
            _paper("Y2024", 2024, 10, "arxiv"),
            _paper("Y2026", 2026, 1, "arxiv"),
            _paper("Y2025", 2025, 50, "arxiv"),
        ]
        ranked = ac.merge_and_rank(papers, top_k=10, year_from=2024)

        assert [p["title"] for p in ranked] == ["Y2026", "Y2025", "Y2024"]

    def test_missing_year_sorted_last_in_recency_mode(self):
        # 年份缺失的记录在时效优先模式下排最后，避免 None 干扰排序
        known = _paper("Known Year", 2025, 1, "arxiv")
        unknown = _paper("Unknown Year", None, 9999, "arxiv")
        ranked = ac.merge_and_rank([unknown, known], top_k=10, year_from=2024)

        assert ranked[0]["title"] == "Known Year"

    def test_search_passes_year_from_to_ranking(self, monkeypatch):
        # 端到端：academic_search 必须把 year_from 透传给 merge_and_rank
        def fake_source(query, max_results=5, year_from=None):
            return [
                _paper("Old Highly Cited", 2022, 900, "openalex"),
                _paper("Fresh Low Cited", 2026, 1, "openalex"),
            ]

        monkeypatch.setattr(ac, "SOURCE_FUNCS", {"openalex": fake_source})
        result = ac.academic_search("q", sources=["openalex"], year_from=2025)

        assert result["papers"][0]["title"] == "Fresh Low Cited"

    def test_arxiv_query_adds_date_range_when_year_from(self, monkeypatch):
        # arXiv 必须在 API 侧加 submittedDate 范围，而非取回后本地过滤
        captured = {}

        class FakeResponse:
            text = (
                '<?xml version="1.0"?>'
                '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
            )

            def raise_for_status(self):
                return None

        def fake_get(url, params=None, headers=None, timeout=None):
            captured["params"] = params
            return FakeResponse()

        monkeypatch.setattr(ac.requests, "get", fake_get)
        ac.search_arxiv("gaussian splatting", max_results=5, year_from=2026)

        assert "submittedDate:[202601010000" in captured["params"]["search_query"]

    def test_arxiv_query_omits_date_range_without_year_from(self, monkeypatch):
        captured = {}

        class FakeResponse:
            text = (
                '<?xml version="1.0"?>'
                '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
            )

            def raise_for_status(self):
                return None

        def fake_get(url, params=None, headers=None, timeout=None):
            captured["params"] = params
            return FakeResponse()

        monkeypatch.setattr(ac.requests, "get", fake_get)
        ac.search_arxiv("gaussian splatting", max_results=5)

        assert "submittedDate" not in captured["params"]["search_query"]


# ============================================================
# academic_search：聚合容错（注入假数据源，不触网）
# ============================================================
class TestAcademicSearchAggregation:
    def test_single_source_failure_isolated(self, monkeypatch):
        def good(query, max_results=3, year_from=None):
            return [_paper("P1", 2024, 1, "openalex")]

        def bad(query, max_results=3, year_from=None):
            raise RuntimeError("service down")

        monkeypatch.setattr(ac, "SOURCE_FUNCS", {"openalex": good, "crossref": bad})
        r = ac.academic_search("q", sources=["openalex", "crossref"])

        assert r["count"] == 1
        assert r["papers"][0]["title"] == "P1"
        assert "openalex" in r["sources_used"]
        assert len(r["sources_failed"]) == 1
        assert "crossref" in r["sources_failed"][0]

    def test_all_sources_empty(self, monkeypatch):
        def empty(query, max_results=3, year_from=None):
            return []

        monkeypatch.setattr(ac, "SOURCE_FUNCS", {"arxiv": empty})
        r = ac.academic_search("q", sources=["arxiv"])
        assert r["count"] == 0
        assert r["papers"] == []
        assert r["sources_failed"] == []


# ============================================================
# 标题相关性把关（高被引排序会混入跨领域高被引论文）
# ============================================================
class TestTitleRelevance:
    def test_query_terms_drop_stopwords_and_short(self):
        terms = ac._query_terms("4D Gaussian Splatting for dynamic scenes")
        assert "gaussian" in terms
        assert "splatting" in terms
        assert "dynamic" in terms
        assert "scene" in terms          # scenes -> scene
        assert "for" not in terms        # 停用词
        assert "4d" not in terms         # 长度 < 3

    def test_query_terms_singular_plural(self):
        # gaussians / gaussian 简单词干化后可匹配
        assert ac._title_overlap("Deformable 3D Gaussians", "deformable gaussians") >= 2

    def test_overlap_keeps_relevant_classic(self):
        # 高被引奠基论文应与 query 命中至少 2 个判别词
        assert ac._title_overlap(
            "3D Gaussian Splatting for Real-Time Radiance Field Rendering",
            "4D Gaussian Splatting",
        ) >= 2

    def test_overlap_rejects_cross_domain_citation_giant(self):
        # 按被引排序混入的跨领域医学论文与 query 无判别词重叠
        assert ac._title_overlap(
            "CD19 CAR T-Cell Therapy in Autoimmune Disease A Case Series",
            "4D Gaussian Splatting",
        ) == 0

    def test_short_query_needs_one_term(self):
        # 单词 query 时阈值退化为 1
        need = min(2, len(ac._query_terms("Gaussian"))) or 1
        assert need == 1


# ============================================================
# 来源标签合并去重
# ============================================================
class TestMergeSource:
    def test_dedup_same_source(self):
        assert ac._merge_source("openalex", "openalex") == "openalex"

    def test_keep_distinct_sources_ordered(self):
        assert ac._merge_source("openalex", "crossref") == "openalex+crossref"
        assert ac._merge_source("crossref", "openalex") == "crossref+openalex"

    def test_merge_already_combined(self):
        assert ac._merge_source("openalex+crossref", "crossref") == "openalex+crossref"


# ============================================================
# Crossref 双路检索：相关性路 + 高被引路（高被引路需标题把关）
# ============================================================
class TestCrossrefDualRoute:
    def test_cited_route_keeps_classic_drops_noise(self, monkeypatch):
        def fake_query(query, max_results=5, year_from=None, sort_cited=False):
            if sort_cited:
                # 高被引路：1 篇真经典 + 1 篇跨领域高被引噪声
                return [
                    _paper("3D Gaussian Splatting for Real-Time Radiance Field Rendering",
                           2023, 6245, "crossref"),
                    _paper("CD19 CAR T-Cell Therapy in Autoimmune Disease A Case Series",
                           2024, 1086, "crossref"),
                ]
            # 相关性路：偏新的贴合论文
            return [_paper("Lumina-4DGS Illumination Robust 4D Gaussian Splatting",
                           2025, 0, "crossref")]

        monkeypatch.setattr(ac, "_crossref_query", fake_query)
        out = ac.search_crossref("4D Gaussian Splatting", max_results=5)
        titles = [p["title"] for p in out]

        assert "3D Gaussian Splatting for Real-Time Radiance Field Rendering" in titles
        assert "Lumina-4DGS Illumination Robust 4D Gaussian Splatting" in titles
        assert "CD19 CAR T-Cell Therapy in Autoimmune Disease A Case Series" not in titles

    def test_cited_route_failure_degrades_gracefully(self, monkeypatch):
        def fake_query(query, max_results=5, year_from=None, sort_cited=False):
            if sort_cited:
                raise RuntimeError("cited route 500")
            return [_paper("Relevant New Paper on Gaussian Splatting", 2025, 1, "crossref")]

        monkeypatch.setattr(ac, "_crossref_query", fake_query)
        out = ac.search_crossref("Gaussian Splatting", max_results=5)
        assert len(out) == 1
        assert out[0]["title"] == "Relevant New Paper on Gaussian Splatting"

    def test_dual_route_dedups_same_title(self, monkeypatch):
        shared = "4D Gaussian Splatting for Real-Time Dynamic Scene Rendering"

        def fake_query(query, max_results=5, year_from=None, sort_cited=False):
            return [_paper(shared, 2024, 816 if sort_cited else 5, "crossref")]

        monkeypatch.setattr(ac, "_crossref_query", fake_query)
        out = ac.search_crossref("4D Gaussian Splatting", max_results=5)
        matching = [p for p in out if p["title"] == shared]
        assert len(matching) == 1          # 两路同标题去重
        assert matching[0]["citation_count"] == 816  # 保留高被引记录
        assert matching[0]["source"] == "crossref"  # 同源不拼标签
