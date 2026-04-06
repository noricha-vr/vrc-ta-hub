"""通報系ビュー: 集会の活動停止通報."""
import logging

from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect
from django.views import View

from ta_hub.utils import get_client_ip

from ..models import Community, CommunityReport
from .helpers import (
    _send_report_webhook,
    REPORT_DUPLICATE_TTL_SECONDS,
    REPORT_GLOBAL_LIMIT_PER_IP,
)

logger = logging.getLogger(__name__)


class CommunityReportView(View):
    """集会の活動停止通報ビュー（匿名OK、POST のみ）"""

    http_method_names = ['post']

    def post(self, request, pk):
        community = get_object_or_404(Community, pk=pk, status='approved')

        ip = get_client_ip(request)

        # 同一集会の重複チェック
        cache_key = f"community_report:{pk}:{ip}"
        if cache.get(cache_key):
            messages.info(request, 'すでに通報済みです。ご協力ありがとうございます。')
            return redirect('community:detail', pk=pk)

        # 同一IPのグローバル制限チェック（月3件まで）
        global_key = f"community_report_global:{ip}"
        global_count = cache.get(global_key, 0)
        if global_count >= REPORT_GLOBAL_LIMIT_PER_IP:
            # 荒らしに気づかせないよう、成功時と同じメッセージを返す
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

        logger.info(
            f"活動停止通報: Community={community.name}, 通報数={report_count}"
        )

        messages.success(request, '通報を受け付けました。ご協力ありがとうございます。')
        return redirect('community:detail', pk=pk)
