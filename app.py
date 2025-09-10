from flask import Flask, request, jsonify, Response
import requests
import json
import time
import uuid
import os
import logging
from logging.handlers import RotatingFileHandler
import threading
import hashlib

app = Flask(__name__)

# 从环境变量获取配置
KIRARA_BASE_URL = os.getenv('KIRARA_BASE_URL', 'http://kirara-agent:8080')
KIRARA_API_KEY = os.getenv('KIRARA_API_KEY', 'Why343949')

# 会话存储 - 存储每个会话的kirara session_id
kirara_session_store = {}
session_lock = threading.Lock()

# 会话过期时间（秒）
SESSION_TIMEOUT = 3600  # 1小时

# 配置日志
def setup_logging():
    if not app.debug:
        if not os.path.exists('logs'):
            os.mkdir('logs')
        file_handler = RotatingFileHandler('logs/kirara-proxy.log', maxBytes=10240000, backupCount=10)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
        ))
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
        app.logger.setLevel(logging.INFO)
        app.logger.info('Kirara Proxy startup')

def generate_conversation_hash(messages):
    """
    基于对话历史生成一致的hash，用于识别同一个对话
    """
    try:
        # 只使用user和assistant的消息，忽略system消息中的时间戳等变化内容
        conversation_messages = []
        for msg in messages[:-1]:  # 排除最后一条消息（当前消息）
            if msg.get('role') in ['user', 'assistant']:
                conversation_messages.append({
                    'role': msg['role'],
                    'content': msg['content']
                })
        
        if not conversation_messages:
            return None
        
        # 生成hash
        conversation_str = json.dumps(conversation_messages, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(conversation_str.encode('utf-8')).hexdigest()[:16]
    except Exception as e:
        app.logger.error(f"Error generating conversation hash: {e}")
        return None

def get_or_create_kirara_session(messages):
    """
    获取或创建kirara会话ID
    """
    try:
        conversation_hash = generate_conversation_hash(messages)
        
        with session_lock:
            if conversation_hash and conversation_hash in kirara_session_store:
                session_info = kirara_session_store[conversation_hash]
                # 检查会话是否过期
                if time.time() - session_info['last_used'] < SESSION_TIMEOUT:
                    session_info['last_used'] = time.time()
                    app.logger.info(f"Reusing kirara session: {session_info['kirara_session_id']}")
                    return session_info['kirara_session_id']
                else:
                    # 会话过期，删除
                    del kirara_session_store[conversation_hash]
            
            # 创建新的kirara会话ID
            new_kirara_session = f"cherry-{uuid.uuid4().hex[:8]}"
            
            if conversation_hash:
                kirara_session_store[conversation_hash] = {
                    'kirara_session_id': new_kirara_session,
                    'created': time.time(),
                    'last_used': time.time()
                }
            
            app.logger.info(f"Created new kirara session: {new_kirara_session}")
            return new_kirara_session
    except Exception as e:
        app.logger.error(f"Error in get_or_create_kirara_session: {e}")
        return f"cherry-{uuid.uuid4().hex[:8]}"

def build_context_message(messages):
    """
    将OpenAI格式的消息转换为包含完整上下文的单个消息
    """
    try:
        if len(messages) <= 1:
            return messages[-1]['content']
        
        # 构建完整的对话上下文
        context_parts = []
        
        # 处理历史消息
        for i, msg in enumerate(messages[:-1]):  # 排除最后一条消息
            role = msg.get('role', '')
            content = msg.get('content', '')
            
            if role == 'system':
                # 系统消息作为背景信息
                if not content.startswith('session_id:'):  # 忽略我们添加的session_id标记
                    context_parts.append(f"[系统]: {content}")
            elif role == 'user':
                context_parts.append(f"用户: {content}")
            elif role == 'assistant':
                context_parts.append(f"助手: {content}")
        
        # 添加当前用户消息
        current_message = messages[-1]['content']
        
        if context_parts:
            # 如果有历史对话，构建完整上下文
            full_context = "\n".join(context_parts) + f"\n用户: {current_message}"
            context_message = f"[对话历史]\n{full_context}\n\n[指令]\n请基于以上对话历史，回答用户的最新问题。保持对话的连贯性。"
            return context_message
        else:
            # 如果没有历史对话，直接返回当前消息
            return current_message
    except Exception as e:
        app.logger.error(f"Error building context message: {e}")
        return messages[-1]['content'] if messages else "Empty message"

def clean_expired_sessions():
    """清理过期的会话"""
    try:
        current_time = time.time()
        with session_lock:
            expired_sessions = []
            for conv_hash, session_info in kirara_session_store.items():
                if current_time - session_info['last_used'] > SESSION_TIMEOUT:
                    expired_sessions.append(conv_hash)
            
            for conv_hash in expired_sessions:
                session_info = kirara_session_store[conv_hash]
                app.logger.info(f"Cleaned expired session: {session_info['kirara_session_id']}")
                del kirara_session_store[conv_hash]
    except Exception as e:
        app.logger.error(f"Error cleaning expired sessions: {e}")

@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    try:
        # 清理过期会话
        clean_expired_sessions()
        
        # 获取OpenAI格式的请求
        openai_request = request.json
        if not openai_request:
            raise ValueError("Invalid JSON request")
            
        app.logger.info(f"Received request from {request.remote_addr}")
        
        messages = openai_request.get('messages', [])
        if not messages:
            raise ValueError("No messages provided")
        
        # 记录收到的消息数量
        app.logger.info(f"Received {len(messages)} messages")
        
        # 获取或创建kirara会话ID
        kirara_session_id = get_or_create_kirara_session(messages)
        
        # 构建包含完整上下文的消息
        context_message = build_context_message(messages)
        
        # 转换为kirara格式
        kirara_request = {
            "session_id": kirara_session_id,
            "username": "cherry_user",
            "message": context_message
        }
        
        app.logger.info(f"Sending to kirara with session: {kirara_session_id}")
        
        # 发送到kirara-ai
        response = requests.post(
            f"{KIRARA_BASE_URL}/v1/chat",
            headers={
                "Authorization": f"Bearer {KIRARA_API_KEY}",
                "Content-Type": "application/json"
            },
            json=kirara_request,
            verify=False,
            timeout=30
        )
        
        if response.status_code != 200:
            raise Exception(f"Kirara API returned status {response.status_code}: {response.text}")
        
        kirara_response = response.json()
        app.logger.info(f"Kirara response status: {kirara_response.get('result')}")
        
        # 获取回复内容并处理分段
        message_segments = []
        if kirara_response.get('result') == 'SUCCESS':
            messages_response = kirara_response.get('message', [])
            for msg in messages_response:
                # 将每个消息按换行符进一步分割
                lines = str(msg).split('\n')
                for line in lines:
                    line = line.strip()
                    if line:  # 只添加非空行
                        message_segments.append(line)
        
        if not message_segments:
            message_segments = ["Empty response from kirara-ai"]
        
        app.logger.info(f"Processed {len(message_segments)} segments")
        
        # 检查是否是流式请求
        if openai_request.get('stream', False):
            # 流式响应 - 逐段发送
            def generate():
                try:
                    chat_id = f"chatcmpl-{uuid.uuid4().hex[:29]}"
                    created_time = int(time.time())
                    model_name = openai_request.get('model', 'kirara-default')
                    
                    # 发送第一个chunk（包含role）
                    first_chunk = {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": model_name,
                        "choices": [{
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "content": ""
                            },
                            "finish_reason": None
                        }]
                    }
                    yield f"data: {json.dumps(first_chunk, ensure_ascii=False)}\n\n"
                    
                    # 发送每个片段
                    for segment in message_segments:
                        chunk = {
                            "id": chat_id,
                            "object": "chat.completion.chunk",
                            "created": created_time,
                            "model": model_name,
                            "choices": [{
                                "index": 0,
                                "delta": {
                                    "content": segment + "\n\n"
                                },
                                "finish_reason": None
                            }]
                        }
                        
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                        time.sleep(0.8)
                    
                    # 发送结束chunk
                    end_chunk = {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": model_name,
                        "choices": [{
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop"
                        }]
                    }
                    yield f"data: {json.dumps(end_chunk, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                except Exception as e:
                    app.logger.error(f"Error in stream generation: {e}")
                    yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
            
            return Response(generate(), mimetype='text/event-stream', headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': '*',
                'Access-Control-Allow-Methods': '*',
                'X-Accel-Buffering': 'no'
            })
        
        # 非流式响应
        full_content = "\n\n".join(message_segments)
        
        openai_response = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:29]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": openai_request.get('model', 'kirara-default'),
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": full_content
                },
                "logprobs": None,
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": len(context_message),
                "completion_tokens": len(full_content),
                "total_tokens": len(context_message) + len(full_content)
            }
        }
        
        response = jsonify(openai_response)
        response.headers.update({
            'Content-Type': 'application/json; charset=utf-8',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': '*',
            'Access-Control-Allow-Methods': '*'
        })
        return response
        
    except Exception as e:
        app.logger.error(f"Error in chat_completions: {e}", exc_info=True)
        
        error_response = {
            "error": {
                "message": str(e),
                "type": "internal_error",
                "code": "internal_error"
            }
        }
        return jsonify(error_response), 500

@app.route('/v1/models', methods=['GET'])
def list_models():
    try:
        models_response = {
            "object": "list",
            "data": [
                {
                    "id": "kirara-default",
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "kirara-ai"
                }
            ]
        }
        response = jsonify(models_response)
        response.headers.update({
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': '*',
            'Access-Control-Allow-Methods': '*'
        })
        return response
    except Exception as e:
        app.logger.error(f"Error in list_models: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/v1/sessions', methods=['GET'])
def list_sessions():
    """列出所有活跃会话"""
    try:
        with session_lock:
            sessions = {}
            for conv_hash, session_info in kirara_session_store.items():
                sessions[conv_hash] = {
                    "kirara_session_id": session_info['kirara_session_id'],
                    "created": session_info['created'],
                    "last_used": session_info['last_used']
                }
        return jsonify(sessions)
    except Exception as e:
        app.logger.error(f"Error in list_sessions: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/v1/sessions/clear', methods=['POST'])
def clear_all_sessions():
    """清除所有会话"""
    try:
        with session_lock:
            count = len(kirara_session_store)
            kirara_session_store.clear()
            app.logger.info(f"Cleared {count} sessions")
        return jsonify({"message": f"Cleared {count} sessions"})
    except Exception as e:
        app.logger.error(f"Error in clear_all_sessions: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    try:
        return jsonify({
            "status": "healthy", 
            "timestamp": int(time.time()),
            "active_sessions": len(kirara_session_store)
        })
    except Exception as e:
        app.logger.error(f"Error in health_check: {e}")
        return jsonify({"error": str(e)}), 500

@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        response = Response()
        response.headers.add("Access-Control-Allow-Origin", "*")
        response.headers.add('Access-Control-Allow-Headers', "*")
        response.headers.add('Access-Control-Allow-Methods', "*")
        return response

# 初始化日志
setup_logging()

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8081))
    app.run(host='0.0.0.0', port=port, debug=False)
