"""Placeholders for FF_FACTORING_CESSION (appendix table + header)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

ALMATY_TZ = ZoneInfo("Asia/Almaty")
DEFAULT_TARIFF = Decimal("0.12")
DEFAULT_DISCOUNT_BY_PERIOD = {
    3: Decimal("0.045"),
    6: Decimal("0.07"),
    9: Decimal("0.10"),
    12: Decimal("0.12"),
    24: Decimal("0.17"),
}
PRODUCT_BRAND = "Smart Remont"
DEFAULT_STATUS = "Выдано"
DEFAULT_STATUS_KZ = "Берілді"
DEFAULT_SIGNER_POSITION_RU_GEN = "Директора"
DEFAULT_SIGNER_POSITION_KZ = "Директор"
TEST_POA_NUM = "01-2026"
TEST_MARK = " (тест)"

_MONTHS_RU = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
_MONTHS_KZ = (
    "қаңтар",
    "ақпан",
    "наурыз",
    "сәуір",
    "мамыр",
    "маусым",
    "шілде",
    "тамыз",
    "қыркүйек",
    "қазан",
    "қараша",
    "желтоқсан",
)
_MONTHS_KZ_LOC = (
    "қаңтардағы",
    "ақпандағы",
    "наурыздағы",
    "сәуірдегі",
    "мамырдағы",
    "маусымдағы",
    "шілдедегі",
    "тамыздағы",
    "қыркүйектегі",
    "қазандағы",
    "қарашадағы",
    "желтоқсандағы",
)

_ONES_RU = (
    "",
    "один",
    "два",
    "три",
    "четыре",
    "пять",
    "шесть",
    "семь",
    "восемь",
    "девять",
)
_ONES_FEM_RU = (
    "",
    "одна",
    "две",
    "три",
    "четыре",
    "пять",
    "шесть",
    "семь",
    "восемь",
    "девять",
)
_TEENS_RU = (
    "десять",
    "одиннадцать",
    "двенадцать",
    "тринадцать",
    "четырнадцать",
    "пятнадцать",
    "шестнадцать",
    "семнадцать",
    "восемнадцать",
    "девятнадцать",
)
_TENS_RU = (
    "",
    "",
    "двадцать",
    "тридцать",
    "сорок",
    "пятьдесят",
    "шестьдесят",
    "семьдесят",
    "восемьдесят",
    "девяносто",
)
_HUNDREDS_RU = (
    "",
    "сто",
    "двести",
    "триста",
    "четыреста",
    "пятьсот",
    "шестьсот",
    "семьсот",
    "восемьсот",
    "девятьсот",
)

_ONES_KZ = (
    "",
    "бір",
    "екі",
    "үш",
    "төрт",
    "бес",
    "алты",
    "жеті",
    "сегіз",
    "тоғыз",
)
_TEENS_KZ = (
    "он",
    "он бір",
    "он екі",
    "он үш",
    "он төрт",
    "он бес",
    "он алты",
    "он жеті",
    "он сегіз",
    "он тоғыз",
)
_TENS_KZ = (
    "",
    "",
    "жиырма",
    "отыз",
    "қырық",
    "елу",
    "алпыс",
    "жетпіс",
    "сексен",
    "тоқсан",
)
_HUNDREDS_KZ = (
    "",
    "жүз",
    "екі жүз",
    "үш жүз",
    "төрт жүз",
    "бес жүз",
    "алты жүз",
    "жеті жүз",
    "сегіз жүз",
    "тоғыз жүз",
)


def _as_date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=ALMATY_TZ).date()
        return value.astimezone(ALMATY_TZ).date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _as_datetime(value: date | datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=ALMATY_TZ)
        return value.astimezone(ALMATY_TZ)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=ALMATY_TZ)
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ALMATY_TZ)
    return parsed.astimezone(ALMATY_TZ)


def _money(amount: Decimal) -> str:
    # NBSP keeps "2 000 000" on one line in narrow table cells.
    return f"{amount.quantize(Decimal('1')):,}".replace(",", "\u00a0")


def _triad_ru(n: int, feminine: bool) -> str:
    hundreds, rest = divmod(n, 100)
    ones_src = _ONES_FEM_RU if feminine else _ONES_RU
    parts: list[str] = []
    if hundreds:
        parts.append(_HUNDREDS_RU[hundreds])
    if rest >= 10 and rest <= 19:
        parts.append(_TEENS_RU[rest - 10])
    else:
        tens, ones = divmod(rest, 10)
        if tens:
            parts.append(_TENS_RU[tens])
        if ones:
            parts.append(ones_src[ones])
    return " ".join(parts)


def amount_text_ru(amount: Decimal) -> str:
    n = int(amount)
    if n == 0:
        return "ноль"
    parts: list[str] = []
    billions, n = divmod(n, 1_000_000_000)
    millions, n = divmod(n, 1_000_000)
    thousands, rest = divmod(n, 1000)
    if billions:
        word = _triad_ru(billions, False)
        parts.append(f"{word} миллиард{_ru_suffix(billions, '', 'а', 'ов')}")
    if millions:
        word = _triad_ru(millions, False)
        parts.append(f"{word} миллион{_ru_suffix(millions, '', 'а', 'ов')}")
    if thousands:
        word = _triad_ru(thousands, True)
        parts.append(f"{word} тысяч{_ru_suffix(thousands, 'а', 'и', '')}")
    if rest:
        parts.append(_triad_ru(rest, False))
    return " ".join(parts)


def _ru_suffix(n: int, one: str, few: str, many: str) -> str:
    n = n % 100
    if 11 <= n <= 14:
        return many
    n = n % 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def _triad_kz(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts: list[str] = []
    if hundreds:
        parts.append(_HUNDREDS_KZ[hundreds])
    if rest >= 10 and rest <= 19:
        parts.append(_TEENS_KZ[rest - 10])
    else:
        tens, ones = divmod(rest, 10)
        if tens:
            parts.append(_TENS_KZ[tens])
        if ones:
            parts.append(_ONES_KZ[ones])
    return " ".join(parts)


def amount_text_kz(amount: Decimal) -> str:
    n = int(amount)
    if n == 0:
        return "нөл"
    parts: list[str] = []
    billions, n = divmod(n, 1_000_000_000)
    millions, n = divmod(n, 1_000_000)
    thousands, rest = divmod(n, 1000)
    if billions:
        parts.append(f"{_triad_kz(billions)} миллиард")
    if millions:
        parts.append(f"{_triad_kz(millions)} миллион")
    if thousands:
        parts.append(f"{_triad_kz(thousands)} мың")
    if rest:
        parts.append(_triad_kz(rest))
    return " ".join(parts)


def product_name(period: int | None) -> str:
    months = period or 12
    unit = "месяц" + _ru_suffix(months, "", "а", "ев")
    return f"{PRODUCT_BRAND} факторинг {months} {unit}"


def product_name_kz(period: int | None) -> str:
    return f"{PRODUCT_BRAND} факторинг {period or 12} ай"


def bare_company_name(official: str) -> str:
    """'ТОО «Smart Remont Azure»' -> 'Smart Remont Azure' (templates add ТОО «…» / «…» ЖШС)."""
    name = re.sub(r"^\s*(ТОО|ЖШС)\s+", "", official or "")
    return name.strip().strip("«»\"“”„'").strip()


def _split_fio(fio: str) -> tuple[str, str, str]:
    parts = (fio or "").split()
    surname = parts[0] if parts else ""
    name = parts[1] if len(parts) > 1 else ""
    patronymic = " ".join(parts[2:])
    return surname, name, patronymic


def _is_female(surname: str, patronymic: str) -> bool:
    tail = patronymic.lower()
    if tail:
        return tail.endswith(("вна", "чна", "кызы", "қызы"))
    return surname.lower().endswith(("ова", "ева", "ёва", "ина", "ына", "ая"))


def surname_genitive_ru(surname: str, *, female: bool) -> str:
    """Родительный падеж фамилии (в лице Директора Усембековой …).

    Only regular Russian-style endings are declined; Kazakh forms like
    Аманкелдіұлы/-қызы and other endings are left as is."""
    lower = surname.lower()
    if female:
        if lower.endswith(("ова", "ева", "ёва", "ина", "ына")):
            return surname[:-1] + "ой"
        if lower.endswith("ая"):
            return surname[:-2] + "ой"
        return surname
    if lower.endswith(("ұлы", "улы")):
        return surname
    if lower.endswith(("ский", "цкий")):
        return surname[:-2] + "ого"
    if lower.endswith(("ов", "ев", "ёв", "ин", "ын")) or re.search(r"[бвгджзклмнпрстфхцчшщ]$", lower):
        return surname + "а"
    return surname


def _initials(*names: str) -> str:
    return "".join(f"{n[0].upper()}." for n in names if n)


def signer_names(director_fio: str) -> dict[str, str]:
    surname, name, patronymic = _split_fio(director_fio)
    initials = _initials(name, patronymic)
    female = _is_female(surname, patronymic)
    short_ru = f"{surname} {initials}".strip()
    return {
        "short_ru": short_ru,
        "short_ru_gen": f"{surname_genitive_ru(surname, female=female)} {initials}".strip(),
        "short_kz": f"{initials} {surname}".strip(),
    }


def date_ru_long(value: date, *, suffix: str = "г.") -> str:
    return f"«{value.day:02d}» {_MONTHS_RU[value.month - 1]} {value.year} {suffix}"


def date_kz_long(value: date, *, locative: bool = False) -> str:
    month = (_MONTHS_KZ_LOC if locative else _MONTHS_KZ)[value.month - 1]
    return f"{value.year} жылғы «{value.day:02d}» {month}"


def parse_discount_by_period(raw: Any) -> dict[int, Decimal]:
    if not isinstance(raw, dict) or not raw:
        return dict(DEFAULT_DISCOUNT_BY_PERIOD)
    parsed: dict[int, Decimal] = {}
    for key, value in raw.items():
        try:
            period = int(key)
            rate = Decimal(str(value))
        except (TypeError, ValueError):
            continue
        if period > 0 and rate >= 0:
            parsed[period] = rate
    return parsed or dict(DEFAULT_DISCOUNT_BY_PERIOD)


def allowed_periods(config: dict[str, Any] | None) -> list[int]:
    raw = (config or {}).get("periods")
    if isinstance(raw, list) and raw:
        periods: list[int] = []
        for item in raw:
            try:
                period = int(item)
            except (TypeError, ValueError):
                continue
            if period > 0:
                periods.append(period)
        if periods:
            return periods
    return sorted(parse_discount_by_period((config or {}).get("discount_by_period")))


def tariff_for_period(
    discount_by_period: dict[int, Decimal] | None,
    period: int | None,
) -> Decimal:
    mapping = discount_by_period or DEFAULT_DISCOUNT_BY_PERIOD
    if period is not None and period in mapping:
        return mapping[period]
    return mapping.get(12, DEFAULT_TARIFF)


def format_tariff_percent(rate: Decimal) -> str:
    percent = (rate * 100).quantize(Decimal("0.1"))
    if percent == percent.to_integral():
        return f"{int(percent)}%"
    return f"{percent.normalize()}%".replace(".", ",")


def _signer_basis(
    signer_config: dict[str, Any] | None,
    signing_date: date,
) -> tuple[str, str]:
    """Основание полномочий подписанта клиента (RU, KZ).

    Until the real power of attorney is put into config, test values are used
    and marked "(тест)"."""
    config = signer_config or {}
    poa_num = str(config.get("poa_num") or "").strip()
    poa_date = _as_date(config.get("poa_date"))
    mark = ""
    if not poa_num or poa_date is None:
        poa_num, poa_date, mark = TEST_POA_NUM, signing_date, TEST_MARK
    ru = f"Доверенности № {poa_num} от {poa_date.day:02d} {_MONTHS_RU[poa_date.month - 1]} {poa_date.year} г.{mark}"
    kz = f"{poa_date.year} жылғы {poa_date.day:02d} {_MONTHS_KZ_LOC[poa_date.month - 1]} № {poa_num} сенімхат{mark}"
    return ru, kz


def build_cession_placeholders(
    *,
    contract_number: str,
    issue_date: date,
    framework_num: str,
    framework_date: date,
    signing_date: date | None = None,
    company_name: str = "",
    company_iik: str = "",
    client_signer: str = "",
    signer_config: dict[str, Any] | None = None,
    applications: list[dict[str, Any]],
    discount_by_period: dict[int, Decimal] | None = None,
) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Header + table rows for the cession template.

    ``issue_date`` is the sale date (date the applications were issued);
    ``signing_date`` is the day the cession is formed and signed;
    ``framework_num``/``framework_date`` — the ТОО's factoring agreement with the bank."""
    rates = discount_by_period or DEFAULT_DISCOUNT_BY_PERIOD
    signing_date = signing_date or issue_date
    client_name = bare_company_name(company_name)
    partner_ru = f"ТОО «{client_name}»" if client_name else ""
    partner_kz = f"«{client_name}» ЖШС" if client_name else ""

    total_claims = Decimal("0")
    total_discount = Decimal("0")
    rows: list[dict[str, str]] = []
    for index, item in enumerate(applications, start=1):
        principal = Decimal(str(item.get("principal") or 0))
        period = item.get("period")
        period_int = int(period) if period is not None else 12
        tariff = tariff_for_period(rates, period_int)
        discount = (principal * tariff).quantize(Decimal("1"))
        financing = principal - discount
        total_claims += principal
        total_discount += discount
        issued = _as_datetime(item.get("issued_at"))
        uuid = str(item.get("uuid") or "")
        credit_contract = str(item.get("credit_contract") or "")
        rows.append(
            {
                "num": str(index),
                "application_num": uuid,
                "date": issued.strftime("%d.%m.%Y") if issued else "",
                "time": issued.strftime("%H:%M") if issued else "",
                "contract_sum": _money(principal),
                "term_ru": f"{period_int} мес.",
                "term_kz": f"{period_int} ай",
                "tariff": format_tariff_percent(tariff),
                "discount_sum": _money(discount),
                "purchase_sum": _money(principal),
                "partner_ru": partner_ru,
                "partner_kz": partner_kz,
                "product_ru": product_name(period_int),
                "product_kz": product_name_kz(period_int),
                "status_ru": DEFAULT_STATUS,
                "status_kz": DEFAULT_STATUS_KZ,
                "contract_num": credit_contract,
                "financing_sum": _money(financing),
                # legacy template (FF_FACTORING_CESSION v1)
                "n": str(index),
                "uuid": uuid,
                "issued_date": issued.strftime("%d.%m.%Y") if issued else "",
                "issued_time": issued.strftime("%H:%M") if issued else "",
                "principal": _money(principal),
                "period": str(period_int),
                "discount": _money(discount),
                "purchase_amount": _money(principal),
                "partner_name": partner_ru,
                "product_name": product_name(period_int),
                "status": DEFAULT_STATUS,
                "credit_contract": credit_contract,
                "financing_amount": _money(financing),
            }
        )

    total_financing = total_claims - total_discount
    config = signer_config or {}
    names = signer_names(client_signer)
    basis_ru, basis_kz = _signer_basis(config, signing_date)

    header = {
        "client_name": client_name,
        "client_account": company_iik,
        "financing_sum": _money(total_financing),
        "financing_sum_words_ru": amount_text_ru(total_financing),
        "financing_sum_words_kz": amount_text_kz(total_financing),
        "claims_sum": _money(total_claims),
        "claims_sum_words_ru": amount_text_ru(total_claims),
        "claims_sum_words_kz": amount_text_kz(total_claims),
        # Bank: стоимость уступки = сумма заказов; дисконт выставляется отдельно (п.2.3).
        "assignment_sum": _money(total_claims),
        "assignment_sum_words_ru": amount_text_ru(total_claims),
        "assignment_sum_words_kz": amount_text_kz(total_claims),
        "framework_num": framework_num,
        "framework_date_ru": f"«{framework_date.day:02d}» {_MONTHS_RU[framework_date.month - 1]} {framework_date.year}",
        "framework_date_kz": date_kz_long(framework_date, locative=True),
        "total_contract_sum": _money(total_claims),
        "total_discount_sum": _money(total_discount),
        "total_purchase_sum": _money(total_claims),
        "assignment_contract_num": contract_number,
        "application_date_ru": date_ru_long(signing_date, suffix="года"),
        "application_date_kz": date_kz_long(signing_date),
        "sale_date_ru": date_ru_long(issue_date),
        "sale_date_kz": f"{issue_date.year} ж. «{issue_date.day:02d}» {_MONTHS_KZ[issue_date.month - 1]}",
        "appendix_date_ru": date_ru_long(issue_date),
        "appendix_date_kz": date_kz_long(issue_date, locative=True),
        "valid_from_date": issue_date.strftime("%d.%m.%Y"),
        "client_basis_ru": basis_ru,
        "client_basis_kz": basis_kz,
        "client_signer_position_ru_gen": str(config.get("position_ru_gen") or DEFAULT_SIGNER_POSITION_RU_GEN),
        "client_signer_position_kz": str(config.get("position_kz") or DEFAULT_SIGNER_POSITION_KZ),
        "client_signer_short_ru": names["short_ru"],
        "client_signer_short_ru_gen": str(config.get("fio_ru_gen") or names["short_ru_gen"]),
        "client_signer_short_kz": names["short_kz"],
        # legacy template (FF_FACTORING_CESSION v1)
        "contract_number": contract_number,
        "issue_date": issue_date.strftime("%d.%m.%Y"),
        "issue_day": f"{issue_date.day:02d}",
        "issue_month": _MONTHS_RU[issue_date.month - 1],
        "issue_month_kz": _MONTHS_KZ[issue_date.month - 1],
        "issue_year": str(issue_date.year),
        "company_name": company_name,
        "company_iik": company_iik,
        "client_signer": client_signer,
        "total_claims": _money(total_claims),
        "total_claims_text": amount_text_ru(total_claims),
        "total_claims_text_kz": amount_text_kz(total_claims),
        "total_financing": _money(total_financing),
        "total_financing_text": amount_text_ru(total_financing),
        "total_financing_text_kz": amount_text_kz(total_financing),
        "payment_amount": _money(total_claims),
        "payment_amount_text": amount_text_ru(total_claims),
        "partner": partner_ru,
        "applications_count": str(len(applications)),
    }
    return header, rows
