---
title: ポスターを掲示する
description: Hubの紹介ポスターを保存し、Unityでワールドに掲示して、読みやすさを確認する手順を画像で案内します。
---

# ポスターを掲示する

技術・学術系イベントHubの紹介ポスターを、VRChatのワールドに置きます。
集会ごとのポスターを紹介したい場合の確認方法も案内します。

## 始める前に

- 掲示先のワールドを編集してよいか確認します。
- Unityで開けるワールドのプロジェクトを用意します。
- 初めてワールドを作る場合は、[VRChat公式の制作手順](https://creators.vrchat.com/worlds/creating-your-first-world/)で準備します。

ワールドを編集しない人は、手順1の画像を保存してください。
保存した画像を、ワールドの制作者に渡せば掲示を頼めます。
以下のUnityの図は、操作する場所を簡略化しています。

## 手順

### 1. ポスターを選んで保存する

次の3種類から選びます。リンクを押すと元の画像が開きます。

![Hubの紹介ポスター3種類を並べ、ポスター1を赤枠①、2を②、3を③で示した図](images/promotion/poster-01.webp)

① は [ポスター1](https://data.vrc-ta-hub.com/guide/images/posters/vrc-ta-hub-poster-1.jpg)、② は [ポスター2](https://data.vrc-ta-hub.com/guide/images/posters/vrc-ta-hub-poster-2.jpg)、③ は [ポスター3](https://data.vrc-ta-hub.com/guide/images/posters/vrc-ta-hub-poster-3.jpg)です。（画面はイメージです）

![ブラウザでポスターを右クリックし、画像を保存するメニューを赤枠①で示した図](images/promotion/poster-02.webp)

開いた画像を右クリックし、① の保存メニューを選びます。
ブラウザによってメニュー名は異なります。（画面はイメージです）

配布画像は3種類とも1875×2500pxの縦長です。
横と縦の比率は3:4です。縦横を別々に引き伸ばさないでください。

### 2. Unityへ画像を取り込む

ワールドのプロジェクトを開きます。
「Project」は、画像などの素材を置く欄です。

![保存した画像ファイルを赤枠①、UnityのProjectのAssets欄を②で示した図](images/promotion/poster-03.webp)

① の画像ファイルを、② の「Project」の「Assets」へドラッグします。
ドラッグは、押したまま移動して離す操作です。（画面はイメージです）

### 3. マテリアルを作る

マテリアルは、物体の見た目を決める素材です。

![UnityのProjectでCreateからMaterialを作るメニューを赤枠①で示した図](images/promotion/poster-04.webp)

「Project」の空いている所を右クリックします。
「Create」から、① の「Material」を選びます。
名前を「HubPoster」にします。（画面はイメージです）

### 4. マテリアルに画像を入れる

作った「HubPoster」を押します。
「Inspector」は、選んだ物の設定を変える欄です。

![UnityのInspectorでShader欄を赤枠①、Base (RGB)の画像欄を②で示した図](images/promotion/poster-05.webp)

① の「Shader」で「Unlit/Texture」を選びます。
② の「Base (RGB)」へ、取り込んだ画像をドラッグします。（画面はイメージです）

この例は、明るさが照明に左右されにくい表示方法です。
ワールドの描画方式が違う場合は、制作者に使う素材を確認してください。

### 5. 掲示面を置いて画像を貼る

「Quad」は、画像を貼るために使える平らな板です。

![UnityのQuad作成メニューを赤枠①、作った掲示面を②で示した図](images/promotion/poster-06.webp)

「GameObject」→「3D Object」→ ① の「Quad」を選びます。
「Project」の「HubPoster」を、② の板へドラッグします。（画面はイメージです）

### 6. 大きさと向きを調整する

「Quad」を選び、「Inspector」の「Transform」を開きます。

![UnityのQuadのTransformでScaleのXを0.75、Yを1にする欄を赤枠①で示した図](images/promotion/poster-07.webp)

① の「Scale」を、Xは「0.75」、Yは「1」にします。
「Position」で位置、「Rotation」で向きを調整します。（画面はイメージです）

もっと大きくする時は、XとYを同じ倍率で増やします。
壁に重ならないよう少し手前へ出し、人が読む側に向けます。

### 7. ワールド内で読めるか確かめる

Unity上部の「VRChat SDK」→「Show Control Panel」を開きます。
「Builder」を選びます。

![VRChat SDKのBuild &amp; Testを赤枠①、ワールドに置いたポスターを②で示した図](images/promotion/poster-08.webp)

① の「Build & Test」で手元のVRChatから試します。
② の文字やQRコードを、近づいて確認します。（画面はイメージです）

裏返っている場合は「Quad」の向きを変えます。
確認が終わったら、ワールド制作者の公開手順で反映してください。

### 8. 集会個別のポスターは転載可の表示を確認する

Hub全体の紹介ポスターとは別に、各集会の紹介ページにもポスターがあります。

![集会の紹介ページで「集会を紹介するためのポスターの転載可」の表示を赤枠①で示した画面](images/promotion/poster-09.webp)

① の「集会を紹介するためのポスターの転載可」がある時は、押して画像を保存します。
「ポスターの転載はお問い合わせください」と出る場合は、主催者に確認してください。
転載可の表示は、集会を紹介する目的での利用を案内するものです。

## うまくいかない時

### 画像が横長になった・文字がつぶれる

「Transform」のXとYの比率を3:4に戻します。
画像を低解像度の画面写真で保存せず、手順1の元画像を使ってください。

### ポスターが見えない・ちらつく

板の裏側を見ていないか確認します。
壁と重なっている場合は、板を少し手前へ動かします。

### Unityで作業する人がいない

DiscordなどでHubを紹介することもできます。
次の文と、手順1のポスターを使って紹介してください。

> VRChatの技術・学術系イベントの開催予定と発表資料を探せるサイトです。
> 技術・学術系イベントHub： https://vrc-ta-hub.com/

## 次にやること

- [開催予定を表示するアセットを設置する](asset.md)
- [集会情報を編集する](../community/edit.md)
