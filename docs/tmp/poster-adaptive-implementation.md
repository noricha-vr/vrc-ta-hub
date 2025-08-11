# ポスター画像対応 実装ガイドライン

## デザインコンセプト

### 基本方針
- **左側固定**: ポスター画像を左側に固定配置（スティッキー）
- **右側スクロール**: 情報コンテンツは右側でスクロール可能
- **自動調整**: 縦長・横長どちらのポスターも自動で最適表示

## レイアウト構造

```
デスクトップ (1200px)
┌─────────────┬───────────────────────┐
│  ポスター    │  タイトル・タグ         │
│  (380px幅)  │  ────────────────    │
│  固定表示    │  クイック情報カード      │
│             │  参加ボタン            │
│  転載許可    │  イベント説明          │
│  DLボタン    │  (スクロール可能)       │
└─────────────┴───────────────────────┘

モバイル (768px以下)
┌─────────────────────┐
│     ポスター         │
│     転載許可・DL      │
├─────────────────────┤
│     タイトル         │
│     クイック情報      │
│     参加ボタン        │
│     イベント説明      │
└─────────────────────┘
```

## ポスター表示ロジック

### CSS実装
```css
.poster-wrapper {
    width: 100%;
    max-height: 600px;  /* 最大高さを制限 */
    background: #2a2a2a;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
}

.poster-image {
    width: 100%;
    height: auto;
    max-height: 600px;
    object-fit: contain;  /* 全体を表示 */
    display: block;
}
```

### JavaScript補助（オプション）
```javascript
class PosterManager {
    constructor(imageElement) {
        this.img = imageElement;
        this.container = imageElement.parentElement;
    }
    
    adjustDisplay() {
        const ratio = this.img.naturalWidth / this.img.naturalHeight;
        
        // アスペクト比に基づく調整
        if (ratio < 0.6) {
            // 超縦長（9:16など）
            this.container.style.maxHeight = '600px';
        } else if (ratio > 1.5) {
            // 横長（16:9など）
            this.container.style.maxHeight = '300px';
        } else {
            // 標準的な比率
            this.container.style.maxHeight = '500px';
        }
    }
}
```

## Django テンプレート実装

### community_detail.html
```django
{% extends 'base.html' %}
{% load static %}

{% block content %}
<div class="container">
    <div class="main-content">
        <!-- ポスターセクション -->
        <div class="poster-section">
            <div class="poster-container">
                <div class="poster-wrapper">
                    {% if community.poster_image %}
                        <img src="{{ community.poster_image.url }}" 
                             alt="{{ community.name }}のポスター"
                             class="poster-image"
                             loading="lazy"
                             onload="adjustPosterDisplay(this)">
                    {% else %}
                        <div class="poster-placeholder">
                            <div class="poster-placeholder-icon">🖼️</div>
                            <div class="poster-placeholder-text">ポスター画像なし</div>
                        </div>
                    {% endif %}
                </div>
                
                {% if community.allow_poster_repost %}
                    <div class="repost-badge allowed">
                        ✓ ポスター転載可
                    </div>
                {% else %}
                    <div class="repost-badge">
                        × ポスター転載不可
                    </div>
                {% endif %}
                
                {% if community.poster_image %}
                    <div class="poster-actions">
                        <a href="{{ community.poster_image.url }}" 
                           download="{{ community.name }}_poster.jpg"
                           class="download-btn">
                            📥 ポスターをダウンロード
                        </a>
                    </div>
                {% endif %}
            </div>
        </div>
        
        <!-- 情報セクション -->
        <div class="info-section">
            <!-- タイトル -->
            <div class="title-section">
                <h1 class="community-title">{{ community.name }}</h1>
                <div class="tags">
                    {% for tag in community.tags %}
                        <span class="tag {% if tag == 'academic' %}academic{% endif %}">
                            {{ tag|capfirst }}
                        </span>
                    {% endfor %}
                </div>
                <div class="organizer-info">
                    <strong>主催:</strong> {{ community.organizers }}
                </div>
            </div>
            
            <!-- クイック情報 -->
            <div class="quick-info-grid">
                {% if community.weekdays %}
                <div class="info-card">
                    <div class="info-card-icon">📅</div>
                    <div class="info-card-content">
                        <h3>開催曜日</h3>
                        <p>{{ community.weekdays|join:", " }}</p>
                    </div>
                </div>
                {% endif %}
                
                <div class="info-card">
                    <div class="info-card-icon">⏰</div>
                    <div class="info-card-content">
                        <h3>開催時間</h3>
                        <p>{{ community.start_time|time:"H:i" }} - {{ community.end_time|time:"H:i" }}</p>
                    </div>
                </div>
                
                {% if community.frequency %}
                <div class="info-card">
                    <div class="info-card-icon">🔄</div>
                    <div class="info-card-content">
                        <h3>開催頻度</h3>
                        <p>{{ community.frequency }}</p>
                    </div>
                </div>
                {% endif %}
                
                <div class="info-card">
                    <div class="info-card-icon">💻</div>
                    <div class="info-card-content">
                        <h3>プラットフォーム</h3>
                        <p>{{ community.get_platform_display }}</p>
                    </div>
                </div>
            </div>
            
            <!-- 参加方法 -->
            <div class="action-section">
                <h2 class="section-title">参加方法</h2>
                <div class="action-buttons">
                    {% if community.group_url %}
                    <a href="{{ community.group_url }}" 
                       target="_blank"
                       class="action-btn vrchat">
                        🌐 VRChatグループ
                    </a>
                    {% endif %}
                    
                    {% if community.discord %}
                    <a href="{{ community.discord }}" 
                       target="_blank"
                       class="action-btn discord">
                        💬 Discord
                    </a>
                    {% endif %}
                    
                    {% if community.twitter_hashtag %}
                    <a href="https://twitter.com/hashtag/{{ community.twitter_hashtag|slice:'1:' }}"
                       target="_blank" 
                       class="action-btn twitter">
                        🐦 {{ community.twitter_hashtag }}
                    </a>
                    {% endif %}
                    
                    {% if community.organizer_url %}
                    <a href="{{ community.organizer_url }}" 
                       target="_blank"
                       class="action-btn sns">
                        👤 主催者プロフィール
                    </a>
                    {% endif %}
                </div>
            </div>
            
            <!-- 説明 -->
            {% if community.description %}
            <div class="description-section">
                <h2 class="section-title">イベント紹介</h2>
                <div class="description-text">{{ community.description|linebreaks }}</div>
            </div>
            {% endif %}
        </div>
    </div>
</div>
{% endblock %}

{% block extra_js %}
<script>
function adjustPosterDisplay(img) {
    // 画像のアスペクト比に基づいて表示を最適化
    const ratio = img.naturalWidth / img.naturalHeight;
    const container = img.parentElement;
    
    if (ratio < 0.6) {
        // 超縦長
        container.style.maxHeight = '600px';
    } else if (ratio > 1.5) {
        // 横長
        container.style.maxHeight = '300px';
    }
    
    console.log(`Poster loaded: ${img.naturalWidth}x${img.naturalHeight}, ratio: ${ratio.toFixed(2)}`);
}
</script>
{% endblock %}
```

## パフォーマンス最適化

### 画像の遅延読み込み
```html
<img loading="lazy" ...>
```

### レスポンシブ画像
```django
{% if community.poster_image %}
    <picture>
        <source media="(max-width: 768px)" 
                srcset="{{ community.poster_image.url|thumbnail:'400x600' }}">
        <source media="(min-width: 769px)" 
                srcset="{{ community.poster_image.url|thumbnail:'380x600' }}">
        <img src="{{ community.poster_image.url }}" 
             alt="{{ community.name }}"
             class="poster-image">
    </picture>
{% endif %}
```

## 実装チェックリスト

- [ ] CSS Grid レイアウトの実装
- [ ] ポスター画像の object-fit 設定
- [ ] スティッキーポジショニング
- [ ] 条件付き表示ロジック（空フィールド非表示）
- [ ] レスポンシブブレークポイント
- [ ] 画像遅延読み込み
- [ ] ダウンロードボタン機能
- [ ] 転載許可バッジ表示
- [ ] モバイル最適化
- [ ] アクセシビリティ対応（alt属性など）

## 期待される効果

1. **統一感のある表示**: どんなアスペクト比でも美しく表示
2. **情報の見やすさ**: 左側ポスター、右側情報の明確な分離
3. **スクロール体験**: ポスターを見ながら情報を読める
4. **モバイル対応**: 小さい画面でも見やすい縦積みレイアウト