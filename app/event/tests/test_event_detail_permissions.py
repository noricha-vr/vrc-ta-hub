"""EventDetail（Web UI）の権限テスト."""

from datetime import date, time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from community.models import Community, CommunityMember
from event.models import Event, EventDetail
from event.libs import BlogOutput
from vket.models import VketCollaboration, VketParticipation


User = get_user_model()


class EventDetailPermissionTests(TestCase):
    """EventDetailの作成/更新/削除がコミュニティ管理者に限定されることを確認する."""

    def setUp(self):
        self.client = Client()

        self.owner = User.objects.create_user(
            user_name="owner_user",
            email="owner@example.com",
            password="testpass123",
        )
        self.other_user = User.objects.create_user(
            user_name="other_user",
            email="other@example.com",
            password="testpass123",
        )
        self.applicant = User.objects.create_user(
            user_name="applicant_user",
            email="applicant@example.com",
            password="testpass123",
        )

        self.community = Community.objects.create(
            name="Test Community",
            status="approved",
            frequency="毎週",
            organizers="Test Organizer",
        )
        CommunityMember.objects.create(
            community=self.community,
            user=self.owner,
            role=CommunityMember.Role.OWNER,
        )

        self.event = Event.objects.create(
            community=self.community,
            date=date(2026, 2, 10),
            start_time=time(22, 0),
            duration=60,
            weekday="Tue",
        )
        self.event_detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            start_time=time(22, 0),
            duration=30,
            speaker="Speaker",
            theme="Theme",
            contents="contents",
        )
        self.applicant_detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            start_time=time(22, 30),
            duration=30,
            speaker="Applicant Speaker",
            theme="Applicant Theme",
            contents="before",
            applicant=self.applicant,
            status="approved",
        )
        self.pending_applicant_detail = EventDetail.objects.create(
            event=self.event,
            detail_type="LT",
            start_time=time(23, 0),
            duration=30,
            speaker="Pending Speaker",
            theme="Pending Theme",
            applicant=self.applicant,
            status="pending",
        )
        self.locked_event = Event.objects.create(
            community=self.community,
            date=date(2026, 2, 17),
            start_time=time(22, 0),
            duration=60,
            weekday="Tue",
        )
        self.locked_detail = EventDetail.objects.create(
            event=self.locked_event,
            detail_type="LT",
            start_time=time(22, 0),
            duration=30,
            speaker="Locked Speaker",
            theme="Locked Theme",
            contents="locked contents",
        )
        collaboration = VketCollaboration.objects.create(
            slug="vket-2026-winter",
            name="Vket 2026 Winter",
            phase=VketCollaboration.Phase.LOCKED,
            period_start=date(2026, 2, 1),
            period_end=date(2026, 2, 28),
            registration_deadline=date(2026, 1, 15),
            lt_deadline=date(2026, 1, 31),
        )
        VketParticipation.objects.create(
            collaboration=collaboration,
            community=self.community,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )

    def test_non_member_cannot_access_event_detail_create_view(self):
        """非メンバーはEventDetail作成ページにアクセスできない（403）."""
        self.client.login(username="other_user", password="testpass123")

        url = reverse("event:detail_create", kwargs={"event_pk": self.event.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 403)

    def test_non_member_cannot_access_event_detail_update_view(self):
        """非メンバーはEventDetail更新ページにアクセスするとイベント詳細にリダイレクトされる."""
        self.client.login(username="other_user", password="testpass123")

        url = reverse("event:detail_update", kwargs={"pk": self.event_detail.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        expected_url = reverse("event:detail", kwargs={"pk": self.event_detail.pk})
        self.assertEqual(response.url, expected_url)

    def test_anonymous_user_redirected_to_login_on_update(self):
        """未ログインユーザーはEventDetail更新ページからログインページにリダイレクトされる."""
        url = reverse("event:detail_update", kwargs={"pk": self.event_detail.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/login/", response.url)

    def test_non_member_cannot_delete_event_detail(self):
        """非メンバーはEventDetailを削除できない（403）."""
        self.client.login(username="other_user", password="testpass123")

        url = reverse("event:detail_delete", kwargs={"pk": self.event_detail.pk})
        response = self.client.post(url)

        self.assertEqual(response.status_code, 403)
        self.assertTrue(EventDetail.objects.filter(pk=self.event_detail.pk).exists())

    def test_applicant_can_access_approved_event_detail_update_view(self):
        """発表者本人は自分の承認済みLTの更新画面にアクセスできる."""
        self.client.login(username="applicant_user", password="testpass123")

        url = reverse("event:detail_update", kwargs={"pk": self.applicant_detail.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)

    def test_applicant_cannot_access_pending_event_detail_update_view(self):
        """発表者本人でも承認待ちLTの更新画面にはアクセスできない."""
        self.client.login(username="applicant_user", password="testpass123")

        url = reverse("event:detail_update", kwargs={"pk": self.pending_applicant_detail.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        expected_url = reverse("event:detail", kwargs={"pk": self.pending_applicant_detail.pk})
        self.assertEqual(response.url, expected_url)

    def test_applicant_can_upload_pdf_on_approved_event_detail(self):
        """発表者本人は自分の承認済みLTにPDFをアップロードできる."""
        self.client.login(username="applicant_user", password="testpass123")

        pdf = SimpleUploadedFile(
            "slides.pdf",
            b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF",
            content_type="application/pdf",
        )
        url = reverse("event:detail_update", kwargs={"pk": self.applicant_detail.pk})
        response = self.client.post(
            url,
            {
                "detail_type": "LT",
                "theme": "Updated Theme",
                "speaker": "Applicant Speaker",
                "start_time": "22:30",
                "duration": "30",
                "contents": "updated contents",
                "slide_file": pdf,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.applicant_detail.refresh_from_db()
        self.assertEqual(self.applicant_detail.theme, "Updated Theme")
        self.assertTrue(bool(self.applicant_detail.slide_file))

    @patch("event.views.blog.generate_blog")
    def test_applicant_can_generate_blog_for_approved_event_detail(self, mock_generate_blog):
        """発表者本人は自分の承認済みLTで記事生成できる."""
        self.client.login(username="applicant_user", password="testpass123")
        self.applicant_detail.slide_file = SimpleUploadedFile(
            "slides.pdf",
            b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF",
            content_type="application/pdf",
        )
        self.applicant_detail.save()
        mock_generate_blog.return_value = BlogOutput(
            title="生成タイトル",
            meta_description="生成ディスクリプション",
            text="生成本文",
        )

        url = reverse("event:generate_blog", kwargs={"pk": self.applicant_detail.pk})
        response = self.client.post(url)

        self.assertEqual(response.status_code, 302)
        self.applicant_detail.refresh_from_db()
        self.assertEqual(self.applicant_detail.h1, "生成タイトル")
        self.assertEqual(self.applicant_detail.meta_description, "生成ディスクリプション")
        self.assertEqual(self.applicant_detail.contents, "生成本文")

    @patch("event.views.blog.generate_blog")
    def test_applicant_cannot_generate_blog_for_pending_event_detail(self, mock_generate_blog):
        """発表者本人でも承認待ちLTでは記事生成できない."""
        self.client.login(username="applicant_user", password="testpass123")
        self.pending_applicant_detail.slide_file = SimpleUploadedFile(
            "slides.pdf",
            b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF",
            content_type="application/pdf",
        )
        self.pending_applicant_detail.save()

        url = reverse("event:generate_blog", kwargs={"pk": self.pending_applicant_detail.pk})
        response = self.client.post(url)

        self.assertEqual(response.status_code, 302)
        mock_generate_blog.assert_not_called()

    def test_locked_event_detail_update_view_disables_datetime_fields(self):
        """Vket 期間中の更新画面では日時欄を無効化して案内文を表示する."""
        self.client.login(username="owner_user", password="testpass123")

        url = reverse("event:detail_update", kwargs={"pk": self.locked_detail.pk})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vketコラボ期間中のため運営のみ変更できます。")
        self.assertTrue(response.context["form"].fields["start_time"].disabled)
        self.assertTrue(response.context["form"].fields["duration"].disabled)

    def test_locked_event_detail_rejects_tampered_datetime_post(self):
        """Vket 期間中は改変 POST でも日時変更できない."""
        self.client.login(username="owner_user", password="testpass123")

        url = reverse("event:detail_update", kwargs={"pk": self.locked_detail.pk})
        response = self.client.post(
            url,
            {
                "detail_type": "LT",
                "theme": "Updated Theme",
                "speaker": "Locked Speaker",
                "start_time": "22:30",
                "duration": "45",
                "contents": "updated contents",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vketコラボ期間中のため運営のみ変更できます。")
        self.locked_detail.refresh_from_db()
        self.assertEqual(self.locked_detail.start_time, time(22, 0))
        self.assertEqual(self.locked_detail.duration, 30)
        self.assertEqual(self.locked_detail.theme, "Locked Theme")
