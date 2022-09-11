from __future__ import annotations
import asyncio
from datetime import datetime
import os
import logging
from queue import Queue
import random
import traceback
import httpx
import json
from bilibili_api.user import User, RelationType
from bilibili_api.utils.Credential import Credential
from bilibili_api.exceptions.ResponseCodeException import ResponseCodeException

record_path = os.path.join(os.path.dirname(__file__), "record.json")
dyn_record_dict = None
logger = logging.getLogger("crawler")

def link_to_https(link: str) -> str:
    if(not link.startswith("https://")):
        if(link.startswith("http://")):
            link = "https" + link[4::]
    return link

def update_user(uid: str, user_dict: dict, msg_list: list, msg_type: str, subtype: str, now: str) -> bool:
    if(not subtype in user_dict[uid]):
        user_dict[uid][subtype] = now
    elif(user_dict[uid][subtype] != now):
        msg_list.append({
            "type": msg_type,
            "subtype": subtype,
            "uid": uid,
            "name": user_dict[uid]["name"],
            "pre": user_dict[uid][subtype],
            "now": now
        })
        user_dict[uid][subtype] = now
        return True
    return False

async def parse_bili_dyn_content(dyn_typ: int, content: dict) -> dict:
    res = dict()
    if dyn_typ == 2:  # 带图片动态
        dyn_text = content['item']['description']
        pic_list = content['item']['pictures']
        pic_src_list = list()
        for pic in pic_list:
            pic_src_list.append(pic['img_src'])
        res = {
            "text": dyn_text,
            "pics": pic_src_list
        }
    elif dyn_typ == 4:  # 纯文字动态
        dyn_text = content['item']['content']
        res = {
            "text": dyn_text,
        }
    elif dyn_typ == 64:  # 文章
        cvid = str(content['id'])
        title = content['title']
        summary = content['summary']
        cover_pic = content['image_urls'][0]
        res = {
            "id": cvid,
            "title": title,
            "desc": summary,
            "cover_pic": cover_pic,
            "link_prefix": "https://www.bilibili.com/read/cv"
        }
    elif dyn_typ == 8:  # 投稿视频
        title = content['title']
        cover_pic = content['pic']
        video_desc = content['desc']
        res = {
            "title": title,
            "desc": video_desc,
            "cover_pic": cover_pic,
            "link_prefix": "https://www.bilibili.com/video/"
        }
    elif dyn_typ == 1:  # 转发动态
        dyn_text = content['item']['content']
        orig_typ = content['item']['orig_type']
        if(orig_typ in [1,2,4,8,64]):
            orig_content = json.loads(content['origin'])
            orig_uname = content['origin_user']['info']['uname']
            res = {
                "text": dyn_text,
                "retweet": await parse_bili_dyn_content(orig_typ, orig_content)
            }
            res["retweet"]["name"] = orig_uname
            res["retweet"]["dyn_type"] = orig_typ
            res["retweet"]["is_retweet"] = True
        else:
            res = {
                "text": dyn_text,
                "retweet": {}
            }
            res["retweet"]["name"] = "[未知用户名]"
            res["retweet"]["dyn_type"] = orig_typ
            res["retweet"]["is_retweet"] = True
    return res

async def parse_bili_dyn(card: dict) -> dict:
    uid = str(card['desc']['user_profile']['info']['uid'])
    uname = card['desc']['user_profile']['info']['uname']
    avatar = card['desc']['user_profile']['info']['face']
    created_time = card['desc']['timestamp']
    dyn_id = str(card['desc']['dynamic_id'])
    dyn_typ = card['desc']['type']
    res = {
        "type": "bili_dyn",
        "subtype": "dynamic",
        "dyn_type": dyn_typ,
        "uid": uid,
        "name": uname,
        "avatar": avatar,
        "id": dyn_id,
        "link_prefix": "https://t.bilibili.com/",
        "created_time": created_time
    }
    parse_res = await parse_bili_dyn_content(dyn_typ, json.loads(card['card']))
    for key, value in parse_res.items():
        res[key] = value
    if dyn_typ == 8:
        res["id"] = card['desc']['bvid']
    elif dyn_typ == 1:
        res["retweet"]["created_time"] = card["desc"]["origin"]["timestamp"]
    return res

async def get_dynamic(bili_ua: str, bili_cookie: str, detail_enable: bool):
    global dyn_record_dict
    dyn_list: list[dict] = []
    dyn_user_dict: dict = dyn_record_dict["user"]
    if(len(dyn_user_dict) == 0):
        return dyn_list
    headers = {
        "User-Agent": bili_ua,
        "Cookie": bili_cookie
    }
    async with httpx.AsyncClient() as client:
        res = await client.get('https://api.live.bilibili.com/dynamic_svr/v1/dynamic_svr/dynamic_new?type_list=268435455', headers=headers, timeout=20)
    res.encoding='utf-8'
    res = res.text
    try:
        cards_data = json.loads(res)
    except json.JSONDecodeError:
        logger.error(f"B站动态解析出错!返回值如下:\n{res}")
        return dyn_list
    if(cards_data['code'] != 0):
        if(cards_data['code'] == -6):
            logger.error(f"B站Cookie失效，请重新获取！")
        else:
            logger.error(f"B站动态请求返回值异常! code:{cards_data['code']} msg:{cards_data['message']}\nraw:{json.dumps(cards_data, ensure_ascii=False)}")
        return dyn_list
    cards_data = cards_data['data']['cards']
    now_dyn_time_dict = dict()
    for dyn_uid in dyn_user_dict:
        now_dyn_time_dict[dyn_uid] = dyn_user_dict[dyn_uid]["last_dyn_time"]
    for card in cards_data:
        uid = str(card['desc']['uid'])
        user_name = card['desc']['user_profile']['info']['uname']
        user_desc = card['desc']['user_profile']['sign']
        user_avatar = card['desc']['user_profile']['info']['face']
        user_avatar = link_to_https(user_avatar) # 转换，B站有时抽风
        created_time = int(card['desc']['timestamp'])
        if (not uid in dyn_user_dict): # 不是推送的人
            continue
        # 更新信息
        if(detail_enable):
            update_user(uid, dyn_user_dict, dyn_list, "bili_dyn", "avatar", user_avatar)
            update_user(uid, dyn_user_dict, dyn_list, "bili_dyn", "desc", user_desc)
            update_user(uid, dyn_user_dict, dyn_list, "bili_dyn", "name", user_name)
            dyn_user_dict[uid]["update_time"] = int(datetime.now().timestamp())
        if (not dyn_user_dict[uid]["last_dyn_time"] < created_time):# 不是新动态
            continue
        # 以下是处理新动态的内容
        if now_dyn_time_dict[uid] < created_time:
            now_dyn_time_dict[uid] = created_time
        dyn = await parse_bili_dyn(card)
        dyn_list.append(dyn)
    for dyn_uid in now_dyn_time_dict.keys():
        dyn_user_dict[dyn_uid]["last_dyn_time"] = now_dyn_time_dict[dyn_uid]
    save_dyn_record()
    dyn_list.reverse() # 按时间从前往后排序
    return dyn_list

async def get_bili_users_detail(bili_ua: str, bili_cookie: str, uid_list: list[str]):
    global dyn_record_dict
    msg_list: list[dict] = []
    dyn_user_dict: dict = dyn_record_dict["user"]
    if(len(dyn_user_dict) == 0):
        return msg_list
    headers = {
        "User-Agent": bili_ua,
    }
    async with httpx.AsyncClient() as client:
        res = await client.get(f'https://api.vc.bilibili.com/account/v1/user/cards?uids={",".join(uid_list)}', headers=headers, timeout=20)
    res.encoding='utf-8'
    res = res.text
    try:
        data_list = json.loads(res)
        data_list = data_list["data"]
    except json.JSONDecodeError:
        logger.error(f"B站批量查询用户详情解析出错!\nUID列表:{uid_list}\n返回值如下:\n{res}")
        return msg_list
    for data in data_list:
        uid = str(data['mid'])
        if not uid in uid_list:
            logger.error(f"B站批量查询用户详情结果中有未知用户! UID:{uid}")
            continue
        uid_list.remove(uid)
        user_desc = data['sign']
        user_name = data['name']
        user_avatar = data['face']
        user_avatar = link_to_https(user_avatar) # 转换
        # 更新头像
        update_user(uid, dyn_user_dict, msg_list, "bili_dyn", "avatar", user_avatar)
        # 更新简介
        update_user(uid, dyn_user_dict, msg_list, "bili_dyn", "desc", user_desc)
        # 更新用户名
        update_user(uid, dyn_user_dict, msg_list, "bili_dyn", "name", user_name)
        # 记录更新时间
        dyn_user_dict[uid]["update_time"] = int(datetime.now().timestamp())
    if not len(uid_list) == 0:
        logger.error(f"B站批量查询用户详情结果不完整!\n遗漏的UID列表:{uid_list}")
    save_dyn_record()
    return msg_list

async def listen_bili_user_detail(dyn_config_dict: dict, msg_queue: Queue):
    global dyn_record_dict
    load_dyn_record()
    bili_ua = dyn_config_dict["ua"]
    bili_cookie = dyn_config_dict["cookie"]
    interval = dyn_config_dict["detail_interval"]
    while(True):
        uid_list: list[str] = list()
        for uid in list(dyn_record_dict["user"].keys()):
            if(datetime.now().timestamp() - dyn_record_dict["user"][uid].get("update_time", 0) > 60 * 10):
                uid_list.append(uid)
            else:
                pass
        for i in range(0,len(uid_list), 45):
            update_uid_list = uid_list[i:i+45:]
            logger.debug(f"执行B站用户详情更新\nUID列表：{update_uid_list}")
            try:
                msg_list = await get_bili_users_detail(bili_ua, bili_cookie, update_uid_list)
                if(msg_list):
                    for msg in msg_list:
                        msg_queue.put(msg)
            except:
                errmsg = traceback.format_exc()
                logger.error(f"B站用户信息抓取出错!\n{errmsg}")
            await asyncio.sleep(interval)
        await asyncio.sleep(10)

async def listen_dynamic(dyn_config_dict: dict, msg_queue: Queue):
    global dyn_record_dict
    load_dyn_record()
    bili_ua = dyn_config_dict["ua"]
    bili_cookie = dyn_config_dict["cookie"]
    interval = dyn_config_dict["interval"]
    detail_enable = dyn_config_dict["detail_enable"]
    await asyncio.sleep(1)
    logger.info("开始抓取B站动态...")
    while(True):
        logger.debug("执行抓取B站动态")
        try:
            dyn_list = await get_dynamic(bili_ua, bili_cookie, detail_enable)
            logger.debug(f"获取的B站动态列表：{dyn_list}")
            if(dyn_list):
                for dyn in dyn_list:
                    msg_queue.put(dyn)
        except:
            errmsg = traceback.format_exc()
            logger.error(f"B站动态抓取出错!\n{errmsg}")
        await asyncio.sleep(random.random()*15 + interval)

async def bili_follow(uid: str, config_dict: dict):
    follow_user = User(
        int(uid),
        Credential(
            bili_jct=config_dict["bili_jct"],
            buvid3=config_dict["buvid3"],
            sessdata=config_dict["sessdata"],
            dedeuserid=config_dict["dedeuserid"],
            ))
    res = await follow_user.modify_relation(RelationType.SUBSCRIBE)
    logger.debug(f"B站关注用户接口返回值:{json.dumps(res, ensure_ascii=False)}")
    return True

async def add_dyn_user(dyn_uid: str, config_dict: dict) -> dict:
    global dyn_record_dict
    resp = {"code": 0, "msg": "Success" }
    if(not dyn_uid in dyn_record_dict["user"]):
        try:
            await bili_follow(dyn_uid, config_dict)
            logger.info(f"成功关注B站用户！")
            dyn_record_dict["user"][dyn_uid] = {
                "last_dyn_time": int(datetime.now().timestamp())
            }
            save_dyn_record()
        except ResponseCodeException as e:
            if(e.code != 22001):
                if(e.code == -101):
                    logger.error(f"B站Cookie已经过期，请更新！")
                else:
                    logger.error(f"B站关注用户请求返回值异常！code:{e.code} msg:{e.msg}\nraw:{e.raw}")
                resp = {"code": 7, "msg": "Follow bilibili user failed"}
            else:
                logger.info(f"无需关注本账号！")
                dyn_record_dict["user"][dyn_uid] = {
                    "last_dyn_time": int(datetime.now().timestamp())
                }
                save_dyn_record()
        except:
            errmsg = traceback.format_exc()
            logger.error(f"B站关注用户发生错误！\n{errmsg}")
            resp = {"code": 7, "msg": "Follow bilibili user failed"}
    return resp

async def remove_dyn_user(dyn_uid: str, config_dict: dict):
    global dyn_record_dict
    resp = {"code": 0, "msg": "Success" }
    if(dyn_uid in dyn_record_dict["user"]):
        del dyn_record_dict["user"][dyn_uid]
        save_dyn_record()
    return resp

def load_dyn_record():
    global dyn_record_dict
    if(not dyn_record_dict is None):
        return
    try:
        with open(record_path, "r", encoding="UTF-8") as f:
            dyn_record_dict = json.loads(f.read())
        for uid in dyn_record_dict["user"].keys():
            if(not type(dyn_record_dict["user"][uid]["last_dyn_time"]) == int):
                dyn_record_dict["user"][uid]["last_dyn_time"] = int(datetime.now().timestamp())
                logger.info(f"UID为{uid}的B站用户动态时间配置不正确，已重设为当前时间")
    except FileNotFoundError:
        dyn_record_dict = {
            "user": dict()
        }
        save_dyn_record()
        logger.info(f"未找到B站动态记录文件，已自动创建")
    except:
        dyn_record_dict = {
            "user": dict()
        }
        logger.error(f"读取B站动态记录文件错误\n{traceback.format_exc()}")
        

def save_dyn_record():
    with open(record_path, "w", encoding="UTF-8") as f:
        f.write(json.dumps(dyn_record_dict))