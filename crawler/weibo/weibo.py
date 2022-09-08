from __future__ import annotations
import asyncio
import collections
from datetime import datetime
import json
import os
import logging
from queue import Queue
import random
import traceback
import httpx
import jsons
from bs4 import BeautifulSoup

record_path = os.path.join(os.path.dirname(__file__), "record.json")
wb_record_dict = None
logger = logging.getLogger("crawler")

def link_to_https(link: str) -> str:
    if(not link.startswith("https://")):
        if(link.startswith("http://")):
            link = "https" + link[4::]
    return link

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

async def get_long_weibo(weibo_id, headers, is_cut: bool = False):
    for i in range(3):
        url = f'https://m.weibo.cn/detail/{weibo_id}'
        async with httpx.AsyncClient() as client:
            html = (await client.get(url, headers = headers, timeout=5)).text
        html = html[html.find('"status":'):]
        try:
            html = bracket_match(html)
        except:
            html = html[:html.rfind('"call"')]
            html = html[:html.rfind(',')]
        html = '{' + html + '}'
        js = jsons.loads(html, strict=False)
        weibo_info = js.get('status')
        if weibo_info:
            weibo = await parse_weibo(weibo_info, headers)
            #截短长微博
            if(is_cut and len(weibo['text']) > 100):
                weibo['text'] = weibo['text'][0:97] + "..."
            return weibo
        await asyncio.sleep(random.randint(1, 3))

async def parse_weibo(weibo_info, headers):
    weibo = collections.OrderedDict()
    if weibo_info['user']:
        weibo['user_id'] = weibo_info['user']['id']
        weibo['screen_name'] = weibo_info['user']['screen_name']
    else:
        weibo['user_id'] = ''
        weibo['screen_name'] = ''

    text_and_pics = await parse_text(weibo_info['text'], headers)

    weibo['text'] = text_and_pics[0]

    weibo['pics'] = get_pics(weibo_info)
    weibo['pics'].extend(text_and_pics[1])
    return weibo

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
    async with httpx.AsyncClient() as client:
        r = await client.get(pic_link, headers=headers, timeout=10)
    wb_soup = BeautifulSoup(r.text, features="lxml")
    return wb_soup.find('img').get('src')

async def parse_text(wb_text, headers) -> list:
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

    res = []
    res.append(wb_soup.getText())
    res.append(pic_list)
    return res

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

async def get_weibo(wb_cookie: str, wb_ua: str, detail_enable: bool):
    global wb_record_dict
    wb_list: list[dict] = []
    wb_user_dict: dict = wb_record_dict["user"]
    if(len(wb_user_dict) == 0):
        return wb_list
    url = 'https://m.weibo.cn/feed/friends?'
    headers = {
        'Cookie': wb_cookie,
        'User-Agent': wb_ua,
        'DNT': "1",
        'MWeibo-Pwa': "1",
        'Referer': 'https://m.weibo.cn/'
    }
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, headers=headers, timeout=20)
    except httpx.ReadTimeout:
        return wb_list
    try:
        res = r.json()
    except json.JSONDecodeError:
        try:
            url_start = r.text.find("https://m.weibo.cn/feed/friends")
            url_end = r.text.find('"', url_start)
            logger.debug(f"获取到的跳转地址：{r.text[url_start:url_end]}")
            url = r.text[url_start:url_end]
            async with httpx.AsyncClient() as client:
                r = await client.get(url, headers=headers, timeout=20)
            res = r.json()
        except httpx.ReadTimeout:
            return wb_list
        except json.JSONDecodeError:
            logger.error(f"微博解析出错!返回值如下:\n{r.text}")
            return wb_list
    if res['ok']:
        weibos = res['data']['statuses']
        now_wb_time_dict: dict[int] = dict()
        for wb_uid in wb_user_dict.keys():
            now_wb_time_dict[wb_uid] = wb_user_dict[wb_uid]["last_wb_time"]
        for i in range(len(weibos)):
            w = weibos[i]
            retweeted_status = w.get('retweeted_status')
            is_long = w.get('isLongText')
            weibo_id = str(w['id'])
            mid = w['mid']
            # 获取用户简介
            user_desc = w['user']['description']
            uid = str(w['user']['id']) # 获取到的是int
            user_name = w['user']['screen_name']
            user_avatar = w['user']['avatar_hd']
            created_time = int(get_created_time(w['created_at']).timestamp())

            user_avatar = link_to_https(user_avatar) # 转换
            # 判断是否在抓取列表中
            if not (uid in wb_user_dict):
                continue
            if(detail_enable):
                update_user(uid, wb_user_dict, wb_list, "weibo", "avatar", user_avatar)
                update_user(uid, wb_user_dict, wb_list, "weibo", "desc", user_desc)
                update_user(uid, wb_user_dict, wb_list, "weibo", "name", user_name)
                wb_user_dict[uid]["update_time"] = int(datetime.now().timestamp())
            if not (wb_user_dict[uid]["last_wb_time"] < created_time): # 不是新微博
                continue
            # 以下是处理新微博的内容
            if now_wb_time_dict[uid] < created_time:
                now_wb_time_dict[uid] = created_time
            is_retweet = False
            if retweeted_status and retweeted_status.get('id'): # 转发
                is_retweet = True
                retweet_id = str(retweeted_status['id'])
                retweet_user = retweeted_status['user']
                is_long_retweet = retweeted_status.get('isLongText')
                if is_long:
                    weibo = await get_long_weibo(weibo_id, headers)
                    if not weibo:
                        weibo = await parse_weibo(w, headers)
                else:
                    weibo = await parse_weibo(w, headers)
                if is_long_retweet:
                    retweet = await get_long_weibo(retweet_id, headers)
                    if not retweet:
                        retweet = await parse_weibo(retweeted_status, headers)
                else:
                    retweet = await parse_weibo(retweeted_status, headers)
                weibo['retweet'] = retweet
            else:  # 原创
                if is_long:
                    weibo = await get_long_weibo(weibo_id, headers)
                    if not weibo:
                        weibo = await parse_weibo(w, headers)
                else:
                    weibo = await parse_weibo(w, headers)
            wb = {
                "type": "weibo",
                "subtype": "weibo",
                "uid": uid,
                "id": weibo_id,
                "name": user_name,
                "avatar": user_avatar,
                "text": weibo["text"],
                "pics": weibo["pics"],
                "created_time": created_time,
            }
            if(is_retweet):
                wb["retweet"] = {
                    "uid": str(retweet_user["id"]),
                    "id": retweet_id,
                    "name": retweet_user["screen_name"],
                    "avatar": retweet_user["avatar_hd"],
                    "text": weibo["retweet"]["text"],
                    "pics": weibo["retweet"]["pics"],
                    "created_time": int(get_created_time(retweeted_status['created_at']).timestamp())
                }
            wb_list.append(wb)
        for uid in now_wb_time_dict.keys():
            wb_user_dict[uid]["last_wb_time"] = now_wb_time_dict[uid]
        save_wb_record()
    else:
        logger.error(f"微博请求返回值异常！\nraw:{json.dumps(res, ensure_ascii=False)}")
    wb_list.reverse()
    return wb_list

async def listen_weibo(wb_config_dict: dict, msg_queue: Queue):
    global wb_record_dict
    load_wb_record()
    wb_cookie = wb_config_dict["cookie"]
    wb_ua = wb_config_dict["ua"]
    interval = wb_config_dict["interval"]
    detail_enable = wb_config_dict["detail_enable"]
    await asyncio.sleep(1)
    logger.info("开始抓取微博...")
    while(True):
        logger.debug("执行抓取微博")
        try:
            wb_list = await get_weibo(wb_cookie, wb_ua, detail_enable)
            logger.debug(f"获取的微博列表：{wb_list}")
            if(wb_list):
                for wb in wb_list:
                    msg_queue.put(wb)
        except:
            errmsg = traceback.format_exc()
            logger.error(f"微博抓取出错!\n{errmsg}")
        await asyncio.sleep(random.random()*15 + interval)

async def get_weibo_user_detail(weibo_ua: str, weibo_cookie: str, uid: str):
    global wb_record_dict
    msg_list: list[dict] = []
    wb_user_dict: dict = wb_record_dict["user"]
    if(len(wb_user_dict) == 0):
        return msg_list
    headers = {
        "User-Agent": weibo_ua,
        "Cookie": weibo_cookie
    }
    async with httpx.AsyncClient() as client:
        res = await client.get(f'https://m.weibo.cn/api/container/getIndex?type=uid&value={uid}&containerid=100505{uid}', headers=headers, timeout=20)
    res.encoding='utf-8'
    res = res.text
    try:
        data = json.loads(res)
        data = data["data"]
    except json.JSONDecodeError:
        logger.error(f"微博用户详情解析出错!\nUID:{uid}\n返回值如下:\n{res}")
        return msg_list
    user_desc = data['userInfo']['description']
    user_name = data['userInfo']['screen_name']
    user_avatar = data['userInfo']['avatar_hd']
    user_avatar = link_to_https(user_avatar) # 转换
    update_user(uid, wb_user_dict, msg_list, "weibo", "avatar", user_avatar)
    update_user(uid, wb_user_dict, msg_list, "weibo", "desc", user_desc)
    update_user(uid, wb_user_dict, msg_list, "weibo", "name", user_name)
    wb_user_dict[uid]["update_time"] = int(datetime.now().timestamp())
    save_wb_record()
    return msg_list

async def listen_weibo_user_detail(wb_config_dict: dict, msg_queue: Queue):
    global wb_record_dict
    load_wb_record()
    wb_cookie = wb_config_dict["cookie"]
    wb_ua = wb_config_dict["ua"]
    interval = wb_config_dict["interval"]
    while(True):
        uid_list = list(wb_record_dict["user"].keys())
        for uid in uid_list:
            if(datetime.now().timestamp() - wb_record_dict["user"][uid].get("update_time", 0) > 60 * 10):
                logger.debug(f"执行微博用户详情更新\nUID：{uid}")
                try:
                    msg_list = await get_weibo_user_detail(wb_ua, wb_cookie, uid)
                    if(msg_list):
                        for msg in msg_list:
                            msg_queue.put(msg)
                except:
                    errmsg = traceback.format_exc()
                    logger.error(f"微博用户信息抓取出错!\n{errmsg}")
                await asyncio.sleep(interval)
        await asyncio.sleep(10)

async def wb_follow(uid: str, config_dict: dict):
    xsrf_token = ""
    wb_url = f"https://m.weibo.cn/profile/{uid}"
    headers = {
        "User-Agent": config_dict["ua"],
        "Cookie": config_dict["cookie"],
        "x-xsrf-token": xsrf_token,
        "x-requested-with": "XMLHttpRequest",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "sec-fetch-dest": "empty",
        "referer": wb_url,
        "dnt": "1",
        "mweibo-pwa": "1"
    }
    async with httpx.AsyncClient(headers=headers) as client:
        await client.get(url=wb_url)
        xsrf_token = client.cookies["XSRF-TOKEN"]
        params = {
            "uid": uid,
            "st": xsrf_token,
            "_spr": "screen:412x915" # S20 Ultra
        }
        res = await client.post(url="https://m.weibo.cn/api/friendships/create", params=params)
        res = res.json()
        logger.debug(f"微博关注用户接口返回值:{json.dumps(res, ensure_ascii=False)}")
        return res

async def add_wb_user(wb_uid: str, config_dict: dict) -> tuple[bool, str]:
    global wb_record_dict
    resp = {"code": 0, "msg": "Success" }
    if(not wb_uid in wb_record_dict["user"]):
        try:
            res = await wb_follow(wb_uid, config_dict)
            if(res["ok"] == 0):
                if(str(res['errno']) != "20504"):
                    logger.error(f"微博关注用户请求返回值异常！errno:{res['errno']} msg:{res['msg']}")
                    resp = {"code": 8, "msg": "Follow weibo user failed"}
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

async def remove_wb_user(wb_uid: str):
    global wb_record_dict
    if(wb_uid in wb_record_dict["user"]):
        del wb_record_dict["user"][wb_uid]
        save_wb_record()
        return True
    return False

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

def save_wb_record():
    with open(record_path, "w", encoding="UTF-8") as f:
        f.write(jsons.dumps(wb_record_dict))