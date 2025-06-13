import os
import base64
import pymysql
import asyncio
import json
import time
from dotenv import load_dotenv
import aiohttp
from telethon.sessions import StringSession
from telethon import TelegramClient, events
from telethon.tl.types import InputDocument
from telethon import events

# Aiogram 相关
from aiogram import F, Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import ContentType
from aiohttp import web

from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

import re



# ================= 1. 载入 .env 中的环境变量 =================
load_dotenv()  # 自动从当前目录的 .env 文件读取

config = {}
# 嘗試載入 JSON 並合併參數
try:
    configuration_json = json.loads(os.getenv('CONFIGURATION', '') or '{}')
    if isinstance(configuration_json, dict):
        config.update(configuration_json)  # 將 JSON 鍵值對合併到 config 中
except Exception as e:
    print(f"⚠️ 無法解析 CONFIGURATION：{e}")

API_ID          = int(config.get('api_id', os.getenv('API_ID', 0)))
API_HASH        = config.get('api_hash', os.getenv('API_HASH', ''))
PHONE_NUMBER    = config.get('phone_number', os.getenv('PHONE_NUMBER', ''))
BOT_TOKEN       = config.get('bot_token', os.getenv('BOT_TOKEN', ''))
TARGET_GROUP_ID = int(config.get('target_group_id', os.getenv('TARGET_GROUP_ID', 0)))
MYSQL_HOST      = config.get('db_host', os.getenv('MYSQL_DB_HOST', 'localhost'))
MYSQL_USER      = config.get('db_user', os.getenv('MYSQL_DB_USER', ''))
MYSQL_PASSWORD  = config.get('db_password', os.getenv('MYSQL_DB_PASSWORD', ''))
MYSQL_DB        = config.get('db_name', os.getenv('MYSQL_DB_NAME', ''))
MYSQL_DB_PORT   = int(config.get('db_port', os.getenv('MYSQL_DB_PORT', 3306)))
SESSION_STRING  = os.getenv("USER_SESSION_STRING")
BOT_MODE        = os.getenv("BOT_MODE", "polling").lower()
WEBHOOK_SECRET  = os.getenv("WEBHOOK_SECRET", "")  
WEBHOOK_PATH    = os.getenv("WEBHOOK_PATH", "/")       
WEBHOOK_HOST    = os.getenv("WEBHOOK_HOST")  # 确保设置为你的域名或 IP                                   
USER_SESSION    = str(API_ID) + 'session_name'  # 确保与上传的会话文件名匹配

# ================= 2. 初始化 MySQL 连接 =================
mysql_config = {
    'host'      : MYSQL_HOST,
    'user'      : MYSQL_USER,
    'password'  : MYSQL_PASSWORD,
    'database'  : MYSQL_DB,
    "port"      : MYSQL_DB_PORT,
    'charset'   : 'utf8mb4',
    'autocommit': True
}
db = pymysql.connect(**mysql_config)
cursor = db.cursor()

lz_var_start_time = time.time()
lz_var_cold_start_flag = True

# file_unique_id 通常是 base64 编码短字串，长度 20~35，字母+数字组成
file_unique_id_pattern = re.compile(r'^[A-Za-z0-9_-]{12,64}$')
# doc_id 是整数，通常为 Telegram 64-bit ID
doc_id_pattern = re.compile(r'^\d{10,20}$')


def safe_execute(sql, params=None):
    try:
        db.ping(reconnect=True)  # 检查连接状态并自动重连
        cursor.execute(sql, params or ())
        return cursor
    except Exception as e:
        print(f"⚠️ 数据库执行出错: {e}")
        return None

async def heartbeat():
    while True:
        print("💓 Alive (Aiogram polling still running)")
        try:
            db.ping(reconnect=True)
            print("✅ MySQL 连接正常")
        except Exception as e:
            print(f"⚠️ MySQL 保活失败：{e}")
        await asyncio.sleep(600)

async def health(request):
    uptime = time.time() - lz_var_start_time
    if lz_var_cold_start_flag or uptime < 10:
        return web.Response(text="⏳ Bot 正在唤醒，请稍候...", status=503)
    return web.Response(text="✅ Bot 正常运行", status=200)


async def on_startup(bot: Bot):
    webhook_url = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"
    print(f"🔗 設定 Telegram webhook 為：{webhook_url}")
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(webhook_url)
    lz_var_cold_start_flag = False  # 启动完成


# async def start_webhook_server():
#     BOT_MODE = os.getenv("BOT_MODE", "polling").lower()
#     if BOT_MODE != "webhook":
#         return  # 不处理，回到 polling 逻辑

#     BASE_URL = os.getenv("BASE_URL", "")
#     WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/")
#     WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
#     WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"
#     PORT = int(os.environ.get("PORT", 10000))

#     # 设置 Telegram webhook
#     await bot_client.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET)
#     print(f"✅ Webhook 设置成功：{WEBHOOK_URL}", flush=True)

#     app = web.Application()

#     # 健康检查（保留 GET /）
#     async def health(request):
#         return web.Response(text="OK")
#     app.router.add_get("/", health)

#     # 设置 aiogram webhook handler
#     SimpleRequestHandler(
#         dispatcher=dp,
#         bot=bot_client,
#         secret_token=WEBHOOK_SECRET,
#     ).register(app, path=WEBHOOK_PATH)

#     # 初始化 application
#     setup_application(app, dp)

#     runner = web.AppRunner(app)
#     await runner.setup()
#     site = web.TCPSite(runner, "0.0.0.0", PORT)
#     await site.start()
#     print(f"✅ aiohttp Web 服务监听端口：{PORT}", flush=True)

#     while True:
#         await asyncio.sleep(3600)



# ================= 3. Helper：从 media.attributes 提取文件名 =================
def get_file_name(media):
    from telethon.tl.types import DocumentAttributeFilename
    for attr in getattr(media, 'attributes', []):
        if isinstance(attr, DocumentAttributeFilename):
            return attr.file_name
    return None

# ================= 4. Upsert 函数：统一 Insert/Update 逻辑 =================
def upsert_file_record(fields: dict):
    """
    fields: dict, 键是列名, 值是要写入的内容。
    自动生成 INSERT ... ON DUPLICATE KEY UPDATE 语句。
    """
    if not fields:
        return
    cols = list(fields.keys())
    placeholders = ["%s"] * len(cols)
    update_clauses = [f"{col}=VALUES({col})" for col in cols]
    sql = f"""
        INSERT INTO file_records ({','.join(cols)})
        VALUES ({','.join(placeholders)})
        ON DUPLICATE KEY UPDATE {','.join(update_clauses)}
    """
    values = list(fields.values())
    try:
        safe_execute(sql, values)
    except Exception as e:
        print(f"110 Error: {e}")

# ================= 5.1 send_media_by_doc_id 函数 =================
async def send_media_by_doc_id(client, to_user_id, doc_id, client_type,msg_id=None):
    print(f"【send_media_by_doc_id】开始处理 doc_id={doc_id}，目标用户：{to_user_id}",flush=True)

    try:
        safe_execute(
            "SELECT chat_id, message_id, doc_id, access_hash, file_reference, file_id, file_unique_id,file_type "
            "FROM file_records WHERE doc_id = %s",
            (doc_id,)
        )
        row = cursor.fetchone()
    except Exception as e:
        print(f"121 Error: {e}")
        return

    if not row:
        if client_type == 'man':
            try:
                # 尝试将 user_id 解析成可用的 InputPeer 实体
                to_user_entity = await client.get_input_entity(to_user_id)
                await client.send_message(to_user_entity, f"未找到 doc_id={doc_id} 对应的文件记录。")
            except Exception as e:
                print(f"获取用户实体失败: {e}")
                await client.send_message('me', f"无法获取用户实体: {to_user_id}")
        else:
            await client.send_message(to_user_id, f"未找到 doc_id={doc_id} 对应的文件记录。")
        return

    if client_type == 'bot':
        # 机器人账号发送
        await send_media_via_bot(client, to_user_id, row, msg_id)
    else:
        await send_media_via_man(client, to_user_id, row, msg_id)

# ================= 5.2 send_media_by_file_unique_id 函数 =================
async def send_media_by_file_unique_id(client, to_user_id, file_unique_id, client_type, msg_id):
    print(f"【send_media_by_file_unique_id】开始处理 file_unique_id={file_unique_id}，目标用户：{to_user_id}",flush=True)
    try:
        safe_execute(
            "SELECT chat_id, message_id, doc_id, access_hash, file_reference, file_id, file_unique_id,file_type FROM file_records WHERE file_unique_id = %s",
            (file_unique_id,)
        )
        row = cursor.fetchone()

        if not row:
            await client.send_message(to_user_id, f"未找到 file_unique_id={file_unique_id} 对应的文件。")
            return
    
    except Exception as e:
        print(f"148 Error: {e}")
        return
    if client_type == 'bot':
        # 机器人账号发送
        await send_media_via_bot(client, to_user_id, row, msg_id)
    else:
        await send_media_via_man(client, to_user_id, row, msg_id)

# ================= 6.1 send_media_via_man 函数 =================
async def send_media_via_man(client, to_user_id, row, msg_id=None):
    # to_user_entity = await client.get_input_entity(to_user_id)
    chat_id, message_id, doc_id, access_hash, file_reference_hex, file_id, file_unique_id, file_type = row
    try:
        file_reference = bytes.fromhex(file_reference_hex)
    except:
        import base64
        try:
            file_reference = base64.b64decode(file_reference_hex)
        except:
            await client.send_message(to_user_id, "文件引用格式异常，无法发送。")
            return

    input_doc = InputDocument(
        id=doc_id,
        access_hash=access_hash,
        file_reference=file_reference
    )
    try:
        await client.send_file(to_user_id, input_doc, reply_to=msg_id)
    except Exception:
        # file_reference 过期时，重新从历史消息拉取
        try:
            msg = await client.get_messages(chat_id, ids=message_id)
            media = msg.document or msg.photo or msg.video
            if not media:
                print(f"历史消息中未找到对应媒体，可能已被删除。",flush=True)
                await client.send_message(to_user_id, "历史消息中未找到对应媒体，可能已被删除。")
                return
            print(f"重新获取文件引用：{media.id}, {media.access_hash}, {media.file_reference.hex()}",flush=True)
            # 区分 photo 和 document
            if msg.document:
                new_input = InputDocument(
                    id=msg.document.id,
                    access_hash=msg.document.access_hash,
                    file_reference=msg.document.file_reference
                )
            elif msg.photo:
                new_input = msg.photo  # 直接发送 photo 不需要构建 InputDocument
            else:
                await client.send_message(to_user_id, "暂不支持此媒体类型。")
                return
            
            
            print(f"重新获取文件引用成功，准备发送。",flush=True)
          

            await client.send_file(to_user_id, new_input, reply_to=msg_id)
        except Exception as e:
            print(f"发送文件时出错：{e}",flush=True)
            await client.send_message(to_user_id, f"发送文件时出错：{e}")

# ================= 6.2 send_media_via_bot 函数 =================
async def send_media_via_bot(bot_client, to_user_id, row,msg_id=None):
    """
    bot_client: Aiogram Bot 实例
    row: (chat_id, message_id, doc_id, access_hash, file_reference_hex, file_id, file_unique_id)
    """
    chat_id, message_id, doc_id, access_hash, file_reference_hex, file_id, file_unique_id, file_type = row


    try:
        if file_type== "photo":
            # 照片（但不包括 GIF）
            await bot_client.send_photo(to_user_id, file_id, reply_to_message_id=msg_id)
      
        elif file_type == "video":
            # 视频
            await bot_client.send_video(to_user_id, file_id, reply_to_message_id=msg_id)
        elif file_type == "document":
            # 其他一律当文件发
            await bot_client.send_document(to_user_id, file_id, reply_to_message_id=msg_id)

    except Exception as e:
        await bot_client.send_message(to_user_id, f"⚠️ 发送文件失败：{e}")
    



# ================= 7. 初始化 Telethon 客户端 =================

if SESSION_STRING:
    user_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    print("【Telethon】使用 StringSession 登录。",flush=True)
else:
    user_client = TelegramClient(USER_SESSION, API_ID, API_HASH)






# ================= 8. 私聊文字处理：人类账号 =================



@user_client.on(events.NewMessage(incoming=True))
async def handle_user_private_text(event):
    msg = event.message
    if not msg.is_private or msg.media or not msg.text:
        return

    print(f"【Telethon】收到私聊文本：{event.message.text}，来自 {event.message.from_id}",flush=True)

    text = msg.text.strip()
    to_user_id = msg.from_id


    if text:
        try:
            match = re.search(r'\|_kick_\|\s*(.*?)\s*(bot)', text, re.IGNORECASE)
            if match:
                botname = match.group(1) + match.group(2)
                await user_client.send_message(botname, "/start")
                await user_client.send_message(botname, "[~bot~]")
                await event.delete()
                return
        except Exception as e:
                print(f"Error kicking bot: {e} {botname}", flush=True)


    if file_unique_id_pattern.fullmatch(text):
        file_unique_id = text
        await send_media_by_file_unique_id(user_client, to_user_id, file_unique_id, 'man', msg.id)
    elif doc_id_pattern.fullmatch(text):
        doc_id = int(text)
        await send_media_by_doc_id(user_client, to_user_id, doc_id, 'man', msg.id)
       
    else:
        await event.delete()

    # if text.isdigit():
    #     doc_id = int(text)
    #     await send_media_by_doc_id(user_client, to_user_id, doc_id, 'man', msg.id)
    # else:
    #     file_unique_id = text
    #     await send_media_by_file_unique_id(user_client, to_user_id, file_unique_id, 'man', msg.id)

    # await event.delete()

# ================= 9. 私聊媒体处理：人类账号 =================
@user_client.on(events.NewMessage(incoming=True))
async def handle_user_private_media(event):
    msg = event.message
    if not msg.is_private or not (msg.document or msg.photo or msg.video):
        return
    print(f"【Telethon】收到私聊媒体：{event.message.media}，来自 {event.message.from_id}",flush=True)
    print(f"{msg}",flush=True)
    print(f"{event.message.text}",flush=True)
    

    if msg.document:
        media = msg.document
        file_type = 'document'
    elif msg.video:
        media = msg.video
        file_type = 'video'
    else:
        media = msg.photo
        file_type = 'photo'

    doc_id         = media.id
    access_hash    = media.access_hash
    file_reference = media.file_reference.hex()
    mime_type      = getattr(media, 'mime_type', 'image/jpeg' if msg.photo else None)
    file_size      = getattr(media, 'size', None)
    file_name      = get_file_name(media)
    caption        = event.message.text or ""

    # 检查：TARGET_GROUP_ID 群组是否已有相同 doc_id
    try:
        safe_execute(
            "SELECT 1 FROM file_records WHERE doc_id = %s AND chat_id = %s AND file_unique_id IS NOT NULL",
            (doc_id, TARGET_GROUP_ID)
        )
    except Exception as e:
        print(f"272 Error: {e}")
        
   
    match = re.search(r'\|_forward_\|\s*@([^\s]+)', caption, re.IGNORECASE)
    
    print(f"【Telethon】匹配到的转发模式：{match}",flush=True)
   
    if match:
        captured_str = match.group(1).strip()  # 捕获到的字符串
        print(f"【Telethon】捕获到的字符串：{captured_str}",flush=True)

        

        if captured_str.isdigit():
            print(f"【Telethon】捕获到的字符串是数字：{captured_str}",flush=True)
            destination_chat_id = int(captured_str)
        else:
            print(f"【Telethon】捕获到的字符串不是数字：{captured_str}",flush=True)
            captured_str = str(captured_str)
            if captured_str.startswith('-100'):
                captured_str = captured_str.replace('-100','')
            #判断 captured_str 是否为数字
            destination_chat_id = str(captured_str)
        
        ret = await user_client.send_file(destination_chat_id, msg.media)
        print(f"【Telethon】已转发到目标群组：{destination_chat_id}，消息 ID：{ret.id}",flush=True)
        print(f"{ret}")

    if cursor.fetchone():
        await event.delete()
        return

    # 转发到群组，并删除私聊
    ret = await user_client.send_file(TARGET_GROUP_ID, msg.media)

    



    # 插入或更新 placeholder 记录 (message_id 自动留空，由群组回调补全)
    upsert_file_record({
        'chat_id'       : ret.chat_id,
        'message_id'    : ret.id,
        'doc_id'        : doc_id,
        'access_hash'   : access_hash,
        'file_reference': file_reference,
        'mime_type'     : mime_type,
        'file_type'     : file_type,
        'file_name'     : file_name,
        'file_size'     : file_size,
        'uploader_type' : 'user'
    })
    await event.delete()



# ================= 12. 群组媒体处理：人类账号 =================
@user_client.on(events.NewMessage(chats=TARGET_GROUP_ID, incoming=True))
async def handle_user_group_media(event):
    print(f"【Telethon】在指定群组，收到群组媒体：{event.message.media}，来自 {event.message.from_id}",flush=True)
    msg = event.message
    if not (msg.document or msg.photo or msg.video):
        return

    if msg.document:
        media = msg.document
        file_type = 'document'
    elif msg.video:
        media = msg.video
        file_type = 'video'
    else:
        media = msg.photo
        file_type = 'photo'

    chat_id        = msg.chat_id
    message_id     = msg.id
    doc_id         = media.id
    access_hash    = media.access_hash
    file_reference = media.file_reference.hex()
    mime_type      = getattr(media, 'mime_type', 'image/jpeg' if msg.photo else None)
    file_size      = getattr(media, 'size', None)
    file_name      = get_file_name(media)

    # —— 步骤 A：先按 doc_id 查库 —— 
    try:
        # 检查是否已存在相同 doc_id 的记录
        safe_execute(
            "SELECT chat_id, message_id FROM file_records WHERE doc_id = %s",
            (doc_id,)
        )
    except Exception as e:
        print(f"332 Error: {e}")
   
    row = cursor.fetchone()
    if row:
        existing_chat_id, existing_msg_id = row
        if not (existing_chat_id == chat_id and existing_msg_id == message_id):
            # 重复上传到不同消息 → 更新并删除新消息
            upsert_file_record({
                'doc_id'        : doc_id,
                'access_hash'   : access_hash,
                'file_reference': file_reference,
                'mime_type'     : mime_type,
                'file_type'     : file_type,
                'file_name'     : file_name,
                'file_size'     : file_size,
                'uploader_type' : 'user',
                'chat_id'       : chat_id,
                'message_id'    : message_id
            })
            await event.delete()
        else:
            # 同一条消息重复触发 → 仅更新，不删除
            upsert_file_record({
                'chat_id'       : chat_id,
                'message_id'    : message_id,
                'access_hash'   : access_hash,
                'file_reference': file_reference,
                'mime_type'     : mime_type,
                'file_type'     : file_type,
                'file_name'     : file_name,
                'file_size'     : file_size,
                'uploader_type' : 'user'
            })
        return

    # —— 步骤 B：若 A 中没找到，再按 (chat_id, message_id) 查库 ——
    try:
        safe_execute(
            "SELECT id FROM file_records WHERE chat_id = %s AND message_id = %s",
            (chat_id, message_id)
        )
    except Exception as e:
        print(f"372 Error: {e}")
    if cursor.fetchone():
        # 已存在同条消息 → 更新并保留
        upsert_file_record({
            'chat_id'       : chat_id,
            'message_id'    : message_id,
            'doc_id'        : doc_id,
            'access_hash'   : access_hash,
            'file_reference': file_reference,
            'mime_type'     : mime_type,
            'file_type'     : file_type,
            'file_name'     : file_name,
            'file_size'     : file_size,
            'uploader_type' : 'user'
        })
    else:
        # 全新媒体 → 插入并保留
        upsert_file_record({
            'chat_id'       : chat_id,
            'message_id'    : message_id,
            'doc_id'        : doc_id,
            'access_hash'   : access_hash,
            'file_reference': file_reference,
            'mime_type'     : mime_type,
            'file_type'     : file_type,
            'file_name'     : file_name,
            'file_size'     : file_size,
            'uploader_type' : 'user'
        })
    # B 分支保留消息，不删除


bot_client = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()

# —— 9.1 Aiogram：Bot 私聊 文本 处理 —— 

@dp.message(F.chat.type == "private", F.content_type.in_({ContentType.TEXT}))
async def aiogram_handle_private_text(message: types.Message):
    print(f"【Aiogram】收到私聊文本：{message.text}，来自 {message.from_user.id}",flush=True)
    # 只处理“私聊里发来的文本”
    if message.chat.type != "private" or message.content_type != ContentType.TEXT:
        return
    text = message.text.strip()
    to_user_id = message.chat.id
    reply_to_message = message.message_id

  

    if file_unique_id_pattern.fullmatch(text):
        await send_media_by_file_unique_id(bot_client, to_user_id, text, 'bot', reply_to_message)
    elif doc_id_pattern.fullmatch(text):
        await send_media_by_doc_id(bot_client, to_user_id, int(text), 'bot', reply_to_message)
    else:
        await message.delete()

# —— 9.2 Aiogram：Bot 私聊 媒体 处理 —— 
# 私聊媒体（图片/文档/视频）
@dp.message(F.chat.type == "private", F.content_type.in_({ContentType.PHOTO, ContentType.DOCUMENT, ContentType.VIDEO}))
async def aiogram_handle_private_media(message: types.Message):
    print(f"【Aiogram】收到私聊媒体：{message.content_type}，来自 {message.from_user.id}",flush=True)
    # 只处理“私聊里发来的媒体”
    if message.chat.type != "private" or message.content_type not in {
        ContentType.PHOTO, ContentType.DOCUMENT, ContentType.VIDEO
    }:
        return
    


    if message.photo:
        largest = message.photo[-1]
        file_id = largest.file_id
        file_unique_id = largest.file_unique_id
        mime_type = 'image/jpeg'
        file_type = 'photo'
        file_size = largest.file_size
        file_name = None
        # 用 Bot API 发到目标群组
      

    elif message.document:
        file_id = message.document.file_id
        file_unique_id = message.document.file_unique_id
        mime_type = message.document.mime_type
        file_type = 'document'
        file_size = message.document.file_size
        file_name = message.document.file_name
       

    else:  # 视频
        file_id = message.video.file_id
        file_unique_id = message.video.file_unique_id
        mime_type = message.video.mime_type or 'video/mp4'
        file_type = 'video'
        file_size = message.video.file_size
        file_name = getattr(message.video, 'file_name', None)
       

    # ⬇️ 检查是否已存在
    if await check_file_exists_by_unique_id(file_unique_id):
        print(f"已存在：{file_unique_id}，跳过转发",flush=True)

    else:
        ret = None
        # ⬇️ 发到群组
        if message.photo:
            ret = await bot_client.send_photo(TARGET_GROUP_ID, file_id)
        elif message.document:
            ret = await bot_client.send_document(TARGET_GROUP_ID, file_id)
        else:
            ret = await bot_client.send_video(TARGET_GROUP_ID, file_id)

        if ret.photo:
            largest = ret.photo[-1]
            file_unique_id = largest.file_unique_id
            file_id = largest.file_id
            file_type = 'photo'
            mime_type = 'image/jpeg'
            file_size = largest.file_size
            file_name = None

        elif ret.document:
            file_unique_id = ret.document.file_unique_id
            file_id = ret.document.file_id
            file_type = 'document'
            mime_type = ret.document.mime_type
            file_size = ret.document.file_size
            file_name = ret.document.file_name

        else:  # msg.video
            file_unique_id = ret.video.file_unique_id
            file_id = ret.video.file_id
            file_type = 'video'
            mime_type = ret.video.mime_type or 'video/mp4'
            file_size = ret.video.file_size
            file_name = getattr(ret.video, 'file_name', None)

        chat_id = ret.chat.id
        message_id = ret.message_id
        upsert_file_record({
                'file_unique_id': file_unique_id,
                'file_id'       : file_id,
                'file_type'     : file_type,
                'mime_type'     : mime_type,
                'file_name'     : file_name,
                'file_size'     : file_size,
                'uploader_type' : 'bot',
                'chat_id'       : chat_id,
                'message_id'    : message_id
            })

    # print(f"{ret} 已发送到目标群组：{TARGET_GROUP_ID}")
   
    await message.delete()

async def check_file_exists_by_unique_id(file_unique_id):
    """
    检查 file_unique_id 是否已存在于数据库中。
    """
    try:
        safe_execute("SELECT 1 FROM file_records WHERE file_unique_id = %s AND doc_id IS NOT NULL LIMIT 1", (file_unique_id,))
    except Exception as e:
        print(f"528 Error: {e}")
        
    return cursor.fetchone() is not None

# —— 9.3 Aiogram：Bot 群组 媒体 处理 —— 
# 群组媒体（图片/文档/视频），只处理指定群组
@dp.message(F.chat.id == TARGET_GROUP_ID, F.content_type.in_({ContentType.PHOTO, ContentType.DOCUMENT, ContentType.VIDEO}))
async def aiogram_handle_group_media(message: types.Message):
    print(f"【Aiogram】收到群组媒体：{message.content_type}，来自 {message.from_user.id}",flush=True)
    # 只处理“指定群组里发来的媒体”
    if message.chat.id != TARGET_GROUP_ID or message.content_type not in {
        ContentType.PHOTO, ContentType.DOCUMENT, ContentType.VIDEO
    }:
        return

    
    msg = message

    if msg.photo:
        largest = msg.photo[-1]
        file_unique_id = largest.file_unique_id
        file_id = largest.file_id
        file_type = 'photo'
        mime_type = 'image/jpeg'
        file_size = largest.file_size
        file_name = None

    elif msg.document:
        file_unique_id = msg.document.file_unique_id
        file_id = msg.document.file_id
        file_type = 'document'
        mime_type = msg.document.mime_type
        file_size = msg.document.file_size
        file_name = msg.document.file_name

    else:  # msg.video
        file_unique_id = msg.video.file_unique_id
        file_id = msg.video.file_id
        file_type = 'video'
        mime_type = msg.video.mime_type or 'video/mp4'
        file_size = msg.video.file_size
        file_name = getattr(msg.video, 'file_name', None)

    chat_id = msg.chat.id
    message_id = msg.message_id

    try:
        # 检查是否已存在相同 file_unique_id 的记录
        safe_execute(
            "SELECT chat_id, message_id FROM file_records WHERE file_unique_id = %s",
            (file_unique_id,)
        )
    except Exception as e:
        print(f"578 Error: {e}")
   
    row = cursor.fetchone()
    if row:
        existing_chat_id, existing_msg_id = row
        if not (existing_chat_id == chat_id and existing_msg_id == message_id):
            upsert_file_record({
                'file_unique_id': file_unique_id,
                'file_id'       : file_id,
                'file_type'     : file_type,
                'mime_type'     : mime_type,
                'file_name'     : file_name,
                'file_size'     : file_size,
                'uploader_type' : 'bot',
                'chat_id'       : chat_id,
                'message_id'    : message_id
            })
            await bot_client.delete_message(chat_id, message_id)
        else:
            upsert_file_record({
                'chat_id'       : chat_id,
                'message_id'    : message_id,
                'file_unique_id': file_unique_id,
                'file_id'       : file_id,
                'file_type'     : file_type,
                'mime_type'     : mime_type,
                'file_name'     : file_name,
                'file_size'     : file_size,
                'uploader_type' : 'bot'
            })
        return

    try:
        safe_execute(
            "SELECT id FROM file_records WHERE chat_id = %s AND message_id = %s",
            (chat_id, message_id)
        )
    except Exception as e:
        print(f"614 Error: {e}")

    if cursor.fetchone():
        upsert_file_record({
            'chat_id'       : chat_id,
            'message_id'    : message_id,
            'file_unique_id': file_unique_id,
            'file_id'       : file_id,
            'file_type'     : file_type,
            'mime_type'     : mime_type,
            'file_name'     : file_name,
            'file_size'     : file_size,
            'uploader_type' : 'bot'
        })
    else:
        upsert_file_record({
            'chat_id'       : chat_id,
            'message_id'    : message_id,
            'file_unique_id': file_unique_id,
            'file_id'       : file_id,
            'file_type'     : file_type,
            'mime_type'     : mime_type,
            'file_name'     : file_name,
            'file_size'     : file_size,
            'uploader_type' : 'bot'
        })


async def keep_alive_ping():
    url = f"{WEBHOOK_HOST}{WEBHOOK_PATH}" if BOT_MODE == "webhook" else f"{WEBHOOK_HOST}/"
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    print(f"🌐 Keep-alive ping {url} status {resp.status}")
        except Exception as e:
            print(f"⚠️ Keep-alive ping failed: {e}")
        await asyncio.sleep(300)  # 每 5 分鐘 ping 一次


# ================= 14. 启动两个客户端 =================
async def main():
# 10.1 Telethon “人类账号” 登录

   

    task_heartbeat = asyncio.create_task(heartbeat())

    await user_client.start(PHONE_NUMBER)
    print("【Telethon】人类账号 已启动。",flush=True)

    # 10.2 并行运行 Telethon 与 Aiogram
    task_telethon = asyncio.create_task(user_client.run_until_disconnected())

    if BOT_MODE == "webhook":
        dp.startup.register(on_startup)
        print("🚀 啟動 Webhook 模式")

        app = web.Application()
        app.router.add_get("/", health)  # ✅ 健康检查路由

        SimpleRequestHandler(dispatcher=dp, bot=bot_client).register(app, path=WEBHOOK_PATH)
        setup_application(app, dp, bot=bot_client)

        task_keep_alive = asyncio.create_task(keep_alive_ping())

        # ✅ Render 环境用 PORT，否则本地用 8080
        port = int(os.environ.get("PORT", 8080))
        await web._run_app(app, host="0.0.0.0", port=port)
    else:
        print("【Aiogram】Bot（纯 Bot-API） 已启动，监听私聊＋群组媒体。",flush=True)
        await dp.start_polling(bot_client)  # Aiogram 轮询

    

    # 理论上 Aiogram 轮询不会退出，若退出则让 Telethon 同样停止
    task_telethon.cancel()

if __name__ == "__main__":
    asyncio.run(main())

