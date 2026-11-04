"""The fake database of ``kaspi-request-pay`` and the XML its tests expect."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from tests.big_integration.fakes import FakeConnection

REQUEST_ROUTE = "/api/kaspi/kaspi-request-pay"
PROXY_SECRET = "test-kaspi-proxy-secret"
PROXY_HEADERS = {"X-Kaspi-Proxy-Secret": PROXY_SECRET}
XML_TYPE = "text/xml;charset=UTF-8"
HTML_TYPE = "text/html; charset=UTF-8"
DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>\n'
CHECK = "client.kaspi_check"
REQUEST_INFO = "client.kaspi_request_get_info"
REQUEST_PAY = "client.kaspi_request_pay"
AFTER = "public.after_payment_operations"
LOG = "client.insert_kaspi_pay_log"
LOCK = "pg_catalog.pg_advisory_xact_lock"
WRITES = {REQUEST_PAY, AFTER, LOG}

REQUEST_INFO_ROW = {
    "prop_fio": "Иванов Иван",
    "prop_number": "12345",
    "prop_date": "01.02.2026",
    "resident_name": "ЖК Тест",
    "company_bin": "123456789012",
    "company_name_official": "ТОО «Компания»",
}
FIELDS = (
    "  <fields>\n"
    '    <field1 name="ФИО">Иванов Иван</field1>\n'
    '    <field2 name="Номер договора">12345</field2>\n'
    '    <field3 name="Дата договора">01.02.2026</field3>\n'
    '    <field4 name="ЖК">ЖК Тест</field4>\n'
    '    <field5 name="Название компании">ТОО «Компания»</field5>\n'
    "  </fields>\n"
)
REQUEST_CHECK_XML = (
    DECLARATION.decode()
    + "<response>\n  <txn_id>T1</txn_id>\n  <result>0</result>\n  <bin>123456789012</bin>\n"
    + FIELDS
    + "  <comment/>\n</response>\n"
).encode()
ACCEPTED = {"account": "77", "iin": "990101300000", "sum": "150000", "txn_id": "T1"}
PAY = {**ACCEPTED, "command": "pay", "txn_date": "20261102120000"}
CHECK_QUERY = {**ACCEPTED, "command": "check"}


def pay_xml(provider_txn: int, *, company_bin: str = "<bin>123456789012</bin>") -> bytes:
    return (
        DECLARATION.decode() + "<response>\n  <txn_id>T1</txn_id>\n"
        f"  <prv_txn>{provider_txn}</prv_txn>\n  <sum>150000</sum>\n  <result>0</result>\n"
        f"  {company_bin}\n  <comment>OK</comment>\n</response>\n"
    ).encode()


def refusal_xml(code: int, *, txn_id: str | None = "T1", comment: str | None = None) -> bytes:
    lines = [DECLARATION.decode(), "<response>\n"]
    if txn_id:
        lines.append(f"  <txn_id>{txn_id}</txn_id>\n")
    lines.append(f"  <result>{code}</result>\n")
    if comment:
        lines.append(f"  <comment>{comment}</comment>\n")
    lines.append("</response>\n")
    return "".join(lines).encode()


class KaspiConnection(FakeConnection):
    """Keeps writes until commit, and undoes the writes of a failed savepoint."""

    def __init__(self, responses: dict[str, Any]) -> None:
        super().__init__(responses)
        self.pending: list[str] = []
        self.queries: list[str] = []

    def _answer(self, function: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
        answer = super()._answer(function, args)
        if function in WRITES:
            self.pending.append(function)
        return answer

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.queries.append(query)
        return await super().fetch(query, *args)

    async def execute(self, query: str, *args: Any) -> None:
        self.queries.append(query)
        await super().execute(query, *args)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        savepoint = len(self.pending)
        try:
            yield
        except Exception:
            del self.pending[savepoint:]
            raise

    def functions(self) -> list[str]:
        return [function for function, _ in self.calls]

    def args_of(self, function: str) -> tuple[Any, ...]:
        return next(args for name, args in self.calls if name == function)


class Database:
    """The request transaction and the connections ``committed()`` takes from the pool."""

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {
            CHECK: [{"id": 0}],
            REQUEST_INFO: [REQUEST_INFO_ROW],
            REQUEST_PAY: [{"id": 54321}],
            LOCK: [{"pg_advisory_xact_lock": ""}],
            AFTER: [{"id": 0}],
            LOG: [{"id": 0}],
        }
        self.request = KaspiConnection(self.responses)
        self.separate = KaspiConnection(self.responses)
        self.committed: list[str] = []
        self.events: list[str] = []

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[KaspiConnection]:
        yield self.separate

    def acquire(self) -> Any:
        return self._acquire()

    def rows_logged_separately(self) -> list[tuple[Any, ...]]:
        return [args for name, args in self.separate.calls if name == LOG]

    def logged(self) -> tuple[Any, ...]:
        return self.request.args_of(LOG)


def _assert_xml(response: Any, body: bytes) -> None:
    assert response.status_code == 200
    assert response.content == body
    assert response.headers["content-type"] == XML_TYPE
