from django.urls import path

from . import views

app_name = 'vket'

urlpatterns = [
    path('', views.CollaborationListView.as_view(), name='list'),
    path('<int:pk>/', views.CollaborationDetailView.as_view(), name='detail'),
    path('<int:pk>/apply/', views.ApplyView.as_view(), name='apply'),
    path('<int:pk>/status/', views.ParticipationStatusView.as_view(), name='status'),
    path('<int:pk>/notices/', views.NoticeListView.as_view(), name='notice_list'),
    path('<int:pk>/manage/', views.ManageView.as_view(), name='manage'),
    path('<int:pk>/manage/schedule/', views.ManageScheduleView.as_view(), name='manage_schedule'),
    path('<int:pk>/manage/notices/', views.ManageNoticeListView.as_view(), name='manage_notice_list'),
    path('<int:pk>/manage/notices/create/', views.ManageNoticeCreateView.as_view(), name='manage_notice_create'),
    path('<int:pk>/manage/publish/', views.ManagePublishView.as_view(), name='manage_publish'),
    path(
        '<int:pk>/manage/participations/<int:participation_id>/update/',
        views.ManageParticipationUpdateView.as_view(),
        name='manage_participation_update',
    ),
    path('ack/<uuid:ack_token>/', views.AckNoticeView.as_view(), name='ack_notice'),
]
