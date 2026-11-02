from fastapi import Response

from src.shared.php_response import PHP_HTML_CONTENT_TYPE, PHP_SESSION_HEADERS

# No space before charset: this is how PHP completes header("Content-type: text/xml").
_XML_CONTENT_TYPE = "text/xml;charset=UTF-8"


def kaspi_xml_response(xml: bytes) -> Response:
    return Response(content=xml, headers={"Content-Type": _XML_CONTENT_TYPE, **PHP_SESSION_HEADERS})


def kaspi_empty_page_response() -> Response:
    return Response(headers={"Content-Type": PHP_HTML_CONTENT_TYPE, **PHP_SESSION_HEADERS})
