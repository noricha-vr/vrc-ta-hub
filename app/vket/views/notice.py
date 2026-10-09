from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.templatetags.static import static
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.vary import vary_on_cookie
from django.views import View
from django.views.generic import TemplateView

from allauth.socialaccount.models import SocialAccount

from community.models import CommunityMember
from community.services import activate_community
from website.constants import DEFAULT_NEWS_IMAGE_URL
from ta_hub.access_mixins import AuthenticatedForbiddenMixin

from ..constants import CREATABLE_TARGET_SCOPES, TARGET_SCOPE_LABELS, target_scope_label
from ..models import (
    VketCollaboration,
    VketNotice,
    VketNoticeReceipt,
    VketParticipation,
)
from .helpers import (
    _get_active_membership,
    _is_vket_admin,
)


DEFAULT_NOTICE_OG_IMAGE_URL = DEFAULT_NEWS_IMAGE_URL

# 同じ管理者が同じ内容（タイトル・本文・配信対象）のお知らせをこの秒数以内に作ったら、2件目を作らない（二重送信対策）
NOTICE_DUPLICATE_WINDOW_SECONDS = 10

# 一覧の要約に出す未確認集会名の数（残りは「ほか N 集会」）
UNACKED_NAMES_PREVIEW_LIMIT = 3

ACK_DONE_PARAM = 'done'
# 確認の結果から一覧へ戻した印。この時はメッセージ（残り件数）が見えるよう、開いたお知らせへスクロールしない
ACK_RESULT_PARAM = 'acked'


def _is_unacked(receipt) -> bool:
    return receipt.notice.requires_ack and receipt.acknowledged_at is None


def _acknowledge_receipt(receipt, user) -> bool:
    """receipt を確認済みにする。今回確認した時だけ True（確認済みなら何もしない）"""
    now = timezone.now()
    receipt_fields = {'acknowledged_at': now, 'updated_at': now}
    participation_fields = {'last_acknowledged_at': now, 'updated_at': now}
    # 確認者はログインしている時だけ記録する（未ログインなら前の値を残す）
    if user.is_authenticated:
        receipt_fields['acknowledged_by'] = user
        participation_fields['last_acknowledged_by'] = user

    updated = VketNoticeReceipt.objects.filter(
        pk=receipt.pk, acknowledged_at__isnull=True
    ).update(**receipt_fields)
    if not updated:
        return False

    # 参加レコードのlast_acknowledged_at/byも更新（進捗管理・監査用）
    VketParticipation.objects.filter(pk=receipt.participation_id).update(**participation_fields)
    return True


def _unacked_receipts(participation):
    return VketNoticeReceipt.objects.filter(
        participation=participation,
        notice__requires_ack=True,
        acknowledged_at__isnull=True,
    ).order_by('-created_at')


def _redirect_to_notice_list(collaboration_id: int, open_notice_id: int | None, *, from_ack: bool = False):
    url = reverse('vket:notice_list', kwargs={'pk': collaboration_id})
    query = {}
    if open_notice_id:
        query['open'] = open_notice_id
    if from_ack:
        query[ACK_RESULT_PARAM] = 1
    if query:
        url = f'{url}?{urlencode(query)}'
    return redirect(url)


def _ack_result_redirect(request, receipt, acknowledged: bool):
    """確認後の行き先。一覧へ戻し、確認結果と残り件数を出して次の未確認を開く"""
    remaining = list(_unacked_receipts(receipt.participation_id).values_list('notice_id', flat=True))
    if acknowledged and remaining:
        messages.success(request, f'確認しました。残り {len(remaining)} 件です。')
    elif acknowledged:
        messages.success(request, '確認しました。未確認のお知らせはもうありません。')
    else:
        messages.info(request, 'このお知らせは前に確認済みです。')
    open_notice_id = remaining[0] if remaining else receipt.notice_id
    return _redirect_to_notice_list(
        receipt.participation.collaboration_id, open_notice_id, from_ack=True
    )


def _parse_open_param(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _activate_community_of_open_notice(request, collaboration) -> None:
    """?open= のお知らせが、選択中ではないが自分の所属する集会宛てなら、その集会に切り替える"""
    open_notice_id = _parse_open_param(request.GET.get('open'))
    if open_notice_id is None:
        return
    community_ids = list(
        VketNoticeReceipt.objects.filter(
            notice_id=open_notice_id,
            participation__collaboration=collaboration,
            participation__community__members__user=request.user,
        ).values_list('participation__community_id', flat=True).distinct()
    )
    if not community_ids or request.session.get('active_community_id') in community_ids:
        return
    activate_community(request.session, request.user, community_ids[0])


def _resolve_open_notice(request, receipts, unacked_receipts) -> tuple[int | None, bool]:
    """開いておくお知らせと、そこへスクロールするかを返す

    ?open= が自分に届いたお知らせなら開いてスクロールする（確認の結果から戻った時はスクロールしない）。
    届いていない ID や不正な値なら、最初の未確認を開く。
    """
    requested = _parse_open_param(request.GET.get('open'))
    if requested is not None and requested in {r.notice_id for r in receipts}:
        return requested, request.GET.get(ACK_RESULT_PARAM) != '1'
    if unacked_receipts:
        return unacked_receipts[0].notice_id, False
    return None, False


@method_decorator([never_cache, vary_on_cookie], name='dispatch')
class NoticeListView(LoginRequiredMixin, View):
    """主催者向け: 自分の参加に届いたお知らせ一覧ビュー"""

    template_name = 'vket/notice_list.html'
    public_template_name = 'vket/notice_public.html'

    def dispatch(self, request, *args, **kwargs):
        # 未ログインでもリンクプレビュー用の公開シェルを返すため、LoginRequiredMixin の判定を飛ばす。
        # ログイン誘導は handle_no_permission を _render_public_shell から使う。
        return View.dispatch(self, request, *args, **kwargs)

    def get(self, request, pk: int):
        if not request.user.is_authenticated:
            return self._render_public_shell(request, pk)

        collaborations = VketCollaboration.objects.all()
        if not _is_vket_admin(request.user):
            collaborations = collaborations.exclude(phase=VketCollaboration.Phase.DRAFT)
        collaboration = get_object_or_404(collaborations, pk=pk)
        _activate_community_of_open_notice(request, collaboration)
        community, _membership = _get_active_membership(request)
        receipts = self._load_receipts(collaboration, community)

        unacked_receipts = [r for r in receipts if _is_unacked(r)]
        other_receipts = [r for r in receipts if not _is_unacked(r)]
        open_notice_id, scroll_to_open = _resolve_open_notice(request, receipts, unacked_receipts)
        for receipt in receipts:
            receipt.is_open = receipt.notice_id == open_notice_id

        return render(
            request,
            self.template_name,
            {
                'collaboration': collaboration,
                'receipts': receipts,
                'unacked_receipts': unacked_receipts,
                'other_receipts': other_receipts,
                'open_notice_id': open_notice_id,
                'scroll_to_open': scroll_to_open,
                'community': community,
            },
        )

    @staticmethod
    def _load_receipts(collaboration, community) -> list:
        if community is None:
            return []
        participation = VketParticipation.objects.filter(
            collaboration=collaboration, community=community
        ).first()
        if participation is None:
            return []
        return list(
            VketNoticeReceipt.objects.filter(participation=participation)
            .select_related('notice')
            .order_by('-created_at')
        )

    def _render_public_shell(self, request, pk: int):
        collaboration = (
            VketCollaboration.objects.exclude(phase=VketCollaboration.Phase.DRAFT)
            .filter(pk=pk)
            .first()
        )
        if collaboration is None:
            return self.handle_no_permission()

        notice_settings = collaboration.settings_json
        if not isinstance(notice_settings, dict):
            notice_settings = {}
        image_path = notice_settings.get('notice_og_image')
        if not isinstance(image_path, str):
            image_path = ''
        image_path = image_path.strip()
        og_image_url = DEFAULT_NOTICE_OG_IMAGE_URL
        if image_path.startswith(('http://', 'https://')):
            og_image_url = image_path
        elif image_path:
            static_path = static(image_path)
            og_image_url = static_path
            if not static_path.startswith(('http://', 'https://', '//')):
                if not static_path.startswith('/'):
                    static_path = f'/{static_path}'
                og_image_url = request.build_absolute_uri(static_path)
        description = (
            f'{collaboration.name}の開催準備や発表に関するお知らせを確認できます。'
            'お知らせの内容を見るにはログインが必要です。'
        )

        return render(
            request,
            self.public_template_name,
            {
                'collaboration_name': collaboration.name,
                'meta_description': description,
                'login_url': (
                    f'{settings.LOGIN_URL}?'
                    f'{urlencode({"next": request.get_full_path()})}'
                ),
                'og_image_url': og_image_url,
            },
        )


class NoticeListAckView(LoginRequiredMixin, View):
    """主催者向け: お知らせ一覧から、自分が所属する集会の receipt を確認済みにする（POST のみ）"""

    def post(self, request, pk: int, receipt_id: int):
        receipts = VketNoticeReceipt.objects.select_related('participation__collaboration').filter(
            pk=receipt_id,
            participation__collaboration_id=pk,
            participation__community__members__user=request.user,
            notice__requires_ack=True,
        )
        if not _is_vket_admin(request.user):
            # 下書きのコラボの一覧は管理者以外には 404 なので、確認も受け付けない
            receipts = receipts.exclude(
                participation__collaboration__phase=VketCollaboration.Phase.DRAFT
            )
        receipt = get_object_or_404(receipts.distinct())
        acknowledged = _acknowledge_receipt(receipt, request.user)
        # 戻り先はこの receipt の集会の一覧（選択中の集会が別なら切り替える）
        _can_open_notice_list_for(request, receipt.participation)
        return _ack_result_redirect(request, receipt, acknowledged)


class ManageNoticeListView(LoginRequiredMixin, AuthenticatedForbiddenMixin, TemplateView):
    """運営向け: お知らせ管理一覧ビュー"""

    template_name = 'vket/manage_notice_list.html'

    def test_func(self):
        return _is_vket_admin(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        collaboration = get_object_or_404(VketCollaboration, pk=kwargs['pk'])

        # 各お知らせの配送状況を集計
        notices = list(
            VketNotice.objects.filter(collaboration=collaboration)
            .prefetch_related('receipts__participation__community')
            .order_by('-created_at')
        )
        # prefetch済みのreceiptsをPython側で集計してN+1を回避
        unacked_by_notice = {
            notice.pk: _unacked_communities(notice.receipts.all()) if notice.requires_ack else []
            for notice in notices
        }
        # メンション未設定の集会のDiscord IDは、コラボ全体で1回だけまとめて引く
        discord_ids = _discord_ids_by_community(
            {c.pk for communities in unacked_by_notice.values() for c in communities if _needs_member_mentions(c)}
        )
        notice_stats = [
            self._build_notice_stat(notice, unacked_by_notice[notice.pk], discord_ids)
            for notice in notices
        ]

        context.update(
            {
                'collaboration': collaboration,
                'notice_stats': notice_stats,
                'target_scope_options': [
                    (value, TARGET_SCOPE_LABELS[value]) for value in CREATABLE_TARGET_SCOPES
                ],
            }
        )
        return context

    def _build_notice_stat(self, notice, unacked_communities, discord_ids) -> dict:
        receipts = list(notice.receipts.all())
        total = len(receipts)
        acked = sum(1 for r in receipts if r.acknowledged_at is not None)

        unacked_community_names = [c.name for c in unacked_communities]
        unacked_mentions = [
            mention for c in unacked_communities for mention in _community_mentions(c, discord_ids)
        ]
        remind_text = ''
        if unacked_communities:
            remind_text = _build_remind_text(self.request, notice, unacked_mentions)

        return {
            'notice': notice,
            'total': total,
            'acked': acked,
            'unacked': total - acked,
            # 切り捨てにし、未確認が残っている間は 100% にしない
            'acked_percent': acked * 100 // total if total else 0,
            'scope_label': target_scope_label(notice.target_scope),
            'unacked_mentions': unacked_mentions,
            'unacked_community_names': unacked_community_names,
            'unacked_names_preview': unacked_community_names[:UNACKED_NAMES_PREVIEW_LIMIT],
            'unacked_names_rest': max(len(unacked_community_names) - UNACKED_NAMES_PREVIEW_LIMIT, 0),
            'remind_text': remind_text,
        }


def _unacked_communities(receipts) -> list:
    communities = []
    seen_community_ids = set()
    for r in receipts:
        if r.acknowledged_at is not None:
            continue
        community = r.participation.community
        if community.id in seen_community_ids:
            continue
        seen_community_ids.add(community.id)
        communities.append(community)
    return communities


def _needs_member_mentions(community) -> bool:
    """メンション設定が無く、メンバーのDiscord IDからメンションを作る集会か"""
    mention_type = community.discord_mention_type
    if mention_type == community.DiscordMentionType.ROLE and community.discord_mention_role_id:
        return False
    return mention_type != community.DiscordMentionType.USERS


def _discord_ids_by_community(community_ids: set[int]) -> dict[int, list[str]]:
    """集会ID → メンバーのDiscord ID の一覧（問い合わせは最大2回）"""
    if not community_ids:
        return {}
    members = list(
        CommunityMember.objects.filter(community_id__in=community_ids)
        .values_list('community_id', 'user_id')
    )
    uids_by_user: dict[int, list[str]] = {}
    accounts = SocialAccount.objects.filter(
        user_id__in={user_id for _, user_id in members}, provider='discord'
    ).values_list('user_id', 'uid')
    for user_id, uid in accounts:
        uids_by_user.setdefault(user_id, []).append(uid)

    result: dict[int, list[str]] = {}
    for community_id, user_id in members:
        result.setdefault(community_id, []).extend(uids_by_user.get(user_id, []))
    return result


def _community_mentions(community, discord_ids: dict[int, list[str]]) -> list[str]:
    """集会のDiscordメンション文字列を返す"""
    if not _needs_member_mentions(community):
        if community.discord_mention_type == community.DiscordMentionType.ROLE:
            return [f'<@&{community.discord_mention_role_id}>']
        return [f'<@{uid}>' for uid in community.discord_mention_user_ids]
    # メンション未設定: メンバーのDiscord IDからメンション生成
    return [f'<@{did}>' for did in discord_ids.get(community.pk, [])]


def _build_remind_text(request, notice, mentions: list[str]) -> str:
    """Discord に貼ればそのまま使えるリマインド文（メンション + 【要確認】タイトル + 一覧URL）"""
    path = reverse('vket:notice_list', kwargs={'pk': notice.collaboration_id})
    url = request.build_absolute_uri(f'{path}?open={notice.id}')
    lines = [' '.join(mentions), f'【要確認】{notice.title}', url]
    return '\n'.join(line for line in lines if line)


class ManageNoticeCreateView(LoginRequiredMixin, AuthenticatedForbiddenMixin, View):
    """運営向け: お知らせ作成ビュー"""

    def test_func(self):
        return _is_vket_admin(self.request.user)

    def post(self, request, pk: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        title = request.POST.get('title', '').strip()
        body = request.POST.get('body', '').strip()
        target_scope = request.POST.get('target_scope', VketNotice.TargetScope.ALL_PARTICIPANTS)
        requires_ack = bool(request.POST.get('requires_ack'))

        if not title or not body:
            messages.error(request, 'タイトルと本文は必須です。')
            return redirect('vket:manage_notice_list', pk=pk)

        if target_scope not in CREATABLE_TARGET_SCOPES:
            messages.error(request, '配信対象の指定が正しくありません。')
            return redirect('vket:manage_notice_list', pk=pk)

        with transaction.atomic():
            # 同じコラボへの作成を直列化し、二重送信の判定と作成の間に割り込ませない
            VketCollaboration.objects.select_for_update().filter(pk=collaboration.pk).first()
            content = {'title': title, 'body': body, 'target_scope': target_scope, 'requires_ack': requires_ack}
            if self._recent_duplicate_exists(collaboration, request.user, content):
                messages.info(request, '同じ内容のお知らせを作成したばかりのため、もう1件は作りませんでした。')
                return redirect('vket:manage_notice_list', pk=pk)

            notice = VketNotice.objects.create(
                collaboration=collaboration,
                title=title,
                body=body,
                target_scope=target_scope,
                requires_ack=requires_ack,
                created_by=request.user,
            )
            self._create_receipts(notice, collaboration, target_scope)

        messages.success(request, 'お知らせを作成しました。')
        return redirect('vket:manage_notice_list', pk=pk)

    @staticmethod
    def _recent_duplicate_exists(collaboration, user, content: dict) -> bool:
        """タイトル・本文・配信対象・確認必須がすべて同じお知らせを、直前に作ったか"""
        since = timezone.now() - timedelta(seconds=NOTICE_DUPLICATE_WINDOW_SECONDS)
        return VketNotice.objects.filter(
            collaboration=collaboration,
            created_by=user,
            created_at__gte=since,
            **content,
        ).exists()

    @staticmethod
    def _create_receipts(notice, collaboration, target_scope: str) -> None:
        # 対象参加者にReceiptを自動生成
        participations = VketParticipation.objects.filter(
            collaboration=collaboration,
            lifecycle=VketParticipation.Lifecycle.ACTIVE,
        )
        if target_scope == VketNotice.TargetScope.UNACKED:
            participations = participations.filter(last_acknowledged_at__isnull=True)

        receipts = [
            VketNoticeReceipt(notice=notice, participation=p)
            for p in participations
        ]
        VketNoticeReceipt.objects.bulk_create(receipts)


class ManageNoticeUpdateView(LoginRequiredMixin, AuthenticatedForbiddenMixin, View):
    """運営向け: お知らせ編集ビュー"""

    def test_func(self):
        return _is_vket_admin(self.request.user)

    def post(self, request, pk: int, notice_id: int):
        collaboration = get_object_or_404(VketCollaboration, pk=pk)
        notice = get_object_or_404(VketNotice, pk=notice_id, collaboration=collaboration)

        title = request.POST.get('title', '').strip()
        body = request.POST.get('body', '').strip()

        if not title or not body:
            messages.error(request, 'タイトルと本文は必須です。')
            return redirect('vket:manage_notice_list', pk=pk)

        notice.title = title
        notice.body = body
        notice.save(update_fields=['title', 'body'])

        messages.success(request, 'お知らせを更新しました。')
        return redirect('vket:manage_notice_list', pk=pk)


class AckNoticeView(View):
    """ログイン不要: お知らせ確認（ACK）ビュー

    GET: お知らせ内容を表示し確認ボタンを提示（状態変更なし）。確認不要のお知らせはボタンを出さない
    POST: 確認済みに変更（Discordプレビュー・スキャナによる誤ACK防止）。確認不要のお知らせは何もせず表示へ戻す
    """

    template_name = 'vket/ack_notice.html'

    def get(self, request, ack_token):
        receipt = get_object_or_404(
            VketNoticeReceipt.objects.select_related('notice', 'participation'),
            ack_token=ack_token,
        )
        is_acked = receipt.acknowledged_at is not None
        return render(
            request,
            self.template_name,
            {
                'receipt': receipt,
                'notice': receipt.notice,
                'already_acked': is_acked,
                # 今押して確認した直後かどうか（表示の言い分けだけに使い、状態は変えない）
                'just_acked': is_acked and request.GET.get(ACK_DONE_PARAM) == '1',
                'collaboration_id': receipt.participation.collaboration_id,
            },
        )

    def post(self, request, ack_token):
        receipt = get_object_or_404(
            VketNoticeReceipt.objects.select_related('notice', 'participation__collaboration'),
            ack_token=ack_token,
        )
        if not receipt.notice.requires_ack:
            return redirect('vket:ack_notice', ack_token=receipt.ack_token)
        acknowledged = _acknowledge_receipt(receipt, request.user)

        # その集会のメンバーなら、選択中の集会をそこへ切り替えて一覧へ戻し、残り件数と次の未確認を出す
        if _can_open_notice_list_for(request, receipt.participation):
            return _ack_result_redirect(request, receipt, acknowledged)

        url = reverse('vket:ack_notice', kwargs={'ack_token': receipt.ack_token})
        if acknowledged:
            url = f'{url}?{ACK_DONE_PARAM}=1'
        return redirect(url)


def _can_open_notice_list_for(request, participation) -> bool:
    """確認後にその集会のお知らせ一覧を開けるか。開ける時は選択中の集会をその集会に合わせる"""
    user = request.user
    if not user.is_authenticated:
        return False
    is_draft = participation.collaboration.phase == VketCollaboration.Phase.DRAFT
    if is_draft and not _is_vket_admin(user):
        return False
    community, _membership = _get_active_membership(request)
    if community is not None and community.pk == participation.community_id:
        return True
    return activate_community(request.session, user, participation.community_id).is_accepted
