import datetime

from django.core.management.base import BaseCommand
from google.auth import default
from google.cloud import bigquery


class Command(BaseCommand):
    help = 'Sets up BigQuery table for event data and inserts test data'

    def handle(self, *args, **options):
        credentials, project = default()
        client = bigquery.Client(credentials=credentials, project=project, location="asia-northeast1")
        # データセットとテーブル名を直接指定
        dataset_id = "web"
        table_name = "event_blog_generation"
        table_id = f"{project}.{dataset_id}.{table_name}"

        # テーブルのスキーマ定義
        schema = [
            bigquery.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("pk", "INTEGER", mode="REQUIRED"),
            bigquery.SchemaField("video_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("user_id", "INTEGER", mode="REQUIRED"),
            bigquery.SchemaField("transcript", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("prompt", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("response", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("prompt_token_count", "INTEGER", mode="REQUIRED"),
            bigquery.SchemaField("output_token_count", "INTEGER", mode="REQUIRED"),
            bigquery.SchemaField("total_token_count", "INTEGER", mode="REQUIRED"),
        ]

        # テーブルの作成または更新
        table = bigquery.Table(table_id, schema=schema)
        try:
            table = client.create_table(table)
            self.stdout.write(self.style.SUCCESS(f"テーブル {table_id} が作成されました。"))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"テーブルの作成中にエラーが発生しました: {e}"))
            table = client.update_table(table, ["schema"])
            self.stdout.write(self.style.SUCCESS(f"テーブル {table_id} のスキーマが更新されました。"))

        # テストデータの挿入
        rows_to_insert = [
            {
                "timestamp": datetime.datetime.now().isoformat(),
                "pk": 1,
                "video_id": "test_video_id",
                "user_id": 1,
                "transcript": "test transcript",
                "prompt": "test prompt",
                "response": "test response",
                "prompt_token_count": 10,
                "output_token_count": 20,
                "total_token_count": 30
            }
        ]

        errors = client.insert_rows_json(table_id, rows_to_insert)
        if errors == []:
            self.stdout.write(self.style.SUCCESS("テストデータが正常に挿入されました。"))
        else:
            self.stdout.write(self.style.ERROR(f"データ挿入中にエラーが発生しました: {errors}"))
