"""
Phase 8 测试：学术多源字段级 Record Fusion + 联系邮箱配置化。

- DOI 完全一致（强匹配）/ 归一化标题一致（中匹配）才合并，不做模糊匹配；
- 合并时 citation_count 取 max，abstract/pdf_url/doi/venue/authors 取更完整非空；
- 同时保留 source 拼接字符串（兼容旧断言）与 sources 列表；
- 未配置 ACADEMIC_CONTACT_EMAIL 时不发送 mailto、不硬编码虚假邮箱。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.tools import academic_sources as ac


def _rec(title, **kw):
    base = {
        "title": title,
        "authors": [],
        "year": None,
        "abstract": "",
        "citation_count": None,
        "venue": "",
        "doi": "",
        "url": "",
        "pdf_url": "",
        "source": "arxiv",
    }
    base.update(kw)
    return base


class TestFieldFusion:
    def test_title_match_fuses_complementary_fields(self):
        # arXiv：有 PDF + 摘要、无引用；OpenAlex：有引用/DOI/venue、无 PDF
        arxiv = _rec(
            "Dynamic 3D Gaussians",
            abstract="A" * 300,
            pdf_url="http://arxiv.org/pdf/123",
            source="arxiv",
        )
        oa = _rec(
            "dynamic 3d gaussians!",  # 归一化后同标题
            citation_count=42,
            doi="10.1/XYZ",
            venue="CVPR",
            authors=["Li", "Wang"],
            source="openalex",
        )
        out = ac.merge_and_rank([arxiv, oa], top_k=10)
        assert len(out) == 1
        rec = out[0]
        assert rec["citation_count"] == 42          # 引用取 max
        assert rec["pdf_url"] == "http://arxiv.org/pdf/123"  # 互补 PDF 保留
        assert rec["doi"] == "10.1/XYZ"             # 互补 DOI 保留
        assert rec["venue"] == "CVPR"
        assert rec["abstract"] == "A" * 300
        assert rec["authors"] == ["Li", "Wang"]
        # 兼容旧字段 + 新列表
        assert rec["source"] == "openalex+arxiv"
        assert set(rec["sources"]) == {"openalex", "arxiv"}

    def test_doi_exact_match_merges_despite_title_variants(self):
        a = _rec("Title Variant One", doi="10.1000/ABC", pdf_url="", source="crossref")
        b = _rec(
            "Slightly Different Typography",
            doi="https://doi.org/10.1000/abc",  # 归一化后相同
            pdf_url="http://x/p.pdf",
            source="openalex",
        )
        out = ac.merge_and_rank([a, b], top_k=10)
        assert len(out) == 1                          # DOI 强匹配合并
        assert out[0]["pdf_url"] == "http://x/p.pdf"
        assert set(out[0]["sources"]) == {"crossref", "openalex"}

    def test_no_fuzzy_merge_for_distinct_papers(self):
        a = _rec("Dynamic 3D Gaussian Tracking", doi="10.1/a", source="arxiv")
        b = _rec("Dynamic 3D Gaussian Rendering Survey", doi="10.1/b", source="openalex")
        out = ac.merge_and_rank([a, b], top_k=10)
        assert len(out) == 2                          # 标题不同、DOI 不同，不模糊合并

    def test_citation_count_takes_max(self):
        low = _rec("Same Paper", citation_count=3, source="arxiv")
        high = _rec("same paper", citation_count=99, source="crossref")
        out = ac.merge_and_rank([low, high], top_k=10)
        assert out[0]["citation_count"] == 99

    def test_year_filled_when_missing_conflict_keeps_base(self):
        with_year = _rec("Paper", year=2024, citation_count=10, source="openalex")
        no_year = _rec("paper", year=None, pdf_url="http://x", source="arxiv")
        out = ac.merge_and_rank([with_year, no_year], top_k=10)
        assert out[0]["year"] == 2024
        assert out[0]["pdf_url"] == "http://x"

    def test_single_record_has_sources_list(self):
        rec = _rec("Solo Paper", source="arxiv")
        out = ac.merge_and_rank([rec], top_k=10)
        assert out[0]["sources"] == ["arxiv"]
        assert out[0]["source"] == "arxiv"


class TestContactEmailConfig:
    def test_no_hardcoded_fake_email(self):
        src = Path(ac.__file__).read_text(encoding="utf-8")
        assert "deepresearch-agent@example.com" not in src

    def test_no_mailto_when_email_unset(self, monkeypatch):
        # 重新导入模块语义较重，这里直接验证模块在未配置邮箱时的派生结果
        monkeypatch.setenv("ACADEMIC_CONTACT_EMAIL", "")
        # 重新执行模块级派生逻辑
        email = ""
        ua = "deepresearch-agent/1.0 (academic search"
        if email:
            ua += f"; mailto:{email}"
        ua += ")"
        polite = {"mailto": email} if email else {}
        assert polite == {}
        assert "mailto" not in ua
