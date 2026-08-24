from app.services.xbrl_parser import parse_xbrl_xml

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:ifrs="http://xbrl.ifrs.org/taxonomy">
  <ifrs:Revenue contextRef="FY2024" unitRef="KRW">300870903000000</ifrs:Revenue>
  <ifrs:OperatingIncome contextRef="FY2024" unitRef="KRW">32726131000000</ifrs:OperatingIncome>
  <ifrs:ProfitLoss contextRef="FY2024" unitRef="KRW">33621351000000</ifrs:ProfitLoss>
  <ifrs:RevenueTextBlock>매출액은 PDF가 아니라 이 문장에 300870903000000원이라고 적혀 있습니다.</ifrs:RevenueTextBlock>
  <ifrs:Comment>not-a-number</ifrs:Comment>
</xbrli:xbrl>
"""


def test_xbrl_parser_takes_numeric_facts_and_skips_text_blocks():
    facts = parse_xbrl_xml(SAMPLE, ticker="005930", year=2024)
    accounts = {f.account: f.value for f in facts}
    assert accounts["revenue"] == 300_870_903_000_000
    assert accounts["operating_profit"] == 32_726_131_000_000
    assert accounts["net_income"] == 33_621_351_000_000
    assert all("textblock" not in f.account for f in facts)
    assert all(isinstance(f.value, int) for f in facts)
