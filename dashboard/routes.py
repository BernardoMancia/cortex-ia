import os
import json
import logging
import sqlite3
from fastapi import APIRouter ,WebSocket ,WebSocketDisconnect
from typing import Any
import asyncio
import requests as sync_requests

logger =logging .getLogger ('cortex.dashboard.routes')

router =APIRouter ()

DB_PATH =os .path .join (os .path .dirname (os .path .dirname (__file__ )),"data","cortex.db")
LOG_PATH =os .path .join (os .path .dirname (os .path .dirname (__file__ )),"logs","cortex.log")
SIMULATOR_STATE_PATH =os .path .join (os .path .dirname (os .path .dirname (__file__ )),"data","simulator_state.json")

def get_db_connection ():
    conn =sqlite3 .connect (DB_PATH )
    conn .row_factory =sqlite3 .Row
    return conn

@router .get ("/api/status")
def get_status ()->dict [str ,Any ]:
    """Returns the current portfolio status and recent decisions."""
    status ={
    "balance":0.0 ,
    "equity":0.0 ,
    "positions":[],
    "recent_decisions":[]
    }

    if os .path .exists (SIMULATOR_STATE_PATH ):
        try :
            with open (SIMULATOR_STATE_PATH ,"r",encoding ="utf-8")as f :
                state =json .load (f )
                status ["balance"]=state .get ("balance",0.0 )
                positions =state .get ("positions",{})

                equity =status ["balance"]
                for p in positions .values ():
                    p_val =p .get ("current_price",p .get ("entry_price",0.0 ))*p .get ("quantity",0 )
                    equity +=p_val
                    status ["positions"].append (p )
                status ["equity"]=equity
        except Exception as exc :
            logger .warning ('Falha ao ler simulator_state.json: %s',exc )

    if os .path .exists (DB_PATH ):
        conn =None
        try :
            conn =get_db_connection ()
            cursor =conn .cursor ()
            cursor .execute ("""
                SELECT ticker, action, confidence, reasoning, timestamp
                FROM ai_decisions
                ORDER BY timestamp DESC
                LIMIT 20
            """)
            rows =cursor .fetchall ()
            for row in rows :
                status ["recent_decisions"].append (dict (row ))
        except Exception as exc :
            logger .warning ('Falha ao ler decisões do DB: %s',exc )
        finally :
            if conn is not None :
                conn .close ()

    return status

@router .get ("/api/production_balance")
async def get_production_balance ()->dict [str ,Any ]:
    """Fetches real account balance from MT5 Bridge."""
    try :
        resp =await asyncio .to_thread (sync_requests .get ,"http://127.0.0.1:5000/account",timeout =3 )
        if resp .status_code ==200 :
            data =resp .json ()
            return {"status":"ok","balance":data .get ("balance",0.0 )}
    except Exception as e :
        logger .warning ('Falha ao buscar saldo de produção: %s',e )
    return {"status":"error","balance":0.0 }

@router .websocket ("/ws/logs")
async def websocket_logs (websocket :WebSocket ):
    await websocket .accept ()

    last_position =0
    if os .path .exists (LOG_PATH ):
        last_position =max (0 ,os .path .getsize (LOG_PATH )-10000 )

    try :
        while True :
            if os .path .exists (LOG_PATH ):
                with open (LOG_PATH ,"r",encoding ="utf-8")as f :
                    f .seek (last_position )
                    new_lines =f .readlines ()
                    last_position =f .tell ()

                    if new_lines :
                        for line in new_lines :
                            if line .strip ():
                                await websocket .send_text (line .strip ())

            await asyncio .sleep (1 )
    except WebSocketDisconnect :
        pass
