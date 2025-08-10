#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Markdown記法を保持したまま文章を整形するテストスクリプト"""

import os
import sys
import django

# Django環境のセットアップ
sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ta_hub.settings')
django.setup()

from event.models import EventDetail
import google.generativeai as genai
from django.conf import settings

# Gemini APIの設定
api_key = os.environ.get('GOOGLE_API_KEY', settings.GOOGLE_API_KEY)
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

# ID 1の記事でテスト
detail = EventDetail.objects.get(pk=1)

print("=== 元の文章 ===")
print(detail.contents[:500])
print(f"\n改行数: {detail.contents.count(chr(10))}")

# APIで整形
prompt = prompt_template.format(content=detail.contents)
response = model.generate_content(prompt)

if response and response.text:
    formatted_content = response.text.strip()
    
    print("\n=== 整形後の文章 ===")
    print(formatted_content[:500])
    print(f"\n改行数: {formatted_content.count(chr(10))}")
    
    # Markdown要素が保持されているか確認
    print("\n=== Markdown要素の確認 ===")
    print(f"見出し(#)を含む: {'#' in formatted_content}")
    print(f"リスト(-)を含む: {'-' in formatted_content}")
    print(f"太字(**)を含む: {'**' in formatted_content}")
    
    # 保存するか確認
    save_input = input("\n保存しますか？ (y/n): ")
    if save_input.lower() == 'y':
        detail.contents = formatted_content
        detail.save()
        print("保存しました")
    else:
        print("保存をキャンセルしました")
else:
    print("APIからの応答が空です")