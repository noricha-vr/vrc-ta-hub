from collections.abc import Callable
import csv
import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict

from community.models import CommunityMember
from event.models import EventDetail

ORGANIZER_SPLIT_RE = re.compile(r"[\s,、/／&＆+＋・|｜≺≻<>＜＞（）()]+")
MANUAL_SPEAKER_USER_ALIASES = {
    "さめ": "真名海さめ",
    "さめ(meg-ssk)": "真名海さめ",
    "さめ（meg-ssk)": "真名海さめ",
    "さめ（meg-ssk）": "真名海さめ",
    "さめ（мег-сск）": "真名海さめ",
    "真名海さめ": "真名海さめ",
    "余暇": "friedelcrafts",
    "TAK1123": "株式投資座談会",
    "Tak1123": "株式投資座談会",
    "Kagu3": "ITエンジニア キャリア相談・雑談集会",
    "KAGU3": "ITエンジニア キャリア相談・雑談集会",
}


def normalize_name(value: str) -> str:
    return value.strip().casefold()


def organizer_name_tokens(organizers: str) -> set[str]:
    """Extract comparable names from the free-form community organizers field."""
    normalized_full = normalize_name(organizers)
    tokens = {normalized_full} if normalized_full else set()
    tokens.update(
        token
        for token in (normalize_name(part) for part in ORGANIZER_SPLIT_RE.split(organizers))
        if token
    )
    return tokens


class UserCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    user_name: str


class MatchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    tier: str
    action: str
    reason: str
    candidate: UserCandidate | None = None
    candidates: tuple[UserCandidate, ...] = ()


def levenshtein_distance(left: str, right: str, max_distance: int = 2) -> int:
    """Return the Levenshtein distance, stopping once it exceeds max_distance."""
    if left == right:
        return 0
    if abs(len(left) - len(right)) > max_distance:
        return max_distance + 1

    previous = list(range(len(right) + 1))
    for row_index, left_char in enumerate(left, start=1):
        current = [row_index]
        row_min = current[0]
        for column_index, right_char in enumerate(right, start=1):
            insertion = current[column_index - 1] + 1
            deletion = previous[column_index] + 1
            substitution = previous[column_index - 1] + (left_char != right_char)
            value = min(insertion, deletion, substitution)
            current.append(value)
            row_min = min(row_min, value)
        if row_min > max_distance:
            return max_distance + 1
        previous = current
    return previous[-1]


class SpeakerMatcher:
    """Match saved LT speaker names to existing CustomUser.user_name values."""

    def __init__(self, users: list[UserCandidate]) -> None:
        self.users = users
        self.exact = self._group_by(lambda user: user.user_name)
        self.casefolded = self._group_by(lambda user: user.user_name.casefold())
        self.stripped = self._group_by(lambda user: user.user_name.strip())
        self.normalized = self._group_by(lambda user: normalize_name(user.user_name))

    def match(self, event_detail: EventDetail, interactive: bool) -> MatchResult:
        speaker = event_detail.speaker
        if not speaker:
            return MatchResult(tier="none", action="skip", reason="speaker is empty")

        exact = self.exact.get(speaker, [])
        if len(exact) == 1:
            return MatchResult(tier="tier1", action="match", reason="speaker exactly matches user_name", candidate=exact[0])
        if len(exact) > 1:
            return MatchResult(tier="tier1", action="skip", reason="multiple exact candidates", candidates=tuple(exact))

        casefolded = self.casefolded.get(speaker.casefold(), [])
        if len(casefolded) == 1:
            return MatchResult(
                tier="tier2",
                action="match",
                reason="speaker case-insensitively matches user_name",
                candidate=casefolded[0],
            )
        if len(casefolded) > 1:
            return MatchResult(
                tier="tier2",
                action="skip",
                reason="multiple case-insensitive candidates",
                candidates=tuple(casefolded),
            )

        stripped_speaker = speaker.strip()
        stripped = self.stripped.get(stripped_speaker, [])
        if len(stripped) == 1 and stripped_speaker != speaker:
            return MatchResult(tier="tier3", action="match", reason="trimmed speaker matches user_name", candidate=stripped[0])
        if len(stripped) > 1 and stripped_speaker != speaker:
            return MatchResult(tier="tier3", action="skip", reason="multiple trimmed candidates", candidates=tuple(stripped))

        manual_alias_match = self._match_manual_alias(stripped_speaker)
        if manual_alias_match is not None:
            return manual_alias_match

        organizer_match = self._match_community_organizer(event_detail, stripped_speaker)
        if organizer_match is not None:
            return organizer_match

        fuzzy_candidates = tuple(
            user
            for user in self.users
            if levenshtein_distance(stripped_speaker.casefold(), user.user_name.casefold(), max_distance=2) <= 2
        )
        if fuzzy_candidates:
            return MatchResult(
                tier="tier6",
                action="needs_confirmation" if interactive else "skip",
                reason="fuzzy candidates require interactive confirmation",
                candidates=fuzzy_candidates,
            )

        return MatchResult(tier="none", action="skip", reason="no matching user")

    def _match_manual_alias(self, speaker: str) -> MatchResult | None:
        target_user_name = MANUAL_SPEAKER_USER_ALIASES.get(speaker)
        if target_user_name is None:
            target_user_name = MANUAL_SPEAKER_USER_ALIASES.get(normalize_name(speaker))
        if target_user_name is None:
            return None

        candidates = self.normalized.get(normalize_name(target_user_name), [])
        if len(candidates) == 1:
            return MatchResult(
                tier="tier4",
                action="match",
                reason="speaker matches manual alias mapping",
                candidate=candidates[0],
            )
        if len(candidates) > 1:
            return MatchResult(
                tier="tier4",
                action="skip",
                reason="manual alias mapping produced multiple user candidates",
                candidates=tuple(candidates),
            )
        return MatchResult(
            tier="tier4",
            action="skip",
            reason="manual alias target user does not exist",
        )

    def _match_community_organizer(self, event_detail: EventDetail, speaker: str) -> MatchResult | None:
        community = event_detail.event.community
        if normalize_name(speaker) not in organizer_name_tokens(community.organizers or ""):
            return None

        owner_candidates = tuple(
            UserCandidate(id=member.user_id, user_name=member.user.user_name)
            for member in community.members.all()
            if member.role == CommunityMember.Role.OWNER
        )
        if len(owner_candidates) == 1:
            return MatchResult(
                tier="tier5",
                action="match",
                reason="speaker matches community organizer; candidate is community owner account",
                candidate=owner_candidates[0],
            )
        if len(owner_candidates) > 1:
            return MatchResult(
                tier="tier5",
                action="skip",
                reason="speaker matches community organizer, but multiple owner candidates exist",
                candidates=owner_candidates,
            )
        return MatchResult(
            tier="tier5",
            action="skip",
            reason="speaker matches community organizer, but no owner account exists",
        )

    def _group_by(self, key_func: Callable[[UserCandidate], str]) -> dict[str, list[UserCandidate]]:
        grouped: dict[str, list[UserCandidate]] = {}
        for user in self.users:
            grouped.setdefault(key_func(user), []).append(user)
        return grouped


class Command(BaseCommand):
    """Link historical LT EventDetail speakers to existing CustomUser records."""

    help = "EventDetail.speaker と CustomUser.user_name を照合し、LTの applicant を安全に補完します。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="一致した applicant をDBへ反映します。未指定の場合はdry-runです。",
        )
        parser.add_argument(
            "--interactive",
            action="store_true",
            help="--commit と併用した場合のみ、曖昧一致候補を確認しながら反映します。",
        )
        parser.add_argument(
            "--output",
            default="",
            help="照合結果CSVの出力先を指定します。",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="処理件数を制限します。0は無制限です。",
        )
        parser.add_argument(
            "--ids",
            default="",
            help="対象 EventDetail ID をカンマ区切りで指定します。",
        )

    def handle(self, *args, **options):
        commit = bool(options["commit"])
        interactive = bool(options["interactive"])
        output_path = self._resolve_output_path(options["output"])
        limit = int(options["limit"])
        target_ids = self._parse_ids(options["ids"])

        if interactive and not commit:
            raise CommandError("--interactive は --commit とセットで指定してください。")
        if limit < 0:
            raise CommandError("--limit は0以上を指定してください。")

        User = get_user_model()
        users = [
            UserCandidate(id=user.id, user_name=user.user_name)
            for user in User.objects.only("id", "user_name").order_by("id")
        ]
        matcher = SpeakerMatcher(users)

        queryset = (
            EventDetail.objects.filter(detail_type="LT", event__date__lt=timezone.localdate())
            .select_related("event", "event__community", "applicant")
            .prefetch_related("event__community__members__user")
            .order_by("id")
        )
        if target_ids is not None:
            queryset = queryset.filter(id__in=target_ids)
        if limit > 0:
            queryset = queryset[:limit]

        event_details = list(queryset)
        stats = {
            "matched": 0,
            "updated": 0,
            "skipped": 0,
            "ambiguous": 0,
            "no_match": 0,
        }

        with output_path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=[
                    "eventDetailId",
                    "communityId",
                    "communityName",
                    "communityOrganizers",
                    "speaker",
                    "applicantId",
                    "candidateUserId",
                    "candidateUserName",
                    "tier",
                    "action",
                    "reason",
                    "candidateCount",
                ],
            )
            writer.writeheader()

            for event_detail in event_details:
                result = self._evaluate_event_detail(event_detail, matcher, interactive)
                candidate = result.candidate
                selected_candidate = candidate

                if result.action == "needs_confirmation":
                    selected_candidate = self._prompt_for_candidate(event_detail, result.candidates)
                    if selected_candidate is None:
                        result = MatchResult(
                            tier=result.tier,
                            action="skip",
                            reason="interactive confirmation skipped",
                            candidates=result.candidates,
                        )
                    else:
                        result = MatchResult(
                            tier=result.tier,
                            action="match",
                            reason="interactive confirmation accepted",
                            candidate=selected_candidate,
                            candidates=result.candidates,
                        )

                if result.action == "match":
                    stats["matched"] += 1
                    if commit and selected_candidate is not None:
                        if self._update_applicant(event_detail.id, selected_candidate.id):
                            stats["updated"] += 1
                        else:
                            result = MatchResult(
                                tier=result.tier,
                                action="skip",
                                reason="applicant was already set before update",
                                candidates=result.candidates,
                            )
                            stats["skipped"] += 1
                    elif not commit:
                        stats["skipped"] += 1
                else:
                    stats["skipped"] += 1
                    if result.tier in ("tier4", "tier5", "tier6") and result.action in ("skip", "needs_confirmation"):
                        stats["ambiguous"] += 1
                    if result.reason == "no matching user":
                        stats["no_match"] += 1

                self._write_row(writer, event_detail, result)

        mode = "commit" if commit else "dry-run"
        self.stdout.write(
            self.style.SUCCESS(
                "link_lt_speakers_to_users: "
                f"mode={mode} targets={len(event_details)} matched={stats['matched']} "
                f"updated={stats['updated']} skipped={stats['skipped']} "
                f"ambiguous={stats['ambiguous']} no_match={stats['no_match']} "
                f"output={output_path}"
            )
        )

    def _evaluate_event_detail(
        self,
        event_detail: EventDetail,
        matcher: SpeakerMatcher,
        interactive: bool,
    ) -> MatchResult:
        if event_detail.applicant_id:
            return MatchResult(tier="none", action="skip", reason="applicant already set")
        return matcher.match(event_detail, interactive=interactive)

    def _update_applicant(self, event_detail_id: int, user_id: int) -> bool:
        with transaction.atomic():
            updated = EventDetail.objects.filter(id=event_detail_id, applicant__isnull=True).update(applicant_id=user_id)
        return updated == 1

    def _prompt_for_candidate(
        self,
        event_detail: EventDetail,
        candidates: tuple[UserCandidate, ...],
    ) -> UserCandidate | None:
        self.stdout.write("")
        self.stdout.write(f"EventDetail #{event_detail.id}: speaker={event_detail.speaker!r}")
        for candidate in candidates:
            self.stdout.write(f"  {candidate.id}: {candidate.user_name}")
        answer = input("紐づける user id を入力してください（空でskip）: ").strip()
        if not answer:
            return None
        try:
            selected_id = int(answer)
        except ValueError:
            self.stdout.write(self.style.WARNING("数値ではないためskipします。"))
            return None
        for candidate in candidates:
            if candidate.id == selected_id:
                return candidate
        self.stdout.write(self.style.WARNING("候補にない user id のためskipします。"))
        return None

    def _write_row(self, writer: csv.DictWriter, event_detail: EventDetail, result: MatchResult) -> None:
        candidate = result.candidate
        writer.writerow(
            {
                "eventDetailId": event_detail.id,
                "communityId": event_detail.event.community_id,
                "communityName": event_detail.event.community.name,
                "communityOrganizers": event_detail.event.community.organizers,
                "speaker": event_detail.speaker,
                "applicantId": event_detail.applicant_id or "",
                "candidateUserId": candidate.id if candidate else "",
                "candidateUserName": candidate.user_name if candidate else "",
                "tier": result.tier,
                "action": result.action,
                "reason": result.reason,
                "candidateCount": len(result.candidates) if result.candidates else (1 if candidate else 0),
            }
        )

    def _parse_ids(self, ids_value: str) -> list[int] | None:
        if not ids_value:
            return None
        try:
            return [int(value.strip()) for value in ids_value.split(",") if value.strip()]
        except ValueError as exc:
            raise CommandError("--ids は数値IDのカンマ区切りで指定してください。") from exc

    def _resolve_output_path(self, output_value: str) -> Path:
        if output_value:
            output_path = Path(output_value)
        else:
            timestamp = timezone.localtime(timezone.now()).strftime("%Y%m%d")
            output_path = Path(f"backfill_lt_speakers_{timestamp}.csv")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return output_path
