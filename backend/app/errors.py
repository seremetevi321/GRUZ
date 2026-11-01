class DomainError(Exception):
    """Ожидаемый отказ бизнес-логики: понятный код и текст для пользователя."""

    def __init__(self, status: int, code: str, message: str, order_id: int | None = None, log: bool = False):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.order_id, self.log = order_id, log


def forbidden(msg: str = "Нет доступа") -> DomainError:
    return DomainError(403, "forbidden", msg)


def not_found(msg: str = "Не найдено") -> DomainError:
    return DomainError(404, "not_found", msg)


def conflict(code: str, msg: str, order_id: int | None = None, log: bool = False) -> DomainError:
    return DomainError(409, code, msg, order_id, log)
