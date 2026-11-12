from django.db import IntegrityError, transaction
from django.db.models import Avg, Exists, OuterRef, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .exceptions import Conflict
from .models import (BOOKED, Category, Favorite, Format, Level, Offer, OfferStatus,
                     Report, ReportStatus, Request, RequestStatus, Review, Slot, User)
from .pagination import DefaultPagination
from .permissions import IsModerator, IsOfferAuthor
from .serializers import (CategorySerializer, LoginSerializer, MeSerializer,
                          MessageSerializer, OfferSerializer, ReasonSerializer,
                          RegisterSerializer, ReportCreateSerializer, ReportSerializer,
                          RequestCreateSerializer, RequestSerializer,
                          ResolveReportSerializer, ReviewSerializer, SlotSerializer,
                          display_name)


# ======================= аутентификация / профиль =======================

class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user": MeSerializer(user).data}, status=201)


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        ser = LoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.validated_data["user"]
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user": MeSerializer(user).data})


class LogoutView(APIView):
    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=204)


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = MeSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user


class CategoryListView(generics.ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = CategorySerializer
    queryset = Category.objects.all()
    pagination_class = None


class UserDetailView(APIView):
    """Публичный профиль. Контакт — только владельцу и подтверждённому партнёру."""
    permission_classes = [AllowAny]

    def get(self, request, pk):
        user = get_object_or_404(User.objects.select_related("profile"), pk=pk, is_active=True)
        viewer = request.user
        can_see_contact = viewer.is_authenticated and (
            viewer.id == user.id or services.are_partners(viewer.id, user.id)
        )
        offers = offer_queryset(viewer).filter(author=user, status=OfferStatus.ACTIVE)[:20]
        reviews_qs = Review.objects.filter(request__slot__offer__author=user)
        reviews = reviews_qs.select_related("author", "request__slot__offer").order_by("-created_at")[:20]
        p = user.profile
        return Response({
            "id": user.id,
            "name": display_name(user),
            "bio": p.bio,
            "skills": p.skills,
            "format": p.format,
            "contact": p.contact if can_see_contact else None,
            "contact_visible": can_see_contact,
            "rating": reviews_qs.aggregate(a=Avg("score"))["a"],
            "offers": OfferSerializer(offers, many=True, context={"request": request}).data,
            "reviews": ReviewSerializer(reviews, many=True).data,
        })


# ======================= предложения =======================

def offer_queryset(user):
    taken = Exists(Request.objects.filter(slot=OuterRef("pk"), status__in=BOOKED))
    future_slots = (
        Slot.objects.filter(start_at__gt=timezone.now()).annotate(is_taken=taken).order_by("start_at")
    )
    qs = Offer.objects.select_related("author", "category").prefetch_related(
        Prefetch("slots", queryset=future_slots)
    )
    if user.is_authenticated:
        qs = qs.annotate(is_favorite=Exists(Favorite.objects.filter(user=user, offer=OuterRef("pk"))))
    return qs


def visible_to(qs, user):
    """Скрытые модератором предложения видят только автор и модераторы."""
    if user.is_authenticated and user.is_moderator:
        return qs
    cond = ~Q(status=OfferStatus.HIDDEN)
    if user.is_authenticated:
        cond |= Q(author=user)
    return qs.filter(cond)


ORDERINGS = {"created_at": "created_at", "-created_at": "-created_at", "title": "title",
             "duration": "duration_minutes", "-duration": "-duration_minutes"}


class OfferViewSet(viewsets.ModelViewSet):
    serializer_class = OfferSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    lookup_value_regex = r"\d+"

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [AllowAny()]
        if self.action in ("partial_update", "archive", "add_slot"):
            return [IsAuthenticated(), IsOfferAuthor()]
        return [IsAuthenticated()]

    def _apply_filters(self, qs):
        p = self.request.query_params
        q = p.get("q", "").strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q) | Q(category__name__icontains=q))
        if p.get("category", "").isdigit():
            qs = qs.filter(category_id=int(p["category"]))
        if p.get("level") in Level.values:
            qs = qs.filter(level=p["level"])
        fmt = p.get("format")
        if fmt in (Format.ONLINE, Format.OFFLINE):
            qs = qs.filter(format__in=[fmt, Format.BOTH])
        elif fmt == Format.BOTH:
            qs = qs.filter(format=Format.BOTH)
        if p.get("author", "").isdigit():
            qs = qs.filter(author_id=int(p["author"]))
        order = ORDERINGS.get(p.get("ordering", ""))
        return qs.order_by(order, "-id") if order else qs

    def get_queryset(self):
        user = self.request.user
        qs = offer_queryset(user)
        a = self.action
        if a == "list":
            return self._apply_filters(visible_to(qs.filter(status=OfferStatus.ACTIVE), user))
        if a == "mine":
            return self._apply_filters(qs.filter(author=user))
        if a == "favorites":
            qs = qs.filter(favorited_by__user=user, status=OfferStatus.ACTIVE)
            return self._apply_filters(visible_to(qs, user))
        if a in ("retrieve", "favorite", "report"):
            return visible_to(qs, user)
        return qs  # partial_update / archive / add_slot: доступ решает IsOfferAuthor (403)

    def perform_create(self, serializer):
        serializer.save(author=self.request.user)

    def partial_update(self, request, *args, **kwargs):
        offer = self.get_object()
        if offer.status != OfferStatus.ACTIVE:
            raise Conflict("Архивное или скрытое предложение редактировать нельзя.")
        return super().partial_update(request, *args, **kwargs)

    @action(detail=False, methods=["get"])
    def mine(self, request):
        return self.list(request)

    @action(detail=False, methods=["get"])
    def favorites(self, request):
        return self.list(request)

    @action(detail=True, methods=["post", "delete"])
    def favorite(self, request, pk=None):
        offer = self.get_object()
        if request.method == "POST":
            if offer.author_id == request.user.id:
                raise ValidationError("В избранное можно добавлять только предложения других участников.")
            Favorite.objects.get_or_create(user=request.user, offer=offer)
            return Response({"is_favorite": True}, status=201)
        Favorite.objects.filter(user=request.user, offer=offer).delete()
        return Response({"is_favorite": False})

    @action(detail=True, methods=["post"])
    def archive(self, request, pk=None):
        offer = self.get_object()
        if offer.status == OfferStatus.HIDDEN:
            raise Conflict("Предложение скрыто модератором.")
        if offer.status == OfferStatus.ARCHIVED:
            raise Conflict("Предложение уже в архиве.")
        offer.status = OfferStatus.ARCHIVED  # заявки и встречи остаются как есть
        offer.save(update_fields=["status"])
        return Response(self.get_serializer(offer).data)

    @action(detail=True, methods=["post"], url_path="slots")
    def add_slot(self, request, pk=None):
        offer = self.get_object()
        if offer.status != OfferStatus.ACTIVE:
            raise Conflict("Слоты можно добавлять только к активному предложению.")
        ser = SlotSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                slot = Slot.objects.create(offer=offer, **ser.validated_data)
        except IntegrityError:
            raise Conflict("Слот с таким временем начала уже есть.")
        return Response(SlotSerializer(slot).data, status=201)

    @action(detail=True, methods=["post"])
    def report(self, request, pk=None):
        offer = self.get_object()
        if offer.author_id == request.user.id:
            raise PermissionDenied("Нельзя пожаловаться на своё предложение.")
        ser = ReportCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if Report.objects.filter(reporter=request.user, offer=offer, status=ReportStatus.OPEN).exists():
            raise Conflict("Ваша жалоба на это предложение уже на рассмотрении.")
        try:
            with transaction.atomic():
                r = Report.objects.create(reporter=request.user, offer=offer, reason=ser.validated_data["reason"])
        except IntegrityError:
            raise Conflict("Ваша жалоба на это предложение уже на рассмотрении.")
        return Response({"id": r.id, "status": r.status}, status=201)


# ======================= заявки =======================

def _req_data(req, request):
    return RequestSerializer(req, context={"request": request}).data


class SlotRequestView(APIView):
    def post(self, request, pk):
        ser = RequestCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        req = services.create_request(pk, request.user, ser.validated_data["message"])
        return Response(_req_data(req, request), status=201)


class RequestListView(generics.ListAPIView):
    serializer_class = RequestSerializer

    def get_queryset(self):
        u, p = self.request.user, self.request.query_params
        incoming, outgoing = Q(slot__offer__author=u), Q(student=u)
        box = p.get("box", "all")
        cond = incoming if box == "incoming" else outgoing if box == "outgoing" else incoming | outgoing
        qs = (Request.objects.filter(cond)
              .select_related("slot__offer__author", "student", "review")
              .order_by("-created_at", "-id"))
        if p.get("status") in RequestStatus.values:
            qs = qs.filter(status=p["status"])
        return qs


class RequestDetailView(generics.RetrieveAPIView):
    serializer_class = RequestSerializer

    def get_object(self):
        req = get_object_or_404(
            Request.objects.select_related("slot__offer__author", "student", "review"), pk=self.kwargs["pk"]
        )
        if not (services.is_author(req, self.request.user) or services.is_student(req, self.request.user)):
            raise PermissionDenied("Эта заявка вам недоступна.")
        return req


def _transition_view(fn):
    class TransitionView(APIView):
        def post(self, request, pk):
            return Response(_req_data(fn(pk, request.user), request))
    return TransitionView.as_view()


AcceptView = _transition_view(services.accept_request)
RejectView = _transition_view(services.reject_request)
CancelView = _transition_view(services.cancel_request)
CompleteView = _transition_view(services.complete_request)


class MessagePagination(DefaultPagination):
    page_size = 100


class MessageListCreateView(generics.GenericAPIView):
    serializer_class = MessageSerializer
    pagination_class = MessagePagination

    def get(self, request, pk):
        req = services.get_chat_request(pk, request.user)
        page = self.paginate_queryset(req.messages.select_related("sender"))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def post(self, request, pk):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        msg = services.add_message(pk, request.user, ser.validated_data["text"])
        return Response(self.get_serializer(msg).data, status=201)


class ReviewCreateView(APIView):
    def post(self, request, pk):
        ser = ReviewSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        review = services.add_review(pk, request.user, ser.validated_data["score"],
                                     ser.validated_data.get("text", ""))
        return Response(ReviewSerializer(review).data, status=201)


# ======================= модерация =======================

class ReportListView(generics.ListAPIView):
    permission_classes = [IsModerator]
    serializer_class = ReportSerializer

    def get_queryset(self):
        st = self.request.query_params.get("status", ReportStatus.OPEN)
        qs = Report.objects.select_related("reporter", "offer")
        return qs.filter(status=st) if st in ReportStatus.values else qs


class ReportResolveView(APIView):
    permission_classes = [IsModerator]

    def post(self, request, pk):
        ser = ResolveReportSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        act, reason = ser.validated_data["action"], ser.validated_data["reason"].strip()
        now = timezone.now()
        with transaction.atomic():
            report = Report.objects.select_for_update(of=("self",)).select_related("offer").filter(pk=pk).first()
            if report is None:
                raise NotFound("Жалоба не найдена.")
            if report.status != ReportStatus.OPEN:
                raise Conflict("Жалоба уже обработана.")
            if act == "hide":
                offer = report.offer
                offer.status = OfferStatus.HIDDEN
                offer.hidden_reason = reason
                offer.save(update_fields=["status", "hidden_reason"])
                # все открытые жалобы на это предложение закрываются одним решением
                Report.objects.filter(offer=offer, status=ReportStatus.OPEN).update(
                    status=ReportStatus.RESOLVED, handled_by=request.user, handled_at=now, resolution=reason)
            else:
                report.status = ReportStatus.DISMISSED
                report.handled_by, report.handled_at, report.resolution = request.user, now, reason
                report.save()
        report.refresh_from_db()
        return Response(ReportSerializer(report).data)


class OfferHideView(APIView):
    permission_classes = [IsModerator]

    def post(self, request, pk):
        ser = ReasonSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        offer = get_object_or_404(Offer, pk=pk)
        offer.status, offer.hidden_reason = OfferStatus.HIDDEN, ser.validated_data["reason"]
        offer.save(update_fields=["status", "hidden_reason"])
        return Response({"id": offer.id, "status": offer.status})


class OfferUnhideView(APIView):
    permission_classes = [IsModerator]

    def post(self, request, pk):
        offer = get_object_or_404(Offer, pk=pk)
        if offer.status != OfferStatus.HIDDEN:
            raise Conflict("Предложение не скрыто.")
        offer.status, offer.hidden_reason = OfferStatus.ACTIVE, ""
        offer.save(update_fields=["status", "hidden_reason"])
        return Response({"id": offer.id, "status": offer.status})
