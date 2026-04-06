from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect
from django.views import View

from ta_hub.utils import get_client_ip

from community.models import Community, CommunityReport

import community.views as community_views


DISCORD_REPORT_TIMEOUT_SECONDS = 10
REPORT_DUPLICATE_TTL_SECONDS = 30 * 24 * 60 * 60
REPORT_GLOBAL_LIMIT_PER_IP = 3


def _send_report_webhook(community, report_count):
    """活動停止通報の Discord Webhook を送信する"""
    webhook_url = settings.DISCORD_REPORT_WEBHOOK_URL
    if not webhook_url:
        return

    community_url = f"https://vrc-ta-hub.com/community/{community.pk}/"
    message = {
        "content": (
            f"**集会の活動停止が通報されました**\n"
            f"📢 **{community.name}**\n"
            f"{community_url}\n\n"
            "活動しているかを確認して、リアクションで教えてください\n\n"
            "✅ → まだ開催されている　❌ → 停止している\n\n"
            "💬 詳しい情報があればスレッドで教えてください"
        ),
        "embeds": [{
            "title": community.name,
            "url": community_url,
            "color": 16776960,
            "fields": [
                {"name": "通報数", "value": str(report_count), "inline": True},
            ],
        }],
    }

    try:
        response = community_views.requests.post(
            webhook_url,
            json=message,
            timeout=DISCORD_REPORT_TIMEOUT_SECONDS,
        )
        if response.ok:
            community_views.logger.info(f"通報Webhook送信成功: Community={community.name}")
        else:
            community_views.logger.warning(f"通報Webhook送信失敗: status={response.status_code}")
    except community_views.requests.RequestException:
        community_views.logger.exception("通報Webhook送信で例外が発生")


class CommunityReportView(View):
    """集会の活動停止通報ビュー（匿名OK、POST のみ）"""

    http_method_names = ['post']

    def post(self, request, pk):
        community = get_object_or_404(Community, pk=pk, status='approved')

        ip = get_client_ip(request)

        cache_key = f"community_report:{pk}:{ip}"
        if cache.get(cache_key):
            messages.info(request, 'すでに通報済みです。ご協力ありがとうございます。')
            return redirect('community:detail', pk=pk)

        global_key = f"community_report_global:{ip}"
        global_count = cache.get(global_key, 0)
        if global_count >= REPORT_GLOBAL_LIMIT_PER_IP:
            messages.success(request, '通報を受け付けました。ご協力ありがとうございます。')
            return redirect('community:detail', pk=pk)

        CommunityReport.objects.create(
            community=community,
            ip_address=ip,
        )
        cache.set(cache_key, True, REPORT_DUPLICATE_TTL_SECONDS)
        cache.set(global_key, global_count + 1, REPORT_DUPLICATE_TTL_SECONDS)

        report_count = community.reports.count()
        _send_report_webhook(community, report_count)

        community_views.logger.info(
            f"活動停止通報: Community={community.name}, 通報数={report_count}"
        )

        messages.success(request, '通報を受け付けました。ご協力ありがとうございます。')
        return redirect('community:detail', pk=pk)
