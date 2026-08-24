from app.services.angles import (
    ANGLE_FLOW,
    ANGLE_ISSUE,
    ANGLE_ORDER,
    ANGLE_PERSON,
    classify_angle,
    order_items_by_angle,
    pick_three_by_angle,
)
from app.services.sources import SourceItem


def test_classify_angle_splits_flow_issue_person():
    assert classify_angle("이번 주 반도체 시황 브리핑") == ANGLE_FLOW
    assert classify_angle("React 19 업데이트 출시") == ANGLE_ISSUE
    assert classify_angle("신간 소설 출간 소식") == ANGLE_ISSUE
    assert classify_angle("일론 머스크 인터뷰") == ANGLE_PERSON
    assert classify_angle("이재용 회장 연설") == ANGLE_PERSON


def test_order_items_by_angle_is_flow_issue_person():
    items = [
        {"title": "인물글", "angle": ANGLE_PERSON, "url": "https://c.example"},
        {"title": "이슈글", "angle": ANGLE_ISSUE, "url": "https://b.example"},
        {"title": "흐름글", "angle": ANGLE_FLOW, "url": "https://a.example"},
    ]
    ordered = order_items_by_angle(items)
    assert [row["angle"] for row in ordered] == list(ANGLE_ORDER)
    assert [row["url"] for row in ordered] == ["https://a.example", "https://b.example", "https://c.example"]


def test_pick_three_by_angle_takes_one_of_each_and_mixes_kinds():
    items = [
        SourceItem(kind="아티클", title="이번 주 시장 동향", url="https://a.example", summary="시황", source="s"),
        SourceItem(kind="아티클", title="또 다른 시황", url="https://a2.example", summary="브리핑", source="s"),
        SourceItem(kind="유튜브", title="타입스크립트 5 업데이트 출시", url="https://b.example", summary="릴리즈", source="s"),
        SourceItem(kind="커뮤니티", title="이재용 인터뷰 토론", url="https://c.example", summary="발언", source="s"),
    ]
    picked = pick_three_by_angle(items)
    assert [ang for ang, _ in picked] == list(ANGLE_ORDER)
    urls = [item.url for _, item in picked]
    assert urls[0] == "https://a.example"
    assert "https://b.example" in urls
    assert "https://c.example" in urls
    kinds = {item.kind for _, item in picked}
    assert kinds >= {"아티클", "유튜브", "커뮤니티"}
