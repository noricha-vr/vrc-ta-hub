import logging
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_method
from django.core.management import call_command
from django.utils import timezone
from django.conf import settings
import os

logger = logging.getLogger(__name__)

# 環境変数から認証トークンを取得
REQUEST_TOKEN = os.environ.get('REQUEST_TOKEN', '')


@require_http_method(["GET"])
def generate_llm_events(request):
    """LLMを使用したイベント自動生成エンドポイント"""
    
    # リクエストトークンの検証
    request_token = request.headers.get('Request-Token', '')
    if request_token != REQUEST_TOKEN:
        return HttpResponse("Unauthorized", status=401)
    
    try:
        logger.info('=' * 80)
        logger.info('LLMイベント自動生成処理開始')
        logger.info(f'実行開始時刻: {timezone.now()}')
        logger.info('=' * 80)
        
        # generate_recurring_eventsコマンドを実行
        # デフォルトで1ヶ月先まで生成
        months_ahead = int(request.GET.get('months', 1))
        
        # コマンドの実行
        call_command('generate_recurring_events', months=months_ahead)
        
        logger.info('=' * 80)
        logger.info(f'LLMイベント自動生成処理完了')
        logger.info(f'生成期間: {months_ahead}ヶ月先まで')
        logger.info(f'実行終了時刻: {timezone.now()}')
        logger.info('=' * 80)
        
        return JsonResponse({
            'status': 'success',
            'message': f'LLM event generation completed for {months_ahead} months ahead',
            'timestamp': timezone.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f'LLMイベント自動生成でエラーが発生しました: {str(e)}')
        return JsonResponse({
            'status': 'error',
            'message': str(e),
            'timestamp': timezone.now().isoformat()
        }, status=500)