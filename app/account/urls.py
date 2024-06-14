from django.contrib.auth.views import PasswordChangeDoneView
from django.urls import path
from django.views.generic import TemplateView

from .views import CustomLoginView, CustomLogoutView, CustomUserCreateView, CustomPasswordChangeView, \
    UserNameChangeView, UserUpdateView, SettingsView

app_name = 'account'
urlpatterns = [
    path('login/', CustomLoginView.as_view(), name='login'),
    path('logout/', CustomLogoutView.as_view(), name='logout'),
    path('register/', CustomUserCreateView.as_view(), name='register'),
    path('password_change/', CustomPasswordChangeView.as_view(), name='password_change'),
    path('user_name_change/', UserNameChangeView.as_view(), name='user_name_change'),
    path('update/', UserUpdateView.as_view(), name='user_update'),
    path('settings/', SettingsView.as_view(), name='settings'),

]
