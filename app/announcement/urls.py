from django.urls import path

from . import views

app_name = 'announcement'

urlpatterns = [
    path('discord/', views.ScheduledMessageListView.as_view(), name='discord_list'),
    path('discord/new/', views.ScheduledMessageCreateView.as_view(), name='discord_create'),
    path('discord/<int:pk>/', views.ScheduledMessageDetailView.as_view(), name='discord_detail'),
    path('discord/<int:pk>/cancel/', views.ScheduledMessageCancelView.as_view(), name='discord_cancel'),
    path('discord/<int:pk>/resend/', views.ScheduledMessageResendView.as_view(), name='discord_resend'),
    # Cloud Scheduler から 1 分ごとに呼ぶ（Request-Token ヘッダーで認証）
    path('discord/send-scheduled/', views.send_scheduled_messages, name='discord_send_scheduled'),
]
