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
from website.constants import DEFAULT_NEWS_IMAGE_URL
from ta_hub.access_mixins import AuthenticatedForbiddenMixin

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

# 同じ管理者が同じタイトルのお知らせをこの秒数以内に作ったら、2件目を作らない（二重送信対策）
NOTICE_DUPLICATE_WINDOW_SECONDS = 10

# 一覧の要約に出す未確認集会名の数（残りは「ほか N 集会」）
UNACKED_NAMES_PREVIEW_LIMIT = 3

# 配信対象の表示名。モデルの choices を変えると migration が要るため画面側で正確な言葉に差し替える
TARGET_SCOPE_LABELS = {
    VketNotice.TargetScope.ALL_PARTICIPANTS: '全参加者',
    VketNotice.TargetScope.UNACKED: 'まだ一度も確認していない参加者',
    VketNotice.TargetScope.MANUAL: '手動選択',
}

ACK_DONE_PARAM = 'done'


def _is_unacked(receipt) -> bool:
    return receipt.notice.requires_ack and receipt.acknowledged_at is None


def _acknowledge_receipt(receipt, user) -> bool:
    """receipt を確認済みにする。今回確認した時だけ True（確認済みなら何もしない）"""
    now = timezone.now()
    acknowledged_by = user if user.is_authenticated else None
    updated = VketNoticeReceipt.objects.filter(
        pk=receipt.pk, acknowledged_at__isnull=True
    ).update(acknowledged_at=now, acknowledged_by=acknowledged_by, updated_at=now)
    if not updated:
        return False

    # 参加レコードのlast_acknowledged_at/byも更新（進捗管理・監査用）
    VketParticipation.objects.filter(pk=receipt.participation_id).update(
        last_acknowledged_at=now,
        last_acknowledged_by=acknowledged_by,
        updated_at=now,
    )
    return True


def _unacked_receipts(participation):
    return VketNoticeReceipt.objects.filter(
        participation=participation,
        notice__requires_ack=True,
        acknowledged_at__isnull=True,
    ).order_by('-created_at')


def _redirect_to_notice_list(collaboration_id: int, open_notice_id: int | None):
    url = reverse('vket:notice_list', kwargs={'pk': collaboration_id})
    if open_notice_id:
        url = f'{url}?open={open_notice_id}'
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
    return _redirect_to_notice_list(receipt.participation.collaboration_id, open_notice_id)


def _parse_open_param(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
        community, _membership = _get_active_membership(request)
        receipts = self._load_receipts(collaboration, community)

        unacked_receipts = [r for r in receipts if _is_unacked(r)]
        other_receipts = [r for r in receipts if not _is_unacked(r)]
        requested_open_id = _parse_open_param(request.GET.get('open'))
        open_notice_id = requested_open_id
        if open_notice_id is None and unacked_receipts:
            open_notice_id = unacked_receipts[0].notice_id
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
                # 確認直後はメッセージ（残り件数）が見えるよう、スクロールしない
                'scroll_to_open': requested_open_id is not None and not len(messages.get_messages(request)),
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
    """主催者向け: お知らせ一覧から自分の集会の receipt を確認済みにする（POST のみ）"""

    def post(self, request, pk: int, receipt_id: int):
        community, _membership = _get_active_membership(request)
        if community is None:
            return _redirect_to_notice_list(pk, None)

        receipt = get_object_or_404(
            VketNoticeReceipt.objects.select_related('participation'),
            pk=receipt_id,
            participation__collaboration_id=pk,
            participation__community=community,
        )
        acknowledged = _acknowledge_receipt(receipt, request.user)
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
        notices = (
            VketNotice.objects.filter(collaboration=collaboration)
            .prefetch_related('receipts__participation__community')
            .order_by('-created_at')
        )
        notice_stats = [self._build_notice_stat(notice) for notice in notices]

        context.update(
            {
                'collaboration': collaboration,
                'notice_stats': notice_stats,
            }
        )
        return context

    def _build_notice_stat(self, notice) -> dict:
        # prefetch済みのreceiptsをPython側で集計してN+1を回避
        receipts = list(notice.receipts.all())
        total = len(receipts)
        acked = sum(1 for r in receipts if r.acknowledged_at is not None)

        unacked_mentions = []
        unacked_community_names = []
        if notice.requires_ack:
            unacked_communities = _unacked_communities(receipts)
            unacked_community_names = [c.name for c in unacked_communities]
            for community in unacked_communities:
                unacked_mentions.extend(_community_mentions(community))

        return {
            'notice': notice,
            'total': total,
            'acked': acked,
            'unacked': total - acked,
            'acked_percent': round(acked * 100 / total) if total else 0,
            'scope_label': TARGET_SCOPE_LABELS.get(notice.target_scope, notice.get_target_scope_display()),
            'unacked_mentions': unacked_mentions,
            'unacked_community_names': unacked_community_names,
            'unacked_names_preview': unacked_community_names[:UNACKED_NAMES_PREVIEW_LIMIT],
            'unacked_names_rest': max(len(unacked_community_names) - UNACKED_NAMES_PREVIEW_LIMIT, 0),
            'remind_text': _build_remind_text(self.request, notice, unacked_mentions),
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


def _community_mentions(community) -> list[str]:
    """集会のDiscordメンション文字列を返す"""
    mention_type = community.discord_mention_type
    if mention_type == community.DiscordMentionType.ROLE and community.discord_mention_role_id:
        return [f'<@&{community.discord_mention_role_id}>']
    if mention_type == community.DiscordMentionType.USERS:
        return [f'<@{uid}>' for uid in community.discord_mention_user_ids]
    # メンション未設定: メンバーのDiscord IDからメンション生成
    member_user_ids = CommunityMember.objects.filter(
        community=community
    ).values_list('user_id', flat=True)
    discord_ids = SocialAccount.objects.filter(
        user_id__in=member_user_ids, provider='discord'
    ).values_list('uid', flat=True)
    return [f'<@{did}>' for did in discord_ids]


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

        if target_scope not in dict(VketNotice.TargetScope.choices):
            target_scope = VketNotice.TargetScope.ALL_PARTICIPANTS

        with transaction.atomic():
            # 同じコラボへの作成を直列化し、二重送信の判定と作成の間に割り込ませない
            VketCollaboration.objects.select_for_update().filter(pk=collaboration.pk).first()
            if self._recent_duplicate_exists(collaboration, request.user, title):
                messages.info(request, '同じタイトルのお知らせを作成したばかりのため、もう1件は作りませんでした。')
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
    def _recent_duplicate_exists(collaboration, user, title: str) -> bool:
        since = timezone.now() - timedelta(seconds=NOTICE_DUPLICATE_WINDOW_SECONDS)
        return VketNotice.objects.filter(
            collaboration=collaboration,
            created_by=user,
            title=title,
            created_at__gte=since,
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

    GET: お知らせ内容を表示し確認ボタンを提示（状態変更なし）
    POST: 確認済みに変更（Discordプレビュー・スキャナによる誤ACK防止）
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
            VketNoticeReceipt.objects.select_related('participation'),
            ack_token=ack_token,
        )
        acknowledged = _acknowledge_receipt(receipt, request.user)

        # 自分の集会の受信記録なら一覧へ戻し、残り件数と次の未確認を出す
        community, _membership = _get_active_membership(request)
        if community is not None and community.pk == receipt.participation.community_id:
            return _ack_result_redirect(request, receipt, acknowledged)

        url = reverse('vket:ack_notice', kwargs={'ack_token': receipt.ack_token})
        if acknowledged:
            url = f'{url}?{ACK_DONE_PARAM}=1'
        return redirect(url)
