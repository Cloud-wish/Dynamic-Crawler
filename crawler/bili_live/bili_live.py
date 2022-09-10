from __future__ import annotations
import asyncio
from datetime import datetime
import os
from queue import Queue
import random
import traceback
import httpx
import json
import logging

record_path = os.path.join(os.path.dirname(__file__), "record.json")
live_record_dict = None
logger = logging.getLogger("crawler")

async def get_live():
    global live_record_dict
    live_list: list[dict] = []
    live_user_dict: dict = live_record_dict["user"]
    if(len(live_user_dict) == 0):
        return live_list
    params = {
        "uids": list(live_user_dict.keys())
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36"
    }
    async with httpx.AsyncClient() as client:
        res = await client.post(url="https://api.live.bilibili.com/room/v1/Room/get_status_info_by_uids", json=params, headers=headers)
    res = json.loads(res.text)
    if(res['code'] != 0):
        logger.error(f"B站直播状态请求返回值异常! code:{res['code']} msg:{res['message']}")
        return live_list
    status_dict = res['data']
    for live_uid in live_user_dict.keys():
        if(not live_uid in status_dict):
            logger.info(f"UID:{live_uid}的用户查不到直播间信息！")
            continue
        now_live_status = str(status_dict[live_uid]['live_status'])
        live_title = status_dict[live_uid]['title']
        room_id = str(status_dict[live_uid]['room_id'])
        liver_name = status_dict[live_uid]['uname']
        if(not "status" in live_user_dict[live_uid]):
            live_user_dict[live_uid]["status"] = now_live_status
            live_user_dict[live_uid]["title"] = live_title
            live_user_dict[live_uid]["name"] = liver_name
        else:
            if(live_user_dict[live_uid]["title"] != live_title):
                live_list.append({
                    "type": "bili_live",
                    "subtype": "title",
                    "pre": live_user_dict[live_uid]["title"],
                    "now": live_title,
                    "name": liver_name,
                    "uid": live_uid
                })
                live_user_dict[live_uid]["title"] = live_title
            if(live_user_dict[live_uid]["status"] != now_live_status):
                live_list.append({
                    "type": "bili_live",
                    "subtype": "status",
                    "pre": live_user_dict[live_uid]["status"],
                    "now": now_live_status,
                    "name": liver_name,
                    "title": live_title,
                    "uid": live_uid,
                    "room_id": room_id
                })
                live_user_dict[live_uid]["status"] = now_live_status
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
        try:
            live_list = await get_live()
            logger.debug(f"获取的B站直播状态列表：{live_list}")
            if(live_list):
                for live in live_list:
                    msg_queue.put(live)
        except:
            errmsg = traceback.format_exc()
            logger.error(f"B站直播状态抓取出错!\n{errmsg}")
        await asyncio.sleep(random.random()*15 + interval)

async def add_live_user(live_uid: str, config_dict: dict):
    global live_record_dict
    resp = {"code": 0, "msg": "Success" }
    try:
        if(not live_uid in live_record_dict["user"]):
            live_record_dict["user"][live_uid] = dict()
            save_live_record()
    except:
        errmsg = traceback.format_exc()
        logger.error(f"添加B站直播用户发生错误！\n{errmsg}")
        resp = {"code": 9, "msg": "Add weibo user failed"}
    return resp

async def remove_live_user(live_uid: str):
    global live_record_dict
    if(live_uid in live_record_dict["user"]):
        del live_record_dict["user"][live_uid]
        save_live_record()
    return {"code": 0, "msg": "Success"}

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