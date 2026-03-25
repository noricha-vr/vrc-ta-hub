from django.urls import path

from . import views

app_name = 'vket'

urlpatterns = [
    path('', views.CollaborationListView.as_view(), name='list'),
    path('status/', views.VketStatusRedirectView.as_view(), name='status_redirect'),
    path('<int:pk>/', views.CollaborationDetailView.as_view(), name='detail'),
    path('<int:pk>/apply/', views.ApplyView.as_view(), name='apply'),
    path('<int:pk>/status/', views.ParticipationStatusView.as_view(), name='status'),
    path('<int:pk>/stage-register/', views.StageRegisterView.as_view(), name='stage_register'),
    path('<int:pk>/notices/', views.NoticeListView.as_view(), name='notice_list'),
    path('<int:pk>/manage/', views.ManageView.as_view(), name='manage'),
    path('<int:pk>/manage/schedule/', views.ManageScheduleView.as_view(), name='manage_schedule'),
    path('<int:pk>/manage/notices/', views.ManageNoticeListView.as_view(), name='manage_notice_list'),
    path('<int:pk>/manage/notices/create/', views.ManageNoticeCreateView.as_view(), name='manage_notice_create'),
    path('<int:pk>/manage/notices/<int:notice_id>/edit/', views.ManageNoticeUpdateView.as_view(), name='manage_notice_update'),
    path('<int:pk>/manage/publish/', views.ManagePublishView.as_view(), name='manage_publish'),
    path(
        '<int:pk>/manage/participations/<int:participation_id>/update/',
        views.ManageParticipationUpdateView.as_view(),
        name='manage_participation_update',
    ),
    path(
        '<int:pk>/presentations/<int:presentation_id>/delete/',
        views.PresentationDeleteView.as_view(),
        name='presentation_delete',
    ),
    path(
        '<int:pk>/manage/presentations/<int:presentation_id>/delete/',
        views.ManagePresentationDeleteView.as_view(),
        name='manage_presentation_delete',
    ),
    path('ack/<uuid:ack_token>/', views.AckNoticeView.as_view(), name='ack_notice'),
]
