"""仓储基类"""
from contextlib import contextmanager

from app.infrastructure.repositories.mysql_pool import mysql_pool


class BaseRepo:

    @contextmanager
    def cursor(self, commit: bool = False):
        conn = mysql_pool.get_connection()
        cur = None
        try:
            cur = conn.cursor()
            yield cur
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if cur:
                cur.close()
            conn.close()

    def query_one(self, sql: str, params=None) -> dict:
        with self.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchone()

    def query_list(self, sql: str, params=None) -> list:
        with self.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()

    def execute(self, sql: str, params=None) -> int:
        with self.cursor(commit=True) as cur:
            return cur.execute(sql, params or ())