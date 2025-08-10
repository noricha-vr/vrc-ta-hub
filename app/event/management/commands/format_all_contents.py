#!/usr/bin/env python
# -*- coding: utf-8 -*-

import time
import logging
from django.core.management.base import BaseCommand
from django.db import transaction
import google.generativeai as genai
from django.conf import settings
from event.models import EventDetail
import os

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'すべてのEventDetailのcontentsを1文ごとに改行を入れて整形する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=10,
            help='一度に処理する記事数 (デフォルト: 10)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='実際に保存せずに結果を表示するのみ'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='既に改行処理済みと思われる記事もスキップしない'
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        dry_run = options['dry_run']
        force = options['force']
        
        # Gemini APIの設定
        api_key = os.environ.get('GOOGLE_API_KEY', settings.GOOGLE_API_KEY)
        if not api_key:
            self.stdout.write(self.style.ERROR('Google API Keyが設定されていません'))
            return
        
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-2.5-flash-lite')
        
        # プロンプトテンプレート
        prompt_template = """
以下の文章を、通常の段落部分のみ1文ごとに改行を入れて整形してください。

【重要なルール】
1. Markdown記法は必ず保持する：
   - 見出し（#, ##, ### など）はそのまま維持
   - リスト（-, *, 1. など）の構造を保持
   - コードブロック（```）は改変しない
   - 引用（>）はそのまま維持
   - 太字（**）、斜体（*）などの装飾も保持
   - リンク記法 [テキスト](URL) を保持
   - 画像記法 ![alt](URL) を保持

2. 通常の段落（Markdown記法以外の部分）のみ：
   - 句点（。）、感嘆符（！）、疑問符（？）で文が終わったら改行
   - ただし、リスト項目内の文は改行しない

3. 禁止事項：
   - 元の文章の意味や内容を変更しない
   - 追加の説明や注釈を加えない
   - Markdown構造を壊さない
   - 空行の数を変更しない（見出しの前後など）

入力文章：
{content}

整形後の文章：
"""
        
        # 処理対象のEventDetailをすべて取得
        event_details = EventDetail.objects.filter(
            contents__isnull=False
        ).exclude(
            contents=''
        ).order_by('-event__date')
        
        total_count = event_details.count()
        
        if not event_details:
            self.stdout.write(self.style.WARNING('処理対象のEventDetailが見つかりません'))
            return
        
        self.stdout.write(self.style.SUCCESS(f'全記事数: {total_count}件'))
        
        processed_count = 0
        skipped_count = 0
        error_count = 0
        
        # バッチ処理
        for i in range(0, total_count, batch_size):
            batch = event_details[i:i+batch_size]
            self.stdout.write(f'\n--- バッチ {i//batch_size + 1} ({i+1}-{min(i+batch_size, total_count)}/{total_count}) ---')
            
            for detail in batch:
                try:
                    self.stdout.write(f'  処理: {detail.title[:50]}...')
                    
                    if dry_run:
                        self.stdout.write('    (DRY RUN - スキップ)')
                        continue
                    
                    # Gemini APIで整形
                    prompt = prompt_template.format(content=detail.contents)
                    response = model.generate_content(prompt)
                    
                    if not response or not response.text:
                        self.stdout.write(self.style.ERROR('    APIからの応答が空です'))
                        error_count += 1
                        continue
                    
                    formatted_content = response.text.strip()
                    
                    # データベースに保存
                    with transaction.atomic():
                        detail.contents = formatted_content
                        detail.save(update_fields=['contents'])
                    
                    processed_count += 1
                    self.stdout.write(self.style.SUCCESS(f'    ✓ 完了 (改行数: {detail.contents.count(chr(10))} → {formatted_content.count(chr(10))})'))
                    
                    # API制限対策のため少し待機
                    time.sleep(0.5)
                    
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'    エラー: {e}'))
                    logger.error(f'EventDetail {detail.pk} の処理中にエラー: {e}', exc_info=True)
                    error_count += 1
                    continue
            
            # バッチ間でも少し待機
            if i + batch_size < total_count:
                self.stdout.write('  バッチ処理完了、次のバッチへ...')
                time.sleep(2)
        
        # 結果サマリー
        self.stdout.write('\n' + '=' * 60)
        self.stdout.write(self.style.SUCCESS('処理完了サマリー:'))
        self.stdout.write(f'  全記事数: {total_count}')
        if dry_run:
            self.stdout.write(self.style.WARNING('  DRY RUNモード: 実際の保存は行われませんでした'))
        else:
            self.stdout.write(self.style.SUCCESS(f'  処理済み: {processed_count}件'))
            self.stdout.write(f'  スキップ: {skipped_count}件')
            self.stdout.write(f'  エラー: {error_count}件')
            self.stdout.write(self.style.SUCCESS(f'  成功率: {processed_count}/{total_count} ({processed_count*100//total_count if total_count > 0 else 0}%)'))