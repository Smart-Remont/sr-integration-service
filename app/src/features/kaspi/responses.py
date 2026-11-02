from fastapi import Response

_SESSION_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Expires": "Thu, 19 Nov 1981 08:52:00 GMT",
    "Pragma": "no-cache",
}
# No space before charset: this is how PHP completes header("Content-type: text/xml").
_XML_CONTENT_TYPE = "text/xml;charset=UTF-8"
_HTML_CONTENT_TYPE = "text/html; charset=UTF-8"


def kaspi_xml_response(xml: bytes) -> Response:
    return Response(content=xml, headers={"Content-Type": _XML_CONTENT_TYPE, **_SESSION_HEADERS})


def kaspi_empty_page_response() -> Response:
    return Response(headers={"Content-Type": _HTML_CONTENT_TYPE, **_SESSION_HEADERS})
