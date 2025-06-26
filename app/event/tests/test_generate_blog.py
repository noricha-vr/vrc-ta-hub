import json
import logging
import os
import tempfile
import unittest
from datetime import datetime

from django.core.files import File
from django.test import TestCase

from account.models import CustomUser
from community.models import Community
from event.libs import generate_blog, get_transcript, BlogOutput
from event.models import Event, EventDetail

logger = logging.getLogger(__name__)


class TestGenerateBlog(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = CustomUser.objects.create_user(
            user_name="test_user",
            email="sample@example.com"
        )
        cls.community = Community.objects.create(
            name="個人開発集会",
            custom_user=cls.user
        )
        cls.event = Event.objects.create(
            date="2024-05-24",
            community=cls.community
        )

        # テスト用のPDFファイルを作成
        cls.test_pdf_content = b"%PDF-1.0\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj 3 0 obj<</Type/Page/MediaBox[0 0 3 3]>>endobj\nxref\n0 4\n0000000000 65535 f\n0000000010 00000 n\n0000000053 00000 n\n0000000102 00000 n\ntrailer<</Size 4/Root 1 0 R>>\nstartxref\n149\n%EOF"

        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
            temp_file.write(cls.test_pdf_content)
            cls.local_file_path = temp_file.name

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if os.path.exists(cls.local_file_path):
            os.unlink(cls.local_file_path)

    def create_event_detail(self, youtube_url=None, slide_file=None):
        event_detail = EventDetail.objects.create(
            theme="Perplexityってどうなのよ？",
            speaker="のりちゃん",
            event=self.event,
            youtube_url=youtube_url
        )
        if slide_file:
            with open(self.local_file_path, 'rb') as file:
                event_detail.slide_file.save('test.pdf', File(file))
        return event_detail

    def _check_environment_variables(self):
        """テストに必要な環境変数が設定されているか確認"""
        # 必要な環境変数のリスト
        required_vars = ['OPENROUTER_API_KEY', 'GOOGLE_API_KEY']
        
        # 現在の時刻をログに記録
        logger.info(f"テスト実行時刻: {datetime.now().isoformat()}")
        
        # 環境変数が設定されているか確認し、ログに記録
        for var in required_vars:
            if not os.environ.get(var):
                logger.warning(f"環境変数 {var} が設定されていません")
            else:
                # APIキー自体は表示せず、設定されていることだけを記録
                logger.info(f"環境変数 {var} は設定されています")

    @unittest.skipIf(not os.environ.get('OPENROUTER_API_KEY'), 'OPENROUTER_API_KEY環境変数が設定されていません')
    def test_generate_blog_video_and_pdf(self):
        # 実際のAPIを使用するため、環境変数が設定されていることを確認
        self._check_environment_variables()
        
        # テスト用のイベント詳細を作成
        event_detail = self.create_event_detail(
            youtube_url="https://www.youtube.com/watch?v=rrKl0s23E0M",
            slide_file=True
        )
        
        # テスト用モデルを指定（軽量モデル）
        test_model = 'google/gemini-2.5-flash-lite-preview-06-17'
        logger.info(f"Using test model: {test_model}")
        
        # 5回ループして安定性を確認
        success_count = 0
        failure_details = []
        
        for i in range(5):
            logger.info(f"\n=== テスト実行 {i + 1}/5 ===")
            
            try:
                result = generate_blog(event_detail, model=test_model)
                
                # 結果の検証
                self.assertIsInstance(result, BlogOutput)
                
                # 各フィールドが正しく生成されているか確認
                if not result.title or len(result.title) == 0:
                    failure_details.append(f"試行 {i + 1}: タイトルが空")
                    logger.error(f"試行 {i + 1}: タイトルが空")
                    continue
                    
                if not result.meta_description or len(result.meta_description) == 0:
                    failure_details.append(f"試行 {i + 1}: メタディスクリプションが空")
                    logger.error(f"試行 {i + 1}: メタディスクリプションが空")
                    continue
                    
                if not result.text or len(result.text) == 0:
                    failure_details.append(f"試行 {i + 1}: 本文が空")
                    logger.error(f"試行 {i + 1}: 本文が空")
                    continue
                
                # 成功した場合の詳細ログ
                logger.info(f"試行 {i + 1}: 成功")
                logger.info(f"  タイトル: {result.title[:50]}..." if len(result.title) > 50 else f"  タイトル: {result.title}")
                logger.info(f"  メタディスクリプション: {len(result.meta_description)} 文字")
                logger.info(f"  本文: {len(result.text)} 文字")
                
                # 文字数制限のチェック
                self.assertLessEqual(len(result.title), 60, f"試行 {i + 1}: タイトルが60文字を超えています")
                self.assertLessEqual(len(result.meta_description), 160, f"試行 {i + 1}: メタディスクリプションが160文字を超えています")
                
                success_count += 1
                
            except Exception as e:
                error_msg = f"試行 {i + 1}: API呼び出しエラー - {str(e)}"
                failure_details.append(error_msg)
                logger.error(error_msg)
                
                # エラーの詳細を記録
                if hasattr(e, '__class__'):
                    logger.error(f"  エラータイプ: {e.__class__.__name__}")
        
        # 結果のサマリー
        logger.info(f"\n=== テスト結果サマリー ===")
        logger.info(f"成功: {success_count}/5")
        logger.info(f"失敗: {5 - success_count}/5")
        
        if failure_details:
            logger.error("\n失敗の詳細:")
            for detail in failure_details:
                logger.error(f"  - {detail}")
        
        # 5回中3回以上成功すれば合格とする
        self.assertGreaterEqual(success_count, 3, 
            f"5回中{success_count}回しか成功しませんでした。安定性に問題があります。\n" +
            "\n".join(failure_details))

    @unittest.skipIf(not os.environ.get('OPENROUTER_API_KEY'), 'OPENROUTER_API_KEY環境変数が設定されていません')
    def test_generate_blog_video_only(self):
        # 実際のAPIを使用するため、環境変数が設定されていることを確認
        self._check_environment_variables()
        
        # テスト用のイベント詳細を作成（動画のみ）
        event_detail = self.create_event_detail(
            youtube_url="https://www.youtube.com/watch?v=rrKl0s23E0M"
        )
        
        # テスト用モデルを指定
        test_model = 'google/gemini-2.5-flash-lite-preview-06-17'
        
        # 5回ループして安定性を確認
        success_count = 0
        failure_details = []
        
        for i in range(5):
            logger.info(f"\n=== 動画のみテスト実行 {i + 1}/5 ===")
            
            try:
                result = generate_blog(event_detail, model=test_model)
                
                # 結果の検証
                self.assertIsInstance(result, BlogOutput)
                
                if result.title and result.meta_description and result.text:
                    success_count += 1
                    logger.info(f"試行 {i + 1}: 成功")
                else:
                    failure_details.append(f"試行 {i + 1}: 一部フィールドが空")
                    
            except Exception as e:
                failure_details.append(f"試行 {i + 1}: {str(e)}")
                logger.error(f"試行 {i + 1}: エラー - {str(e)}")
        
        # 5回中3回以上成功すれば合格
        self.assertGreaterEqual(success_count, 3, 
            f"動画のみテスト: 5回中{success_count}回しか成功しませんでした")

    @unittest.skipIf(not os.environ.get('OPENROUTER_API_KEY'), 'OPENROUTER_API_KEY環境変数が設定されていません')
    def test_generate_blog_pdf_only(self):
        # 実際のAPIを使用するため、環境変数が設定されていることを確認
        self._check_environment_variables()
        
        # テスト用のイベント詳細を作成（PDFのみ）
        event_detail = self.create_event_detail(slide_file=True)
        
        # テスト用モデルを指定
        test_model = 'google/gemini-2.5-flash-lite-preview-06-17'
        
        # 5回ループして安定性を確認
        success_count = 0
        failure_details = []
        
        for i in range(5):
            logger.info(f"\n=== PDFのみテスト実行 {i + 1}/5 ===")
            
            try:
                result = generate_blog(event_detail, model=test_model)
                
                # 結果の検証
                self.assertIsInstance(result, BlogOutput)
                
                if result.title and result.meta_description and result.text:
                    success_count += 1
                    logger.info(f"試行 {i + 1}: 成功")
                else:
                    failure_details.append(f"試行 {i + 1}: 一部フィールドが空")
                    
            except Exception as e:
                failure_details.append(f"試行 {i + 1}: {str(e)}")
                logger.error(f"試行 {i + 1}: エラー - {str(e)}")
        
        # 5回中3回以上成功すれば合格
        self.assertGreaterEqual(success_count, 3, 
            f"PDFのみテスト: 5回中{success_count}回しか成功しませんでした")

    def test_generate_blog_no_video_no_pdf(self):
        # 動画もPDFもない場合
        event_detail = self.create_event_detail()
        result = generate_blog(event_detail)

        self.assertIsInstance(result, BlogOutput)
        self.assertEqual(result.title, '')
        self.assertEqual(result.meta_description, '')
        self.assertEqual(result.text, '')

    def test_blog_output_basic(self):
        """BlogOutputモデルの基本的な動作をテスト"""
        # 正常なケース
        valid_output = BlogOutput(
            title="テストタイトル",
            meta_description="テストのメタ説明",
            text="テスト本文の内容"
        )
        self.assertIsInstance(valid_output, BlogOutput)
        self.assertTrue(valid_output.title)
        self.assertTrue(valid_output.meta_description)
        self.assertTrue(valid_output.text)

    @unittest.skipIf(not os.environ.get('GOOGLE_API_KEY'), 'GOOGLE_API_KEY環境変数が設定されていません')
    def test_get_transcript(self):
        # 実際のAPIを使用するため、環境変数が設定されていることを確認
        self._check_environment_variables()
        
        # 実在する動画IDを使用
        video_id = "ewqOnvr8tAU"  # 適切な実在の動画IDに置き換えてください
        
        # 実際のAPIを使用して文字起こしを取得
        result = get_transcript(video_id)
        
        # 結果の検証
        self.assertIsNotNone(result)
        self.assertGreater(len(result), 0)
        logger.info(f"取得した文字起こしの長さ: {len(result)} 文字")
    
    @unittest.skipIf(not os.environ.get('OPENROUTER_API_KEY'), 'OPENROUTER_API_KEY環境変数が設定されていません')
    def test_generate_blog_format_stability(self):
        """出力フォーマットの安定性を詳細にテストする"""
        self._check_environment_variables()
        
        # テスト用のイベント詳細を作成
        event_detail = self.create_event_detail(
            youtube_url="https://www.youtube.com/watch?v=rrKl0s23E0M",
            slide_file=True
        )
        
        # テスト用モデルを指定
        test_model = 'google/gemini-2.5-flash-lite-preview-06-17'
        logger.info(f"フォーマット安定性テスト - モデル: {test_model}")
        
        # エラーパターンを記録
        error_patterns = {
            'json_parse_error': 0,
            'validation_error': 0,
            'empty_field': 0,
            'api_error': 0,
            'format_error': 0
        }
        
        success_results = []
        
        for i in range(5):
            logger.info(f"\n=== フォーマット安定性テスト {i + 1}/5 ===")
            
            try:
                result = generate_blog(event_detail, model=test_model)
                
                # 詳細な検証
                validation_errors = []
                
                # タイトルの検証
                if not result.title:
                    validation_errors.append("タイトルが空")
                    error_patterns['empty_field'] += 1
                elif len(result.title) > 60:
                    validation_errors.append(f"タイトルが長すぎる: {len(result.title)}文字")
                    error_patterns['format_error'] += 1
                
                # メタディスクリプションの検証
                if not result.meta_description:
                    validation_errors.append("メタディスクリプションが空")
                    error_patterns['empty_field'] += 1
                elif len(result.meta_description) > 160:
                    validation_errors.append(f"メタディスクリプションが長すぎる: {len(result.meta_description)}文字")
                    error_patterns['format_error'] += 1
                
                # 本文の検証
                if not result.text:
                    validation_errors.append("本文が空")
                    error_patterns['empty_field'] += 1
                elif len(result.text) < 500:
                    validation_errors.append(f"本文が短すぎる: {len(result.text)}文字")
                    error_patterns['format_error'] += 1
                elif len(result.text) > 3000:
                    validation_errors.append(f"本文が長すぎる: {len(result.text)}文字")
                    error_patterns['format_error'] += 1
                
                if validation_errors:
                    logger.warning(f"試行 {i + 1} - 検証エラー: {', '.join(validation_errors)}")
                    error_patterns['validation_error'] += 1
                else:
                    logger.info(f"試行 {i + 1}: 完全に成功")
                    success_results.append({
                        'title_length': len(result.title),
                        'meta_length': len(result.meta_description),
                        'text_length': len(result.text)
                    })
                
            except json.JSONDecodeError as e:
                error_patterns['json_parse_error'] += 1
                logger.error(f"試行 {i + 1}: JSONパースエラー - {str(e)}")
            except Exception as e:
                if 'API' in str(e) or 'OpenRouter' in str(e):
                    error_patterns['api_error'] += 1
                else:
                    error_patterns['format_error'] += 1
                logger.error(f"試行 {i + 1}: エラー - {type(e).__name__}: {str(e)}")
        
        # エラーパターンの分析
        logger.info("\n=== エラーパターン分析 ===")
        for pattern, count in error_patterns.items():
            if count > 0:
                logger.info(f"{pattern}: {count}回")
        
        # 成功結果の統計
        if success_results:
            logger.info("\n=== 成功結果の統計 ===")
            avg_title = sum(r['title_length'] for r in success_results) / len(success_results)
            avg_meta = sum(r['meta_length'] for r in success_results) / len(success_results)
            avg_text = sum(r['text_length'] for r in success_results) / len(success_results)
            logger.info(f"平均タイトル長: {avg_title:.1f}文字")
            logger.info(f"平均メタディスクリプション長: {avg_meta:.1f}文字")
            logger.info(f"平均本文長: {avg_text:.1f}文字")
        
        # 最も多いエラーパターンを特定
        if any(error_patterns.values()):
            most_common_error = max(error_patterns.items(), key=lambda x: x[1])
            logger.warning(f"\n最も多いエラーパターン: {most_common_error[0]} ({most_common_error[1]}回)")
            
            # エラーパターンに基づく改善提案
            if most_common_error[0] == 'json_parse_error':
                logger.info("提案: プロンプトのJSON出力指示を強化する必要があります")
            elif most_common_error[0] == 'empty_field':
                logger.info("提案: 各フィールドの必須性をプロンプトで強調する必要があります")
            elif most_common_error[0] == 'format_error':
                logger.info("提案: 文字数制限をプロンプトで明確に指定する必要があります")
        
        # 成功率が60%未満の場合はテスト失敗
        success_rate = len(success_results) / 5
        self.assertGreaterEqual(success_rate, 0.6, 
            f"成功率が低すぎます: {success_rate * 100:.0f}% (成功: {len(success_results)}/5)")
