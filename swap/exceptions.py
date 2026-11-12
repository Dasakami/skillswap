from rest_framework.exceptions import APIException, ValidationError
from rest_framework.views import exception_handler as drf_exception_handler


class Conflict(APIException):
    """409: действие не соответствует текущему состоянию объекта."""
    status_code = 409
    default_detail = "Действие невозможно в текущем состоянии."
    default_code = "conflict"


def handler(exc, context):
    """Единый формат ошибок: всегда есть "detail"; для 400 ещё "errors" по полям."""
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    if isinstance(exc, ValidationError):
        response.data = {"detail": "Проверьте введённые данные.", "errors": response.data}
    return response
