import logging

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm
from django.contrib.admin.forms import AdminAuthenticationForm
from django.core.exceptions import ValidationError

from allauth.account.models import EmailAddress

from user_account.forms import consume_email_change_rate_limit
from user_account.models import CustomUser, APIKey

logger = logging.getLogger(__name__)


class VerifiedAdminAuthenticationForm(AdminAuthenticationForm):
    """Require a verified primary identity before creating an admin session."""

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not EmailAddress.objects.filter(
            user=user,
            email__iexact=user.email,
            verified=True,
        ).exists():
            raise ValidationError(
                '管理画面へログインする前にメールアドレスの確認を完了してください。',
                code='unverified_email',
            )


class CustomUserChangeForm(UserChangeForm):
    def __init__(self, *args, request=None, **kwargs):
        self.request = request
        super().__init__(*args, **kwargs)
        self.original_email = self.instance.email

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if EmailAddress.objects.filter(email__iexact=email).exclude(user=self.instance).exists():
            raise ValidationError('このメールアドレスは既に登録されています。')
        if (
            self.instance.pk
            and email != self.original_email
            and not consume_email_change_rate_limit(self.request, self.instance)
        ):
            raise ValidationError(
                'メールアドレスの変更回数が上限に達しました。時間をおいて再度お試しください。'
            )
        return email

    def save(self, commit=True):
        """Keep the current login email until the replacement is confirmed."""
        user = super().save(commit=False)
        user.email = self.original_email
        if commit:
            user.save()
            self.save_m2m()
        return user

    class Meta:
        model = CustomUser
        fields = '__all__'


class CustomUserCreationForm(UserCreationForm):
    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if EmailAddress.objects.filter(email__iexact=email).exists():
            raise ValidationError('このメールアドレスは既に登録されています。')
        return email

    class Meta:
        model = CustomUser
        fields = ('email', 'user_name', 'display_name')


# Register your models here.
class CustomUserAdmin(UserAdmin):
    form = CustomUserChangeForm
    add_form = CustomUserCreationForm

    def get_form(self, request, obj=None, **kwargs):
        form_class = super().get_form(request, obj, **kwargs)
        if obj is None:
            return form_class

        class RequestAwareUserChangeForm(form_class):
            def __init__(self, *args, **form_kwargs):
                form_kwargs['request'] = request
                super().__init__(*args, **form_kwargs)

        return RequestAwareUserChangeForm

    def save_model(self, request, obj, form, change):
        requested_email = form.cleaned_data['email'].lower()
        super().save_model(request, obj, form, change)
        if change:
            if requested_email != form.original_email:
                try:
                    EmailAddress.objects.add_new_email(request, obj, requested_email)
                except Exception as exc:
                    logger.error(
                        'Failed to send admin email-change confirmation: '
                        'user_id=%s exception_type=%s',
                        obj.pk,
                        type(exc).__name__,
                        exc_info=True,
                    )
                    self.message_user(
                        request,
                        '確認メールを送信できませんでした。現在のメールアドレスは変更されていません。時間をおいて再度お試しください。',
                        level='WARNING',
                    )
        else:
            # Admin creation is a trusted operator flow, matching
            # ``createsuperuser`` and the one-time legacy migration.
            EmailAddress.objects.create(
                user=obj,
                email=obj.email,
                verified=True,
                primary=True,
            )
    
    list_display = ('email', 'user_name', 'display_name', 'vrchat_user_id', 'is_staff', 'is_active')
    list_filter = ('is_staff', 'is_active')
    search_fields = ('user_name', 'display_name', 'email')
    ordering = ('email',)
    
    fieldsets = (
        (None, {'fields': ('email', 'user_name', 'display_name', 'vrchat_user_id', 'password')}),
        ('権限', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('重要な日付', {'fields': ('last_login', 'date_joined')}),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'user_name', 'display_name', 'password1', 'password2'),
        }),
    )


@admin.register(APIKey)
class APIKeyAdmin(admin.ModelAdmin):
    list_display = ('user', 'name', 'scope', 'expires_at', 'created_at', 'last_used', 'is_active')
    list_filter = ('is_active', 'scope', 'created_at', 'last_used', 'expires_at')
    search_fields = ('user__user_name', 'user__display_name', 'name', 'key')
    readonly_fields = ('key', 'created_at', 'last_used')

    fieldsets = (
        (None, {'fields': ('user', 'name', 'key')}),
        ('ステータス', {'fields': ('is_active', 'scope', 'expires_at')}),
        ('アクセス制御', {'fields': ('allowed_ips',)}),
        ('履歴', {'fields': ('created_at', 'last_used')}),
    )


admin.site.register(CustomUser, CustomUserAdmin)
admin.site.login_form = VerifiedAdminAuthenticationForm
