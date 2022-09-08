import asyncio
import json
import requests
import websockets

ws_url = "ws://localhost:37773"
http_url = "http://localhost:27773"
client_name = "client_sample"

async def init():
    requests.post(http_url+"/init", json={"client_name": client_name, "url": "websocket"}) # 客户端使用Websocket连接到服务端，也可更改url，服务端将使用HTTP POST发送JSON参数到客户端指定的url

async def add(typ, uid):
    requests.post(http_url+"/add", json={"client_name": client_name, "type": typ, "uid": uid})

async def remove(typ, uid):
    requests.post(http_url+"/remove", json={"client_name": client_name, "type": typ, "uid": uid})

async def ws_client():
    while True: # 断线重连
        try:
            async with websockets.connect(ws_url) as websocket:
                msg = {"type": "init", "client_name": client_name} # 初始化Websocket连接
                await websocket.send(json.dumps(msg))
                recv_msg = await websocket.recv()
                print(recv_msg)
                while True:
                    recv_msg = await websocket.recv()
                    print(recv_msg)
        except Exception as e:
            print(f"Websocket连接出错！错误信息：\n{str(e)}\n尝试重连...")

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(init())
    asyncio.get_event_loop().create_task(ws_client())
    asyncio.get_event_loop().run_forever() # 阻塞