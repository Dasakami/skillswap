from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth.password_validation import validate_password as django_validate_password
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from .models import (Category, Format, Message, Offer, Report, Request, Review,
                     Slot, User)


def display_name(user):
    return user.first_name or user.username


class UserBriefSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "name"]

    def get_name(self, obj):
        return display_name(obj)


# ---------- аутентификация и профиль ----------

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["username", "email", "password", "first_name"]
        extra_kwargs = {"first_name": {"required": True, "allow_blank": False}}

    def validate(self, attrs):
        probe = User(username=attrs.get("username", ""), email=attrs.get("email", ""))
        try:
            django_validate_password(attrs["password"], probe)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})
        return attrs

    def create(self, validated_data):
        # роль не принимается из API: всегда «участник»
        return User.objects.create_user(**validated_data)


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(trim_whitespace=False)

    def validate(self, attrs):
        user = authenticate(username=attrs["username"], password=attrs["password"])
        if not user or not user.is_active:
            raise serializers.ValidationError("Неверный логин или пароль.")
        attrs["user"] = user
        return attrs


class MeSerializer(serializers.ModelSerializer):
    bio = serializers.CharField(source="profile.bio", required=False, allow_blank=True, max_length=2000)
    contact = serializers.CharField(source="profile.contact", required=False, allow_blank=True, max_length=200)
    format = serializers.ChoiceField(source="profile.format", choices=Format.choices, required=False)
    skills = serializers.ListField(
        source="profile.skills", child=serializers.CharField(max_length=50),
        required=False, max_length=20,
    )
    is_moderator = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "email", "role", "is_moderator", "first_name",
                  "bio", "contact", "format", "skills"]
        read_only_fields = ["id", "username", "email", "role"]

    def update(self, instance, validated_data):
        profile_data = validated_data.pop("profile", {})
        for k, v in validated_data.items():
            setattr(instance, k, v)
        instance.save()
        profile = instance.profile
        for k, v in profile_data.items():
            setattr(profile, k, v)
        profile.save()
        return instance


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "slug"]


# ---------- предложения и слоты ----------

class SlotSerializer(serializers.ModelSerializer):
    is_taken = serializers.SerializerMethodField()

    class Meta:
        model = Slot
        fields = ["id", "start_at", "end_at", "is_taken"]

    def get_is_taken(self, obj):
        return getattr(obj, "is_taken", False)

    def validate(self, attrs):
        if attrs["end_at"] <= attrs["start_at"]:
            raise serializers.ValidationError({"end_at": "Окончание должно быть позже начала."})
        if attrs["start_at"] <= timezone.now():
            raise serializers.ValidationError({"start_at": "Слот должен начинаться в будущем."})
        return attrs


class OfferSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()
    category_name = serializers.CharField(source="category.name", read_only=True)
    slots = SlotSerializer(many=True, required=False)
    is_favorite = serializers.SerializerMethodField()
    hidden_reason = serializers.SerializerMethodField()

    class Meta:
        model = Offer
        fields = ["id", "author", "author_name", "category", "category_name", "title",
                  "description", "level", "duration_minutes", "format", "status",
                  "hidden_reason", "slots", "is_favorite", "created_at"]
        read_only_fields = ["id", "author", "status", "created_at"]

    def get_author_name(self, obj):
        return display_name(obj.author)

    def get_is_favorite(self, obj):
        return getattr(obj, "is_favorite", False)

    def get_hidden_reason(self, obj):
        u = self.context["request"].user
        if u.is_authenticated and (u.id == obj.author_id or u.is_moderator):
            return obj.hidden_reason
        return None

    def validate_slots(self, value):
        starts = [s["start_at"] for s in value]
        if len(starts) != len(set(starts)):
            raise serializers.ValidationError("Два слота начинаются в одно и то же время.")
        return value

    def validate(self, attrs):
        if self.instance is not None and "slots" in attrs:
            raise serializers.ValidationError({"slots": "Слоты добавляются отдельным запросом: POST /offers/{id}/slots."})
        return attrs

    def create(self, validated_data):
        slots = validated_data.pop("slots", [])
        with transaction.atomic():
            offer = Offer.objects.create(**validated_data)
            Slot.objects.bulk_create([Slot(offer=offer, **s) for s in slots])
        return offer


# ---------- заявки, сообщения, отзывы ----------

class RequestCreateSerializer(serializers.Serializer):
    message = serializers.CharField(required=False, allow_blank=True, max_length=500, default="")


class RequestSerializer(serializers.ModelSerializer):
    slot = serializers.SerializerMethodField()
    offer = serializers.SerializerMethodField()
    student = UserBriefSerializer(read_only=True)
    has_review = serializers.SerializerMethodField()

    class Meta:
        model = Request
        fields = ["id", "status", "message", "created_at", "slot", "offer", "student", "has_review"]

    def get_slot(self, obj):
        return {"id": obj.slot_id, "start_at": obj.slot.start_at, "end_at": obj.slot.end_at}

    def get_offer(self, obj):
        o = obj.slot.offer
        return {"id": o.id, "title": o.title, "status": o.status,
                "author": o.author_id, "author_name": display_name(o.author)}

    def get_has_review(self, obj):
        return hasattr(obj, "review")


class MessageSerializer(serializers.ModelSerializer):
    sender = UserBriefSerializer(read_only=True)
    text = serializers.CharField(max_length=2000)

    class Meta:
        model = Message
        fields = ["id", "sender", "text", "created_at"]
        read_only_fields = ["id", "created_at"]


class ReviewSerializer(serializers.ModelSerializer):
    score = serializers.IntegerField(min_value=1, max_value=5)
    text = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    author = UserBriefSerializer(read_only=True)
    offer_title = serializers.SerializerMethodField()

    class Meta:
        model = Review
        fields = ["id", "score", "text", "author", "offer_title", "created_at"]
        read_only_fields = ["id", "created_at"]

    def get_offer_title(self, obj):
        return obj.request.slot.offer.title


# ---------- жалобы и модерация ----------

class ReportCreateSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=5, max_length=1000)


class ReportSerializer(serializers.ModelSerializer):
    reporter = UserBriefSerializer(read_only=True)
    offer = serializers.SerializerMethodField()

    class Meta:
        model = Report
        fields = ["id", "reporter", "offer", "reason", "status", "created_at", "resolution"]

    def get_offer(self, obj):
        o = obj.offer
        return {"id": o.id, "title": o.title, "status": o.status, "author": o.author_id}


class ResolveReportSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["hide", "dismiss"])
    reason = serializers.CharField(required=False, allow_blank=True, max_length=1000, default="")

    def validate(self, attrs):
        if attrs["action"] == "hide" and not attrs["reason"].strip():
            raise serializers.ValidationError({"reason": "Укажите причину скрытия."})
        return attrs


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=3, max_length=1000)
