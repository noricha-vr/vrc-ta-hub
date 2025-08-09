#!/usr/bin/env python
import os
import sys
import django
from datetime import datetime, timezone

# Djangoのセットアップ
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ta_hub.settings')
django.setup()

from news.models import Post, Category
from django.utils.text import slugify

def create_activity_posts():
    """3つの活動記事を作成"""
    
    # 活動カテゴリーを取得
    try:
        activity_category = Category.objects.get(slug='activity')
    except Category.DoesNotExist:
        print("活動カテゴリーが見つかりません")
        return
    
    # 記事データ
    posts_data = [
        {
            'title': 'Webサイトの制作・運営',
            'slug': 'website-management',
            'body_markdown': '''# Webサイトの制作・運営

[VRChat技術学術系イベントHub](https://vrc-ta-hub.com/)の制作や運営をしています。

## 主な機能

### イベントカレンダー
VRChat内で開催される技術・学術系イベントを一覧で確認できるカレンダー機能を提供しています。開催日時、場所、内容などの詳細情報を掲載し、参加者が事前に予定を立てやすくしています。

### イベント情報の自動更新
Google Calendarと連携し、最新のイベント情報を自動的に取得・更新する仕組みを構築しています。これにより、常に最新の情報を提供できます。

### コミュニティページ
各技術・学術系コミュニティの紹介ページを用意し、活動内容や参加方法などを紹介しています。新規参加者にも分かりやすい情報提供を心がけています。

## 技術スタック
- Django (Python)
- Docker
- Google Cloud Platform
- Bootstrap 5

Webサイトは[オープンソース](https://github.com/noricha-vr/vrc-ta-hub)として公開しており、コミュニティメンバーからの貢献も受け付けています。''',
            # thumbnailはImageFieldなので、URLではなくNoneまたは画像ファイルを設定
            # 'thumbnail': None,  # 今回はサムネイルは設定しない
            'meta_description': 'VRChat技術学術系イベントHubのWebサイト制作・運営について。イベントカレンダー機能や自動更新システムなどを提供しています。',
            'is_published': True,
            'published_at': datetime.now(timezone.utc),
        },
        {
            'title': 'VRC 技術・学術イベントガイド 本',
            'slug': 'event-guide-book',
            'body_markdown': '''# VRC 技術・学術イベントガイド 本

VRChatで開催されている技術・学術系のイベント情報をまとめた書籍を作ってVketRealやコミケで販売しています。

## 書籍の特徴

### 包括的なイベント情報
VRChat内で定期的に開催されている技術・学術系イベントを網羅的に紹介。各イベントの特徴、開催頻度、参加方法などを詳しく解説しています。

### 初心者にも優しい内容
VRChatを始めたばかりの方でも参加しやすいよう、基本的な用語解説や参加の流れなども含めています。

### イベント主催者インタビュー
各イベントの主催者へのインタビューを掲載し、イベント開催の想いや今後の展望などを紹介しています。

## 入手方法

### 電子書籍版
[Booth](https://booth.pm/ja/items/5307031)から電子書籍版をダウンロードできます。PDFフォーマットで提供しており、スマートフォンやタブレット、PCなど様々なデバイスで閲覧可能です。

### 物理書籍版
VketRealやコミックマーケットなどのイベントで頒布しています。イベント開催情報は[Twitter](https://twitter.com/yonabeyona)でお知らせしています。

## 今後の展開
定期的に内容を更新し、最新のイベント情報を反映した改訂版の発行を予定しています。また、英語版の制作も検討中です。''',
            # 'thumbnail': None,
            'meta_description': 'VRChatの技術・学術系イベント情報をまとめた書籍。VketRealやコミケで販売、電子版はBoothからダウンロード可能。',
            'is_published': True,
            'published_at': datetime.now(timezone.utc),
        },
        {
            'title': '技術・学術系イベント情報アセット',
            'slug': 'event-info-asset',
            'body_markdown': '''# 技術・学術系イベント情報アセット

VRChat技術学術系のイベントを一覧で表示できるアセットを無料配布しています。

## アセットの特徴

### リアルタイム更新
Web APIと連携し、最新のイベント情報をリアルタイムで取得・表示します。ワールド制作者が手動で更新する必要がなく、常に最新の情報を提供できます。

### カスタマイズ可能
ワールドのデザインに合わせて、表示サイズや色などをカスタマイズ可能です。複数のプリセットデザインも用意しています。

### 軽量設計
パフォーマンスを重視した軽量設計により、ワールドの負荷を最小限に抑えています。多人数が同時にアクセスしても安定して動作します。

## 導入実績

### JPチュートリアルワールド
[JPチュートリアルワールドにも設置](https://twitter.com/azukimochi25/status/1721888965924720929)されており、多くのVRChatユーザーに利用されています。

### コミュニティワールド
複数の技術・学術系コミュニティのワールドで採用されており、イベント情報の共有に役立っています。

## 導入方法

### VCCから簡単導入
[VCC (VRChat Creator Companion)](https://azukimochi.github.io/vpm-repos-world/)から簡単に導入できます。詳しい導入手順はGitHubのドキュメントをご覧ください。

### サポート
導入や使用方法について質問がある場合は、[Discord](https://discord.gg/技術学術ハブ)でサポートを提供しています。

## 今後の機能追加予定
- イベント詳細情報の表示機能
- お気に入りイベントの登録機能
- イベントリマインダー機能

ぜひあなたのワールドにも設置してみてください！''',
            # 'thumbnail': None,
            'meta_description': 'VRChat技術学術系イベントを一覧表示できる無料アセット。JPチュートリアルワールドにも設置。VCCから簡単導入可能。',
            'is_published': True,
            'published_at': datetime.now(timezone.utc),
        }
    ]
    
    # 記事を作成
    for data in posts_data:
        # 既存の記事をチェック
        if Post.objects.filter(slug=data['slug']).exists():
            print(f"記事 '{data['title']}' は既に存在します")
            continue
        
        post = Post.objects.create(
            title=data['title'],
            slug=data['slug'],
            body_markdown=data['body_markdown'],
            meta_description=data['meta_description'],
            category=activity_category,
            is_published=data['is_published'],
            published_at=data['published_at']
        )
        print(f"記事 '{post.title}' を作成しました")
    
    print("\n活動記事の作成が完了しました")

if __name__ == '__main__':
    create_activity_posts()