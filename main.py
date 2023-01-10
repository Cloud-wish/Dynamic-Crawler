# -*- coding: utf-8 -*-

from __future__ import annotations
import configparser
import asyncio
import jsons
import threading
import queue
import os
import websockets
import requests
import traceback
import logging
from logging.handlers import TimedRotatingFileHandler
from aiohttp import web
from aiohttp.web_request import Request
from crawler.weibo.weibo import listen_weibo, add_wb_user, add_wb_cmt_user, remove_wb_user, remove_wb_cmt_user, listen_weibo_user_detail, listen_weibo_comment
from crawler.bili_live.bili_live import listen_live, add_live_user, remove_live_user
from crawler.bili_dynamic.bili_dynamic import listen_dynamic, add_dyn_user, add_dyn_cmt_user, remove_dyn_user, remove_dyn_cmt_user, listen_bili_user_detail, listen_dynamic_comment

routes = web.RouteTableDef()
msg_queue = queue.Queue(maxsize=-1) # infinity length
config_dict = dict()
push_config_dict = dict()
ws_conn_dict = dict()
ws_server = None
logger: logging.Logger = None
LOGGER_NAME = "crawler"
LOGGER_PRINT_FORMAT = "\033[1;33m%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)s) %(funcName)s:\033[0m\n%(message)s"
LOGGER_FILE_FORMAT = "%(asctime)s [%(levelname)s] (%(filename)s:%(lineno)s) %(funcName)s:\n%(message)s"
logging.basicConfig(format=LOGGER_PRINT_FORMAT)
log_path = os.path.join(os.path.dirname(__file__), "logs", f"{LOGGER_NAME}.log")

def init_logger():
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    global logger
    logger = logging.getLogger(LOGGER_NAME)
    handler = TimedRotatingFileHandler(log_path, when="midnight", interval=1, encoding="UTF-8")
    handler.setFormatter(logging.Formatter(LOGGER_FILE_FORMAT))
    # 从配置文件接收是否打印debug日志
    if config_dict["logger"]["debug"]:
        logger.setLevel(level=logging.DEBUG)
        handler.level = logging.DEBUG
    else:
        logger.setLevel(logging.INFO)
        handler.level = logging.INFO
    logger.addHandler(handler)

def cookie_to_dict(cookie: str):
    cookie_list = cookie.split(";")
    cookie_dict = dict()
    for c in cookie_list:
        cookie_pair = c.lstrip().rstrip().split("=")
        cookie_dict[cookie_pair[0]] = cookie_pair[1]
    return cookie_dict

def load_config():
    cf = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=["#"], comment_prefixes=["#"])
    cf.read(f"config.ini", encoding="UTF-8")
    global config_dict, push_config_dict
    for name, section in cf.items():
        config_dict[name] = dict()
        for key, value in section.items():
            try:
                value = int(value)
            except:
                pass
            if(value == "true"):
                value = True
            elif(value == "false"):
                value = False
            config_dict[name][key] = value
    if(config_dict["bili_dyn"]["enable"]):
        cookie_dict = cookie_to_dict(config_dict["bili_dyn"]["cookie"])
        config_dict["bili_dyn"]["bili_jct"] = cookie_dict["bili_jct"]
        config_dict["bili_dyn"]["buvid3"] = cookie_dict["buvid3"]
        config_dict["bili_dyn"]["sessdata"] = cookie_dict["SESSDATA"]
        config_dict["bili_dyn"]["dedeuserid"] = cookie_dict["DedeUserID"]
    try:
        with open("push_config.json", "r", encoding="UTF-8") as f:
            push_config_dict = jsons.loads(f.read())
            for typ in push_config_dict.keys():
                if(typ == "clients"):
                    continue
                if(type(push_config_dict[typ]) == dict):
                    for x in push_config_dict[typ].keys():
                        if(type(push_config_dict[typ][x]) == dict):
                            for uid in push_config_dict[typ][x].keys():
                                push_config_dict[typ][x][uid] = set(push_config_dict[typ][x][uid])
                        else:
                            push_config_dict[typ][x] = set(push_config_dict[typ][x])
                else:
                    push_config_dict[typ] = set(push_config_dict[typ])
        # print(push_config_dict)
    except:
        pass

def save_push_config():
    with open("push_config.json", "w", encoding="UTF-8") as f:
        f.write(jsons.dumps(push_config_dict))

async def check_params(req: Request, required_params: list[str]):
    if(type(req) != dict):
        try:
            logger.debug(f"接收到的参数：{await req.text()}")
            params = await req.json()
        except:
            return (None, {"code": -1 ,"msg": "Invaild JSON Parameter"})
    else:
        params = req
    logger.debug(f"解析后的参数：{type(params)} {params}")
    if(type(params) != dict):
        return (None, {"code": -1 ,"msg": "Invaild JSON Parameter"})
    for param in required_params:
        if not param in params:
            return (None, {"code": 1 ,"msg": "Missing Parameter {"+param+"}"})
        params[param] = str(params[param])
    return (params, {"code": 0, "msg": "Success" })

@routes.post("/init")
async def init(req):
    required_params = ("client_name", "url")
    params, resp = await check_params(req, required_params)
    if(not params is None):
        client_name: str = params["client_name"]
        url: str = params["url"]
        if not "clients" in push_config_dict:
            push_config_dict["clients"] = dict()
        push_config_dict["clients"][client_name] = url
        save_push_config()
        logger.debug(f"HTTP服务收到init命令\nparams:{jsons.dumps(params, ensure_ascii=False)}\nresp:{jsons.dumps(resp, ensure_ascii=False)}")
    return web.json_response(resp)

@routes.post("/add")
async def add(req):
    required_params = ("type", "uid", "client_name")
    params, resp = await check_params(req, required_params)
    if(not params is None):
        typ: str = params["type"]
        uid: str = params["uid"]
        subtype: str = params.get("subtype", None)
        is_top: bool = params.get("is_top", False)
        if subtype and subtype == typ:
            subtype = None
        client_name: str = params["client_name"]
        if not uid.isdigit():
            resp = {"code": 2, "msg": "Invalid UID"}
        elif not typ in config_dict:
            resp = {"code": 3, "msg": "Invalid type"}
        elif not config_dict[typ]["enable"]:
            resp = {"code": 4, "msg": "This type of crawler is not enabled"}
        elif subtype and not config_dict[typ].get(f"{subtype}_enable", False):
            resp = {"code": 5, "msg": "This subtype of crawler is not enabled"}
        elif not "clients" in push_config_dict or not client_name in push_config_dict["clients"]:
            resp = {"code": 6, "msg": "Client is not initialized"}
        else:
            if not typ in push_config_dict:
                push_config_dict[typ] = dict()
            if subtype:
                if not uid in push_config_dict[typ]:
                    resp = {"code": 15, "msg": "The user is not in the crawler list before"}
                else:
                    if not subtype in push_config_dict[typ]:
                        push_config_dict[typ][subtype] = dict()
                    if not uid in push_config_dict[typ][subtype]:
                        if(typ == "weibo"):
                            if(subtype == "comment"):
                                resp = await add_wb_cmt_user(uid, config_dict[typ])
                        elif(typ == "bili_dyn"):
                            if(subtype == "comment"):
                                resp = await add_dyn_cmt_user(uid, config_dict[typ], is_top)
                        if(resp['code'] == 0):
                            push_config_dict[typ][subtype][uid] = set()
                            push_config_dict[typ][subtype][uid].add(client_name)
                    else:
                        push_config_dict[typ][subtype][uid].add(client_name)
            elif not uid in push_config_dict[typ]:
                if(typ == "weibo"):
                    resp = await add_wb_user(uid, config_dict[typ])
                elif(typ == "bili_dyn"):
                    resp = await add_dyn_user(uid, config_dict[typ])
                elif(typ == "bili_live"):
                    resp = await add_live_user(uid, config_dict[typ])
                if(resp['code'] == 0):
                    push_config_dict[typ][uid] = set()
                    push_config_dict[typ][uid].add(client_name)
            else:
                push_config_dict[typ][uid].add(client_name)
            save_push_config()
        logger.debug(f"HTTP服务收到add命令\nparams:{jsons.dumps(params, ensure_ascii=False)}\nresp:{jsons.dumps(resp, ensure_ascii=False)}")
    return web.json_response(resp)

@routes.post("/remove")
async def remove(req):
    required_params = ("type", "uid", "client_name")
    params, resp = await check_params(req, required_params)
    if(not params is None):
        typ: str = params["type"]
        uid: str = params["uid"]
        client_name: str = params["client_name"]
        subtype: str = params.get("subtype", None)
        if subtype and subtype == typ:
            subtype = None
        if not uid.isdigit():
            resp = {"code": 2, "msg": "Invalid UID"}
        elif not typ in config_dict:
            resp = {"code": 3, "msg": "Invalid type"}
        elif not config_dict[typ]["enable"]:
            resp = {"code": 4, "msg": "This type of crawler is not enabled"}
        elif subtype and not config_dict[typ].get(f"{subtype}_enable", False):
            resp = {"code": 5, "msg": "This subtype of crawler is not enabled"}
        elif not "clients" in push_config_dict or not client_name in push_config_dict["clients"]:
            resp = {"code": 6, "msg": "Client is not initialized"}
        elif not typ in push_config_dict or not uid in push_config_dict[typ]:
            resp = {"code": 7, "msg": "Invalid user"}
        elif subtype and (not subtype in push_config_dict[typ] or not uid in push_config_dict[typ][subtype]):
            resp = {"code": 7, "msg": "Invalid user"}
        elif subtype and client_name in push_config_dict[typ][subtype][uid]:
            if(len(push_config_dict[typ][subtype][uid]) == 1):
                if(typ == "weibo"):
                    if(subtype == "comment"):
                        resp = await remove_wb_cmt_user(uid, config_dict[typ])
                elif(typ == "bili_dyn"):
                    if(subtype == "comment"):
                        resp = await remove_dyn_cmt_user(uid, config_dict[typ])
                if(resp['code'] == 0):
                    del push_config_dict[typ][subtype][uid]
            else:
                push_config_dict[typ][subtype][uid].remove(client_name)
        elif client_name in push_config_dict[typ][uid]:
            if(len(push_config_dict[typ][uid]) == 1):
                if(typ == "weibo"):
                    resp = await remove_wb_user(uid, config_dict[typ])
                elif(typ == "bili_dyn"):
                    resp = await remove_dyn_user(uid, config_dict[typ])
                elif(typ == "bili_live"):
                    resp = await remove_live_user(uid, config_dict[typ])
                if(resp['code'] == 0):
                    del push_config_dict[typ][uid]
            else:
                push_config_dict[typ][uid].remove(client_name)
            save_push_config()
        logger.debug(f"HTTP服务收到remove命令\nparams:{jsons.dumps(params, ensure_ascii=False)}\nresp:{jsons.dumps(resp, ensure_ascii=False)}")
    return web.json_response(resp)

def send_msg(client_name: str, msg: dict):
    http_url = push_config_dict["clients"][client_name]
    ws_conn = ws_conn_dict.get(client_name, None)
    if(ws_conn is None and http_url == "websocket"):
        logger.error(f"client_name:{client_name} 未连接Websocket服务，无法推送消息！\n消息内容：\n{jsons.dumps(msg, ensure_ascii=False)}")
        return
    if(not ws_conn is None):
        try:
            asyncio.get_event_loop().run_until_complete(ws_conn.send(jsons.dumps(msg)))
            return
        except:
            errmsg = traceback.format_exc()
            logger.error(f"Websocket消息推送发生错误！\nclient_name:{client_name}\n{errmsg}")
    if(not http_url == "websocket"):
        try:
            resp = requests.post(url=http_url, json=msg)
        except:
            errmsg = traceback.format_exc()
            logger.error(f"HTTP消息推送发生错误！\nclient_name:{client_name} url:{http_url}\n{errmsg}")

def msg_sender():
    asyncio.set_event_loop(asyncio.new_event_loop())
    while True:
        msg = msg_queue.get(block = True, timeout = None)
        msg_queue.task_done()
        msg_type = msg["type"]
        subtype = msg["subtype"]
        uid = msg["user"]["uid"]
        logger.debug(f"消息推送线程接收到消息：\n{jsons.dumps(msg, ensure_ascii=False)}")
        if not subtype in push_config_dict[msg_type]:
            for client_name in push_config_dict[msg_type][uid]:
                send_msg(client_name, msg)
        else:
            for client_name in push_config_dict[msg_type][subtype][uid]:
                send_msg(client_name, msg)

async def receiver(websocket):
    global ws_conn_dict
    client_name = None
    try:
        async for message in websocket:
            data = jsons.loads(message)
            params, resp = await check_params(data, ("client_name", "type"))
            if(not params is None):
                _client_name = params["client_name"]
                cmd_type = params["type"]
                if(cmd_type == "init"):
                    if(not client_name is None):
                        resp = {"code": 11, "msg": "Duplicate websocket initialization"}
                    elif(_client_name in ws_conn_dict and ws_conn_dict[_client_name].open):
                        resp = {"code": 12, "msg": "Client name already exists"}
                    else:
                        client_name = _client_name
                        ws_conn_dict[client_name] = websocket
                        if not "clients" in push_config_dict:
                            push_config_dict["clients"] = dict()
                        push_config_dict["clients"][client_name] = "websocket"
                        save_push_config()
                elif(cmd_type == "exit"):
                    if(client_name is None):
                        resp = {"code": 13, "msg": "Not initialized"}
                    else:
                        del ws_conn_dict[client_name]
                        await websocket.close()
                        return
                else:
                    resp = {"code": 14, "msg": "Illegal command type"}
                logger.debug(f"Websocket服务收到{cmd_type}命令\nparams:{jsons.dumps(params, ensure_ascii=False)}\nresp:{jsons.dumps(resp, ensure_ascii=False)}")
            await websocket.send(jsons.dumps(resp))
    except websockets.exceptions.ConnectionClosedOK:
        logger.debug(f"client_name:{client_name}的Websocket连接正常关闭")
    except websockets.exceptions.ConnectionClosedError as e:
        logger.error(f"client_name:{client_name}的Websocket连接异常关闭！\n错误详情：{str(e)}")
    except:
        errmsg = traceback.format_exc()
        logger.error(f"client_name:{client_name}的Websocket连接发生错误！\n错误详情：{errmsg}")
    finally:
        if(not client_name is None):
            del ws_conn_dict[client_name]

async def start_tasks(app):
    if(config_dict["bili_live"]["enable"]):
        app["bili_live_listener"] = asyncio.create_task(listen_live(config_dict["bili_live"], msg_queue))
    if(config_dict["bili_dyn"]["enable"]):
        app["bili_dyn_listener"] = asyncio.create_task(listen_dynamic(config_dict["bili_dyn"], msg_queue))
        if(config_dict["bili_dyn"]["detail_enable"]):
            app["bili_dyn_detail_listener"] = asyncio.create_task(listen_bili_user_detail(config_dict["bili_dyn"], msg_queue))
        if(config_dict["bili_dyn"]["comment_enable"]):
            app["bili_dyn_comment_listener"] = asyncio.create_task(listen_dynamic_comment(config_dict["bili_dyn"], msg_queue))
    if(config_dict["weibo"]["enable"]):
        app["weibo_listener"] = asyncio.create_task(listen_weibo(config_dict["weibo"], msg_queue))
        if(config_dict["weibo"]["detail_enable"]):
            app["weibo_detail_listener"] = asyncio.create_task(listen_weibo_user_detail(config_dict["weibo"], msg_queue))
        if(config_dict["weibo"]["comment_enable"]):
            app["weibo_comment_listener"] = asyncio.create_task(listen_weibo_comment(config_dict["weibo"], msg_queue))
    if(config_dict["websocket"]["enable"]):
        global ws_server
        ws_server = await websockets.serve(receiver, config_dict["websocket"]["host"], config_dict["websocket"]["port"])
        logger.info("Websocket服务已开启")

async def cleanup_tasks(app):
    global ws_server
    if(not ws_server is None):
        ws_server.close()
        logger.info("Websocket服务已关闭")

def main():
    load_config()
    init_logger()

    sender = threading.Thread(target = msg_sender)
    sender.start()

    app = web.Application()
    app.add_routes(routes)
    app.on_startup.append(start_tasks)
    app.on_cleanup.append(cleanup_tasks)
    web.run_app(app, host=config_dict["server"]["host"], port=config_dict["server"]["port"])

if __name__ == "__main__":
    main()
