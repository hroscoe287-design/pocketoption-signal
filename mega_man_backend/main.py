from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Literal
from statistics import mean
import os, secrets

app = FastAPI(title='Mega Man Signal API', version='1.0.0')
origins = [x.strip() for x in os.getenv('CORS_ORIGINS','https://mega-man-signal-lab.onrender.com').split(',') if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=['GET','POST'], allow_headers=['Content-Type','X-API-Key'])

class Candle(BaseModel):
    time: float
    open: float
    high: float
    low: float
    close: float

class AnalyzeRequest(BaseModel):
    asset: str = Field(min_length=1, max_length=80)
    timeframe_seconds: int = Field(default=60, ge=5, le=604800)
    candles: List[Candle] = Field(min_length=30, max_length=500)

@app.middleware('http')
async def protect_api(request, call_next):
    secret = os.getenv('MEGAMAN_API_KEY','')
    if request.url.path == '/analyze' and secret and not secrets.compare_digest(request.headers.get('X-API-Key',''), secret):
        return __import__('starlette.responses').responses.JSONResponse({'detail':'Unauthorized'}, status_code=401)
    return await call_next(request)

@app.get('/')
def root():
    return {'name':'Mega Man Signal API','status':'online','signals':'analysis-only','automatic_execution':False,'data_requirement':'verified broker candles must be supplied by a trusted feed'}

@app.get('/health')
def health():
    return {'ok':True,'provider_configured':bool(os.getenv('PO_AUTH_JSON')),'automatic_execution':False}

@app.post('/analyze')
def analyze(req: AnalyzeRequest):
    cs=req.candles
    if any(c.high < max(c.open,c.close,c.low) or c.low > min(c.open,c.close,c.high) for c in cs):
        raise HTTPException(422,'Invalid OHLC candle values')
    closes=[c.close for c in cs]
    if any(x<=0 for x in closes): raise HTTPException(422,'Prices must be positive')
    last=closes[-1]
    fast=mean(closes[-9:]); slow=mean(closes[-21:])
    momentum=closes[-1]-closes[-5]
    ranges=[max(c.high-c.low, abs(c.high-c.open), abs(c.low-c.open)) for c in cs[-14:]]
    atr=mean(ranges) if ranges else 0
    body=abs(cs[-1].close-cs[-1].open)
    # Transparent rule-based score; not a calibrated probability or guaranteed win rate.
    up=0; down=0
    up += 1 if fast>slow else 0; down += 1 if fast<slow else 0
    up += 1 if momentum>0 else 0; down += 1 if momentum<0 else 0
    up += 1 if cs[-1].close>cs[-1].open else 0; down += 1 if cs[-1].close<cs[-1].open else 0
    direction='WAIT'
    if up>=3 and down==0: direction='UP'
    elif down>=3 and up==0: direction='DOWN'
    confidence=round(50 + 12*max(up,down)) if direction!='WAIT' else 0
    return {'asset':req.asset,'timeframe_seconds':req.timeframe_seconds,'direction':direction,'arrow':'green_up' if direction=='UP' else ('red_down' if direction=='DOWN' else None),'confidence_score':min(confidence,86) if direction!='WAIT' else None,'confidence_type':'heuristic_score_not_win_probability','entry_reference':last,'expiry_seconds':req.timeframe_seconds,'analysis':{'ema9':round(fast,8),'ema21':round(slow,8),'five_candle_momentum':round(momentum,8),'recent_range_mean':round(atr,8),'last_candle_body':round(body,8),'up_checks':up,'down_checks':down},'candle_count':len(cs),'source':'client_supplied_candles_not_independently_verified','automatic_execution':False,'disclaimer':'Educational heuristic only; no verified win-rate claim. WAIT means no qualifying alignment.'}
