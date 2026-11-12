"""Наполнение демонстрационными данными: 6 участников, 4 категории, 12 предложений,
15 слотов, 8 заявок (все статусы). Запускать на чистой базе после migrate."""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from swap.models import (Category, Favorite, Message, Offer, Report, Request,
                         Review, Slot, User)

PASSWORD = "demo12345"

PEOPLE = [("anna", "Анна"), ("boris", "Борис"), ("vera", "Вера"),
          ("gleb", "Глеб"), ("dina", "Дина"), ("egor", "Егор")]
CATEGORIES = [("Python", "python"), ("Дизайн", "design"), ("Английский", "english"), ("Гитара", "guitar")]
# (название, уровень, минут, формат) — по 3 предложения на категорию
OFFERS = [
    ("Основы Python с нуля", "beginner", 60, "online"),
    ("Django и ORM на практике", "intermediate", 90, "both"),
    ("Алгоритмы на Python для собеседований", "advanced", 60, "online"),
    ("Figma для новичков", "beginner", 60, "online"),
    ("Композиция и цвет", "intermediate", 45, "both"),
    ("Типографика в интерфейсах", "advanced", 60, "offline"),
    ("Разговорный английский", "beginner", 45, "online"),
    ("IELTS Speaking: тренировка", "advanced", 60, "online"),
    ("Деловая переписка на английском", "intermediate", 45, "both"),
    ("Первые аккорды за вечер", "beginner", 60, "offline"),
    ("Фингерстайл для начинающих", "intermediate", 60, "offline"),
    ("Импровизация: пентатоника", "advanced", 60, "both"),
]


class Command(BaseCommand):
    help = "Создаёт демо-данные (запускать на чистой БД)."

    @transaction.atomic
    def handle(self, *args, **opts):
        if User.objects.filter(username="admin").exists():
            raise CommandError("Демо-данные уже есть. Для чистого старта: python manage.py flush")
        now = timezone.now()

        admin = User.objects.create_superuser("admin", "admin@example.com", PASSWORD, role=User.Role.ADMIN, first_name="Администратор")
        User.objects.create_user("moderator", "moderator@example.com", PASSWORD, role=User.Role.MODERATOR, first_name="Модератор")
        users = {}
        for i, (username, name) in enumerate(PEOPLE):
            u = User.objects.create_user(username, f"{username}@example.com", PASSWORD, first_name=name)
            p = u.profile
            p.bio = f"Студент, люблю делиться знаниями. Меня зовут {name}."
            p.contact = f"@{username}_demo (Telegram)"
            p.skills = ["Python", "Django"] if i % 2 == 0 else ["Английский", "Гитара"]
            p.format = ["online", "offline", "both"][i % 3]
            p.save()
            users[username] = u
        people = [users[u] for u, _ in PEOPLE]

        cats = [Category.objects.create(name=n, slug=s) for n, s in CATEGORIES]
        offers = []
        for i, (title, level, minutes, fmt) in enumerate(OFFERS):
            offers.append(Offer.objects.create(
                author=people[i % 6], category=cats[i // 3], title=title,
                description=f"{title}. Разберём тему на практике, с примерами и домашним заданием.",
                level=level, duration_minutes=minutes, format=fmt))

        def slot(offer, days, hour=18):
            start = (now + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
            return Slot.objects.create(offer=offer, start_at=start, end_at=start + timedelta(minutes=offer.duration_minutes))

        # 12 будущих слотов (по одному на предложение) + второй слот у предложения 0 + 2 прошедших = 15
        future = [slot(o, i + 2) for i, o in enumerate(offers)]
        future_extra = slot(offers[0], 14, 19)
        past1, past2 = slot(offers[1], -3), slot(offers[2], -5)

        reqs = [
            (past1, users["anna"], "completed", "Хочу разобраться с миграциями."),
            (past2, users["boris"], "completed", "Готовлюсь к собеседованию."),
            (future[0], users["boris"], "accepted", "Привет! Давай начнём с основ."),
            (future_extra, users["vera"], "pending", "Можно в этот день?"),
            (future[3], users["anna"], "pending", "Никогда не работала в Figma."),
            (future[4], users["egor"], "accepted", "Интересует цветовой круг."),
            (future[5], users["dina"], "rejected", "Хочу научиться подбирать шрифты."),
            (future[6], users["gleb"], "cancelled", "К сожалению, не смогу."),
        ]
        made = [Request.objects.create(slot=s, student=st, status=status, message=m) for s, st, status, m in reqs]

        chat = [
            (made[0], [(made[0].student, "Спасибо за встречу!"), (past1.offer.author, "Рад был помочь, удачи!")]),
            (made[2], [(made[2].student, "Привет! Что установить заранее?"), (future[0].offer.author, "Python 3.12 и любой редактор.")]),
            (made[5], [(made[5].student, "Подготовлю пару примеров.")]),
        ]
        for req, msgs in chat:
            for sender, text in msgs:
                Message.objects.create(request=req, sender=sender, text=text)

        Review.objects.create(request=made[0], author=made[0].student, score=5, text="Всё понятно, много практики.")
        # у второй завершённой встречи отзыва нет — можно показать форму отзыва на защите

        for u, o in [(users["anna"], offers[3]), (users["anna"], offers[7]), (users["boris"], offers[4])]:
            Favorite.objects.create(user=u, offer=o)
        Report.objects.create(reporter=users["dina"], offer=offers[9], reason="Описание не соответствует содержанию.")

        self.stdout.write(self.style.SUCCESS(
            f"Готово. Пароль у всех аккаунтов: {PASSWORD}\n"
            "  участники: anna, boris, vera, gleb, dina, egor\n"
            "  модератор: moderator\n  администратор: admin (также доступ в /admin/)"))
