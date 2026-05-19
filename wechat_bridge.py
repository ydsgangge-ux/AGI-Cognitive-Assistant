"""
企业微信机器人桥接模块 v1
与 server.py 共享同一个 agent 实例
用户ID映射规则：wx_{企业微信UserId}

使用方式：
  1. 企业微信后台 → 应用管理 → 创建应用 → 配置接收消息
  2. URL 填写：http://你的公网地址/wechat （需 frp / ngrok 映射到 18765 端口）
  3. Token 填写任意字符串，与下方 WECHAT_TOKEN 保持一致
"""

import hashlib
import xml.etree.ElementTree as ET
import time
import logging

logger = logging.getLogger("wechat")

# 共享的 agent 实例（由 server.py start_server 注入）
_agent = None

# 企业微信 Token（用户自行修改此值，与企业微信后台保持一致）
WECHAT_TOKEN = "myAGI123"


def init(agent):
    global _agent
    _agent = agent


def set_token(token: str):
    global WECHAT_TOKEN
    WECHAT_TOKEN = token


def verify_url(msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
    """企业微信 URL 验证（GET 请求）"""
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