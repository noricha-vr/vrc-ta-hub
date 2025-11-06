from django.contrib.auth.views import PasswordChangeDoneView, PasswordResetDoneView, PasswordResetCompleteView
from django.urls import path
from django.views.generic import TemplateView

from .views import CustomLoginView, CustomLogoutView, CustomUserCreateView, CustomPasswordChangeView, \
    UserNameChangeView, UserUpdateView, SettingsView, APIKeyListView, APIKeyCreateView, APIKeyDeleteView, \
    CustomPasswordResetView, CustomPasswordResetConfirmView

app_name = 'account'
urlpatterns = [
    path('login/', CustomLoginView.as_view(), name='login'),
    path('logout/', CustomLogoutView.as_view(), name='logout'),
    path('register/', CustomUserCreateView.as_view(), name='register'),
    path('password_change/', CustomPasswordChangeView.as_view(), name='password_change'),
    path('user_name_change/', UserNameChangeView.as_view(), name='user_name_change'),
    path('update/', UserUpdateView.as_view(), name='user_update'),
    path('settings/', SettingsView.as_view(), name='settings'),
    path('api-keys/', APIKeyListView.as_view(), name='api_key_list'),
    path('api-key/create/', APIKeyCreateView.as_view(), name='api_key_create'),
    path('api-key/<int:pk>/delete/', APIKeyDeleteView.as_view(), name='api_key_delete'),

    # パスワードリセット関連
    path('password_reset/', CustomPasswordResetView.as_view(), name='password_reset'),
    path('password_reset/done/', PasswordResetDoneView.as_view(
        template_name='account/password_reset_done.html'
    ), name='password_reset_done'),
    path('password_reset/<uidb64>/<token>/', CustomPasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('password_reset/complete/', PasswordResetCompleteView.as_view(
        template_name='account/password_reset_complete.html'
    ), name='password_reset_complete'),

]
