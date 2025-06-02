# VRC-TA-Hub Discord Bot 実装ガイド

## アーキテクチャ概要

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Discord Server │────▶│   Discord Bot    │────▶│  VRC-TA-Hub API │
│                 │     │  (Python/Go)     │     │   (Django DRF)  │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │                           │
                               ▼                           ▼
                        ┌──────────────┐           ┌──────────────┐
                        │  LLM Service │           │   Database   │
                        │  (OpenRouter)│           │ (PostgreSQL) │
                        └──────────────┘           └──────────────┘
```

## 必要なPythonパッケージ

```python
# requirements.txt
discord.py==2.3.2
aiohttp==3.9.1
python-dotenv==1.0.0
pydantic==2.5.3
openai==1.6.1
pypdf==3.17.4
youtube-transcript-api==0.6.1
Pillow==10.2.0
```

## コア実装

### 1. Bot基本構造

```python
# bot.py
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import logging
from api_client import VRCTAHubAPI
from llm_handler import LLMHandler

load_dotenv()
logging.basicConfig(level=logging.INFO)

class VRCTAHubBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        
        super().__init__(
            command_prefix='!',
            intents=intents
        )
        
        self.api_client = VRCTAHubAPI(
            base_url=os.getenv('VRCTAHUB_API_URL'),
            api_key=os.getenv('VRCTAHUB_API_KEY')
        )
        self.llm_handler = LLMHandler()
        
    async def setup_hook(self):
        # Cogの読み込み
        await self.load_extension('cogs.lt_detector')
        await self.load_extension('cogs.slide_handler')
        await self.load_extension('cogs.youtube_handler')
        
    async def on_ready(self):
        logging.info(f'{self.user} has connected to Discord!')
        # サーバーごとのコミュニティマッピングを初期化
        await self.initialize_community_mapping()
        
    async def initialize_community_mapping(self):
        """サーバー名からコミュニティを特定してマッピング"""
        self.community_map = {}
        
        for guild in self.guilds:
            # APIでコミュニティを検索
            community = await self.api_client.search_community(guild.name)
            if community:
                self.community_map[guild.id] = community['id']
                logging.info(f"Mapped {guild.name} to community {community['id']}")
```

### 2. API クライアント実装

```python
# api_client.py
import aiohttp
import logging
from typing import Optional, Dict, List
from datetime import date

class VRCTAHubAPI:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
        
    async def search_community(self, name: str) -> Optional[Dict]:
        """コミュニティ名で検索"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'{self.base_url}/community/',
                headers=self.headers,
                params={'name': name}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data['results']:
                        return data['results'][0]
                return None
                
    async def get_events_by_community(self, community_id: int, target_date: date) -> List[Dict]:
        """コミュニティIDと日付でイベントを検索"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'{self.base_url}/event/',
                headers=self.headers,
                params={
                    'community': community_id,
                    'start_date': target_date.isoformat(),
                    'end_date': target_date.isoformat()
                }
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data['results']
                return []
                
    async def create_event_detail(self, data: Dict) -> Optional[Dict]:
        """イベント詳細を作成"""
        async with aiohttp.ClientSession() as session:
            # ファイルアップロードの場合
            if 'slide_file' in data:
                form_data = aiohttp.FormData()
                for key, value in data.items():
                    if key == 'slide_file':
                        form_data.add_field(
                            'slide_file',
                            value['content'],
                            filename=value['filename'],
                            content_type='application/pdf'
                        )
                    else:
                        form_data.add_field(key, str(value))
                        
                headers = {'Authorization': f'Bearer {self.api_key}'}
                async with session.post(
                    f'{self.base_url}/event-details/',
                    headers=headers,
                    data=form_data
                ) as resp:
                    if resp.status == 201:
                        return await resp.json()
                    else:
                        logging.error(f"Failed to create event detail: {await resp.text()}")
                        return None
            else:
                # JSONデータの場合
                async with session.post(
                    f'{self.base_url}/event-details/',
                    headers=self.headers,
                    json=data
                ) as resp:
                    if resp.status == 201:
                        return await resp.json()
                    else:
                        logging.error(f"Failed to create event detail: {await resp.text()}")
                        return None
                        
    async def generate_blog(self, event_detail_id: int) -> bool:
        """ブログを生成"""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f'{self.base_url}/event-details/{event_detail_id}/generate_blog/',
                headers=self.headers
            ) as resp:
                return resp.status == 200
```

### 3. LT検出Cog

```python
# cogs/lt_detector.py
import discord
from discord.ext import commands
import re
from datetime import datetime
from typing import Optional, Dict

class LTDetector(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.lt_patterns = [
            r'LT.*?(\d{1,2}:\d{2})',
            r'ライトニングトーク.*?(\d{1,2}:\d{2})',
            r'発表.*?@(\w+)',
        ]
        self.pending_registrations = {}
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
            
        # LT情報を検出
        lt_info = self.detect_lt_info(message.content)
        if not lt_info:
            return
            
        # コミュニティを特定
        community_id = self.bot.community_map.get(message.guild.id)
        if not community_id:
            await message.reply("このサーバーはまだVRC-TA-Hubに登録されていません。")
            return
            
        # スレッドを作成して確認
        thread = await message.create_thread(
            name=f"LT登録: {lt_info.get('theme', '未定')}",
            auto_archive_duration=60
        )
        
        # 必要情報を収集
        registration_data = await self.collect_lt_information(
            thread, message, lt_info, community_id
        )
        
        if registration_data:
            # 登録実行
            await self.register_lt(thread, registration_data)
            
    def detect_lt_info(self, content: str) -> Optional[Dict]:
        """メッセージからLT情報を抽出"""
        info = {}
        
        # 時刻を検出
        time_match = re.search(r'(\d{1,2}):(\d{2})', content)
        if time_match:
            info['start_time'] = f"{time_match.group(1).zfill(2)}:{time_match.group(2)}:00"
            
        # ユーザーメンションを検出
        mention_match = re.search(r'<@!?(\d+)>', content)
        if mention_match:
            info['speaker_id'] = mention_match.group(1)
            
        # テーマを推定（LLMを使用）
        theme = self.bot.llm_handler.extract_theme(content)
        if theme:
            info['theme'] = theme
            
        return info if info else None
        
    async def collect_lt_information(
        self, thread: discord.Thread, 
        original_message: discord.Message,
        lt_info: Dict, 
        community_id: int
    ) -> Optional[Dict]:
        """LT登録に必要な情報を収集"""
        embed = discord.Embed(
            title="LT情報を確認",
            description="以下の情報で登録してよろしいですか？",
            color=discord.Color.blue()
        )
        
        # 発表者情報
        if 'speaker_id' in lt_info:
            user = await self.bot.fetch_user(int(lt_info['speaker_id']))
            speaker_name = user.display_name
        else:
            speaker_name = original_message.author.display_name
            
        # 今日のイベントを取得
        today_events = await self.bot.api_client.get_events_by_community(
            community_id, datetime.now().date()
        )
        
        if not today_events:
            await thread.send("本日のイベントが見つかりません。")
            return None
            
        event = today_events[0]  # 通常は1日1イベント
        
        registration_data = {
            'event': event['id'],
            'detail_type': 'LT',
            'speaker': speaker_name,
            'theme': lt_info.get('theme', ''),
            'start_time': lt_info.get('start_time', '20:00:00'),
            'duration': 10  # デフォルト10分
        }
        
        embed.add_field(name="イベント", value=event['title'], inline=False)
        embed.add_field(name="発表者", value=speaker_name, inline=True)
        embed.add_field(name="テーマ", value=registration_data['theme'], inline=True)
        embed.add_field(name="開始時刻", value=registration_data['start_time'], inline=True)
        embed.add_field(name="発表時間", value=f"{registration_data['duration']}分", inline=True)
        
        # 確認メッセージ
        confirm_msg = await thread.send(embed=embed)
        await confirm_msg.add_reaction('✅')
        await confirm_msg.add_reaction('❌')
        await confirm_msg.add_reaction('✏️')  # 編集
        
        # リアクション待機
        def check(reaction, user):
            return (
                user != self.bot.user and
                reaction.message.id == confirm_msg.id and
                str(reaction.emoji) in ['✅', '❌', '✏️']
            )
            
        try:
            reaction, user = await self.bot.wait_for('reaction_add', timeout=300.0, check=check)
            
            if str(reaction.emoji) == '✅':
                return registration_data
            elif str(reaction.emoji) == '✏️':
                # 編集モード
                return await self.edit_lt_information(thread, registration_data)
            else:
                await thread.send("登録をキャンセルしました。")
                return None
                
        except asyncio.TimeoutError:
            await thread.send("タイムアウトしました。")
            return None
            
    async def register_lt(self, thread: discord.Thread, data: Dict):
        """LTをAPIに登録"""
        result = await self.bot.api_client.create_event_detail(data)
        
        if result:
            # ブログ生成（必要に応じて）
            if data.get('slide_file') or data.get('youtube_url'):
                await self.bot.api_client.generate_blog(result['id'])
                
            # 成功通知
            embed = discord.Embed(
                title="✅ LT登録完了",
                description=f"LTが正常に登録されました！",
                color=discord.Color.green()
            )
            embed.add_field(
                name="詳細ページ",
                value=f"https://vrc-ta-hub.com/event/detail/{result['id']}/",
                inline=False
            )
            await thread.send(embed=embed)
        else:
            await thread.send("❌ 登録に失敗しました。")

async def setup(bot):
    await bot.add_cog(LTDetector(bot))
```

### 4. スライドハンドラーCog

```python
# cogs/slide_handler.py
import discord
from discord.ext import commands
import io
from typing import Optional

class SlideHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
            
        # PDFファイルを検出
        pdf_attachment = None
        for attachment in message.attachments:
            if attachment.filename.lower().endswith('.pdf'):
                pdf_attachment = attachment
                break
                
        if not pdf_attachment:
            return
            
        # スレッドを作成
        thread = await message.create_thread(
            name=f"スライド登録: {pdf_attachment.filename}",
            auto_archive_duration=60
        )
        
        # 登録確認
        embed = discord.Embed(
            title="📄 スライドを検出しました",
            description="このスライドをVRC-TA-Hubに登録しますか？",
            color=discord.Color.blue()
        )
        embed.add_field(name="ファイル名", value=pdf_attachment.filename, inline=False)
        embed.add_field(name="サイズ", value=f"{pdf_attachment.size / 1024 / 1024:.2f} MB", inline=True)
        
        confirm_msg = await thread.send(embed=embed)
        await confirm_msg.add_reaction('✅')
        await confirm_msg.add_reaction('❌')
        
        def check(reaction, user):
            return (
                user != self.bot.user and
                reaction.message.id == confirm_msg.id and
                str(reaction.emoji) in ['✅', '❌']
            )
            
        try:
            reaction, user = await self.bot.wait_for('reaction_add', timeout=300.0, check=check)
            
            if str(reaction.emoji) == '✅':
                await self.process_slide(thread, message, pdf_attachment)
            else:
                await thread.send("登録をキャンセルしました。")
                
        except asyncio.TimeoutError:
            await thread.send("タイムアウトしました。")
            
    async def process_slide(
        self, 
        thread: discord.Thread, 
        message: discord.Message, 
        attachment: discord.Attachment
    ):
        """スライドを処理して登録"""
        # PDFをダウンロード
        pdf_data = await attachment.read()
        
        # PDFからテキストを抽出（LLMで解析）
        slide_info = await self.bot.llm_handler.analyze_pdf(pdf_data)
        
        # 既存のEventDetailから候補を検索
        community_id = self.bot.community_map.get(message.guild.id)
        if not community_id:
            await thread.send("コミュニティが特定できません。")
            return
            
        # 今日のイベントを取得
        today_events = await self.bot.api_client.get_events_by_community(
            community_id, datetime.now().date()
        )
        
        if not today_events:
            await thread.send("本日のイベントが見つかりません。")
            return
            
        event = today_events[0]
        
        # EventDetail候補を表示
        event_details = await self.bot.api_client.get_event_details_by_event(event['id'])
        
        if event_details:
            embed = discord.Embed(
                title="どのLTのスライドですか？",
                description="該当するLTを選択してください",
                color=discord.Color.blue()
            )
            
            options = []
            for i, detail in enumerate(event_details[:10]):  # 最大10件
                embed.add_field(
                    name=f"{i+1}. {detail['theme']}",
                    value=f"発表者: {detail['speaker']}",
                    inline=False
                )
                options.append(detail)
                
            select_msg = await thread.send(embed=embed)
            
            # 番号リアクションを追加
            for i in range(len(options)):
                await select_msg.add_reaction(f"{i+1}️⃣")
                
            # 選択を待機
            def check(reaction, user):
                return (
                    user != self.bot.user and
                    reaction.message.id == select_msg.id
                )
                
            try:
                reaction, user = await self.bot.wait_for('reaction_add', timeout=300.0, check=check)
                
                # 選択されたEventDetailを取得
                emoji_to_index = {
                    '1️⃣': 0, '2️⃣': 1, '3️⃣': 2, '4️⃣': 3, '5️⃣': 4,
                    '6️⃣': 5, '7️⃣': 6, '8️⃣': 7, '9️⃣': 8, '🔟': 9
                }
                
                index = emoji_to_index.get(str(reaction.emoji))
                if index is not None and index < len(options):
                    selected_detail = options[index]
                    
                    # スライドをアップロード
                    update_data = {
                        'slide_file': {
                            'content': pdf_data,
                            'filename': attachment.filename
                        }
                    }
                    
                    result = await self.bot.api_client.update_event_detail(
                        selected_detail['id'], 
                        update_data
                    )
                    
                    if result:
                        # ブログ生成
                        await self.bot.api_client.generate_blog(selected_detail['id'])
                        
                        embed = discord.Embed(
                            title="✅ スライド登録完了",
                            description="スライドが正常に登録され、ブログが生成されました！",
                            color=discord.Color.green()
                        )
                        embed.add_field(
                            name="詳細ページ",
                            value=f"https://vrc-ta-hub.com/event/detail/{selected_detail['id']}/",
                            inline=False
                        )
                        await thread.send(embed=embed)
                    else:
                        await thread.send("❌ スライドの登録に失敗しました。")
                        
            except asyncio.TimeoutError:
                await thread.send("タイムアウトしました。")
        else:
            # 新規作成
            await self.create_new_lt_with_slide(thread, message, event, pdf_data, attachment.filename, slide_info)

async def setup(bot):
    await bot.add_cog(SlideHandler(bot))
```

### 5. LLMハンドラー

```python
# llm_handler.py
import openai
import os
from typing import Optional, Dict
import json
import re
from pypdf import PdfReader
import io

class LLMHandler:
    def __init__(self):
        self.client = openai.OpenAI(
            api_key=os.getenv('OPENROUTER_API_KEY'),
            base_url="https://openrouter.ai/api/v1"
        )
        self.model = os.getenv('GEMINI_MODEL', 'google/gemini-2.0-flash-001')
        
    def extract_theme(self, message: str) -> Optional[str]:
        """メッセージからLTテーマを抽出"""
        prompt = f"""
        以下のメッセージからLT（ライトニングトーク）のテーマを抽出してください。
        テーマが明確でない場合はNoneを返してください。
        
        メッセージ: {message}
        
        出力形式: テーマのみを返す（説明不要）
        """
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=100
            )
            
            theme = response.choices[0].message.content.strip()
            return theme if theme != "None" else None
            
        except Exception as e:
            logging.error(f"Failed to extract theme: {e}")
            return None
            
    async def analyze_pdf(self, pdf_data: bytes) -> Dict:
        """PDFからスライド情報を抽出"""
        try:
            pdf_reader = PdfReader(io.BytesIO(pdf_data))
            text = ""
            
            # 最初の数ページからテキストを抽出
            for i in range(min(5, len(pdf_reader.pages))):
                text += pdf_reader.pages[i].extract_text() + "\n"
                
            prompt = f"""
            以下のスライドテキストから、発表情報を抽出してください。
            
            テキスト:
            {text[:2000]}
            
            以下のJSON形式で出力してください：
            {{
                "title": "発表タイトル",
                "speaker": "発表者名（見つからない場合は空文字）",
                "theme": "発表テーマ（タイトルと同じでも可）",
                "summary": "内容の要約（100文字以内）"
            }}
            """
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            
            return json.loads(response.choices[0].message.content)
            
        except Exception as e:
            logging.error(f"Failed to analyze PDF: {e}")
            return {
                "title": "",
                "speaker": "",
                "theme": "",
                "summary": ""
            }
            
    def match_event_detail(self, slide_info: Dict, event_details: list) -> Optional[int]:
        """スライド情報から最も適合するEventDetailを特定"""
        if not event_details:
            return None
            
        prompt = f"""
        以下のスライド情報に最も適合するイベント詳細を選んでください。
        
        スライド情報:
        - タイトル: {slide_info.get('title', '')}
        - 発表者: {slide_info.get('speaker', '')}
        - テーマ: {slide_info.get('theme', '')}
        
        イベント詳細リスト:
        """
        
        for i, detail in enumerate(event_details):
            prompt += f"\n{i+1}. テーマ: {detail['theme']}, 発表者: {detail['speaker']}"
            
        prompt += "\n\n最も適合する番号を返してください。適合するものがない場合は0を返してください。"
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=10
            )
            
            match = re.search(r'\d+', response.choices[0].message.content)
            if match:
                index = int(match.group()) - 1
                if 0 <= index < len(event_details):
                    return index
                    
        except Exception as e:
            logging.error(f"Failed to match event detail: {e}")
            
        return None
```

## デプロイメント

### Docker構成

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
```

### docker-compose.yml

```yaml
version: '3.8'

services:
  discord-bot:
    build: .
    restart: always
    env_file:
      - .env
    volumes:
      - ./logs:/app/logs
      - ./config:/app/config
    networks:
      - vrc-ta-hub-network

networks:
  vrc-ta-hub-network:
    external: true
```

## 監視とログ

### ログ設定

```python
# logging_config.py
import logging
import logging.handlers
import os

def setup_logging():
    # ログディレクトリの作成
    os.makedirs('logs', exist_ok=True)
    
    # ルートロガーの設定
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # ファイルハンドラー（日別ローテーション）
    file_handler = logging.handlers.TimedRotatingFileHandler(
        'logs/bot.log',
        when='midnight',
        interval=1,
        backupCount=30,
        encoding='utf-8'
    )
    file_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    )
    
    # コンソールハンドラー
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    )
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    # Discord.pyのログレベル調整
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('discord.http').setLevel(logging.WARNING)
```

### メトリクス収集

```python
# metrics.py
from dataclasses import dataclass
from datetime import datetime
import json

@dataclass
class BotMetrics:
    total_messages_processed: int = 0
    lt_registrations: int = 0
    slide_uploads: int = 0
    youtube_links: int = 0
    errors: int = 0
    
    def to_dict(self):
        return {
            'timestamp': datetime.now().isoformat(),
            'total_messages_processed': self.total_messages_processed,
            'lt_registrations': self.lt_registrations,
            'slide_uploads': self.slide_uploads,
            'youtube_links': self.youtube_links,
            'errors': self.errors,
            'success_rate': self.calculate_success_rate()
        }
        
    def calculate_success_rate(self):
        total_operations = (
            self.lt_registrations + 
            self.slide_uploads + 
            self.youtube_links
        )
        if total_operations == 0:
            return 1.0
        return 1.0 - (self.errors / total_operations)
        
    def save_metrics(self, filepath='logs/metrics.json'):
        with open(filepath, 'a') as f:
            json.dump(self.to_dict(), f)
            f.write('\n')
```

## セキュリティ考慮事項

1. **APIキーの管理**
   - 環境変数での管理
   - キーローテーション機能
   - アクセスログの記録

2. **ファイルアップロード**
   - ファイルサイズ制限（50MB）
   - ファイルタイプ検証
   - ウイルススキャン（オプション）

3. **レート制限**
   - ユーザーごとの操作制限
   - API呼び出し制限
   - スパム対策

4. **権限管理**
   - サーバーごとの権限設定
   - ロールベースアクセス制御
   - 監査ログ

## トラブルシューティング

### よくある問題

1. **コミュニティが見つからない**
   - サーバー名とコミュニティ名の不一致
   - コミュニティが未承認状態

2. **API認証エラー**
   - APIキーの期限切れ
   - 権限不足

3. **ファイルアップロードエラー**
   - ファイルサイズ超過
   - 不正なファイル形式

### デバッグモード

```python
# debug_mode.py
import os

DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

if DEBUG:
    logging.getLogger().setLevel(logging.DEBUG)
    # APIレスポンスの詳細ログ
    # LLMプロンプトの表示
    # 処理時間の計測
```