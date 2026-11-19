from fastapi import Response

from src.php_compat.responses import PHP_SESSION_HEADERS

# No space before charset: this is how PHP completes header("Content-type: text/xml").
_XML_CONTENT_TYPE = "text/xml;charset=UTF-8"


def kaspi_xml_response(xml: bytes) -> Response:
    return Response(content=xml, headers={"Content-Type": _XML_CONTENT_TYPE, **PHP_SESSION_HEADERS})
