import discord
from discord import app_commands, Interaction, SelectOption, ButtonStyle, TextStyle, ui
from discord.ext import commands, tasks
from discord.utils import escape_markdown, get
from discord.ui import Button, View, Select, TextInput
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timedelta, timezone
import textwrap
import io
import sqlite3
import asyncio
import json
import os
import re
import base64
from typing import Optional, Union, List
import aiohttp
from collections import defaultdict
import string
import random
import traceback
import copy
from logs import Logs
from moderation import Moderation

# Объявление command_state
command_state = {}

class CloseTicketModal(discord.ui.Modal, title="Закрыть обращение"):
    def __init__(self):
        super().__init__()
        self.reason = discord.ui.TextInput(
            label="Причина",
            style=discord.TextStyle.long,
            placeholder="Причина для закрытия обращения, например, 'Решено'",
            required=True,
            max_length=1024
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Получаем данные о тикете из базы данных
            conn = sqlite3.connect('lawyers.db')
            cursor = conn.cursor()
            
            # Получаем информацию о клиенте и адвокате
            cursor.execute("""
                SELECT client_name, client_tag, lawyer_tag
                FROM help_data 
                WHERE channel_id = ?
            """, (str(interaction.channel.id),))
            
            result = cursor.fetchone()
            conn.close()

            if result:
                client_name, client_tag, lawyer_tag = result
                print(f"Данные для отзыва: client_name={client_name}, client_tag={client_tag}, lawyer_tag={lawyer_tag}")

                # Убеждаемся, что у нас есть тег клиента
                if not client_tag or not client_tag.startswith('<@'):
                    if interaction.channel.members:
                        # Ищем участника канала, который не является ботом и не является адвокатом
                        client_member = next(
                            (member for member in interaction.channel.members 
                             if not member.bot and not any(role.id == LAWYER_ROLE_ID for role in member.roles)),
                            None
                        )
                        if client_member:
                            client_tag = client_member.mention
                            print(f"Найден тег клиента из канала: {client_tag}")

                # Отправляем запрос на отзыв
                await send_review_request(
                    interaction.guild,
                    lawyer_tag or "Адвокат не назначен",
                    client_tag or f"<@{client_name}>" if client_name.isdigit() else client_tag or client_name
                )
            else:
                print(f"❌ Ошибка: Данные о тикете не найдены для канала {interaction.channel.id}")

            # Проверяем, не является ли канал основным каналом тикетов
            if interaction.channel.id == 1399117597450043422:
                await interaction.response.send_message(
                    "❌ Нельзя закрыть основной канал тикетов!",
                    ephemeral=True
                )
                return

            # Отправляем подтверждение
            await interaction.response.send_message(
                f"Обращение закрыто по причине: {self.reason.value}",
                ephemeral=True
            )

            # Удаляем канал через небольшую задержку, только если это не основной канал тикетов
            if interaction.channel.category_id == TICKET_CATEGORY_ID:
                await asyncio.sleep(2)
                # Удаляем только каналы из категории тикетов
                await interaction.channel.delete(reason=f"Закрыто управляющим: {interaction.user}")
                print(f"✅ Канал {interaction.channel.name} удален из категории тикетов")

        except Exception as e:
            print(f"Ошибка при закрытии тикета: {e}")
            await interaction.response.send_message(
                "Произошла ошибка при закрытии тикета.",
                ephemeral=True
            )

TICKET_CATEGORY_ID = 1379559023124156602
PAYMENT_CATEGORY_ID = 1379559023124156602
LAWYER_ROLE_ID = 1391399383441997866
MOD_ROLE_IDS = [1379547784717402152, 1379547989680324750]
CHANNEL_ID = 1516302913205440512
LOG_CHANNEL_ID = 1379613135631290459  # Замените на ID вашего канала логов


def init_db():
    conn = sqlite3.connect('lawyers.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS lawyers
                 (passport TEXT PRIMARY KEY,
                  name TEXT,
                  phone TEXT,
                  email TEXT,
                  discord_id TEXT UNIQUE)''')
    c.execute('''CREATE TABLE IF NOT EXISTS help_data
                 (channel_id text, agreement_number text, client_name text, client_tag text, lawyer_tag text, client_passport text)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tickets
                 (channel_id text PRIMARY KEY, lawyer_id text, client_id text, nickname text)''')
    c.execute('''CREATE TABLE IF NOT EXISTS lawyer_stats
                 (lawyer_id text PRIMARY KEY, cases_taken integer DEFAULT 0, total_earned integer DEFAULT 0)''')
    conn.commit()
    conn.close()

init_db()

def add_lawyer(passport, name, phone, email, discord_id):
    """Добавляет адвоката в базу данных"""
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO lawyers (passport, name, phone, email, discord_id)
        VALUES (?, ?, ?, ?, ?)
    """, (passport, name, phone, email, discord_id))
    conn.commit()
    conn.close()

def get_lawyer(discord_id):
    """Получает данные адвоката по Discord ID"""
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM lawyers WHERE discord_id = ?", (discord_id,))
    result = cursor.fetchone()
    conn.close()
    return result


class LawyerBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Добавляем постоянные view при запуске бота
        self.add_view(MacroButtons())
        # Загружаем коги при запуске бота
        await self.add_cog(Logs(self))
        await self.add_cog(Moderation(self))
        # Регистрируем вечные кнопки
        self.add_view(PaymentConfirmButton())
        self.add_view(PaymentClientView())
        self.add_view(ApproveApplicationView(
            lawyer_name="",
            lawyer_passport="",
            phone="",
            discord_id="",
            email="",
            manager_name="",
            manager_id=""
        ))

bot = LawyerBot()

# Глобальный словарь для хранения кастомных ID
PERSISTENT_VIEWS = {
    "create_ticket": None,
    "join_bureau": None,
    "start_work": None,
    "close_ticket": None
}


def get_initials(name):
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[0][0]}.{parts[1]}"
    else:
        return name


def add_date_and_agreement(draw, номер_документа, font_small):
    today = datetime.now().strftime("%d.%m.%Y")
    draw.text((1250, 150), f"Страница / 1\nДата публикации\n{today}", font=font_small, fill="black")
    draw.text((90, 150), f"Соглашение\n№SD-{номер_документа}", font=font_small, fill="black")

async def check_channel_and_role(interaction: discord.Interaction):
    # Проверка категории
    if interaction.channel.category_id != TICKET_CATEGORY_ID:
        await interaction.response.send_message("Эта команда может быть использована только в тикет-канале.", ephemeral=True)
        return False

    # Проверка роли адвоката
    if not any(role.id == LAWYER_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("Только адвокаты могут использовать эту команду.", ephemeral=True)
        return False

    return True
@bot.tree.command(name="запрос", description="Создать адвокатский запрос")
@app_commands.describe(
    номер_документа="Номер документа",
    начало_времени="Время начала",
    конец_времени="Время окончания",
    дата_нарушения="Дата нарушения",
    сотрудник="сотрудник",
    дата_дедлайна="Дата дедлайна",
    время_дедлайна="Время дедлайна",
    template_path="Путь к шаблону (опционально)"
)
async def generate_request(
        interaction: discord.Interaction,
        номер_документа: str,
        начало_времени: str,
        конец_времени: str,
        дата_нарушения: str,
        сотрудник: str,
        дата_дедлайна: str,
        время_дедлайна: str,
        template_path: str = "запросfig.png"
):
    await interaction.response.defer()

    # Получаем данные из базы данных
    channel_id = str(interaction.channel_id)
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    cursor.execute("SELECT agreement_number, client_name FROM help_data WHERE channel_id = ?", (channel_id,))
    result = cursor.fetchone()
    conn.close()

    if not result:
        await interaction.followup.send("Данные для этого канала не найдены. Сначала используйте команду /помощь.", ephemeral=True)
        return

    номер_соглашения, истец = result

    # Получаем данные адвоката из базы данных
    адвокат = get_lawyer(str(interaction.user.id))  # Используем Discord ID
    if not адвокат:
        await interaction.followup.send("Вы не зарегистрированы как адвокат.", ephemeral=True)
        return

    name = адвокат[1]
    email = адвокат[3]
    phone = адвокат[2]

    # Остальной код команды с использованием данных адвоката
    try:
        img = Image.open(template_path).convert("RGBA")
        draw = ImageDraw.Draw(img)

        try:
            font_small = ImageFont.truetype("times.ttf", 22)
            font_main = ImageFont.truetype("times.ttf", 32)
            font_lawyer = ImageFont.truetype("times.ttf", 24)
            font_bold = ImageFont.truetype("times.ttf", 32)
            font_signature = ImageFont.truetype("timesi.ttf", 48)
        except IOError: # Исправлено на IOError для шрифтов
            font_small = font_main = font_lawyer = font_bold = font_signature = ImageFont.load_default()

        today = datetime.now().strftime("%d.%m.%Y")
        draw.text((1250, 150), f"Страница / 1\nДата публикации\n{today}", font=font_small, fill="black")
        draw.text((90, 150), f"Запрос\n№SB-{номер_документа}", font=font_small, fill="black")

        main_text = f"""
На основании Главы II Статьи 1, Главы II Статьи 2 Части 1 закона о Коллегии Адвокатов и в рамках оказания юридической помощи по соглашению об оказании юридической помощи №SD-{номер_соглашения},
\nЯ, действующий партнёр адвокатского бюро, {name}, "PACT Attorney" запрашиваю:
\n1. Предоставить видеофиксацию совершения правонарушения (уголовного и/или административного) от гражданина {истец} в промежуток с {начало_времени} до {конец_времени} {дата_нарушения}, а также полную видеофиксацию процессуальных действий в отношении вышеописанного гражданина.
\n2. Запрос направить сотруднику {сотрудник}.
\n3. Запрошенные материалы предоставить до {дата_дедлайна} {время_дедлайна}.
\n4. Запрошенные материалы предоставить лично адвокату бюро {name}, или по почте: {email}. Контактный телефон: {phone}.
"""

        wrapper = textwrap.TextWrapper(width=90)
        y_position = 520
        line_spacing = 12
        paragraphs = [p for p in main_text.split('\n\n') if p.strip()]
        for paragraph in paragraphs:
            lines = wrapper.wrap(paragraph)
            for line in lines:
                line_width = font_main.getlength(line)
                x = (img.width - line_width) // 2
                draw.text((x, y_position), line, font=font_main, fill="black")
                y_position += font_main.size + line_spacing
            y_position += 20

        signature_text = get_initials(name)
        y_signature = img.height - 200
        sig_width = font_signature.getlength(signature_text)
        x_signature = (img.width - sig_width) // 2
        draw.text((x_signature, y_signature), signature_text, font=font_signature, fill="black")

        label_text = f"Партнёр Бюро\n{name}"
        lines = label_text.split("\n")
        y_label = y_signature + font_signature.size + 20
        for line in lines:
            line_width = font_lawyer.getlength(line)
            x = (img.width - line_width) // 2
            draw.text((x, y_label), line, font=font_lawyer, fill="black")
            y_label += font_lawyer.size + 8

        bold_text = "Настоящий запрос вступает в законную силу с момента его публикации."
        line_width = font_bold.getlength(bold_text)
        x = (img.width - line_width) // 2
        draw.text((x, y_position + 100), bold_text, font=font_bold, fill="black")

        with io.BytesIO() as image_binary:
            img.save(image_binary, 'PNG')
            image_binary.seek(0)
            file = discord.File(fp=image_binary, filename='lawyer_request.png')
            await interaction.followup.send(file=file)
            notification_message = (
                f"В отношении Вас опубликован адвокатский запрос №SB-{номер_документа}. До {время_дедлайна} {дата_дедлайна} вам нужно предоставить запись осуществления задержания и доказательства нарушения закона гражданина {истец} в период с {начало_времени}-{конец_времени} {дата_нарушения}. Связь: {email}"
            )
            await interaction.followup.send(notification_message)

    except Exception as e:
        await interaction.followup.send(f"Произошла ошибка: {str(e)}")


@bot.tree.command(name="повестка", description="Создать адвокатскую повестку")
@app_commands.describe(
    номер_документа="Номер документа",
    номер_ордера="Номер ордера",
    фракция="фракция",
    имя_подозреваемого="Имя подозреваемого",
    номер_паспорта="Номер паспорта",
    дата_встречи="Дата встречи",
    время_встречи="Время встречи",
    template_path="Путь к шаблону (опционально)"
)
async def generate_summons(
        interaction: discord.Interaction,
        номер_документа: str,
        номер_ордера: str,
        фракция: str,
        имя_подозреваемого: str,
        номер_паспорта: str,
        дата_встречи: str,
        время_встречи: str,
        template_path: str = "повесткаfig.png"
):
    # Проверка канала и роли
    if not await check_channel_and_role(interaction):
        return

    await interaction.response.defer()

    # Получаем данные из базы данных
    channel_id = str(interaction.channel_id)
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    cursor.execute("SELECT agreement_number, client_name FROM help_data WHERE channel_id = ?", (channel_id,))
    result = cursor.fetchone()
    conn.close()

    if not result:
        await interaction.followup.send("Данные для этого канала не найдены. Сначала используйте команду /помощь.", ephemeral=True)
        return

    номер_соглашения, клиент = result

    # Получаем данные адвоката из базы данных
    адвокат = get_lawyer(str(interaction.user.id))  # Используем Discord ID
    if not адвокат:
        await interaction.followup.send("Вы не зарегистрированы как адвокат.", ephemeral=True)
        return

    name = адвокат[1]
    email = адвокат[3]

    # Остальной код команды с использованием данных адвоката
    try:
        img = Image.open(template_path).convert("RGBA")
        draw = ImageDraw.Draw(img)

        try:
            font_small = ImageFont.truetype("times.ttf", 22)
            font_main = ImageFont.truetype("times.ttf", 32)
            font_lawyer = ImageFont.truetype("times.ttf", 28)
            font_bold = ImageFont.truetype("times.ttf", 32)
            font_signature = ImageFont.truetype("timesi.ttf", 48)
        except IOError: # Исправлено на IOError
            font_small = font_main = font_lawyer = font_bold = font_signature = ImageFont.load_default()

        today = datetime.now().strftime("%d.%m.%Y")
        draw.text((1250, 150), f"Страница / 1\nДата публикации\n{today}", font=font_small, fill="black")
        draw.text((90, 150), f"Акт\n№SB-{номер_документа}", font=font_small, fill="black")

        main_text = f"""
На основании Главы II Статьи 3, Главы II Статьи 1 закона о Коллегии Адвокатов, Главы X Статьи 3 Части 5 Процессуального Кодекса, также в рамках оказания юридической помощи по соглашению об оказании юридической помощи №{номер_соглашения} и ордером Issuance of Powers №{номер_ордера},

Я, действующий партнёр адвокатского бюро, {name}, "PACT Attorney", вызываю:

сотрудника {фракция} {имя_подозреваемого} с номером паспорта {номер_паспорта} необходимо явиться в офис адвокатского бюро, расположенному по адресу г. Лос-Сантос, Пилбокс-Хилл, офис Arcadius (главный вход) в {время_встречи} {дата_встречи} г. для последующего допроса в качестве подозреваемого.

При себе необходимо иметь удостоверение личности или иной документ, который удостоверяет личность.

При наличии причин, препятствующих явке по вызову в назначенный срок, необходимо обратиться к ведущему настоящее адвокатское расследование адвокату {name} по электронной почте {email}.

В случае неявки в указанный срок без уважительных причин, вызываемое лицо может быть привлечено к уголовной ответственности в соответствии с действующим законодательством.
"""
        margin = 120
        line_spacing = 12
        y_position = 520
        wrapper = textwrap.TextWrapper(width=90)
        paragraphs = [p for p in main_text.split('\n\n') if p.strip()]
        for paragraph in paragraphs:
            lines = wrapper.wrap(paragraph)
            for line in lines:
                line_width = font_main.getlength(line)
                x = (img.width - line_width) // 2
                draw.text((x, y_position), line, font=font_main, fill="black")
                y_position += font_main.size + line_spacing
            y_position += 20

        signature_text = get_initials(name)
        y_signature = img.height - 200
        sig_width = font_signature.getlength(signature_text)
        x_signature = (img.width - sig_width) // 2
        draw.text((x_signature, y_signature), signature_text, font=font_signature, fill="black")

        label_text = f"Партнёр Бюро\n{name}"
        lines = label_text.split("\n")
        y_label = y_signature + font_signature.size + 20
        for line in lines:
            line_width = font_lawyer.getlength(line)
            x = (img.width - line_width) // 2
            draw.text((x, y_label), line, font=font_lawyer, fill="black")
            y_label += font_lawyer.size + 8

        with io.BytesIO() as image_binary:
            img.save(image_binary, 'PNG')
            image_binary.seek(0)
            file = discord.File(fp=image_binary, filename='lawyer_summons.png')
            await interaction.followup.send(file=file)
    except Exception as e:
        await interaction.followup.send(f"Произошла ошибка: {str(e)}")

@bot.tree.command(name="запрос_увольнения", description="Создать запрос по увольнению")
@app_commands.describe(
    номер_документа="Номер документа",
    расшифровка_статьи="Расшифровка статьи из ТК",
    структура_истца="Структура, в которой работал истец",
    дата_и_время_увольнения="Дата и время увольнения",
    ответчик="Имя ответчика",
    дата_дедлайна="Дата предоставления материалов",
    время_дедлайна="Время предоставления материалов",
    template_path="Путь к шаблону (опционально)"
)
async def generate_dismissal_request(
        interaction: discord.Interaction,
        номер_документа: str,
        расшифровка_статьи: str,
        структура_истца: str,
        дата_и_время_увольнения: str,
        ответчик: str,
        дата_дедлайна: str,
        время_дедлайна: str,
        template_path: str = "запросfig.png"
):
    # Проверка канала и роли
    if not await check_channel_and_role(interaction):
        return
        
    await interaction.response.defer()

    # Получаем данные из базы данных
    channel_id = str(interaction.channel_id)
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    cursor.execute("SELECT agreement_number, client_name, client_passport FROM help_data WHERE channel_id = ?", (channel_id,))
    result = cursor.fetchone()
    conn.close()

    if not result:
        await interaction.followup.send("Данные для этого канала не найдены. Сначала используйте команду /помощь.", ephemeral=True)
        return

    номер_соглашения, истец, паспорт_истца = result

    # Получаем данные адвоката из базы данных
    адвокат = get_lawyer(str(interaction.user.id))
    if not адвокат:
        await interaction.followup.send("Вы не зарегистрированы как адвокат.", ephemeral=True)
        return

    name = адвокат[1]
    email = адвокат[3]
    phone = адвокат[2]

    try:
        img = Image.open(template_path).convert("RGBA")
        draw = ImageDraw.Draw(img)

        try:
            font_small = ImageFont.truetype("times.ttf", 22)
            font_main = ImageFont.truetype("times.ttf", 32)
            font_lawyer = ImageFont.truetype("times.ttf", 24)
            font_bold = ImageFont.truetype("times.ttf", 32)
            font_signature = ImageFont.truetype("timesi.ttf", 48)
        except IOError:
            font_small = font_main = font_lawyer = font_bold = font_signature = ImageFont.load_default()

        today = datetime.now().strftime("%d.%m.%Y")
        draw.text((1250, 150), f"Страница / 1\nДата публикации\n{today}", font=font_small, fill="black")
        draw.text((90, 150), f"Запрос\n№SB-{номер_документа}", font=font_small, fill="black")

        main_text = f"""
На основании Главы II Статьи 1, Главы II Статьи 2 Части 1 закона о Коллегии Адвокатов, Главы 2 статьи 4 части 4.2 пункту 6 Трудового кодекса и в рамках оказания юридической помощи по соглашению об оказании юридической помощи №SD-{номер_соглашения},

Я, действующий партнёр адвокатского бюро "PACT Attorney" запрашиваю:

1. Предоставить доказательства (то есть все существующие доказательства {расшифровка_статьи}) по факту увольнения сотрудника {структура_истца} {истец} (номер паспорта: {паспорт_истца}) в {дата_и_время_увольнения};

2. Запрос направить сотруднику {ответчик};

3. Запрошенные материалы предоставить до {дата_дедлайна} {время_дедлайна};

4. Запрошенные материалы предоставить по почте адвоката: {email}. Контактный телефон: {phone}.
"""

        wrapper = textwrap.TextWrapper(width=90)
        y_position = 520
        line_spacing = 12
        paragraphs = [p for p in main_text.split('\n\n') if p.strip()]
        for paragraph in paragraphs:
            lines = wrapper.wrap(paragraph)
            for line in lines:
                line_width = font_main.getlength(line)
                x = (img.width - line_width) // 2
                draw.text((x, y_position), line, font=font_main, fill="black")
                y_position += font_main.size + line_spacing
            y_position += 20

        signature_text = get_initials(name)
        y_signature = img.height - 200
        sig_width = font_signature.getlength(signature_text)
        x_signature = (img.width - sig_width) // 2
        draw.text((x_signature, y_signature), signature_text, font=font_signature, fill="black")

        label_text = f"Партнёр Бюро\n{name}"
        lines = label_text.split("\n")
        y_label = y_signature + font_signature.size + 20
        for line in lines:
            line_width = font_lawyer.getlength(line)
            x = (img.width - line_width) // 2
            draw.text((x, y_label), line, font=font_lawyer, fill="black")
            y_label += font_lawyer.size + 8

        bold_text = "Настоящий запрос вступает в законную силу с момента его публикации."
        line_width = font_bold.getlength(bold_text)
        x = (img.width - line_width) // 2
        draw.text((x, y_position + 100), bold_text, font=font_bold, fill="black")

        with io.BytesIO() as image_binary:
            img.save(image_binary, 'PNG')
            image_binary.seek(0)
            file = discord.File(fp=image_binary, filename='dismissal_request.png')
            await interaction.followup.send(file=file)
            notification_message = (
                f"В отношении Вас опубликован адвокатский запрос №SB-{номер_документа}. "
                f"До {время_дедлайна} {дата_дедлайна} вам нужно предоставить доказательства по факту увольнения сотрудника {истец} (паспорт: {паспорт_истца}) в {дата_и_время_увольнения}. "
                f"Связь: {email}"
            )
            await interaction.followup.send(notification_message)

    except Exception as e:
        await interaction.followup.send(f"Произошла ошибка: {str(e)}")
@bot.tree.command(name="принятие", description="Создать документ о принятии адвоката")
@app_commands.describe(
    document_number="Номер документа",
    manager_name="Имя управляющего партнера",
    manager_passport="Паспорт управляющего",
    lawyer_name="Имя нового адвоката",
    lawyer_passport="Паспорт адвоката",
    discord_id="Discord ID адвокаа",
    phone="Телефон адвоката",
    email="Почта адвоката",
    template_path="Путь к шаблону (опционально)"
)
async def generate_acceptance(
        interaction: discord.Interaction,
        document_number: str,
        manager_name: str,
        manager_passport: str,
        lawyer_name: str,
        lawyer_passport: str,
        discord_id: str,
        phone: str,
        email: str,
        template_path: str = "принятие.png"
):
    # Проверка роли
    if not any(role.id in MOD_ROLE_IDS for role in interaction.user.roles):
        if not interaction.response.is_done():
            await interaction.response.send_message("Только модераторы могут использовать эту команду.", ephemeral=True)
        return

    # Убираем @, если он есть
    if discord_id.startswith("@"):
        discord_id = discord_id[1:]

    # Проверяем, что Discord ID состоит только из цифр
    if not discord_id.isdigit():
        if not interaction.response.is_done():
            await interaction.response.send_message("Неверный Discord ID. Он должен состоять только из цифр.", ephemeral=True)
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=False, thinking=True)

    print("[DEBUG] generate_acceptance: Начало генерации документа")
    # Пропускаем добавление в базу данных, так как адвокат уже добавлен

    # Далее код для создания документа
    def draw_centered_text(draw, text, y_position, font, image_width, line_spacing=15):
        """Функция для рисования центрированного текста с переносами"""
        wrapper = textwrap.TextWrapper(width=110)
        paragraphs = [p.strip() for p in text.split('\n') if p.strip()]

        for paragraph in paragraphs:
            lines = wrapper.wrap(paragraph)
            for line in lines:
                text_width = font.getlength(line)
                x = (image_width - text_width) // 2
                draw.text((x, y_position), line, font=font, fill="black")
                y_position += font.size + line_spacing
            y_position += 10
        return y_position

    try:
        img = Image.open(template_path).convert("RGBA")
        draw = ImageDraw.Draw(img)

        try:
            font_small = ImageFont.truetype("times.ttf", 22)
            font_main = ImageFont.truetype("times.ttf", 26)
            font_lawyer = ImageFont.truetype("times.ttf", 24)
            font_signature = ImageFont.truetype("timesi.ttf", 48)
        except IOError: # Исправлено на IOError
            font_ssmall = font_main = font_lawyer = font_signature = ImageFont.load_default()

        today = datetime.now().strftime("%d.%m.%Y")
        draw.text((1250, 150), f"Страница / 1\nДата публикации\n{today}", font=font_small, fill="black")
        draw.text((90, 150), f"Соглашение\n№SB-{document_number}", font=font_small, fill="black")

        y_position = 510

        agreement_text = f"""
Часть I. Общие положения
Настоящий договор заключен между Адвокатским бюро "PACT Attorney" в лице Управляющего партнера {manager_name} (паспорт № {manager_passport}), именуемого далее "Управляющий партнер", и Адвокатом {lawyer_name} (паспорт № {lawyer_passport}), именуемого далее "Адвокат". В дальнейшем Управляющий партнер и Адвокат совместно именуются "Стороны".

Часть II. Принятие условий
Заключение настоящего договорa подтверждает согласие Адвоката с условиями, описанными в Партнерском договоре №2265 от 16.08.2025, а также уставом бюро (Внутренний акт бюро №1 от 16.08.2025). Адвокат обязуется соблюдать все условия, положения, права, обязанности и ответственность, установленные этим Партнерским договором и внутренним уставом, а также любыми будущими договорами, заменяющими указанный Партнерский договор Адвокатского бюro.

Часть III. Обязанности адвоката и ответственность за нарушение обязательств
3.1. Адвокат обязуется предоставлять юридическую помощь доверителям Адвокатского бюро совместно с другими партнерами бюro.
3.2. Адвокат должен строго соблюдать конфиденциальность, информировать доверителей о ходе дел и действовать исключительно в их интересах компетентно и добросовестно.
3.3. Адвокат обязуется исполнять требования действующего законодательства, Партнерского договора, а также иных внутренних регламентов, регулирующих отношения между доверителями и бюro.
3.4. В случае неисполнения или ненадлежащего исполнения Адвокатом своих обязательств по настоящему договору или Партнерского договора, он обязан возместить другим партнерам причиненные убытки.

Часть IV. Взносы и финансирование
4.1. За вступление в Адвокатское бюро Адвокат обязуется оплатить вступительный взнос в размере 10 000 долларов США.
4.2. В период членства в бюро Адвокат обязуется вносить еженедельные взносы, сумма которых определяется Управляющим партнером.
4.3. Вступительный взнос и еженедельные взносы не подлежат возврату при выходе или исключении Адвоката из состава бюро.

Часть V. Передача и распределение вознаграждений
5.1. При оказании юридической помощи клиентам бюро Адвокат обязан передавать полученные вознаграждения в бюро на указанный банковский счет или Управляющему партнеру в соответствии с соглашением об оказании юридической помощи.
5.2. Вознаграждение распределяется между партнерами, участвовавшими в оказании юридической помощи, после удержания согласованного процента на нужды бюро. Расчеты производятся сразу после завершения работы с клиентом.
5.3. Обязательства по передаче вознаграждений в бюро не распространяются на соглашения, заключенные Адвокатом от собственного имени и не от имени бюро. Такие вознаграждения не подлежат удержанию или перераспределению.

Часть VI. Заключительные положения
6.1. Настоящий договор вступает в силу с момента его подписания обеими сторонами.
6.2. Все изменения и дополнения к настоящему договору оформляются в письменной форме и подписываются обеими сторонами.
6.3. Споры, возникающие из настоящего договора, разрешаются в порядке, предусмотренном действующим законодательством.
"""
        y_position = draw_centered_text(draw, agreement_text, y_position, font_main, img.width)

        bottom_margin = 160
        spacing = 20
        left_x = 120
        right_x = img.width - 450
        y_base = img.height - bottom_margin

        left_initials = get_initials(manager_name)
        draw.text((left_x, y_base), left_initials, font=font_signature, fill="black")
        draw.text((left_x, y_base + font_signature.size + spacing), manager_name, font=font_lawyer, fill="black")

        right_initials = get_initials(lawyer_name)  # Changed from self.lawyer_name to lawyer_name
        draw.text((right_x, y_base), right_initials, font=font_signature, fill="black")
        draw.text((right_x, y_base + font_signature.size + spacing), lawyer_name, font=font_lawyer, fill="black")        # Сохраняем файл локально и отправляем его
        output_filename = f"output_{document_number}.png"
        img.save(output_filename, 'PNG')
        file = discord.File(output_filename, filename='lawyer_acceptance.png')
        await interaction.followup.send(file=file)

    except Exception as e:
        await interaction.followup.send(f"Произошла ошибка: {str(e)}")

    # Обновляем эмбед
    await update_lawyers_embed(bot, interaction.guild)

# Функция для обновления реестра клиентов


# Безопасный вызов обновления реестра из любого места (sync/async)
def trigger_update_client_registry():
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        loop.create_task(update_client_registry(bot))
    else:
        asyncio.run(update_client_registry(bot))
async def update_client_registry(bot):
    """
    Обновляет реестр клиентов с единым форматом вывода
    """
    # Получаем канал реестра
    registry_channel = bot.get_channel(1379612255594872893)
    if not registry_channel:
        print("Канал реестра не найден.")
        return

    # Запрашиваем все 6 столбцов
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT channel_id, client_name, agreement_number,
               client_tag, lawyer_tag, client_passport
        FROM help_data
    """)
    clients = cursor.fetchall()
    conn.close()

    # Удаляем старые сообщения
    async for message in registry_channel.history(limit=100):
        if message.author == bot.user:
            await message.delete()

    # Убираем дубликаты
    unique_clients = []
    seen_agreements = set()
    for client in clients:
        agreement_number = client[2]
        if agreement_number in seen_agreements:
            continue
        seen_agreements.add(agreement_number)
        unique_clients.append(client)

    # Разбиваем на группы по 25
    chunk_size = 25
    for i in range(0, len(unique_clients), chunk_size):
        chunk = unique_clients[i:i + chunk_size]

        embed = discord.Embed(
            title=f"📂 Реестр клиентов (часть {i // chunk_size + 1})",
            color=discord.Color.blue()
        )

        for client in chunk:
            channel_id, client_name, agreement_number, client_tag, lawyer_tag, passport = client
            channel = bot.get_channel(int(channel_id)) if channel_id else None

            # Формируем содержимое
            field_value = [
                f"🔗 Канал: {channel.mention if channel else 'N/A'}",
                f"📄 Соглашение: {agreement_number}"
            ]

            # Добавляем дополнительные поля если они есть и не равны None
            if client_tag and str(client_tag).lower() != "none" and not client_tag.startswith("<@None"):
                field_value.append(f"👤 Тег клиента: {client_tag}")
            if lawyer_tag and str(lawyer_tag).lower() != "none" and not lawyer_tag.startswith("<@None"):
                field_value.append(f"⚖️ Тег адвоката: {lawyer_tag}")
            if passport:
                field_value.append(f"📝 Паспорт: {passport}")

            # Всегда используем client_name из БД для заголовка
            embed.add_field(
                name=f"Клиент: {client_name}",
                value="\n".join(field_value),
                inline=False
            )

        if embed.fields:
            await registry_channel.send(embed=embed)

    print("Реестр клиентов успешно обновлен.")


@bot.tree.command(name="удалить_клиента", description="Удалить клиента из реестра по номеру соглашения")
@app_commands.describe(номер_соглашения="Номер соглашения для удаления")
async def delete_client(interaction: discord.Interaction, номер_соглашения: str):
    # Проверка, что команду использует модератор
    if not any(role.id in MOD_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("Только модераторы могут удалять клиентов.", ephemeral=True)
        return

    # Удаляем клиента из базы данных
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM help_data WHERE agreement_number = ?", (номер_соглашения,))
    if cursor.rowcount == 0:
        await interaction.response.send_message(f"Клиент с номером соглашения {номер_соглашения} не найден.", ephemeral=True)
    else:
        conn.commit()
        await interaction.response.send_message(f"Клиент с номером соглашения {номер_соглашения} удален.", ephemeral=True)
        import asyncio
        asyncio.create_task(update_client_registry(bot))

        # Обновляем реестр клиентов
    conn.close()



# Одноразовая функция миграции: привести tickets к хранению числовых ID
def migrate_tickets_to_ids():
    import sqlite3, re
    conn = sqlite3.connect('lawyers.db')
    c = conn.cursor()
    rows = c.execute('SELECT channel_id, lawyer_id, client_id FROM tickets').fetchall()
    for ch, lid, cid in rows:
        def norm(x):
            if x is None:
                return None
            s = re.sub(r'\D', '', str(x))
            return s or None
        c.execute('UPDATE tickets SET lawyer_id=?, client_id=? WHERE channel_id=?', (norm(lid), norm(cid), ch))
    conn.commit()
    conn.close()
async def get_assigned_lawyer(channel) -> str:
    """Получает упоминание адвоката из БД по ID канала"""
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()

    try:
        # Выполняем запрос к БД
        cursor.execute('''
        SELECT lawyer_id FROM tickets
        WHERE channel_id = ?
''', (str(channel.id),))

        result = cursor.fetchone()

        if result:
            return f"<@{result[0]}>" if str(result[0]).isdigit() else result[0]
        else:
            return "Адвокат не назначен"

    except Exception as e:
        print(f"Ошибка при запросе к БД: {e}")
        return "Ошибка при получении данных"

    finally:
        conn.close()

@bot.tree.command(name="помощь", description="Создать документы о юридической помощи")
@app_commands.describe(
    document_number="Номер документа",
    client_name="Имя клиента",
    client_passport="Паспорт клиента",
    template1_path="Путь к первому шаблону (опционально)",
    template2_path="Путь ко второму шаблону (опционально)"
)
async def generate_help_docs(
        interaction: discord.Interaction,
        document_number: str,
        client_name: str,
        client_passport: str,
        template1_path: str = "help1.png",
        template2_path: str = "help2.png"
):
    print(f"Команда 'помощь' вызвана пользователем {interaction.user.name} в канале {interaction.channel_id}")
    
    # Получаем данные адвоката из базы данных для макроса
    lawyer = get_lawyer(str(interaction.user.id))
    if not lawyer:
        await interaction.response.send_message("Ошибка: данные адвоката не найдены в базе данных.", ephemeral=True)
        return

    lawyer_email = lawyer[3]  # Email находится в четвертой колонке

    def draw_centered_text(draw, text, y_position, font, image_width, line_spacing=15):
        """Функция для рисования центрированного текста с переносами"""
        wrapper = textwrap.TextWrapper(width=110)
        paragraphs = [p.strip() for p in text.split('\n') if p.strip()]

        for paragraph in paragraphs:
            lines = wrapper.wrap(paragraph)
            for line in lines:
                text_width = font.getlength(line)
                x = (image_width - text_width) // 2
                draw.text((x, y_position), line, font=font, fill="black")
                y_position += font.size + line_spacing
            y_position += 10
        return y_position

    try:
        # Подтверждаем взаимодействие, чтобы продлить время ожидания
        await interaction.response.defer(ephemeral=False, thinking=True)

        conn = sqlite3.connect('lawyers.db')
        cursor = conn.cursor()

        # Проверяем, существует ли уже запись с таким номером соглашения
        cursor.execute("SELECT * FROM help_data WHERE agreement_number = ?", (document_number,))
        if cursor.fetchone():
            await interaction.followup.send("Запись с таким номером соглашения уже существует.", ephemeral=True)
            conn.close()
            return

        channel_id = str(interaction.channel_id)

        # Получаем данные адвоката
        lawyer = get_lawyer(str(interaction.user.id))
        if not lawyer:
            await interaction.followup.send("Вы не зарегистрированы как адвокат.", ephemeral=True)
            return

        channel_name = interaction.channel.name
        username = channel_name.split('-', 1)[1]

        # Ищем пользователя на сервере
        user = discord.utils.get(interaction.guild.members, name=username)
        tag_client = None
        # Попытаться взять client_id из tickets для текущего канала
        try:
            _conn = sqlite3.connect('lawyers.db')
            _cur = _conn.cursor()
            _cur.execute('SELECT client_id FROM tickets WHERE channel_id = ?', (str(interaction.channel.id),))
            _row = _cur.fetchone()
        finally:
            try:
                _conn.close()
            except Exception:
                pass
        if _row and _row[0]:
            tag_client = f"<@{_row[0]}>"
        elif user:
            tag_client = user.mention
        else:
            tag_client = f"@{username}"
        tag_lawyer = await get_assigned_lawyer(interaction.channel)

        # Добавляем новую запись
        cursor.execute("INSERT INTO help_data (channel_id, client_name, agreement_number, client_tag, lawyer_tag, client_passport) VALUES (?, ?, ?, ?, ?, ?)",
                       (channel_id, client_name, document_number, tag_client, tag_lawyer, client_passport))
        conn.commit()
        conn.close()

        # Обновляем реестр клиентов
        import asyncio
        trigger_update_client_registry()

        lawyer_name = lawyer[1]  # Имя адвоката
        lawyer_passport = lawyer[0]  # Паспорт адвоката

        # Создаем первый документ
        img1 = Image.open(template1_path).convert("RGBA")
        draw1 = ImageDraw.Draw(img1)

        # Загружаем шрифты
        try:
            font_small = ImageFont.truetype("times.ttf", 22)
            font_main = ImageFont.truetype("times.ttf", 24)
            font_header = ImageFont.truetype("times.ttf", 28)
        except IOError: # Исправлено на IOError
            font_small = font_main = font_header = ImageFont.load_default()

        # Добавляем текст на первый документ
        today = datetime.now().strftime("%d.%m.%Y")
        draw1.text((1250, 150), f"Страница / 1\nДата публикации\n{today}", font=font_small, fill="black")
        draw1.text((90, 150), f"Соглашение\n№SB-{document_number}", font=font_small, fill="black")

        # Остальной текст на первый документ
        y_position = 500

        part1_text = f"""
Часть I. Общие положения
Настоящее соглашение об оказании юридической помощи заключено между Адвокатским бюро "PACT Attorney" (далее именуемое "Адвокатское бюро"), в состав которого входят действующие адвокаты-партнеры, имеющие право оказывать юридическую помощь клиенту (доверителю) на основании настоящего соглашения (далее именуемые "Адвокаты"), представляемое адвокатом {lawyer_name} (паспорт №{lawyer_passport}, далее именуемый "Адвокат"), и доверителем {client_name} (паспорт №{client_passport}, далее именуемый "Клиент"). Стороны совместно именуются "Стороны".
"""
        y_position = draw_centered_text(draw1, part1_text, y_position, font_main, img1.width)

        part2_text = """
Часть II. Предмет соглашения
2.1. Предметом настоящего соглашения является предоставление Адвокатами и Адвокатским бюро Клиенту по его запросу следующих видов юридической помощи, при условии отсутствия конфликта интересов:
○ Составление и подача заявлений, жалоб, ходатайств и других документов правового характера;
○ Представление интересов Клиента в судопроизводстве и следствии, включая участие в качестве представителя или защитника;
○ Представление интересов Клиента в органах государственной власти и иных организациях;
○ Обеспечение выхода Клиента под залог;
○ Оказание срочной юридической помощи при задержании;
○ Оказание иных видов юридической помощи в рамках законодательства.
"""
        y_position = draw_centered_text(draw1, part2_text, y_position, font_main, img1.width)

        part3_text = """
Часть III. Условия расторжения соглашения
3.1. Настоящее соглашение может быть расторгнуто по следующим основаниям:
○ По взаимному согласию сторон;
○ При возникновении существенных причин в соответствии с законодательством;
○ В случае нарушения одной из сторон условий соглашения — в одностороннем порядке;
○ При отсутствии запросов Клиента на юридической помощи и завершении всех действий, начатых в рамках данного соглашения.
3.2. Уведомление о расторжении соглашения является обязательным и должно быть произведено в письменной форме.
3.3. Соглашение может быть перезаключено с изменением условий, которые распространяются на юридическую помощь, запрошенную после заключения нового соглашения.
"""
        y_position = draw_centered_text(draw1, part3_text, y_position, font_main, img1.width)

        part4_text = """
Часть IV. Обязанности и права адвокатов
4.1. Адвокаты обязаны:
○ Предоставлять юридическую помощь добросовестно и компетентно;
○ Соблюдать конфиденциальность информации Клиента;
○ Действовать в интересах Клиента и в рамках законодательства.
4.2. Адвокаты имеют право:
○ Получать вознаграждение за оказанную помощь;
○ Отказаться от представления интересов Клиента в случаях, предусмотренных законом;
○ Требовать предоставления информации и документов, необходимых для оказания юридической помощи;
○ Защищать интересы Клиента в пределах закона.
"""
        y_position = draw_centered_text(draw1, part4_text, y_position, font_main, img1.width)

        with io.BytesIO() as image_binary1:
            img1.save(image_binary1, 'PNG')
            image_binary1.seek(0)
            file1 = discord.File(fp=image_binary1, filename='help_doc1.png')

            img2 = Image.open(template2_path).convert("RGBA")
            draw2 = ImageDraw.Draw(img2)

            try:
                font_small = ImageFont.truetype("times.ttf", 22)
                font_main = ImageFont.truetype("times.ttf", 28)
                font_lawyer = ImageFont.truetype("times.ttf", 24)
                font_signature = ImageFont.truetype("timesi.ttf", 48)
            except IOError: # Исправлено на IOError
                font_small = font_main = font_lawyer = font_signature = ImageFont.load_default()

            draw2.text((1250, 150), f"Страница / 2\nДата публикации\n{today}", font=font_small, fill="black")
            draw2.text((90, 150), f"Соглашение \n№SB-{document_number}", font=font_small, fill="black")

            y_position = 500

            part5_text = """
Часть V. Передача и распределение вознаграждений
5.1. При оказании юридической помощи клиентам бюро Адвокат обязан передавать полученные вознаграждения в бюро на указанный банковский счет или Управляющему партнеру в соответствии с соглашением об оказании юридической помощи.
5.2. Вознаграждение распределяется между партнерами, участвовавшими в оказании юридической помощи, после удержания согласованного процента на нужды бюро. Расчеты производятся сразу после завершения работы с клиентом.
5.3. Обязательства по передаче вознаграждений в бюро не распространяются на соглашения, заключенные Адвокатом от собственного имени и не от имени бюро. Такие вознаграждения не подлежат удержанию или перераспределению.
"""
            y_position = draw_centered_text(draw2, part5_text, y_position, font_main, img2.width)

            part6_text = """
Часть VI. Финансовые условия
6.1. Клиент обязуется выплатить:
○ Разовое вознаграждение в размере 10 000 долларов США при подписания соглашения;
○ Дополнительные вознаграждения за определенные виды юридической помощи, включая:
○ Подготовку и подачу исковых заявлений, жалоб, ходатайств и иных документов;
○ Представление интересов в судах:
○ В окружном суде: 55 000 долларов США (сторона обвинения) / 60 000 долларов США (сторона защиты);
○ В апелляционном суде: 75 000 долларов США / 80 000 долларов США;
○ В Верховном суде: 100 000 долларов США / 110 000 долларов США;
○ Подачу конституционной жалобы: 80 000 долларов США;
○ Срочную помощь при задержании: 15 000 долларов США;
○ Участие в допросе: 10 000 долларов США.
6.2. При неполном оказании юридической помощи Клиент оплачивает сумму, пропорциональную объему выполненной работы.
6.3. Выплаты производятся на счет Адвокатского бюро или передаются Адвокатам с последующим зачислением на банковский счет бюро.
"""
            y_position = draw_centered_text(draw2, part6_text, y_position, font_main, img2.width)

            part7_text = """
Часть VII. Заключительные положения
7.1. Настоящее соглашение вступает в силу с момента подписания его сторонами.
7.2. Изменения и дополнения к соглашению оформляются в письменной форме и подписываются обеими сторонами.
7.3. Споры, возникающие из настоящего соглашения, разрешаются в порядке, предусмотренном действующим законодательством.
"""
            y_position = draw_centered_text(draw2, part7_text, y_position, font_main, img2.width)

            bottom_margin = 220
            spacing = 20
            left_x = 150
            right_x = img2.width - 450
            y_base = img2.height - bottom_margin

            left_initials = get_initials(lawyer_name)
            draw2.text((left_x, y_base), left_initials, font=font_signature, fill="black")
            draw2.text((left_x, y_base + font_signature.size + spacing), lawyer_name,
                       font=font_lawyer, fill="black")

            right_initials = get_initials(client_name)
            draw2.text((right_x, y_base), right_initials, font=font_signature, fill="black")
            draw2.text((right_x, y_base + font_signature.size + spacing), client_name, font=font_lawyer, fill="black")

            with io.BytesIO() as image_binary2:
                img2.save(image_binary2, 'PNG')
                image_binary2.seek(0)
                file2 = discord.File(fp=image_binary2, filename='help_doc2.png')

                # Отправляем файлы
                await interaction.edit_original_response(attachments=[file1, file2])
                
                # Создаем представление с кнопками для макросов
                macro_view = MacroButtons()

                # Создаем и отправляем эмбед с инструкцией для создания макроса
                macro_embed = discord.Embed(
                    title="📝 Создание макроса DataBase",
                    description="Нажмите на кнопку ниже, чтобы создать макрос. После этого введите номер паспорта или никнейм игрока.",
                    color=discord.Color.blue()
                )
                macro_embed.add_field(
                    name="Инструкция",
                    value="1. Нажмите на кнопку 'Создать макрос на DataBase'\n2. Введите НИК или НОМЕР ПАСПОРТА игрока\n3. Готовый макрос будет отправлен в личном сообщении",
                    inline=False
                )
                macro_embed.set_footer(text="PACT Attorney")
                macro_embed.timestamp = datetime.now()

                # Отправляем эмбед с кнопками
                await interaction.channel.send(embed=macro_embed, view=macro_view)

    except Exception as e:
        print(f"[ОШИБКА /помощь]: {e}")
        try:
            await interaction.followup.send("Произошла ошибка при выполнении команды.", ephemeral=True)
        except discord.errors.InteractionResponded:
            pass  # уже подтверждено, ничего не делаем@bot.event
async def on_guild_channel_delete(channel):
    """Очищает базу данных при удалении канала"""
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM help_data WHERE channel_id = ?", (str(channel.id),))
    conn.commit()
    conn.close()
    print(f"Данные для канала {channel.id} удалены из базы данных.")
    import asyncio
    asyncio.create_task(update_client_registry(bot))

@bot.tree.command(name="удалить_старые_соглашения", description="Удалить старые данные из базы данных")
async def delete_old_agreements(interaction: discord.Interaction):
    if not any(role.id in MOD_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("Только модераторы могут использовать эту команду.", ephemeral=True)
        return

    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM help_data")
    conn.commit()
    conn.close()
    await interaction.response.send_message("Все старые соглашения удалены из базы данных.", ephemeral=True)
# ========== ОБЫЧНЫЕ ТИКЕТЫ ==========
class TicketView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Создать тикет", style=discord.ButtonStyle.green, custom_id="create_ticket")
    async def create_ticket(self, interaction: discord.Interaction, button: ui.Button):
        category = interaction.guild.get_channel(TICKET_CATEGORY_ID)

        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }

        # Даем доступ всем адвокатам и модераторам
        lawyer_role = interaction.guild.get_role(LAWYER_ROLE_ID)
        if lawyer_role:
            overwrites[lawyer_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        for role_id in MOD_ROLE_IDS:
            role = interaction.guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        ticket_channel = await category.create_text_channel(
            f"тикет-{interaction.user.name}",
            topic=f"Тикет пользователя {interaction.user.mention}",
            reason=f"Создан тикет для {interaction.user}",
            overwrites=overwrites
        )

        embed = discord.Embed(
            title="Тикет создан",
            description=f"{interaction.user.mention}, ваш тикет создан. Опишите ваш вопрос.",
            color=discord.Color.green()
        )

        view = TakeTicketView()
        await ticket_channel.send(embed=embed, view=view)
        await interaction.response.send_message(
            f"Ваш тикет создан: {ticket_channel.mention}", ephemeral=True
        )


class TakeTicketView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Взять тикет", style=discord.ButtonStyle.blurple, custom_id="take_ticket")
    async def take_ticket(self, interaction: discord.Interaction, button: ui.Button):
        if not any(role.id == LAWYER_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message("Только адвокаты могут брать тикеты.", ephemeral=True)
            return

        # Ограничиваем доступ к каналу только для адвоката и клиента
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            interaction.channel.guild.owner: discord.PermissionOverwrite(read_messages=True)
        }

        await interaction.channel.edit(overwrites=overwrites)
        embed = discord.Embed(
            title="Тикет взят",
            description=f"{interaction.user.mention} взял этот тикет.",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)
        await interaction.followup.send("Теперь только вы и клиент можете видеть этот канал.")


# ========== ПЛАТЕЖНЫЕ ТИКЕТЫ ==========

class MacroButtons(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Создать макрос на DataBase", style=discord.ButtonStyle.primary, custom_id="persistent_create_database_macro")
    async def create_database_macro(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Проверяем наличие роли адвоката
        if not any(role.id == 1379548122111545354 for role in interaction.user.roles):
            await interaction.response.send_message("Вы должны иметь роль адвоката для использования этой кнопки.", ephemeral=True)
            return

        # Получаем данные адвоката из базы данных
        lawyer = get_lawyer(str(interaction.user.id))
        if not lawyer:
            await interaction.response.send_message("Ошибка: ваши данные не найдены в базе данных адвокатов.", ephemeral=True)
            return

        lawyer_email = lawyer[3]  # Email находится в четвертой колонке

        # Отправляем инструкцию для ввода
        instruction_embed = discord.Embed(
            title="📝 Создание макроса DataBase",
            description="Введите номер паспорта или никнейм игрока для создания макроса.",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=instruction_embed, ephemeral=True)
        
        def check(m):
            return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id
        
        try:
            # Ждем сообщение от пользователя
            response = await interaction.client.wait_for('message', timeout=300.0, check=check)
            player_data = response.content

            # Удаляем сообщение пользователя
            try:
                await response.delete()
            except:
                pass

            # Создаем красивый эмбед для макроса
            macro_embed = discord.Embed(
                title="🔐 Макрос DataBase готов",
                description="Скопируйте код ниже:",
                color=discord.Color.green()
            )
            macro_embed.set_author(
                name=interaction.user.display_name,
                icon_url=interaction.user.display_avatar.url
            )

            # Создаем макрос
            macro_data = [
                "Получение database.gov",
                [
                    {"id": "chat_rp_me", "params": [f"достал телефон, открыл database.gov и ввел в поиск: {player_data}"]},
                    {"id": "chat_rp_do", "params": ["На экране отобразилась информация о гражданине."]},
                    {"id": "chat_rp_me", "params": [f"сделал скриншот и отправил на почту: {lawyer_email}@sa.com"]}
                ]
            ]

            # Импортируем необходимые модули в начале файла
            # Кодируем макрос в base64
            # Используем глобальные импорты base64 и json
            encoded_json = json.dumps(macro_data, ensure_ascii=False).encode('utf-8')
            macro_encoded = base64.b64encode(encoded_json).decode('utf-8')

            # Добавляем макрос в эмбед
            macro_embed.add_field(
                name="Закодированный макрос",
                value=f"```{macro_encoded}```",
                inline=False
            )
            macro_embed.set_footer(text="PACT Attorney | DataBase Macro System")
            macro_embed.timestamp = datetime.now()

            # Отправляем макрос
            await interaction.followup.send(embed=macro_embed, ephemeral=True)

        except asyncio.TimeoutError:
            error_embed = discord.Embed(
                title="❌ Ошибка",
                description="Время ожидания ответа истекло",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=error_embed, ephemeral=True)
        except Exception as e:
            error_embed = discord.Embed(
                title="❌ Ошибка",
                description=f"Произошла ошибка при создании макроса: {e}",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=error_embed, ephemeral=True)

    @discord.ui.button(label="Получить макрос о кадровом аудите фракции", style=discord.ButtonStyle.success, custom_id="persistent_create_audit_macro")
    async def create_audit_macro(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Проверяем наличие роли адвоката
        if not any(role.id == 1379548122111545354 for role in interaction.user.roles):
            await interaction.response.send_message("Вы должны иметь роль адвоката для использования этой кнопки.", ephemeral=True)
            return

        # Получаем данные адвоката из базы данных
        lawyer = get_lawyer(str(interaction.user.id))
        if not lawyer:
            await interaction.response.send_message("Ошибка: ваши данные не найдены в базе данных адвокатов.", ephemeral=True)
            return

        lawyer_email = lawyer[3]

        # Отправляем инструкцию для ввода
        instruction_embed = discord.Embed(
            title="📝 Создание макроса кадрового аудита",
            description="Введите номер паспорта сотрудника для создания макроса.",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=instruction_embed, ephemeral=True)
        
        def check(m):
            return m.author.id == interaction.user.id and m.channel.id == interaction.channel.id
        
        try:
            response = await interaction.client.wait_for('message', timeout=300.0, check=check)
            passport = response.content

            # Удаляем сообщение пользователя
            try:
                await response.delete()
            except:
                pass

            # Создаем красивый эмбед для макроса
            macro_embed = discord.Embed(
                title="🔐 Макрос кадрового аудита готов",
                description="Скопируйте код ниже:",
                color=discord.Color.green()
            )
            macro_embed.set_author(
                name=interaction.user.display_name,
                icon_url=interaction.user.display_avatar.url
            )

            # Создаем макрос для кадрового аудита
            macro_data = [
                "Кадровый аудит фракции",
                [
                    {"id": "chat_rp_me", "params": ["достал и включил планшет"]},
                    {"id": "chat_rp_me", "params": ["открыл кадровый аудит фракции"]},
                    {"id": "chat_rp_me", "params": [f'вписал в поле "Действие": {passport}']},
                    {"id": "chat_rp_do", "params": ["В экране планшета отобразился результат."]},
                    {"id": "chat_rp_me", "params": [f"сохранил результат и отправил на почту: {lawyer_email}@sa.com"]}
                ]
            ]

            encoded_json = json.dumps(macro_data, ensure_ascii=False).encode('utf-8')
            macro_encoded = base64.b64encode(encoded_json).decode('utf-8')

            macro_embed.add_field(
                name="Закодированный макрос",
                value=f"```{macro_encoded}```",
                inline=False
            )
            macro_embed.set_footer(text="PACT Attorney | Audit Macro System")
            macro_embed.timestamp = datetime.now()

            await interaction.followup.send(embed=macro_embed, ephemeral=True)

        except asyncio.TimeoutError:
            await interaction.followup.send("Время ожидания истекло.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Произошла ошибка: {str(e)}", ephemeral=True)

# Классы для системы отзывов
class ReviewModal(discord.ui.Modal, title="⭐ Отзыв о работе адвоката"):
    def __init__(self, lawyer_tag: str, client_name: str):
        super().__init__()
        self.lawyer_tag = lawyer_tag
        self.client_name = client_name
        
        self.rating = discord.ui.TextInput(
            label="⭐ Оценка адвоката (от 1 до 5)",
            placeholder="Введите число от 1 до 5 (где 5 - отлично, 1 - плохо)",
            min_length=1,
            max_length=1,
            required=True,
            style=discord.TextStyle.short
        )
        
        self.review_text = discord.ui.TextInput(
            label="📝 Опишите качество оказанных услуг",
            placeholder="Расскажите о: \n- Профессионализме адвоката\n- Скорости работы\n- Качестве консультации\n- Общем впечатлении",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000  # Увеличили максимальную длину
        )
        
        self.add_item(self.rating)
        self.add_item(self.review_text)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Проверяем валидность оценки
            try:
                rating = int(self.rating.value)
                if not 1 <= rating <= 5:
                    await interaction.response.send_message("❌ Оценка должна быть от 1 до 5!", ephemeral=True)
                    return
            except ValueError:
                await interaction.response.send_message("❌ Пожалуйста, введите число от 1 до 5!", ephemeral=True)
                return

            # Определяем цвет и эмодзи на основе рейтинга
            rating_colors = {
                5: (discord.Color.green(), "🌟"),
                4: (discord.Color.blue(), "⭐"),
                3: (discord.Color.gold(), "⚠️"),
                2: (discord.Color.orange(), "⚠️"),
                1: (discord.Color.red(), "❌")
            }
            embed_color, rating_emoji = rating_colors[rating]

            # Создаем эмбед с отзывом
            review_embed = discord.Embed(
                title=f"{rating_emoji} Новый отзыв о работе адвоката",
                color=embed_color,
                timestamp=datetime.now()
            )
            
            # Добавляем звездный рейтинг
            stars = "⭐" * rating + "☆" * (5 - rating)
            
            review_embed.add_field(
                name="Адвокат",
                value=self.lawyer_tag,
                inline=True
            )
            review_embed.add_field(
                name="Клиент",
                value=self.client_name,
                inline=True
            )
            review_embed.add_field(
                name="Оценка",
                value=stars,
                inline=False
            )
            review_embed.add_field(
                name="Отзыв",
                value=self.review_text.value,
                inline=False
            )
            
            # Отправляем отзыв в канал отзывов
            review_channel = interaction.guild.get_channel(1392607447616720896)
            if review_channel:
                await review_channel.send(embed=review_embed)
                await interaction.response.send_message("Спасибо за ваш отзыв!", ephemeral=True)
            else:
                await interaction.response.send_message("Ошибка: канал для отзывов не найден.", ephemeral=True)

        except ValueError:
            await interaction.response.send_message("Оценка должна быть числом от 1 до 5!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Произошла ошибка при отправке отзыва: {e}", ephemeral=True)

class ReviewButton(discord.ui.View):
    def __init__(self, lawyer_tag: str, client_tag: str):
        super().__init__(timeout=None)  # Важно: timeout=None для постоянной кнопки
        self.lawyer_tag = lawyer_tag
        self.client_tag = client_tag
        
        # Добавляем кнопку при инициализации
        self.review_button = discord.ui.Button(
            label="Оставить отзыв",
            style=discord.ButtonStyle.primary,
            custom_id="leave_review",
            emoji="⭐"
        )
        self.review_button.callback = self.review_button_callback
        self.add_item(self.review_button)

    async def review_button_callback(self, interaction: discord.Interaction):
        # Проверяем, является ли пользователь тем, кому предназначен отзыв
        user_mention = interaction.user.mention
        if not self.client_tag.startswith('<@'):
            # Если client_tag не тег, то просто сравниваем имена
            can_review = True  # Позволяем всем оставлять отзыв в этом случае
        else:
            # Если это тег, проверяем совпадение
            can_review = user_mention == self.client_tag

        if not can_review:
            await interaction.response.send_message(
                "❌ Только указанный клиент может оставить отзыв!",
                ephemeral=True
            )
            return

        try:
            modal = ReviewModal(self.lawyer_tag, self.client_tag)
            await interaction.response.send_modal(modal)
            print(f"✅ Модальное окно отзыва отправлено для {self.client_tag}")
        except Exception as e:
            print(f"❌ Ошибка при отправке модального окна: {e}")
            await interaction.response.send_message(
                "Произошла ошибка при открытии формы отзыва. Попробуйте позже.",
                ephemeral=True
            )

async def send_review_request(guild, lawyer_tag: str, client_tag: str):
    """Отправляет запрос на отзыв в специальный канал"""
    review_channel = guild.get_channel(1392607447616720896)
    if not review_channel:
        print(f"❌ Ошибка: Канал отзывов не найден (ID: 1392607447616720896)")
        return

    try:
        # Создаем эмбед для запроса отзыва
        embed = discord.Embed(
            title="📊 Запрос отзыва",
            description=f"Уважаемый {client_tag}, оставьте отзыв о работе адвоката {lawyer_tag}",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        embed.add_field(
            name="⭐ Как оставить отзыв?",
            value="1. Нажмите на кнопку 'Оставить отзыв' ниже\n2. В появившемся окне укажите оценку от 1 до 5\n3. Напишите свой отзыв о работе адвоката",
            inline=False
        )
        embed.set_footer(text="PACT Attorney | Система отзывов")

        # Создаем новую view с кнопкой для отзыва
        view = ReviewButton(lawyer_tag, client_tag)
        
        # Отправляем сообщение с тегом клиента и кнопкой
        message = await review_channel.send(
            content=client_tag,
            embed=embed,
            view=view
        )
        print(f"✅ Запрос на отзыв успешно отправлен для {client_tag}")
        
        # Проверяем, что сообщение отправилось с кнопкой
        if not message.components:
            print("⚠️ Предупреждение: сообщение отправлено, но кнопка не отображается")
            
    except Exception as e:
        print(f"❌ Ошибка при отправке запроса на отзыв: {e}")
        print(f"Детали ошибки: {traceback.format_exc()}")

# Класс для кнопки "Одобрить перевод"
class ApprovePaymentView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Одобрить перевод", style=discord.ButtonStyle.green, custom_id="approve_payment")
    async def approve_payment(self, interaction: discord.Interaction, button: ui.Button):
        # Проверка, что кнопку нажал модератор
        if not any(role.id in MOD_ROLE_IDS for role in interaction.user.roles):
            await interaction.response.send_message("Только модераторы могут одобрять переводы.", ephemeral=True)
            return

        # Изменяем сообщение
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.add_field(name="Статус", value=f"✅ Оплачено (одобрил: {interaction.user.mention})", inline=False)

        # Убираем кнопку
        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message("Перевод одобрен!", ephemeral=True)

# Команда /pay
# Форма подачи заявки на должность адвоката
class JoinBureauForm(discord.ui.Modal, title='Заявка на должность адвоката'):
    def __init__(self, user):
        super().__init__()
        self.user = user
        
    name = discord.ui.TextInput(
        label='Ваше имя и фамилия',
        placeholder='Введите ваше полное имя...',
        required=True,
    )
    
    passport = discord.ui.TextInput(
        label='Номер паспорта',
        placeholder='Введите номер вашего паспорта...',
        required=True,
    )
    
    phone = discord.ui.TextInput(
        label='Номер телефона',
        placeholder='Введите ваш номер телефона...',
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Создаем канал в нужной категории
            category = interaction.guild.get_channel(1379559114283155580)
            if not category:
                await interaction.response.send_message("Ошибка: категория для заявок не найдена.", ephemeral=True)
                return

            channel_name = f"заявка-{interaction.user.name}"
            
            # Настраиваем права доступа - для управляющих и заявителя
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            
            # Даем доступ управляющим
            for role_id in MOD_ROLE_IDS:
                role = interaction.guild.get_role(role_id)
                if role:
                    overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

            # Создаем канал
            channel = await category.create_text_channel(channel_name, overwrites=overwrites)

            # Формируем email из тега
            email = f"{interaction.user.name}@sa.com"
            
            # Создаем эмбед с заявкой
            embed = discord.Embed(
                title="📝 Новая заявка на должность адвоката",
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            )
            
            embed.add_field(name="Имя и фамилия", value=self.name.value, inline=False)
            embed.add_field(name="Номер паспорта", value=self.passport.value, inline=False)
            embed.add_field(name="Номер телефона", value=self.phone.value, inline=False)
            embed.add_field(name="Discord тег", value=interaction.user.mention, inline=False)
            embed.add_field(name="Email", value=email, inline=False)

            # Создаем кнопку одобрения с правильными параметрами
            view = ApproveApplicationView(
                lawyer_name=self.name.value,
                lawyer_passport=self.passport.value,
                phone=self.phone.value,
                discord_id=str(interaction.user.id),
                email=email,
                manager_name=interaction.user.name,
                manager_id=interaction.user.id
            )
            
            # Отправляем сообщение с заявкой и кнопкой
            await channel.send(
                content=f"Заявка от {interaction.user.mention}\nВнимание <@&1379547784717402152> и <@&1379547989680324750>",
                embed=embed,
                view=view
            )
            
            await interaction.response.send_message(
                "Ваша заявка успешно отправлена! Ожидайте ответа от управляющих.",
                ephemeral=True
            )

        except Exception as e:
            print(f"Ошибка при создании заявки: {e}")
            await interaction.response.send_message(
                "Произошла ошибка при отправке заявки.",
                ephemeral=True
            )

# Класс для кнопки одобрения заявки
class ApproveApplicationView(discord.ui.View):
    def __init__(self, lawyer_name, lawyer_passport, phone, discord_id, email, manager_name=None, manager_id=None):
        super().__init__(timeout=None)
        self.lawyer_name = lawyer_name
        self.lawyer_passport = lawyer_passport
        self.phone = phone
        self.discord_id = discord_id
        self.email = email
        self.manager_name = manager_name
        self.manager_id = manager_id
        
    async def on_timeout(self) -> None:
        # Предотвращаем таймаут кнопки
        return

    @discord.ui.button(label="Одобрить", style=discord.ButtonStyle.green, custom_id="persistent_approve_application")
    async def approve_application(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Проверяем права управляющего
            if not any(role.id in MOD_ROLE_IDS for role in interaction.user.roles):
                await interaction.response.send_message(
                    "❌ Только управляющие могут одобрять заявки.",
                    ephemeral=True
                )
                return

            # Получаем данные управляющего из базы данных
            conn = sqlite3.connect('lawyers.db')
            cursor = conn.cursor()
            cursor.execute(
                "SELECT passport, name FROM lawyers WHERE discord_id = ?",
                (str(interaction.user.id),)
            )
            result = cursor.fetchone()
            conn.close()

            if not result:
                await interaction.response.send_message(
                    "Ошибка: не найден ваш паспорт в базе данных.",
                    ephemeral=True
                )
                return

            manager_passport, manager_name = result

            # Обновляем данные управляющего
            self.manager_name = manager_name
            self.manager_id = str(interaction.user.id)

            # Показываем модальное окно для ввода номера документа
            modal = DocumentNumberModal(
                lawyer_name=self.lawyer_name,
                lawyer_passport=self.lawyer_passport,
                phone=self.phone,
                discord_id=self.discord_id,
                email=self.email,
                manager_name=self.manager_name,
                manager_id=self.manager_id
            )
            await interaction.response.send_modal(modal)

        except Exception as e:
            print(f"Ошибка при одобрении заявки: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Произошла ошибка при обработке заявки.",
                    ephemeral=True
                )

# Модальное окно для ввода номера документа
class DocumentNumberModal(discord.ui.Modal, title='Номер документа'):
    document_number = discord.ui.TextInput(
        label='Введите номер документа',
        placeholder='Введите номер документа...',
        required=True,
    )

    def __init__(self, lawyer_name, lawyer_passport, phone, discord_id, email, manager_name, manager_id):
        super().__init__()
        self.lawyer_name = lawyer_name
        self.lawyer_passport = lawyer_passport
        self.phone = phone
        self.discord_id = discord_id
        self.email = email
        self.manager_name = manager_name
        self.manager_id = manager_id
        self.output_path = None

    @staticmethod
    def draw_centered_text(draw, text, y_position, font, image_width, line_spacing=15):
        """Функция для рисования центрированного текста с переносами"""
        wrapper = textwrap.TextWrapper(width=110)
        paragraphs = [p.strip() for p in text.split('\n') if p.strip()]

        current_y = y_position
        for paragraph in paragraphs:
            lines = wrapper.wrap(paragraph)
            for line in lines:
                text_width = font.getlength(line)
                x = (image_width - text_width) // 2
                draw.text((x, current_y), line, font=font, fill="black")
                current_y += font.size + line_spacing
            current_y += 10
        return current_y

    def _create_document_base(self):
        """Создание базового документа и загрузка шрифтов"""
        img = Image.open("принятие.png").convert("RGBA")
        draw = ImageDraw.Draw(img)

        # Загружаем шрифты
        try:
            font_small = ImageFont.truetype("times.ttf", 22)
            font_main = ImageFont.truetype("times.ttf", 26)
            font_lawyer = ImageFont.truetype("times.ttf", 24)
            font_signature = ImageFont.truetype("timesi.ttf", 48)
        except IOError:
            font_small = font_main = font_lawyer = font_signature = ImageFont.load_default()

        return img, draw, font_small, font_main, font_lawyer, font_signature

    def _create_header(self, draw, font_small):
        """Создание заголовка документа"""
        today = datetime.now().strftime("%d.%m.%Y")
        draw.text((1250, 150), f"Страница / 1\nДата публикации\n{today}", font=font_small, fill="black")
        draw.text((90, 150), f"Соглашение\n№SB-{self.document_number.value}", font=font_small, fill="black")
        return today

    def _generate_agreement_text(self, manager_name, manager_passport):
        """Генерация текста соглашения"""
        return f"""Часть I. Общие положения
Настоящий договор заключен между Адвокатским бюро "PACT Attorney" в лице Управляющего партнера {manager_name} (паспорт № {manager_passport}), именуемого далее "Управляющий партнер", и Адвокатом {self.lawyer_name} (паспорт № {self.lawyer_passport}), именуемого далее "Адвокат". В дальнейшем Управляющий партнер и Адвокат совместно именуются "Стороны".

Часть II. Принятие условий
Заключение настоящего договорa подтверждает согласие Адвоката с условиями, описанными в Партнерском договоре №2265 от 16.08.2025, а также уставом бюро (Внутренний акт бюро №1 от 16.08.2025). Адвокат обязуется соблюдать все условия, положения, права, обязанности и ответственность, установленные этим Партнерским договором и внутренним уставом, а также любыми будущими договорами, заменяющими указанный Партнерский договор Адвокатского бюpo.

Часть III. Обязанности адвоката и ответственность за нарушение обязательств
3.1. Адвокат обязуется предоставлять юридическую помощь доверителям Адвокатского бюро совместно с другими партнерами бюpo.
3.2. Адвокат должен строго соблюдать конфиденциальность, информировать доверителей о ходе дел и действовать исключительно в их интересах компетентно и добросовестно.
3.3. Адвокат обязуется исполнять требования действующего законодательства, Партнерского договора, а также иных внутренних регламентов, регулирующих отношения между доверителями и бюpo.
3.4. В случае неисполнения или ненадлежащего исполнения Адвокатом своих обязательств по настоящему договору или Партнерского договора, он обязан возместить другим партнерам причиненные убытки.

Часть IV. Взносы и финансирование
4.1. За вступление в Адвокатское бюро Адвокат обязуется оплатить вступительный взнос в размере 10 000 долларов США.
4.2. В период членства в бюро Адвокат обязуется вносить еженедельные взносы, сумма которых определяется Управляющим партнером.
4.3. Вступительный взнос и еженедельные взносы не подлежат возврату при выходе или исключении Адвоката из состава бюро.

Часть V. Передача и распределение вознаграждений
5.1. При оказании юридической помощи клиентам бюро Адвокат обязан передавать полученные вознаграждения в бюро на указанный банковский счет или Управляющему партнеру в соответствии с соглашением об оказании юридической помощи.
5.2. Вознаграждение распределяется между партнерами, участвовавшими в оказании юридической помощи, после удержания согласованного процента на нужды бюро. Расчеты производятся сразу после завершения работы с клиентом.
5.3. Обязательства по передаче вознаграждений в бюро не распространяются на соглашения, заключенные Адвокатом от собственного имени и не от имени бюро. Такие вознаграждения не подлежат удержанию или перераспределению.

Часть VI. Заключительные положения
6.1. Настоящий договор вступает в силу с момента его подписания обеими сторонами.
6.2. Все изменения и дополнения к настоящему договору оформляются в письменной форме и подписываются обеими сторонами.
6.3. Споры, возникающие из настоящего договора, разрешаются в порядке, предусмотренном действующим законодательством."""

    async def on_submit(self, interaction: discord.Interaction):
        print("[DEBUG] Начало обработки формы с номером документа")
        try:
            # Сразу откладываем ответ
            await interaction.response.defer(thinking=True)
            print("[DEBUG] Ответ отложен")
            
            # Получаем данные управляющего
            print(f"[DEBUG] Получаем данные управляющего ID: {self.manager_id}")
            conn = sqlite3.connect('lawyers.db')
            cursor = conn.cursor()
            cursor.execute("SELECT passport, name FROM lawyers WHERE discord_id = ?", (str(self.manager_id),))
            result = cursor.fetchone()

            if not result:
                print("[DEBUG] Не найдены данные управляющего в БД")
                await interaction.followup.send("Ошибка: не найден паспорт управляющего в базе данных.", ephemeral=True)
                return

            manager_passport, manager_name = result
            print(f"[DEBUG] Данные управляющего получены: {manager_name}")

            # Создаем документ
            print("[DEBUG] Начинаем генерацию документа")
            img, draw, font_small, font_main, font_lawyer, font_signature = self._create_document_base()
            print("[DEBUG] База документа создана")

            today = self._create_header(draw, font_small)
            print("[DEBUG] Заголовок документа создан")

            # Генерируем текст соглашения
            agreement_text = self._generate_agreement_text(manager_name, manager_passport)
            print("[DEBUG] Текст соглашения сгенерирован")

            # Добавляем текст на документ
            y_position = 500
            y_position = self.draw_centered_text(draw, agreement_text, y_position, font_main, img.width)
            print("[DEBUG] Текст добавлен на документ")

            # Добавляем подписи
            bottom_margin = 160
            spacing = 20
            left_x = 120
            right_x = img.width - 450
            y_base = img.height - bottom_margin

            left_initials = get_initials(manager_name)
            draw.text((left_x, y_base), left_initials, font=font_signature, fill="black")
            draw.text((left_x, y_base + font_signature.size + spacing), manager_name, font=font_lawyer, fill="black")

            right_initials = get_initials(self.lawyer_name)
            draw.text((right_x, y_base), right_initials, font=font_signature, fill="black")
            draw.text((right_x, y_base + font_signature.size + spacing), self.lawyer_name, font=font_lawyer, fill="black")
            print("[DEBUG] Подписи добавлены")

            # Сохраняем документ
            output_filename = f"output_{self.document_number.value}.png"
            img.save(output_filename, 'PNG')
            print(f"[DEBUG] Документ сохранен как {output_filename}")

            # Отправляем документ
            file = discord.File(output_filename, filename='lawyer_acceptance.png')
            await interaction.followup.send(file=file)
            print("[DEBUG] Документ отправлен в Discord")

            # Проверяем существование адвоката в базе
            print("[DEBUG] Проверяем существование адвоката в базе")
            cursor.execute("SELECT discord_id FROM lawyers WHERE discord_id = ?", (self.discord_id,))
            existing_lawyer = cursor.fetchone()
            
            if existing_lawyer:
                print("[DEBUG] Адвокат уже существует в базе")
                await interaction.followup.send("Этот пользователь уже зарегистрирован как адвокат!", ephemeral=True)
                return

            # Добавляем адвоката в базу данных
            print("[DEBUG] Добавляем адвоката в базу данных")
            cursor.execute("""
                INSERT INTO lawyers 
                (passport, name, phone, email, discord_id) 
                VALUES (?, ?, ?, ?, ?)
            """, (self.lawyer_passport, self.lawyer_name, self.phone, self.email, self.discord_id))
            conn.commit()
            print("[DEBUG] Адвокат успешно добавлен в базу данных")

            # Выдаем роль адвоката
            try:
                member = await interaction.guild.fetch_member(int(self.discord_id))
                lawyer_role = interaction.guild.get_role(1379548122111545354)
                if lawyer_role and member:
                    await member.add_roles(lawyer_role)
                    
                    # Устанавливаем никнейм в соответствии с именем и фамилией
                    try:
                        await member.edit(nick=self.lawyer_name)
                    except discord.Forbidden:
                        print(f"[ERROR] Не удалось изменить никнейм пользователя {self.discord_id}")
            except Exception as e:
                print(f"[ERROR] Ошибка при выдаче роли: {str(e)}")

            # Отправляем сообщение в канал кадрового аудита
            audit_channel = interaction.guild.get_channel(1379612435425529899)
            if audit_channel:
                # Создаем эмбед с изображением
                embed = discord.Embed(
                    title="📋 Новый адвокат принят",
                    color=discord.Color.green(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.add_field(name="Имя", value=self.lawyer_name, inline=True)
                embed.add_field(name="Паспорт", value=self.lawyer_passport, inline=True)
                
                # Создаем файл для отправки
                file = discord.File(output_filename, filename='lawyer_acceptance.png')
                # Привязываем изображение к эмбеду через attachment
                embed.set_image(url="attachment://lawyer_acceptance.png")
                embed.add_field(name="Discord", value=f"<@{self.discord_id}>", inline=True)
                embed.add_field(name="Номер документа", value=self.document_number.value, inline=True)
                embed.add_field(name="Принял", value=f"<@{self.manager_id}>", inline=True)
                embed.add_field(name="Дата принятия", value=datetime.now().strftime("%d.%m.%Y"), inline=True)
                
                # Отправляем финальный эмбед с файлом
                await audit_channel.send(file=file, embed=embed)
                
            # Обновляем эмбед с составом бюро
            await update_lawyers_embed(bot, interaction.guild)

            # Делаем кнопку неактивной
            original_message = interaction.message
            if original_message:
                # Создаем новый View с неактивной кнопкой
                new_view = discord.ui.View()
                button = discord.ui.Button(
                    style=discord.ButtonStyle.primary,
                    label="Стать адвокатом бюро",
                    disabled=True,
                    custom_id="join_bureau"
                )
                new_view.add_item(button)
                await original_message.edit(view=new_view)

        except sqlite3.Error as e:
            print(f"[ERROR] Ошибка базы данных: {str(e)}")
            await interaction.followup.send(f"Ошибка при работе с базой данных: {str(e)}", ephemeral=True)
            return
        except Exception as e:
            print(f"[ERROR] Общая ошибка: {str(e)}")
            await interaction.followup.send(f"Произошла ошибка: {str(e)}", ephemeral=True)
            return
        finally:
            conn.close()
            print("[DEBUG] Соединение с базой данных закрыто")

# Класс для кнопки "Стать адвокатом бюро"
class JoinBureauView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Стать адвокатом бюро", 
        style=discord.ButtonStyle.primary,
        custom_id="persistent_join_bureau"
    )
    async def join_bureau_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.send_modal(JoinBureauForm(interaction.user))
        except Exception as e:
            print(f"[ERROR] Ошибка при открытии формы: {str(e)}")
            if not interaction.response.is_done():
                await interaction.response.send_message("Произошла ошибка при открытии формы. Пожалуйста, попробуйте еще раз.", ephemeral=True)

class PaymentClientView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def on_timeout(self) -> None:
        # Предотвращаем таймаут кнопки
        return

    @discord.ui.button(label="Я оплатил", style=discord.ButtonStyle.green, custom_id="persistent_confirm_payment")
    async def confirm_payment(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Проверяем, что это клиент канала
            conn = sqlite3.connect('lawyers.db')
            cursor = conn.cursor()
            cursor.execute("""
                SELECT client_tag
                FROM help_data 
                WHERE channel_id = ?
            """, (str(interaction.channel.id),))
            result = cursor.fetchone()
            conn.close()

            if not result or f"<@{interaction.user.id}>" != result[0]:
                await interaction.followup.send("Только клиент может подтвердить оплату в этом канале!", ephemeral=True)
                return
            # Получаем данные из сообщения
            message_embed = interaction.message.embeds[0]
            
            # Создаем новый embed для управляющего
            manager_embed = discord.Embed(
                title="🔔 Требуется подтверждение платежа",
                description="Клиент подтвердил оплату",
                color=discord.Color.orange(),
                timestamp=datetime.now(timezone.utc)
            )
            
            # Копируем все поля из оригинального эмбеда
            for field in message_embed.fields:
                manager_embed.add_field(name=field.name, value=field.value, inline=field.inline)
            
            # Добавляем ID канала и статус
            manager_embed.add_field(name="ID канала", value=str(interaction.channel.id), inline=False)
            manager_embed.add_field(name="Статус", value="⏳ Ожидает подтверждения скриншотом", inline=False)
            manager_embed.set_footer(text="Отправьте скриншот в ответ на это сообщение")

            # Отправляем уведомление управляющему
            manager = await interaction.client.fetch_user(1068037217898995752)
            msg = await manager.send(embed=manager_embed)
            
            # Создаем новый embed для обновления сообщения в канале
            channel_embed = discord.Embed(
                title=message_embed.title,
                description=message_embed.description,
                color=discord.Color.yellow(),
                timestamp=message_embed.timestamp
            )
            
            # Копируем существующие поля
            for field in message_embed.fields:
                if field.name != "Статус":  # Пропускаем старый статус
                    channel_embed.add_field(name=field.name, value=field.value, inline=field.inline)
            
            # Добавляем новый статус
            channel_embed.add_field(
                name="Статус",
                value="⏳ Клиент подтвердил оплату, ожидается подтверждение управляющего",
                inline=False
            )
            
            # Обновляем сообщение с новым embed и отключенной кнопкой
            button.disabled = True
            await interaction.message.edit(embed=channel_embed, view=self)
            
            # Запускаем напоминания
            interaction.client.loop.create_task(
                send_payment_reminders(msg, interaction.channel.id, None, 
                    channel_embed.fields[0].value, channel_embed.fields[3].value, channel_embed.fields[1].value)
            )
            
            await interaction.followup.send(
                "Спасибо за подтверждение оплаты! Ожидаем проверки управляющим.",
                ephemeral=True
            )
            
        except Exception as e:
            print(f"Ошибка при обработке подтверждения оплаты: {e}")
            await interaction.followup.send(
                "Произошла ошибка при обработке подтверждения.",
                ephemeral=True
            )

async def process_payment_screenshot(message):
    # Проверяем, что это ответ на сообщение с эмбедом
    if not message.reference or not message.attachments:
        print("Сообщение не является ответом или не содержит вложений")
        return
    
    print(f"Обработка скриншота оплаты от {message.author}")

    try:
        # Получаем оригинальное сообщение
        original_msg = await message.channel.fetch_message(message.reference.message_id)
        if not original_msg.embeds:
            return

        embed = original_msg.embeds[0]
        
        # Проверяем, не подтверждено ли уже
        if any(field.name == "Статус" and "✅" in field.value for field in embed.fields):
            await message.reply("Этот платеж уже подтвержден!")
            return

        # Ищем ID канала в эмбеде
        channel_id = None
        for field in embed.fields:
            if field.name == "ID канала":
                channel_id = field.value
                break

        if not channel_id:
            await message.reply("Ошибка: не найден ID канала в сообщении!")
            return

        # Обновляем эмбед в личных сообщениях управляющего
        embed.color = discord.Color.green()
        
        # Находим и обновляем поле статуса
        for field in embed.fields:
            if field.name == "Статус":
                embed.remove_field(embed.fields.index(field))
                break
                
        embed.add_field(name="Статус", value=f"✅ Подтверждено управляющим {message.author.mention}", inline=False)
        embed.add_field(name="Скриншот", value="✅ Прикреплен", inline=False)
        
        await original_msg.edit(embed=embed)
        
        # Обновляем сообщение в канале тикета
        try:
            # Используем bot вместо message.client
            print(f"Попытка получить канал {channel_id}")
            channel = await bot.fetch_channel(int(channel_id))
            if channel:
                print(f"Канал найден: {channel.name}")
                found_message = False
                async for msg in channel.history(limit=100):
                    if msg.embeds:
                        print(f"Проверяем сообщение. Title: {msg.embeds[0].title if msg.embeds[0].title else 'Без заголовка'}")
                        if msg.embeds[0].title and "Счет на оплату" in msg.embeds[0].title:
                            print("Найдено сообщение со счетом на оплату")
                            payment_embed = msg.embeds[0]
                            payment_embed.color = discord.Color.green()
                            # Удаляем старое поле статуса если есть
                            for field in payment_embed.fields:
                                if field.name == "Статус":
                                    payment_embed.remove_field(payment_embed.fields.index(field))
                                    break
                            payment_embed.add_field(name="Статус", value=f"✅ Подтверждено управляющим {message.author.mention}", inline=False)
                            # Получаем URL скриншота
                            screenshot_url = message.attachments[0].url if message.attachments else None
                            if screenshot_url:
                                payment_embed.set_image(url=screenshot_url)
                            await msg.edit(embed=payment_embed)
                            found_message = True
                            print("Сообщение успешно обновлено со скриншотом")
                            break
                if not found_message:
                    print(f"Сообщение со счетом не найдено в канале {channel.name}")
            else:
                print(f"Канал не найден: {channel_id}")
        except Exception as e:
            print(f"Ошибка при обновлении сообщения в канале: {e}")
            
        await message.add_reaction('✅')
        
    except Exception as e:
        print(f"Ошибка при обработке скриншота: {e}")
        await message.add_reaction('❌')

@bot.tree.command(name="pay", description="Выставить счет клиенту")
@app_commands.describe(amount="Сумма оплаты")
async def pay(interaction: discord.Interaction, amount: str):
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Проверяем, является ли отправитель адвокатом
        if not any(role.id == LAWYER_ROLE_ID for role in interaction.user.roles):
            await interaction.followup.send("Только адвокаты могут выставлять счета!", ephemeral=True)
            return
            
        # Проверяем, что команда использована в тикет-канале
        if not isinstance(interaction.channel, discord.TextChannel) or interaction.channel.category_id != TICKET_CATEGORY_ID:
            await interaction.followup.send("Эта команда может использоваться только в тикет-каналах!", ephemeral=True)
            return

        try:
            amount_num = int(amount)
        except ValueError:
            await interaction.followup.send("Сумма должна быть числом!", ephemeral=True)
            return

        # Получаем данные из help_data
        conn = sqlite3.connect('lawyers.db')
        cursor = conn.cursor()
        cursor.execute("""
            SELECT client_name, client_tag, agreement_number 
            FROM help_data 
            WHERE channel_id = ?
        """, (str(interaction.channel.id),))
        result = cursor.fetchone()
        conn.close()

        if not result:
            await interaction.followup.send("Данные для этого канала не найдены.", ephemeral=True)
            return

        client_name, client_tag, agreement_number = result
        
        # Проверяем и исправляем тег клиента
        if client_tag in ['<@None>', 'None', None]:
            # Пытаемся найти участника сервера по имени
            member = discord.utils.find(lambda m: m.name.lower() == client_name.lower(), interaction.guild.members)
            if member:
                # Если нашли участника, обновляем тег в базе данных
                client_tag = member.mention
                conn = sqlite3.connect('lawyers.db')
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE help_data 
                    SET client_tag = ? 
                    WHERE channel_id = ?
                """, (client_tag, str(interaction.channel.id)))
                conn.commit()
                conn.close()
            else:
                # Если не нашли, просто показываем имя без тега
                client_tag = "Нет тега"

        # Создаем эмбед для счета на оплату
        payment_embed = discord.Embed(
            title="💰 Счет на оплату",
            description="Для продолжения работы требуется оплата\n\n**Важно:** Оплатить счет можно переводом на номер паспорта: **401087**",
            color=discord.Color.gold()
        )
        payment_embed.add_field(name="Клиент", value=f"{client_name} {client_tag}" if client_tag != "Нет тега" else client_name, inline=False)
        payment_embed.add_field(name="Сумма к оплате", value=f"${amount}", inline=True)
        payment_embed.add_field(name="Адвокат", value=interaction.user.mention, inline=True)
        payment_embed.add_field(name="Номер соглашения", value=agreement_number, inline=False)
        payment_embed.add_field(name="Статус", value="⏳ Ожидает оплаты", inline=False)
        payment_embed.set_footer(text="После оплаты нажмите кнопку 'Я оплатил' и прикрепите скриншот")

        # Отправляем сообщение с кнопкой оплаты
        view = PaymentClientView()
        await interaction.channel.send(embed=payment_embed, view=view)

        # Создаем эмбед для управляющего
        manager_embed = discord.Embed(
            title="💰 Новый счет на оплату",
            description=f"Выставлен новый счет в канале {interaction.channel.mention}",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        manager_embed.add_field(name="Клиент", value=f"{client_name} {client_tag}" if client_tag != "Нет тега" else client_name, inline=False)
        manager_embed.add_field(name="Сумма", value=f"${amount}", inline=True)
        manager_embed.add_field(name="Адвокат", value=interaction.user.mention, inline=True)
        manager_embed.add_field(name="Номер соглашения", value=agreement_number, inline=False)
        manager_embed.add_field(name="Статус", value="⏳ Ожидает оплаты", inline=False)

        # Отправляем уведомление управляющему
        manager = interaction.guild.get_member(1068037217898995752)  # ID управляющего
        if manager:
            try:
                await manager.send(embed=manager_embed)
            except discord.Forbidden:
                print("Не удалось отправить сообщение управляющему")

        await interaction.followup.send("Счет успешно выставлен.", ephemeral=True)

    except Exception as e:
        print(f"Ошибка при выставлении счета: {e}")
        await interaction.followup.send("Произошла ошибка при выставлении счета.", ephemeral=True)

        # Создаем ветку для оплаты
        thread = await interaction.channel.create_thread(
            name=f"Оплата: {client_name}",
            auto_archive_duration=1440
        )
        
        # Отправляем счет в ветку
        payment_embed = discord.Embed(
            title="💳 Счет на оплату",
            description=f"Клиент {client_name}, вам выставлен счет в размере: **{amount}**.\n\n"
                        f"Оплатить счет можно переводом на номер паспорта: **401087**.",
            color=0x3498db
        )
        
        view = ApprovePaymentView()
        await thread.send(embed=payment_embed, view=view)
        await interaction.followup.send(f"Счет на сумму {amount} создан в ветке {thread.mention}", ephemeral=True)

    except Exception as e:
        print(f"Ошибка в команде pay: {e}")
        await interaction.followup.send("Произошла ошибка при выполнении команды.", ephemeral=True)


async def send_payment_reminders(original_msg, channel_id, lawyer_id, client_name, agreement_number, amount):
    manager_id = 1068037217898995752
    reminder_count = 0
    max_reminders = 15  # Максимальное количество напоминаний
    
    while reminder_count < max_reminders:
        # Проверяем текущее время по МСК (UTC+3)
        now_utc = datetime.now(timezone.utc)
        now_msk = now_utc + timedelta(hours=3)
        
        # Не отправляем напоминания с 00:00 до 10:00 по МСК
        if 0 <= now_msk.hour < 10:
            # Ждем до 10 утра
            wait_hours = 10 - now_msk.hour
            await asyncio.sleep(wait_hours * 3600)
            continue
        
        # Ждем 3 часа перед следующим напоминанием
        await asyncio.sleep(3 * 3600)
        
        try:
            # Проверяем, не был ли уже подтвержден платеж (по истории сообщений)
            channel = original_msg.channel
            async for msg in channel.history(limit=20):
                if msg.reference and msg.reference.message_id == original_msg.id and msg.attachments:
                    return  # Платеж подтвержден
            
            # Отправляем новое напоминание
            reminder_embed = discord.Embed(
                title="⏰ Напоминание: подтверждение платежа",
                description="Пожалуйста, предоставьте скриншот перевода средств",
                color=discord.Color.red()
            )
            reminder_embed.add_field(name="Клиент", value=client_name, inline=False)
            reminder_embed.add_field(name="Сумма", value=f"${amount}", inline=True)
            reminder_embed.add_field(name="Канал", value=f"<#{channel_id}>", inline=True)
            reminder_embed.add_field(name="Номер соглашения", value=agreement_number, inline=False)
            reminder_embed.set_footer(text="Ответьте на оригинальное сообщение с прикрепленным скриншотом")
            
            await original_msg.channel.send(embed=reminder_embed)
            
            reminder_count += 1
        except Exception as e:
            print(f"Ошибка при отправке напоминания: {e}")
            break

def get_db_connection():
    conn = sqlite3.connect('lawyers.db')
    conn.row_factory = sqlite3.Row
    return conn

# Функция для обновления эмбеда
async def update_lawyers_embed(bot, guild):
    print("DEBUG: Начало обновления эмбеда")

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM lawyers")
        lawyers = cursor.fetchall()
    except sqlite3.Error as e:
        print(f"Ошибка при работе с базой данных: {e}")
        lawyers = []
    finally:
        conn.close()

    print(f"DEBUG: Получено {len(lawyers)} адвокатов из базы данных.")

    if guild is None:
        print("Сервер не найден")
        return

    await guild.chunk()
    print(f"DEBUG: Загружено {len(guild.members)} участников сервера.")

    embed = discord.Embed(title="Сотрудники Адвокатского бюро", color=0x00FF00)

    lawyer_dict = {str(lawyer['discord_id']): lawyer for lawyer in lawyers}
    managers_list = []
    manager_ids = set()

    for member in guild.members:
        roles = [role.id for role in member.roles]
        is_manager = any(role_id in MOD_ROLE_IDS for role_id in roles)
        member_id_str = str(member.id)

        if is_manager and member_id_str in lawyer_dict:
            lawyer = lawyer_dict[member_id_str]
            managers_list.append(f"<@{member.id}> | {lawyer['name']} | Телефон: {lawyer['phone']}")
            manager_ids.add(member_id_str)
            print(f"DEBUG: {member.name} добавлен в управляющие.")
        elif is_manager:
            print(f"DEBUG: {member.name} имеет управляющую роль, но не зарегистрирован в базе.")

    embed.add_field(
        name="Управляющие бюро",
        value="\n".join(managers_list) if managers_list else "Нет данных",
        inline=False
    )

    lawyers_list = []
    for discord_id_str, lawyer in lawyer_dict.items():
        if discord_id_str in manager_ids:
            continue

        member = guild.get_member(int(discord_id_str))
        if member is not None:
            lawyers_list.append(f"<@{member.id}> | {lawyer['name']} | Телефон: {lawyer['phone']}")

    embed.add_field(
        name="Адвокаты бюро",
        value="\n".join(lawyers_list) if lawyers_list else "Нет данных",
        inline=False
    )

    channel = bot.get_channel(CHANNEL_ID)

    # Ищем последнее сообщение от бота в канале
    try:
        async for message in channel.history(limit=10):
            if message.author == bot.user and message.embeds:
                print("DEBUG: Найдено существующее сообщение для обновления")
                await message.edit(embed=embed)
                bot.lawyers_message = message  # Сохраняем ссылку на сообщение
                return
    except Exception as e:
        print(f"Ошибка при поиске сообщения: {e}")

    # Если сообщение не найдено, создаем новое
    print("DEBUG: Создание нового эмбеда")
    bot.lawyers_message = await channel.send(embed=embed)

# Команда /добавить_адвоката
@bot.tree.command(name="добавить_адвоката", description="Добавить адвоката в базу данных")
@app_commands.describe(
    паспорт="Номер паспорта адвоката",
    имя="Имя адвоката",
    телефон="Телефон адвоката",
    емэил="Почта адвоката",
    discord_id="Discord ID адвоката"
)
async def add_lawyer_command(
    interaction: discord.Interaction,
    паспорт: str,
    имя: str,
    телефон: str,
    емэил: str,
    discord_id: str
):
    # Проверка роли
    if not any(role.id in MOD_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("Только модераторы могут добавлять адвокатов.", ephemeral=True)
        return

    # Убираем @, если он есть
    if discord_id.startswith("@"):
        discord_id = discord_id[1:]

    # Проверяем, что Discord ID состоит только из цифр
    if not discord_id.isdigit():
        await interaction.response.send_message("Неверный Discord ID. Он должен состоять только из цифр.", ephemeral=True)
        return

    # Добавляем данные адвоката
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO lawyers (passport, name, phone, email, discord_id) VALUES (?, ?, ?, ?, ?)",
                   (паспорт, имя, телефон, емэил, discord_id))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"Адвокат {имя} успешно добавлен в базу данных!", ephemeral=True)
    await update_lawyers_embed(bot, interaction.guild)
# Команда для отправки кнопки набора адвокатов
@bot.tree.command(name="отправить_кнопку_набора", description="Отправить кнопки заявок")
async def send_join_button(interaction: discord.Interaction):
    channel = bot.get_channel(1379610180735467520)

    # Первое сообщение с кнопкой
    embed1 = discord.Embed(
        title="Оказать юр. помощь",
        description="Если нажать на кнопку, то Вам окажут юридическую помощь.",
        color=0x3498db
    )

    view1 = discord.ui.View(timeout=None)
    button1 = discord.ui.Button(
        style=discord.ButtonStyle.primary,
        label="Создать заявку",
        custom_id="persistent_create_ticket"
    )
    button1.callback = lambda i: i.response.send_modal(TicketModal())
    view1.add_item(button1)
    PERSISTENT_VIEWS["create_ticket"] = view1

    await channel.send(embed=embed1, view=view1)

    # Второе сообщение с кнопкой
    embed2 = discord.Embed(
        title="Быть адвокатом бюро",
        description="Если вы хотите работать адвокатом бюро, Вам необходимо заполнить заявку.",
        color=0x2ecc71
    )

    view2 = discord.ui.View(timeout=None)
    button2 = discord.ui.Button(
        style=discord.ButtonStyle.green,
        label="Хочу в бюро!",
        custom_id="persistent_join_bureau"
    )
    button2.callback = lambda i: i.response.send_modal(JoinBureauForm(i.user))
    view2.add_item(button2)
    PERSISTENT_VIEWS["join_bureau"] = view2

    await channel.send(embed=embed2, view=view2)

# Команда /удалить_адвоката
@bot.tree.command(name="удалить_адвоката", description="Удалить адвоката из базы данных")
async def delete_lawyer_command(interaction: discord.Interaction, passport: str):
    # Проверка роли
    if not any(role.id in MOD_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("Только модераторы могут удалять адвокатов.", ephemeral=True)
        return

    # Убедимся, что бот подключен к серверам
    if not bot.guilds:
        await interaction.response.send_message("Бот еще не загрузил данные серверов. Попробуйте снова через несколько секунд.", ephemeral=True)
        return

    # Получаем сервер
    guild = bot.get_guild(bot.guilds[0].id)
    if not guild:
        await interaction.response.send_message("Не удалось получить данные сервера.", ephemeral=True)
        return

    # Удаляем адвоката из базы данных
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM lawyers WHERE passport = ?", (passport,))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"Адвокат с паспортом {passport} успешно удален.", ephemeral=True)
    await update_lawyers_embed(bot, interaction.guild)


# Команда для получения всех соглашений
@bot.tree.command(name="получить_все_соглашения", description="Получить список всех соглашений")
async def get_all_agreements(interaction: discord.Interaction):
    # Проверка роли
    if not any(role.id in MOD_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("Только модераторы могут просматривать все соглашения.", ephemeral=True)
        return

    # Получаем все соглашения из базы данных
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT h.agreement_number, h.client_name, h.client_passport, h.client_tag, h.lawyer_tag, h.channel_id
        FROM help_data h
        ORDER BY h.agreement_number
    """)
    agreements = cursor.fetchall()
    conn.close()

    if not agreements:
        await interaction.response.send_message("В базе данных нет соглашений.", ephemeral=True)
        return

    # Создаем эмбеды для списка соглашений (максимум 25 полей в одном эмбеде)
    embeds = []
    current_embed = discord.Embed(
        title="📑 Список всех соглашений",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    field_count = 0

    for agreement in agreements:
        agreement_number, client_name, client_passport, client_tag, lawyer_tag, channel_id = agreement
        
        # Формируем значение поля
        field_value = []
        if channel_id:
            field_value.append(f"📎 Канал: <#{channel_id}>")
        if client_passport:
            field_value.append(f"🎫 Паспорт клиента: {client_passport}")
        if client_tag and not client_tag.startswith("<@None"):
            field_value.append(f"👤 Клиент Discord: {client_tag}")
        if lawyer_tag and not lawyer_tag.startswith("<@None"):
            field_value.append(f"⚖️ Адвокат: {lawyer_tag}")

        # Добавляем поле в эмбед
        current_embed.add_field(
            name=f"Соглашение №{agreement_number} | {client_name}",
            value="\n".join(field_value) or "Нет дополнительной информации",
            inline=False
        )
        field_count += 1

        # Если достигли лимита полей, создаем новый эмбед
        if field_count == 25:
            embeds.append(current_embed)
            current_embed = discord.Embed(
                title="📑 Список всех соглашений (продолжение)",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            field_count = 0

    # Добавляем последний эмбед, если в нем есть поля
    if field_count > 0:
        embeds.append(current_embed)

    # Отправляем все эмбеды
    for embed in embeds:
        await interaction.followup.send(embed=embed) if embeds.index(embed) > 0 else await interaction.response.send_message(embed=embed)

# Команда для редактирования базы клиентов
@bot.tree.command(name="edit_client_baza", description="Редактировать данные клиента в базе")
async def edit_client_database(interaction: discord.Interaction):
    # Проверка роли
    if not any(role.id in MOD_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("Только модераторы могут изменять данные клиентов.", ephemeral=True)
        return

    # Получаем список всех клиентов
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    cursor.execute("SELECT agreement_number, client_name, client_passport FROM help_data")
    clients = cursor.fetchall()
    conn.close()

    if not clients:
        await interaction.response.send_message("В базе данных нет клиентов.", ephemeral=True)
        return

    # Создаем выбор клиента
    options = [discord.SelectOption(
        label=f"{client_name[:50]}... (№{agreement_number})" if len(client_name) > 50 else f"{client_name} (№{agreement_number})",
        description=f"Паспорт: {client_passport}" if client_passport else "Паспорт не указан",
        value=agreement_number
    ) for agreement_number, client_name, client_passport in clients]

    select = discord.ui.Select(placeholder="Выберите клиента", options=options)

    # Создаем View для первого меню
    view = discord.ui.View()
    view.add_item(select)

    async def select_callback(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        agreement_number = select.values[0]

        data_select = discord.ui.Select(
            placeholder="Выберите что изменить",
            options=[
                discord.SelectOption(label="Имя клиента", value="client_name"),
                discord.SelectOption(label="Паспорт клиента", value="client_passport"),
                discord.SelectOption(label="Тег клиента", value="client_tag"),
                discord.SelectOption(label="Тег адвоката", value="lawyer_tag")
            ]
        )

        async def data_callback(interaction: discord.Interaction):
            field = data_select.values[0]
            modal = EditClientModal(field=field, agreement_number=agreement_number)
            await interaction.response.send_modal(modal)

        data_select.callback = data_callback
        data_view = discord.ui.View()
        data_view.add_item(data_select)
        await interaction.followup.send("Выберите поле для изменения:", view=data_view, ephemeral=True)

    select.callback = select_callback
    await interaction.response.send_message("Выберите клиента для редактирования:", view=view, ephemeral=True)

class EditClientModal(discord.ui.Modal):
    def __init__(self, field: str, agreement_number: str):
        super().__init__(title=f"Изменение данных клиента")
        self.field = field
        self.agreement_number = agreement_number
        self.new_value = discord.ui.TextInput(
            label=f"Новое значение",
            style=discord.TextStyle.short,
            required=True
        )
        self.add_item(self.new_value)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            conn = sqlite3.connect('lawyers.db')
            cursor = conn.cursor()
            cursor.execute(
                f"UPDATE help_data SET {self.field} = ? WHERE agreement_number = ?",
                (self.new_value.value, self.agreement_number)
            )
            conn.commit()
            conn.close()

            await interaction.response.send_message(
                f"Данные клиента успешно обновлены!",
                ephemeral=True
            )
            
            # Обновляем реестр клиентов
            asyncio.create_task(update_client_registry(bot))
        except Exception as e:
            await interaction.response.send_message(
                f"Произошла ошибка при обновлении данных: {e}",
                ephemeral=True
            )

# Команда /изменить_данные_адвоката
@bot.tree.command(name="изменить_данные_адвоката", description="Изменить данные адвоката")
async def update_lawyer_command(interaction: discord.Interaction):

    # Проверка роли
    if not any(role.id in MOD_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("Только модераторы могут изменять данные адвокатов.", ephemeral=True)
        return

    # Получаем список всех адвокатов
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT passport, name FROM lawyers")
    lawyers = cursor.fetchall()
    conn.close()

    if not lawyers:
        await interaction.response.send_message("В базе данных нет адвокатов.", ephemeral=True)
        return

    # Создаем выбор адвоката
    options = [discord.SelectOption(label=f"{name} (Паспорт: {passport})",
               value=str(passport)) for passport, name in lawyers]
    select = discord.ui.Select(placeholder="Выберите адвоката", options=options)

    # Создаем View для первого меню
    view = discord.ui.View()
    view.add_item(select)
    view.timeout = 300  # 5 минут таймаут

    async def select_callback(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        selected_passport = select.values[0]

        # Создаем класс для модального окна внутри обработчика
        class EditModal(discord.ui.Modal, title="Изменение данных адвоката"):
            def __init__(self, field: str, passport: str):
                super().__init__()
                self.field = field
                self.passport = passport
                self.new_value = discord.ui.TextInput(
                    label=f"Новое значение для {field}",
                    style=discord.TextStyle.short,
                    required=True
                )
                self.add_item(self.new_value)

            async def on_submit(self, interaction: discord.Interaction):
                try:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        f"UPDATE lawyers SET {self.field} = ? WHERE passport = ?",
                        (self.new_value.value, self.passport)
                    )
                    conn.commit()
                    conn.close()
                    await interaction.response.send_message(
                        f"Данные адвоката успешно обновлены!",
                # Обновляем реестр клиентов
                        ephemeral=True
                    )
                    import asyncio
                    asyncio.create_task(update_client_registry(bot))
                    guild = bot.get_guild(1379431094205550732)
                    await update_lawyers_embed(bot, guild)
                except Exception as e:
                    print(f"Ошибка при обновлении: {e}")
                    await interaction.response.send_message(
                        "Произошла ошибка при обновлении данных. Проверьте логи.",
                        ephemeral=True
                    )

        data_select = discord.ui.Select(
            placeholder="Выберите что изменить",
            options=[
                discord.SelectOption(label="Имя", value="name"),
                discord.SelectOption(label="Телефон", value="phone"),
                discord.SelectOption(label="Почта", value="email"),
                discord.SelectOption(label="Discord ID", value="discord_id")
            ]
        )

        async def data_callback(interaction: discord.Interaction):
            if not data_select.values:
                await interaction.response.send_message("Не выбрано поле для изменения!", ephemeral=True)
                return

            selected_field = data_select.values[0]
            modal = EditModal(field=selected_field, passport=selected_passport)
            await interaction.response.send_modal(modal)

        data_select.callback = data_callback
        data_view = discord.ui.View()
        data_view.add_item(data_select)
        data_view.timeout = 300

        await interaction.followup.send(
            f"Выбран адвокат с паспортом: {selected_passport}\nВыберите поле для изменения:",
            view=data_view,
            ephemeral=True
        )

    select.callback = select_callback
    await interaction.response.send_message("Выберите адвоката для изменения:", view=view, ephemeral=True)

# ========== КОМАНДЫ НАСТРОЙКИ ==========
@bot.command()
async def setup_tickets(ctx):
    if not ctx.author.guild_permissions.administrator:
        return await ctx.send("У вас нет прав на эту команду.")

    embed = discord.Embed(
        title="Создать тикет",
        description="Нажмите на кнопку ниже, чтобы создать тикет",
        color=discord.Color.blue()
    )

    view = TicketView()
    await ctx.send(embed=embed, view=view)

# СОСТАВЛЕНИЕ ЗАЯВКИ
class TicketModal(ui.Modal, title='Заявка'):
    name = ui.TextInput(
        label='Ваш Ник (Имя И Фамилия)',
        placeholder='Пример: Timosha Morris',
        required=True
    )

    passport = ui.TextInput(
        label='Номер Паспорта',
        required=True
    )

    phone = ui.TextInput(
        label='Номер Телефона',
        required=True
    )

    description = ui.TextInput(
        label='Опишите, Что У Вас Случилось',
        style=discord.TextStyle.long,
        placeholder='Меня посадили без причины. Боди-камеры у меня нет. А откат есть, ниже кину.',
        required=False
    )

    date = ui.TextInput(
        label='Укажите Дату И Промежуток Времени Случивш.',
        placeholder='05.07.2025 кафнули в 10:40, посадили в 11:40',
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Получаем категорию по ID
        category = interaction.guild.get_channel(1379559023124156602)
        if not category:
            await interaction.response.send_message("Категория для тикетов не найдена!", ephemeral=True)
            return

        # Формируем название канала
        username = str(interaction.user).replace("#", "").replace(" ", "_")
        channel_name = f"обращение-{username}"

        # Создаем канал в указанной категории
        try:
            ticket_channel = await interaction.guild.create_text_channel(
                name=channel_name,
                category=category,
                reason=f"Тикет от пользователя {interaction.user}"
            )
        except discord.Forbidden:
            await interaction.response.send_message("У бота нет прав на создание каналов!", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.response.send_message("Ошибка при создании канала!", ephemeral=True)
            return

        embed = discord.Embed(
            title="Обращение",
            description=f"Открыто новое обращение: {ticket_channel.mention}",
            color=0x3498db
        )

        # Отправляем подтверждение пользователю
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

        MANAGER_ROLE_ID = 1379547784717402152  # Управляющие
        LAWYER_ROLE_ID = 1379548122111545354   # Адвокаты

        # Получаем роли
        manager_role = interaction.guild.get_role(MANAGER_ROLE_ID)
        lawyer_role = interaction.guild.get_role(LAWYER_ROLE_ID)

        # Запрещаем доступ всем по умолчанию
        await ticket_channel.set_permissions(
            interaction.guild.default_role,
            read_messages=False,
            send_messages=False
        )

        # Даем доступ создателю тикета (клиенту)
        await ticket_channel.set_permissions(
            interaction.user,
            read_messages=True,
            send_messages=True
        )

        # Даем доступ управляющим
        if manager_role:
            await ticket_channel.set_permissions(
                manager_role,
                read_messages=True,
                send_messages=True
            )

        # Даем доступ всем адвокатам (изначально)
        if lawyer_role:
            await ticket_channel.set_permissions(
                lawyer_role,
                read_messages=True,
                send_messages=True,
                manage_messages=True
            )

        # Даем доступ роли адвокатов
        lawyer_role = interaction.guild.get_role(LAWYER_ROLE_ID)
        if lawyer_role:
            await ticket_channel.set_permissions(
                lawyer_role,
                read_messages=True,
                send_messages=True
            )

        # Запрещаем доступа всем остальным участникам сервера
        await ticket_channel.set_permissions(
            interaction.guild.default_role,
            read_messages=False,
            send_messages=False,
            view_channel=False
        )

        # Первое сообщение (приветственное)
        welcome_embed = discord.Embed(
            title="РАСТ",
            description=(
                "Добро пожаловать!\n\n"
                "Спасибо за обращение в самое лучшее адвокатское бюро \"Р.А.С.Т\"\n"
                "Ожидайте ответа адвоката и не забудьте приложить фото вашего паспорта в этот канал"
            ),
            color=0x00a86b  # Зеленый цвет
        )

        # Второе сообщение (данные из формы)
        data_embed = discord.Embed(
            title="Данные заявки",
            color=0x00a86b  # Синий цвет
        )
        data_embed.add_field(name="👤 Ваш ник (Имя и Фамилия)", value=self.name.value, inline=False)
        data_embed.add_field(name="📄 Номер паспорта", value=self.passport.value, inline=False)
        data_embed.add_field(name="📱 Номер телефона", value=self.phone.value, inline=False)
        data_embed.add_field(name="📝 Опишите, что у Вас случилось", value=self.description.value, inline=False)
        data_embed.add_field(name="📅 Укажите дату и промежуток времени случивш.", value=self.date.value, inline=False)

        # Используем глобальный класс CloseTicketModal
        await interaction.channel.delete(reason=f"Закрыто управляющим: {interaction.user}")

        class TicketButtons(discord.ui.View):
            def __init__(self, client_name: str):
                super().__init__(timeout=None)
                self.client_name = client_name

                  # Кнопка "Закрыть с причиной"
                close_btn = discord.ui.Button(
                    style=discord.ButtonStyle.red,
                    label="Закрыть с причиной",
                    custom_id="persistent_close_ticket"
                )
                close_btn.callback = self.close_ticket
                self.add_item(close_btn)
                PERSISTENT_VIEWS["close_ticket"] = self

                # Кнопка "Начать работу"
                start_btn = discord.ui.Button(
                    style=discord.ButtonStyle.green,
                    label="Начать работу",
                    custom_id="persistent_start_work"
                )
                start_btn.callback = self.start_work
                self.add_item(start_btn)
                PERSISTENT_VIEWS["start_work"] = self

            async def close_ticket(self, interaction: discord.Interaction):
                # Проверяем роль управляющего
                if MANAGER_ROLE_ID not in [role.id for role in interaction.user.roles]:
                    await interaction.response.send_message(
                        "Только управляющие могут закрывать обращения!",
                        ephemeral=True
                    )
                    return

                # Открываем модальное окно
                await interaction.response.send_modal(CloseTicketModal())

            async def start_work(self, interaction: discord.Interaction):
                # Проверяем роль адвоката
                if LAWYER_ROLE_ID not in [role.id for role in interaction.user.roles]:
                    await interaction.response.send_message(
                        "Только адвокаты могут начинать работу!",
                        ephemeral=True
                    )
                    return

                await interaction.response.defer()

                # Обновляем статистику адвоката
                conn = sqlite3.connect('lawyers.db')
                cursor = conn.cursor()

                # Увеличиваем счетчик обращений
                cursor.execute('''
                INSERT OR IGNORE INTO lawyer_stats (lawyer_id, cases_taken)
                VALUES (?, 0)
''', (str(interaction.user.id),))

                cursor.execute('''
                UPDATE lawyer_stats
                SET cases_taken = cases_taken + 1
                WHERE lawyer_id = ?
''', (str(interaction.user.id),))

                conn.commit()
                conn.close()

                # Получаем всех адвокатов на сервере
                lawyer_role = interaction.guild.get_role(1379548122111545354)
                all_lawyers = lawyer_role.members if lawyer_role else []

                # Убираем права просмотра у всех адвокатов
                for lawyer in all_lawyers:
                    if lawyer.id != interaction.user.id:
                        await interaction.channel.set_permissions(
                            lawyer,
                            view_channel=False,
                            read_messages=False
                        )
                        await asyncio.sleep(1.2)

                # Создаем embed-ответ
                embed = discord.Embed(
                    title="Принятое обращение",
                    description=f"Ваше обращение будет обработано {interaction.user.mention}",
                    color=0x00FF00
                )

                # Отправляем новое сообщение
                await interaction.followup.send(embed=embed)

                # Находим кнопку "Начать работу" и отключаем ее
                for child in self.children:
                    if isinstance(child, discord.ui.Button) and child.custom_id == "persistent_start_work":
                        child.disabled = True
                        break

                await interaction.message.edit(view=self)

                channel_name = interaction.channel.name
                username = channel_name.split('-', 1)[1]
                # Ищем пользователя на сервере
                user = discord.utils.get(interaction.guild.members, name=username)
                tag_client = user.mention

                # Сохраняем в БД
                conn = sqlite3.connect('lawyers.db')
                cursor = conn.cursor()
                cursor.execute('''
                INSERT OR REPLACE INTO tickets
                (channel_id, lawyer_id, client_id, nickname)
                VALUES (?, ?, ?, ?)
''', (interaction.channel.id, str(interaction.user.id), str(user.id) if user else None, self.client_name))
                conn.commit()
                conn.close()

        # Отправляем сообщения в новый канал
        ticket_view = TicketButtons(client_name=str(self.name.value))
        bot.add_view(ticket_view)  # Регистрируем вью
        await ticket_channel.send(embed=welcome_embed)
        await ticket_channel.send(embed=data_embed)
        await ticket_channel.send(view=ticket_view)
        await ticket_channel.send(f"Новое обращение! {lawyer_role.mention}")

async def send_ticket_embeds(channel):
    """Упрощённая версия функции с персистентными кнопками"""
    if not channel:
        print("❌ Канал не найден")
        return

    try:
        await channel.purge(limit=None)
    except Exception as e:
        print(f"Ошибка очистки канала: {e}")
        return

    # Первое сообщение с кнопкой
    embed1 = discord.Embed(
        title="Оказать юр. помощь",
        description="Если нажать на кнопку, то Вам окажут юридическую помощь.",
        color=0x3498db
    )

    view1 = discord.ui.View(timeout=None)
    button1 = discord.ui.Button(
        style=discord.ButtonStyle.primary,
        label="Создать заявку",
        custom_id="persistent_create_ticket"
    )
    button1.callback = lambda i: i.response.send_modal(TicketModal())
    view1.add_item(button1)
    PERSISTENT_VIEWS["create_ticket"] = view1

    await channel.send(embed=embed1, view=view1)

    # Второе сообщение с кнопкой
    embed2 = discord.Embed(
        title="Быть адвокатом бюро",
        description="Если вы хотите работать адвокатом бюро, Вам необходимо заполнить заявку.",
        color=0x2ecc71
    )

    view2 = discord.ui.View(timeout=None)
    button2 = discord.ui.Button(
        style=discord.ButtonStyle.green,
        label="Хочу в бюро!",
        custom_id="persistent_join_bureau"
    )
    button2.callback = lambda i: i.response.send_modal(JoinBureauForm(i.user))
    view2.add_item(button2)
    PERSISTENT_VIEWS["join_bureau"] = view2

    await channel.send(embed=embed2, view=view2)

# Команда добавления участника в тикет
@bot.tree.command(name="добавить_в_тикет", description="Добавляет участника в тикет")
async def add_to_ticket(interaction: discord.Interaction, member: discord.Member):
    # Только управляющие и адвокаты могут добавлять
    if not any(role.id in [1379547784717402152, 1379548122111545354] for role in interaction.user.roles):
        await interaction.response.send_message("Недостаточно прав!", ephemeral=True)
        return

    await interaction.channel.set_permissions(
        member,
        read_messages=True,
        send_messages=True
    )
    await interaction.response.send_message(
        f"Пользователь {member.mention} добавлен в тикет",
        ephemeral=True
    )

# Команда удаления участника из тикета
@bot.tree.command(name="убрать_из_тикета", description="Убирает участника из тикета")
async def remove_from_ticket(interaction: discord.Interaction, member: discord.Member):
    # Только управляющие и адвокаты могут добавлять
    if not any(role.id in [1379547784717402152, 1379548122111545354] for role in interaction.user.roles):
        await interaction.response.send_message("Недостаточно прав!", ephemeral=True)
        return

    await interaction.channel.set_permissions(
        member,
        read_messages=False,
        send_messages=False
    )
    await interaction.response.send_message(
        f"Пользователь {member.mention} удален из тикета",
        ephemeral=True
    )

# Команда переназначения адвоката
@bot.tree.command(name="переназначить", description="Переназначает ответственного адвоката")
async def reassign_ticket(interaction: discord.Interaction, new_lawyer: discord.Member):
    # Проверяем что это управляющий
    MANAGER_ROLE_ID = 1379547784717402152
    if MANAGER_ROLE_ID not in [role.id for role in interaction.user.roles]:
        await interaction.response.send_message("Только управляющие могут переназначать!", ephemeral=True)
        return
    # Update lawyer_tag in help_data
    conn2 = sqlite3.connect('lawyers.db')
    c2 = conn2.cursor()
    c2.execute("UPDATE help_data SET lawyer_tag = ? WHERE channel_id = ?", (f"<@{new_lawyer.id}>", str(interaction.channel.id)))
    conn2.commit()
    conn2.close()

    # Refresh registry channel
    import asyncio
    asyncio.create_task(update_client_registry(bot))

    # Получаем данные из БД для текущего тикета
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    cursor.execute('SELECT lawyer_id, client_id, nickname FROM tickets WHERE channel_id = ?', (str(interaction.channel.id),))
    result = cursor.fetchone()
    conn.close()

    current_lawyer_id = None
    client_id = None
    nickname = None

    if result:
        current_lawyer_id, client_id, nickname = result
        # Если client_id или nickname отсутствуют, это проблема
        if not client_id or not nickname:
            await interaction.response.send_message("Неполная информация о тикете в базе данных (отсутствует client_id или nickname).", ephemeral=True)
            return

    # Обновляем или добавляем запись в БД
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()

    # Используем INSERT OR REPLACE, чтобы либо обновить, либо добавить новую запись
    # Важно: Для INSERT OR REPLACE нужны все поля, включая client_id и nickname,
    # которые мы уже получили.
    cursor.execute('''
    INSERT OR REPLACE INTO tickets
    (channel_id, lawyer_id, client_id, nickname)
    VALUES (?, ?, ?, ?)
''', (
        str(interaction.channel.id),  # ID текущего канала
        str(new_lawyer.id),           # ID нового адвоката
        str(client_id),               # Полученный client_id
        str(nickname)                 # Полученный nickname
    ))
    conn.commit()
    conn.close()

    # Меняем права доступа
    LAWYER_ROLE_ID = 1379548122111545354
    lawyer_role = interaction.guild.get_role(LAWYER_ROLE_ID)

    await interaction.response.send_message(
        f"Ответственный адвокат изменен на {new_lawyer.mention}",
        ephemeral=True
    )

    # Отбираем права у текущего адвоката (если он был назначен)
    if current_lawyer_id:
        try:
            # current_lawyer_id может быть упоминанием, нужно получить ID
            # Убедимся, что current_lawyer_id - это строка с ID, а не упоминание
            current_lawyer_id_numeric = int(current_lawyer_id.strip('<@>'))
            current_lawyer_member = await interaction.guild.fetch_member(current_lawyer_id_numeric)
            if current_lawyer_member:
                await interaction.channel.set_permissions(
                    current_lawyer_member,
                    read_messages=False,
                    send_messages=False,
                    # Важно: убедитесь, что права не отбираются у клиента или модераторов
                    # Здесь мы только убираем права у адвокатов, которые не являются новым адвокатом
                    # Если current_lawyer_member == new_lawyer, то ничего не делаем
                    # Это уже учтено в логике ниже, где права новому адвокату даются.
                )
        except Exception as e:
            print(f"Не удалось найти или изменить права текущего адвоката (ID: {current_lawyer_id}): {e}")

    # Предоставляем права новому адвокату
    if lawyer_role:
        await interaction.channel.set_permissions(
            new_lawyer,
            read_messages=True,
            send_messages=True
        )
    else:
        print(f"Роль адвоката с ID {LAWYER_ROLE_ID} не найдена.")

    # Если у новых адвокатов были права, а теперь их нужно отобрать,
    # кроме нового адвоката
    if lawyer_role:
        for member in lawyer_role.members:
            if member != new_lawyer and member.id != interaction.user.id: # Исключаем нового адвоката и вызвавшего команду (управляющего)
                # Проверяем, есть ли у member права на чтение в этом канале
                # Это может быть сложно, так как права могут быть наследуемыми или переопределенными
                # Проще всего установить права всем, кроме нового адвоката, на "нет".
                try:
                    await interaction.channel.set_permissions(
                        member,
                        read_messages=False,
                        send_messages=False,
                        view_channel=False
                    )
                    await asyncio.sleep(1.1) # Небольшая пауза, чтобы избежать лимитов API
                except Exception as e:
                    print(f"Не удалось изменить права для участника {member.name}: {e}")

    # Перемещаем канал, если нужно (здесь ваш код для перемещения)
    # Предполагаем, что 'no_archive_category' - это ID категории для активных тикетов
    ACTIVE_TICKET_CATEGORY_ID = 1379559023124156602
    active_category = interaction.guild.get_channel(ACTIVE_TICKET_CATEGORY_ID)

    if active_category and interaction.channel.category_id != active_category.id:
        try:
            await interaction.channel.edit(category=active_category)
        except Exception as e:
            print(f"Не удалось переместить канал в категорию {active_category.name}: {e}")

@bot.tree.command(name="освободиться", description="Освобождает тикет для всех адвокатов")
async def release_ticket(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=True)

        conn = sqlite3.connect('lawyers.db')
        cursor = conn.cursor()
        cursor.execute('SELECT lawyer_id FROM tickets WHERE channel_id = ?', (str(interaction.channel.id),))
        result = cursor.fetchone()
        conn.close()

        if not result:
            await interaction.followup.send("Этот тикет не назначен никому!", ephemeral=True)
            return

        # Извлекаем ID адвоката из БД (числовой ID)
        lawyer_raw = result[0]
        try:
            import re as _re
            lawyer_id = int(_re.sub(r'\D', '', str(lawyer_raw))) if lawyer_raw is not None else None
        except Exception:
            await interaction.followup.send("Ошибка: неверный формат ID адвоката в БД", ephemeral=True)
            return
            return

        is_lawyer = str(interaction.user.id) == str(lawyer_id)
        is_manager = 1379547784717402152 in [role.id for role in interaction.user.roles]

        if not (is_lawyer or is_manager):
            await interaction.followup.send("Вы не можете освободить этот тикет!", ephemeral=True)
            return

        # Используем глобальный класс CloseTicketModal

        class TicketButtons(discord.ui.View):
            def __init__(self, client_name: str):
                super().__init__(timeout=None)
                self.client_name = client_name

            @discord.ui.button(
                style=discord.ButtonStyle.red,
                label="Закрыть с причиной",
                custom_id="close_ticket"
            )
            async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
                # Проверяем роль управляющего
                if 1379547784717402152 not in [role.id for role in interaction.user.roles]:
                    await interaction.response.send_message(
                        "Только управляющие могут закрывать обращения!",
                        ephemeral=True
                    )
                    return

                # Открываем модальное окно
                await interaction.response.send_modal(CloseTicketModal())

            @discord.ui.button(
                style=discord.ButtonStyle.green,
                label="Начать работу",
                custom_id="start_work",
            )
            async def start_work(self, interaction: discord.Interaction, button: discord.ui.Button):
                # Проверяем роль адвоката
                if LAWYER_ROLE_ID not in [role.id for role in interaction.user.roles]:
                    await interaction.response.send_message(
                        "Только адвокаты могут начинать работу!",
                        ephemeral=True
                    )
                    return

                # Откладываем ответ
                await interaction.response.defer(ephemeral=True)

                # Обновляем статистику адвоката
                conn = sqlite3.connect('lawyers.db')
                cursor = conn.cursor()

                # Увеличиваем счетчик обращений
                cursor.execute('''
                INSERT OR IGNORE INTO lawyer_stats (lawyer_id, cases_taken)
                VALUES (?, 0)
''', (str(interaction.user.id),))

                cursor.execute('''
                UPDATE lawyer_stats
                SET cases_taken = cases_taken + 1
                WHERE lawyer_id = ?
''', (str(interaction.user.id),))

                conn.commit()
                conn.close()

                # Извлекаем ID адвоката из упоминания
                lawyer_mention = result[0] # result получен ранее
                try:
                    lawyer_id_from_db = int(lawyer_mention.strip('<@>'))
                except:
                    await interaction.followup.send("Ошибка: неверный формат ID адвоката в БД", ephemeral=True)
                    return

                # Получаем всех адвокатов на сервере
                lawyer_role = interaction.guild.get_role(1379548122111545354)
                all_lawyers = lawyer_role.members if lawyer_role else []

                # Убираем права просмотра у всех адвокатов, кроме нового
                for lawyer in all_lawyers:
                    if lawyer.id != interaction.user.id:
                        await interaction.channel.set_permissions(
                            lawyer,
                            view_channel=False,
                            read_messages=False
                        )
                        await asyncio.sleep(1.2)

                # Создаем embed-ответ
                embed = discord.Embed(
                    title="Принятое обращение",
                    description=f"Ваше обращение будет обработано {interaction.user.mention}",
                    color=0x00FF00
                )

                # Отправляем новое сообщение
                await interaction.followup.send(embed=embed)

                # Находим кнопку "Начать работу" и отключаем ее
                for child in self.children:
                    if isinstance(child, discord.ui.Button) and child.custom_id == "persistent_start_work":
                        child.disabled = True
                        break

                await interaction.message.edit(view=self)

                channel_name = interaction.channel.name
                username = channel_name.split('-', 1)[1]
                # Ищем пользователя на сервере
                user = discord.utils.get(interaction.guild.members, name=username)
                tag_client = user.mention

                # Сохраняем в БД
                conn = sqlite3.connect('lawyers.db')
                cursor = conn.cursor()
                cursor.execute('''
                INSERT OR REPLACE INTO tickets
                (channel_id, lawyer_id, client_id, nickname)
                VALUES (?, ?, ?, ?)
''', (interaction.channel.id, str(interaction.user.id), str(user.id) if user else None, self.client_name))
                conn.commit()
                conn.close()

        # Восстанавливаем кнопку "Начать работу"
        async for message in interaction.channel.history(limit=100):
            if message.components:
                # Получаем оригинальное View из сообщения
                conn = sqlite3.connect('lawyers.db')
                cursor = conn.cursor()
                cursor.execute('''
                SELECT nickname FROM tickets
                WHERE channel_id = ?
''', (str(interaction.channel.id),))

                result = cursor.fetchone()
                conn.close()

                view = TicketButtons(client_name=str(result[0]))

                # Ищем нужную кнопку
                for child in view.children:
                    if isinstance(child, discord.ui.Button) and child.custom_id == "persistent_start_work":
                        # Включаем кнопку обратно
                        child.disabled = False
                        await message.edit(view=view)
                        break

        # Даем доступ всем адвокатам
        lawyer_role = interaction.guild.get_role(1379548122111545354)
        if lawyer_role:
            for member in lawyer_role.members:
                await interaction.channel.set_permissions(
                    member,
                    read_messages=True,
                    send_messages=True
                )
                await asyncio.sleep(1.2)

        # Удаляем запись из БД
        conn = sqlite3.connect('lawyers.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM tickets WHERE channel_id = ?', (str(interaction.channel.id),))
        conn.commit()
        conn.close()

        # Отправляем подтверждение
        await interaction.followup.send(
            "Тикет освобожден и доступен всем адвокатам",
            ephemeral=True
        )

    except Exception as e:
        print(f"Ошибка в команде 'освободиться': {e}")
        await interaction.followup.send(
            "Произошла ошибка при обработке команды",
            ephemeral=True
        )

# Функция для расчета суммы адвоката
def calculate_lawyer_amount(amount: int) -> int:
    # Особые случаи
    if amount == 75000:
        base_amount = 65000
    elif amount == 95000:
        base_amount = 85000
    elif amount == 70000:
        base_amount = 55000
    elif amount == 90000:
        base_amount = 75000   
    elif amount == 110000:
        base_amount = 95000     
    elif amount in [55000, 45000]:
        return 24000    
    else:
        base_amount = amount
    
    # Расчет 60% от базовой суммы
    return int(base_amount * 0.6)

# Класс для кнопки подтверждения оплаты
class PaymentConfirmButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def on_timeout(self) -> None:
        # Предотвращаем таймаут кнопки
        return

    @discord.ui.button(label="Оплачено", style=discord.ButtonStyle.green, custom_id="persistent_confirm_lawyer_payment")
    async def confirm_payment(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Проверяем, что кнопку нажал управляющий
        if interaction.user.id != 1068037217898995752:
            await interaction.response.send_message("❌ Только управляющий может подтвердить оплату!", ephemeral=True)
            return

        # Обновляем эмбед
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.add_field(name="Статус", value="✅ Оплачено", inline=False)
        embed.set_footer(text=f"Подтверждено управляющим {interaction.user.mention} • {datetime.now().strftime('%d.%m.%Y %H:%M')}")
        
        # Удаляем кнопку и обновляем сообщение
        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message("✅ Оплата успешно подтверждена", ephemeral=True)

class TipsButton(discord.ui.View):
    def __init__(self, lawyer_passport: str):
        super().__init__(timeout=None)
        self.lawyer_passport = lawyer_passport

    @discord.ui.button(label="Оставить чаевые", style=discord.ButtonStyle.green, emoji="💸", custom_id="give_tips")
    async def give_tips(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Ищем информацию о канале в теге
        review_channel = interaction.guild.get_channel(1392607447616720896)
        review_channel_mention = f"<#{1392607447616720896}>" if review_channel else "#отзывы"

        tips_embed = discord.Embed(
            title="💸 Отправка чаевых",
            description="Спасибо за желание отблагодарить адвоката!",
            color=discord.Color.green()
        )
        tips_embed.add_field(
            name="Счет для перевода",
            value=f"```{self.lawyer_passport}```",
            inline=False
        )
        tips_embed.add_field(
            name="📝 Не забудьте оставить отзыв!",
            value=f"Пожалуйста, оставьте отзыв о работе адвоката в канале {review_channel_mention}\nВаше мнение очень важно для нас!",
            inline=False
        )
        tips_embed.set_footer(text="Чаевые - это отличный способ поблагодарить адвоката за качественную работу!")
        
        await interaction.response.send_message(
            embed=tips_embed,
            ephemeral=True
        )

@bot.tree.command(name="закончить_работу", description="Завершает работу по иску и перемещает в архив")
@app_commands.describe(
    claim_link="Ссылка на исковой документ",
    photo="Фото последней страницы приговора"
)
async def finish_work(
    interaction: discord.Interaction,
    claim_link: str,
    photo: discord.Attachment
):
    print(f"Начало выполнения команды закончить_работу для канала {interaction.channel.name}")
    
    # Отложим ответ сразу в начале
    await interaction.response.defer(ephemeral=True)
    
    # Проверяем роль адвоката
    if not any(role.id == LAWYER_ROLE_ID for role in interaction.user.roles):
        await interaction.followup.send("❌ Только адвокаты могут использовать эту команду!", ephemeral=True)
        return

    try:
        # Получаем данные адвоката
        lawyer_data = get_lawyer(str(interaction.user.id))
        if not lawyer_data:
            await interaction.followup.send("Ваши данные не найдены в базе данных адвокатов!", ephemeral=True)
            return

        # Получаем канал для выплат
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            await interaction.followup.send("Канал для логов не найден.", ephemeral=True)
            return

        # Создаем эмбед для выплаты
        payment_embed = discord.Embed(
            title="💰 Новый счет на выплату",
            description="В связи с выполненной работой!",
            color=discord.Color.blue()
        )
        
        # Создаем ссылку на канал
        channel_link = f"https://discord.com/channels/{interaction.guild.id}/{interaction.channel.id}"
        
        payment_embed.add_field(name="Обращение", value=claim_link, inline=False)
        payment_embed.add_field(
            name="Канал обращения", 
            value=f"[#{interaction.channel.name}]({channel_link})", 
            inline=False
        )
        payment_embed.add_field(
            name="Адвокат, выполнявший работу", 
            value=f"{interaction.user.mention} ({lawyer_data[1]})", 
            inline=False
        )
        
        # Отправляем эмбед в канал логов
        await log_channel.send(embed=payment_embed, view=PaymentConfirmButton())

        # Создаем эмбед для благодарности
        thanks_embed = discord.Embed(
            title="🙏 Благодарим за доверие!",
            description=(
                "Огромное спасибо Вам за доверие и обращение в наше бюро!\n\n"
                "Не хотели бы вы оставить чаевые адвокату, который вам помог?"
            ),
            color=discord.Color.gold()
        )

        # Отправляем сообщение с благодарностью и кнопкой для чаевых
        await interaction.channel.send(embed=thanks_embed, view=TipsButton(lawyer_data[0]))
        
        # Архивируем канал
        await interaction.channel.edit(archived=True)
        await interaction.followup.send("✅ Работа завершена, канал архивирован. Счет на выплату отправлен.", ephemeral=True)

    except Exception as e:
        print(f"Ошибка в команде 'закончить_работу': {e}")
        await interaction.followup.send(f"Произошла ошибка: {str(e)}", ephemeral=True)

    try:
        # Получаем данные адвоката
        lawyer_data = get_lawyer(str(interaction.user.id))
        if not lawyer_data:
            await interaction.followup.send("Ваши данные не найдены в базе данных адвокатов!", ephemeral=True)
            return
            
        print(f"Данные адвоката получены: {lawyer_data}")

        # Находим время последнего перемещения канала из архива
        last_unarchive_time = None
        print(f"Проверяем историю аудита канала {interaction.channel.name}")
        
        try:
            async for entry in interaction.guild.audit_logs(
                action=discord.AuditLogAction.channel_update,
                limit=100
            ):
                if (entry.target and entry.target.id == interaction.channel.id and 
                    hasattr(entry.before, 'category') and hasattr(entry.after, 'category') and
                    entry.before.category != entry.after.category):
                    # Если канал был перемещен из архивной категории
                    if (entry.before.category and 
                        hasattr(entry.before.category, 'id') and 
                        entry.before.category.id == 1379559023124156602):
                        last_unarchive_time = entry.created_at
                        print(f"Найдено последнее перемещение из архива: {last_unarchive_time}")
                        break
        except Exception as e:
            print(f"Ошибка при проверке аудита: {e}")
            
        # Инициализируем переменные
        total_payment = 0
        payments_found = []
        payment_details = []
        total_amount = 0
        
        print(f"Поиск платежей в канале {interaction.channel.name}")
        
        # Ищем момент последнего вызова команды /закончить_работу
        last_finish_time = None
        async for message in interaction.channel.history(limit=100, oldest_first=False):
            try:
                # Проверяем является ли сообщение командой /закончить_работу
                if message.author.id == bot.user.id and "по вашему обращению был вынесен приговор" in message.content:
                    last_finish_time = message.created_at
                    print(f"Найдено предыдущее завершение работы от {last_finish_time}")
                    break
            except:
                continue
        
        print("Поиск платежей после последней команды /закончить_работу")

        async for message in interaction.channel.history(limit=500, oldest_first=False):  # Проверяем от новых к старым
            # Если нашли предыдущую команду /закончить_работу, пропускаем все сообщения до неё
            if last_finish_time and message.created_at < last_finish_time:
                print(f"Пропускаем сообщение от {message.created_at} (до последней команды /закончить_работу)")
                continue
                
            # Проверяем эмбеды в сообщении
            if message.embeds:
                for embed in message.embeds:
                    print(f"Проверяем эмбед с заголовком: {embed.title}")
                    if embed.title == "💰 Счет на оплату":
                        try:
                            # Ищем поле с суммой
                            for field in embed.fields:
                                if field.name in ["Сумма", "Сумма к оплате"]:
                                    print(f"Найдено поле с суммой: {field.value}")
                                    # Извлекаем число из строки, убирая все лишние символы и знак доллара
                                    amount_str = ''.join(filter(str.isdigit, field.value.replace('$', '')))
                                    if amount_str:
                                        amount = int(amount_str)
                                        if amount > 0:  # Проверяем, что сумма положительная
                                            payment_date = message.created_at.strftime("%d.%m.%Y %H:%M")
                                            if amount not in payments_found:  # Проверяем на дубликаты
                                                payments_found.append(amount)
                                                payment_details.append(f"💰 {amount:,} $ ({payment_date})")
                                                print(f"Найден платеж в эмбеде: {amount} от {payment_date}")
                                            break
                        except Exception as e:
                            print(f"Ошибка при обработке эмбеда: {e}")
            else:
                print(f"Сообщение без эмбедов: {message.content[:100]}")

        print(f"\nВсего найдено платежей: {len(payments_found)}")
        
        # Рассчитываем суммы платежей
        payment_breakdown = []
        total_lawyer_amount = 0
        
        print("\nРасчет платежей:")
        for amount in payments_found:
            lawyer_amount = calculate_lawyer_amount(amount)
            total_lawyer_amount += lawyer_amount
            payment_breakdown.append(f"С {amount:,} $ → {lawyer_amount:,} $")
            print(f"Расчет для платежа {amount}: {lawyer_amount}")
        
        if not payments_found:
            print("Платежи не найдены. Последние сообщения в канале:")
            async for message in interaction.channel.history(limit=10):
                print(f"[{message.created_at}] {message.author}: {message.content}")
            
            # Спрашиваем подтверждение вместо возврата
            payment_info = "⚠️ В канале не найдена информация о платежах.\nОбращение будет завершено без отправки информации в канал выплат."
            await interaction.followup.send(payment_info, ephemeral=True)
        else:
            payment_info = f"💰 Найдено платежей: {len(payments_found)}\n" + "\n".join(payment_breakdown)
            # Создаем эмбед для уведомления об оплате только если есть платежи
            payment_embed = discord.Embed(
                title="💰 Новый счет на выплату",
                description="В связи с выполненной работой!",
                color=discord.Color.blue()
            )
            
            # Создаем ссылку на канал
            channel_link = f"https://discord.com/channels/{interaction.guild.id}/{interaction.channel.id}"
            
            payment_embed.add_field(name="Обращение", value=claim_link, inline=False)
            payment_embed.add_field(
                name="Канал обращения", 
                value=f"[#{interaction.channel.name}]({channel_link})", 
                inline=False
            )
            payment_embed.add_field(
                name="Адвокат, выполнявший работу", 
                value=f"{interaction.user.mention} {lawyer_data[1]}", 
                inline=False
            )
            payment_embed.add_field(
                name="Найденные платежи по делу", 
                value="\n".join(f"💰 {amount:,} $ ({payment_date})" for amount, payment_date in zip(payments_found, payment_details)),
                inline=False
            )
            payment_embed.add_field(
                name="Расчет выплаты", 
                value="\n".join(payment_breakdown),
                inline=False
            )
            payment_embed.add_field(
                name="Итоговая сумма к выплате", 
                value=f"**{total_lawyer_amount:,} $**",
                inline=False
            )
            payment_embed.set_footer(text=f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

        # Рассчитываем суммы платежей
        payment_breakdown = []
        total_lawyer_amount = 0
        
        print("\nРасчет платежей:")
        for amount in payments_found:
            lawyer_amount = calculate_lawyer_amount(amount)
            total_lawyer_amount += lawyer_amount
            payment_breakdown.append(f"С {amount:,} руб. → {lawyer_amount:,} руб.")
            print(f"Расчет для платежа {amount}: {lawyer_amount}")
        
        print(f"Общая сумма к выплате адвокату: {total_lawyer_amount}")

        # Рассчитываем сумму для каждого платежа
        lawyer_amount = 0
        payment_breakdown = []
        
        print("\nРасчет платежей:")
        for amount in payments_found:
            current_amount = calculate_lawyer_amount(amount)
            lawyer_amount += current_amount
            payment_breakdown.append(f"С {amount:,} $ → {current_amount:,} $")
            print(f"Расчет для платежа {amount}: {current_amount}")

        print(f"Общая сумма к выплате адвокату: {lawyer_amount}")

        # Создаем эмбед для уведомления об оплате
        payment_embed = discord.Embed(
            title="💰 Новый счет на выплату",
            description="В связи с выполненной работой!",
            color=discord.Color.blue()
        )
        
        # Создаем ссылку на канал
        channel_link = f"https://discord.com/channels/{interaction.guild.id}/{interaction.channel.id}"
        
        payment_embed.add_field(name="Обращение", value=claim_link, inline=False)
        payment_embed.add_field(
            name="Канал обращения", 
            value=f"[#{interaction.channel.name}]({channel_link})", 
            inline=False
        )
        payment_embed.add_field(
            name="Адвокат, выполнявший работу", 
            value=f"{interaction.user.mention} {lawyer_data[1]}", 
            inline=False
        )
        payment_embed.add_field(name="Номер паспорта адвоката", value=lawyer_data[0], inline=False)
        payment_embed.add_field(
            name="Найденные платежи по делу", 
            value="\n".join(payment_details),
            inline=False
        )
        payment_embed.add_field(
            name="Расчет выплаты", 
            value="\n".join(payment_breakdown),
            inline=False
        )
        payment_embed.add_field(
            name="Итоговая сумма к выплате", 
            value=f"**{lawyer_amount:,} $**",
            inline=False
        )
        payment_embed.set_footer(text=f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

        # Отправляем сообщение в канал выплат только если есть платежи
        if payments_found:
            payment_channel = interaction.guild.get_channel(1379612318903697448)
            print(f"Поиск канала выплат: ID={1379612318903697448}")
            if not payment_channel:
                print(f"Не удалось найти канал выплат")
                # Выведем список всех доступных каналов для отладки
                print("Доступные каналы:")
                for channel in interaction.guild.channels:
                    print(f"- {channel.name} (ID: {channel.id})")
                await interaction.followup.send("⚠️ Предупреждение: не найден канал для выплат", ephemeral=True)
            else:
                print(f"Найден канал выплат: {payment_channel.name} (ID: {payment_channel.id})")
                print(f"Отправляем информацию о выплате в канал {payment_channel.name}")
                await payment_channel.send(
                    content=f"<@1068037217898995752>",
                    embed=payment_embed,
                    view=PaymentConfirmButton()
                )

        try:
            # Отправляем сообщение о начале архивации
            await interaction.followup.send("⏳ Завершаю работу и архивирую канал...", ephemeral=True)

            # Сохраняем фото
            photo_path = f"temp_photo_{interaction.channel.id}.png"
            await photo.save(photo_path)
            print(f"Фото сохранено: {photo_path}")

            # Архивируем канал
            archive_category = discord.utils.get(interaction.guild.categories, id=1379559023124156602)  # ID категории архива
            if archive_category:
                print(f"Перемещаем канал в архив")
                await interaction.channel.edit(category=archive_category)
                
                # Закрываем доступ к каналу
                await interaction.channel.set_permissions(interaction.guild.default_role, read_messages=False)
                for role_id in [LAWYER_ROLE_ID, *MOD_ROLE_IDS]:
                    role = interaction.guild.get_role(role_id)
                    if role:
                        await interaction.channel.set_permissions(role, read_messages=True)

                print(f"Канал успешно архивирован")
                # Отправляем сообщение в сам канал
                await interaction.channel.send("✅ Работа завершена, канал перемещен в архив")
            else:
                print(f"Не найдена категория архива")
                await interaction.channel.send("⚠️ Не удалось переместить канал в архив")

        except Exception as e:
            print(f"Ошибка при архивации канала: {e}")
            await interaction.channel.send(f"❌ Произошла ошибка при архивации канала: {str(e)}")

    except Exception as e:
        print(f"Ошибка в команде 'закончить_работу': {e}")
        await interaction.followup.send("Произошла ошибка при обработке команды", ephemeral=True)
        return


    # Получаем client_id из tickets; при отсутствии — пробуем help_data.client_tag и имя канала
    channel_id = str(interaction.channel.id)
    print(f"Checking data for channel: {channel_id}")
    
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    
    # Проверяем tickets
    cursor.execute('SELECT client_id FROM tickets WHERE channel_id = ?', (channel_id,))
    tickets_result = cursor.fetchone()
    print(f"Found in tickets: {tickets_result}")
    
    # Проверяем help_data и обновляем/добавляем запись в tickets если нужно
    cursor.execute('SELECT client_tag, client_name FROM help_data WHERE channel_id = ?', (channel_id,))
    help_data_result = cursor.fetchone()
    print(f"Found in help_data: {help_data_result}")
    
    if help_data_result and help_data_result[0]:
        client_tag = help_data_result[0]
        # Извлекаем ID из тега
        if client_tag.startswith('<@') and client_tag.endswith('>'):
            extracted_client_id = client_tag.strip('<@>')
            
            if not tickets_result or tickets_result[0] == 'None':
                # Если записи нет или client_id='None', создаем/обновляем запись
                cursor.execute('''
                    INSERT OR REPLACE INTO tickets (channel_id, lawyer_id, client_id, nickname)
                    VALUES (?, ?, ?, ?)
                ''', (channel_id, str(interaction.user.id), extracted_client_id, help_data_result[1]))
                conn.commit()
                tickets_result = (extracted_client_id,)  # Обновляем результат
                print(f"Updated tickets record with client_id: {extracted_client_id}")
    
    conn.close()

    client_id = tickets_result[0] if tickets_result else None

    # Фоллбек 1: help_data.client_tag -> извлечь id
    if not client_id:
        try:
            conn = sqlite3.connect('lawyers.db')
            c = conn.cursor()
            c.execute('SELECT client_tag FROM help_data WHERE channel_id = ?', (str(interaction.channel.id),))
            row = c.fetchone()
            
            if row and row[0]:
                client_tag = str(row[0])
                # Если тег в формате <@123456789>, извлекаем только цифры
                if client_tag.startswith('<@') and client_tag.endswith('>'):
                    client_id = client_tag.strip('<@>')
                else:
                    # Для других форматов просто извлекаем все цифры
                    import re as _re
                    _id = _re.sub(r'\D', '', client_tag)
                    if _id:
                        client_id = _id
                
                # Отладочное сообщение
                print(f"Found client_tag: {client_tag}, extracted client_id: {client_id}")
        finally:
            try: conn.close()
            except Exception: pass

    # Фоллбек 2: по имени канала
    if not client_id:
        channel_name = interaction.channel.name
        if '-' in channel_name:
            username = channel_name.split('-', 1)[1]
            member = discord.utils.get(interaction.guild.members, name=username)
            if member:
                client_id = str(member.id)

    if not client_id or str(client_id).lower() == "none":
        # Пробуем получить имя клиента для более информативного сообщения
        try:
            conn = sqlite3.connect('lawyers.db')
            cursor = conn.cursor()
            cursor.execute('SELECT client_name FROM help_data WHERE channel_id = ?', (str(interaction.channel.id),))
            client_name_result = cursor.fetchone()
            client_info = f" (клиент: {client_name_result[0]})" if client_name_result else ""
            conn.close()
        except:
            client_info = ""

        await interaction.followup.send(
            f'❌ Ошибка: не удалось определить ID клиента для уведомления{client_info}.\n'
            f'Убедитесь, что:\n'
            f'1. Клиент добавлен в базу данных через /помощь\n'
            f'2. В help_data указан корректный client_tag в формате <@ID>\n'
            f'3. Канал привязан к клиенту в tickets',
            ephemeral=True
        )
        return

    # Отправляем сообщение о приговоре
    await interaction.channel.send(
        f"<@{client_id}>, по вашему обращению был вынесен приговор!\nСсылка на иск: {claim_link}",
        file=await photo.to_file()
    )

    # Включаем кнопку "Начать работу" в верхнем сообщении
    async for msg in interaction.channel.history(limit=100):
        if msg.components:
            for comp in msg.components:
                for child in comp.children:
                    if isinstance(child, discord.ui.Button) and child.custom_id == 'persistent_start_work':
                        child.disabled = False
                        try:
                            await msg.edit(view=msg.components[0])
                        except Exception:
                            pass
                        break
            break

    # Обновляем информацию об архивации в help_data
    try:
        conn = sqlite3.connect('lawyers.db')
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE help_data 
            SET lawyer_tag = ? 
            WHERE channel_id = ?
        ''', (f"✅ Архивировано {interaction.user.mention}", str(interaction.channel.id)))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Ошибка при обновлении lawyer_tag: {e}")

    # Обновляем реестр после завершения
    await update_client_registry(bot)

    target_message = None
    client_name = None

    # 1. Ищем нужное сообщение
    async for msg in interaction.channel.history(limit=100):
        if msg.components:
            for component in msg.components:
                for child in component.children:
                    if isinstance(child, discord.ui.Button) and child.custom_id == "persistent_start_work":
                        target_message = msg # Нашли сообщение, которое нужно отредактировать

                        # Получаем client_name для создания View
                        conn = sqlite3.connect('lawyers.db')
                        cursor = conn.cursor()
                        cursor.execute('SELECT nickname FROM tickets WHERE channel_id = ?', (str(interaction.channel.id),))
                        result = cursor.fetchone()
                        conn.close()

                        if result:
                            client_name = result[0]
                        break # Вышли из цикла по детям
                if target_message:
                    break # Вышли из цикла по компонентам
        if target_message:
            break # Вышли из основного цикла

    # 2. Если нашли сообщение и client_name, создаем View и редактируем
    if target_message and client_name:
        view = TicketButtons(client_name=client_name)

        # Находим и активируем кнопку "Начать работу"
        for child in view.children:
            if isinstance(child, discord.ui.Button) and child.custom_id == "persistent_start_work":
                child.disabled = False
                break

        await target_message.edit(view=view)

    # Используем глобальный класс CloseTicketModal

    class TicketButtons(discord.ui.View):
            def __init__(self, client_name: str):
                super().__init__(timeout=None)
                self.client_name = client_name

            @discord.ui.button(
                style=discord.ButtonStyle.red,
                label="Закрыть с причиной",
                custom_id="close_ticket"
            )
            async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
                # Проверяем роль управляющего
                if 1379547784717402152 not in [role.id for role in interaction.user.roles]:
                    await interaction.response.send_message(
                        "Только управляющие могут закрывать обращения!",
                        ephemeral=True
                    )
                    return

                # Открываем модальное окно
                await interaction.response.send_modal(CloseTicketModal())

            @discord.ui.button(
                style=discord.ButtonStyle.green,
                label="Начать работу",
                custom_id="start_work",
            )
            async def start_work(self, interaction: discord.Interaction, button: discord.ui.Button):
                # Проверяем роль адвоката
                if LAWYER_ROLE_ID not in [role.id for role in interaction.user.roles]:
                    await interaction.response.send_message(
                        "Только адвокаты могут начинать работу!",
                        ephemeral=True
                    )
                    return

                # Откладываем ответ
                await interaction.response.defer(ephemeral=True)

                # Обновляем статистику адвоката
                conn = sqlite3.connect('lawyers.db')
                cursor = conn.cursor()

                # Увеличиваем счетчик обращений
                cursor.execute('''
                INSERT OR IGNORE INTO lawyer_stats (lawyer_id, cases_taken)
                VALUES (?, 0)
''', (str(interaction.user.id),))

                cursor.execute('''
                UPDATE lawyer_stats
                SET cases_taken = cases_taken + 1
                WHERE lawyer_id = ?
''', (str(interaction.user.id),))

                conn.commit()
                conn.close()

                # Извлекаем ID адвоката из упоминания
                lawyer_mention = result[0] # result получен ранее
                try:
                    lawyer_id_from_db = int(lawyer_mention.strip('<@>'))
                except:
                    await interaction.followup.send("Ошибка: неверный формат ID адвоката в БД", ephemeral=True)
                    return

                # Получаем категории
                archive_category = interaction.guild.get_channel(1394351354855690402)
                active_category = interaction.guild.get_channel(1379559023124156602)

                # Если канал в архиве - перемещаем в активные
                if interaction.channel.category_id == archive_category.id:
                    await interaction.channel.edit(category=active_category)
                    await interaction.followup.send("Канал возвращён в активные!", ephemeral=True)

                # Получаем всех адвокатов на сервере
                lawyer_role = interaction.guild.get_role(1379548122111545354)
                all_lawyers = lawyer_role.members if lawyer_role else []

                # Убираем права просмотра у всех адвокатов
                for lawyer in all_lawyers:
                    if lawyer.id != interaction.user.id:
                        await interaction.channel.set_permissions(
                            lawyer,
                            view_channel=False,
                            read_messages=False
                        )
                        await asyncio.sleep(1.2)

                # Создаем embed-ответ
                embed = discord.Embed(
                    title="Принятое обращение",
                    description=f"Ваше обращение будет обработано {interaction.user.mention}",
                    color=0x00FF00
                )

                # Отправляем новое сообщение
                await interaction.followup.send(embed=embed)

                # Находим кнопку "Начать работу" и отключаем ее
                for child in self.children:
                    if isinstance(child, discord.ui.Button) and child.custom_id == "persistent_start_work":
                        child.disabled = True
                        break

                await interaction.message.edit(view=self)

                channel_name = interaction.channel.name
                username = channel_name.split('-', 1)[1]
                # Ищем пользователя на сервере
                user = discord.utils.get(interaction.guild.members, name=username)
                tag_client = user.mention

                # Сохраняем в БД
                conn = sqlite3.connect('lawyers.db')
                cursor = conn.cursor()
                cursor.execute('''
                INSERT OR REPLACE INTO tickets
                (channel_id, lawyer_id, client_id, nickname)
                VALUES (?, ?, ?, ?)
''', (interaction.channel.id, str(interaction.user.id), str(user.id) if user else None, self.client_name))
                conn.commit()
                conn.close()

    # Восстанавливаем кнопку "Начать работу"
    async for message in interaction.channel.history(limit=100):
        if message.components and any(
            child.custom_id == "persistent_start_work"
            for comp in message.components
            for child in comp.children
            if isinstance(child, discord.ui.Button)
        ):
            # Получаем оригинальное View из сообщения
            conn = sqlite3.connect('lawyers.db')
            cursor = conn.cursor()
            cursor.execute('''
            SELECT nickname FROM tickets
            WHERE channel_id = ?
''', (str(interaction.channel.id),))

            result = cursor.fetchone()
            conn.close()

            view = TicketButtons(client_name=str(result[0])) # <--- Здесь создается TicketButtons

            # Ищем нужную кнопку
            for child in view.children:
                if isinstance(child, discord.ui.Button) and child.custom_id == "persistent_start_work":
                    # Включаем кнопку обратно
                    child.disabled = False
            await message.edit(view=view)
            break

    # Даем доступ всем адвокатам
    lawyer_role = interaction.guild.get_role(1379548122111545354)
    if lawyer_role:
            for member in lawyer_role.members:
                await interaction.channel.set_permissions(
                    member,
                    read_messages=True,
                    send_messages=True
                )
                await asyncio.sleep(1.2)

    # Удаляем запись из БД
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM tickets WHERE channel_id = ?', (str(interaction.channel.id),))
    conn.commit()
    conn.close()

    # Перемещаем канал в архив
    archive_category = bot.get_channel(1394351354855690402)
    if archive_category:
        await interaction.channel.edit(category=archive_category)

    await interaction.followup.send(
        "Работа завершена, канал перемещен в архив!",
        ephemeral=True
    )


@bot.tree.command(name="статистика_личная", description="Показать личную статистику адвоката")
async def personal_stats(interaction: discord.Interaction):
    # Проверка роли адвоката
    if not any(role.id == LAWYER_ROLE_ID for role in interaction.user.roles):
        await interaction.response.send_message("Только адвокаты могут просматривать статистику.", ephemeral=True)
        return

    # Получаем статистику из БД
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    cursor.execute('''
    SELECT cases_taken, total_earned
    FROM lawyer_stats
    WHERE lawyer_id = ?
''', (str(interaction.user.id),))
    result = cursor.fetchone()
    conn.close()

    if not result:
        cases = 0
        earned = 0
    else:
        cases, earned = result

    # Создаем embed с статистикой
    embed = discord.Embed(
        title=f"📊 Статистика адвоката {interaction.user.display_name}",
        color=0x00ff00
    )
    embed.add_field(name="Количество обращений", value=f"`{cases}`", inline=True)
    embed.add_field(name="Общий заработок", value=f"`${earned}`", inline=True)
    embed.set_thumbnail(url=interaction.user.display_avatar.url)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="edit_client", description="Редактировать данные клиента в базе данных")
@app_commands.describe(
    identifier="Номер соглашения или тег клиента",
    field="Поле для редактирования (name/tag/passport)",
    new_value="Новое значение"
)
async def edit_client(
    interaction: discord.Interaction,
    identifier: str,
    field: str,
    new_value: str
):
    # Проверяем, есть ли у пользователя права модератора
    if not any(role.id in MOD_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("У вас нет прав для использования этой команды.", ephemeral=True)
        return

    # Проверяем корректность поля для редактирования
    valid_fields = {
        "name": "client_name",
        "tag": "client_tag",
        "passport": "client_passport"
    }
    
    if field.lower() not in valid_fields:
        await interaction.response.send_message(
            f"Некорректное поле для редактирования. Доступные поля: {', '.join(valid_fields.keys())}", 
            ephemeral=True
        )
        return

    # Если редактируется тег, проверяем формат
    if field.lower() == "tag" and not new_value.startswith("<@") and new_value != "Нет тега":
        # Пытаемся найти пользователя
        try:
            user_id = ''.join(filter(str.isdigit, new_value))
            member = await interaction.guild.fetch_member(int(user_id))
            new_value = member.mention
        except (ValueError, discord.NotFound):
            # Если не удалось найти пользователя, пытаемся найти по имени
            member = discord.utils.find(
                lambda m: m.name.lower() == new_value.lower() or m.display_name.lower() == new_value.lower(),
                interaction.guild.members
            )
            if member:
                new_value = member.mention
            else:
                await interaction.response.send_message(
                    "Не удалось найти пользователя с таким именем или ID. Убедитесь, что указали правильный тег или ID пользователя.",
                    ephemeral=True
                )
                return

    try:
        # Подключаемся к базе данных
        conn = sqlite3.connect('lawyers.db')
        cursor = conn.cursor()

        # Проверяем, что передано - тег или номер соглашения
        is_tag = '<@' in identifier
        
        # Формируем запрос в зависимости от типа идентификатора
        if is_tag:
            search_query = "SELECT * FROM help_data WHERE client_tag = ?"
            search_value = identifier
            display_type = "тегом"
        else:
            search_query = "SELECT * FROM help_data WHERE agreement_number = ?"
            search_value = identifier
            display_type = "номером соглашения"

            # Проверяем существование записи
            cursor.execute(search_query, (search_value,))
            client_data = cursor.fetchone()
            
            if not client_data:
                await interaction.response.send_message(
                    f"Клиент с {display_type} {identifier} не найден.",
                    ephemeral=True
                )
                return

            # Получаем номер соглашения для дальнейшего обновления
            if is_tag:
                agreement_number = client_data[1]  # Индекс столбца agreement_number
            else:
                agreement_number = identifier

            # Обновляем данные
            update_query = f"UPDATE help_data SET {valid_fields[field.lower()]} = ? WHERE agreement_number = ?"
            cursor.execute(update_query, (new_value, agreement_number))
            conn.commit()

            # Получаем обновленные данные для подтверждения
            cursor.execute(
                "SELECT client_name, client_tag, client_passport, agreement_number FROM help_data WHERE agreement_number = ?",
                (agreement_number,)
            )
        updated_data = cursor.fetchone()
        conn.close()

        # Создаем эмбед с обновленной информацией
        embed = discord.Embed(
            title="✅ Данные клиента обновлены",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Номер соглашения", value=updated_data[3], inline=False)
        embed.add_field(name="Имя клиента", value=updated_data[0], inline=True)
        embed.add_field(name="Тег Discord", value=updated_data[1], inline=True)
        embed.add_field(name="Паспорт", value=updated_data[2], inline=True)
        embed.add_field(name="Изменено поле", value=field.lower(), inline=True)
        embed.add_field(name="Новое значение", value=new_value, inline=True)
        embed.set_footer(text=f"Изменено модератором {interaction.user.name}")

        # Отправляем подтверждение
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Если изменился тег, обновляем все каналы где упоминается этот клиент
        if field.lower() == "tag":
            try:
                # Находим все каналы в категории тикетов
                ticket_category = interaction.guild.get_channel(TICKET_CATEGORY_ID)
                if ticket_category:
                    for channel in ticket_category.text_channels:
                        async for message in channel.history(limit=100):
                            if message.embeds:
                                for embed in message.embeds:
                                    # Проверяем, содержит ли эмбед информацию о клиенте
                                    if embed.fields and any(
                                        field.name == "Клиент" and updated_data[0] in field.value 
                                        for field in embed.fields
                                    ):
                                        try:
                                            await message.edit(content=f"Обновленный тег клиента: {new_value}")
                                        except discord.Forbidden:
                                            continue
            except Exception as e:
                print(f"Ошибка при обновлении сообщений: {e}")

        # Обновляем реестр клиентов
        await update_client_registry(bot)

    except sqlite3.Error as e:
        await interaction.response.send_message(
            f"Произошла ошибка при обновлении базы данных: {str(e)}",
            ephemeral=True
        )
        if 'conn' in locals():
            conn.close()
        return
    except Exception as e:
        await interaction.response.send_message(
            f"Произошла неизвестная ошибка: {str(e)}",
            ephemeral=True
        )
        if 'conn' in locals():
            conn.close()
        return
    finally:
        # Закрываем соединение с базой данных
        if 'conn' in locals():
            conn.close()

@bot.tree.command(name="статистика", description="Показать статистику адвоката")
@app_commands.describe(адвокат="Укажите адвоката для просмотра статистики")
async def lawyer_stats(interaction: discord.Interaction, адвокат: discord.Member):


    # Проверка, что указанный пользователь - адвокат
    LAWYER_ROLE_ID = 1379548122111545354
    if LAWYER_ROLE_ID not in [role.id for role in адвокат.roles]:
        await interaction.response.send_message(
            "Указанный пользователь не является адвокатом!",
            ephemeral=True
        )
        return

    # Получаем статистику из БД
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    cursor.execute('''
    SELECT cases_taken, total_earned
    FROM lawyer_stats
    WHERE lawyer_id = ?
''', (str(адвокат.id),))
    result = cursor.fetchone()
    conn.close()

    if not result:
        cases = 0
        earned = 0
    else:
        cases, earned = result

    # Создаем embed с статистикой
    embed = discord.Embed(
        title=f"📊 Статистика адвоката {адвокат.display_name}",
        color=0x3498db,
        timestamp=datetime.now()
    )
    embed.add_field(name="Количество обращений", value=f"`{cases}`", inline=True)
    embed.add_field(name="Общий заработок", value=f"`${earned}`", inline=True)
    embed.set_thumbnail(url=адвокат.display_avatar.url)
    embed.set_footer(text=f"Запрошено: {interaction.user.display_name}",
                   icon_url=interaction.user.display_avatar.url)

    await interaction.response.send_message(embed=embed)

# Каналы, где работает команда /news
ALLOWED_CHANNELS = {1394571871415963680, 1379612096509247599}

# URL фотографии
LOGO_URL = "https://avatars.mds.yandex.net/get-altay/5483320/2a0000017df67123a9d24a271874673280b0/M_height"

# Проверка, есть ли у пользователя нужные роли
ALLOWED_ROLES = {1379547784717402152, 1379547989680324750, 1379548354018541619}

def has_allowed_roles(user: discord.User) -> bool:
    """Проверяет, есть ли у пользователя одна из разрешенных ролей."""
    if not hasattr(user, 'roles'):  # Для приватных сообщений или если роли не загружены
        return False
    for role in user.roles:
        if role.id in ALLOWED_ROLES:
            return True
    return False


@bot.tree.command(name="news", description="Отправить новость с пингом @everyone")
async def news(interaction: discord.Interaction):
    # Проверка канала и ролей
    if interaction.channel_id not in ALLOWED_CHANNELS:
        await interaction.response.send_message("Эта команда недоступна в этом канале.", ephemeral=True)
        return
    if not has_allowed_roles(interaction.user):
        await interaction.response.send_message("У вас нет прав на использование этой команды.", ephemeral=True)
        return

    # Сохраняем состояние команды
    command_state[interaction.user.id] = "waiting_for_news_text"

    # Отправляем сообщение с инструкцией
    await interaction.response.send_message(
        "Готов написать новость! Пожалуйста, отправьте текст с форматированием.",
        ephemeral=True
    )


@bot.tree.command(name="сделать_эмбед", description="Создать эмбед с текстом")
async def make_embed_command(interaction: discord.Interaction): # Переименована команда
    # Проверка ролей
    if not has_allowed_roles(interaction.user):
        await interaction.response.send_message("У вас нет прав на использование этой команды.", ephemeral=True)
        return

    # Сохраняем состояние команды
    command_state[interaction.user.id] = "waiting_for_embed_text"

    # Отправляем сообщение с инструкцией
    await interaction.response.send_message(
        "Готов создать эмбед! Пожалуйста, отправьте текст с форматированием.",
        ephemeral=True
    )


@bot.tree.command(name="редактировать_эмбед", description="Редактировать эмбед через ответ на сообщение")
async def edit_embed(interaction: discord.Interaction):
    # Проверка ролей
    if not has_allowed_roles(interaction.user):
        await interaction.response.send_message("У вас нет прав на использование этой команды.", ephemeral=True)
        return

    # Сохраняем состояние команды
    command_state[interaction.user.id] = "waiting_for_edit_text"

    # Отправляем сообщение с инструкцией
    await interaction.response.send_message(
        "Готов редактировать эмбед! Пожалуйста, отправьте текст с форматированием, ответив на сообщение с эмбедом.",
        ephemeral=True
    )
CALL_CHANNEL_ID = 1399464539959070841
CLIENT_ROLE_ID = 1379558888063369346
LAWYER_ROLE_ID = 1379548122111545354
VIP_ROLE_ID = 1398263213006651505
def get_client_info(discord_id):
    """Получает данные клиента из таблицы help_data по Discord ID"""
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT client_name, client_passport FROM help_data WHERE client_tag = ?", 
                      (f"<@{discord_id}>",))
        result = cursor.fetchone()
        return result
    except sqlite3.Error as e:
        print(f"Ошибка при получении данных клиента: {e}")
        return None
    finally:
        conn.close()
# Класс для модального окна вызова адвоката
class CallLawyerModal(ui.Modal, title="Вызов частного адвоката"):
    location = ui.TextInput(
        label="Куда Вас привезли?",
        placeholder="Например: КПЗ LSPD",
        required=True,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Проверяем, что это клиент
        if not any(role.id == CLIENT_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message("Только клиенты могут вызывать адвокатов!", ephemeral=True)
            return

        # Получаем данные клиента из таблицы help_data
        client_info = get_client_info(str(interaction.user.id))
        if client_info:
            client_name = client_info[0]  # client_name
            client_passport = client_info[1]  # client_passport (если нужно)
        else:
            # Если записи нет, используем display_name
            client_name = interaction.user.display_name
            client_passport = "Не указан"
        client_tag = str(interaction.user).split("@")[-1]  # Если тег есть
        if not client_tag:  # Если тега нет (новый Discord без тегов)
            client_tag = interaction.user.name  # Используем имя пользователя    

        # Определяем цену
        is_vip = any(role.id == VIP_ROLE_ID for role in interaction.user.roles)
        price = "10.500$ (VIP-цена)" if is_vip else "15.000$"

        # Создаем эмбед с заявкой
        embed = discord.Embed(
            title="Вызов частного адвоката",
            color=discord.Color.red(),
            timestamp=datetime.now()
        )
        embed.add_field(name="Тег клиента", value=interaction.user.mention, inline=False)
        embed.add_field(name="Имя и Фамилия", value=client_name, inline=False)
        embed.add_field(name="Цена услуги", value=price, inline=False)
        embed.add_field(name="Место, куда везут", value=self.location.value, inline=False)
        embed.add_field(name="Статус услуги", value="Ожидает принятия адвокатом", inline=False)

        # Создаем view с кнопками
        view = CallLawyerView(interaction.user, client_name, price, self.location.value)

        # Отправляем сообщение с пингом адвокатов
        channel = interaction.guild.get_channel(CALL_CHANNEL_ID)
        await channel.send(f"<@&1379548122111545354> новый вызов!", embed=embed, view=view)
        
        await interaction.response.send_message("Ваш вызов отправлен!", ephemeral=True)

# Класс для кнопок вызова адвоката
class CallLawyerView(ui.View):
    def __init__(self, client, client_name, price, location):
        super().__init__(timeout=None)
        self.client = client
        self.client_name = client_name
        self.price = price
        self.location = location
        self.lawyer = None
        self.thread = None  # Будем хранить ссылку на ветку

    @ui.button(label="Принять вызов", style=discord.ButtonStyle.green, custom_id="accept_call", emoji="<a:blackverification:1401927462014812181>")
    async def accept_call(self, interaction: discord.Interaction, button: ui.Button):
        print("🟢 Кнопка 'Принять вызов' нажата!")
        
        # Проверка роли адвоката
        if not any(role.id == LAWYER_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message("🚫 Только адвокаты могут принимать вызовы!", ephemeral=True)
            return

        # Получаем данные адвоката
        lawyer = get_lawyer(str(interaction.user.id))
        if not lawyer:
            await interaction.response.send_message("🚫 Вы не зарегистрированы как адвокат!", ephemeral=True)
            return

        # 1. Сначала обновляем оригинальное сообщение
        embed = interaction.message.embeds[0].copy()  # Создаем копию embed
        embed.set_field_at(
            4,  # Индекс поля "Статус услуги"
            name="📌 Статус услуги",
            value=f"**Вызов принят адвокатом** {interaction.user.mention}",
            inline=False
        )
        embed.color = discord.Color.green()
        
        # Удаляем кнопку "Принять вызов"
        self.remove_item(button)
        
        try:
            # 2. Обновляем сообщение (ДО создания ветки)
            await interaction.response.edit_message(embed=embed, view=self)
            
            # 3. Получаем тег клиента (с обработкой спецсимволов)
            client_tag = str(self.client).split('#')[-1] if '#' in str(self.client) else self.client.name
            client_tag = re.sub(r'[^a-zA-Z0-9_]', '_', client_tag)  # Санитайзинг
            channel_name = f"обращение-{client_tag}"
            print(f"🔍 Ищем канал: {channel_name}")

            # 4. Ищем канал клиента (с учетом регистра)
            client_channel = next(
                (ch for ch in interaction.guild.text_channels 
                 if ch.name.lower() == channel_name.lower()),
                None
            )
            
            if client_channel:
                print(f"✅ Найден канал: {client_channel.name}")
            else:
                print(f"❌ Канал {channel_name} не найден!")

            # 5. Создаем ветку для обсуждения
            self.thread = await interaction.channel.create_thread(
                name=f"⚖️Вызов-{self.client_name[:90]}",  # Обрезаем слишком длинные имена
                auto_archive_duration=1440,
                reason=f"Вызов адвоката от {self.client_name}"
            )

            # 6. Добавляем участников с обработкой ошибок
            members_to_add = {self.client, interaction.user}
            for member in members_to_add:
                try:
                    await self.thread.add_user(member)
                except discord.Forbidden:
                    print(f"⚠️ Нет прав добавить {member} в ветку")
                except discord.HTTPException as e:
                    print(f"⚠️ Ошибка добавления {member}: {e}")

            # 7. Отправляем информацию в ветку
            welcome_msg = [
                f"## ⚖️ Адвокат {interaction.user.mention} принял ваш вызов!",
                f"**📞 Телефон адвоката:** {lawyer[2]}",
                f"**<:dock:1401925338182717471> Имя адвоката:** {lawyer[1]}",
                "\n**Для выбора результата используйте кнопки ниже ↓**"
            ]
            
            await self.thread.send('\n'.join(welcome_msg))

            # 8. Добавляем кнопки исхода
            outcome_view = OutcomeView(interaction.message)
            await self.thread.send("## :pencil: Выберите исход защиты:", view=outcome_view)
            
            # 9. Уведомляем клиента (если канал найден)
            if client_channel:
                try:
                    await client_channel.send(
                        f"{self.client.mention}, ваш вызов принят {interaction.user.mention}!\n"
                        f"➡ Обсуждение: {self.thread.mention}"
                    )
                except discord.Forbidden:
                    print(f"⚠️ Нет прав писать в канал {client_channel.name}")

            # 10. Логируем успешное создание
            print(f"✅ Вызов успешно обработан. Ветка: {self.thread.name}")

        except Exception as e:
            print(f"🔥 Критическая ошибка: {type(e).__name__}: {e}")
            traceback.print_exc()  # Печатаем полный traceback
            
            try:
                await interaction.followup.send(
                    "⚠️ Произошла ошибка при обработке вызова!",
                    ephemeral=True
                )
            except:
                pass
                
            # Пытаемся удалить ветку, если она была создана
            if hasattr(self, 'thread') and self.thread:
                try:
                    await self.thread.delete()
                except:
                    pass

    @ui.button(label="Отменить вызов", style=discord.ButtonStyle.red, custom_id="cancel_call", emoji="<:crest:1401920036083335218>")
    async def cancel_call(self, interaction: discord.Interaction, button: ui.Button):
        # Проверяем права
        if interaction.user != self.client and not any(role.id == LAWYER_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message("🚫 Вы не можете отменить этот вызов!", ephemeral=True)
            return

        # Удаляем все кнопки
        self.clear_items()
        await interaction.response.edit_message(view=self)

        # Обновляем эмбед
        embed = interaction.message.embeds[0]
        who = "клиентом" if interaction.user == self.client else "адвокатом"
        embed.set_field_at(4, name="📌 Статус услуги", 
                         value=f"**Вызов отменен {who}** {interaction.user.mention}", 
                         inline=False)
        embed.color = discord.Color.dark_grey()
        
        try:
            # Если была создана ветка (вызов был принят), удаляем ее
            if self.thread:
                await self.thread.delete(reason=f"Вызов отменен {who}")
            
            await interaction.message.edit(embed=embed, view=self)
            
        except Exception as e:
            await interaction.followup.send(f"🚫 Произошла ошибка: {str(e)}", ephemeral=True)
def sanitize_username(username):
    # Заменяем пробелы и спецсимволы на "_"
    return re.sub(r'[^a-zA-Z0-9_]', '_', username)
# Класс для выбора исхода защиты
class OutcomeView(ui.View):
    def __init__(self, original_message):
        super().__init__(timeout=None)
        self.original_message = original_message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not any(role.id == LAWYER_ROLE_ID for role in interaction.user.roles):
            await interaction.response.send_message("🚫 Только адвокаты могут выбирать исход защиты!", ephemeral=True)
            return False
        return True

    async def complete_outcome(self, interaction: discord.Interaction, outcome: str, emoji: str, color: discord.Color):
        # Удаляем все кнопки
        self.clear_items()
        
        # Обновляем оригинальное сообщение
        embed = self.original_message.embeds[0].copy()
        embed.set_field_at(
            4, 
            name="📌 Статус услуги", 
            value=f"**Вызов принят адвокатом** {interaction.user.mention}\n"
                 f"**Исход:** {emoji} {outcome}",
            inline=False
        )
        embed.color = color
        
        # Обновляем сообщение с результатом
        try:
            await self.original_message.edit(embed=embed, view=None)
            await interaction.response.edit_message(view=self)
        except Exception as e:
            print(f"Ошибка при обновлении сообщения: {e}")
            await interaction.response.defer()

        # Отправляем результат клиенту (используем новый метод)
        await self.send_result_to_client(interaction, outcome, emoji, color)
        
        await interaction.followup.send(
            f"✅ **Исход защиты установлен:** {emoji} {outcome}",
            ephemeral=True
        )

    async def send_result_to_client(self, interaction: discord.Interaction, outcome: str, emoji: str, color: discord.Color):
        try:
            # 1. Получаем данные из оригинального сообщения
            original_embed = self.original_message.embeds[0]
            client_mention = original_embed.fields[0].value
            client_id = int(client_mention.strip("<@!>"))
            client = interaction.guild.get_member(client_id)
            
            if not client:
                print("❌ Клиент не найден на сервере!")
                return

            # 2. Получаем ТЕГ клиента (например, "boberkyrva2288")
            client_tag = str(client).split("#")[-1] if "#" in str(client) else client.name
            channel_name = f"обращение-{client_tag}"
            print(f"🔍 Ищем канал клиента: {channel_name}")

            # 3. Ищем канал клиента
            client_channel = None
            for channel in interaction.guild.text_channels:
                if channel.name.lower() == channel_name.lower():
                    client_channel = channel
                    print(f"✅ Найден канал клиента: {channel.name}")
                    break

            # 4. Создаем ПОЛНЫЙ embed (как в оригинале)
            result_embed = discord.Embed(
                title="⚖️ Итог вызова адвоката",
                color=color,
                timestamp=datetime.now()
            )
            
            # Копируем ВСЕ поля из оригинального сообщения
            for field in original_embed.fields:
                if field.name not in ["📌 Статус услуги"]:  # Пропускаем только поле статуса
                    result_embed.add_field(
                        name=field.name,
                        value=field.value,
                        inline=field.inline
                    )
            
            # Добавляем итоговый результат
            result_embed.add_field(
                name="🎯 Итоговый результат",
                value=f"{emoji} {outcome}",
                inline=False
            )
            
            result_embed.set_footer(text="Адвокатское бюро PACT Attorney")

            # 5. Отправляем в канал клиента (если найден)
            if client_channel:
                await client_channel.send(embed=result_embed)
                print(f"📨 Полный результат отправлен в канал: {client_channel.name}")
            else:
                # Если канал не найден, пробуем ЛС
                try:
                    await client.send(embed=result_embed)
                    print("📨 Полный результат отправлен в ЛС (канал не найден)")
                except:
                    print("❌ Не удалось отправить результат ни в канал, ни в ЛС!")
                    
        except Exception as e:
            print(f"🔥 Ошибка при отправке результата: {e}")

    # Кнопки остаются без изменений
    @ui.button(label="Успешная защита", style=discord.ButtonStyle.green, custom_id="defended", emoji="🛡️")
    async def defended(self, interaction: discord.Interaction, button: ui.Button):
        await self.complete_outcome(interaction, "Клиент успешно защищен", "🛡️", discord.Color.green())

    @ui.button(label="Воздержался", style=discord.ButtonStyle.blurple, custom_id="abstained", emoji="✋")
    async def abstained(self, interaction: discord.Interaction, button: ui.Button):
        await self.complete_outcome(interaction, "Воздержался от рекомендации", "✋", discord.Color.blue())

    @ui.button(label="Нарушение УАК", style=discord.ButtonStyle.red, custom_id="violation", emoji="⚠️")
    async def violation(self, interaction: discord.Interaction, button: ui.Button):
        await self.complete_outcome(interaction, "Госник нарушил УАК, готовится иск", "⚠️", discord.Color.orange())

# Команда для настройки канала вызовов
@bot.tree.command(name="setup_call_channel", description="Настраивает канал для вызовов адвокатов")
async def setup_call_channel(interaction: discord.Interaction):
    # Проверяем права
    if not any(role.id in MOD_ROLE_IDS for role in interaction.user.roles):
        await interaction.response.send_message("Только модераторы могут настраивать этот канал!", ephemeral=True)
        return

    # Отправляем первоначальный ответ
    await interaction.response.defer(ephemeral=True)

    try:
        # Очищаем канал
        channel = interaction.guild.get_channel(CALL_CHANNEL_ID)
        if not channel:
            await interaction.followup.send("❌ Канал звонков не найден", ephemeral=True)
            return
            
        await channel.purge()

        # Создаем эмбед с инструкциями
        embed = discord.Embed(
            title="Вызов частного адвоката",
            description=(
                "Если вам требуется срочная юридическая помощь, нажмите кнопку ниже.\n\n"
                "**Условия:**\n"
                "1. Адвокат выезжает только в случае задержания\n"
                "2. Оплата производится по факту оказания услуг\n"
                "3. VIP-клиенты получают скидку 30%\n"
                "4. В случае проигрыша - услуга бесплатна"
            ),
            color=discord.Color.red()
        )

        # Создаем кнопку вызова
        view = ui.View()
        view.add_item(ui.Button(
            label="Вызвать адвоката",
            style=discord.ButtonStyle.red,
            custom_id="call_lawyer",
            emoji="<a:sirena:1402595268691759175> "
        ))

        await channel.send(embed=embed, view=view)
        await interaction.followup.send("Канал для вызовов настроен!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Произошла ошибка: {str(e)}", ephemeral=True)

@bot.event
async def on_message(message: discord.Message):
    # Игнорируем сообщения от ботов
    if message.author.bot:
        return

    # Проверяем сообщения от управляющего для подтверждения платежей
    if isinstance(message.channel, discord.DMChannel) and message.author.id == 1068037217898995752:
        await process_payment_screenshot(message)
        return

    # Проверяем, ожидает ли бот текст от пользователя
    user_id = message.author.id
    if user_id in command_state:
        # Удаляем сообщение пользователя
        try:
            await message.delete()
        except discord.Forbidden:
            print(f"Не удалось удалить сообщение от {message.author.name} (ID: {message.author.id}) из-за отсутствия прав.")
        except discord.HTTPException as e:
            print(f"Ошибка при удалении сообщения от {message.author.name} (ID: {message.author.id}): {e}")

        # Получаем текст
        text = message.content

        # Создаем эмбед
        embed = discord.Embed(
            description=f"{text}",  # Основной текст
            color=discord.Color.gold()  # Жёлтый цвет эмбеда
        )
        # Добавляем фото и текст в верхнюю часть эмбеда
        embed.set_author(
            name="GTA 5 RP | PACT Attorney",  # Текст справа от фото
            icon_url=LOGO_URL  # Фото слева
        )
        # Добавляем футер
        embed.set_footer(text=f"{message.author.name}", icon_url=message.author.avatar.url if message.author.avatar else None)
        embed.timestamp = datetime.now()

        # Обрабатываем команду /news
        if command_state[user_id] == "waiting_for_news_text":
            # Отправка сообщения с пингом @everyone
            try:
                await message.channel.send(content="@everyone", embed=embed)
            except discord.Forbidden:
                await message.channel.send("Недостаточно прав для отправки сообщения с @everyone.", ephemeral=True)
            except discord.HTTPException as e:
                await message.channel.send(f"Произошла ошибка при отправке новости: {e}", ephemeral=True)


        # Обрабатываем команду /сделать_эмбед
        elif command_state[user_id] == "waiting_for_embed_text":
            # Отправка эмбеда
            try:
                await message.channel.send(embed=embed)
            except discord.HTTPException as e:
                await message.channel.send(f"Произошла ошибка при создании эмбеда: {e}", ephemeral=True)


        # Обрабатываем команду /редактировать_эмбед
        elif command_state[user_id] == "waiting_for_edit_text":
            # Проверяем, есть ли ответ на сообщение
            if not message.reference:
                await message.channel.send(
                    "Пожалуйста, ответьте на сообщение с эмбедом, чтобы отредактировать его.",
                    delete_after=5
                )
                # Продолжаем ожидать, не удаляем состояние
                return

            # Получаем сообщение, на которое ответили
            try:
                replied_message = await message.channel.fetch_message(message.reference.message_id)
            except discord.NotFound:
                await message.channel.send(
                    "Не удалось найти сообщение, на которое вы отвечаете. Убедитесь, что оно находится в этом канале.",
                    delete_after=5
                )
                del command_state[user_id]
                return
            except discord.HTTPException as e:
                await message.channel.send(f"Ошибка при получении сообщения: {e}", delete_after=5)
                del command_state[user_id]
                return


            # Проверяем, содержит ли сообщение эмбед
            if not replied_message.embeds:
                await message.channel.send(
                    "Сообщение, на которое вы ответили, не содержит эмбеда.",
                    delete_after=5
                )
                # Продолжаем ожидать, не удаляем состояние
                return

            # Редактируем эмбед
            try:
                # Создаем новый эмбед на основе старого, чтобы избежать проблем с сохранением состояния
                new_embed = discord.Embed(
                    title=replied_message.embeds[0].title or None,
                    description=text,  # Обновляем основной текст
                    color=replied_message.embeds[0].color or discord.Color.gold(),
                    url=replied_message.embeds[0].url or None,
                    timestamp=datetime.now()
                )

                # Копируем поля, если они есть, кроме тех, которые мы хотим обновить
                for field in replied_message.embeds[0].fields:
                    if field.name != "‏‏‎ ‎": # Если это поле, которое мы будем обновлять
                        new_embed.add_field(name=field.name, value=field.value, inline=field.inline)

                # Добавляем обновленное поле
                new_embed.add_field(name="‏‏‎ ‎", value="**GTA5RP | PACT Attorney**", inline=False)
                new_embed.set_thumbnail(url="https://yurgorod.ru/assets/image/0/09de87f54ce65511fc6ae28daf01f820.png")
                new_embed.set_footer(text=f"{message.author.name}", icon_url=message.author.avatar.url if message.author.avatar else None)

                await replied_message.edit(embed=new_embed)
                await message.channel.send("Эмбед успешно отредактирован!", delete_after=5)

            except discord.Forbidden:
                await message.channel.send("Недостаточно прав для редактирования сообщения.", delete_after=5)
            except discord.HTTPException as e:
                await message.channel.send(f"Произошла ошибка при редактировании эмбеда: {e}", delete_after=5)

        # Удаляем состояние, только если команда была успешно обработана
        if user_id in command_state:
            del command_state[user_id]
    if message.reference:
        try:
            # Получаем оригинальное сообщение
            channel = message.channel
            original_msg = await channel.fetch_message(message.reference.message_id)
            
            # Проверяем, что это наше сообщение с запросом подтверждения
            if original_msg.author.id == bot.user.id and original_msg.embeds and "Требуется подтверждение платежа" in original_msg.embeds[0].title:
                # Проверяем, есть ли вложения (скриншоты)
                if message.attachments:
                    # Получаем информацию из оригинального embed
                    embed = original_msg.embeds[0]
                    client_field = next(f for f in embed.fields if f.name == "Клиент")
                    channel_field = next(f for f in embed.fields if f.name == "Канал")
                    amount_field = next(f for f in embed.fields if f.name == "Сумма")
                    
                    client_name = client_field.value.split("(")[0].strip()
                    channel_id = int(channel_field.value.strip("<#>").strip())
                    amount = amount_field.value
                    
                    # Получаем ID адвоката из описания
                    lawyer_id = int(embed.description.split()[1].strip("<@!>"))
                    
                    # Отправляем подтверждение управляющему
                    await message.reply("✅ Скриншот принят! Уведомление отправлено адвокату.")
                    
                    # Отправляем подтверждение в канал
                    channel = bot.get_channel(channel_id)
                    if channel:
                        lawyer = await bot.fetch_user(lawyer_id)
                        
                        confirm_embed = discord.Embed(
                            title="✅ Платеж подтвержден",
                            description=f"Управляющий подтвердил получение платежа от {client_name}",
                            color=discord.Color.green(),
                            timestamp=datetime.now(timezone.utc)
                        )
                        confirm_embed.add_field(name="Сумма", value=amount, inline=False)
                        confirm_embed.add_field(name="Подтвердил", value=message.author.mention, inline=False)
                        confirm_embed.set_image(url=message.attachments[0].url)
                        
                        await channel.send(f"{lawyer.mention}", embed=confirm_embed)
                else:
                    await message.reply("❌ Пожалуйста, прикрепите скриншот перевода.")
        except Exception as e:
            print(f"Ошибка при обработке подтверждения платежа: {e}")


@bot.event
async def on_command_error(ctx, error):
    # Обработка ошибок для слеш-команд (ctx будет discord.ApplicationContext)
    if isinstance(ctx, discord.ApplicationContext):
        if isinstance(error, discord.ApplicationCommandInvokeError):
            original_error = error.original
            if isinstance(original_error, commands.CommandNotFound):
                await ctx.respond("Команда не найдена.", ephemeral=True)
            elif isinstance(original_error, commands.MissingPermissions):
                await ctx.respond("У вас нет прав на использование этой команды.", ephemeral=True)
            else:
                await ctx.respond(f"Произошла ошибка при выполнении команды: {original_error}", ephemeral=True)
        else:
            await ctx.respond(f"Произошла ошибка: {error}", ephemeral=True)
    # Обработка ошибок для префиксных команд (если они используются)
    elif isinstance(ctx, commands.Context):
        if isinstance(error, commands.CommandNotFound):
            await ctx.send("Команда не найдена.")
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("У вас нет прав на использование этой команды.")
        else:
            await ctx.send(f"Произошла ошибка: {error}")

def make_embed(title, description, color=discord.Color.blurple()):
    return discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now()
    )
# Обработчик для кнопки вызова адвоката
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        if interaction.data.get("custom_id") == "call_lawyer":
            # Проверяем что это клиент
            if not any(role.id == CLIENT_ROLE_ID for role in interaction.user.roles):
                await interaction.response.send_message("Только клиенты могут вызывать адвокатов!", ephemeral=True)
                return

            # Открываем модальное окно
            await interaction.response.send_modal(CallLawyerModal())

@bot.event
async def on_ready():
    print(f'Бот {bot.user} готов к работе!')

    for view in PERSISTENT_VIEWS.values():
        if view:
            bot.add_view(view)

    # Регистрируем persistent views
    bot.add_view(TicketView())
    bot.add_view(TakeTicketView())
    bot.add_view(ApprovePaymentView())
    bot.add_view(CallLawyerView(None, None, None, None))
    bot.add_view(OutcomeView(None))
    bot.add_view(JoinBureauView())

    # Получаем сервер
    guild = bot.get_guild(1379431094205550732)
    if not guild:
        print("Сервер не найден")
        return

    # Обновляем эмбед
    await update_lawyers_embed(bot, guild)

    channel = bot.get_channel(1408969018617888818)
    if channel:
        await send_ticket_embeds(channel)
    else:
        print("❌ Канал для тикетов не найден (ID: 1399117617905664060)")

    # Инициализируем канал отзывов
    review_channel = bot.get_channel(1392607447616720896)
    if review_channel:
        try:
            # Проверяем, есть ли приветственное сообщение
            async for message in review_channel.history(limit=1):
                if message.author == bot.user and message.embeds and "Отзывы о работе адвокатов" in message.embeds[0].title:
                    print("✅ Приветственное сообщение в канале отзывов уже существует")
                    break
            else:
                # Отправляем приветственное сообщение только если его нет
                embed = discord.Embed(
                    title="💫 Отзывы о работе адвокатов",
                    description="Здесь вы можете оставить свой отзыв о работе адвокатов бюро.\n\nКогда ваше обращение будет закрыто, вы получите уведомление с возможностью оставить отзыв.\n\nВаше мнение очень важно для нас!",
                    color=discord.Color.blue()
                )
                embed.set_footer(text="PACT Attorney | Система отзывов")
                await review_channel.send(embed=embed)
                print("✅ Создано новое приветственное сообщение в канале отзывов")
        except Exception as e:
            print(f"Ошибка при инициализации канала отзывов: {e}")
    else:
        print("❌ Канал отзывов не найден (ID: 1392607447616720896)")

    # Синхронизируем команды
    try:
        await bot.tree.sync()
        print("✅ Команды успешно синхронизированы")
    except Exception as e:
        print(f"Ошибка синхронизации команд: {e}")
import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

bot.run(TOKEN)

async def update_clients_registry(bot):
    import sqlite3
    conn = sqlite3.connect('lawyers.db')
    cursor = conn.cursor()
    cursor.execute("SELECT agreement_number, client_name, client_tag, lawyer_tag, client_passport FROM help_data")
    rows = cursor.fetchall()
    conn.close()

    registry_channel = bot.get_channel(1379612255594872893)
    if not registry_channel:
        return

    content_lines = ["**📜 Реестр клиентов**\n"]
    for row in rows:
        agreement, name, tag, lawyer, passport = row
        content_lines.append(f"**{agreement}** — {name} ({tag}) | Адвокат: {lawyer} | Паспорт: {passport}")

    if not registry_channel:
        print("❌ Канал реестра не найден")
        return

    try:
        await registry_channel.purge(limit=10)
        await registry_channel.send("\n".join(content_lines))
    except Exception as e:
        print(f"Ошибка обновления реестра: {e}")
