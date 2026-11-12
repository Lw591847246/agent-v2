"""MySQL 连接池"""
import pymysql
from dbutils.pooled_db import PooledDB

from app.config.loader import config


class MySQLPool:

    _pool = None

    @classmethod
    def init(cls):
        if cls._pool is None:
            cls._pool = PooledDB(
                creator=pymysql,
                maxconnections=config.get("mysql.pool_size", 10),
                mincached=2, maxcached=5, blocking=True, ping=1,
                host=config.get("mysql.host"),
                port=config.get("mysql.port"),
                user=config.get("mysql.user"),
                password=config.get("mysql.password"),
                database=config.get("mysql.database"),
                charset=config.get("mysql.charset", "utf8mb4"),
                cursorclass=pymysql.cursors.DictCursor,
            )

    @classmethod
    def get_connection(cls):
        if cls._pool is None:
            cls.init()
        return cls._pool.connection()


mysql_pool = MySQLPool