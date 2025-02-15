import asyncio
import json
import os
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import and_, func

from core.db import RecordToDatabase, Database, SystemInfo, CPUInfo, GPUInfo, CheckOnlineStatus
from core.msgqueue import msg_queue

DB = Database()
record_to_database = RecordToDatabase(DB, msg_queue)
asyncio.create_task(record_to_database.run())  # 不要等待，直接运行

check_online = CheckOnlineStatus(DB)
asyncio.create_task(check_online.run())

app = FastAPI()
# app.state.record_to_database = record_to_database
# app.state.db = DB

# 在这里定义允许的密钥列表或做其他的认证逻辑
VALID_KEYS = os.environ.get("key", "123456").split(",")


async def verify_key(websocket: WebSocket, key: str):
    """验证 WebSocket 连接时提供的 key"""
    if key not in VALID_KEYS:
        await websocket.close(code=4000)  # 关闭连接，状态码 4000 表示认证失败
        raise ValueError("Invalid key")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, key: Optional[str] = Query(None)):
    """WebSocket 端点处理客户端连接"""
    # 验证 key 是否有效
    try:
        uid = None
        ip = websocket.client.host
        if key is None:
            await websocket.close(code=4000)  # 如果没有 key，关闭连接
            return
        await verify_key(websocket, key)
        await websocket.accept()
        print(f"Client connected with key: {key}")
        while True:
            try:
                data = await websocket.receive_text()
                data = json.loads(data)
                host_name = data.get("host_info", {}).get("host_name", "")
                uid = SystemInfo.generate_uid(host_name, ip)
                data["host_info"]["ip"] = ip
                await websocket.send_text("OK")
                await msg_queue.push(f"{ip}{host_name}", data)
            except WebSocketDisconnect:
                print("Client disconnected")

                # 更新数据库中的在线状态
                with DB.get_session() as session:
                    if uid:  # 如果生成了 uid
                        system = session.query(SystemInfo).filter(SystemInfo.uid == uid).first()
                        if system:
                            print(f"Set {system.host_name} offline")
                            system.is_online = 0
                            session.commit()
                break
    except ValueError as e:
        print(f"Connection failed: {e}")


@app.get("/")
async def all():
    with DB.get_session() as session:
        # 获取所有的系统信息
        systems = session.query(SystemInfo).all()
        all_data = []
        for system in systems:
            # 根据 Gpu Info lindex 做索引，返回 相同 lindex 的最大值
            gpus = session.query(
                GPUInfo.id,
                GPUInfo.lindex.label("lindex"),
                func.max(GPUInfo.fan_speed).label("fan_speed"),
                func.max(GPUInfo.temperature).label("temperature"),
                func.max(GPUInfo.gpu_utilization).label("gpu_utilization"),
                func.max(GPUInfo.memory_total).label("memory_total"),
                func.max(GPUInfo.memory_used).label("memory_used"),
                func.max(GPUInfo.memory_free).label("memory_free")
            ).filter(GPUInfo.uid == system.uid).group_by(GPUInfo.lindex).all()
            gpu_info = []
            for gpu in gpus:
                gpu_info.append({
                    "id": gpu.id,
                    # "uid": gpu.uid,
                    "index": gpu.lindex,
                    "fan_speed": gpu.fan_speed,
                    "temperature": gpu.temperature,
                    "gpu_utilization": gpu.gpu_utilization,
                    "memory_total": gpu.memory_total,
                    "memory_used": gpu.memory_used,
                    "memory_free": gpu.memory_free,
                })

            # 获取 CPU 信息
            cpu = session.query(
                func.max(CPUInfo.cpu_percent).label("cpu_percent"),
                func.max(CPUInfo.cpu_count).label("cpu_count")
            ).filter(CPUInfo.uid == system.uid).first()

            cpu_info = {
                # "uid": cpu.uid,
                "cpu_percent": cpu.cpu_percent,
                "cpu_count": cpu.cpu_count
            }
            sin = {
                "host_name": system.host_name,
                "ip": system.ip,
                "uid": system.uid,
                "is_online": system.is_online,
                "last_update": system.last_update,
                "cpu": cpu_info,
                "gpus": gpu_info
            }
            all_data.append(sin)
        return all_data


# 使用 humanfriendly 库将字符串转换为字节
from humanfriendly import parse_size


@app.get("/get_device")
async def get_device(
        cpu_count: int = Query(4, description="最小的CPU 核心数"),
        max_cpu_percent: float = Query(50, description="允许CPU当前综合最大利用率 (%)"),
        sum_gpu_count: int = Query(2, description="需要的GPU数量"),
        gpu_memory_total: str = Query("16GB", description="需求的显卡的最小内存"),
        gpu_memory_free: str = Query("8GB", description="需求的显卡的最小剩余内存"),
        max_gpu_utilization: float = Query(20, description="允许需求的显卡的当前最大利用率 (%)"),
        only_one: bool = Query(True, description="是否只返回一个机器"),
):
    gpu_memory_total = parse_size(gpu_memory_total)
    gpu_memory_free = parse_size(gpu_memory_free)

    with DB.get_session() as session:
        # 查询 CPU 最大利用率
        cpu_query = session.query(
            SystemInfo.uid,
            func.max(CPUInfo.cpu_percent).label("cpu_percent"),
            func.max(CPUInfo.cpu_count).label("cpu_count")
        ).join(CPUInfo, SystemInfo.uid == CPUInfo.uid).group_by(SystemInfo.uid).subquery()

        # 查询 GPU 的最大值（以 lindex 分组）
        gpu_query = session.query(
            GPUInfo.uid,
            GPUInfo.lindex.label("lindex"),
            func.max(GPUInfo.fan_speed).label("fan_speed"),
            func.max(GPUInfo.temperature).label("temperature"),
            func.max(GPUInfo.gpu_utilization).label("gpu_utilization"),
            func.max(GPUInfo.memory_total).label("memory_total"),
            func.max(GPUInfo.memory_used).label("memory_used"),
            func.max(GPUInfo.memory_free).label("memory_free")
        ).group_by(GPUInfo.uid, GPUInfo.lindex).subquery()

        # 关联 SystemInfo 和查询结果，筛选符合要求的设备
        systems = session.query(
            SystemInfo.host_name,
            SystemInfo.ip,
            SystemInfo.uid,
            SystemInfo.is_online,
            cpu_query.c.cpu_percent,
            cpu_query.c.cpu_count
        ).join(cpu_query, SystemInfo.uid == cpu_query.c.uid).filter(
            and_(
                SystemInfo.is_online == 1,
                cpu_query.c.cpu_count >= cpu_count,
                cpu_query.c.cpu_percent <= max_cpu_percent
            )
        ).all()

        # 组装符合条件的设备信息
        filtered_systems = []
        for system in systems:
            # 查询该系统下符合要求的 GPU
            gpus = session.query(gpu_query).filter(
                gpu_query.c.uid == system.uid,
                gpu_query.c.memory_total >= gpu_memory_total,
                gpu_query.c.memory_free >= gpu_memory_free,
                gpu_query.c.gpu_utilization <= max_gpu_utilization
            ).all()

            if len(gpus) >= sum_gpu_count:  # 确保符合 GPU 需求
                filtered_systems.append({
                    "host_name": system.host_name,
                    "ip": system.ip,
                    "cpu_info": {
                        "cpu_percent": system.cpu_percent,
                        "cpu_count": system.cpu_count
                    },
                    "gpus_info": [
                                     {
                                         "index": gpu.lindex,
                                         "fan_speed": gpu.fan_speed,
                                         "temperature": gpu.temperature,
                                         "gpu_utilization": gpu.gpu_utilization,
                                         "memory_total": gpu.memory_total,
                                         "memory_used": gpu.memory_used,
                                         "memory_free": gpu.memory_free
                                     }
                                     for gpu in sorted(gpus, key=lambda x: x.lindex)
                                 ][:sum_gpu_count]
                })

        return filtered_systems[:1] if only_one else filtered_systems
