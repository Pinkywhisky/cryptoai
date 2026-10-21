"""One explicitly triggered thread, with durable jobs and fenced financial writes."""
import json
import re
import threading
import time
import uuid

from services.binance_api_budget import ApiBudget
from services.binance_private_client import BinancePrivateClient, BinanceReadError
from services.binance_trade_history import connect, exchange_for_client, SyncBusy, SyncCancelled, LeaseLost
from services import binance_sync_planner as planner
from services.history import DB_PATH, init_db

ACTIVE={"QUEUED","RUNNING","CANCELLING"}
RESUMABLE={"CANCELLED","INTERRUPTED","PAUSED_QUOTA","ERROR","PARTIAL"}
QUOTAS={"RATE_LIMIT","HTTP_418","LOCAL_QUOTA","RETRY_LATER"}


def safe_code(error):
    code=getattr(error,"code",type(error).__name__)
    return code if isinstance(code,str) and re.fullmatch(r"[A-Z0-9_]{1,64}",code) else type(error).__name__


class SyncJobs:
    def __init__(self,path=DB_PATH,*,client_factory=None,clock=time.time):
        self.path=path
        self.clock=clock
        self.client_factory=client_factory or (lambda:BinancePrivateClient(budget=ApiBudget(path)))
        self._threads={}
        self._guard=threading.Lock()
        self._stopping=threading.Event()

    def _recover(self,connection):
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        now=self.clock()
        for row in connection.execute("SELECT * FROM binance_sync_jobs WHERE status IN ('QUEUED','RUNNING','CANCELLING')").fetchall():
            lease=connection.execute("SELECT * FROM financial_sync_lock WHERE exchange=? AND owner=?",
                                     (row["exchange"],row["owner"])).fetchone()
            if row["updated_at"]+90<=now and not (lease and lease["expires_at"]>now):
                connection.execute("UPDATE binance_sync_jobs SET status='INTERRUPTED',error_code='WORKER_INTERRUPTED',updated_at=? WHERE id=?",
                                   (now,row["id"]))

    def preview(self,*,symbols=(),full_scan=False):
        client=self.client_factory()
        return planner.preview(client,self.path,exchange_for_client(client),symbols=symbols,full_scan=full_scan,clock=self.clock)

    def _launch(self,identifier):
        if self._stopping.is_set():
            raise RuntimeError("Synchronization worker is stopping")
        with self._guard:
            active=self._threads.get(identifier)
            if active and active.is_alive():
                return
            thread=threading.Thread(target=self.run,args=(identifier,),name="binance-sync",daemon=True)
            self._threads[identifier]=thread
            thread.start()

    def start(self,*,mode="incremental",preview_id=None,confirmed=False,resume_id=None,start_thread=True):
        if self._stopping.is_set():
            raise RuntimeError("Synchronization worker is stopping")
        client=self.client_factory()
        if not client.configured:
            return {"accepted":False,"configured":False,"synced":False,"message":"API Binance privee non configuree"}
        exchange=exchange_for_client(client)
        init_db(self.path)
        now=self.clock()
        with connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover(connection)
            busy=connection.execute("SELECT id FROM binance_sync_jobs WHERE status IN ('QUEUED','RUNNING','CANCELLING')").fetchone()
            if busy:
                identifier=busy["id"]
                accepted=False
            elif resume_id:
                row=connection.execute("SELECT * FROM binance_sync_jobs WHERE id=? AND exchange=?",(resume_id,exchange)).fetchone()
                if not row or row["status"] not in RESUMABLE:
                    raise ValueError("JOB_NOT_RESUMABLE")
                if row["retry_at"] and row["retry_at"]>now:
                    raise ValueError("QUOTA_SUSPENSION_ACTIVE")
                identifier=resume_id
                connection.execute("""UPDATE binance_sync_jobs SET status='QUEUED',updated_at=?,owner=NULL,
                    cancel_requested=0,error_code=NULL,retry_at=NULL WHERE id=?""",(now,identifier))
                connection.execute("UPDATE binance_job_markets SET status='PENDING',error_code=NULL WHERE job_id=? AND status!='DONE'",(identifier,))
                connection.execute("UPDATE binance_sync_jobs SET processed=(SELECT COUNT(*) FROM binance_job_markets WHERE job_id=? AND status='DONE') WHERE id=?",(identifier,identifier))
                accepted=True
            else:
                payload=None
                if mode=="bootstrap":
                    if not preview_id:
                        raise ValueError("BOOTSTRAP_PREVIEW_REQUIRED")
                    payload=planner.consume_preview(connection,preview_id,exchange,confirmed=confirmed,clock=self.clock)
                elif mode!="incremental" or preview_id or confirmed:
                    raise ValueError("INVALID_SYNCHRONIZATION_REQUEST")
                identifier=uuid.uuid4().hex
                connection.execute("""INSERT INTO binance_sync_jobs(id,exchange,mode,scope,status,created_at,updated_at,total)
                    VALUES(?,?,?,?,'QUEUED',?,?,?)""",(identifier,exchange,mode,payload["scope"] if payload else "INCREMENTAL",
                                                      now,now,len(payload["symbols"]) if payload else 0))
                if payload:
                    connection.executemany("INSERT INTO binance_job_markets(job_id,symbol) VALUES(?,?)",
                                           [(identifier,s) for s in payload["symbols"]])
                accepted=True
        if accepted and start_thread:
            self._launch(identifier)
        return {"accepted":accepted,"configured":True,"read_only_mode":True,"job":self.get(identifier),
                "sync_status":"QUEUED" if accepted else "BUSY","synced":False}

    def _public(self,row,connection):
        if not row:
            return None
        item={k:row[k] for k in ("id","exchange","mode","scope","status","created_at","updated_at",
                                  "processed","total","inserted","duplicates","pages","error_code","retry_at","attempt")}
        item["remaining"]=max(0,item["total"]-item["processed"])
        item["cancel_requested"]=bool(row["cancel_requested"])
        item["errors"]=[dict(r) for r in connection.execute("""SELECT symbol,error_code FROM binance_job_markets
            WHERE job_id=? AND status='ERROR' ORDER BY symbol LIMIT 100""",(row["id"],))]
        item["error_count"]=connection.execute("SELECT COUNT(*) FROM binance_job_markets WHERE job_id=? AND status='ERROR'",(row["id"],)).fetchone()[0]
        item["errors_truncated"]=item["error_count"]>len(item["errors"])
        item["result"]=json.loads(row["result_json"]) if row["result_json"] else None
        return item

    def get(self,identifier=None,*,exchange=None):
        init_db(self.path)
        with connect(self.path) as connection:
            # Recovery is a write only when an orphan really exists.
            rows=connection.execute("SELECT updated_at FROM binance_sync_jobs WHERE status IN ('QUEUED','RUNNING','CANCELLING')").fetchall()
            if any(row[0]+90<=self.clock() for row in rows):
                self._recover(connection)
            if identifier:
                row=connection.execute("SELECT * FROM binance_sync_jobs WHERE id=?",(identifier,)).fetchone()
            else:
                row=connection.execute("SELECT * FROM binance_sync_jobs WHERE exchange=? ORDER BY created_at DESC LIMIT 1",
                                       (exchange or "BINANCE_SPOT",)).fetchone()
            return self._public(row,connection)

    def cancel(self,identifier):
        init_db(self.path)
        with connect(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._recover(connection)
            row=connection.execute("SELECT * FROM binance_sync_jobs WHERE id=?",(identifier,)).fetchone()
            if not row:
                raise ValueError("JOB_NOT_FOUND")
            if row["status"]=="QUEUED":
                connection.execute("UPDATE binance_sync_jobs SET cancel_requested=1,status='CANCELLED',updated_at=? WHERE id=?",(self.clock(),identifier))
            elif row["status"] in ACTIVE:
                connection.execute("UPDATE binance_sync_jobs SET cancel_requested=1,status='CANCELLING',updated_at=? WHERE id=?",(self.clock(),identifier))
        return self.get(identifier)

    def run(self,identifier):
        owner=uuid.uuid4().hex
        try:
            with connect(self.path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                updated=connection.execute("""UPDATE binance_sync_jobs SET status='RUNNING',owner=?,attempt=attempt+1,updated_at=?
                    WHERE id=? AND status='QUEUED' AND cancel_requested=0""",(owner,self.clock(),identifier))
                if updated.rowcount!=1:
                    return
                job=dict(connection.execute("SELECT * FROM binance_sync_jobs WHERE id=?",(identifier,)).fetchone())
                rows=connection.execute("SELECT * FROM binance_job_markets WHERE job_id=?",(identifier,)).fetchall()
            def cancelled():
                if self._stopping.is_set():
                    return True
                with connect(self.path) as connection:
                    row=connection.execute("SELECT owner,cancel_requested,status FROM binance_sync_jobs WHERE id=?",(identifier,)).fetchone()
                return not row or row["owner"]!=owner or row["cancel_requested"] or row["status"] not in ACTIVE

            def update(event,*,connection=None):
                if connection is None:
                    with connect(self.path) as opened:
                        return update(event,connection=opened)
                current=connection.execute("""UPDATE binance_sync_jobs SET updated_at=?
                    WHERE id=? AND owner=? AND status IN ('RUNNING','CANCELLING')""",
                    (self.clock(),identifier,owner))
                if current.rowcount!=1:
                    raise LeaseLost("Synchronization job ownership lost")
                kind=event["event"]
                if kind=="PLAN":
                    connection.executemany("""INSERT INTO binance_job_markets(job_id,symbol) VALUES(?,?)
                        ON CONFLICT(job_id,symbol) DO NOTHING""",[(identifier,s) for s in event["symbols"]])
                    connection.execute("UPDATE binance_sync_jobs SET total=(SELECT COUNT(*) FROM binance_job_markets WHERE job_id=?) WHERE id=?",(identifier,identifier))
                elif kind=="PAGE":
                    connection.execute("""UPDATE binance_sync_jobs SET inserted=inserted+?,duplicates=duplicates+?,pages=pages+?
                        WHERE id=? AND owner=?""",(event["inserted"],event["duplicates"],event["pages"],identifier,owner))
                elif kind in {"MARKET","MARKET_START"}:
                    connection.execute("""UPDATE binance_job_markets SET status=?,error_code=?,updated_at=?
                        WHERE job_id=? AND symbol=?""",(event.get("status","RUNNING"),event.get("code"),self.clock(),identifier,event["symbol"]))
                    connection.execute("""UPDATE binance_sync_jobs SET processed=(SELECT COUNT(*) FROM binance_job_markets
                        WHERE job_id=? AND status IN ('DONE','ERROR')) WHERE id=?""",(identifier,identifier))

            client=self.client_factory()
            if exchange_for_client(client)!=job["exchange"] or not client.configured:
                raise BinanceReadError("CLIENT_CONFIGURATION_CHANGED")
            if hasattr(client,"request_hook"):
                def hook():
                    if cancelled():
                        raise SyncCancelled("Synchronization cancelled")
                    update({"event":"HEARTBEAT"})
                client.request_hook=hook
            from services.binance_sync import sync_binance_positions
            result=sync_binance_positions(client,self.path,mode=job["mode"],
                symbols=tuple(row["symbol"] for row in rows),
                progress=update,should_cancel=cancelled,
                resume_completed=tuple(row["symbol"] for row in rows if row["status"]=="DONE"),lease_owner=owner)
            if cancelled():
                raise SyncCancelled("Synchronization cancelled")
            self._finish(identifier,owner,"COMPLETED" if result["synced"] else "PARTIAL",
                result={k:v for k,v in result.items() if k not in {"positions","dust","closed"}})
        except SyncCancelled:
            self._finish(identifier,owner,"CANCELLED",error="CANCELLED")
        except BinanceReadError as error:
            self._finish(identifier,owner,"PAUSED_QUOTA" if error.code in QUOTAS else "ERROR",
                         error=safe_code(error),retry_at=self.clock()+error.retry_after if error.retry_after else None)
        except SyncBusy:
            self._finish(identifier,owner,"INTERRUPTED",error="SYNC_BUSY")
        except Exception as error:
            self._finish(identifier,owner,"ERROR",error=safe_code(error))
        finally:
            with self._guard:
                self._threads.pop(identifier,None)

    def _finish(self,identifier,owner,status,*,error=None,retry_at=None,result=None):
        with connect(self.path) as connection:
            connection.execute("""UPDATE binance_sync_jobs SET status=?,updated_at=?,error_code=?,retry_at=?,result_json=?
                WHERE id=? AND owner=? AND status IN ('RUNNING','CANCELLING')""",
                (status,self.clock(),error,retry_at,json.dumps(result,allow_nan=False) if result else None,identifier,owner))

    def shutdown(self,timeout=30):
        self._stopping.set()
        with self._guard:
            threads=list(self._threads.values())
        deadline=time.monotonic()+timeout
        for thread in threads:
            thread.join(max(0,deadline-time.monotonic()))
        return not any(thread.is_alive() for thread in threads)
