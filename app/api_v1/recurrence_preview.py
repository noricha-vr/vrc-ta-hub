"""定期イベントプレビューAPI"""
from datetime import datetime
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from event.recurrence_service import RecurrenceService


class RecurrencePreviewAPIView(APIView):
    """定期イベントの日付プレビューAPI"""
    permission_classes = [IsAuthenticated]
    
    def post(self, request):
        """定期ルールから日付リストをプレビュー生成"""
        # リクエストデータの取得
        frequency = request.data.get('frequency', 'WEEKLY')
        custom_rule = request.data.get('custom_rule', '')
        base_date_str = request.data.get('base_date')
        base_time_str = request.data.get('base_time', '22:00')
        interval = int(request.data.get('interval', 1))
        week_of_month = request.data.get('week_of_month')
        weekday = request.data.get('weekday')  # 曜日（0=月曜、6=日曜）
        months = int(request.data.get('months', 3))
        community_id = request.data.get('community_id')
        
        # バリデーション
        if not base_date_str:
            return Response({
                'success': False,
                'error': '基準日が指定されていません'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if frequency == 'OTHER' and not custom_rule:
            return Response({
                'success': False,
                'error': 'カスタムルールが指定されていません'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # 日付と時刻のパース
            base_date = datetime.strptime(base_date_str, '%Y-%m-%d').date()
            base_time = datetime.strptime(base_time_str, '%H:%M').time()
            
            # コミュニティを取得（オプション）
            community = None
            if community_id:
                from community.models import Community
                try:
                    community = Community.objects.get(id=community_id)
                except Community.DoesNotExist:
                    pass
            
            # RecurrenceServiceを使用してプレビュー生成
            service = RecurrenceService()
            result = service.preview_dates(
                frequency=frequency,
                custom_rule=custom_rule,
                base_date=base_date,
                base_time=base_time,
                interval=interval,
                week_of_month=week_of_month,
                weekday=weekday,
                months=months,
                community=community
            )
            
            if result['success']:
                return Response(result)
            else:
                # エラーメッセージをより詳細に
                error_msg = result.get('error', '日付の生成に失敗しました')
                if '複雑' in error_msg or '解釈' in error_msg:
                    error_msg += '\n開催周期が複雑な場合は、複数のシンプルなルールに分けて登録してください。'
                
                return Response({
                    'success': False,
                    'error': error_msg,
                    'dates': [],
                    'count': 0
                }, status=status.HTTP_400_BAD_REQUEST)
                
        except ValueError as e:
            return Response({
                'success': False,
                'error': f'日付形式が正しくありません: {str(e)}',
                'dates': [],
                'count': 0
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({
                'success': False,
                'error': f'予期しないエラーが発生しました: {str(e)}',
                'dates': [],
                'count': 0
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)