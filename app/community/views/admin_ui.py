"""集会管理画面の表示データ。保存処理・権限判定は既存ビューに置く。"""
from django import forms
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone
from website.discord_delivery import select_community_discord_delivery

from ..constants import DEFAULT_LT_APPLICATION_TEMPLATE, RECORDING_POLICY_FORM_CHOICES
from ..forms import CommunityUpdateForm
from ..models import BotInstallation, CommunityInvitation
from .bot_install import bot_install_enabled


def invitation_display(invitation, request, route='community:accept_invitation'):
    """通常表示ではトークンを出さず、共有する時だけ明示操作で表示する。"""
    form = forms.Form(prefix=f'invitation-{invitation.pk}')
    form.fields['link'] = forms.CharField(required=False, disabled=True)
    invitation.secret_field = form['link']
    invitation.secret_suffix = str(invitation.token)[-4:]
    invitation.shared_link = ''
    if request.GET.get('reveal_invitation') == str(invitation.pk):
        invitation.shared_link = request.build_absolute_uri(reverse(route, args=[invitation.token]))
    return invitation


def settings_display(context, community, request):
    """設定と旧編集 URL で同じまとまり・保存バーを使う。"""
    if 'form' not in context:
        context['form'] = CommunityUpdateForm(instance=community)
    context['is_owner'] = community.is_owner(request.user)
    context['can_manage_bot'] = community.is_manager(request.user)
    context['bot_install_enabled'] = bot_install_enabled()
    context['bot_installations'] = []
    context['bot_installation_history'] = []
    for installation in community.bot_installations.all():
        installation.display_name = installation.guild_name or 'Discord サーバー（名前未登録）'
        installation.confirm_panel_id = f'remove-bot-{installation.pk}'
        # 同じ名前のサーバーも区別できるよう、確認ではサーバー ID も示す。
        target = f'{installation.display_name}（サーバー ID: {installation.guild_id}）'
        installation.remove_confirmation = (
            f'『{target}』との連携を解除しますか？'
            'この集会と Discord サーバーの連携を解除します。'
            'Discord サーバー内の Bot は削除されません。連携の記録は残ります。'
        )
        if installation.status == BotInstallation.Status.REMOVED:
            context['bot_installation_history'].append(installation)
        else:
            context['bot_installations'].append(installation)
    context['bot_installations'].sort(key=lambda item: item.status != BotInstallation.Status.INSTALLED)
    context['lt_application_template_display'] = community.lt_application_template or DEFAULT_LT_APPLICATION_TEMPLATE
    context['recording_policy_choices'] = RECORDING_POLICY_FORM_CHOICES
    context['webhook_has_error'] = any(
        'webhook-error' in message.tags.split() for message in get_messages(request)
    )
    webhook_form = forms.Form(data={} if context['webhook_has_error'] else None)
    webhook_form.fields['notification_webhook_url'] = forms.URLField(required=False)
    context['webhook_field'] = webhook_form['notification_webhook_url']
    context['webhook_suffix'] = community.notification_webhook_url[-4:]
    context['discord_delivery_kind'] = select_community_discord_delivery(community).kind
    if context['webhook_has_error']:
        webhook_form.add_error('notification_webhook_url', 'Discord Webhook URLの形式が正しくありません。')
    context['save_actions'] = [
        {'form_id': 'community-form', 'scope': '集会情報', 'label': '集会情報を保存'},
        {'form_id': 'notification-form', 'scope': '通知', 'label': '通知を保存', 'disabled': True},
        {'form_id': 'lt-form', 'scope': '発表受付', 'label': '発表受付を保存'},
    ]
    if context['is_owner']:
        invitation = CommunityInvitation.objects.filter(
            community=community,
            invitation_type=CommunityInvitation.InvitationType.OWNERSHIP_TRANSFER,
            expires_at__gt=timezone.now(),
        ).first()
        context['ownership_transfer_invitation'] = (
            invitation_display(invitation, request, route='community:accept_ownership_transfer')
            if invitation else None
        )
    return context
