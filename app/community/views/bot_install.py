"""Discord Bot の追加と、集会への紐づけ。"""
import logging
import re
import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.debug import sensitive_variables

from ..models import BotInstallation, Community

logger = logging.getLogger(__name__)
STATE_SESSION_KEY = 'bot_install_state'
STATE_TTL_SECONDS = 600
BOT_PERMISSIONS = (1 << 10) | (1 << 11) | (1 << 16)
GUILD_ID_PATTERN = re.compile(r'[0-9]{17,20}')


def bot_install_enabled():
    return bool(settings.DISCORD_BOT_CLIENT_ID and settings.DISCORD_BOT_CLIENT_SECRET)


class BotInstallStartView(LoginRequiredMixin, View):
    def post(self, request, pk):
        community = get_object_or_404(Community, pk=pk)
        if not community.is_manager(request.user):
            raise PermissionDenied
        if not bot_install_enabled():
            messages.error(request, 'Bot の追加は現在利用できません。')
            return redirect('community:settings')

        state = secrets.token_urlsafe(32)
        redirect_uri = request.build_absolute_uri(reverse('community:bot_install_callback'))
        request.session['active_community_id'] = community.pk
        request.session[STATE_SESSION_KEY] = {
            'state': state,
            'community_id': community.pk,
            'user_id': request.user.pk,
            'expires_at': timezone.now().timestamp() + STATE_TTL_SECONDS,
            'redirect_uri': redirect_uri,
        }
        query = urlencode({
            'client_id': settings.DISCORD_BOT_CLIENT_ID,
            'scope': 'bot applications.commands',
            'permissions': BOT_PERMISSIONS,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'integration_type': 0,
            'state': state,
        })
        return redirect(f'https://discord.com/oauth2/authorize?{query}')


class BotInstallCallbackView(LoginRequiredMixin, View):
    @sensitive_variables()
    def get(self, request):
        saved = request.session.pop(STATE_SESSION_KEY, None)
        # API 交換の失敗やキャンセルでも再利用させない。
        request.session.save()
        state = request.GET.get('state', '')
        if (
            not saved or not state
            or not secrets.compare_digest(state.encode(), saved['state'].encode())
            or saved['user_id'] != request.user.pk
            or saved['expires_at'] <= timezone.now().timestamp()
            or saved['community_id'] != request.session.get('active_community_id')
        ):
            raise PermissionDenied('Bot の追加を最初からやり直してください。')

        community = get_object_or_404(Community, pk=saved['community_id'])
        if not community.is_manager(request.user):
            raise PermissionDenied
        if not bot_install_enabled():
            messages.error(request, 'Bot の追加は現在利用できません。')
            return redirect('community:settings')
        if request.GET.get('error') or not request.GET.get('code'):
            messages.warning(request, 'Bot の追加が完了しませんでした。もう一度お試しください。')
            return redirect('community:settings')

        try:
            response = requests.post(
                'https://discord.com/api/oauth2/token',
                data={
                    'client_id': settings.DISCORD_BOT_CLIENT_ID,
                    'client_secret': settings.DISCORD_BOT_CLIENT_SECRET,
                    'grant_type': 'authorization_code',
                    'code': request.GET['code'],
                    'redirect_uri': saved['redirect_uri'],
                },
                timeout=10,
                allow_redirects=False,
            )
            response.raise_for_status()
            payload = response.json()
            guild = payload.get('guild') if isinstance(payload, dict) else None
            guild_id = guild.get('id') if isinstance(guild, dict) else None
            if response.status_code != 200 or not isinstance(guild_id, str) or not GUILD_ID_PATTERN.fullmatch(guild_id):
                raise ValueError('invalid guild response')
        except (requests.RequestException, ValueError):
            # 例外本文・レスポンスには code やトークンが含まれ得るため記録しない。
            logger.warning('bot_install result=exchange_failed')
            messages.error(request, 'Discord で追加結果を確認できませんでした。もう一度お試しください。')
            return redirect('community:settings')

        try:
            with transaction.atomic():
                Community.objects.select_for_update().get(pk=community.pk)
                if not community.is_manager(request.user):
                    raise PermissionDenied
                existing = BotInstallation.objects.exclude(status=BotInstallation.Status.REMOVED).filter(
                    guild_id=guild_id,
                ).first()
                if existing and existing.community_id != community.pk:
                    raise IntegrityError('guild already linked')
                if not existing:
                    BotInstallation.objects.create(
                        community=community, guild_id=guild_id, added_by=request.user,
                    )
        except IntegrityError:
            logger.info('bot_install result=already_linked')
            messages.error(request, 'この Discord サーバーは既に別の集会に紐づいています。先に紐づけを外してください。')
            return redirect('community:settings')

        logger.info('bot_install result=installed')
        messages.success(request, 'Bot を追加しました。次は Discord で /hub setup を実行してください。')
        return redirect('community:settings')


class BotInstallRemoveView(LoginRequiredMixin, View):
    def post(self, request, pk, installation_id):
        community = get_object_or_404(Community, pk=pk)
        if not community.is_manager(request.user):
            raise PermissionDenied
        installation = get_object_or_404(BotInstallation, pk=installation_id, community=community)
        BotInstallation.objects.filter(pk=installation.pk).exclude(status=BotInstallation.Status.REMOVED).update(
            status=BotInstallation.Status.REMOVED, removed_at=timezone.now(),
        )
        request.session['active_community_id'] = community.pk
        logger.info('bot_install result=removed')
        messages.success(request, 'Bot の紐づけを外しました。Discord から Bot を削除する場合は、サーバーの設定から操作してください。')
        return redirect('community:settings')
