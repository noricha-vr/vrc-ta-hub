from datetime import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.views.generic import View

from event.models import EventDetail
from website.settings import GEMINI_MODEL

from .compat import get_generate_blog, get_logger
from .helpers import _get_bigquery_client, can_manage_event_detail


class GenerateBlogView(LoginRequiredMixin, View):
    def post(self, request, pk):
        try:
            event_detail = EventDetail.objects.get(id=pk)

            if not can_manage_event_detail(request.user, event_detail):
                messages.error(
                    request,
                    "Invalid request. You don't have permission to perform this action.",
                )
                return redirect("event:detail", pk=event_detail.id)

            if event_detail.detail_type != "LT":
                messages.error(
                    request,
                    "記事の自動生成はLT（発表）タイプのみ利用可能です。",
                )
                return redirect("event:detail", pk=event_detail.id)

            blog_output = get_generate_blog()(event_detail, model=GEMINI_MODEL)

            if blog_output.title:
                event_detail.h1 = blog_output.title
                event_detail.contents = blog_output.text
                event_detail.meta_description = blog_output.meta_description
                event_detail.save()

                get_logger().info(f"ブログ記事が生成されました。: {event_detail.id}")
                get_logger().info(
                    f"ブログ記事のメタディスクリプション: {event_detail.meta_description}"
                )
                messages.success(request, "ブログ記事が生成されました。")
            else:
                get_logger().warning(
                    f"ブログ記事の生成に失敗しました（空の結果）: {event_detail.id}"
                )
                messages.warning(request, "ブログ記事の生成に失敗しました。")

            return redirect("event:detail", pk=event_detail.id)
        except Exception:
            get_logger().exception("ブログ記事の生成中にエラーが発生しました")
            messages.error(
                request,
                "エラーが発生しました。しばらくしてから再度お試しください。",
            )
            return redirect("event:detail", pk=pk)

    def save_to_bigquery(self, pk, video_id, user_id, transcript, prompt, response):
        client, project = _get_bigquery_client()
        dataset_id = "web"
        table_name = "event_blog_generation"
        table_id = f"{project}.{dataset_id}.{table_name}"

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
                "total_token_count": total_token_count,
            }
        ]

        errors = client.insert_rows_json(table_id, rows_to_insert)

        if errors == []:
            get_logger().info(f"New rows have been added to {table_id}")
        else:
            get_logger().error(f"Encountered errors while inserting rows: {errors}")
