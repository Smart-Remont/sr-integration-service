"""Scalar examples. Shapes are the real PHP responses, with made-up values."""

_CHECK_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <txn_id>1234567890</txn_id>
  <result>0</result>
  <bin>123456789012</bin>
  <fields>
    <field1 name="ФИО">Иванов Иван</field1>
    <field2 name="Номер договора">12345</field2>
    <field3 name="Дата договора">01.02.2026</field3>
    <field4 name="ЖК">Название ЖК</field4>
    <field5 name="Название компании">ТОО «Компания»</field5>
  </fields>
  <comment/>
</response>
"""
_PAY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <txn_id>1234567890</txn_id>
  <prv_txn>54321</prv_txn>
  <sum>150000</sum>
  <result>0</result>
  <bin>123456789012</bin>
  <comment>OK</comment>
</response>
"""
_REFUSED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <txn_id>1234567890</txn_id>
  <result>3</result>
</response>
"""


def _query_parameters(*names: str) -> dict[str, object]:
    return {
        "parameters": [
            {"name": name, "in": "query", "required": False, "schema": {"type": "string"}}
            for name in names
        ],
    }


KASPI_PAY_PARAMETERS = _query_parameters("command", "txn_id", "txn_date", "account", "iin", "sum")
KASPI_PAY_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {
        "description": (
            "XML: `result` 0 — успех, 1 — не найдено, 3 — уже оплачено, 5 — ошибка. "
            "Неизвестный `command` — пустой ответ `text/html`."
        ),
        "content": {
            "text/xml": {
                "examples": {
                    "check": {"value": _CHECK_XML},
                    "pay": {"value": _PAY_XML},
                    "refused": {"value": _REFUSED_XML},
                },
            },
        },
    },
    403: {"description": "Заголовка `X-Kaspi-Proxy-Secret` нет или он неверный"},
    503: {"description": "`KASPI_PROXY_SECRET` не настроен"},
}
