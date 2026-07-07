import discord
from discord.ext import commands
from datetime import datetime, timezone
from typing import Optional, List
import os
import asyncio

class Logs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.LOG_CHANNEL_ID = 1379613135631290459  # ID канала для логов
        self.channel_messages_cache = {}  # Кэш для хранения сообщений каналов
        self.cache_task = None  # Задача для периодического обновления кэша
        
        # Создаем директорию для логов при инициализации
        try:
            os.makedirs("logs/deleted_channels", exist_ok=True)
            print("[ИНФО] Директория для логов успешно создана или уже существует")
        except Exception as e:
            print(f"[КРИТИЧЕСКАЯ ОШИБКА] Не удалось создать директорию для логов: {str(e)}")

    async def cog_load(self):
        """Вызывается при загрузке расширения"""
        print("[ИНФО] Запуск начального кэширования каналов...")
        # Кэшируем все существующие каналы при запуске
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                print(f"[ИНФО] Начальное кэширование канала: {channel.name}")
                await self.cache_channel_messages(channel)
        
        self.cache_task = self.bot.loop.create_task(self.cache_channels_periodically())

    async def cog_unload(self):
        """Вызывается при выгрузке расширения"""
        if self.cache_task:
            self.cache_task.cancel()
            
    async def cache_channels_periodically(self):
        """Периодически обновляет кэш сообщений всех каналов"""
        while True:
            try:
                for guild in self.bot.guilds:
                    for channel in guild.text_channels:
                        if channel.id not in self.channel_messages_cache:
                            await self.cache_channel_messages(channel)
                print("[ИНФО] Завершено периодическое обновление кэша каналов")
                await asyncio.sleep(300)  # Обновляем каждые 5 минут
            except Exception as e:
                print(f"[ОШИБКА] Ошибка при периодическом кэшировании: {str(e)}")
                await asyncio.sleep(60)  # При ошибке ждем минуту перед повторной попыткой
            
    async def cache_channel_messages(self, channel):
        """Кэширует сообщения канала"""
        if not isinstance(channel, discord.TextChannel):
            print(f"[ПРОПУСК] {channel.name} не является текстовым каналом")
            return False
        
        # Проверяем, не слишком ли свежий кэш
        if channel.id in self.channel_messages_cache:
            cache_time = self.channel_messages_cache[channel.id]['timestamp']
            cache_age = (datetime.now() - cache_time).total_seconds()
            if cache_age < 300:  # Кэш свежее 5 минут
                print(f"[ИНФО] Используем существующий кэш для канала {channel.name} (возраст: {cache_age:.1f} сек)")
                return True
            
        try:
            messages = []
            message_count = 0
            print(f"[ИНФО] Кэширование сообщений канала {channel.name}")
            
            try:
                async for message in channel.history(limit=None, oldest_first=True):
                    message_count += 1
                    messages.append({
                        'timestamp': message.created_at,
                        'author': str(message.author),
                        'author_id': message.author.id,
                        'content': message.content,
                        'attachments': [att.url for att in message.attachments]
                    })
            except discord.Forbidden:
                print(f"[ОШИБКА] Нет прав для чтения сообщений в канале {channel.name}")
                return False
            except Exception as e:
                print(f"[ОШИБКА] Не удалось получить историю канала {channel.name}: {str(e)}")
                return False
            
            self.channel_messages_cache[channel.id] = {
                'name': channel.name,
                'messages': messages,
                'timestamp': datetime.now()
            }
            
            print(f"[ИНФО] Кэшировано {message_count} сообщений из канала {channel.name}")
            return True
            
        except Exception as e:
            print(f"[ОШИБКА] Ошибка при кэшировании канала {channel.name}: {str(e)}")
            return False

    async def send_log(self, embed: discord.Embed, file: discord.File = None):
        """Отправляет лог в канал логов"""
        try:
            log_channel = self.bot.get_channel(self.LOG_CHANNEL_ID)
            if not log_channel:
                print(f"[ОШИБКА] Канал логов с ID {self.LOG_CHANNEL_ID} не найден!")
                return
                
            if file:
                print(f"[ИНФО] Отправка лога с файлом {file.filename}")
                await log_channel.send(embed=embed, file=file)
            else:
                await log_channel.send(embed=embed)
                
        except Exception as e:
            print(f"[КРИТИЧЕСКАЯ ОШИБКА] Не удалось отправить лог: {str(e)}")
            import traceback
            print(f"[СТЕК ОШИБКИ]\n{traceback.format_exc()}")

    async def save_channel_messages(self, channel_id, channel_name):
        """Сохраняет историю сообщений канала из кэша и возвращает путь к файлу"""
        if channel_id not in self.channel_messages_cache:
            print(f"[ОШИБКА] Не найдены кэшированные сообщения для канала {channel_name}")
            print(f"[ДЕБАГ] Текущие каналы в кэше: {', '.join(str(k) for k in self.channel_messages_cache.keys())}")
            if self.channel_messages_cache:
                for k, v in self.channel_messages_cache.items():
                    print(f"[ДЕБАГ] Канал {v['name']}: {len(v['messages'])} сообщений")
            return None
            
        channel_data = self.channel_messages_cache[channel_id]
        print(f"[ИНФО] Найден кэш для канала {channel_name} с {len(channel_data['messages'])} сообщениями")
        
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"logs/deleted_channels/{channel_name}_{timestamp}.txt"
        
        try:
            os.makedirs("logs/deleted_channels", exist_ok=True)
        except Exception as e:
            print(f"[ОШИБКА] Не удалось создать директорию для сохранения: {str(e)}")
            return None
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(f"Канал: {channel_name} (ID: {channel_id})\n")
                f.write(f"Дата сохранения: {datetime.now()}\n")
                f.write(f"Количество сообщений: {len(channel_data['messages'])}\n\n")
                f.write("=== Содержимое канала ===\n\n")
                
                for msg in channel_data['messages']:
                    f.write(f"[{msg['timestamp']}] {msg['author']} ({msg['author_id']}):\n")
                    if msg['content']:
                        f.write(f"{msg['content']}\n")
                    if msg['attachments']:
                        f.write("Вложения:\n")
                        for att in msg['attachments']:
                            f.write(f"- {att}\n")
                    f.write("\n")  # Пустая строка между сообщениями
            
            print(f"[УСПЕХ] Сообщения канала {channel_name} успешно сохранены в файл {filename}")
            # Очищаем кэш после сохранения
            del self.channel_messages_cache[channel_id]
            return filename
            
        except Exception as e:
            print(f"[ОШИБКА] Не удалось сохранить сообщения в файл: {str(e)}")
            return None
        
        try:
            os.makedirs("logs/deleted_channels", exist_ok=True)
        except Exception as e:
            print(f"[ОШИБКА] Не удалось создать директорию для сохранения: {str(e)}")
            return None
        
        try:
            messages = []
            # Сначала получаем все сообщения и сохраняем их в память
            try:
                message_count = 0
                async for message in channel.history(limit=None, oldest_first=True):
                    message_count += 1
                    messages.append({
                        'timestamp': message.created_at,
                        'author': str(message.author),
                        'author_id': message.author.id,
                        'content': message.content,
                        'attachments': [att.url for att in message.attachments]
                    })
                print(f"[ИНФО] Успешно получено {message_count} сообщений из канала {channel.name}")
            except discord.NotFound:
                print(f"[ОШИБКА] Канал {channel.name} не найден (уже удален)")
                return None
            except discord.Forbidden:
                print(f"[ОШИБКА] Нет прав для чтения сообщений в канале {channel.name}")
                return None
            except Exception as e:
                print(f"[ОШИБКА] Не удалось получить историю сообщений из канала {channel.name}: {str(e)}")
                return None
            
            # Если есть сообщения, сохраняем их в файл
            if messages:
                try:
                    with open(filename, 'w', encoding='utf-8') as f:
                        f.write(f"Канал: {channel.name} (ID: {channel.id})\n")
                        f.write(f"Дата сохранения: {datetime.now()}\n")
                        f.write(f"Количество сообщений: {len(messages)}\n\n")
                        f.write("=== Содержимое канала ===\n\n")
                        
                        for msg in messages:
                            f.write(f"[{msg['timestamp']}] {msg['author']} ({msg['author_id']}):\n")
                            if msg['content']:
                                f.write(f"{msg['content']}\n")
                            if msg['attachments']:
                                f.write("Вложения:\n")
                                for att in msg['attachments']:
                                    f.write(f"- {att}\n")
                            f.write("\n")  # Пустая строка между сообщениями
                    
                    print(f"[УСПЕХ] Сообщения канала {channel.name} успешно сохранены в файл {filename}")
                    return filename
                except Exception as e:
                    print(f"[ОШИБКА] Не удалось сохранить сообщения в файл {filename}: {str(e)}")
                    return None
            else:
                print(f"[ИНФО] Канал {channel.name} не содержит сообщений для сохранения")
                return None
        except Exception as e:
            print(f"[КРИТИЧЕСКАЯ ОШИБКА] При сохранении истории канала {channel.name}: {str(e)}")
            import traceback
            print(f"[СТЕК ОШИБКИ]\n{traceback.format_exc()}")
        return None

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        """Отслеживает изменения в каналах для кэширования перед удалением"""
        # Если канал получил новые разрешения, которые могут указывать на скорое удаление
        if isinstance(before, discord.TextChannel) and before.permissions_for(before.guild.me).read_messages:
            if not after.permissions_for(after.guild.me).read_messages:
                print(f"[ИНФО] Обнаружено изменение прав канала {before.name}, кэширование сообщений...")
                await self.cache_channel_messages(before)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Обновляет кэш при новых сообщениях"""
        if isinstance(message.channel, discord.TextChannel):
            channel_id = message.channel.id
            
            # Инициализируем кэш для канала, если его нет
            if channel_id not in self.channel_messages_cache:
                print(f"[ИНФО] Инициализация кэша для канала {message.channel.name}")
                try:
                    messages = []
                    async for old_message in message.channel.history(limit=None, oldest_first=True):
                        messages.append({
                            'timestamp': old_message.created_at,
                            'author': str(old_message.author),
                            'author_id': old_message.author.id,
                            'content': old_message.content,
                            'attachments': [att.url for att in old_message.attachments]
                        })
                    self.channel_messages_cache[channel_id] = {
                        'name': message.channel.name,
                        'messages': messages,
                        'timestamp': datetime.now()
                    }
                    print(f"[ИНФО] Загружено {len(messages)} существующих сообщений из канала {message.channel.name}")
                except Exception as e:
                    print(f"[ОШИБКА] Не удалось загрузить историю канала {message.channel.name}: {str(e)}")
                    self.channel_messages_cache[channel_id] = {
                        'name': message.channel.name,
                        'messages': [],
                        'timestamp': datetime.now()
                    }
            
            # Добавляем новое сообщение в кэш
            if not message.author.bot:  # Только сообщения от пользователей
                new_message = {
                    'timestamp': message.created_at,
                    'author': str(message.author),
                    'author_id': message.author.id,
                    'content': message.content,
                    'attachments': [att.url for att in message.attachments]
                }
                
                self.channel_messages_cache[channel_id]['messages'].append(new_message)
                message_count = len(self.channel_messages_cache[channel_id]['messages'])
                print(f"[ИНФО] Новое сообщение добавлено в кэш канала {message.channel.name} (всего: {message_count})")

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        """Логирование удаления сообщений"""
        if message.author.bot:
            return

        # Получаем информацию о том, кто удалил сообщение
        deleter = message.author
        try:
            async for entry in message.guild.audit_logs(limit=5, action=discord.AuditLogAction.message_delete):
                # Проверяем, совпадает ли ID канала и временная метка
                if (entry.extra.channel.id == message.channel.id and 
                    entry.target.id == message.author.id and 
                    (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 2):
                    deleter = entry.user
                    break
        except (discord.Forbidden, discord.HTTPException):
            pass  # Если нет прав на просмотр audit logs или другая ошибка

        embed = discord.Embed(
            title="🗑️ Удаление сообщения",
            description=f"В канале {message.channel.mention}",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        
        embed.add_field(name="Автор", value=f"{message.author.mention} ({message.author}) [{message.author.id}]", inline=False)
        embed.add_field(name="Удалил", value=f"{deleter.mention} ({deleter}) [{deleter.id}]", inline=False)
        
        # Добавляем контент сообщения
        if message.content:
            if len(message.content) > 1024:
                chunks = [message.content[i:i+1024] for i in range(0, len(message.content), 1024)]
                for i, chunk in enumerate(chunks):
                    embed.add_field(name=f"Содержание {i+1}", value=chunk, inline=False)
            else:
                embed.add_field(name="Содержание", value=message.content, inline=False)

        # Добавляем вложения
        if message.attachments:
            attachments_text = []
            for i, attachment in enumerate(message.attachments, 1):
                attachments_text.append(f"[Вложение {i}]({attachment.proxy_url})")
            embed.add_field(name="Вложения", value="\n".join(attachments_text), inline=False)

        embed.set_footer(text="Адвокатское бюро P.A.C.T • https://discord.gg/feKD2KYTSs")
        await self.send_log(embed)

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        """Логирование редактирования сообщений"""
        if before.author.bot or before.content == after.content:
            return

        embed = discord.Embed(
            title="📝 Редактирование сообщения",
            description=f"В канале {before.channel.mention}\n[Перейти к сообщению]({before.jump_url})",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        embed.add_field(name="Автор", value=f"{before.author.mention} ({before.author}) [{before.author.id}]", inline=False)
        
        # Добавляем старое и новое содержание
        if len(before.content) > 1024:
            chunks = [before.content[i:i+1024] for i in range(0, len(before.content), 1024)]
            for i, chunk in enumerate(chunks):
                embed.add_field(name=f"До {i+1}", value=chunk, inline=False)
        else:
            embed.add_field(name="До", value=before.content or "[Пусто]", inline=False)

        if len(after.content) > 1024:
            chunks = [after.content[i:i+1024] for i in range(0, len(after.content), 1024)]
            for i, chunk in enumerate(chunks):
                embed.add_field(name=f"После {i+1}", value=chunk, inline=False)
        else:
            embed.add_field(name="После", value=after.content or "[Пусто]", inline=False)

        # Добавляем информацию о вложениях
        if before.attachments or after.attachments:
            before_attachments = [f"[Вложение {i}]({a.proxy_url})" for i, a in enumerate(before.attachments, 1)]
            after_attachments = [f"[Вложение {i}]({a.proxy_url})" for i, a in enumerate(after.attachments, 1)]
            
            if before_attachments:
                embed.add_field(name="Вложения до", value="\n".join(before_attachments), inline=False)
            if after_attachments:
                embed.add_field(name="Вложения после", value="\n".join(after_attachments), inline=False)

        embed.set_footer(text="Адвокатское бюро P.A.C.T • https://discord.gg/feKD2KYTSs")
        await self.send_log(embed)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Логирование входа на сервер"""
        # Получаем информацию о приглашении
        guild = member.guild
        invites_after = await guild.invites()
        
        # Ищем использованное приглашение
        inviter = None
        invite_used = None
        
        for invite in invites_after:
            if invite.uses > 0:
                try:
                    inviter = invite.inviter
                    invite_used = invite
                    break
                except:
                    continue

        embed = discord.Embed(
            title="📥 Новый участник",
            description=f"{member.mention} присоединился к серверу",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        
        embed.add_field(name="Участник", value=f"{member} ({member.id})", inline=False)
        embed.add_field(name="Аккаунт создан", value=member.created_at.strftime("%d.%m.%Y %H:%M:%S"), inline=False)
        
        if inviter and invite_used:
            embed.add_field(
                name="Приглашение",
                value=f"Пригласил: {inviter} ({inviter.id})\nСсылка: discord.gg/{invite_used.code}\nИспользований: {invite_used.uses}",
                inline=False
            )
        
        embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
        embed.set_footer(text="Адвокатское бюро P.A.C.T • https://discord.gg/feKD2KYTSs")
        await self.send_log(embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Логирование выхода с сервера"""
        embed = discord.Embed(
            title="📤 Участник покинул сервер",
            description=f"{member.mention} покинул сервер",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        
        embed.add_field(name="Участник", value=f"{member} ({member.id})", inline=False)
        embed.add_field(name="Присоединился", value=member.joined_at.strftime("%d.%m.%Y %H:%M:%S"), inline=False)
        
        # Проверяем, был ли пользователь кикнут/забанен
        async for entry in member.guild.audit_logs(limit=1):
            if entry.target.id == member.id and entry.created_at.timestamp() > datetime.now().timestamp() - 5:
                if entry.action == discord.AuditLogAction.kick:
                    embed.add_field(name="Причина", value=f"Кикнут модератором {entry.user} ({entry.user.id})", inline=False)
                elif entry.action == discord.AuditLogAction.ban:
                    embed.add_field(name="Причина", value=f"Забанен модератором {entry.user} ({entry.user.id})", inline=False)
                break
        else:
            embed.add_field(name="Причина", value="Покинул сервер самостоятельно", inline=False)

        embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
        embed.set_footer(text="Адвокатское бюро P.A.C.T • https://discord.gg/feKD2KYTSs")
        await self.send_log(embed)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        """Логирование создания каналов"""
        if isinstance(channel, discord.TextChannel):
            print(f"[ИНФО] Новый канал создан: {channel.name}. Инициализация кэша...")
            await self.cache_channel_messages(channel)
            
        async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_create):
            if entry.target.id == channel.id:
                embed = discord.Embed(
                    title="📝 Создание канала",
                    description=f"Создан новый канал {channel.mention}",
                    color=discord.Color.green(),
                    timestamp=datetime.now()
                )
                
                embed.add_field(name="Название", value=channel.name, inline=True)
                embed.add_field(name="Тип", value=str(channel.type), inline=True)
                embed.add_field(name="Создал", value=f"{entry.user.mention} ({entry.user}) [{entry.user.id}]", inline=False)
                
                embed.set_footer(text="Адвокатское бюро P.A.C.T • https://discord.gg/feKD2KYTSs")
                await self.send_log(embed)
                break

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        """Логирование удаления каналов"""
        print(f"[ИНФО] Начало обработки удаления канала: {channel.name}")
        
        # Пытаемся сохранить историю из кэша
        history_file = None
        if isinstance(channel, discord.TextChannel):
            print(f"[ИНФО] Попытка сохранить историю текстового канала {channel.name}")
            history_file = await self.save_channel_messages(channel.id, channel.name)
            if history_file:
                print(f"[УСПЕХ] История канала сохранена в файл: {history_file}")
            else:
                print(f"[ОШИБКА] Не удалось сохранить историю канала {channel.name}")

        try:
            async for entry in channel.guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
                if entry.target.id == channel.id:
                    embed = discord.Embed(
                        title="🗑️ Удаление канала",
                        description=f"Удален канал {channel.name}",
                        color=discord.Color.red(),
                        timestamp=datetime.now()
                    )
                    
                    embed.add_field(name="Название", value=channel.name, inline=True)
                    embed.add_field(name="Тип", value=str(channel.type), inline=True)
                    embed.add_field(name="Удалил", value=f"{entry.user.mention} ({entry.user}) [{entry.user.id}]", inline=False)
                    embed.set_footer(text="Адвокатское бюро P.A.C.T • https://discord.gg/feKD2KYTSs")
                    
                    if history_file:
                        print(f"[ИНФО] Подготовка файла {history_file} для отправки")
                        try:
                            file = discord.File(history_file, filename=f"{channel.name}_messages.txt")
                            embed.add_field(name="Архив сообщений", value="Сообщения сохранены в прикрепленном файле", inline=False)
                            await self.send_log(embed, file=file)
                            print(f"[УСПЕХ] Лог с файлом истории успешно отправлен")
                        except Exception as e:
                            print(f"[ОШИБКА] Не удалось прикрепить файл истории: {str(e)}")
                            await self.send_log(embed)
                    else:
                        if isinstance(channel, discord.TextChannel):
                            embed.add_field(name="Архив сообщений", value="Не удалось сохранить сообщения канала", inline=False)
                        await self.send_log(embed)
                    break
                    
        except Exception as e:
            print(f"[КРИТИЧЕСКАЯ ОШИБКА] При обработке удаления канала {channel.name}: {str(e)}")
            import traceback
            print(f"[СТЕК ОШИБКИ]\n{traceback.format_exc()}")

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        """Логирование изменения ролей"""
        async for entry in after.guild.audit_logs(limit=1, action=discord.AuditLogAction.role_update):
            if entry.target.id == after.id:
                embed = discord.Embed(
                    title="📝 Изменение роли",
                    description=f"Роль {after.mention} была изменена",
                    color=after.color,
                    timestamp=datetime.now()
                )
                
                changes = []
                if before.name != after.name:
                    changes.append(f"Название: {before.name} → {after.name}")
                if before.color != after.color:
                    changes.append(f"Цвет: {before.color} → {after.color}")
                if before.permissions != after.permissions:
                    changes.append("Изменены права роли")
                if before.hoist != after.hoist:
                    changes.append(f"Отображение отдельно: {'Да' if after.hoist else 'Нет'}")
                if before.mentionable != after.mentionable:
                    changes.append(f"Упоминаемость: {'Да' if after.mentionable else 'Нет'}")
                
                embed.add_field(name="Изменения", value="\n".join(changes), inline=False)
                embed.add_field(name="Модератор", value=f"{entry.user} ({entry.user.id})", inline=False)
                
                embed.set_footer(text="Адвокатское бюро P.A.C.T • https://discord.gg/feKD2KYTSs")
                await self.send_log(embed)
                break

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Логирование изменения ролей участника"""
        # Проверяем, изменились ли роли
        if before.roles != after.roles:
            # Находим добавленные и удаленные роли
            removed_roles = set(before.roles) - set(after.roles)
            added_roles = set(after.roles) - set(before.roles)

            async for entry in after.guild.audit_logs(limit=1, action=discord.AuditLogAction.member_role_update):
                if entry.target.id == after.id:
                    embed = discord.Embed(
                        title="👥 Изменение ролей участника",
                        color=discord.Color.blue(),
                        timestamp=datetime.now()
                    )
                    
                    embed.add_field(name="Участник", value=f"{after.mention} ({after}) [{after.id}]", inline=False)
                    embed.add_field(name="Модератор", value=f"{entry.user.mention} ({entry.user}) [{entry.user.id}]", inline=False)
                    
                    if removed_roles:
                        roles_text = ", ".join(role.mention for role in removed_roles)
                        embed.add_field(name="❌ Удалены роли", value=roles_text, inline=False)
                    
                    if added_roles:
                        roles_text = ", ".join(role.mention for role in added_roles)
                        embed.add_field(name="✅ Добавлены роли", value=roles_text, inline=False)
                    
                    embed.set_thumbnail(url=after.avatar.url if after.avatar else after.default_avatar.url)
                    embed.set_footer(text="Адвокатское бюро P.A.C.T • https://discord.gg/feKD2KYTSs")
                    await self.send_log(embed)
                    break

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        """Логирование разбана пользователей"""
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.unban):
            if entry.target.id == user.id:
                embed = discord.Embed(
                    title="🔓 Разбан пользователя",
                    color=discord.Color.green(),
                    timestamp=datetime.now()
                )
                embed.add_field(name="Пользователь", value=f"{user.mention} ({user}) [{user.id}]", inline=False)
                embed.add_field(name="Модератор", value=f"{entry.user.mention} ({entry.user}) [{entry.user.id}]", inline=False)
                if entry.reason:
                    embed.add_field(name="Причина", value=entry.reason, inline=False)
                
                embed.set_footer(text="Адвокатское бюро P.A.C.T • https://discord.gg/feKD2KYTSs")
                await self.send_log(embed)
                break

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Логирование изменений пользователя (в том числе мут/размут)"""
        # Проверяем изменение ролей
        if before.roles != after.roles:
            removed_roles = set(before.roles) - set(after.roles)
            added_roles = set(after.roles) - set(before.roles)
            
            async for entry in after.guild.audit_logs(limit=1, action=discord.AuditLogAction.member_role_update):
                if entry.target.id == after.id:
                    # Проверяем роль мута (если она была добавлена или убрана)
                    mute_role = after.guild.get_role(1405628689848340551)  # ID роли мута
                    if mute_role:
                        if mute_role in added_roles:
                            embed = discord.Embed(
                                title="🔇 Мут пользователя",
                                color=discord.Color.yellow(),
                                timestamp=datetime.now()
                            )
                        elif mute_role in removed_roles:
                            embed = discord.Embed(
                                title="🔊 Размут пользователя",
                                color=discord.Color.green(),
                                timestamp=datetime.now()
                            )
                        else:
                            continue  # Если изменение не связано с мутом, пропускаем

                        embed.add_field(name="Пользователь", value=f"{after.mention} ({after}) [{after.id}]", inline=False)
                        embed.add_field(name="Модератор", value=f"{entry.user.mention} ({entry.user}) [{entry.user.id}]", inline=False)
                        if entry.reason:
                            embed.add_field(name="Причина", value=entry.reason, inline=False)
                        
                        embed.set_footer(text="Адвокатское бюро P.A.C.T • https://discord.gg/feKD2KYTSs")
                        await self.send_log(embed)
                    break

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Логирование действий в голосовых каналах"""
        # Создаем эмбед только если есть какие-то изменения
        changes_made = False
        embed = discord.Embed(
            title="🎤 Изменение в голосовом канале",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        embed.add_field(name="Участник", value=f"{member.mention} ({member}) [{member.id}]", inline=False)
        
        # Подключение/отключение от канала
        if before.channel != after.channel:
            changes_made = True
            if not before.channel:
                action = f"Подключился к каналу {after.channel.mention}"
            elif not after.channel:
                action = f"Отключился от канала {before.channel.mention}"
            else:
                action = f"Перешел из канала {before.channel.mention} в канал {after.channel.mention}"
            embed.add_field(name="Действие", value=action, inline=False)
        
        # Самостоятельные действия пользователя
        if before.self_mute != after.self_mute:
            changes_made = True
            embed.add_field(
                name="Микрофон",
                value=f"{member.mention} выключил микрофон" if after.self_mute else f"{member.mention} включил микрофон",
                inline=False
            )
        
        if before.self_deaf != after.self_deaf:
            changes_made = True
            embed.add_field(
                name="Наушники",
                value=f"{member.mention} выключил звук" if after.self_deaf else f"{member.mention} включил звук",
                inline=False
            )
        
        # Действия модераторов
        if before.mute != after.mute or before.deaf != after.deaf:
            async for entry in member.guild.audit_logs(limit=1, action=discord.AuditLogAction.member_update):
                if (entry.target and entry.target.id == member.id and 
                    entry.created_at.timestamp() > datetime.now().timestamp() - 5):
                    changes_made = True
                    
                    if before.mute != after.mute:
                        embed.add_field(
                            name="Серверный мут",
                            value=f"{member.mention} был {'замучен' if after.mute else 'размучен'} модератором {entry.user.mention} ({entry.user}) [{entry.user.id}]",
                            inline=False
                        )
                    
                    if before.deaf != after.deaf:
                        embed.add_field(
                            name="Серверные наушники",
                            value=f"{member.mention} был {'отключен звук' if after.deaf else 'включен звук'} модератором {entry.user.mention} ({entry.user}) [{entry.user.id}]",
                            inline=False
                        )
                    break
        
        if changes_made:
            embed.set_footer(text="Адвокатское бюро P.A.C.T • https://discord.gg/feKD2KYTSs")
            await self.send_log(embed)

async def setup(bot):
    await bot.add_cog(Logs(bot))
