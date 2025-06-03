import os
import pymysql
import asyncio
import json
from dotenv import load_dotenv
from telethon.sessions import StringSession
from telethon import TelegramClient, events
from telethon.tl.types import InputDocument

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
BOT_TOKEN       = config.get('bot_token', os.getenv('BOT_TOKEN', ''))
TARGET_GROUP_ID = int(config.get('target_group_id', os.getenv('TARGET_GROUP_ID', 0)))
MYSQL_HOST      = config.get('db_host', os.getenv('MYSQL_DB_HOST', 'localhost'))
MYSQL_USER      = config.get('db_user', os.getenv('MYSQL_DB_USER', ''))
MYSQL_PASSWORD  = config.get('db_password', os.getenv('MYSQL_DB_PASSWORD', ''))
MYSQL_DB        = config.get('db_name', os.getenv('MYSQL_DB_NAME', ''))
MYSQL_DB_PORT = int(config.get('db_port', os.getenv('MYSQL_DB_PORT', 3306)))

                                                
USER_SESSION     = str(API_ID) + 'session_name'  # 确保与上传的会话文件名匹配
BOT_SESSION      = str(API_ID) + '_bot_session_name'  # 确保与上传的会话文件名匹配
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
    cursor.execute(sql, values)

# ================= 5.1 send_media_by_doc_id 函数 =================
async def send_media_by_doc_id(client, to_user_id, doc_id, client_type):
    cursor.execute(
        "SELECT chat_id, message_id, doc_id, access_hash, file_reference, file_id, file_unique_id "
        "FROM file_records WHERE doc_id = %s",
        (doc_id,)
    )
    row = cursor.fetchone()
    if not row:
        await client.send_message(to_user_id, f"未找到 doc_id={doc_id} 对应的文件记录。")
        return
    if client_type == 'bot':
        # 机器人账号发送
        await send_media_via_bot(client, to_user_id, row)
    else:
        await send_media_via_man(client, to_user_id, row)

# ================= 5.2 send_media_by_file_unique_id 函数 =================
async def send_media_by_file_unique_id(client, to_user_id, file_unique_id, client_type):
    cursor.execute(
        "SELECT chat_id, message_id, doc_id, access_hash, file_reference, file_id, file_unique_id FROM file_records WHERE file_unique_id = %s",
        (file_unique_id,)
    )
    row = cursor.fetchone()
    if not row:
        await client.send_message(to_user_id, f"未找到 file_unique_id={file_unique_id} 对应的文件。")
        return
    
    if client_type == 'bot':
        # 机器人账号发送
        await send_media_via_bot(client, to_user_id, row)
    else:
        await send_media_via_man(client, to_user_id, row)

# ================= 6.1 send_media_via_man 函数 =================
async def send_media_via_man(client, to_user_id, row):

    chat_id, message_id, doc_id, access_hash, file_reference_hex, file_id, file_unique_id = row
   
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
        await client.send_file(to_user_id, input_doc)
    except Exception:
        # file_reference 过期时，重新从历史消息拉取
        try:
            msg = await client.get_messages(chat_id, ids=message_id)
            media = msg.document or msg.photo or msg.video
            if not media:
                await client.send_message(to_user_id, "历史消息中未找到对应媒体，可能已被删除。")
                return
            new_input = InputDocument(
                id=media.id,
                access_hash=media.access_hash,
                file_reference=media.file_reference
            )
            await client.send_file(to_user_id, new_input)
        except Exception as e:
            await client.send_message(to_user_id, f"发送文件时出错：{e}")

# ================= 6.2 send_media_via_bot 函数 =================
async def send_media_via_bot(client, to_user_id, row):
    chat_id, message_id, doc_id, access_hash, file_reference_hex, file_id, file_unique_id = row
    try:
        await client.send_file(to_user_id, file_id)
    except Exception as e:
        await client.send_message(to_user_id, f"发送文件时出错：{e}")

# ================= 7. 初始化 Telethon 客户端 =================
user_client = TelegramClient(USER_SESSION, API_ID, API_HASH)
bot_client = TelegramClient(BOT_SESSION, API_ID, API_HASH)



# ================= 8. 私聊文字处理：人类账号 =================
@user_client.on(events.NewMessage(incoming=True))
async def handle_user_private_text(event):
    msg = event.message
    if not msg.is_private or msg.media or not msg.text:
        return

    text = msg.text.strip()
    to_user_id = msg.from_id

    if text.isdigit():
        doc_id = int(text)
        await send_media_by_doc_id(user_client, to_user_id, doc_id, client_type='man')
    else:
        file_unique_id = text
        await send_media_by_file_unique_id(user_client, to_user_id, file_unique_id, client_type='man')

    await event.delete()

# ================= 9. 私聊媒体处理：人类账号 =================
@user_client.on(events.NewMessage(incoming=True))
async def handle_user_private_media(event):
    msg = event.message
    if not msg.is_private or not (msg.document or msg.photo or msg.video):
        return

    if msg.document:
        media = msg.document
    elif msg.video:
        media = msg.video
    else:
        media = msg.photo

    doc_id         = media.id
    access_hash    = media.access_hash
    file_reference = media.file_reference.hex()
    mime_type      = getattr(media, 'mime_type', 'image/jpeg' if msg.photo else None)
    file_size      = getattr(media, 'size', None)
    file_name      = get_file_name(media)

    # 检查：TARGET_GROUP_ID 群组是否已有相同 doc_id
    cursor.execute(
        "SELECT 1 FROM file_records WHERE doc_id = %s AND chat_id = %s",
        (doc_id, TARGET_GROUP_ID)
    )
    if cursor.fetchone():
        await event.delete()
        return

    # 转发到群组，并删除私聊
    await user_client.send_file(TARGET_GROUP_ID, msg.media)

    # 插入或更新 placeholder 记录 (message_id 自动留空，由群组回调补全)
    upsert_file_record({
        'chat_id'       : TARGET_GROUP_ID,
        'message_id'    : None,
        'doc_id'        : doc_id,
        'access_hash'   : access_hash,
        'file_reference': file_reference,
        'mime_type'     : mime_type,
        'file_name'     : file_name,
        'file_size'     : file_size,
        'uploader_type' : 'user'
    })
    await event.delete()

# ================= 10. 私聊文字处理：机器人账号 =================
@bot_client.on(events.NewMessage(incoming=True))
async def handle_bot_private_text(event):
    msg = event.message
    if not msg.is_private or msg.media or not msg.text:
        return

    text = msg.text.strip()
    to_user_id = msg.from_id


    if text.isdigit():
        doc_id = int(text)
        await send_media_by_doc_id(bot_client, to_user_id, doc_id, client_type='bot')
    else:
        file_unique_id = text
        await send_media_by_file_unique_id(bot_client, to_user_id, file_unique_id, client_type='bot')

    await event.delete()

# ================= 11. 私聊媒体处理：机器人账号 =================
@bot_client.on(events.NewMessage(incoming=True))
async def handle_bot_private_media(event):
    msg = event.message
    if not msg.is_private or not (msg.document or msg.photo or msg.video):
        return

    if msg.document:
        media = msg.document
    elif msg.video:
        media = msg.video
    else:
        media = msg.photo

    file_id        = media.file_id
    file_unique_id = media.file_unique_id
    mime_type      = getattr(media, 'mime_type', 'image/jpeg' if msg.photo else None)
    file_size      = getattr(media, 'size', None)
    file_name      = get_file_name(media)

    # 检查：TARGET_GROUP_ID 群组是否已有相同 file_unique_id
    cursor.execute(
        "SELECT 1 FROM file_records WHERE file_unique_id = %s AND chat_id = %s",
        (file_unique_id, TARGET_GROUP_ID)
    )
    if cursor.fetchone():
        await event.delete()
        return

    # 转发到群组，并删除私聊
    await bot_client.send_file(TARGET_GROUP_ID, msg.media)

    # 插入或更新 placeholder 记录
    upsert_file_record({
        'chat_id'       : TARGET_GROUP_ID,
        'message_id'    : None,
        'file_id'       : file_id,
        'file_unique_id': file_unique_id,
        'mime_type'     : mime_type,
        'file_name'     : file_name,
        'file_size'     : file_size,
        'uploader_type' : 'bot'
    })
    await event.delete()

# ================= 12. 群组媒体处理：人类账号 =================
@user_client.on(events.NewMessage(chats=TARGET_GROUP_ID, incoming=True))
async def handle_user_group_media(event):
    msg = event.message
    if not (msg.document or msg.photo or msg.video):
        return

    if msg.document:
        media = msg.document
    elif msg.video:
        media = msg.video
    else:
        media = msg.photo

    chat_id        = msg.chat_id
    message_id     = msg.id
    doc_id         = media.id
    access_hash    = media.access_hash
    file_reference = media.file_reference.hex()
    mime_type      = getattr(media, 'mime_type', 'image/jpeg' if msg.photo else None)
    file_size      = getattr(media, 'size', None)
    file_name      = get_file_name(media)

    # —— 步骤 A：先按 doc_id 查库 —— 
    cursor.execute(
        "SELECT chat_id, message_id FROM file_records WHERE doc_id = %s",
        (doc_id,)
    )
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
                'file_name'     : file_name,
                'file_size'     : file_size,
                'uploader_type' : 'user'
            })
        return

    # —— 步骤 B：若 A 中没找到，再按 (chat_id, message_id) 查库 ——
    cursor.execute(
        "SELECT id FROM file_records WHERE chat_id = %s AND message_id = %s",
        (chat_id, message_id)
    )
    if cursor.fetchone():
        # 已存在同条消息 → 更新并保留
        upsert_file_record({
            'chat_id'       : chat_id,
            'message_id'    : message_id,
            'doc_id'        : doc_id,
            'access_hash'   : access_hash,
            'file_reference': file_reference,
            'mime_type'     : mime_type,
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
            'file_name'     : file_name,
            'file_size'     : file_size,
            'uploader_type' : 'user'
        })
    # B 分支保留消息，不删除

# ================= 13. 群组媒体处理：机器人账号 =================
@bot_client.on(events.NewMessage(chats=TARGET_GROUP_ID, incoming=True))
async def handle_bot_group_media(event):
    msg = event.message
    if not (msg.document or msg.photo or msg.video):
        return

    if msg.document:
        media = msg.document
    elif msg.video:
        media = msg.video
    else:
        media = msg.photo

    chat_id        = msg.chat_id
    message_id     = msg.id
    file_id        = media.file_id
    file_unique_id = media.file_unique_id
    mime_type      = getattr(media, 'mime_type', 'image/jpeg' if msg.photo else None)
    file_size      = getattr(media, 'size', None)
    file_name      = get_file_name(media)

    # —— 步骤 A：先按 file_unique_id 查库 ——
    cursor.execute(
        "SELECT chat_id, message_id FROM file_records WHERE file_unique_id = %s",
        (file_unique_id,)
    )
    row = cursor.fetchone()
    if row:
        existing_chat_id, existing_msg_id = row
        if not (existing_chat_id == chat_id and existing_msg_id == message_id):
            # 重复内容到不同消息 → 更新并删除新消息
            upsert_file_record({
                'file_unique_id': file_unique_id,
                'file_id'       : file_id,
                'mime_type'     : mime_type,
                'file_name'     : file_name,
                'file_size'     : file_size,
                'uploader_type' : 'bot',
                'chat_id'       : chat_id,
                'message_id'    : message_id
            })
            await event.delete()
        else:
            # 同一条消息重复触发 → 仅更新，不删除
            upsert_file_record({
                'chat_id'       : chat_id,
                'message_id'    : message_id,
                'file_id'       : file_id,
                'mime_type'     : mime_type,
                'file_name'     : file_name,
                'file_size'     : file_size,
                'uploader_type' : 'bot'
            })
        return

    # —— 步骤 B：若 A 中没找到，再按 (chat_id, message_id) 查库 ——
    cursor.execute(
        "SELECT id FROM file_records WHERE chat_id = %s AND message_id = %s",
        (chat_id, message_id)
    )
    if cursor.fetchone():
        # 已存在同条消息 → 更新并保留
        upsert_file_record({
            'chat_id'       : chat_id,
            'message_id'    : message_id,
            'file_unique_id': file_unique_id,
            'file_id'       : file_id,
            'mime_type'     : mime_type,
            'file_name'     : file_name,
            'file_size'     : file_size,
            'uploader_type' : 'bot'
        })
    else:
        # 全新媒体 → 插入并保留
        upsert_file_record({
            'chat_id'       : chat_id,
            'message_id'    : message_id,
            'file_unique_id': file_unique_id,
            'file_id'       : file_id,
            'mime_type'     : mime_type,
            'file_name'     : file_name,
            'file_size'     : file_size,
            'uploader_type' : 'bot'
        })
    # B 分支保留消息，不删除

# ================= 14. 启动两个客户端 =================
async def main():
    await user_client.start()
   
    await bot_client.start(bot_token=BOT_TOKEN)
    print("【INFO】“人类账号”与“机器人账号” 已启动，监听私聊文本/媒体与目标群组。")
    await asyncio.gather(
        user_client.run_until_disconnected(),
        bot_client.run_until_disconnected()
    )

if __name__ == "__main__":
    asyncio.run(main())
