from __future__ import annotations
import configparser
from typing import Any
from .network import cookie_str_to_dict

CONFIG_PATH = "config.ini"
# config_dict: dict[str,Any] = None
# cf: configparser.ConfigParser = None
# is_modified: bool = False

def get_config_dict() -> dict[str,Any]:
    return config_dict

def bili_cookie_process():
    cookie_dict = cookie_str_to_dict(config_dict["bili_dyn"]["cookie"])
    config_dict["bili_dyn"]["bili_jct"] = cookie_dict["bili_jct"]
    config_dict["bili_dyn"]["buvid3"] = cookie_dict["buvid3"]
    config_dict["bili_dyn"]["sessdata"] = cookie_dict["SESSDATA"]
    config_dict["bili_dyn"]["dedeuserid"] = cookie_dict["DedeUserID"]

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
