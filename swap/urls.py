from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter(trailing_slash=False)
router.register("offers", views.OfferViewSet, basename="offer")

urlpatterns = [
    path("auth/register", views.RegisterView.as_view()),
    path("auth/login", views.LoginView.as_view()),
    path("auth/logout", views.LogoutView.as_view()),
    path("me", views.MeView.as_view()),
    path("users/<int:pk>", views.UserDetailView.as_view()),
    path("categories", views.CategoryListView.as_view()),

    path("slots/<int:pk>/requests", views.SlotRequestView.as_view()),
    path("requests", views.RequestListView.as_view()),
    path("requests/<int:pk>", views.RequestDetailView.as_view()),
    path("requests/<int:pk>/accept", views.AcceptView),
    path("requests/<int:pk>/reject", views.RejectView),
    path("requests/<int:pk>/cancel", views.CancelView),
    path("requests/<int:pk>/complete", views.CompleteView),
    path("requests/<int:pk>/messages", views.MessageListCreateView.as_view()),
    path("requests/<int:pk>/review", views.ReviewCreateView.as_view()),

    path("moderation/reports", views.ReportListView.as_view()),
    path("moderation/reports/<int:pk>/resolve", views.ReportResolveView.as_view()),
    path("moderation/offers/<int:pk>/hide", views.OfferHideView.as_view()),
    path("moderation/offers/<int:pk>/unhide", views.OfferUnhideView.as_view()),
] + router.urls
