from django.test import SimpleTestCase

from community import views
from community.views import membership, moderation, notifications, ownership, public, reporting


class CommunityViewExportsTest(SimpleTestCase):
    def test_package_reexports_existing_views(self):
        self.assertIs(views.CommunityListView, public.CommunityListView)
        self.assertIs(views.CommunityDetailView, public.CommunityDetailView)
        self.assertIs(views.ArchivedCommunityListView, public.ArchivedCommunityListView)
        self.assertIs(views.CommunityUpdateView, moderation.CommunityUpdateView)
        self.assertIs(views.CommunityCreateView, moderation.CommunityCreateView)
        self.assertIs(views.WaitingCommunityListView, moderation.WaitingCommunityListView)
        self.assertIs(views.AcceptView, moderation.AcceptView)
        self.assertIs(views.RejectView, moderation.RejectView)
        self.assertIs(views.CloseCommunityView, moderation.CloseCommunityView)
        self.assertIs(views.AdminCommunityCleanupView, moderation.AdminCommunityCleanupView)
        self.assertIs(views.ReopenCommunityView, moderation.ReopenCommunityView)
        self.assertIs(views.SwitchCommunityView, membership.SwitchCommunityView)
        self.assertIs(views.CommunityMemberManageView, membership.CommunityMemberManageView)
        self.assertIs(views.RemoveStaffView, membership.RemoveStaffView)
        self.assertIs(views.CreateInvitationView, membership.CreateInvitationView)
        self.assertIs(views.RevokeInvitationView, membership.RevokeInvitationView)
        self.assertIs(views.AcceptInvitationView, membership.AcceptInvitationView)
        self.assertIs(views.CommunitySettingsView, membership.CommunitySettingsView)
        self.assertIs(views.CreateOwnershipTransferView, ownership.CreateOwnershipTransferView)
        self.assertIs(views.AcceptOwnershipTransferView, ownership.AcceptOwnershipTransferView)
        self.assertIs(views.RevokeOwnershipTransferView, ownership.RevokeOwnershipTransferView)
        self.assertIs(views.UpdateWebhookView, notifications.UpdateWebhookView)
        self.assertIs(views.TestWebhookView, notifications.TestWebhookView)
        self.assertIs(views.LTApplicationListView, notifications.LTApplicationListView)
        self.assertIs(views.UpdateLTSettingsView, notifications.UpdateLTSettingsView)
        self.assertIs(views.CommunityReportView, reporting.CommunityReportView)

    def test_package_keeps_patch_targets(self):
        self.assertIs(views._send_report_webhook, reporting._send_report_webhook)
        self.assertEqual(views.DISCORD_REPORT_TIMEOUT_SECONDS, reporting.DISCORD_REPORT_TIMEOUT_SECONDS)
        self.assertEqual(views.REPORT_DUPLICATE_TTL_SECONDS, reporting.REPORT_DUPLICATE_TTL_SECONDS)
        self.assertEqual(views.REPORT_GLOBAL_LIMIT_PER_IP, reporting.REPORT_GLOBAL_LIMIT_PER_IP)
        self.assertTrue(hasattr(views.requests, 'post'))
        self.assertTrue(callable(views.cleanup_community_future_data))
