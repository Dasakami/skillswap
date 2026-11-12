from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import Category, Offer, Report, Request, Review, Slot, User

PW = "pass12345"


def iso(dt):
    return dt.isoformat()


class Base(APITestCase):
    def setUp(self):
        self.cat = Category.objects.create(name="Python", slug="python")
        self.cat2 = Category.objects.create(name="Гитара", slug="guitar")
        self.author = User.objects.create_user("author", "a@x.io", PW, first_name="Автор")
        self.student = User.objects.create_user("student", "s@x.io", PW, first_name="Ученик")
        self.student2 = User.objects.create_user("student2", "s2@x.io", PW)
        self.outsider = User.objects.create_user("outsider", "o@x.io", PW)
        self.mod = User.objects.create_user("mod", "m@x.io", PW, role="moderator")
        self.offer = Offer.objects.create(
            author=self.author, category=self.cat, title="Основы Python", description="Разберём основы",
            level="beginner", duration_minutes=60, format="online")
        now = timezone.now()
        self.slot = Slot.objects.create(offer=self.offer, start_at=now + timedelta(days=1),
                                        end_at=now + timedelta(days=1, hours=1))

    def as_(self, user):
        self.client.force_authenticate(user)
        return self.client

    def anon(self):
        self.client.force_authenticate(None)
        return self.client

    def make_request(self, student=None, slot=None, status="pending"):
        return Request.objects.create(slot=slot or self.slot, student=student or self.student, status=status)

    def start_meeting(self, req):
        """Имитирует наступление времени встречи."""
        past = timezone.now() - timedelta(hours=2)
        Slot.objects.filter(pk=req.slot_id).update(start_at=past, end_at=past + timedelta(hours=1))


class FullScenarioTests(Base):
    def test_create_offer_request_accept_chat_complete_review(self):
        # автор создаёт предложение со слотом
        now = timezone.now()
        r = self.as_(self.author).post("/api/offers", {
            "category": self.cat.id, "title": "Django", "description": "ORM и DRF", "level": "intermediate",
            "duration_minutes": 60, "format": "both",
            "slots": [{"start_at": iso(now + timedelta(days=2)), "end_at": iso(now + timedelta(days=2, hours=1))}],
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        slot_id = r.data["slots"][0]["id"]

        # второй аккаунт подаёт заявку
        r = self.as_(self.student).post(f"/api/slots/{slot_id}/requests", {"message": "Привет"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        req_id = r.data["id"]

        # до принятия чата нет
        self.assertEqual(self.as_(self.student).get(f"/api/requests/{req_id}/messages").status_code, 409)

        # автор принимает
        r = self.as_(self.author).post(f"/api/requests/{req_id}/accept")
        self.assertEqual((r.status_code, r.data["status"]), (200, "accepted"))

        # обмен сообщениями
        self.assertEqual(self.as_(self.student).post(f"/api/requests/{req_id}/messages", {"text": "Здравствуйте"}, format="json").status_code, 201)
        self.assertEqual(self.as_(self.author).post(f"/api/requests/{req_id}/messages", {"text": "Добрый день"}, format="json").status_code, 201)
        r = self.as_(self.student).get(f"/api/requests/{req_id}/messages")
        self.assertEqual([m["text"] for m in r.data["results"]], ["Здравствуйте", "Добрый день"])

        # завершить раньше времени нельзя, после начала — можно
        self.assertEqual(self.as_(self.author).post(f"/api/requests/{req_id}/complete").status_code, 409)
        self.start_meeting(Request.objects.get(pk=req_id))
        r = self.as_(self.author).post(f"/api/requests/{req_id}/complete")
        self.assertEqual((r.status_code, r.data["status"]), (200, "completed"))

        # отзыв
        r = self.as_(self.student).post(f"/api/requests/{req_id}/review", {"score": 5, "text": "Супер"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)


class SlotBookingTests(Base):
    def test_cannot_request_own_offer(self):
        r = self.as_(self.author).post(f"/api/slots/{self.slot.id}/requests", {}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_duplicate_active_request_is_conflict(self):
        c = self.as_(self.student)
        self.assertEqual(c.post(f"/api/slots/{self.slot.id}/requests", {}, format="json").status_code, 201)
        r = c.post(f"/api/slots/{self.slot.id}/requests", {}, format="json")
        self.assertEqual(r.status_code, 409)
        self.assertIn("уже", r.data["detail"])

    def test_second_accept_on_taken_slot_is_impossible(self):
        r1, r2 = self.make_request(self.student), self.make_request(self.student2)
        c = self.as_(self.author)
        self.assertEqual(c.post(f"/api/requests/{r1.id}/accept").status_code, 200)
        r2.refresh_from_db()
        self.assertEqual(r2.status, "rejected")  # остальные ожидающие отклонены автоматически
        self.assertEqual(c.post(f"/api/requests/{r2.id}/accept").status_code, 409)

    def test_new_request_on_taken_slot_is_conflict(self):
        self.make_request(self.student, status="accepted")
        r = self.as_(self.student2).post(f"/api/slots/{self.slot.id}/requests", {}, format="json")
        self.assertEqual(r.status_code, 409)

    def test_db_rejects_two_booked_requests_per_slot(self):
        self.make_request(self.student, status="accepted")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_request(self.student2, status="accepted")

    def test_cancel_frees_slot(self):
        req = self.make_request(self.student, status="accepted")
        self.assertEqual(self.as_(self.student).post(f"/api/requests/{req.id}/cancel").status_code, 200)
        self.assertEqual(self.as_(self.student2).post(f"/api/slots/{self.slot.id}/requests", {}, format="json").status_code, 201)

    def test_cannot_cancel_after_start(self):
        req = self.make_request(status="accepted")
        self.start_meeting(req)
        self.assertEqual(self.as_(self.student).post(f"/api/requests/{req.id}/cancel").status_code, 409)

    def test_cannot_request_past_slot(self):
        self.start_meeting(self.make_request(self.student2, status="rejected"))
        self.assertEqual(self.as_(self.student).post(f"/api/slots/{self.slot.id}/requests", {}, format="json").status_code, 409)


class PermissionTests(Base):
    def test_unauthenticated_gets_401(self):
        self.assertEqual(self.anon().post("/api/offers", {}, format="json").status_code, 401)
        self.assertEqual(self.anon().get("/api/requests").status_code, 401)

    def test_guest_can_browse_catalog(self):
        r = self.anon().get("/api/offers")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 1)

    def test_only_author_can_accept_reject_complete(self):
        req = self.make_request()
        for who in (self.student, self.outsider):
            for act in ("accept", "reject", "complete"):
                self.assertEqual(self.as_(who).post(f"/api/requests/{req.id}/{act}").status_code, 403, (who, act))

    def test_outsider_cannot_read_messages_or_send(self):
        req = self.make_request(status="accepted")
        self.assertEqual(self.as_(self.outsider).get(f"/api/requests/{req.id}/messages").status_code, 403)
        self.assertEqual(self.as_(self.outsider).post(f"/api/requests/{req.id}/messages", {"text": "hi"}, format="json").status_code, 403)
        self.assertEqual(self.as_(self.outsider).get(f"/api/requests/{req.id}").status_code, 403)

    def test_contacts_visible_only_to_confirmed_partner(self):
        self.author.profile.contact = "@author_tg"
        self.author.profile.save()
        url = f"/api/users/{self.author.id}"
        self.assertIsNone(self.anon().get(url).data["contact"])
        self.assertIsNone(self.as_(self.outsider).get(url).data["contact"])
        self.make_request(self.student, status="pending")
        self.assertIsNone(self.as_(self.student).get(url).data["contact"])  # заявка ещё не принята
        Request.objects.filter(student=self.student).update(status="accepted")
        self.assertEqual(self.as_(self.student).get(url).data["contact"], "@author_tg")
        self.assertEqual(self.as_(self.author).get(url).data["contact"], "@author_tg")

    def test_role_cannot_be_set_via_api(self):
        r = self.anon().post("/api/auth/register", {"username": "new", "email": "n@x.io", "password": "Str0ng-pass-77",
                                                    "first_name": "Новый", "role": "admin"}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(User.objects.get(username="new").role, "participant")
        self.assertEqual(self.as_(self.student).patch("/api/me", {"role": "admin"}, format="json").status_code, 200)
        self.student.refresh_from_db()
        self.assertEqual(self.student.role, "participant")

    def test_cannot_edit_foreign_offer(self):
        r = self.as_(self.student).patch(f"/api/offers/{self.offer.id}", {"title": "Взлом"}, format="json")
        self.assertEqual(r.status_code, 403)


class StatusTransitionTests(Base):
    def test_illegal_transitions_return_409(self):
        c = self.as_(self.author)
        for status in ("accepted", "rejected", "cancelled", "completed"):
            req = self.make_request(self.student2, status=status)
            Request.objects.filter(pk=req.pk).update(status=status)
            self.assertEqual(c.post(f"/api/requests/{req.id}/accept").status_code, 409, status)
            req.delete()
        pending = self.make_request()
        self.assertEqual(c.post(f"/api/requests/{pending.id}/complete").status_code, 409)

    def test_author_cannot_cancel_pending_but_rejects(self):
        req = self.make_request()
        self.assertEqual(self.as_(self.author).post(f"/api/requests/{req.id}/cancel").status_code, 403)
        self.assertEqual(self.as_(self.author).post(f"/api/requests/{req.id}/reject").status_code, 200)

    def test_missing_objects_404(self):
        self.assertEqual(self.as_(self.author).post("/api/requests/99999/accept").status_code, 404)
        self.assertEqual(self.as_(self.student).post("/api/slots/99999/requests", {}, format="json").status_code, 404)


class ReviewTests(Base):
    def test_review_rules(self):
        req = self.make_request(status="accepted")
        url = f"/api/requests/{req.id}/review"
        body = {"score": 4, "text": "ок"}
        # до завершения нельзя
        self.assertEqual(self.as_(self.student).post(url, body, format="json").status_code, 409)
        Request.objects.filter(pk=req.pk).update(status="completed")
        # автор не может, посторонний не может
        self.assertEqual(self.as_(self.author).post(url, body, format="json").status_code, 403)
        self.assertEqual(self.as_(self.outsider).post(url, body, format="json").status_code, 403)
        # невалидная оценка
        for bad in (0, 6, "x"):
            self.assertEqual(self.as_(self.student).post(url, {"score": bad}, format="json").status_code, 400)
        # ученик — один раз
        self.assertEqual(self.as_(self.student).post(url, body, format="json").status_code, 201)
        r = self.as_(self.student).post(url, body, format="json")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(Review.objects.count(), 1)

    def test_db_rejects_score_out_of_range(self):
        req = self.make_request(status="completed")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Review.objects.create(request=req, author=self.student, score=9)


class OfferTests(Base):
    def test_validation_errors_are_per_field(self):
        r = self.as_(self.author).post("/api/offers", {"title": "", "duration_minutes": 5}, format="json")
        self.assertEqual(r.status_code, 400)
        for f in ("title", "description", "level", "duration_minutes", "category", "format"):
            self.assertIn(f, r.data["errors"])

    def test_slot_in_past_rejected(self):
        past = timezone.now() - timedelta(days=1)
        r = self.as_(self.author).post(f"/api/offers/{self.offer.id}/slots",
                                       {"start_at": iso(past), "end_at": iso(past + timedelta(hours=1))}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_archive_blocks_new_requests_keeps_existing(self):
        existing = self.make_request(self.student)
        self.assertEqual(self.as_(self.author).post(f"/api/offers/{self.offer.id}/archive").status_code, 200)
        self.assertEqual(self.as_(self.student2).post(f"/api/slots/{self.slot.id}/requests", {}, format="json").status_code, 409)
        self.assertEqual(self.as_(self.author).post(f"/api/requests/{existing.id}/accept").status_code, 200)
        existing.refresh_from_db()
        self.assertEqual(existing.status, "accepted")
        self.assertEqual(self.as_(self.student).post(f"/api/requests/{existing.id}/messages", {"text": "всё в силе?"}, format="json").status_code, 201)
        self.assertEqual(self.anon().get("/api/offers").data["count"], 0)  # из каталога пропало
        self.assertEqual(self.as_(self.author).get("/api/offers/mine").data["count"], 1)

    def test_filters_and_search(self):
        Offer.objects.create(author=self.author, category=self.cat2, title="Аккорды", description="гитара с нуля",
                             level="advanced", duration_minutes=45, format="offline")
        c = self.anon()
        self.assertEqual(c.get("/api/offers?q=ккорд").data["count"], 1)
        self.assertEqual(c.get(f"/api/offers?category={self.cat.id}").data["count"], 1)
        self.assertEqual(c.get("/api/offers?level=advanced").data["count"], 1)
        self.assertEqual(c.get("/api/offers?format=online").data["count"], 1)
        self.assertEqual(c.get("/api/offers?format=offline").data["count"], 1)
        self.assertEqual(c.get("/api/offers?page_size=1").data["next"] is not None, True)

    def test_favorites(self):
        c = self.as_(self.student)
        self.assertEqual(c.post(f"/api/offers/{self.offer.id}/favorite").status_code, 201)
        self.assertEqual(c.get("/api/offers/favorites").data["count"], 1)
        self.assertTrue(c.get(f"/api/offers/{self.offer.id}").data["is_favorite"])
        self.assertEqual(self.as_(self.author).post(f"/api/offers/{self.offer.id}/favorite").status_code, 400)  # своё нельзя
        self.assertEqual(self.as_(self.student).delete(f"/api/offers/{self.offer.id}/favorite").status_code, 200)
        self.assertEqual(self.as_(self.student).get("/api/offers/favorites").data["count"], 0)


class ModerationTests(Base):
    def test_report_and_hide_flow(self):
        r = self.as_(self.student).post(f"/api/offers/{self.offer.id}/report", {"reason": "Спам и реклама"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.as_(self.student).post(f"/api/offers/{self.offer.id}/report", {"reason": "Спам и реклама"}, format="json").status_code, 409)
        rid = Report.objects.get().id

        # обычный участник не видит и не обрабатывает жалобы
        self.assertEqual(self.as_(self.student).get("/api/moderation/reports").status_code, 403)
        self.assertEqual(self.as_(self.student).post(f"/api/moderation/reports/{rid}/resolve", {"action": "hide", "reason": "x"}, format="json").status_code, 403)

        # модератор: причина обязательна
        c = self.as_(self.mod)
        self.assertEqual(c.get("/api/moderation/reports").data["count"], 1)
        r = c.post(f"/api/moderation/reports/{rid}/resolve", {"action": "hide"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("reason", r.data["errors"])
        r = c.post(f"/api/moderation/reports/{rid}/resolve", {"action": "hide", "reason": "Нарушает правила"}, format="json")
        self.assertEqual(r.status_code, 200)

        self.offer.refresh_from_db()
        self.assertEqual(self.offer.status, "hidden")
        self.assertEqual(self.anon().get("/api/offers").data["count"], 0)
        self.assertEqual(self.anon().get(f"/api/offers/{self.offer.id}").status_code, 404)
        self.assertEqual(self.as_(self.author).get(f"/api/offers/{self.offer.id}").data["hidden_reason"], "Нарушает правила")
        self.assertEqual(self.as_(self.student).post(f"/api/slots/{self.slot.id}/requests", {}, format="json").status_code, 404)
        self.assertEqual(self.as_(self.mod).post(
            f"/api/moderation/reports/{rid}/resolve",
            {"action": "dismiss", "reason": "Повторная обработка"},
            format="json",
        ).status_code, 409)
