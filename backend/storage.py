"""Small durable repository. PostgreSQL in production, SQLite for local use."""
import json
import sqlite3
import hashlib
import threading
from contextvars import ContextVar
from contextlib import contextmanager


class Store:
    def __init__(self, path, database_url=''):
        self.path, self.url = str(path), database_url
        self._transaction = ContextVar('store_transaction', default=None)
        self._lock = threading.RLock()
        # Hosted tables are provisioned explicitly; cold starts never change schema.
        if self.url: return
        with self.connection() as db:
            blob = 'BYTEA' if self.url else 'BLOB'
            db.execute('CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, password TEXT NOT NULL, recovery TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1)')
            db.execute('CREATE TABLE IF NOT EXISTS invites (code TEXT PRIMARY KEY, expires DOUBLE PRECISION NOT NULL, used INTEGER NOT NULL DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, body TEXT NOT NULL)')
            db.execute(f'CREATE TABLE IF NOT EXISTS images (job_id TEXT NOT NULL, filename TEXT NOT NULL, body {blob} NOT NULL, PRIMARY KEY(job_id, filename))')

    @contextmanager
    def connection(self):
        active = self._transaction.get()
        if active is not None:
            yield active
            return
        if self.url:
            import psycopg
            from psycopg.rows import dict_row
            raw = psycopg.connect(self.url, row_factory=dict_row, connect_timeout=10, prepare_threshold=None)
        else:
            raw = sqlite3.connect(self.path, timeout=15)
            raw.row_factory = sqlite3.Row
        class DB:
            def execute(_, sql, params=()):
                if self.url:
                    import re
                    sql = re.sub(r'\b(users|invites|jobs|images)\b', r'studio.\1', sql)
                    sql = sql.replace('?', '%s')
                return raw.execute(sql, params)
        try:
            yield DB()
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    @contextmanager
    def job_lock(self, jid):
        """Serialize a catalogue across instances, including transaction poolers."""
        from fastapi import HTTPException
        key = int.from_bytes(hashlib.sha256(jid.encode()).digest()[:8], 'big', signed=True)
        with self._lock, self.connection() as db:
            if self.url and not db.execute('SELECT pg_try_advisory_xact_lock(?) AS acquired', (key,)).fetchone()['acquired']:
                raise HTTPException(409, 'This catalogue is busy. Try again after the current product finishes.')
            token = self._transaction.set(db)
            try: yield
            finally: self._transaction.reset(token)

    def invite(self, code, expires):
        with self.connection() as db:
            db.execute('INSERT INTO invites(code,expires) VALUES(?,?)', (code,expires))

    def redeem(self, code, now, uid, email, name, password, recovery):
        with self.connection() as db:
            result = db.execute('UPDATE invites SET used=1 WHERE code=? AND used=0 AND expires>?', (code,now))
            if result.rowcount != 1: return False
            db.execute('INSERT INTO users(id,email,name,password,recovery) VALUES(?,?,?,?,?)', (uid,email,name,password,recovery))
            return True

    def image_usage(self, owner):
        with self.connection() as db:
            row = db.execute('SELECT COALESCE(SUM(LENGTH(images.body)),0) AS size FROM images JOIN jobs ON jobs.id=images.job_id WHERE jobs.owner=?', (owner,)).fetchone()
            return row['size']

    def user(self, email=None, uid=None):
        with self.connection() as db:
            row = db.execute('SELECT * FROM users WHERE '+('id=?' if uid else 'email=?'), (uid or email,)).fetchone()
            return dict(row) if row else None

    def add_user(self, uid, email, name, password, recovery):
        with self.connection() as db:
            db.execute('INSERT INTO users(id,email,name,password,recovery) VALUES(?,?,?,?,?)', (uid,email,name,password,recovery))

    def reset_user(self, uid, password, recovery):
        with self.connection() as db:
            db.execute('UPDATE users SET password=?, recovery=?, version=version+1 WHERE id=?', (password,recovery,uid))

    def jobs(self, owner=None):
        with self.connection() as db:
            rows = db.execute('SELECT body FROM jobs'+(' WHERE owner=?' if owner else ''), (owner,) if owner else ()).fetchall()
            return [json.loads(r['body']) for r in rows]

    def read(self, jid):
        with self.connection() as db:
            row = db.execute('SELECT body FROM jobs WHERE id=?', (jid,)).fetchone()
            return json.loads(row['body']) if row else None

    def write(self, job):
        with self.connection() as db:
            db.execute('INSERT INTO jobs(id,owner,body) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body', (job['id'],job.get('owner','legacy'),json.dumps(job)))

    def put_image(self, jid, name, data):
        with self.connection() as db:
            db.execute('INSERT INTO images(job_id,filename,body) VALUES(?,?,?) ON CONFLICT(job_id,filename) DO UPDATE SET body=excluded.body', (jid,name,data))

    def image(self, jid, name):
        with self.connection() as db:
            row = db.execute('SELECT body FROM images WHERE job_id=? AND filename=?', (jid,name)).fetchone()
            return bytes(row['body']) if row else None
