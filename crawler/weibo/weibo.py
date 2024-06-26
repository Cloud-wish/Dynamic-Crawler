from __future__ import annotations
import asyncio
import collections
import copy
import json
import os
import random
import traceback
import jsons
from datetime import datetime
from urllib.parse import urlparse
from queue import Queue
from http.cookiejar import CookieJar
from functools import partial
from httpx import UnsupportedProtocol, ReadTimeout, ConnectError, ConnectTimeout, RemoteProtocolError, ReadError
from bs4 import BeautifulSoup

from util.logger import init_logger
from util.network import Network, cookiejar_to_dict
from util.config import set_value, get_config_dict
from util.exception import get_exception_list

record_path = os.path.join(os.path.dirname(__file__), "record.json")
wb_record_dict = None
weibo_client: Network = None
logger = init_logger()

def link_process(link: str) -> str:
    res = urlparse(link)
    return "https://" + res.netloc + res.path

def bracket_match(html: str):
    stack = collections.deque()
    inQuote = False
    endPos = 0
    for i in range(len(html)):
        c = html[i]
        if(c == "(" or c == "[" or c == "{"):
            if(not inQuote):
                stack.append(c)
        elif(c == ")" or c == "]" or c == "}"):
            if(not inQuote):
                top = stack.pop()
                if(c == ")" and top == "("):
                    pass
                elif(c == "]" and top == "["):
                    pass
                elif(c == "}" and top == "{"):
                    pass
                else:
                    stack.append(top)
                if(len(stack) == 0):
                    endPos = i
                    break
        elif(c == '"'):
            inQuote = not inQuote
    return html[:endPos+1]

async def get_long_weibo(weibo_id: str, headers: dict):
    for i in range(3):
        try:
            url = f'https://m.weibo.cn/detail/{weibo_id}'
            html = (await weibo_client.get(url, headers = headers, timeout=25)).text
            html = html[html.find('"status":'):]
            try:
                html = bracket_match(html)
            except:
                html = html[:html.rfind('"call"')]
                html = html[:html.rfind(',')]
            html = '{' + html + '}'
            res = jsons.loads(html, strict=False).get('status')
            if res:
                return res
        except:
            pass
        await asyncio.sleep(random.randint(1,3))

async def parse_weibo_content(weibo, headers):
    text, pics = await parse_text(weibo['text'], headers)
    pics.extend(get_pics(weibo))
    return (text, pics)

def get_pics(weibo_info) -> list:
    """ 获取微博原始图片url """
    if weibo_info.get('pics'):
        pic_info = weibo_info['pics']
        pic_list = [pic['large']['url'] for pic in pic_info]
    else:
        pic_list = []
    try:
        if(weibo_info["page_info"]["type"] == "video"):
            info_pic = weibo_info["page_info"]["page_pic"]["url"]
        pic_list.append(info_pic)
    except:
        pass
    return pic_list

def get_created_time(created_at):
    created_at = datetime.strptime(created_at, '%a %b %d %H:%M:%S %z %Y')
    return created_at

async def get_weibo_photo(pic_link, headers):
    r = await weibo_client.get(pic_link, headers=headers, timeout=20)
    wb_soup = BeautifulSoup(r.text, features="lxml")
    return wb_soup.find('img').get('src')

async def parse_text(wb_text, headers) -> tuple[str, list]:
    wb_soup = BeautifulSoup(wb_text, features="lxml")
    all_a = wb_soup.findAll('a')
    pic_list = []
    for a in all_a:
        pic_link = a.get('href')
        if pic_link == None:
            pic_link = a.getText()
            a.replaceWith(pic_link)
        else:
            # 判断是否为图片
            if pic_link.endswith('.jpg') or pic_link.endswith('.jpeg') or pic_link.endswith('.png') or pic_link.endswith('.gif'):
                pic_list.append(pic_link)
                a.extract()
            else: # 不是图片
                # 先尝试转一下photo.weibo.com
                if "photo.weibo.com" in pic_link:
                    pic_list.append(await get_weibo_photo(pic_link, headers))
                    a.extract()
                else:
                    pic_link = a.getText()
                    if not ((pic_link.startswith("[") and pic_link.endswith("[")) or pic_link.startswith("@") or (pic_link.startswith("#") and pic_link.endswith("#"))):
                        pic_link = "【"+pic_link+"】"
                    a.replaceWith(pic_link)

    all_img = wb_soup.findAll('img')
    for img in all_img:
        img_desc = img.get('alt')
        if img_desc == None:
            img_desc = img.getText()
        img.replaceWith(img_desc)

    all_br = wb_soup.findAll('br')
    for br in all_br:
        br.replaceWith("\n")

    return (wb_soup.getText(), pic_list)

def parse_weibo_user(user: dict) -> dict:
    if not user:
        return {}
    res = {
        "uid": user.get("id"),
        "name": user.get("screen_name"),
        "avatar": user.get("avatar_hd"),
        "desc": user.get("description")
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
    # debug
    is_updated = False
    for key, value in _user.items():
        if key in record["user"] and record["user"][key] != value:
            is_updated = True # debug
            msg_list.append({
                "type": typ,
                "subtype": key,
                "user": user,
                "pre": record["user"][key],
                "now": value
            })
    if is_updated:
        logger.info(f"微博用户信息更新 prev:{record['user']} now:{_user}")
    record["user"] = _user
    return is_updated #

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

async def parse_weibo(weibo: dict, headers: dict, get_long: bool = True, required_values: list[str] = None, excluded_values: list[str] = None) -> dict:
    """required_values和excluded_values只能选择一个使用，会去除retweet中的相同字段"""
    is_long = weibo.get('isLongText')
    weibo_id = str(weibo['id'])
    if is_long and get_long:
        weibo = await get_long_weibo(weibo_id, headers)
    retweet_weibo = weibo.get('retweeted_status')
    weibo_mid = weibo['mid']
    created_time = int(get_created_time(weibo['created_at']).timestamp())
    text, pics = await parse_weibo_content(weibo, headers)
    if retweet_weibo and retweet_weibo.get('id'): # 转发
        retweet_id = str(retweet_weibo['id'])
        retweet_weibo = await parse_weibo(retweet_weibo, headers, get_long)
    followed_only = False
    if weibo.get("visible", {}) and weibo["visible"].get("type", 0) == 10:
        followed_only = True
    res = {
        "type": "weibo",
        "subtype": "weibo",
        "id": weibo_id,
        "mid": weibo_mid,
        "user": parse_weibo_user(weibo.get("user", {})),
        "text": text,
        "pics": pics,
        "followed_only": followed_only,
        "created_time": created_time,
    }
    if retweet_weibo:
        res["retweet"] = retweet_weibo
    if required_values and not excluded_values:
        res = trim_dict(res, required_values=required_values + ["retweet"])
        if "retweet" in res:
            if not "retweet" in required_values:
                del res["retweet"]
            else:
                res["retweet"] = trim_dict(res["retweet"], required_values=required_values)
    elif excluded_values and not required_values:
        res = trim_dict(res, excluded_values=excluded_values)
        if "retweet" in res:
            if not "retweet" in excluded_values:
                res["retweet"] = trim_dict(res["retweet"], excluded_values=excluded_values)
    return res

async def get_weibo(wb_cookie: str, wb_ua: str, detail_enable: bool, comment_limit: int):
    global wb_record_dict
    wb_list: list[dict] = []
    wb_user_dict: dict = wb_record_dict["user"]
    if(len(wb_user_dict) == 0):
        return 0, wb_list
    url = 'https://m.weibo.cn/feed/friends?'
    headers = {
        'DNT': "1",
        'MWeibo-Pwa': "1",
        'Referer': 'https://m.weibo.cn/'
    }
    try:
        r = await weibo_client.get(url, headers=headers, timeout=30)
    except (ReadTimeout, ConnectTimeout, RemoteProtocolError) as e:
        logger.info(f"微博请求超时:{repr(e)}")
        return 1, wb_list
    except (ConnectError, ReadError):
        exc_list = get_exception_list()
        if exc_list[0].startswith("ssl.SSLSyscallError") or exc_list[0].startswith("ConnectionResetError"):
            logger.info(f"微博请求超时:{exc_list[0]}")
        else:
            logger.error(f"微博请求出错!错误信息:\n{traceback.format_exc()}")
        return 1, wb_list
    try:
        res = r.json()
    except json.decoder.JSONDecodeError:
        try:
            if not r.text:
                logger.error(f"微博请求返回值为空, Cookie可能已过期, 请及时更新")
                return 1, wb_list
            url_start = r.text.find("https://m.weibo.cn/feed/friends")
            url_end = r.text.find('"', url_start)
            if url_start == -1 or url_end == -1:
                logger.info(f"微博请求出错!原始返回值:{r.text}")
                return 1, wb_list
            url = r.text[url_start:url_end]
            logger.debug(f"获取到的跳转地址：{url}")
            r = await weibo_client.get(url, headers=headers, timeout=30)
            res = r.json()
        except (ReadTimeout, ConnectTimeout, RemoteProtocolError) as e:
            logger.info(f"微博请求超时:{repr(e)}")
            return 1, wb_list
        except (ConnectError, ReadError):
            exc_list = get_exception_list()
            if exc_list[0].startswith("ssl.SSLSyscallError") or exc_list[0].startswith("ConnectionResetError"):
                logger.info(f"微博请求超时:{exc_list[0]}")
            else:
                logger.error(f"微博请求出错!错误信息:\n{traceback.format_exc()}")
            return 1, wb_list
        except json.decoder.JSONDecodeError:
            try:
                bs = BeautifulSoup(res.text, features="lxml")
                logger.error(f"微博解析出错!返回值:\n{bs.find('body').text.strip()}")
            except:
                logger.error(f"微博解析出错!返回值:\n{r.text}")
            return 1, wb_list
        except:
            logger.error(f"微博请求出错!获取到的跳转URL:\n{url}\n原始返回值:\n{r.text}")
            return 1, wb_list
    if res['ok']:
        weibos = res['data']['statuses']
        now_wb_time_dict: dict[int] = dict()
        for wb_uid in wb_user_dict.keys():
            now_wb_time_dict[wb_uid] = wb_user_dict[wb_uid]["last_wb_time"]
        for i in range(len(weibos)):
            w = weibos[i]
            # 获取用户简介
            try:
                user = parse_weibo_user(w.get("user", {}))
                uid = user["uid"]
                created_time = int(get_created_time(w['created_at']).timestamp())
            except:
                logger.error(f"一条微博用户解析错误，已跳过")
                logger.debug(f"微博用户解析出错！错误信息：\n{traceback.format_exc()}\n原始微博：{w}")
                continue
            # 判断是否在抓取列表中
            if not (uid in wb_user_dict):
                continue
            if(detail_enable):
                if update_user(wb_user_dict[uid], "weibo", user, wb_list): # debug case
                    logger.info(f"get_weibo 用户信息更新 uid:{uid} user:{user} 原微博:{w}\n")
                wb_user_dict[uid]["update_time"] = int(datetime.now().timestamp())
            if not (wb_user_dict[uid]["last_wb_time"] < created_time): # 不是新微博
                continue
            # 以下是处理新微博的内容
            try:
                weibo = await parse_weibo(w, headers)
                wb_list.append(weibo)
                if now_wb_time_dict[uid] < created_time:
                    now_wb_time_dict[uid] = created_time
            except:
                logger.error(f"获取新微博时解析微博失败！原微博：\n{w}")
        for uid in now_wb_time_dict.keys():
            wb_user_dict[uid]["last_wb_time"] = now_wb_time_dict[uid]
        save_wb_record()
    else:
        logger.error(f"微博请求返回值异常！\nraw:{json.dumps(res, ensure_ascii=False)}")
    wb_list.reverse()
    return 0, wb_list

async def listen_weibo(wb_config_dict: dict, msg_queue: Queue):
    global wb_record_dict
    load_wb_record()
    wb_cookie = wb_config_dict["cookie"]
    wb_ua = wb_config_dict["ua"]
    interval = wb_config_dict["interval"]
    detail_enable = wb_config_dict["detail_enable"]
    comment_limit = wb_config_dict["comment_limit"]
    init_network_client(wb_cookie, wb_ua)
    await asyncio.sleep(1)
    logger.info("开始抓取微博...")
    while(True):
        logger.debug("执行抓取微博")
        interval_add = 0
        try:
            code, wb_list = await get_weibo(wb_cookie, wb_ua, detail_enable, comment_limit)
            if(code > 0):
                logger.error("抓取微博超时")
                interval_add = int(interval/2)
            logger.debug(f"获取的微博列表：{wb_list}")
            if(wb_list):
                for wb in wb_list:
                    attach_cookie(wb)
                    msg_queue.put(wb)
        except:
            errmsg = traceback.format_exc()
            logger.error(f"微博抓取出错!\n{errmsg}")
        await asyncio.sleep(random.random()*15 + interval + interval_add)

async def get_weibo_user_detail(weibo_ua: str, weibo_cookie: str, uid: str):
    global wb_record_dict
    msg_list: list[dict] = []
    wb_user_dict: dict = wb_record_dict["user"]
    if(len(wb_user_dict) == 0):
        return msg_list
    try:
        res = await weibo_client.get(f'https://m.weibo.cn/api/container/getIndex?type=uid&value={uid}&containerid=100505{uid}', timeout=30)
    except (ReadTimeout, ConnectTimeout, RemoteProtocolError) as e:
        logger.info(f"微博用户信息请求超时:{repr(e)}")
        return msg_list
    except (ConnectError, ReadError):
        exc_list = get_exception_list()
        if exc_list[0].startswith("ssl.SSLSyscallError") or exc_list[0].startswith("ConnectionResetError"):
            logger.info(f"微博用户信息请求超时:{exc_list[0]}")
        else:
            logger.error(f"微博用户信息请求出错!错误信息:\n{traceback.format_exc()}")
        return msg_list
    res.encoding='utf-8'
    res = res.text
    try:
        data = json.loads(res)
        data = data["data"]
    except json.JSONDecodeError:
        logger.error(f"微博用户详情解析出错!\nUID:{uid}\n返回值:\n{res}")
        return msg_list
    try:
        user = parse_weibo_user(data['userInfo'])
        uid = user["uid"]
    except:
        logger.error(f"微博用户详情解析出错!原用户详情:\n{data}")
        return []
    update_user(wb_user_dict[uid], "weibo", user, msg_list)
    wb_user_dict[uid]["update_time"] = int(datetime.now().timestamp())
    save_wb_record()
    return msg_list

async def listen_weibo_user_detail(wb_config_dict: dict, msg_queue: Queue):
    global wb_record_dict
    load_wb_record()
    wb_cookie = wb_config_dict["cookie"]
    wb_ua = wb_config_dict["ua"]
    interval = wb_config_dict["detail_interval"]
    wb_user_dict: dict = wb_record_dict["user"]
    init_network_client(wb_cookie, wb_ua)
    while(True):
        try:
            uid_list = list(wb_user_dict.keys())
            logger.debug(f"微博用户数：{len(uid_list)}")
            for uid in uid_list:
                logger.debug(f'微博列表与用户详情更新 UID：{uid} 当前时间{datetime.now().timestamp()} 记录时间{wb_user_dict[uid].get("update_time", 0)}')
                if(datetime.now().timestamp() - wb_user_dict[uid].get("update_time", 0) > 60 * 30): # 30 min
                    logger.debug(f"执行微博列表与用户详情更新 UID：{uid} 当前记录项：{wb_user_dict[uid]}")
                    try:
                        res = await get_user_wb_list(wb_cookie, wb_ua, uid)
                        if res["ok"]:
                            logger.debug(f"UID:{uid}的微博用户微博列表更新成功")
                            wb_list = res["wb_list"]
                            if wb_list:
                                msg_list = []
                                # debug
                                if uid != wb_list[0]["user"]["uid"]:
                                    logger.error(f"微博列表与用户详情更新 UID：{uid} 发现用户不匹配 微博:{wb_list[0]}")
                                else:
                                    update_user(wb_user_dict[uid], "weibo", wb_list[0]["user"], msg_list)
                                if(msg_list):
                                    for msg in msg_list:
                                        attach_cookie(msg)
                                        msg_queue.put(msg)
                            msg_list = []
                            now_wb_time = wb_user_dict[uid]["last_wb_time"]
                            for wb in wb_list:
                                if wb_user_dict[uid]["last_wb_time"] < wb["created_time"]:
                                    now_wb_time = max(now_wb_time, wb["created_time"])
                                    msg_list.append(wb)
                            if(msg_list):
                                for msg in msg_list:
                                    attach_cookie(msg)
                                    msg_queue.put(msg)
                            logger.debug(f"微博列表与用户详情更新结束 UID：{uid} now:{now_wb_time}")
                            wb_user_dict[uid]["last_wb_time"] = now_wb_time
                            wb_user_dict[uid]["update_time"] = int(datetime.now().timestamp())
                            save_wb_record()
                        else:
                            logger.info(f"UID:{uid}的微博用户微博列表未成功更新")
                    except:
                        errmsg = traceback.format_exc()
                        logger.error(f"UID:{uid}的微博用户详情更新出错!错误信息:\n{errmsg}")
                    await asyncio.sleep(random.random()*7 + interval)
        except:
            errmsg = traceback.format_exc()
            logger.error(f"微博用户详情更新进程出错!错误信息:\n{errmsg}")
        await asyncio.sleep(10)

async def parse_comment(comment: dict, headers: dict) -> dict:
    comment_id = str(comment['id'])
    created_time = int(get_created_time(comment['created_at']).timestamp())
    text, pics = await parse_text(comment['text'], headers)
    if comment.get('pic'):
        pics.append(comment['pic']['large']['url'])
    res = {
        "type": "weibo",
        "subtype": "comment",
        "id": comment_id,
        "user": parse_weibo_user(comment.get("user", {})),
        "text": text,
        "pics": pics,
        "created_time": created_time,
    }
    return res

async def get_weibo_comment(weibo_ua: str, weibo_cookie: str, weibo: dict, wb_uid: str):
    global wb_record_dict
    cmt_list: list[dict] = []
    wb_user_dict: dict = wb_record_dict["user"]
    last_wb_cmt_time = wb_user_dict[wb_uid]["cmt_config"].get("last_wb_cmt_time", int(datetime.now().timestamp()))
    now_wb_cmt_time = last_wb_cmt_time
    headers = {
        'DNT': "1",
        'MWeibo-Pwa': "1",
        'Referer': 'https://m.weibo.cn/'
    }
    url = 'https://m.weibo.cn/comments/hotflow?'
    params = {
        'id': weibo["id"],
        'mid': weibo["mid"],
        'max_id_type': '0'
    }
    try:
        r = await weibo_client.get(url, params=params, headers=headers, timeout=25)
        res = r.json()
    except (ReadTimeout, ConnectTimeout, RemoteProtocolError) as e:
        logger.info(f"微博评论请求超时:{repr(e)}")
        return 1, cmt_list, now_wb_cmt_time
    except (ConnectError, ReadError) or exc_list[0].startswith("ConnectionResetError"):
        exc_list = get_exception_list()
        if exc_list[0].startswith("ssl.SSLSyscallError"):
            logger.info(f"微博评论请求超时:{exc_list[0]}")
        else:
            logger.error(f"微博评论请求出错!错误信息:\n{traceback.format_exc()}")
        return 1, cmt_list, now_wb_cmt_time
    except json.decoder.JSONDecodeError as e:
        try:
            logger.debug(f"微博评论解析出错，尝试跳转")
            url_start = r.text.find("https://m.weibo.cn/comments/hotflow?")
            url_end = r.text.find('"', url_start)
            # print(url_start, url_end)
            logger.debug(f"获取到的跳转地址:{r.text[url_start:url_end]}")
            if(not r.text[url_start:url_end].startswith("http://") and not r.text[url_start:url_end].startswith("https://")):
                try:
                    bs = BeautifulSoup(r.text, features="lxml")
                    logger.error(f"微博评论解析出错!UID：{wb_uid} 返回值:\n{bs.find('body').text.strip()}")
                except:
                    logger.error(f"微博评论解析出错!UID：{wb_uid} 返回值：{r.text}")
                return 1, cmt_list, now_wb_cmt_time
            r = await weibo_client.get(r.text[url_start:url_end], params=params, headers=headers, timeout=25)
            res = r.json()
        except json.decoder.JSONDecodeError as e:
            try:
                bs = BeautifulSoup(r.text, features="lxml")
                logger.error(f"微博评论解析出错!UID：{wb_uid} 返回值:\n{bs.find('body').text}")
            except:
                logger.error(f"微博评论解析出错!UID：{wb_uid} 返回值：{r.text}")
            return 1, cmt_list, now_wb_cmt_time
    if res['ok']: # ok为0是没有评论
        comments = res['data']['data']
        if comments:
            for comment in comments:
                created_time = int(get_created_time(comment['created_at']).timestamp())
                comment_id = str(comment['id'])
                comment_uid = str(comment['user']['id'])
                cmt = await parse_comment(comment, headers)
                if comment_uid == wb_uid and last_wb_cmt_time < created_time:
                    if now_wb_cmt_time < created_time:
                        now_wb_cmt_time = created_time
                    _cmt = copy.deepcopy(cmt)
                    _cmt["root"] = weibo
                    _cmt["followed_only"] = weibo["followed_only"]
                    cmt_list.append(_cmt)
                if comment['comments']: # 是否存在楼中楼
                    for inner_comment in comment['comments']:
                        # print(inner_comment)
                        inner_created_time = int(get_created_time(inner_comment['created_at']).timestamp())
                        inner_comment_id = str(inner_comment['id'])
                        inner_comment_uid = str(inner_comment['user']['id'])
                        if inner_comment_uid == wb_uid and last_wb_cmt_time < inner_created_time:
                            inner_cmt = await parse_comment(inner_comment, headers)
                            inner_cmt["reply"] = cmt
                            inner_cmt["root"] = weibo
                            inner_cmt["followed_only"] = weibo["followed_only"]
                            if now_wb_cmt_time < inner_created_time:
                                now_wb_cmt_time = inner_created_time
                            cmt_list.append(inner_cmt)
    elif("msg" in res):
        if(not res["msg"] == "快来发表你的评论吧"):
            logger.debug(f"微博评论请求返回值异常!\nmsg:{res['msg']}")
    else:
        logger.error(f"微博评论请求返回值异常!微博ID:{weibo['id']} 返回值:\n{json.dumps(res, ensure_ascii=False)}")
        return -1, cmt_list, now_wb_cmt_time
    cmt_list.reverse()
    return 0, cmt_list, now_wb_cmt_time

async def listen_weibo_comment(wb_config_dict: dict, msg_queue: Queue):
    global wb_record_dict
    load_wb_record()
    wb_cookie = wb_config_dict["cookie"]
    wb_ua = wb_config_dict["ua"]
    interval = wb_config_dict["comment_interval"]
    limit = wb_config_dict["comment_limit"]
    init_network_client(wb_cookie, wb_ua)
    await asyncio.sleep(1)
    logger.info("开始抓取微博评论...")
    while(True):
        uid_list = list(wb_record_dict["user"].keys())
        is_cmt = False
        for uid in uid_list:
            if(uid in wb_record_dict["user"] and "cmt_config" in wb_record_dict["user"][uid]):
                interval_add = 0
                is_cmt = True
                logger.debug(f"执行微博用户评论抓取 UID：{uid}")
                cnt = 0
                logger.debug(f"执行微博列表与用户详情更新 UID：{uid}")
                try:
                    res = await get_user_wb_list(wb_cookie, wb_ua, uid)
                    if res["ok"]:
                        logger.debug(f"UID:{uid}的微博用户微博列表更新成功")
                        wb_list = res["wb_list"]
                        msg_list = []
                        update_user(wb_record_dict["user"][uid], "weibo", wb_list[0]["user"], msg_list)
                        if(msg_list):
                            for msg in msg_list:
                                attach_cookie(msg)
                                msg_queue.put(msg)
                    else:
                        logger.info(f"UID:{uid}的微博用户微博列表未成功更新")
                        continue
                except:
                    errmsg = traceback.format_exc()
                    logger.error(f"UID:{uid}的微博用户微博列表更新出错!错误信息:\n{errmsg}")
                    await asyncio.sleep(random.random()*7 + interval)
                    continue
                await asyncio.sleep(random.random()*3 + 5)
                now_wb_cmt_time = wb_record_dict["user"][uid]["cmt_config"].get("last_wb_cmt_time", int(datetime.now().timestamp()))
                for weibo in wb_list:
                    if wb_config_dict["comment_followed_only"] and not weibo["followed_only"]:
                        logger.debug(f"跳过获取非粉丝可见微博的评论 ID:{weibo['id']}")
                        continue
                    logger.debug(f"获取ID:{weibo['id']}微博的评论\n微博:{weibo}")
                    if(cnt == limit):
                        break
                    try:
                        code, cmt_list, cmt_time = await get_weibo_comment(wb_ua, wb_cookie, weibo, uid)
                        if(code < 0):
                            logger.error(f"微博评论抓取出错, ID为{weibo['id']}的微博可能已被删除或不可见")
                            continue
                        elif(code > 0):
                            logger.error(f"微博评论抓取超时")
                            interval_add = int(interval/2)
                            break
                        cnt += 1
                        now_wb_cmt_time = max(now_wb_cmt_time, cmt_time)
                        if(cmt_list):
                            for cmt in cmt_list:
                                attach_cookie(cmt)
                                msg_queue.put(cmt)
                    except:
                        errmsg = traceback.format_exc()
                        logger.error(f"微博评论抓取出错!错误信息:\n{errmsg}")
                    await asyncio.sleep(random.random()*3 + 5)
                wb_record_dict["user"][uid]["cmt_config"]["last_wb_cmt_time"] = now_wb_cmt_time
                save_wb_record()
                await asyncio.sleep(random.random()*5 + interval + interval_add)
        if not is_cmt:
            await asyncio.sleep(interval)

async def wb_follow(uid: str, config_dict: dict):
    xsrf_token = ""
    wb_url = f"https://m.weibo.cn/profile/{uid}"
    headers = {
        'DNT': "1",
        'MWeibo-Pwa': "1",
        'Referer': wb_url
    }
    await weibo_client.get(url=wb_url, headers=headers)
    xsrf_token = cookiejar_to_dict(weibo_client.get_cookiejar())["XSRF-TOKEN"]
    params = {
        "uid": uid,
        "st": xsrf_token,
        "_spr": "screen:412x915" # S20 Ultra
    }
    res = await weibo_client.post(url="https://m.weibo.cn/api/friendships/create", params=params)
    res = res.json()
    logger.debug(f"微博关注用户接口返回值:{json.dumps(res, ensure_ascii=False)}")
    return res

async def get_user_wb_list(wb_cookie: str, wb_ua: str, wb_uid: str, required_values: list[str] = None) -> dict:
    def wb_time_key(weibo) -> int:
        return int(get_created_time(weibo['created_at']).timestamp())
    headers = {
        'DNT': "1",
        'MWeibo-Pwa': "1",
        'Referer': 'https://m.weibo.cn/'
    }
    url = f'https://m.weibo.cn/api/container/getIndex?containerid=107603{wb_uid}'
    wb_list = []
    try:
        r = await weibo_client.get(url, headers=headers, timeout=30)
    except (ReadTimeout, ConnectTimeout, RemoteProtocolError) as e:
        logger.info(f"微博列表请求超时:{repr(e)}")
        return {"ok": False, "wb_list": wb_list}
    except (ConnectError, ReadError) or exc_list[0].startswith("ConnectionResetError"):
        exc_list = get_exception_list()
        if exc_list[0].startswith("ssl.SSLSyscallError"):
            logger.info(f"微博列表请求超时:{exc_list[0]}")
        else:
            logger.error(f"微博列表请求出错!错误信息:\n{traceback.format_exc()}")
        return {"ok": False, "wb_list": wb_list}
    res = r.json()
    if res['ok']:
        weibos = []
        for w in res['data']['cards']:
            if w["card_type"] == 9:
                weibos.append(w["mblog"])
        weibos.sort(key=wb_time_key, reverse=True)
        for weibo in weibos:
            weibo_typ = weibo['mblogtype'] # 0=普通 1=热门 2=置顶（推测）
            try:
                wb_list.append(await parse_weibo(weibo, headers, get_long=False, required_values=required_values))
            except:
                logger.error(f"获取用户微博列表时解析微博出错!原微博:\n{weibo}")
    else:
        logger.error(f"未成功获取UID:{wb_uid}用户的微博列表!返回值:\n{json.dumps(res, ensure_ascii=False)}")
    return {"ok": res['ok'], "wb_list": wb_list}

async def add_wb_user(wb_uid: str, config_dict: dict) -> tuple[bool, str]:
    global wb_record_dict
    resp = {"code": 0, "msg": "Success" }
    if(not wb_uid in wb_record_dict["user"]):
        try:
            res = await wb_follow(wb_uid, config_dict)
            if(res["ok"] == 0):
                if(str(res['errno']) != "20504"):
                    logger.error(f"微博关注用户请求返回值异常！errno:{res['errno']} msg:{res['msg']}")
                    resp = {"code": 9, "msg": "Follow weibo user failed"}
                else:
                    logger.info(f"无需关注本账号！")
                    wb_record_dict["user"][wb_uid] = {
                        "last_wb_time": int(datetime.now().timestamp())
                    }
                    save_wb_record()
            else:
                logger.info(f"成功关注微博用户！")
                wb_record_dict["user"][wb_uid] = {
                    "last_wb_time": int(datetime.now().timestamp())
                }
                save_wb_record()
        except:
            errmsg = traceback.format_exc()
            logger.error(f"微博关注用户发生错误！\n{errmsg}")
            resp = {"code": 8, "msg": "Follow weibo user failed"}
    return resp

async def add_wb_cmt_user(wb_uid: str, config_dict: dict) -> tuple[bool, str]:
    global wb_record_dict
    resp = {"code": 0, "msg": "Success" }
    if(not wb_uid in wb_record_dict["user"]):
        resp = {"code": 15, "msg": "The weibo user is not in the crawler list before"}
    else:
        wb_cookie = config_dict["cookie"]
        wb_ua = config_dict["ua"]
        try:
            cmt_config = {
                "enable_cmt": True,
                "last_wb_cmt_time": int(datetime.now().timestamp())
            }
            wb_record_dict["user"][wb_uid]["cmt_config"] = cmt_config
            save_wb_record()
        except:
            errmsg = traceback.format_exc()
            logger.error(f"微博添加抓取评论用户发生错误！错误信息：\n{errmsg}")
            resp = {"code": 16, "msg": "Add weibo comment user failed"}
    return resp

async def remove_wb_user(wb_uid: str, config_dict: dict):
    global wb_record_dict
    resp = {"code": 0, "msg": "Success" }
    if(wb_uid in wb_record_dict["user"]):
        del wb_record_dict["user"][wb_uid]
        save_wb_record()
    return resp

async def remove_wb_cmt_user(wb_uid: str, config_dict: dict):
    global wb_record_dict
    resp = {"code": 0, "msg": "Success" }
    if(wb_uid in wb_record_dict["user"] and "cmt_config" in wb_record_dict["user"][wb_uid]):
        del wb_record_dict["user"][wb_uid]["cmt_config"]
        save_wb_record()
    return resp

def load_wb_record():
    global wb_record_dict
    if(not wb_record_dict is None):
        return
    try:
        with open(record_path, "r", encoding="UTF-8") as f:
            wb_record_dict = jsons.loads(f.read())
        for uid in wb_record_dict["user"].keys():
            if(not type(wb_record_dict["user"][uid]["last_wb_time"]) == int):
                wb_record_dict["user"][uid]["last_wb_time"] = int(datetime.now().timestamp())
                logger.info(f"UID为{uid}的微博用户动态时间配置不正确，已重设为当前时间")
            if("cmt_config" in wb_record_dict["user"][uid] and (not type(wb_record_dict["user"][uid]["cmt_config"]["last_wb_cmt_time"]) == int)):
                wb_record_dict["user"][uid]["cmt_config"]["last_wb_cmt_time"] = int(datetime.now().timestamp())
                logger.info(f"UID为{uid}的微博用户动态评论时间配置不正确，已重设为当前时间")
    except FileNotFoundError:
        wb_record_dict = {
            "user": dict()
        }
        save_wb_record()
        logger.info(f"未找到微博动态记录文件，已自动创建")
    except:
        wb_record_dict = {
            "user": dict()
        }
        logger.error(f"读取微博动态记录文件错误\n{traceback.format_exc()}")

def save_wb_cookie(cookies: CookieJar):
    cookie_dict = cookiejar_to_dict(cookies)
    cookie_str = ""
    for k, v in cookie_dict.items():
        cookie_str += f"{k}={v};"
    set_value("weibo", "cookie", cookie_str)

def attach_cookie(msg: dict) -> None:
    cookie = get_config_dict()["weibo"]["cookie"]
    ua = get_config_dict()["weibo"]["ua"]
    msg["cookie"] = cookie
    msg["ua"] = ua

def init_network_client(cookie_str: str = None, ua_str: str = None):
    global weibo_client
    if weibo_client is None:
        logger.debug("微博HTTP客户端开始初始化")
        weibo_client = Network(cookie_str, ua_str)
        # 暂时关闭Cookie更新
        # weibo_client.set_save_cookie_func(partial(save_wb_cookie))
        logger.debug("微博HTTP客户端初始化完成")

def save_wb_record():
    with open(record_path, "w", encoding="UTF-8") as f:
        f.write(jsons.dumps(wb_record_dict))