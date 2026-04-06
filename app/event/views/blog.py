import logging
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.views.generic import View

from event.libs import generate_blog
from event.models import EventDetail
from event.views.helpers import can_manage_event_detail
from website.settings import GEMINI_MODEL

logger = logging.getLogger(__name__)

# BigQueryクライアントの遅延初期化
# CI環境でモジュールインポート時にGCP認証エラーが発生するのを防ぐ
_bigquery_client = None
_bigquery_project = None


def _get_bigquery_client():
    """BigQueryクライアントを遅延初期化して返す。

    GCP認証情報が必要になるまで初期化を遅延させることで、
    CI環境などGCP認証情報がない環境でもモジュールをインポートできる。
    """
    global _bigquery_client, _bigquery_project
    if _bigquery_client is None:
        import os
        if os.environ.get('TESTING'):
            from unittest.mock import MagicMock
            _bigquery_project = 'test-project'
            _bigquery_client = MagicMock()
        else:
            from google.auth import default
            from google.cloud import bigquery
            credentials, project = default()
            _bigquery_project = project
            _bigquery_client = bigquery.Client(
                credentials=credentials,
                project=project,
                location="asia-northeast1"
            )
    return _bigquery_client, _bigquery_project


class GenerateBlogView(LoginRequiredMixin, View):
    def post(self, request, pk):
        try:
            event_detail = EventDetail.objects.get(id=pk)

            if not can_manage_event_detail(request.user, event_detail):
                messages.error(request, "Invalid request. You don't have permission to perform this action.")
                return redirect('event:detail', pk=event_detail.id)

            # LTタイプのみ記事生成を許可
            if event_detail.detail_type != 'LT':
                messages.error(request, "記事の自動生成はLT（発表）タイプのみ利用可能です。")
                return redirect('event:detail', pk=event_detail.id)

            # BlogOutputモデルを受け取る
            blog_output = generate_blog(event_detail, model=GEMINI_MODEL)

            # 空でないことを確認
            if blog_output.title:
                # BlogOutputの各フィールドを保存
                event_detail.h1 = blog_output.title
                event_detail.contents = blog_output.text
                event_detail.meta_description = blog_output.meta_description
                event_detail.save()

                logger.info(f"ブログ記事が生成されました。: {event_detail.id}")
                logger.info(f"ブログ記事のメタディスクリプション: {event_detail.meta_description}")

                messages.success(request, "ブログ記事が生成されました。")
            else:
                logger.warning(f"ブログ記事の生成に失敗しました（空の結果）: {event_detail.id}")
                messages.warning(request, "ブログ記事の生成に失敗しました。")

            return redirect('event:detail', pk=event_detail.id)

        except Exception:
            logger.exception("ブログ記事の生成中にエラーが発生しました")
            messages.error(request, "エラーが発生しました。しばらくしてから再度お試しください。")
            return redirect('event:detail', pk=pk)

    def save_to_bigquery(self, pk, video_id, user_id, transcript, prompt, response):
        client, project = _get_bigquery_client()
        dataset_id = "web"
        table_name = "event_blog_generation"
        table_id = f"{project}.{dataset_id}.{table_name}"

        # トークン情報を取得
        usage_metadata = response.usage_metadata
        prompt_token_count = usage_metadata.prompt_token_count
        candidates_token_count = usage_metadata.candidates_token_count
        total_token_count = usage_metadata.total_token_count

        rows_to_insert = [
            {
                "timestamp": datetime.now().isoformat(),
                "pk": pk,
                "video_id": video_id,
                "user_id": user_id,
                "transcript": transcript,
                "prompt": prompt,
                "response": response.text,
                "prompt_token_count": prompt_token_count,
                "output_token_count": candidates_token_count,
                "total_token_count": total_token_count
            }
        ]

        errors = client.insert_rows_json(table_id, rows_to_insert)

        if errors == []:
            logger.info(f"New rows have been added to {table_id}")
        else:
            logger.error(f"Encountered errors while inserting rows: {errors}")
