from __future__ import annotations
import asyncio
import copy
from datetime import datetime
import os
from queue import Queue
import random
import traceback
from urllib.parse import urlparse
import httpx
import json
import logging

from util.logger import init_logger

record_path = os.path.join(os.path.dirname(__file__), "record.json")
live_record_dict = None
unknown_uid_set = set()
logger = init_logger()

def link_process(link: str) -> str:
    if len(link) == 0:
        return ""
    res = urlparse(link)
    return "https://" + res.netloc + res.path

def parse_live_user(user: dict) -> dict:
    res = {
        "uid": user.get("uid"),
        "name": user.get("uname"),
        "title": user.get("title"),
        "status": user.get("live_status"),
        "cover": user.get("cover_from_user"),
        "room_id": user.get("room_id")
    }
    if not res["uid"] is None:
        res["uid"] = str(res["uid"])
    if not res["room_id"] is None:
        res["room_id"] = str(res["room_id"])
    if not res["status"] is None:
        res["status"] = str(res["status"])
    if not res["cover"] is None:
        res["cover"] = link_process(res["cover"])
    for key in list(res.keys()):
        if not res[key]:
            del res[key]
    return res

def update_user(record: dict, typ: str, user: dict, msg_list: list):
    _user = copy.deepcopy(user)
    if not "user" in record:
        record["user"] = {}
    if "uid" in _user:
        del _user["uid"]
    if "room_id" in _user:
        del _user["room_id"]
    for key, value in record["user"].items():
        if key in _user and value != _user[key]:
            msg_list.append({
                "type": typ,
                "subtype": key,
                "user": user,
                "pre": value,
                "now": _user[key]
            })
            logger.info(f"{user['name']}的B站直播状态 {key}:{value} -> {_user[key]}")
        elif not key in _user:
            _user[key] = value
            user[key] = value
    record["user"] = _user

async def get_live(uid_list: list[str]):
    global live_record_dict
    live_list: list[dict] = []
    live_user_dict: dict = live_record_dict["user"]
    if(len(live_user_dict) == 0):
        return live_list
    params = {
        "uids": uid_list
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36"
    }
    async with httpx.AsyncClient() as client:
        res = await client.post(url="https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids", json=params, headers=headers)
    res = json.loads(res.content.decode(encoding="UTF-8"))
    if(res['code'] != 0):
        logger.error(f"B站直播状态请求返回值异常! code:{res['code']} msg:{res['message']}")
        return live_list
    status_dict = res['data']
    for live_uid in uid_list:
        if not live_uid in status_dict: # 结果中无对应UID
            if not live_uid in live_user_dict or not "user" in live_user_dict[live_uid]:
                continue
            else:
                user = copy.deepcopy(live_user_dict[live_uid]["user"])
                if user.get("status", "0") == "1":
                    user["status"] = "0" # 设置为下播
            if not live_uid in unknown_uid_set:
                unknown_uid_set.add(live_uid)
                logger.info(f"UID:{live_uid}的用户无法查询到直播信息, 可能是未开通直播或直播间被封禁, 状态设置为下播")
        else:
            user = parse_live_user(status_dict[live_uid])
            if live_uid in unknown_uid_set:
                unknown_uid_set.remove(live_uid)
                logger.info(f"UID:{live_uid}的用户直播信息查询恢复正常")
        update_user(live_user_dict[live_uid], "bili_live", user, live_list)
    save_live_record()
    return live_list

async def listen_live(live_config_dict: dict, msg_queue: Queue):
    global live_record_dict
    load_live_record()
    interval = live_config_dict["interval"]
    await asyncio.sleep(1)
    logger.info("开始抓取B站直播状态...")
    while(True):
        logger.debug("执行抓取B站直播状态")
        uid_list = list(live_record_dict["user"].keys())
        for i in range(0,len(uid_list),100):
            update_uid_list = uid_list[i:i+100:]
            try:
                live_list = await get_live(update_uid_list)
                logger.debug(f"获取的B站直播状态列表：{live_list}")
                if(live_list):
                    for live in live_list:
                        msg_queue.put(live)
            except:
                errmsg = traceback.format_exc()
                logger.error(f"B站直播状态抓取出错!\n{errmsg}")
            await asyncio.sleep(random.random()*15 + interval)
        await asyncio.sleep(5)

async def check_live_user(live_uid: str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36"
    }
    roomid = ""
    name = ""
    title = ""
    async with httpx.AsyncClient() as client:
        res = await client.post(url="https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids", json={"uids": [live_uid]}, headers=headers)
    status_dict = json.loads(res.text)
    if(status_dict['code'] != 0):
        raise Exception(f"查询用户{live_uid}直播间信息出错 code:{status_dict['code']} msg:{status_dict['message']}")
    else:
        data = status_dict['data'][live_uid]
        roomid = data['room_id']
        name = data['uname']
        title = data['title']
    return {
        "roomid": roomid,
        "name": name,
        "title": title
    }

async def add_live_user(live_uid: str, config_dict: dict):
    global live_record_dict
    resp = {"code": 0, "msg": "Success" }
    try:
        if(not live_uid in live_record_dict["user"]):
            room_detail = await check_live_user(live_uid)
            if room_detail['roomid'] == 0:
                resp = {"code": 11, "msg": "user has no live room"}
            live_record_dict["user"][live_uid] = dict()
            save_live_record()
    except:
        errmsg = traceback.format_exc()
        logger.error(f"添加B站直播用户发生错误！\n{errmsg}")
        resp = {"code": 10, "msg": "Add bilibili live user failed"}
    return resp

async def remove_live_user(live_uid: str, config_dict: dict):
    global live_record_dict
    resp = {"code": 0, "msg": "Success" }
    if(live_uid in live_record_dict["user"]):
        del live_record_dict["user"][live_uid]
        save_live_record()
    return resp

def load_live_record():
    global live_record_dict
    if(not live_record_dict is None):
        return
    try:
        with open(record_path, "r", encoding="UTF-8") as f:
            live_record_dict = json.loads(f.read())
    except FileNotFoundError:
        live_record_dict = {
            "user": dict()
        }
        save_live_record()
        logger.info(f"未找到B站直播记录文件，已自动创建")
    except:
        live_record_dict = {
            "user": dict()
        }
        logger.error(f"读取B站直播记录文件错误\n{traceback.format_exc()}")

def save_live_record():
    with open(record_path, "w", encoding="UTF-8") as f:
        f.write(json.dumps(live_record_dict))