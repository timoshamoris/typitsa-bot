import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
import sqlite3
import json
import os
import asyncio
from typing import Optional, Union

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        
        # ID ролей
        self.MOD_ROLES = [1379547784717402152, 1379547989680324750]  # ID роли модераторов
        self.SUPPORT_ROLE = 1379548354018541619  # ID роли поддержки
        self.MUTE_ROLE = 1405628689848340551  # ID роли мута
        self.LOG_CHANNEL_ID = 1379613135631290459  # ID канала для логов
        
        # Словарь для хранения приглашений
        self.invites = {}
        self.bot.loop.create_task(self.load_invites())
        
        # Инициализация БД
        self.init_db()
        
    async def load_invites(self):
        """Загрузка всех текущих приглашений"""
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try:
                self.invites[guild.id] = {}
                for invite in await guild.invites():
                    self.invites[guild.id][invite.code] = {
                        'uses': invite.uses,
                        'inviter': invite.inviter
                    }
            except discord.Forbidden:
                pass

    def init_db(self):
        """Инициализация базы данных для хранения наказаний"""
        conn = sqlite3.connect('punishments.db')
        cursor = conn.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS punishments (
            user_id TEXT,
            type TEXT,
            reason TEXT,
            duration TEXT,
            moderator_id TEXT,
            timestamp DATETIME
        )
        ''')
        conn.commit()
        conn.close()

    def has_mod_role(self, member):
        """Проверка на наличие роли модератора"""
        return any(role.id in self.MOD_ROLES for role in member.roles)

    def has_support_role(self, member):
        """Проверка на наличие роли поддержки"""
        return any(role.id in [*self.MOD_ROLES, self.SUPPORT_ROLE] for role in member.roles)

    async def log_action(self, embed: discord.Embed):
        """Отправка лога действия в канал логов"""
        channel = self.bot.get_channel(self.LOG_CHANNEL_ID)
        if channel:
            await channel.send(embed=embed)

    async def send_dm(self, user, action: str, reason: str, duration: str = None, moderator = None):
        """Отправка личного сообщения наказанному пользователю"""
        embed = discord.Embed(
            title=f"Вы были {action} на сервере P.A.C.T",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed.add_field(name="Причина", value=reason, inline=False)
        if duration:
            embed.add_field(name="Срок", value=duration, inline=False)
        if moderator:
            embed.add_field(name="Модератор", value=str(moderator), inline=False)
        embed.set_footer(text="Адвокатское бюро P.A.C.T")
        
        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            pass

    async def add_punishment(self, user_id: int, type_: str, reason: str, duration: str, moderator_id: int):
        """Добавление наказания в базу данных"""
        conn = sqlite3.connect('punishments.db')
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO punishments (user_id, type, reason, duration, moderator_id, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (str(user_id), type_, reason, duration, str(moderator_id), datetime.now()))
        conn.commit()
        conn.close()

    @app_commands.command(name="ban", description="Забанить пользователя")
    @app_commands.describe(
        users="ID пользователей через запятую или @упоминание",
        reason="Причина бана",
        duration="Длительность (например: 1d, 7d, permanent)",
        delete_days="Удалить сообщения за последние дни (0-7)"
    )
    async def ban(self, interaction: discord.Interaction, users: str,
                 reason: str, duration: str = "permanent", delete_days: int = 1):
        """Забанить одного или нескольких пользователей"""
        if not self.has_mod_role(interaction.user):
            return await interaction.response.send_message("❌ У вас недостаточно прав", ephemeral=True)
        
        await interaction.response.defer()

        try:
            # Разделяем строку с ID пользователей
            user_ids = [id.strip('<@!>') for id in users.split(',')]
            banned_users = []
            failed_users = []

            for user_id in user_ids:
                try:
                    # Получаем пользователя по ID
                    user = await self.bot.fetch_user(int(user_id))
                    
                    # Отправляем сообщение пользователю
                    await self.send_dm(user, "забанены", reason, duration, interaction.user)
                    
                    # Баним пользователя
                    await interaction.guild.ban(user, reason=reason, delete_message_days=delete_days)
                    
                    # Записываем в БД
                    await self.add_punishment(user.id, "BAN", reason, duration, interaction.user.id)
                    
                    banned_users.append(user)
                except Exception as e:
                    failed_users.append(f"{user_id} ({str(e)})")

            # Создаем эмбед для лога
            embed = discord.Embed(
                title="🔨 Массовый бан",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            
            if banned_users:
                users_text = "\n".join(f"{user.mention} ({user}) [{user.id}]" for user in banned_users)
                embed.add_field(name=f"Забанено пользователей: {len(banned_users)}", value=users_text, inline=False)
            
            if failed_users:
                embed.add_field(name="Ошибки", value="\n".join(failed_users), inline=False)
            
            embed.add_field(name="Причина", value=reason, inline=False)
            embed.add_field(name="Срок", value=duration, inline=False)
            embed.add_field(name="Модератор", value=f"{interaction.user.mention} ({interaction.user}) [{interaction.user.id}]", inline=False)
            
            # Отправляем лог
            await self.log_action(embed)
            await interaction.followup.send(embed=embed)

        except Exception as e:
            await interaction.followup.send(f"❌ Произошла ошибка: {e}", ephemeral=True)
        if not self.has_mod_role(interaction.user):
            return await interaction.response.send_message("❌ У вас недостаточно прав", ephemeral=True)
        
        await interaction.response.defer()

        try:
            # Разделяем строку с ID пользователей
            user_ids = [id.strip() for id in users.split(',')]
            banned_users = []
            failed_users = []

            for user_id in user_ids:
                try:
                    # Пробуем получить пользователя
                    if user_id.isdigit():
                        user = await self.bot.fetch_user(int(user_id))
                    else:
                        # Обработка упоминания
                        user_id = user_id.strip('<@!>')
                        user = await self.bot.fetch_user(int(user_id))

                    # Отправляем сообщение пользователю
                    await self.send_dm(user, "забанены", reason, duration, interaction.user)
            
                    # Баним пользователя
                    await interaction.guild.ban(user, reason=reason, delete_message_days=delete_days)
                    
                    # Записываем в БД
                    await self.add_punishment(user.id, "BAN", reason, duration, interaction.user.id)
                    
                    banned_users.append(user)
                except Exception as e:
                    failed_users.append(f"{user_id} ({str(e)})")
            
            # Создаем эмбед для лога
            embed = discord.Embed(
                title="🔨 Массовый бан",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            
            if banned_users:
                users_text = "\n".join(f"{user.mention} ({user}) [{user.id}]" for user in banned_users)
                embed.add_field(name=f"Забанено пользователей: {len(banned_users)}", value=users_text, inline=False)
            
            if failed_users:
                embed.add_field(name="Ошибки", value="\n".join(failed_users), inline=False)
            
            embed.add_field(name="Причина", value=reason, inline=False)
            embed.add_field(name="Срок", value=duration, inline=False)
            embed.add_field(name="Модератор", value=f"{interaction.user.mention} ({interaction.user}) [{interaction.user.id}]", inline=False)
            
            # Отправляем лог
            await self.log_action(embed)
            await interaction.followup.send(embed=embed)

        except Exception as e:
            await interaction.followup.send(f"❌ Произошла ошибка: {e}", ephemeral=True)

    @app_commands.command(name="unban", description="Разбанить пользователя")
    @app_commands.describe(
        user="ID пользователя",
        reason="Причина разбана"
    )
    async def unban(self, interaction: discord.Interaction, user: str, reason: str):
        if not self.has_mod_role(interaction.user):
            await interaction.response.send_message("❌ У вас недостаточно прав", ephemeral=True)
            return

        try:
            # Получаем пользователя
            user_id = int(user)
            banned_user = await self.bot.fetch_user(user_id)
            
            # Разбаниваем
            await interaction.guild.unban(banned_user, reason=reason)
            
            # Создаем эмбед для лога
            embed = discord.Embed(
                title="🔓 Разбан пользователя",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Пользователь", value=f"{banned_user.mention} ({banned_user}) [{banned_user.id}]", inline=False)
            embed.add_field(name="Причина", value=reason, inline=False)
            embed.add_field(name="Модератор", value=f"{interaction.user.mention} ({interaction.user}) [{interaction.user.id}]", inline=False)
            
            # Отправляем лог
            await self.log_action(embed)
            await interaction.response.send_message(embed=embed)

        except ValueError:
            await interaction.response.send_message("❌ Неверный формат ID", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("❌ Пользователь не найден", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("❌ У бота недостаточно прав", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Произошла ошибка: {e}", ephemeral=True)

    @app_commands.command(name="unmute", description="Размутить пользователя")
    @app_commands.describe(
        member="Пользователь",
        reason="Причина размута"
    )
    async def unmute(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        if not self.has_support_role(interaction.user):
            await interaction.response.send_message("❌ У вас недостаточно прав", ephemeral=True)
            return

        try:
            # Получаем роль мута
            mute_role = interaction.guild.get_role(self.MUTE_ROLE)
            if not mute_role:
                await interaction.response.send_message("❌ Роль мута не найдена", ephemeral=True)
                return
            
            if mute_role not in member.roles:
                await interaction.response.send_message("❌ Пользователь не замучен", ephemeral=True)
                return
            
            # Снимаем мут
            await member.remove_roles(mute_role, reason=reason)

            # Отправляем в ЛС
            try:
                await member.send(f"Вы были размучены на сервере. Причина: {reason}")
            except:
                pass  # If DM fails, continue anyway

            # Создаем эмбед для лога
            embed = discord.Embed(
                title="� Размут пользователя",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Пользователь", value=f"{member.mention} ({member}) [{member.id}]", inline=False)
            embed.add_field(name="Причина", value=reason, inline=False)
            embed.add_field(name="Модератор", value=f"{interaction.user.mention} ({interaction.user}) [{interaction.user.id}]", inline=False)
            
            # Записываем в БД и отправляем лог
            await self.add_punishment(member.id, "UNMUTE", reason, "N/A", interaction.user.id)
            await self.log_action(embed)
            await interaction.response.send_message(embed=embed)

        except discord.Forbidden:
            await interaction.response.send_message("❌ У бота недостаточно прав", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Произошла ошибка: {e}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Произошла ошибка: {e}", ephemeral=True)

    @app_commands.command(name="kick", description="Выгнать пользователя с сервера")
    @app_commands.describe(
        member="Пользователь",
        reason="Причина кика"
    )
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        if not self.has_mod_role(interaction.user):
            return await interaction.response.send_message("❌ У вас недостаточно прав", ephemeral=True)
        
        await interaction.response.defer()

        try:
            # Отправляем сообщение пользователю
            await self.send_dm(member, "выгнаны", reason, None, interaction.user)
            
            # Кикаем пользователя
            await member.kick(reason=reason)
            
            # Создаем эмбед для лога
            embed = discord.Embed(
                title="👢 Кик пользователя",
                color=discord.Color.orange(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Пользователь", value=f"{member} ({member.id})", inline=False)
            embed.add_field(name="Причина", value=reason, inline=False)
            embed.add_field(name="Модератор", value=f"{interaction.user} ({interaction.user.id})", inline=False)
            
            # Записываем в БД
            await self.add_punishment(member.id, "KICK", reason, "N/A", interaction.user.id)
            
            # Отправляем лог
            await self.log_action(embed)
            await interaction.followup.send(embed=embed)

        except discord.Forbidden:
            await interaction.followup.send("❌ У бота недостаточно прав", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Произошла ошибка: {e}", ephemeral=True)

    @app_commands.command(name="mute", description="Замутить пользователя")
    @app_commands.describe(
        member="Пользователь",
        reason="Причина мута",
        duration="Длительность (например: 1h, 24h, 7d)"
    )
    async def mute(self, interaction: discord.Interaction, member: discord.Member,
                  reason: str, duration: str):
        if not self.has_support_role(interaction.user):
            return await interaction.response.send_message("❌ У вас недостаточно прав", ephemeral=True)
        
        await interaction.response.defer()

        try:
            # Конвертируем длительность
            time_units = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
            unit = duration[-1].lower()
            if unit not in time_units:
                return await interaction.followup.send("❌ Неверный формат времени", ephemeral=True)
            
            amount = int(duration[:-1])
            delta = timedelta(**{time_units[unit]: amount})
            
            # Получаем роль мута
            mute_role = interaction.guild.get_role(self.MUTE_ROLE)
            if not mute_role:
                return await interaction.followup.send("❌ Роль мута не найдена", ephemeral=True)
            
            # Отправляем сообщение пользователю
            await self.send_dm(member, "замучены", reason, duration, interaction.user)
            
            # Выдаем мут
            await member.add_roles(mute_role, reason=reason)
            
            # Создаем эмбед для лога
            embed = discord.Embed(
                title="🔇 Мут пользователя",
                color=discord.Color.yellow(),
                timestamp=datetime.now()
            )
            embed.add_field(name="Пользователь", value=f"{member} ({member.id})", inline=False)
            embed.add_field(name="Причина", value=reason, inline=False)
            embed.add_field(name="Срок", value=duration, inline=False)
            embed.add_field(name="Модератор", value=f"{interaction.user} ({interaction.user.id})", inline=False)
            
            # Записываем в БД
            await self.add_punishment(member.id, "MUTE", reason, duration, interaction.user.id)
            
            # Отправляем лог
            await self.log_action(embed)
            await interaction.followup.send(embed=embed)
            
            # Снимаем мут после истечения срока
            await asyncio.sleep(delta.total_seconds())
            if mute_role in member.roles:
                await member.remove_roles(mute_role, reason="Время мута истекло")
                
                unmute_embed = discord.Embed(
                    title="🔊 Автоматическое размучивание",
                    color=discord.Color.green(),
                    timestamp=datetime.now()
                )
                unmute_embed.add_field(name="Пользователь", value=f"{member} ({member.id})", inline=False)
                await self.log_action(unmute_embed)

        except ValueError:
            await interaction.followup.send("❌ Неверный формат времени", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ У бота недостаточно прав", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Произошла ошибка: {e}", ephemeral=True)

    @app_commands.command(name="clear", description="Очистить сообщения")
    @app_commands.describe(
        amount="Количество сообщений",
        user="Пользователь (опционально)"
    )
    async def clear(self, interaction: discord.Interaction, amount: int,
                   user: Optional[discord.Member] = None):
        if not self.has_support_role(interaction.user):
            return await interaction.response.send_message("❌ У вас недостаточно прав", ephemeral=True)
        
        if amount < 1 or amount > 100:
            return await interaction.response.send_message("❌ Количество сообщений должно быть от 1 до 100", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)

        try:
            # Сохраняем сообщения перед удалением
            messages_to_delete = []
            if user:
                async for message in interaction.channel.history(limit=100):
                    if message.author == user and len(messages_to_delete) < amount:
                        messages_to_delete.append(message)
                deleted = await interaction.channel.delete_messages(messages_to_delete)
            else:
                async for message in interaction.channel.history(limit=amount):
                    messages_to_delete.append(message)
                deleted = await interaction.channel.delete_messages(messages_to_delete)

            # Сохраняем удаленные сообщения в файл
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"logs/deleted_messages/{interaction.channel.name}_{timestamp}.txt"
            os.makedirs("logs/deleted_messages", exist_ok=True)
            
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(f"Канал: {interaction.channel.name} (ID: {interaction.channel.id})\n")
                    f.write(f"Модератор: {interaction.user} (ID: {interaction.user.id})\n")
                    f.write(f"Дата удаления: {datetime.now()}\n")
                    f.write(f"Количество удаленных сообщений: {len(messages_to_delete)}\n\n")
                    f.write("=== Удаленные сообщения ===\n\n")
                    
                    for message in reversed(messages_to_delete):
                        f.write(f"[{message.created_at}] {message.author} ({message.author.id}):\n{message.content}\n")
                        if message.attachments:
                            f.write("Вложения:\n")
                            for att in message.attachments:
                                f.write(f"- {att.url}\n")
                        f.write("\n")

                # Создаем эмбед для лога
                embed = discord.Embed(
                    title="🗑️ Очистка сообщений",
                    color=discord.Color.blue(),
                    timestamp=datetime.now()
                )
                embed.add_field(name="Канал", value=interaction.channel.mention, inline=False)
                embed.add_field(name="Количество", value=str(len(messages_to_delete)), inline=False)
                if user:
                    embed.add_field(name="Пользователь", value=f"{user} ({user.id})", inline=False)
                embed.add_field(name="Модератор", value=f"{interaction.user} ({interaction.user.id})", inline=False)
                embed.add_field(name="Архив сообщений", value="Сообщения сохранены в прикрепленном файле", inline=False)
                
                # Создаем объект File для отправки
                file = discord.File(filename, filename=f"{interaction.channel.name}_deleted_messages.txt")
                
                # Отправляем лог с файлом
                log_channel = interaction.guild.get_channel(self.LOG_CHANNEL_ID)
                if log_channel:
                    await log_channel.send(embed=embed, file=file)
                
                await interaction.followup.send(f"✅ Удалено {len(messages_to_delete)} сообщений", ephemeral=True)
            except Exception as e:
                print(f"Ошибка при сохранении сообщений: {e}")
                await interaction.followup.send(f"✅ Удалено {len(messages_to_delete)} сообщений, но произошла ошибка при сохранении лога: {e}", ephemeral=True)

        except discord.Forbidden:
            await interaction.followup.send("❌ У бота недостаточно прав", ephemeral=True)
        except discord.HTTPException:
            await interaction.followup.send("❌ Невозможно удалить сообщения старше 14 дней", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Произошла ошибка: {e}", ephemeral=True)

    @app_commands.command(name="user_info", description="Получить информацию о пользователе")
    @app_commands.describe(user="ID пользователя или @упоминание")
    async def user_info(self, interaction: discord.Interaction, user: str):
        if not interaction.user.get_role(self.SUPPORT_ROLE) and not any(role.id in self.MOD_ROLES for role in interaction.user.roles):
            return await interaction.response.send_message("❌ У вас недостаточно прав", ephemeral=True)
        
        await interaction.response.defer()

        try:
            # Получаем объект пользователя
            target_user = None
            target_member = None
            
            # Очищаем строку от упоминания
            user = user.strip('<@!>')
            
            try:
                # Пытаемся получить пользователя по ID
                user_id = int(user)
                target_user = await self.bot.fetch_user(user_id)
                target_member = interaction.guild.get_member(user_id)
            except (ValueError, discord.NotFound):
                await interaction.followup.send("❌ Пользователь не найден", ephemeral=True)
                return

            # Создаем эмбед
            embed = discord.Embed(
                title=f"Информация о пользователе {target_user}",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )

            # Основная информация
            embed.add_field(name="Тег", value=str(target_user), inline=True)
            embed.add_field(name="ID", value=target_user.id, inline=True)
            embed.add_field(name="Создан", value=target_user.created_at.strftime("%d.%m.%Y %H:%M"), inline=True)

            if target_member:
                # Информация о пользователе на сервере
                embed.add_field(
                    name="Присоединился", 
                    value=target_member.joined_at.strftime("%d.%m.%Y %H:%M"), 
                    inline=True
                )
                
                # Роли пользователя
                roles = [role.mention for role in target_member.roles[1:]]  # Исключаем @everyone
                embed.add_field(
                    name=f"Роли [{len(roles)}]",
                    value=" ".join(roles) if roles else "Нет ролей",
                    inline=False
                )

                # Информация о приглашении
                guild_invites = self.invites.get(interaction.guild.id, {})
                for invite_code, invite_data in guild_invites.items():
                    if invite_data['uses'] > 0 and invite_data['inviter']:
                        embed.add_field(
                            name="Приглашение",
                            value=f"Приглашен: {invite_data['inviter'].mention}\n"
                                  f"Код: {invite_code}",
                            inline=False
                        )
                        break

            # История наказаний
            conn = sqlite3.connect('punishments.db')
            cursor = conn.cursor()
            cursor.execute('''SELECT type, reason, duration, timestamp, moderator_id 
                            FROM punishments WHERE user_id = ? 
                            ORDER BY timestamp DESC''', (str(target_user.id),))
            punishments = cursor.fetchall()
            conn.close()

            if punishments:
                punishment_text = []
                total_punishments = len(punishments)
                for type_, reason, duration, timestamp, mod_id in punishments[:5]:  # Показываем только 5 последних
                    try:
                        moderator = await self.bot.fetch_user(int(mod_id))
                        mod_name = f"{moderator.mention} ({moderator})"
                    except:
                        mod_name = "Неизвестно"
                    
                    dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                    punishment_text.append(
                        f"**{type_}** ({dt.strftime('%d.%m.%Y %H:%M')})\n"
                        f"Причина: {reason}\n"
                        f"Срок: {duration}\n"
                        f"Модератор: {mod_name}"
                    )
                
                embed.add_field(
                    name=f"История наказаний [{total_punishments}]",
                    value="\n\n".join(punishment_text) if punishment_text else "Нет наказаний",
                    inline=False
                )

            # Устанавливаем аватар справа
            embed.set_thumbnail(url=target_user.avatar.url if target_user.avatar else target_user.default_avatar.url)
            
            await interaction.followup.send(embed=embed)

        except Exception as e:
            await interaction.followup.send(f"❌ Произошла ошибка: {e}", ephemeral=True)

    @app_commands.command(name="warns", description="Посмотреть историю наказаний пользователя")
    @app_commands.describe(user="Пользователь")
    async def warns(self, interaction: discord.Interaction, user: Union[discord.Member, discord.User]):
        if not self.has_support_role(interaction.user):
            return await interaction.response.send_message("❌ У вас недостаточно прав", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)

        try:
            conn = sqlite3.connect('punishments.db')
            cursor = conn.cursor()
            cursor.execute('''
            SELECT type, reason, duration, timestamp, moderator_id,
                   COUNT(*) OVER() as total_count,
                   SUM(CASE WHEN type IN ('UNBAN', 'UNMUTE') THEN 1 ELSE 0 END) OVER() as revoked_count
            FROM punishments
            WHERE user_id = ?
            ORDER BY timestamp DESC
            ''', (str(user.id),))
            punishments = cursor.fetchall()
            conn.close()

            if not punishments:
                return await interaction.followup.send("📝 У пользователя нет истории наказаний", ephemeral=True)

            total_count = punishments[0][5]  # Общее количество наказаний
            revoked_count = punishments[0][6] or 0  # Количество отмененных наказаний

            # Разбиваем историю на части по 20 наказаний
            max_fields = 20
            embeds = []
            def create_embed(page_num, total_pages):
                embed = discord.Embed(
                    title=f"📝 История наказаний {user}",
                    description=f"**Всего наказаний:** {total_count}\n**Отменено наказаний:** {revoked_count}",
                    color=discord.Color.blue(),
                    timestamp=datetime.now()
                )
                embed.set_footer(text=f"Страница {page_num}/{total_pages} • Адвокатское бюро P.A.C.T")
                return embed

            total_pages = (len(punishments) + max_fields - 1) // max_fields
            for page in range(total_pages):
                embed = create_embed(page+1, total_pages)
                for i in range(page*max_fields, min((page+1)*max_fields, len(punishments))):
                    type_, reason, duration, timestamp, mod_id, _, _ = punishments[i]
                    try:
                        moderator = await self.bot.fetch_user(int(mod_id))
                        mod_mention = f"<@{mod_id}>"
                        mod_name = f"{moderator} [{mod_id}]"
                    except:
                        mod_mention = "Unknown"
                        mod_name = f"Неизвестно [ID: {mod_id}]"

                    emoji = {
                        'BAN': '🔨',
                        'UNBAN': '🔓',
                        'MUTE': '🔇',
                        'UNMUTE': '🔊',
                        'KICK': '👢',
                        'WARN': '⚠️'
                    }.get(type_, '📝')

                    embed.add_field(
                        name=f"#{i+1} | {emoji} {type_} - {timestamp}",
                        value=f"**Причина:** {reason}\n**Срок:** {duration}\n**Модератор:** {mod_mention} ({mod_name})",
                        inline=False
                    )
                embeds.append(embed)

            for embed in embeds:
                await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ Произошла ошибка: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Moderation(bot))
