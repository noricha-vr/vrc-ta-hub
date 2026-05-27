import csv
from datetime import date, time
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from community.models import Community
from event.models import Event, EventDetail

User = get_user_model()


class LinkLtSpeakersToUsersCommandTest(TestCase):
    """LT speaker と CustomUser の紐づけコマンドを検証する."""

    def setUp(self):
        self.community = Community.objects.create(name="Test Community")
        self.event = Event.objects.create(
            community=self.community,
            date=date(2025, 1, 1),
            start_time=time(22, 0),
            duration=60,
            weekday="Wed",
        )

    def test_dry_run_does_not_update_applicant(self):
        """dry-runでは一致してもDBを変更しない."""
        user = User.objects.create_user(user_name="ExactSpeaker", email="exact@example.com", password="pw")
        detail = self._create_detail(speaker="ExactSpeaker")

        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "result.csv"
            stdout = StringIO()

            call_command("link_lt_speakers_to_users", "--output", str(output), stdout=stdout)

            detail.refresh_from_db()
            self.assertIsNone(detail.applicant)
            rows = self._read_rows(output)
            self.assertEqual(rows[0]["candidateUserId"], str(user.id))
            self.assertEqual(rows[0]["tier"], "tier1")
            self.assertIn("mode=dry-run", stdout.getvalue())

    def test_commit_updates_exact_match(self):
        """--commit指定時は完全一致した applicant を設定する."""
        user = User.objects.create_user(user_name="ExactSpeaker", email="exact@example.com", password="pw")
        detail = self._create_detail(speaker="ExactSpeaker")

        with TemporaryDirectory() as tmpdir:
            call_command(
                "link_lt_speakers_to_users",
                "--commit",
                "--output",
                str(Path(tmpdir) / "result.csv"),
                stdout=StringIO(),
            )

        detail.refresh_from_db()
        self.assertEqual(detail.applicant, user)

    def test_commit_updates_case_insensitive_match(self):
        """大文字小文字だけが違う場合はTier 2として紐づける."""
        user = User.objects.create_user(user_name="CaseSpeaker", email="case@example.com", password="pw")
        detail = self._create_detail(speaker="casespeaker")

        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "result.csv"
            call_command("link_lt_speakers_to_users", "--commit", "--output", str(output), stdout=StringIO())

            detail.refresh_from_db()
            self.assertEqual(detail.applicant, user)
            self.assertEqual(self._read_rows(output)[0]["tier"], "tier2")

    def test_commit_updates_trimmed_match(self):
        """前後空白を除くと一致する場合はTier 3として紐づける."""
        user = User.objects.create_user(user_name="TrimSpeaker", email="trim@example.com", password="pw")
        detail = self._create_detail(speaker="  TrimSpeaker  ")

        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "result.csv"
            call_command("link_lt_speakers_to_users", "--commit", "--output", str(output), stdout=StringIO())

            detail.refresh_from_db()
            self.assertEqual(detail.applicant, user)
            self.assertEqual(self._read_rows(output)[0]["tier"], "tier3")

    def test_existing_applicant_is_not_overwritten(self):
        """applicant設定済みのレコードは上書きしない."""
        existing = User.objects.create_user(user_name="ExistingUser", email="existing@example.com", password="pw")
        User.objects.create_user(user_name="ExactSpeaker", email="exact@example.com", password="pw")
        detail = self._create_detail(speaker="ExactSpeaker", applicant=existing)

        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "result.csv"
            call_command("link_lt_speakers_to_users", "--commit", "--output", str(output), stdout=StringIO())

            detail.refresh_from_db()
            self.assertEqual(detail.applicant, existing)
            self.assertEqual(self._read_rows(output)[0]["reason"], "applicant already set")

    def test_non_lt_detail_is_not_targeted(self):
        """LT以外のEventDetailは処理対象にしない."""
        User.objects.create_user(user_name="BlogSpeaker", email="blog@example.com", password="pw")
        detail = self._create_detail(speaker="BlogSpeaker", detail_type="BLOG")

        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "result.csv"
            call_command("link_lt_speakers_to_users", "--commit", "--output", str(output), stdout=StringIO())

            detail.refresh_from_db()
            self.assertIsNone(detail.applicant)
            self.assertEqual(self._read_rows(output), [])

    def test_future_lt_detail_is_not_targeted(self):
        """既存データ補完なので未来のLTは処理対象にしない."""
        User.objects.create_user(user_name="FutureSpeaker", email="future@example.com", password="pw")
        future_event = Event.objects.create(
            community=self.community,
            date=date(2099, 1, 1),
            start_time=time(22, 0),
            duration=60,
            weekday="Thu",
        )
        detail = EventDetail.objects.create(
            event=future_event,
            detail_type="LT",
            status="approved",
            theme="Future Theme",
            speaker="FutureSpeaker",
        )

        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "result.csv"
            call_command("link_lt_speakers_to_users", "--commit", "--output", str(output), stdout=StringIO())

            detail.refresh_from_db()
            self.assertIsNone(detail.applicant)
            self.assertEqual(self._read_rows(output), [])

    def test_empty_speaker_and_no_match_are_skipped(self):
        """空speakerと候補なしはskipし、理由をCSVに残す."""
        empty_detail = self._create_detail(speaker="")
        no_match_detail = self._create_detail(speaker="Nobody")

        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "result.csv"
            call_command("link_lt_speakers_to_users", "--commit", "--output", str(output), stdout=StringIO())

            empty_detail.refresh_from_db()
            no_match_detail.refresh_from_db()
            self.assertIsNone(empty_detail.applicant)
            self.assertIsNone(no_match_detail.applicant)
            reasons = {row["eventDetailId"]: row["reason"] for row in self._read_rows(output)}
            self.assertEqual(reasons[str(empty_detail.id)], "speaker is empty")
            self.assertEqual(reasons[str(no_match_detail.id)], "no matching user")

    def test_fuzzy_match_is_skipped_without_interactive(self):
        """曖昧一致候補は通常実行では更新しない."""
        User.objects.create_user(user_name="SpeakerOne", email="speaker@example.com", password="pw")
        detail = self._create_detail(speaker="SpeakerOme")

        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "result.csv"
            call_command("link_lt_speakers_to_users", "--commit", "--output", str(output), stdout=StringIO())

            detail.refresh_from_db()
            row = self._read_rows(output)[0]
            self.assertIsNone(detail.applicant)
            self.assertEqual(row["tier"], "tier4")
            self.assertEqual(row["action"], "skip")

    def test_interactive_without_commit_is_rejected(self):
        """--interactive単独指定は誤操作防止のため拒否する."""
        with self.assertRaises(CommandError):
            call_command("link_lt_speakers_to_users", "--interactive", stdout=StringIO())

    def test_ids_and_limit_restrict_targets(self):
        """--ids と --limit で対象を絞り込める."""
        target_user = User.objects.create_user(user_name="TargetSpeaker", email="target@example.com", password="pw")
        other_user = User.objects.create_user(user_name="OtherSpeaker", email="other@example.com", password="pw")
        target = self._create_detail(speaker="TargetSpeaker")
        other = self._create_detail(speaker="OtherSpeaker")

        with TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "result.csv"
            call_command(
                "link_lt_speakers_to_users",
                "--commit",
                "--ids",
                f"{target.id},{other.id}",
                "--limit",
                "1",
                "--output",
                str(output),
                stdout=StringIO(),
            )

            target.refresh_from_db()
            other.refresh_from_db()
            self.assertEqual(target.applicant, target_user)
            self.assertIsNone(other.applicant)
            self.assertEqual(self._read_rows(output)[0]["candidateUserId"], str(target_user.id))
            self.assertNotEqual(self._read_rows(output)[0]["candidateUserId"], str(other_user.id))

    def _create_detail(self, speaker: str, detail_type: str = "LT", applicant=None) -> EventDetail:
        return EventDetail.objects.create(
            event=self.event,
            detail_type=detail_type,
            status="approved",
            theme=f"Theme {speaker}",
            speaker=speaker,
            applicant=applicant,
        )

    def _read_rows(self, path: Path) -> list[dict[str, str]]:
        with path.open(encoding="utf-8", newline="") as csv_file:
            return list(csv.DictReader(csv_file))
