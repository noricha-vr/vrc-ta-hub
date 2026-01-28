from django.urls import path

from event_calendar.views import CalendarEntryUpdateView
from .views import (
    CommunityListView,
    CommunityDetailView,
    CommunityUpdateView,
    CommunityCreateView,
    WaitingCommunityListView,
    AcceptView,
    RejectView,
    CloseCommunityView,
    ReopenCommunityView,
    ArchivedCommunityListView,
    SwitchCommunityView,
    CommunityMemberManageView,
    RemoveStaffView,
    CreateInvitationView,
    RevokeInvitationView,
    AcceptInvitationView,
    CommunitySettingsView,
    CreateOwnershipTransferView,
    AcceptOwnershipTransferView,
    RevokeOwnershipTransferView,
    LTApplicationListView,
    UpdateWebhookView,
    TestWebhookView,
    UpdateLtApplicationView,
)

app_name = 'community'
urlpatterns = [
    path('list/', CommunityListView.as_view(), name='list'),
    path('archive/', ArchivedCommunityListView.as_view(), name='archive_list'),
    path('calendar_update/', CalendarEntryUpdateView.as_view(), name='calendar_update'),
    path('waiting_list/', WaitingCommunityListView.as_view(), name='waiting_list'),
    path('switch/', SwitchCommunityView.as_view(), name='switch'),
    path('settings/', CommunitySettingsView.as_view(), name='settings'),
    path('<int:pk>/', CommunityDetailView.as_view(), name='detail'),
    path('update/', CommunityUpdateView.as_view(), name='update'),
    path('create/', CommunityCreateView.as_view(), name='create'),
    path('accept/<int:pk>/', AcceptView.as_view(), name='accept'),
    path('reject/<int:pk>/', RejectView.as_view(), name='reject'),
    path('close/<int:pk>/', CloseCommunityView.as_view(), name='close'),
    path('reopen/<int:pk>/', ReopenCommunityView.as_view(), name='reopen'),
    path('<int:pk>/members/', CommunityMemberManageView.as_view(), name='member_manage'),
    path('<int:pk>/members/<int:member_id>/remove/', RemoveStaffView.as_view(), name='remove_staff'),
    path('<int:pk>/invite/create/', CreateInvitationView.as_view(), name='create_invitation'),
    path('<int:pk>/invite/<int:invitation_id>/revoke/', RevokeInvitationView.as_view(), name='revoke_invitation'),
    path('invite/<str:token>/', AcceptInvitationView.as_view(), name='accept_invitation'),
    # 主催者引き継ぎ
    path('<int:pk>/transfer/create/', CreateOwnershipTransferView.as_view(), name='create_ownership_transfer'),
    path('transfer/<str:token>/', AcceptOwnershipTransferView.as_view(), name='accept_ownership_transfer'),
    path('<int:pk>/transfer/<int:invitation_id>/revoke/', RevokeOwnershipTransferView.as_view(), name='revoke_ownership_transfer'),
    # LT申請一覧
    path('<int:pk>/applications/', LTApplicationListView.as_view(), name='lt_application_list'),
    # Webhook設定
    path('<int:pk>/webhook/update/', UpdateWebhookView.as_view(), name='update_webhook'),
    path('<int:pk>/webhook/test/', TestWebhookView.as_view(), name='test_webhook'),
    # LT申請受付設定
    path('<int:pk>/lt-application/update/', UpdateLtApplicationView.as_view(), name='update_lt_application'),
]
