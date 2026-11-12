from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.signals import post_save
from django.dispatch import receiver

# Политика удаления: историю (предложения, слоты, заявки, сообщения, отзывы, жалобы)
# защищаем PROTECT — данные не исчезают каскадно, предложения архивируются, а не удаляются.
# CASCADE используется только для данных, не имеющих самостоятельной ценности:
# Profile (1:1 с пользователем) и Favorite (личная закладка).


class Format(models.TextChoices):
    ONLINE = "online", "Онлайн"
    OFFLINE = "offline", "Очно"
    BOTH = "both", "Онлайн и очно"


class Level(models.TextChoices):
    BEGINNER = "beginner", "Начальный"
    INTERMEDIATE = "intermediate", "Средний"
    ADVANCED = "advanced", "Продвинутый"


class OfferStatus(models.TextChoices):
    ACTIVE = "active", "Активно"
    ARCHIVED = "archived", "В архиве"
    HIDDEN = "hidden", "Скрыто модератором"


class RequestStatus(models.TextChoices):
    PENDING = "pending", "Ожидает"
    ACCEPTED = "accepted", "Принята"
    REJECTED = "rejected", "Отклонена"
    CANCELLED = "cancelled", "Отменена"
    COMPLETED = "completed", "Завершена"


class ReportStatus(models.TextChoices):
    OPEN = "open", "На рассмотрении"
    RESOLVED = "resolved", "Контент скрыт"
    DISMISSED = "dismissed", "Отклонена"


# «Занимают» слот и «действующие» заявки — используются в частичных уникальных индексах.
BOOKED = [RequestStatus.ACCEPTED.value, RequestStatus.COMPLETED.value]
ACTIVE_REQUEST = [RequestStatus.PENDING.value, *BOOKED]


class User(AbstractUser):
    class Role(models.TextChoices):
        PARTICIPANT = "participant", "Участник"
        MODERATOR = "moderator", "Модератор"
        ADMIN = "admin", "Администратор"

    email = models.EmailField("email", unique=True)
    # Роль меняется только через Django-админку (суперпользователем); API её не принимает.
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.PARTICIPANT)

    @property
    def is_moderator(self):
        return self.is_superuser or self.role in (self.Role.MODERATOR, self.Role.ADMIN)


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    bio = models.TextField(blank=True)
    contact = models.CharField(max_length=200, blank=True)  # видно только подтверждённому партнёру
    format = models.CharField(max_length=8, choices=Format.choices, default=Format.BOTH)
    skills = models.JSONField(default=list, blank=True)


@receiver(post_save, sender=User)
def create_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)


class Category(models.Model):
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Offer(models.Model):
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name="offers")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="offers")
    title = models.CharField(max_length=120)
    description = models.TextField()
    level = models.CharField(max_length=12, choices=Level.choices)
    duration_minutes = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(15), MaxValueValidator(480)]
    )
    format = models.CharField(max_length=8, choices=Format.choices)
    status = models.CharField(max_length=8, choices=OfferStatus.choices, default=OfferStatus.ACTIVE)
    hidden_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            # основной запрос каталога: статус + фильтры, сортировка по дате
            models.Index(fields=["status", "category", "level", "format"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["author", "status"]),
        ]

    def __str__(self):
        return self.title


class Slot(models.Model):
    offer = models.ForeignKey(Offer, on_delete=models.PROTECT, related_name="slots")
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()

    class Meta:
        ordering = ["start_at"]
        constraints = [
            models.CheckConstraint(condition=Q(end_at__gt=F("start_at")), name="slot_end_after_start"),
            models.UniqueConstraint(fields=["offer", "start_at"], name="uniq_slot_start_per_offer"),
        ]
        indexes = [models.Index(fields=["start_at"])]


class Request(models.Model):
    slot = models.ForeignKey(Slot, on_delete=models.PROTECT, related_name="requests")
    student = models.ForeignKey(User, on_delete=models.PROTECT, related_name="requests_sent")
    message = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=RequestStatus.choices, default=RequestStatus.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            # один ученик — не более одной действующей заявки на слот
            models.UniqueConstraint(
                fields=["slot", "student"],
                condition=Q(status__in=ACTIVE_REQUEST),
                name="uniq_active_request_per_student_slot",
            ),
            # у слота не более одной принятой (или уже завершённой) заявки — гарантия на уровне БД
            models.UniqueConstraint(
                fields=["slot"],
                condition=Q(status__in=BOOKED),
                name="uniq_booked_request_per_slot",
            ),
        ]
        indexes = [
            models.Index(fields=["student", "status"]),
            models.Index(fields=["slot", "status"]),
        ]


class Message(models.Model):
    request = models.ForeignKey(Request, on_delete=models.PROTECT, related_name="messages")
    sender = models.ForeignKey(User, on_delete=models.PROTECT, related_name="messages_sent")
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["request", "created_at"])]


class Review(models.Model):
    # OneToOne => «один отзыв на встречу» закреплён уникальностью в БД
    request = models.OneToOneField(Request, on_delete=models.PROTECT, related_name="review")
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name="reviews_written")
    score = models.PositiveSmallIntegerField()
    text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(score__gte=1, score__lte=5), name="review_score_1_5"),
        ]


class Favorite(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="favorites")
    offer = models.ForeignKey(Offer, on_delete=models.CASCADE, related_name="favorited_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "offer"], name="uniq_favorite")]


class Report(models.Model):
    reporter = models.ForeignKey(User, on_delete=models.PROTECT, related_name="reports_made")
    offer = models.ForeignKey(Offer, on_delete=models.PROTECT, related_name="reports")
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=ReportStatus.choices, default=ReportStatus.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    handled_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="reports_handled"
    )
    handled_at = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            # одна открытая жалоба от одного человека на одно предложение
            models.UniqueConstraint(
                fields=["reporter", "offer"],
                condition=Q(status="open"),
                name="uniq_open_report_per_reporter_offer",
            ),
        ]
        indexes = [models.Index(fields=["status", "-created_at"])]
