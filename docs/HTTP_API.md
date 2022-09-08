# HTTP API
## 如何使用
1. 进行初始化，声明客户端名称所对应的推送URL地址
2. 使用HTTP API添加所需推送
3. 通过URL或连接[Websocket Server](https://github.com/Cloud-wish/Dynamic-Crawler/blob/master/docs/Websocket_Server.md)接收推送消息
## API列表
### 客户端初始化

> http://{http_host}:{http_port}/init

请求方式：POST

**参数（application/json）：**

| 参数名 | 类型 | 内容        | 必要性 | 备注 |
| ------ | ---- | ----------- | ------ | ---- |
| client_name | str | 客户端名称 | 必要 |      |
| url | str | 客户端URL | 必要 | 用于向客户端推送消息<br/>设置为`Websocket`代表使用Websocket方式推送 |

**json回复：**

| 字段    | 类型 | 内容     | 备注                        |
| ------- | ---- | -------- | --------------------------- |
| code    | num  | 返回值   | 0：成功<br/>-1：参数非JSON<br/>>0：其它错误 |
| msg | str  | 错误信息 |                      |

### 添加指定用户的推送
> http://{http_host}:{http_port}/add

请求方式：POST

**参数（application/json）：**

| 参数名 | 类型 | 内容        | 必要性 | 备注 |
| ------ | ---- | ----------- | ------ | ---- |
| client_name | str | 客户端名称 | 必要 |      |
| uid | int | 要添加推送的用户UID | 必要 |      |
| type | str | 要添加推送的类型 | 必要 | `weibo`：微博<br/>`bili_dyn`：哔哩哔哩动态<br/>`bili_live`：哔哩哔哩直播 |

**json回复：**

| 字段    | 类型 | 内容     | 备注                        |
| ------- | ---- | -------- | --------------------------- |
| code    | num  | 返回值   | 0：成功<br/>-1：参数非JSON<br/>>0：其它错误 |
| msg | str  | 错误信息 |                      |

### 删除指定用户的推送
> http://{http_host}:{http_port}/remove

请求方式：POST

**参数（application/json）：**

| 参数名 | 类型 | 内容        | 必要性 | 备注 |
| ------ | ---- | ----------- | ------ | ---- |
| client_name | str | 客户端名称 | 必要 |      |
| uid | int | 要删除推送的用户UID | 必要 |      |
| type | str | 要删除推送的类型 | 必要 | `weibo`：微博<br/>`bili_dyn`：哔哩哔哩动态<br/>`bili_live`：哔哩哔哩直播 |

**json回复：**

| 字段    | 类型 | 内容     | 备注                        |
| ------- | ---- | -------- | --------------------------- |
| code    | num  | 返回值   | 0：成功<br/>-1：参数非JSON<br/>>0：其它错误 |
| msg | str  | 错误信息 |                      |

## 推送消息格式

消息统一采用HTTP POST请求，发送的参数类型为application/json

| 字段    | 类型 | 内容     | 备注                        |
| ------- | ---- | -------- | --------------------------- |
| type | str  | 推送消息的类型 |  |
| subtype | str  | 推送消息的子类型 |   |
| uid | str  | 用户UID |   |
| name | str  | 用户昵称 |   |
（其余字段根据消息类型有所不同）