from app.services.dart_router import QueryRoute, route_finance_question


def test_router_number_query_skips_llm():
    routed = route_finance_question("삼성전자 2024 매출액은 얼마야?")
    assert routed.route == QueryRoute.NUMBER
    assert routed.account == "revenue"
    assert routed.year == 2024


def test_router_calculation_yoy_and_margin():
    yoy = route_finance_question("영업이익 증가율 알려줘")
    assert yoy.route == QueryRoute.CALCULATION
    assert yoy.account == "operating_profit"
    assert yoy.calc == "yoy"

    margin = route_finance_question("2024 영업이익률 계산")
    assert margin.route == QueryRoute.CALCULATION
    assert margin.calc == "margin"
    assert margin.year == 2024


def test_router_analysis_beats_numbers_when_why():
    routed = route_finance_question("반도체 수요 때문에 영업이익이 왜 늘었어?")
    assert routed.route == QueryRoute.ANALYSIS
