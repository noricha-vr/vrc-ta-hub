from django.urls import path
from .views import CustomLoginView, CustomLogoutView, CustomUserCreateView

app_name = 'account'
urlpatterns = [
    path('login/', CustomLoginView.as_view(), name='login'),
    path('logout/', CustomLogoutView.as_view(), name='logout'),
    path('register/', CustomUserCreateView.as_view(), name='register'),

]
