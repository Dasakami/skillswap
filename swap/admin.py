from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from . import models


@admin.register(models.User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = DjangoUserAdmin.fieldsets + (("SkillSwap", {"fields": ("role",)}),)
    list_display = ("username", "email", "role", "is_active")
    list_filter = ("role", "is_active")


for m in (models.Profile, models.Category, models.Offer, models.Slot, models.Request,
          models.Message, models.Review, models.Favorite, models.Report):
    admin.site.register(m)
