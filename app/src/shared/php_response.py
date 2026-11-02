"""Headers PHP adds to every answer of a controller that opens a session."""

PHP_SESSION_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Expires": "Thu, 19 Nov 1981 08:52:00 GMT",
    "Pragma": "no-cache",
}
PHP_HTML_CONTENT_TYPE = "text/html; charset=UTF-8"
