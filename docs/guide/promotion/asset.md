---
title: アセットを設置する
description: 技術・学術系イベントの開催予定を表示するアセットを、VCCからワールドへ導入する流れを画像で案内します。
---

# アセットを設置する

アセットは、ワールドに追加して使う素材や仕組みです。
開催予定を表示する板を置き、訪れた人にイベントを紹介します。

## 始める前に

- 編集してよいワールドのプロジェクトを用意します。
- VCC（VRChat Creator Companion）とUnityを使える状態にします。
- 初めてワールドを作る場合は、[VRChat公式の制作手順](https://creators.vrchat.com/worlds/creating-your-first-world/)で準備します。

ワールドを編集しない人は、このページをワールド制作者に渡してください。
以下は [配布元の導入案内](https://github.com/Azukimochi/TaAGatheringListSystem) に沿った流れです。
画面の図は、アカウントや実際の予定を写さずに簡略化しています。

## 手順

### 1. 配布ページを開く

[アセットの配布ページ](https://azukimochi.github.io/vpm-repos-world/)を開きます。

![配布ページのTaAGatheringListSystemでAdd to VCCを赤枠①で示した図](images/promotion/asset-01.webp)

「TaAGatheringListSystem」の ①「Add to VCC」を押します。
ブラウザがVCCを開くか尋ねたら許可します。（画面はイメージです）

### 2. リポジトリを追加する

リポジトリは、VCCがアセットを取得する配布先です。

![VCCのリポジトリ追加確認でI Understand, Add Repositoryを赤枠①で示した図](images/promotion/asset-02.webp)

配布元が「Azukimochi-world」であることを確認します。
① の「I Understand, Add Repository」を押します。（画面はイメージです）

### 3. 設置するプロジェクトを選ぶ

VCCの「Projects」を開きます。

![VCCのProjectsで架空のワールド「さくらの広場」のManage Projectを赤枠①で示した図](images/promotion/asset-03.webp)

編集したいワールドの ①「Manage Project」を押します。（画面はイメージです）

### 4. パッケージを追加してUnityを開く

パッケージは、アセットをまとめて導入する単位です。

![VCCのAvailable PackagesでTaAGatheringListSystemの追加ボタンを赤枠①で示した図](images/promotion/asset-04.webp)

「TaAGatheringListSystem」の行の ①「＋」を押します。（画面はイメージです）

![VCCで導入後のOpen Projectを赤枠①で示した図](images/promotion/asset-05.webp)

追加が終わったら、① の「Open Project」を押します。
Unityが開くまで待ちます。（画面はイメージです）

### 5. 表示板をワールドに置く

Unityの「Project」は、素材を探す欄です。
「Packages」の「TaAGatheringListSystem」を開きます。

![UnityでTaAGatheringInfoSys.prefabを赤枠①、置き先のHierarchyを②で示した図](images/promotion/asset-06.webp)

① の「TaAGatheringInfoSys.prefab」を、② の「Hierarchy」へドラッグします。
Prefabは、部品を組み合わせた配置用のひな型です。（画面はイメージです）

### 6. 読みやすい位置に調整する

「Hierarchy」の「TaAGatheringInfoSys」を押します。
「Inspector」は、選んだ物の設定を変える欄です。

![UnityのInspectorで表示板のTransformを赤枠①で示した図](images/promotion/asset-07.webp)

① の「Transform」で調整します。（画面はイメージです）

| 項目 | 変わるもの |
|---|---|
| Position | 置く位置 |
| Rotation | 向き |
| Scale | 大きさ |

来た人が近づいて文字を読める高さと向きに置きます。
大きさはX・Y・Zを同じ倍率で変えてください。

### 7. ワールドで試す

Unity上部の「VRChat SDK」→「Show Control Panel」を開きます。
「Builder」を選びます。

![VRChat SDKのBuilderでBuild &amp; Testを赤枠①で示した図](images/promotion/asset-08.webp)

① の「Build & Test」で、手元のVRChatから表示を確認します。（画面はイメージです）

### 8. 開催予定の表示を確認する

ワールドで、置いた表示板の前へ移動します。

![ワールド内の開催予定表示板で架空の予定一覧を赤枠①で示した図](images/promotion/asset-09.webp)

① の一覧が読めることを確認します。（画面はイメージです）
図は説明用の架空データです。実際の表示は配布アセットの版によって異なります。
確認が終わったら、ワールド制作者の公開手順で反映してください。

配布元の説明では、取得元の情報に合わせて表示内容が更新されます。
毎回ワールドを編集して予定を書き換える必要はありません。

## うまくいかない時

### 「Add to VCC」で何も起きない

VCCがインストールされているか確認します。
[配布ページ](https://azukimochi.github.io/vpm-repos-world/)の「Copy」で配布先URLをコピーします。
VCCの「Settings」→「Packages」→「Add Repository」から追加します。
[VRChat公式の追加手順](https://vcc.docs.vrchat.com/guides/community-repositories/)にも画像があります。

### パッケージやPrefabが見つからない

VCCで、目的のワールドに追加したか確認します。
Unityでは「Assets」だけでなく「Packages」の中を探します。

### 開催予定が表示されない

VRChatでインターネットに接続できる状態か確認します。
配布元の案内で、対応するSDKや取得元URLの変更を確認します。
HubのAPIキーを新しく作る操作は不要です。

## 次にやること

- [Hubのポスターも掲示する](poster.md)
- [イベントを登録する](../event/create.md)（主催者・スタッフ向け）
