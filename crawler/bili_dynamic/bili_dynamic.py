from __future__ import annotations
import asyncio
import copy
from datetime import datetime
import os
import logging
from queue import Queue
import random
import traceback
from urllib.parse import urlparse
import httpx
import json
from bs4 import BeautifulSoup
from bilibili_api.user import User, RelationType
from bilibili_api import Credential
from bilibili_api.exceptions.ResponseCodeException import ResponseCodeException
from bilibili_api.comment import CommentResourceType, OrderType, get_comments

from util.logger import init_logger

record_path = os.path.join(os.path.dirname(__file__), "record.json")
dyn_record_dict = None
logger = init_logger()

def link_process(link: str) -> str:
    res = urlparse(link)
    return "https://" + res.netloc + res.path

def trim_dict(data: dict[str], required_values: list[str] = None, excluded_values: list[str] = None) -> dict[str]:
    """返回一个新的dict"""
    res = dict()
    if required_values and not excluded_values:
        for key in list(data.keys()):
            if key in required_values:
                res[key] = data[key]
    elif excluded_values and not required_values:
        for key in list(data.keys()):
            if not key in excluded_values:
                res[key] = data[key]
    return res

def parse_dyn_user(user: dict) -> dict:
    res = {
        "uid": user.get("mid"),
        "name": user.get("name"),
        "avatar": user.get("face"),
        "desc": user.get("sign")
    }
    if res["uid"]:
        res["uid"] = str(res["uid"])
    if res["avatar"]:
        res["avatar"] = link_process(res["avatar"])
    for key in list(res.keys()):
        if res[key] is None:
            del res[key]
    return res

def update_user(record: dict, typ: str, user: dict, msg_list: list):
    _user = copy.deepcopy(user)
    if not "user" in record:
        record["user"] = {}
    if "uid" in _user:
        del _user["uid"]
    for key, value in record["user"].items():
        if key in _user and value != _user[key]:
            msg_list.append({
                "type": typ,
                "subtype": key,
                "user": user,
                "pre": value,
                "now": _user[key]
            })
        elif not key in _user:
            _user[key] = value
            user[key] = value
    record["user"] = _user

def get_dyn_oid_type(card: dict) -> tuple(int, CommentResourceType):
    dyn_type = CommentResourceType.DYNAMIC
    dyn_oid = card['desc']['dynamic_id']
    if "rid" in card["desc"]:
        if "bvid" in card["desc"]:
            dyn_type = CommentResourceType.VIDEO
        elif "pictures" in card["card"].get("item", {}):
            dyn_type = CommentResourceType.DYNAMIC_DRAW
        elif card['desc']['type'] == 64:
            dyn_type = CommentResourceType.ARTICLE
    if dyn_type != CommentResourceType.DYNAMIC:
        dyn_oid = card["desc"]["rid"]
    return (dyn_oid, dyn_type.value)

async def parse_bili_dyn_content(dyn_typ: str, content: dict, orig: dict = None) -> dict:
    res = dict()
    if dyn_typ == "DYNAMIC_TYPE_DRAW":  # 带图片动态
        if content['desc'] is None:
            # 新动态类型
            dyn_text = content['major']['opus']['summary']['text']
            pic_list = content['major']['opus']['pics']
            pic_src_list = list()
            for pic in pic_list:
                pic_src_list.append(pic['url'])
        else:
            dyn_text = content['desc']['text']
            pic_list = content['major']['draw']['items']
            pic_src_list = list()
            for pic in pic_list:
                pic_src_list.append(pic['src'])
        res = {
            "text": dyn_text,
            "pics": pic_src_list
        }
    elif dyn_typ == "DYNAMIC_TYPE_WORD":  # 纯文字动态
        dyn_text = content['desc']['text']
        res = {
            "text": dyn_text,
        }
    elif dyn_typ == "DYNAMIC_TYPE_ARTICLE":  # 文章
        title = content['major']['opus']['title']
        summary = content['major']['opus']['summary']['text']
        cover_pic = content['major']['opus']['pics'][0]['url']
        res = {
            "title": title,
            "desc": summary,
            "cover_pic": cover_pic,
            "link": content['major']['opus']['jump_url'][2:] # 去掉开头的"//"
        }
    elif dyn_typ == "DYNAMIC_TYPE_AV":  # 投稿视频
        title = content['major']['archive']['title']
        cover_pic = content['major']['archive']['cover']
        video_desc = content['major']['archive']['desc']
        res = {
            "title": title,
            "desc": video_desc,
            "cover_pic": cover_pic,
            "link": content['major']['archive']['jump_url'][2:] # 去掉开头的"//"
        }
    elif dyn_typ == "DYNAMIC_TYPE_FORWARD":  # 转发动态
        dyn_text = content['desc']['text']
        orig_typ = orig['type']
        res = {
            "text": dyn_text,
            "retweet": {}
        }
        if(orig_typ in ["DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_WORD", "DYNAMIC_TYPE_ARTICLE", "DYNAMIC_TYPE_AV"]):
            res["retweet"] = await parse_bili_dyn_content(orig_typ, orig["modules"]["module_dynamic"])
        res["retweet"]["dyn_type"] = orig_typ
        res["retweet"]["is_retweet"] = True
        res["retweet"]["user"] = parse_dyn_user(orig['modules']['module_author'])
    return res

async def parse_bili_dyn(card: dict, user: dict) -> dict:
    created_time = card['modules']['module_author']['pub_ts']
    dyn_id = card['id_str']
    dyn_typ = card['type']
    # dyn_oid, oid_type = get_dyn_oid_type(card)
    res = {
        "type": "bili_dyn",
        "subtype": "dynamic",
        "dyn_type": dyn_typ,
        "id": dyn_id,
        "user": user,
        "link": f"https://t.bilibili.com/{dyn_id}",
        "created_time": created_time
    }
    orig = None
    if(dyn_typ == "DYNAMIC_TYPE_FORWARD"):
        orig = card['orig']
    parse_res = await parse_bili_dyn_content(dyn_typ, card['modules']['module_dynamic'], orig)
    for key, value in parse_res.items():
        res[key] = value
    if dyn_typ == "DYNAMIC_TYPE_FORWARD":
        res["retweet"]["created_time"] = orig['modules']['module_author']['pub_ts']
    return res

async def parse_bili_dyn_cmt(cmt: dict) -> dict:
    user_id = str(cmt['member']['mid'])
    user_desc = cmt['member']['sign']
    user_name = cmt['member']['uname']
    user_avatar = link_process(cmt['member']['avatar'])
    user = {
        "uid": user_id,
        "desc": user_desc,
        "name": user_name,
        "avatar": user_avatar
    }
    cmt_text = cmt['content']['message']
    comment_id = str(cmt['rpid'])
    created_time = int(cmt['ctime'])
    res = {
        "type": "bili_dyn",
        "subtype": "comment",
        "id": comment_id,
        "user": user,
        "text": cmt_text,
        "created_time": created_time
    }
    return res

async def get_dynamic(bili_ua: str, bili_cookie: str, detail_enable: bool, comment_limit: int):
    global dyn_record_dict
    dyn_list: list[dict] = []
    dyn_user_dict: dict = dyn_record_dict["user"]
    if(len(dyn_user_dict) == 0):
        return dyn_list
    headers = {
        "User-Agent": bili_ua,
        "Cookie": bili_cookie,
    }
    params = {
        "type": "all",
        "timezone_offset": -480,
        "platform": "web",
        "page": 1,
        "features": "itemOpusStyle,opusBigCover,onlyfansVote,endFooterHidden,decorationCard,onlyfansAssetsV2,ugcDelete",
        "web_location": "333.1368",
        "x-bili-device-req-json": '{"platform":"web","device":"pc"}',
        "x-bili-web-req-json": '{"spm_id":"333.1368"}',
    }
    async with httpx.AsyncClient() as client:
        res = await client.get('https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/all', headers=headers, params=params, timeout=20)
    res.encoding='utf-8'
    res = res.text
    try:
        cards_data = json.loads(res)
    except json.JSONDecodeError:
        try:
            bs = BeautifulSoup(res, features="lxml")
            logger.error(f"B站动态解析出错!返回值如下:\n{bs.find('body').text.strip()}")
        except:
            logger.error(f"B站动态解析出错!返回值如下:\n{res}")
        return dyn_list
    if(cards_data['code'] != 0):
        if(cards_data['code'] == -6):
            logger.error(f"B站Cookie失效，请重新获取！")
        else:
            logger.error(f"B站动态请求返回值异常! code:{cards_data['code']} msg:{cards_data['message']}\nraw:{json.dumps(cards_data, ensure_ascii=False)}")
        return dyn_list
    cards_data = cards_data['data']['items']
    # with open("bili_dynamic.json", "w", encoding="utf-8") as f:
    #     f.write(json.dumps(cards_data, ensure_ascii=False, indent=4))
    now_dyn_time_dict = dict()
    for dyn_uid in dyn_user_dict:
        now_dyn_time_dict[dyn_uid] = dyn_user_dict[dyn_uid]["last_dyn_time"]
    for card in cards_data:
        uid = str(card['modules']['module_author']['mid'])
        if (not uid in dyn_user_dict): # 不是推送的人
            continue
        dyn_type = card['type']
        if (dyn_type in ['DYNAMIC_TYPE_LIVE_RCMD']): # 忽略直播动态
            continue
        try:
            # if type(card["card"]) == str:
            #     card["card"] = json.loads(card["card"])
            # # b站bug: user_profile中的头像可能为旧头像
            # if card["card"].get("user",{}).get("head_url"):
            #     card['desc']['user_profile']['info']['face'] = card["card"]["user"]["head_url"]

            # 尝试获取完整用户信息
            # user_info = await get_user_info(bili_ua, bili_cookie, uid)
            # await asyncio.sleep(3 + random.random()*3)
            # if(user_info):
            #     user = parse_dyn_user(user_info)
            # else:
            # 无sign
            user = parse_dyn_user(card['modules']['module_author'])
            created_time = int(card['modules']['module_author']['pub_ts'])
        except:
            logger.info(f"一条B站动态用户解析错误，可能不是动态消息，已跳过")
            logger.debug(f"B站动态用户解析出错！错误信息：\n{traceback.format_exc()}\n原始动态：{card}")
            continue

        # 更新信息
        # 由于该接口不返回完整用户信息，不在此处更新
        # if(detail_enable):
        #     update_user(dyn_user_dict[uid], "bili_dyn", user, dyn_list)
        #     dyn_user_dict[uid]["update_time"] = int(datetime.now().timestamp())
        if (not dyn_user_dict[uid]["last_dyn_time"] < created_time):# 不是新动态
            continue
        # 以下是处理新动态的内容
        if now_dyn_time_dict[uid] < created_time:
            now_dyn_time_dict[uid] = created_time
        try:
            dyn = await parse_bili_dyn(card, user)
        except:
            logger.info(f"一条B站动态解析错误，可能不是动态消息，已跳过")
            logger.debug(f"B站动态解析出错！错误信息：\n{traceback.format_exc()}\n原始动态：{card}")
            continue
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
        "Cookie": bili_cookie
    }
    async with httpx.AsyncClient() as client:
        res = await client.get(f'https://api.vc.bilibili.com/account/v1/user/cards?uids={",".join(uid_list)}', headers=headers, timeout=20)
    res.encoding='utf-8'
    res = res.text
    try:
        data_list = json.loads(res)
        data_list = data_list["data"]
    except (json.JSONDecodeError, KeyError):
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
        user_avatar = link_process(user_avatar) # 转换
        user = {
            "uid": uid,
            "desc": user_desc,
            "name": user_name,
            "avatar": user_avatar
        }
        update_user(dyn_user_dict[uid], "bili_dyn", user, msg_list)
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
    comment_limit = dyn_config_dict["comment_limit"]
    await asyncio.sleep(1)
    logger.info("开始抓取B站动态...")
    while(True):
        logger.debug("执行抓取B站动态")
        try:
            dyn_list = await get_dynamic(bili_ua, bili_cookie, detail_enable, comment_limit)
            if(dyn_list):
                logger.info(f"获取的B站动态列表：{dyn_list}")
                for dyn in dyn_list:
                    # attach_cookie(dyn)
                    dyn["ua"] = bili_ua
                    dyn["cookie"] = bili_cookie
                    msg_queue.put(dyn)
            else:
                logger.debug(f"获取的B站动态列表：{dyn_list}")
        except:
            errmsg = traceback.format_exc()
            logger.error(f"B站动态抓取出错!\n{errmsg}")
        await asyncio.sleep(random.random()*15 + interval)

async def get_dynamic_comment(dyn: dict, dyn_uid: str):
    global dyn_record_dict
    cmt_list: list[dict] = []
    dyn_user_dict: dict = dyn_record_dict["user"]
    last_dyn_cmt_time = dyn_user_dict[dyn_uid]["cmt_config"].get("last_dyn_cmt_time", int(datetime.now().timestamp()))
    now_dyn_cmt_time = last_dyn_cmt_time
    resp = await get_comments(oid=dyn["oid"], type_=CommentResourceType(dyn["oid_type"]), order=OrderType.LIKE)
    comments = resp["replies"]
    if("upper" in resp and "top" in resp["upper"] and resp["upper"]["top"]):
        comments.append(resp["upper"]["top"])
    if comments:
        for comment in comments:
            cmt = await parse_bili_dyn_cmt(comment)
            if cmt["user"]["uid"] == dyn_uid and last_dyn_cmt_time < cmt["created_time"]:
                if now_dyn_cmt_time < cmt["created_time"]:
                    now_dyn_cmt_time = cmt["created_time"]
                _cmt = copy.deepcopy(cmt)
                _cmt["root"] = dyn
                cmt_list.append(_cmt)
            if "replies" in comment and not comment["replies"] is None:
                for inner_comment in comment["replies"]:
                    inner_cmt = await parse_bili_dyn_cmt(inner_comment)
                    if inner_cmt["user"]["uid"] == dyn_uid and last_dyn_cmt_time < inner_cmt["created_time"]:
                        if now_dyn_cmt_time < inner_cmt["created_time"]:
                            now_dyn_cmt_time = inner_cmt["created_time"]
                        inner_cmt["reply"] = cmt
                        inner_cmt["root"] = dyn
                        cmt_list.append(inner_cmt)
    return cmt_list, now_dyn_cmt_time

async def listen_dynamic_comment(dyn_config_dict: dict, msg_queue: Queue):
    global dyn_record_dict
    load_dyn_record()
    dyn_cookie = dyn_config_dict["cookie"]
    dyn_ua = dyn_config_dict["ua"]
    interval = dyn_config_dict["comment_interval"]
    limit = dyn_config_dict["comment_limit"]
    await asyncio.sleep(1)
    # logger.info("更新抓取评论用户的B站动态列表...")
    # uid_list = list(dyn_record_dict["user"].keys())
    # for uid in uid_list:
    #     if("cmt_config" in dyn_record_dict["user"][uid] and not dyn_record_dict["user"][uid]["cmt_config"]["is_top"]):
    #         logger.debug(f"执行B站用户动态列表更新\nUID：{uid}")
    #         try:
    #             res = await get_user_dyn_list(uid)
    #             dyn_record_dict["user"][uid]["cmt_config"]["dyn_list"] = res
    #             logger.debug(f"UID:{uid}的B站用户动态列表更新成功")
    #         except:
    #             errmsg = traceback.format_exc()
    #             logger.error(f"UID:{uid}的B站用户动态列表更新失败！错误信息：\n{errmsg}")
    #         await asyncio.sleep(30)
    logger.info("开始抓取B站动态评论...")
    while(True):
        uid_list = list(dyn_record_dict["user"].keys())
        is_cmt = False
        for uid in uid_list:
            if(uid in dyn_record_dict["user"] and "cmt_config" in dyn_record_dict["user"][uid]):
                is_cmt = True
                logger.debug(f"执行B站用户评论抓取 UID：{uid}")
                cnt = 0
                logger.debug(f"执行B站动态列表与用户详情更新 UID：{uid}")
                try:
                    dyn_list = await get_user_dyn_list(uid, dyn_record_dict["user"][uid]["cmt_config"]["is_top"])
                    logger.debug(f"UID:{uid}的B站用户动态列表更新成功")
                    msg_list = []
                    update_user(dyn_record_dict["user"][uid], "bili_dyn", dyn_list[0]["user"], msg_list)
                    if(msg_list):
                        for msg in msg_list:
                            msg_queue.put(msg)
                except:
                    errmsg = traceback.format_exc()
                    logger.error(f"UID:{uid}的B站用户动态列表更新失败！\n{errmsg}")
                    await asyncio.sleep(random.random()*7 + interval)
                    continue
                if dyn_record_dict["user"][uid]["cmt_config"]["is_top"]:
                    dyn_list = dyn_list[0:1]
                now_dyn_cmt_time = dyn_record_dict["user"][uid]["cmt_config"].get("last_dyn_cmt_time", int(datetime.now().timestamp()))
                for dyn in dyn_list:
                    if(cnt == limit):
                        break
                    try:
                        cmt_list, cmt_time = await get_dynamic_comment(dyn, uid)
                        cnt += 1
                        now_dyn_cmt_time = max(now_dyn_cmt_time, cmt_time)
                        if(cmt_list):
                            for cmt in cmt_list:
                                msg_queue.put(cmt)
                    except ResponseCodeException as e:
                        if e.code == -404:
                            logger.error(f"B站动态评论抓取出错，ID为{dyn['id']}的动态可能已被删除")
                        elif e.code == 12002:
                            logger.error(f"B站动态评论抓取出错，ID为{dyn['id']}的动态评论区已关闭")
                        else:
                            errmsg = traceback.format_exc()
                            logger.error(f"B站动态评论抓取出错！错误信息：\n{errmsg}")
                    except:
                        errmsg = traceback.format_exc()
                        logger.error(f"B站动态评论抓取出错！错误信息：\n{errmsg}")
                dyn_record_dict["user"][uid]["cmt_config"]["last_dyn_cmt_time"] = now_dyn_cmt_time
                save_dyn_record()
                await asyncio.sleep(random.random()*5 + interval)
        if not is_cmt:
            await asyncio.sleep(interval)

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

async def get_user_dyn_list(dyn_uid: str, need_top: bool = False):
    user = User(uid=int(dyn_uid))
    card_list = (await user.get_dynamics(need_top=need_top))["cards"]
    dyn_list = []
    for card in card_list:
        dyn = await parse_bili_dyn(card)
        dyn_list.append(dyn)
    return dyn_list

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
            if(e.code != 22001 and e.code != 22014):
                if(e.code == -101):
                    logger.error(f"B站Cookie已经过期，请更新！")
                else:
                    logger.error(f"B站关注用户请求返回值异常！code:{e.code} msg:{e.msg}\nraw:{e.raw}")
                resp = {"code": 8, "msg": "Follow bilibili user failed"}
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

async def add_dyn_cmt_user(dyn_uid: str, config_dict: dict, is_top: bool = False) -> tuple[bool, str]:
    global dyn_record_dict
    resp = {"code": 0, "msg": "Success" }
    if(not dyn_uid in dyn_record_dict["user"]):
        resp = {"code": 17, "msg": "The bilibili user is not in the crawler list before"}
    else:
        try:
            cmt_config = {
                "is_top": is_top,
                "enable_cmt": True,
                "last_dyn_cmt_time": int(datetime.now().timestamp())
            }
            dyn_record_dict["user"][dyn_uid]["cmt_config"] = cmt_config
            save_dyn_record()
        except:
            errmsg = traceback.format_exc()
            logger.error(f"B站动态添加抓取评论用户发生错误！错误信息：\n{errmsg}")
            resp = {"code": 18, "msg": "Add bilibili dynamic comment user failed"}
    return resp

async def remove_dyn_user(dyn_uid: str, config_dict: dict):
    global dyn_record_dict
    resp = {"code": 0, "msg": "Success" }
    if(dyn_uid in dyn_record_dict["user"]):
        del dyn_record_dict["user"][dyn_uid]
        save_dyn_record()
    return resp

async def remove_dyn_cmt_user(dyn_uid: str, config_dict: dict):
    global dyn_record_dict
    resp = {"code": 0, "msg": "Success" }
    if(dyn_uid in dyn_record_dict["user"] and "cmt_config" in dyn_record_dict["user"][dyn_uid]):
        del dyn_record_dict["user"][dyn_uid]["cmt_config"]
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
            if("cmt_config" in dyn_record_dict["user"][uid] and (not type(dyn_record_dict["user"][uid]["cmt_config"]["last_dyn_cmt_time"]) == int)):
                dyn_record_dict["user"][uid]["cmt_config"]["last_dyn_cmt_time"] = int(datetime.now().timestamp())
                logger.info(f"UID为{uid}的B站用户动态评论时间配置不正确，已重设为当前时间")
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