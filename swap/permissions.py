from rest_framework.permissions import BasePermission


class IsModerator(BasePermission):
    message = "Требуется роль модератора."

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.is_moderator)


class IsOfferAuthor(BasePermission):
    message = "Только автор предложения может это делать."

    def has_object_permission(self, request, view, obj):
        return obj.author_id == request.user.id
