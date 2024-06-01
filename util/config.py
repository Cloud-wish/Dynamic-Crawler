from __future__ import annotations
import configparser
from typing import Any

CONFIG_PATH = "config.ini"
# config_dict: dict[str,Any] = None
# cf: configparser.ConfigParser = None
# is_modified: bool = False

def cookie_str_to_dict(cookie_str: str):
    cookies_list = cookie_str.split(";")
    if len(cookies_list) != 0 and len(cookies_list[-1]) == 0:
        cookies_list.pop()
    cookies = dict()
    for c in cookies_list:
        cookie_pair = c.lstrip().rstrip().split("=")
        cookies[cookie_pair[0]] = cookie_pair[1]
    return cookies

def get_config_dict() -> dict[str,Any]:
    return config_dict

def bili_cookie_process():
    try:
        cookie_dict = cookie_str_to_dict(config_dict["bili_dyn"]["cookie"])
        config_dict["bili_dyn"]["bili_jct"] = cookie_dict["bili_jct"]
        config_dict["bili_dyn"]["buvid3"] = cookie_dict["buvid3"]
        config_dict["bili_dyn"]["sessdata"] = cookie_dict["SESSDATA"]
        config_dict["bili_dyn"]["dedeuserid"] = cookie_dict["DedeUserID"]
    except Exception as e:
        print("B站Cookie读取出错，请检查是否正确")
        raise e

def load_config() -> None:
    global config_dict, cf, is_modified
    try:
        config_dict
    except NameError:
        cf = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=["#"], comment_prefixes=["#"])
        cf.read(CONFIG_PATH, encoding="UTF-8")
        config_dict = dict()
        is_modified = False
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
            bili_cookie_process()

def get_value(section: str, key: str):
    global config_dict
    if not section in config_dict or not key in config_dict[section]:
        return None
    else:
        return config_dict[section][key]

def set_value(section: str, key: str, value):
    global cf, config_dict, is_modified
    if value == config_dict[section][key]:
        return
    cf.set(section=section, option=key, value=value)
    config_dict[section][key] = value
    if section == "bili_dyn" and key == "cookie":
        bili_cookie_process()
    is_modified = True

def save_config():
    global is_modified, cf
    with open(CONFIG_PATH, "w") as f:
        cf.write(f)
    is_modified = False

load_config()
