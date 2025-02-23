from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserChangeForm, UserCreationForm

from account.models import CustomUser


class CustomUserChangeForm(UserChangeForm):
    class Meta:
        model = CustomUser
        fields = '__all__'


class CustomUserCreationForm(UserCreationForm):
    class Meta:
        model = CustomUser
        fields = ('user_name', 'email')


# Register your models here.
class CustomUserAdmin(UserAdmin):
    form = CustomUserChangeForm
    add_form = CustomUserCreationForm
    
    list_display = ('user_name', 'email', 'is_staff', 'is_active')
    list_filter = ('is_staff', 'is_active')
    search_fields = ('user_name', 'email')
    ordering = ('user_name',)
    
    fieldsets = (
        (None, {'fields': ('user_name', 'email', 'password')}),
        ('個人情報', {'fields': ('discord_id',)}),
        ('権限', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('重要な日付', {'fields': ('last_login', 'date_joined')}),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('user_name', 'email', 'password1', 'password2'),
        }),
    )


admin.site.register(CustomUser, CustomUserAdmin)
