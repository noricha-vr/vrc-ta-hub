from django.contrib.auth.views import PasswordChangeDoneView
from django.urls import path
from django.views.generic import TemplateView

from .views import CustomLoginView, CustomLogoutView, RegisterView, CustomPasswordChangeView, \
    UserNameChangeView, UserUpdateView, SettingsView, APIKeyListView, APIKeyCreateView, APIKeyDeleteView

app_name = 'account'
urlpatterns = [
    path('login/', CustomLoginView.as_view(), name='login'),
    path('logout/', CustomLogoutView.as_view(), name='logout'),
    path('register/', RegisterView.as_view(), name='register'),
    path('password_change/', CustomPasswordChangeView.as_view(), name='password_change'),
    path('user_name_change/', UserNameChangeView.as_view(), name='user_name_change'),
    path('update/', UserUpdateView.as_view(), name='user_update'),
    path('settings/', SettingsView.as_view(), name='settings'),
    path('api-keys/', APIKeyListView.as_view(), name='api_key_list'),
    path('api-key/create/', APIKeyCreateView.as_view(), name='api_key_create'),
    path('api-key/<int:pk>/delete/', APIKeyDeleteView.as_view(), name='api_key_delete'),

]
