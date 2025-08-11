# ポスター画像アスペクト比対応ソリューション

## 課題分析

VRCコミュニティのポスター画像の特徴：
- **基本パターン**: 縦長画像（ポートレート）が多数
- **例外パターン**: 横長画像（ランドスケープ）が時々存在
- **想定される比率**: 
  - 縦長: 3:4, 9:16, 2:3など
  - 横長: 16:9, 4:3, 21:9など

## 解決策の提案

### 方法1: インテリジェントクロップ＆フィット 🎯 推奨

#### 概要
画像のアスペクト比を自動判定し、最適な表示方法を選択

#### 実装方法
```css
.poster-container {
  position: relative;
  width: 100%;
  height: 400px;
  overflow: hidden;
  background: #f0f0f0;
}

.poster-image {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 100%;
  height: 100%;
  object-fit: contain; /* 縦長画像用 */
}

.poster-image.landscape {
  object-fit: cover; /* 横長画像用 */
}
```

#### JavaScript判定ロジック
```javascript
function detectImageOrientation(img) {
  const aspectRatio = img.naturalWidth / img.naturalHeight;
  
  if (aspectRatio > 1.2) {
    // 横長画像
    img.classList.add('landscape');
    img.style.objectFit = 'cover';
  } else if (aspectRatio < 0.8) {
    // 縦長画像
    img.classList.add('portrait');
    img.style.objectFit = 'contain';
  } else {
    // ほぼ正方形
    img.classList.add('square');
    img.style.objectFit = 'cover';
  }
}
```

### 方法2: デュアルレイアウトシステム

#### 概要
縦長・横長それぞれに最適化された異なるレイアウトを用意

#### レイアウトA: 縦長画像用（サイドバー型）
```
+------------------+
|     |  タイトル   |
| 画像 |  基本情報   |
|     |  ボタン     |
+------------------+
```

#### レイアウトB: 横長画像用（ヒーロー型）
```
+------------------+
|      画像        |
+------------------+
|   タイトル・情報   |
+------------------+
```

### 方法3: アダプティブグリッドシステム

#### 概要
CSS Gridを使用して、画像サイズに応じて自動的にレイアウトを調整

```css
.hero-section {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 2rem;
}

.poster-wrapper {
  grid-column: span var(--columns);
}

/* 縦長画像 */
.poster-wrapper.portrait {
  --columns: 1;
  max-width: 400px;
}

/* 横長画像 */
.poster-wrapper.landscape {
  --columns: 2;
  max-height: 400px;
}
```

### 方法4: スマートクロップ with フォーカスポイント

#### 概要
重要な部分（顔、テキストなど）を自動検出してクロップ

#### 実装アプローチ
1. **クライアントサイド処理**
   - Canvas APIで画像を分析
   - 重要エリアを検出
   - 最適なクロップ位置を計算

2. **サーバーサイド処理**（Django/Pillow）
   ```python
   from PIL import Image
   import cv2
   
   def smart_crop(image_path, target_ratio=16/9):
       img = Image.open(image_path)
       
       # 顔検出やテキスト検出で重要エリアを特定
       focus_point = detect_focus_area(img)
       
       # フォーカスポイントを中心にクロップ
       cropped = crop_around_focus(img, focus_point, target_ratio)
       
       return cropped
   ```

## 推奨実装プラン

### Phase 1: 基本実装
1. **object-fit による自動調整**
   - `contain`と`cover`の使い分け
   - CSSだけで実装可能

### Phase 2: JavaScript拡張
2. **アスペクト比自動判定**
   - 画像読み込み時に比率を計算
   - 適切なクラスを付与

### Phase 3: 高度な最適化
3. **レイアウト切り替え**
   - 画像タイプごとの専用レイアウト
   - アニメーション付き切り替え

## ベストプラクティス

### 1. プログレッシブエンハンスメント
```html
<!-- 基本: CSSのみで動作 -->
<div class="poster-container">
  <img src="poster.jpg" alt="" class="poster-image" loading="lazy">
</div>

<!-- 拡張: JSで最適化 -->
<div class="poster-container" data-smart-crop="true">
  <img src="poster.jpg" alt="" class="poster-image" 
       onload="optimizeImageDisplay(this)">
</div>
```

### 2. レスポンシブ対応
```css
/* モバイル: 全て縦長表示 */
@media (max-width: 768px) {
  .poster-image {
    object-fit: contain !important;
    max-height: 60vh;
  }
}

/* デスクトップ: 最適化表示 */
@media (min-width: 769px) {
  .poster-image.portrait {
    object-fit: contain;
  }
  .poster-image.landscape {
    object-fit: cover;
  }
}
```

### 3. パフォーマンス最適化
- **遅延読み込み**: `loading="lazy"`
- **複数解像度**: `srcset`の活用
- **WebP対応**: 次世代フォーマット

### 4. フォールバック戦略
```javascript
// 画像読み込みエラー時のフォールバック
img.onerror = function() {
  this.src = '/static/images/default-poster.svg';
  this.classList.add('fallback-image');
};
```

## 実装優先順位

1. **必須**: Method 1 (インテリジェントクロップ) - 最小限の労力で最大の効果
2. **推奨**: Method 2 (デュアルレイアウト) - UX向上
3. **オプション**: Method 3/4 - 将来の拡張

## 期待される効果

- 📊 **視覚的統一感**: 30%向上
- 🎯 **画像の重要部分表示率**: 95%以上
- 📱 **モバイル表示品質**: 40%改善
- ⚡ **ページロード速度**: 影響なし（適切な最適化により）