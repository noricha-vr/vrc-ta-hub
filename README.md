# VRChat 技術学術ハブ（VRChat Technology and Academic Hub）

このプロジェクトはVRChatで開催されている技術学術系イベントを知って、参加してもらい、盛り上げていくためのWeb制作プロジェクトです。

https://vrc-ta-hub.com

## 概要

VRChat技術学術ハブは、VRChat内で開催される技術・学術系イベントの情報を集約し、利用者が簡単にイベントの情報にアクセスできるようにするためのWebサイトです。

## 主な機能

1. ホームページ（ランディングページ）
    - プロジェクトの概要と主要な機能について説明します。

2. 開催日程ページ
    - GoogleカレンダーをAPIで埋め込み、今後開催予定のイベントをカレンダー形式で表示します。
    - 各イベントの詳細情報へのリンクを提供します。

3. 集会一覧ページ
    - これまでに開催された全てのイベントをリスト形式で表示します。
    - 検索（フィルター）機能を実装し、ユーザーが関心のあるイベントを見つけやすくします。
    - 各イベントの詳細ページへのリンクを提供します。

4. 集会詳細ページ
    - 各イベントの詳細情報を提供します。
    - イベントの概要、開催日時、登壇者、関連資料などの情報を掲載します。
    - 各回のイベントで行われたライトニングトークの資料やYouTube動画を埋め込みます。

5. LTアーカイブページ
    - 過去に開催されたライトニングトークの資料やYouTube動画を一覧で表示します。
    - 各資料や動画へのリンクを提供します。

## 使用技術

- Django（Python）
- Bootstrap5.3
- SQLite
- MySql
- Google Calendar API

## 開発環境

- Python 3.9以上
- Django 4.2以上
- Bootstrap5.3

## データモデル

1. 集会モデル
    - 集会の基本情報を管理します。
    - フィールド：名称、概要、開催頻度、開催場所、主催者など

2. イベントモデル
    - 各回のイベントの情報を管理します。
    - フィールド：集会（外部キー）、開催日時、登壇者、資料、YouTube動画のURLなど
    - 集会モデルとの1対多の関係を持ちます。

## TODO

- PGNをJPEGに変換して画像を軽量化
- ユーザー・イベントの登録画面
- ユーザーの承認画面
- ユーザー名の変更画面
- パスワードの変更画面
- 集会の編集画面
- スライドや動画のリンクを登録する画面