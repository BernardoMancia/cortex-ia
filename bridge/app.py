from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import MetaTrader5 as mt5
import logging

app = FastAPI(title="MT5 Bridge API")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mt5_bridge")

class OrderRequest(BaseModel):
    action: int
    symbol: str
    volume: float
    type: int
    price: float
    sl: float = 0.0
    tp: float = 0.0
    deviation: int = 20
    magic: int
    comment: str
    type_time: int
    type_filling: int

class ConnectRequest(BaseModel):
    login: int
    password: str
    server: str
    path: str = ""

@app.on_event("shutdown")
def shutdown_event():
    mt5.shutdown()

@app.post("/connect")
def connect_mt5(req: ConnectRequest):
    kwargs = {}
    if req.path:
        kwargs['path'] = req.path
        
    if not mt5.initialize(**kwargs):
        raise HTTPException(status_code=500, detail=f"Initialize failed: {mt5.last_error()}")
        
    if not mt5.login(login=req.login, password=req.password, server=req.server):
        raise HTTPException(status_code=401, detail=f"Login failed: {mt5.last_error()}")
        
    info = mt5.account_info()
    if info is None:
        raise HTTPException(status_code=500, detail="Could not get account info")
        
    return {
        "status": "connected",
        "login": info.login,
        "server": info.server,
        "balance": info.balance,
        "equity": info.equity
    }

@app.get("/account")
def get_account():
    info = mt5.account_info()
    if info is None:
        raise HTTPException(status_code=500, detail="Account info not available")
    return {
        "balance": info.balance,
        "equity": info.equity
    }

@app.post("/order_check")
def check_order(req: OrderRequest):
    result = mt5.order_check(req.model_dump())
    if result is None:
        raise HTTPException(status_code=500, detail=f"Check failed: {mt5.last_error()}")
    return {
        "retcode": result.retcode,
        "margin": result.margin,
        "margin_free": result.margin_free,
        "margin_level": result.margin_level,
        "comment": result.comment
    }

@app.post("/order_send")
def send_order(req: OrderRequest):
    result = mt5.order_send(req.model_dump())
    if result is None:
        raise HTTPException(status_code=500, detail=f"Send failed: {mt5.last_error()}")
    return {
        "retcode": result.retcode,
        "order": result.order,
        "volume": result.volume,
        "price": result.price,
        "comment": result.comment
    }

@app.get("/positions")
def get_positions():
    pos = mt5.positions_get()
    if pos is None:
        return []
    return [p._asdict() for p in pos]

@app.get("/symbol_info/{symbol}")
def get_symbol_info(symbol: str):
    info = mt5.symbol_info(symbol)
    if info is None:
        raise HTTPException(status_code=404, detail="Symbol not found")
    return info._asdict()

@app.get("/tick/{symbol}")
def get_tick(symbol: str):
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise HTTPException(status_code=404, detail="Tick not found")
    return tick._asdict()

@app.post("/symbol_select/{symbol}")
def select_symbol(symbol: str):
    if not mt5.symbol_select(symbol, True):
        raise HTTPException(status_code=500, detail="Could not select symbol")
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
