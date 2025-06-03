import asyncio
import logging
from telethon import TelegramClient
import os
from dotenv import load_dotenv
import json
import time

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
USER_SESSION     = str(API_ID) + 'session_name'  # 确保与上传的会话文件名匹配
PHONE_NUMBER    = config.get('phone_number', os.getenv('PHONE_NUMBER', ''))

logging.basicConfig(level=logging.INFO)  # 可改 DEBUG

from telethon.network.connection.tcpabridged import ConnectionTcpAbridged

proxy = ('socks5', '127.0.0.1', 7890)  # 或使用你的代理服务
user_client = TelegramClient('user', API_ID, API_HASH, proxy=proxy, connection=ConnectionTcpAbridged)
 

async def main():
    print("Connecting...")

    start = time.time()
    try:
        await asyncio.wait_for(user_client.connect(), timeout=10)
    except asyncio.TimeoutError:
        print("❌ connect() 超时！")
        return

    print(f"Connected after {time.time() - start:.2f}s")

    try:
        auth_status = await asyncio.wait_for(user_client.is_user_authorized(), timeout=10)
    except asyncio.TimeoutError:
        print("❌ is_user_authorized() 卡住！")
        return

    if not auth_status:
        print("尚未登录，需要认证")
        try:
            await user_client.send_code_request(PHONE_NUMBER)
            code = input('请输入你收到的验证码：')
            await user_client.sign_in(PHONE_NUMBER, code)
        except Exception as e:
            print(f"登录出错：{e}")
            return

    print("✅ 登录成功！")
    await user_client.send_message('me', 'Hello from Telethon!')
    await user_client.disconnect()

asyncio.run(main())
