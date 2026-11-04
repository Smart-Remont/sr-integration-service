from src.php_compat.pdo_errors import php_clean_error, php_error_state, php_pdo_message


class ClientSignDatabaseError(Exception):
    """What ``getSP``, ``readSP``, ``scalarSP`` and ``execSP`` put into ``error``."""

    def __init__(self, pdo_message: str, error_code: str) -> None:
        self.message = php_clean_error(pdo_message)
        # SQLSTATE or the class name: what a log may hold, while the text can name the signer's IIN.
        self.error_code = error_code
        super().__init__(self.message)

    @classmethod
    def from_exception(cls, exc: BaseException) -> ClientSignDatabaseError:
        return cls(php_pdo_message(exc), php_error_state(exc))


class ClientSignError(Exception):
    """PHP ``throw new Exception``: the page or the callback answer carries this text."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
