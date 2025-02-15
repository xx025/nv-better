import asyncio
import hashlib
import time
import uuid

from sqlalchemy import create_engine, Column, String, ForeignKey, Integer, Float, UUID
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

from core.msgqueue import MsgQueue

Base = declarative_base()


# 系统信息表
class SystemInfo(Base):
    __tablename__ = "system_info"

    uid = Column(String, primary_key=True)
    host_name = Column(String, nullable=False)
    ip = Column(String, nullable=False)

    last_update = Column(Integer, default=lambda: int(time.time()))
    is_online = Column(Integer, default=1)  # 1 为在线，0 为离线
    # 关系
    cpu = relationship("CPUInfo", back_populates="system")
    gpus = relationship("GPUInfo", back_populates="system")

    def __init__(self, host_name, ip):
        self.host_name = host_name
        self.ip = ip
        self.uid = self.generate_uid(host_name, ip)

    @staticmethod
    def generate_uid(host_name, ip):
        """计算 UID，保证相同的 `host_name` 和 `ip` 生成相同的 UID"""
        unique_string = f"{host_name}:{ip}"
        return hashlib.sha1(unique_string.encode()).hexdigest()[:16]  # 取前 16 位


# CPU 信息表
class CPUInfo(Base):
    __tablename__ = "cpu_info"

    pkid = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # 使用 UUID 作为主键

    uid = Column(String, ForeignKey("system_info.uid"))
    cpu_percent = Column(Float, nullable=False)
    cpu_count = Column(Integer, nullable=False)
    recode_time = Column(Integer, default=lambda: int(time.time()))

    # 关系
    system = relationship("SystemInfo", back_populates="cpu")


# GPU 信息表（允许多块 GPU）
class GPUInfo(Base):
    __tablename__ = "gpu_info"

    pkid = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)  # 使用 UUID 作为主键
    id = Column(String)
    uid = Column(String, ForeignKey("system_info.uid"))
    lindex = Column(Integer)
    fan_speed = Column(String)
    temperature = Column(Integer)
    gpu_utilization = Column(Integer)
    memory_total = Column(Integer)
    memory_used = Column(Integer)
    memory_free = Column(Integer)
    recode_time = Column(Integer, default=lambda: int(time.time()))

    # 关系
    system = relationship("SystemInfo", back_populates="gpus")

    def __init__(self, id, uid, lindex, fan_speed, temperature, gpu_utilization, memory_total, memory_used,
                 memory_free):
        self.id = id
        self.uid = uid
        self.lindex = lindex
        self.fan_speed = fan_speed
        self.temperature = temperature
        self.gpu_utilization = gpu_utilization
        self.memory_total = memory_total
        self.memory_used = memory_used
        self.memory_free = memory_free

    @staticmethod
    def generate_id(uid, lindex):
        unique_string = f"{uid}:{lindex}"
        return hashlib.sha1(unique_string.encode()).hexdigest()[:16]


class Database:
    def __init__(self, db_url: str = "sqlite:///:memory:"):
        self.engine = create_engine(db_url, echo=False)  # echo=True 显示 SQL 语句
        self.Session = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,  # Avoid session expiry after commit
        )

        Base.metadata.create_all(bind=self.engine)

    def get_session(self):
        return self.Session()

    def close(self):
        self.engine.dispose()


# 创建一个线程持续读取 msg_queue 中的数据并写入数据库


class RecordToDatabase:
    def __init__(self, db: Database, msg_queue: MsgQueue):
        self.db = db
        self.msg_queue = msg_queue

    async def run(self):
        while True:
            data = await self.msg_queue.pop()
            if data is None:
                await asyncio.sleep(0.1)
                continue
            key, value = data

            # print(f"RecordToDatabase: {key} {value}")
            with self.db.get_session() as session:
                try:
                    host_name = value["host_info"]["host_name"]
                    ip = value["host_info"]["ip"]
                    uid = SystemInfo.generate_uid(host_name, ip)

                    system = session.query(SystemInfo).get(uid)
                    if system is None:
                        system = SystemInfo(host_name=host_name, ip=ip)
                        session.add(system)
                    else:
                        system.is_online = 1
                        system.last_update = int(time.time())
                    # 清除原有的 CPU 信息
                    if system.cpu:
                        # 清除 最新10 秒外的数据
                        session.query(CPUInfo).filter(CPUInfo.uid == uid,
                                                      CPUInfo.recode_time < int(time.time()) - 10).delete()
                    # 保存 CPU 信息
                    cpu_info = value["cpu_info"]
                    cpu = CPUInfo(uid=uid, cpu_percent=cpu_info["cpu_percent"], cpu_count=cpu_info["cpu_count"])
                    session.add(cpu)
                    # 清除原有的 GPU 信息
                    for gpu in system.gpus:
                        # session.delete(gpu)
                        # 按照 uid 和 index 索引，清除最新10秒外的数据
                        session.query(GPUInfo).filter(GPUInfo.uid == uid, GPUInfo.lindex == gpu.lindex,
                                                      GPUInfo.recode_time < int(time.time()) - 10).delete()

                    # 保存 GPU 信息
                    for gpu_info in value["gpu_info"]:
                        gpu = GPUInfo(
                            id=GPUInfo.generate_id(uid, gpu_info["index"]),
                            uid=uid,
                            lindex=gpu_info["index"],
                            fan_speed=gpu_info["fan_speed"],
                            temperature=gpu_info["temperature"],
                            gpu_utilization=gpu_info["gpu_utilization"],
                            memory_total=gpu_info["memory_total"],
                            memory_used=gpu_info["memory_used"],
                            memory_free=gpu_info["memory_free"],
                        )
                        session.add(gpu)
                    session.commit()
                except Exception as e:
                    print(f"Error: {e}")
                    session.rollback()
                    session.close()
                    continue


class CheckOnlineStatus:
    def __init__(self, db: Database):
        self.db = db

    async def run(self):
        while True:
            await asyncio.sleep(2)
            with self.db.get_session() as session:
                try:
                    systems = session.query(SystemInfo).filter(SystemInfo.is_online == 1).all()
                    for system in systems:
                        if system.last_update < int(time.time()) - 10:
                            system.is_online = 0
                            print(f"System {system.host_name} is offline")
                    session.commit()
                except Exception as e:
                    print(f"Error: {e}")
                    session.rollback()
                    session.close()
                    continue
