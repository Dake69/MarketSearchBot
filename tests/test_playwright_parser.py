from app.marketplace.playwright_client import _currency_from_price, _fields_from_lines


def test_marketplace_card_line_fields() -> None:
    fields = _fields_from_lines(
        ["Just listed", "CZK5,500", "MacBook Air M1", "Prague, Czech Republic"],
        "MacBook Air M1 in Prague",
        "123",
    )
    assert fields == ("MacBook Air M1", "CZK5,500", "Prague, Czech Republic", "")
    assert _currency_from_price("CZK5,500") == "CZK"
