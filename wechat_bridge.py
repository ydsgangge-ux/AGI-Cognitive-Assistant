"""
WeChat Work Bot Bridge Module v1
Shares the same agent instance with server.py
User ID mapping: wx_{WeChatUserId}

Usage:
  1. WeChat Work admin -> App Management -> Create App -> Configure message receiving
  2. URL: http://your-public-address/wechat (requires frp/ngrok mapping to port 18765)
  3. Token: any string, keep consistent with WECHAT_TOKEN below
"""

import hashlib
import xml.etree.ElementTree as ET
import time
import logging

logger = logging.getLogger("wechat")

# Shared agent instance (injected by server.py start_server)
_agent = None

# WeChat Work Token (modify to match WeChat Work admin panel)
WECHAT_TOKEN = "myAGI123"


def init(agent):
    global _agent
    _agent = agent


def set_token(token: str):
    global WECHAT_TOKEN
    WECHAT_TOKEN = token


def verify_url(msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
    """WeChat Work URL verification (GET request)"""
    token = WECHAT_TOKEN
    if not token:
        return echostr
    arr = sorted([token, timestamp, nonce])
    signature = hashlib.sha1("".join(arr).encode()).hexdigest()
    return echostr if signature == msg_signature else ""


def parse_message(xml_data: str) -> dict:
    """解析企业微信消息 XML → dict"""
    root = ET.fromstring(xml_data)
    return {child.tag: child.text or "" for child in root}


def handle_message(xml_data: str) -> str:
    """处理收到的消息，返回 XML 回复"""
    msg = parse_message(xml_data)
    from_user = msg.get("FromUserName", "unknown")
    to_user = msg.get("ToUserName", "")
    content = msg.get("Content", "").strip()
    msg_type = msg.get("MsgType", "text")

    if msg_type == "text" and content:
        system_uid = f"wx_{from_user}"
        try:
            if _agent is None:
                reply_text = "AGI 引擎尚未就绪，请稍后再试"
            else:
                result = _agent.process(content, user_id=system_uid)
                reply_text = result.get("response", str(result))
        except Exception as e:
            logger.error(f"处理消息失败: {e}")
            reply_text = f"抱歉，处理消息时出错了：{e}"
    elif msg_type == "event":
        reply_text = ""
    else:
        reply_text = "暂不支持非文本消息"

    if not reply_text:
        return ""

    timestamp = str(int(time.time()))
    return f"""<xml>
<ToUserName><![CDATA[{from_user}]]></ToUserName>
<FromUserName><![CDATA[{to_user}]]></FromUserName>
<CreateTime>{timestamp}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[{reply_text}]]></Content>
</xml>"""