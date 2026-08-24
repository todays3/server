from app.services.llm_quality import (
    compute_timing,
    estimate_completion_tokens,
    percentile_float,
    score_rag,
)


def test_ttft_tps_from_stream_timestamps():
    timing = compute_timing(
        started_at=1.0,
        first_token_at=1.2,
        ended_at=2.2,
        completion_tokens=10,
        streamed=True,
    )
    assert timing.ttft_ms == 200
    assert timing.total_ms == 1200
    assert timing.generate_ms == 1000
    assert timing.tps == 10.0
    assert timing.streamed is True


def test_nonstream_ttft_equals_total():
    timing = compute_timing(
        started_at=0.0,
        first_token_at=None,
        ended_at=0.5,
        completion_tokens=20,
        streamed=False,
    )
    assert timing.ttft_ms == 500
    assert timing.total_ms == 500
    assert timing.tps == 40.0
    assert timing.streamed is False


def test_estimate_tokens_and_zero_duration():
    assert estimate_completion_tokens("안녕하세요") >= 1
    timing = compute_timing(
        started_at=1.0,
        first_token_at=1.0,
        ended_at=1.0,
        completion_tokens=8,
        streamed=True,
    )
    assert timing.tps == 0.0


def test_rag_numeric_grounding_and_relevance():
    grounded = score_rag(
        question="반도체 수요 때문에 왜 실적이 좋아졌나",
        answer="매출액은 300870903000000원입니다. 반도체 수요 증가가 배경입니다.",
        contexts=["반도체 수요 증가와 HBM 판매 확대가 실적 개선의 주요 배경입니다."],
        grounded_values=["300870903000000"],
    )
    assert grounded.hallucination_rate == 0.0
    assert grounded.faithfulness == 1.0
    assert grounded.answer_relevance > 0.3
    assert grounded.context_precision == 1.0

    hallucinated = score_rag(
        question="매출액은?",
        answer="매출액은 999888777666원입니다.",
        contexts=["반도체 수요가 회복되었습니다."],
        grounded_values=["111000"],
    )
    assert hallucinated.hallucination_rate == 1.0
    assert hallucinated.faithfulness == 0.0


def test_context_precision_drops_unrelated_chunk():
    scores = score_rag(
        question="환율 영향은?",
        answer="환율 영향은 제한적이었습니다.",
        contexts=["환율 영향은 제한적이었습니다.", "오늘 날씨는 맑음입니다."],
        grounded_values=[],
    )
    assert scores.context_precision == 0.5


def test_percentile_float():
    assert percentile_float([], 50) == 0.0
    assert percentile_float([4.0], 90) == 4.0
    assert 2.0 <= percentile_float([1.0, 2.0, 3.0], 50) <= 2.0
