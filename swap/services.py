"""Бизнес-правила SkillSwap. Все переходы статусов заявок — только здесь.

Защита от гонок двойная:
  1) select_for_update по строке слота (и заявки) внутри transaction.atomic;
  2) частичные уникальные индексы в БД (uniq_booked_request_per_slot и др.) —
     последний рубеж, если логика всё же пропустит конфликт.
"""
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied

from .exceptions import Conflict
from .models import (ACTIVE_REQUEST, BOOKED, Message, OfferStatus, Request,
                     RequestStatus, Review, Slot)

S = RequestStatus


def _get_request(pk):
    try:
        return Request.objects.select_related("slot__offer__author", "student").get(pk=pk)
    except Request.DoesNotExist:
        raise NotFound("Заявка не найдена.")


def _lock_request(pk):
    """Блокирует слот, затем заявку (всегда в таком порядке — без дедлоков).
    Вызывать только внутри transaction.atomic()."""
    req = _get_request(pk)
    Slot.objects.select_for_update().get(pk=req.slot_id)
    return (
        Request.objects.select_for_update(of=("self",))
        .select_related("slot__offer__author", "student")
        .get(pk=pk)
    )


def is_author(req, user):
    return req.slot.offer.author_id == user.id


def is_student(req, user):
    return req.student_id == user.id


def are_partners(a_id, b_id):
    """Подтверждённые партнёры: между ними есть принятая или завершённая встреча."""
    return Request.objects.filter(status__in=BOOKED).filter(
        Q(student_id=a_id, slot__offer__author_id=b_id)
        | Q(student_id=b_id, slot__offer__author_id=a_id)
    ).exists()


def create_request(slot_id, student, message=""):
    try:
        with transaction.atomic():
            try:
                slot = Slot.objects.select_for_update(of=("self",)).select_related("offer").get(pk=slot_id)
            except Slot.DoesNotExist:
                raise NotFound("Слот не найден.")
            offer = slot.offer
            if offer.status == OfferStatus.HIDDEN:
                raise NotFound("Слот не найден.")
            if offer.author_id == student.id:
                raise PermissionDenied("Нельзя записаться на собственное предложение.")
            if offer.status != OfferStatus.ACTIVE:
                raise Conflict("Предложение в архиве — новые заявки не принимаются.")
            if slot.start_at <= timezone.now():
                raise Conflict("Этот слот уже начался или прошёл.")
            if Request.objects.filter(slot=slot, status__in=BOOKED).exists():
                raise Conflict("Этот слот уже занят.")
            if Request.objects.filter(slot=slot, student=student, status__in=ACTIVE_REQUEST).exists():
                raise Conflict("Вы уже отправили заявку на этот слот.")
            return Request.objects.create(slot=slot, student=student, message=message)
    except IntegrityError:
        raise Conflict("Не удалось создать заявку: слот только что был занят или заявка уже есть.")


def accept_request(pk, user):
    try:
        with transaction.atomic():
            req = _lock_request(pk)
            if not is_author(req, user):
                raise PermissionDenied("Принять заявку может только автор предложения.")
            if req.status != S.PENDING:
                raise Conflict(f"Заявка уже в статусе «{req.get_status_display()}» — принять её нельзя.")
            if req.slot.offer.status == OfferStatus.HIDDEN:
                raise Conflict("Предложение скрыто модератором.")
            if req.slot.start_at <= timezone.now():
                raise Conflict("Время слота уже наступило.")
            if Request.objects.filter(slot_id=req.slot_id, status__in=BOOKED).exists():
                raise Conflict("Этот слот уже занят принятой заявкой.")
            now = timezone.now()
            req.status = S.ACCEPTED
            req.save(update_fields=["status", "updated_at"])
            # остальные ожидающие заявки на этот слот отклоняются в той же транзакции
            Request.objects.filter(slot_id=req.slot_id, status=S.PENDING).exclude(pk=req.pk).update(
                status=S.REJECTED, updated_at=now
            )
            return req
    except IntegrityError:
        raise Conflict("Этот слот уже занят.")


def reject_request(pk, user):
    with transaction.atomic():
        req = _lock_request(pk)
        if not is_author(req, user):
            raise PermissionDenied("Отклонить заявку может только автор предложения.")
        if req.status != S.PENDING:
            raise Conflict(f"Заявка уже в статусе «{req.get_status_display()}» — отклонить её нельзя.")
        req.status = S.REJECTED
        req.save(update_fields=["status", "updated_at"])
        return req


def cancel_request(pk, user):
    with transaction.atomic():
        req = _lock_request(pk)
        author, student = is_author(req, user), is_student(req, user)
        if not (author or student):
            raise PermissionDenied("Отменить заявку могут только участники встречи.")
        if req.status not in (S.PENDING, S.ACCEPTED):
            raise Conflict(f"Заявка в статусе «{req.get_status_display()}» — отменить её нельзя.")
        if req.status == S.PENDING and not student:
            raise PermissionDenied("Ожидающую заявку автор не отменяет, а отклоняет.")
        if req.slot.start_at <= timezone.now():
            raise Conflict("Отмена возможна только до начала встречи.")
        req.status = S.CANCELLED  # слот освобождается: он больше не «занят» по индексу
        req.save(update_fields=["status", "updated_at"])
        return req


def complete_request(pk, user):
    with transaction.atomic():
        req = _lock_request(pk)
        if not is_author(req, user):
            raise PermissionDenied("Завершить встречу может только автор предложения.")
        if req.status != S.ACCEPTED:
            raise Conflict(f"Заявка в статусе «{req.get_status_display()}» — завершить встречу нельзя.")
        if req.slot.start_at > timezone.now():
            raise Conflict("Встреча ещё не началась — завершить её можно после начала слота.")
        req.status = S.COMPLETED
        req.save(update_fields=["status", "updated_at"])
        return req


def get_chat_request(pk, user):
    req = _get_request(pk)
    if not (is_author(req, user) or is_student(req, user)):
        raise PermissionDenied("Сообщения доступны только участникам встречи.")
    if req.status not in BOOKED:
        raise Conflict("Чат доступен только для принятой или завершённой встречи.")
    return req


def add_message(pk, user, text):
    req = get_chat_request(pk, user)
    return Message.objects.create(request=req, sender=user, text=text)


def add_review(pk, user, score, text=""):
    req = _get_request(pk)
    if not is_student(req, user):
        raise PermissionDenied("Оставить отзыв может только ученик этой встречи.")
    if req.status != S.COMPLETED:
        raise Conflict("Отзыв можно оставить только после завершения встречи.")
    if Review.objects.filter(request=req).exists():
        raise Conflict("Вы уже оставили отзыв на эту встречу.")
    try:
        with transaction.atomic():
            return Review.objects.create(request=req, author=user, score=score, text=text)
    except IntegrityError:
        raise Conflict("Вы уже оставили отзыв на эту встречу.")
