import discord
from discord.ext import commands
import discum
import asyncio
import threading
import json
from datetime import datetime
import requests
from io import BytesIO
import os
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Конфигурация
CONFIG = {
    "user_token": os.getenv("USER_TOKEN", "1"),
    "bot_token": os.getenv("BOT_TOKEN", "1"),
    "source_server_id": os.getenv("SOURCE_SERVER_ID", "1131000554005467206"),
    "target_server_id": int(os.getenv("TARGET_SERVER_ID", "1382453417754231028")),
    "channel_mapping": {
        # Маппинг каналов: исходный_канал_id: целевой_канал_id
        "1213700486302146650": 1400863423746674698,
        "1131011652062564413": 1400863591552520192,
    },
    "webhook_name": "Mirror Bot",
    "include_attachments": True,
    "include_embeds": True,
    "filter_bots": True,
    "prefix_format": "",  # Префикс для сообщений
    "log_messages": True,  # Логирование в консоль
    "debug_avatars": False,  # Отладочная информация об аватарах
}

class DiscordMirror:
    def __init__(self):
        # Discum client для user token (чтение)
        self.user_client = None
        
        # Discord.py bot для отправки
        self.bot = self.create_bot()
        
        # Кеш для webhooks
        self.webhook_cache = {}
        
        # Статус подключения
        self.user_ready = False
        self.bot_ready = False
    
    def create_bot(self):
        """Создание Discord.py бота"""
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.webhooks = True
        
        bot = commands.Bot(command_prefix='!mirror_', intents=intents)
        
        @bot.event
        async def on_ready():
            print(f'🤖 Bot готов: {bot.user}')
            target_guild = bot.get_guild(CONFIG["target_server_id"])
            if target_guild:
                print(f'✅ Подключен к целевому серверу: {target_guild.name}')
                self.bot_ready = True
            else:
                print(f'❌ Целевой сервер не найден: {CONFIG["target_server_id"]}')
        
        return bot
    
    def setup_user_client(self):
        """Настройка Discum клиента"""
        try:
            self.user_client = discum.Client(token=CONFIG["user_token"], log=False)
            
            @self.user_client.gateway.command
            def on_ready(resp):
                """Обработчик готовности"""
                if resp.event.ready:
                    if not self.user_ready:
                        # Получаем информацию о пользователе из респонса
                        user_data = resp.parsed.auto()
                        if 'user' in user_data:
                            user_info = user_data['user']
                            username = user_info.get('username', 'Unknown')
                            discriminator = user_info.get('discriminator', '0000')
                            print(f'👤 User client готов: {username}#{discriminator}')
                        else:
                            print(f'👤 User client готов')
                        
                        # Проверяем доступность исходного сервера
                        try:
                            guilds = self.user_client.getGuilds().json()
                            source_guild = None
                            for guild in guilds:
                                if str(guild["id"]) == str(CONFIG["source_server_id"]):
                                    source_guild = guild
                                    break
                            
                            if source_guild:
                                print(f'✅ Подключен к исходному серверу: {source_guild["name"]}')
                                self.user_ready = True
                            else:
                                print(f'❌ Исходный сервер не найден: {CONFIG["source_server_id"]}')
                        except Exception as e:
                            print(f"⚠️ Ошибка получения серверов: {e}")
                            self.user_ready = True  # Продолжаем работу
            
            @self.user_client.gateway.command
            def on_message(resp):
                """Обработчик сообщений"""
                if resp.event.message:
                    try:
                        message = resp.parsed.auto()
                        if self.should_mirror_message(message):
                            # Запускаем пересылку в отдельном потоке
                            asyncio.run_coroutine_threadsafe(
                                self.mirror_message(message), 
                                self.bot.loop
                            )
                    except Exception as e:
                        print(f"⚠️ Ошибка обработки сообщения: {e}")
            
            return True
            
        except Exception as e:
            print(f"❌ Ошибка настройки user client: {e}")
            return False
    
    def should_mirror_message(self, message):
        """Проверка, нужно ли пересылать сообщение"""
        try:
            # Игнорируем системные сообщения
            if not message or (not message.get('content') and not message.get('attachments')):
                return False
            
            # Проверяем сервер
            guild_id = str(message.get('guild_id', ''))
            if guild_id != str(CONFIG["source_server_id"]):
                return False
            
            # Проверяем канал
            channel_id = str(message.get('channel_id', ''))
            if channel_id not in CONFIG["channel_mapping"]:
                return False
            
            # Фильтр ботов
            author = message.get('author', {})
            if CONFIG["filter_bots"] and author.get('bot', False):
                return False
            
            # Игнорируем свои сообщения (пробуем получить ID пользователя)
            try:
                if hasattr(self.user_client, 'user') and self.user_client.user:
                    user_id = self.user_client.user.get('id')
                    if author.get('id') == user_id:
                        return False
            except:
                pass  # Если не удалось получить ID пользователя, продолжаем
            
            return True
            
        except Exception as e:
            print(f"⚠️ Ошибка проверки сообщения: {e}")
            return False
    
    async def mirror_message(self, message):
        """Пересылка сообщения через bot"""
        try:
            channel_id = str(message.get('channel_id', ''))
            target_channel_id = CONFIG["channel_mapping"].get(channel_id)
            
            if not target_channel_id:
                return
            
            target_channel = self.bot.get_channel(target_channel_id)
            if not target_channel:
                print(f"❌ Целевой канал не найден: {target_channel_id}")
                return
            
            # Получаем или создаем webhook
            webhook = await self.get_or_create_webhook(target_channel)
            
            # Подготовка данных
            author = message.get('author', {})
            content = CONFIG["prefix_format"] + (message.get('content', '') or '')
            
            # Обрезаем контент если слишком длинный
            if len(content) > 2000:
                content = content[:1997] + "..."
            
            # Подготовка embeds
            embeds = []
            if CONFIG["include_embeds"] and message.get('embeds'):
                embeds = [discord.Embed.from_dict(embed) for embed in message['embeds'][:10]]
            
            # Подготовка файлов
            files = []
            if CONFIG["include_attachments"] and message.get('attachments'):
                files = await self.download_attachments(message['attachments'])
            
            # Отладочная информация об авторе
            if CONFIG["debug_avatars"]:
                print(f"🔍 Автор сообщения:")
                print(f"   ID: {author.get('id')}")
                print(f"   Username: {author.get('username')}")
                print(f"   Global name: {author.get('global_name')}")
                print(f"   Avatar ID: {author.get('avatar')}")
                print(f"   Discriminator: {author.get('discriminator')}")
            
            if webhook:
                # Отправка через webhook (сохраняет имя и аватар)
                author_id = author.get('id')
                avatar_id = author.get('avatar')
                
                # Формируем URL аватара
                avatar_url = None
                if avatar_id and author_id:
                    # Проверяем, является ли аватар анимированным (GIF)
                    if avatar_id.startswith('a_'):
                        avatar_url = f"https://cdn.discordapp.com/avatars/{author_id}/{avatar_id}.gif"
                    else:
                        avatar_url = f"https://cdn.discordapp.com/avatars/{author_id}/{avatar_id}.png"
                else:
                    # Если нет кастомного аватара, используем дефолтный
                    if author_id:
                        # Discord использует discriminator для дефолтных аватаров
                        discriminator = author.get('discriminator', '0000')
                        if discriminator == '0' or discriminator == '0000':
                            # Новая система username (без discriminator)
                            default_avatar = int(author_id) % 5
                        else:
                            # Старая система с discriminator
                            default_avatar = int(discriminator) % 5
                        avatar_url = f"https://cdn.discordapp.com/embed/avatars/{default_avatar}.png"
                
                # Получаем display_name (nickname на сервере)
                username = author.get('global_name') or author.get('username', 'Unknown')
                
                await webhook.send(
                    content=content if content.strip() else None,
                    username=username[:80],  # Discord лимит на имя
                    avatar_url=avatar_url,
                    embeds=embeds,
                    files=files,
                    wait=False
                )
                
                if CONFIG["log_messages"]:
                    print(f"📤 Webhook: {username} | Avatar: {'✅' if avatar_url else '❌'}")
            else:
                # Fallback: отправка как обычное сообщение бота
                await self.send_as_bot_message(target_channel, message, files)
            
            if CONFIG["log_messages"]:
                try:
                    source_channel_info = self.user_client.getChannel(channel_id).json()
                    source_channel_name = source_channel_info.get('name', 'unknown')
                except:
                    source_channel_name = 'unknown'
                
                print(f"📤 Переслано: {author.get('username', 'Unknown')} | #{source_channel_name} -> #{target_channel.name}")
        
        except Exception as e:
            print(f"❌ Ошибка пересылки: {e}")
            import traceback
            traceback.print_exc()
    
    async def get_or_create_webhook(self, channel):
        """Получить или создать webhook"""
        if channel.id in self.webhook_cache:
            webhook = self.webhook_cache[channel.id]
            try:
                await webhook.fetch()
                return webhook
            except (discord.NotFound, discord.HTTPException):
                del self.webhook_cache[channel.id]
        
        try:
            webhooks = await channel.webhooks()
            webhook = discord.utils.get(webhooks, name=CONFIG["webhook_name"])
            
            if not webhook:
                webhook = await channel.create_webhook(name=CONFIG["webhook_name"])
                print(f"🔗 Создан webhook в #{channel.name}")
            
            self.webhook_cache[channel.id] = webhook
            return webhook
            
        except discord.Forbidden:
            print(f"❌ Нет прав для webhook в #{channel.name}")
            return None
        except Exception as e:
            print(f"❌ Ошибка webhook: {e}")
            return None
    
    async def send_as_bot_message(self, channel, original_message, files):
        """Fallback: отправка как сообщение бота"""
        try:
            author = original_message.get('author', {})
            
            # Пробуем получить название канала
            try:
                source_channel_info = self.user_client.getChannel(str(original_message.get('channel_id', ''))).json()
                source_channel_name = source_channel_info.get('name', 'unknown')
            except:
                source_channel_name = 'unknown'
            
            content = f"**{author.get('username', 'Unknown')}** (из #{source_channel_name}):\n"
            content += CONFIG["prefix_format"] + (original_message.get('content') or "*сообщение без текста*")
            
            if len(content) > 2000:
                content = content[:1997] + "..."
            
            await channel.send(content, files=files)
            
        except Exception as e:
            print(f"❌ Ошибка fallback отправки: {e}")
    
    async def download_attachments(self, attachments):
        """Скачивание вложений"""
        files = []
        for attachment in attachments[:10]:
            try:
                # Проверяем размер
                if attachment.get('size', 0) > 8 * 1024 * 1024:  # 8MB лимит
                    print(f"⚠️ Файл слишком большой: {attachment.get('filename')}")
                    continue
                
                # Скачиваем файл
                response = requests.get(attachment['url'])
                if response.status_code == 200:
                    file_data = BytesIO(response.content)
                    files.append(discord.File(
                        fp=file_data,
                        filename=attachment.get('filename', 'unknown')
                    ))
                
            except Exception as e:
                print(f"❌ Ошибка скачивания {attachment.get('filename')}: {e}")
        
        return files
    
    def add_bot_commands(self):
        """Добавление команд управления"""
        @self.bot.command(name='status')
        @commands.has_permissions(administrator=True)
        async def status(ctx):
            embed = discord.Embed(title="📊 Mirror Status", color=0x00ff00)
            
            user_status = "✅ Подключен" if self.user_ready else "❌ Не подключен"
            bot_status = "✅ Подключен" if self.bot_ready else "❌ Не подключен"
            
            embed.add_field(name="User Client", value=user_status, inline=True)
            embed.add_field(name="Bot Client", value=bot_status, inline=True)
            embed.add_field(name="Маппинг каналов", value=len(CONFIG["channel_mapping"]), inline=True)
            
            await ctx.send(embed=embed)
        
        @self.bot.command(name='add')
        @commands.has_permissions(administrator=True)
        async def add_mapping(ctx, source_id: str, target_id: int):
            CONFIG["channel_mapping"][source_id] = target_id
            await ctx.send(f"✅ Добавлен маппинг: <#{source_id}> -> <#{target_id}>")
        
        @self.bot.command(name='remove')
        @commands.has_permissions(administrator=True)
        async def remove_mapping(ctx, source_id: str):
            if source_id in CONFIG["channel_mapping"]:
                target_id = CONFIG["channel_mapping"].pop(source_id)
                await ctx.send(f"✅ Удален маппинг: <#{source_id}> -> <#{target_id}>")
            else:
                await ctx.send("❌ Маппинг не найден")
        
        @self.bot.command(name='list')
        @commands.has_permissions(administrator=True)
        async def list_mappings(ctx):
            if not CONFIG["channel_mapping"]:
                await ctx.send("📝 Нет активных маппингов")
                return
            
            embed = discord.Embed(title="📝 Маппинг каналов", color=0x0099ff)
            for source_id, target_id in CONFIG["channel_mapping"].items():
                embed.add_field(
                    name=f"<#{source_id}>",
                    value=f"-> <#{target_id}>",
                    inline=False
                )
            await ctx.send(embed=embed)
        
        @self.bot.command(name='test_webhook')
        @commands.has_permissions(administrator=True)
        async def test_webhook(ctx):
            """Тест webhook с аватаром"""
            try:
                webhook = await self.get_or_create_webhook(ctx.channel)
                if webhook:
                    await webhook.send(
                        content="🧪 Тест webhook с аvatаром!",
                        username="Test User",
                        avatar_url="https://cdn.discordapp.com/embed/avatars/0.png"
                    )
                    await ctx.send("✅ Webhook тест отправлен!")
                else:
                    await ctx.send("❌ Не удалось создать webhook")
            except Exception as e:
                await ctx.send(f"❌ Ошибка теста: {e}")
    
    def run_user_client(self):
        """Запуск user client в отдельном потоке"""
        try:
            print("🔄 Connecting user client...")
            self.user_client.gateway.run(auto_reconnect=True)
        except Exception as e:
            print(f"❌ Ошибка user client: {e}")
            import traceback
            traceback.print_exc()
    
    async def run(self):
        """Главная функция запуска"""
        print("🚀 Запуск Discord Mirror (Discum + Discord.py)...")
        print("⚠️  ВНИМАНИЕ: Использование user token нарушает ToS Discord!")
        
        # Проверяем токены
        if CONFIG["user_token"] == "YOUR_USER_TOKEN_HERE":
            print("❌ Укажи user_token в CONFIG!")
            return
        
        if CONFIG["bot_token"] == "YOUR_BOT_TOKEN_HERE":
            print("❌ Укажи bot_token в CONFIG!")
            return
        
        # Настраиваем user client
        print("🔄 Настройка user client...")
        if not self.setup_user_client():
            print("❌ Не удалось настроить user client!")
            return
        
        # Добавляем команды к боту
        self.add_bot_commands()
        
        print("🔄 Запуск user client...")
        # Запускаем user client в отдельном потоке
        user_thread = threading.Thread(target=self.run_user_client, daemon=True)
        user_thread.start()
        
        # Ждем инициализации user client
        print("⏳ Ожидание инициализации user client...")
        await asyncio.sleep(5)
        
        print("🔄 Запуск bot client...")
        # Запускаем бота
        try:
            await self.bot.start(CONFIG["bot_token"])
        except Exception as e:
            print(f"❌ Ошибка запуска бота: {e}")

# Инициализация и запуск
mirror = DiscordMirror()

async def main():
    try:
        await mirror.run()
    except KeyboardInterrupt:
        print("🛑 Остановка...")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
    finally:
        if mirror.user_client:
            mirror.user_client.gateway.close()
        if mirror.bot:
            await mirror.bot.close()

if __name__ == "__main__":
    asyncio.run(main())