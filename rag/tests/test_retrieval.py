import pytest

from rag.embeddings import HashingEmbedder, cosine, tokenize
from rag.retrieval.retriever import RetrievalFilters as F


@pytest.mark.parametrize(
    ("query", "filters", "expected_doc", "expected_section_word"),
    [
        ("high bearing temperature boiler feed pump response", F(asset_type="pump", alarm_names=["High Bearing Temperature"]), "SOP-PUMP-004", "bearing"),
        ("compressor surge anti-surge valve immediate response", F(asset_type="compressor", alarm_names=["Surge Detected"]), "TG-CMP-012", "surge"),
        ("anti-surge valve stroke test method acceptance", F(), "KA-0057", ""),
        ("P1 incident response time notify", F(doc_types=["escalation_procedure"]), "ESC-001", "priority"),
        ("motor overcurrent phase imbalance", F(asset_type="motor"), "TG-MTR-007", "overcurrent"),
        ("isolation permit double block and bleed hot feedwater", F(), "SAF-003", "isolation"),
        ("alarm flood definition per operator console", F(), "ALM-PHIL-001", "flood"),
    ],
)
def test_retrieval_relevance(retriever, query, filters, expected_doc, expected_section_word):
    res = retriever.search(query, filters)
    assert res.hits, query
    top = res.hits[0].chunk
    assert top.doc_id == expected_doc
    assert expected_section_word in top.section.lower()
    assert res.confidence in ("high", "medium")


def test_doc_type_filter_is_hard(retriever):
    res = retriever.search("surge compressor", F(doc_types=["resolution_notes"]))
    assert res.hits and all(h.chunk.doc_type == "resolution_notes" for h in res.hits)


def test_strict_asset_type_filter_excludes_other_equipment(retriever):
    res = retriever.search("high vibration troubleshooting", F(asset_type="compressor", strict_asset_type=True))
    assert res.hits
    assert all(not h.chunk.asset_types or "compressor" in h.chunk.asset_types for h in res.hits)


def test_soft_filters_boost_matching_chunks(retriever):
    plain = retriever.search("high vibration", F())
    boosted = retriever.search("high vibration", F(asset_type="compressor", alarm_names=["High Vibration"]))
    assert boosted.hits[0].boosts.get("asset_type") and "compressor" in boosted.hits[0].chunk.asset_types
    assert plain.hits[0].boosts == {}


def test_citations_are_correct_and_traceable(retriever):
    res = retriever.search("low suction pressure strainer", F(asset_type="pump"))
    cites = res.citations()
    assert [c.ref for c in cites] == [f"[D{i}]" for i in range(1, len(cites) + 1)]
    ids = {c.chunk_id for c in retriever.index.chunks}
    for c, h in zip(cites, res.hits, strict=True):
        assert c.chunk_id in ids and c.chunk_id == h.chunk.chunk_id
        assert c.snippet.rstrip(".").rstrip() in h.chunk.text or c.snippet.endswith("...")
        assert c.source_path.endswith((".md", ".txt", ".html", ".json"))


def test_results_are_diversified_across_documents(retriever):
    res = retriever.search("boiler feed pump alarm procedure steps", F(asset_type="pump"), k=5)
    per_doc = {}
    for h in res.hits:
        per_doc[h.chunk.doc_id] = per_doc.get(h.chunk.doc_id, 0) + 1
    assert max(per_doc.values()) <= 2


@pytest.mark.parametrize("query", ["quantum chromodynamics lattice", "what is on the cafeteria menu", "football world cup results"])
def test_off_topic_queries_have_low_or_no_confidence(retriever, query):
    res = retriever.search(query, F())
    assert res.confidence in ("low", "none")
    assert any("low retrieval confidence" in w for w in res.warnings)


def test_empty_query_returns_none(retriever):
    assert retriever.search("   ", F()).confidence == "none"


def test_injected_and_unverified_chunks_are_excluded(retriever):
    res = retriever.search("bearing temperature tips overgreasing nuisance", F(asset_type="pump"))
    assert all(h.chunk.doc_id != "KA-0099" for h in res.hits)
    assert any(cid.startswith("KA-0099") for cid in res.excluded_chunk_ids)
    allowed = retriever.search("overgreasing grease-lubricated bearings", F(include_unverified=True))
    assert allowed.hits[0].chunk.chunk_id == "KA-0099#001"  # clean chunk usable when explicitly allowed
    assert all(not h.chunk.injection_flags for h in allowed.hits)


def test_hashing_embedder_is_deterministic_and_semantic():
    e = HashingEmbedder()
    a, b, c = e.embed("pump cavitation suction"), e.embed("suction cavitation on the pump"), e.embed("flame failure burner")
    assert a == e.embed("pump cavitation suction")
    assert abs(sum(x * x for x in a) - 1) < 1e-9
    assert cosine(a, b) > cosine(a, c)


def test_tokenizer_stems_and_drops_stopwords():
    assert tokenize("The pumps are vibrating") == ["pump", "vibrat"]
