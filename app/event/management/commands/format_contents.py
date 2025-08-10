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
    help = 'EventDetailのcontentsを1文ごとに改行を入れて整形する'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=3,
            help='処理する記事の数 (デフォルト: 3)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='実際に保存せずに結果を表示するのみ'
        )
        parser.add_argument(
            '--skip',
            type=int,
            default=0,
            help='先頭からスキップする記事数 (デフォルト: 0)'
        )

    def handle(self, *args, **options):
        limit = options['limit']
        dry_run = options['dry_run']
        skip = options['skip']
        
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
        
        # 処理対象のEventDetailを取得
        event_details = EventDetail.objects.filter(
            contents__isnull=False
        ).exclude(
            contents=''
        ).order_by('-event__date')[skip:skip+limit]
        
        if not event_details:
            self.stdout.write(self.style.WARNING('処理対象のEventDetailが見つかりません'))
            return
        
        self.stdout.write(f'処理対象: {len(event_details)}件')
        
        processed_count = 0
        urls = []
        
        for detail in event_details:
            try:
                self.stdout.write(f'\n処理中: {detail.title} (Event: {detail.event.community.name})')
                
                # 既に改行処理済みかチェック（簡易的に、5行以上あるかで判定）
                if detail.contents.count('\n') >= 5:
                    self.stdout.write(self.style.WARNING('  既に改行処理済みの可能性があります'))
                    continue
                
                # Gemini APIで整形
                prompt = prompt_template.format(content=detail.contents)
                response = model.generate_content(prompt)
                
                if not response or not response.text:
                    self.stdout.write(self.style.ERROR('  APIからの応答が空です'))
                    continue
                
                formatted_content = response.text.strip()
                
                # 結果を表示
                self.stdout.write(self.style.SUCCESS(f'  元の文字数: {len(detail.contents)}'))
                self.stdout.write(self.style.SUCCESS(f'  整形後文字数: {len(formatted_content)}'))
                self.stdout.write(f'  元の改行数: {detail.contents.count(chr(10))}')
                self.stdout.write(f'  整形後改行数: {formatted_content.count(chr(10))}')
                
                if dry_run:
                    self.stdout.write('\n--- 整形結果プレビュー ---')
                    self.stdout.write(formatted_content[:500] + '...' if len(formatted_content) > 500 else formatted_content)
                    self.stdout.write('--- プレビュー終了 ---\n')
                else:
                    # データベースに保存
                    with transaction.atomic():
                        detail.contents = formatted_content
                        detail.save(update_fields=['contents'])
                    
                    self.stdout.write(self.style.SUCCESS(f'  ✓ 保存完了'))
                    
                    # URLを生成して保存
                    event = detail.event
                    url = f'https://vrc-ta-hub.com/event/{event.pk}/{detail.pk}/'
                    urls.append(url)
                    processed_count += 1
                
                # API制限対策のため少し待機
                time.sleep(1)
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'  エラー: {e}'))
                logger.error(f'EventDetail {detail.pk} の処理中にエラー: {e}', exc_info=True)
                continue
        
        # 結果サマリー
        self.stdout.write('\n' + '=' * 50)
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUNモード: 実際の保存は行われませんでした'))
        else:
            self.stdout.write(self.style.SUCCESS(f'処理完了: {processed_count}件'))
            if urls:
                self.stdout.write('\n修正された記事のURL:')
                for url in urls:
                    self.stdout.write(f'  - {url}')